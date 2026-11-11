"""Skill hook manifests, and the dispatcher that runs them for a host.

Every dispatcher row works in its own temporary repository holding a fixture
generation at ``.agent-skills/current``. The subprocess rows run the
generation's own ``__main__.py``, as the installed command does; the budget and
process rows call the dispatcher in-process so its constants can be scaled down
and its process starts observed. No row starts a host session or writes a host
settings file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_skills import hooks
from tests.corpus_copy import copy_corpus


ROOT = Path(__file__).resolve().parents[1]
GENERATION = "f" * 64

ALLOW = 'print(\'{"verdict": "allow"}\')\n'


def verdict_script(document: object) -> str:
    return f"import json\nprint(json.dumps({document!r}))\n"


def stop_payload(cwd: Path, *, active: object = False) -> dict[str, object]:
    return {
        "cwd": str(cwd),
        "hook_event_name": "Stop",
        "permission_mode": "default",
        "session_id": "0b8f2c1e-session",
        "stop_hook_active": active,
        "transcript_path": "/nonexistent/transcript.jsonl",
    }


def start_payload(cwd: Path, source: str = "startup") -> dict[str, object]:
    return {
        "cwd": str(cwd),
        "hook_event_name": "SessionStart",
        "session_id": "0b8f2c1e-session",
        "source": source,
        "transcript_path": "/nonexistent/transcript.jsonl",
    }


def clean_environment(**extra: str) -> dict[str, str]:
    """The test process's environment without what would hide a fault in a row."""
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"CLAUDE_PROJECT_DIR", "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"}
    }
    environment.update(extra)
    return environment


class FixtureRepository:
    """A temporary Git repository whose active generation declares the given skills' hooks.

    ``skills`` maps a skill id to ``(folder, hosts, manifest, scripts)``: the
    skill's folder under ``skills/``, its catalog hosts, its ``hooks.json``
    document (or ``None`` for no manifest), and its files by path relative to
    the folder. ``catalog_order`` and ``members`` let a row list skills in an
    order other than their sorted ids.
    """

    def __init__(self, directory: Path, skills: dict[str, tuple], *, catalog_order: list[str] | None = None,
                 members: list[str] | None = None, runtime: bool = True) -> None:
        self.root = directory / "repository"
        self.root.mkdir()
        subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(self.root)], check=True,
                       capture_output=True)
        self.root = self.root.resolve()
        managed = self.root / ".agent-skills"
        self.generation = managed / "generations" / GENERATION
        self.generation.mkdir(parents=True)
        (managed / "current").symlink_to(Path("generations") / GENERATION)
        if runtime:
            copy_corpus(self.generation / "agent_skills", source=ROOT / "agent_skills")
            (self.generation / "__main__.py").write_text(
                "import os, sys\nsys.path[0] = os.path.realpath(sys.path[0])\n"
                "from agent_skills.cli import main\nraise SystemExit(main())\n", encoding="utf-8")
        order = catalog_order or list(skills)
        catalog = []
        for skill_id in order:
            folder, hosts, manifest, scripts = skills[skill_id]
            skill_root = self.generation / "skills" / folder
            skill_root.mkdir(parents=True)
            (skill_root / "SKILL.md").write_text(f"---\nname: {skill_id}\ndescription: fixture\n---\n",
                                                 encoding="utf-8")
            if manifest is not None:
                (skill_root / "hooks.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            for relative, contents in scripts.items():
                path = skill_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents, encoding="utf-8")
            catalog.append({"dependencies": [], "externalTools": [], "hosts": hosts, "id": skill_id,
                            "path": f"skills/{folder}/SKILL.md", "profiles": ["fixture"],
                            "provenanceId": skill_id})
        (self.generation / "catalog.json").write_text(
            json.dumps({"schemaVersion": "agent-skills-catalog/1", "skills": catalog}, indent=2) + "\n",
            encoding="utf-8")
        (self.generation / "profiles").mkdir()
        (self.generation / "profiles" / "fixture.json").write_text(json.dumps({
            "id": "fixture", "includes": [], "members": members or order,
            "schemaVersion": "agent-skills-profile/1"}, indent=2) + "\n", encoding="utf-8")
        (self.generation / "release-manifest.json").write_text(json.dumps({
            "profile": "fixture", "releaseId": "fixture-v1", "schemaVersion": "agent-skills-release/1"},
            indent=2) + "\n", encoding="utf-8")

    def run(self, event: str, payload: dict[str, object] | bytes, *, project_dir: bool = True,
            cwd: Path | None = None, timeout: float = 60) -> subprocess.CompletedProcess[str]:
        """Run the generation's own entry point as the installed command does."""
        environment = clean_environment(**({"CLAUDE_PROJECT_DIR": str(self.root)} if project_dir else {}))
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        result = subprocess.run(
            [sys.executable, "-B", str(self.root / ".agent-skills" / "current" / "__main__.py"),
             "hook", "claude-code", event],
            input=data, capture_output=True, cwd=cwd or self.root, env=environment, timeout=timeout, check=False,
        )
        return subprocess.CompletedProcess(result.args, result.returncode, result.stdout.decode(),
                                           result.stderr.decode())


def hook(skill_id: str, name: str, event: str = "stop", *, hosts: list[str] | None = None,
         argv: list[str] | None = None) -> dict[str, object]:
    return {"argv": argv or [f"scripts/{name}.py"], "event": event, "hosts": hosts or ["claude-code"],
            "id": f"{skill_id}.{name}"}


def manifest(*entries: dict[str, object]) -> dict[str, object]:
    return {"hooks": list(entries), "schemaVersion": "skill-hooks/1"}


def single(name: str, script: str, event: str = "stop", *, extra: dict[str, str] | None = None,
           argv: list[str] | None = None) -> dict[str, tuple]:
    """One skill, ``alpha``, declaring one hook ``alpha.<name>`` running ``script``."""
    scripts = {f"scripts/{name}.py": script, **(extra or {})}
    return {"alpha": ("alpha", ["claude-code", "codex"], manifest(hook("alpha", name, event, argv=argv)), scripts)}


def _alive(pid: int) -> bool:
    """Whether ``pid`` is a running process; a zombie waiting for its parent counts as stopped."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return bool(state.stdout.strip()) and not state.stdout.strip().startswith("Z")


def stderr_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stderr.splitlines() if line]


class DispatcherCase(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.directory = Path(self._directory.name).resolve()

    def repository(self, skills: dict[str, tuple], **options: object) -> FixtureRepository:
        directory = Path(tempfile.mkdtemp(dir=self.directory))
        return FixtureRepository(directory, skills, **options)


class SingleHookTests(DispatcherCase):
    def test_allow_prints_nothing(self) -> None:
        repository = self.repository(single("gate", ALLOW))
        result = repository.run("stop", stop_payload(repository.root))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_warn_prints_a_system_message(self) -> None:
        repository = self.repository(single("gate", verdict_script({"verdict": "warn", "reason": "listen first"})))
        result = repository.run("stop", stop_payload(repository.root))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"systemMessage": "listen first"})
        self.assertEqual(result.stderr, "")

    def test_block_with_stop_hook_active_false_prints_a_block(self) -> None:
        repository = self.repository(single("gate", verdict_script({"verdict": "block", "reason": "run lead-wait"})))
        payload = stop_payload(repository.root)
        self.assertIs(payload["stop_hook_active"], False)
        result = repository.run("stop", payload)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"decision": "block", "reason": "run lead-wait"})
        self.assertEqual(result.stderr, "")

    def test_a_payload_missing_stop_hook_active_leaves_a_block_a_block(self) -> None:
        repository = self.repository(single("gate", verdict_script({"verdict": "block", "reason": "held"})))
        payload = stop_payload(repository.root)
        del payload["stop_hook_active"]
        result = repository.run("stop", payload)
        self.assertEqual(json.loads(result.stdout), {"decision": "block", "reason": "held"})


class OneHoldTests(DispatcherCase):
    def test_stop_hook_active_true_turns_a_block_into_a_warn(self) -> None:
        repository = self.repository(single("gate", verdict_script({"verdict": "block", "reason": "run lead-wait"})))
        result = repository.run("stop", stop_payload(repository.root, active=True))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"systemMessage": "run lead-wait"})

    def test_only_exactly_true_counts_as_active(self) -> None:
        repository = self.repository(single("gate", verdict_script({"verdict": "block", "reason": "held"})))
        for value in ("true", 1, None, {"x": True}):
            with self.subTest(value=value):
                result = repository.run("stop", stop_payload(repository.root, active=value))
                self.assertEqual(json.loads(result.stdout), {"decision": "block", "reason": "held"})


class JoinOrderTests(DispatcherCase):
    def test_blocks_join_in_sorted_skill_then_manifest_order(self) -> None:
        # Sorted ids: alpha before bravo. Alpha's manifest lists `second` before
        # `first`. The catalog and the profile list bravo first, and alpha's
        # folder sorts after bravo's, so no other order gives the same result.
        skills = {
            "bravo": ("aaa-bravo-folder", ["claude-code"], manifest(hook("bravo", "only")),
                      {"scripts/only.py": verdict_script({"verdict": "block", "reason": "bravo blocks"})}),
            "alpha": ("zzz-alpha-folder", ["claude-code"], manifest(hook("alpha", "second"), hook("alpha", "first")),
                      {"scripts/second.py": verdict_script({"verdict": "block", "reason": "alpha second blocks"}),
                       "scripts/first.py": verdict_script({"verdict": "warn", "reason": "alpha first warns"})}),
        }
        repository = self.repository(skills, catalog_order=["bravo", "alpha"], members=["bravo", "alpha"])
        listed = sorted(path.name for path in (repository.generation / "skills").iterdir())
        self.assertEqual(listed, ["aaa-bravo-folder", "zzz-alpha-folder"])
        result = repository.run("stop", stop_payload(repository.root))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {
            "decision": "block",
            "reason": "alpha second blocks\nbravo blocks",
            "systemMessage": "alpha first warns",
        })

    def test_warns_alone_allow_and_join_in_run_order(self) -> None:
        skills = {"alpha": ("alpha", ["claude-code"], manifest(hook("alpha", "one"), hook("alpha", "two")),
                            {"scripts/one.py": verdict_script({"verdict": "warn", "reason": "first"}),
                             "scripts/two.py": verdict_script({"verdict": "warn", "reason": "second"})})}
        repository = self.repository(skills)
        result = repository.run("stop", stop_payload(repository.root))
        self.assertEqual(json.loads(result.stdout), {"systemMessage": "first\nsecond"})


FAULTS = {
    "non-zero exit": "import sys\nprint('{\"verdict\": \"block\", \"reason\": \"x\"}')\nsys.exit(3)\n",
    "not JSON": "print('block please')\n",
    "two JSON objects": "print('{\"verdict\": \"allow\"} {\"verdict\": \"block\", \"reason\": \"x\"}')\n",
    "empty output": "",
    "JSON array": verdict_script(["block", "x"]),
    "JSON string": verdict_script("block"),
    "no verdict": verdict_script({"reason": "x"}),
    "unknown verdict": verdict_script({"verdict": "deny", "reason": "x"}),
    "verdict in another case": verdict_script({"verdict": "Block", "reason": "x"}),
    "verdict not a string": verdict_script({"verdict": True, "reason": "x"}),
    "warn without reason": verdict_script({"verdict": "warn"}),
    "block without reason": verdict_script({"verdict": "block"}),
    "block with empty reason": verdict_script({"verdict": "block", "reason": ""}),
    "block with non-string reason": verdict_script({"verdict": "block", "reason": ["x"]}),
    "context on stop": verdict_script({"verdict": "block", "reason": "x", "context": "c"}),
}


class FaultTests(DispatcherCase):
    def test_each_fault_counts_as_allow_with_one_line_naming_the_hook(self) -> None:
        for fault, script in FAULTS.items():
            with self.subTest(fault=fault):
                repository = self.repository(single("faulty", script))
                result = repository.run("stop", stop_payload(repository.root))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                lines = stderr_lines(result)
                self.assertEqual(len(lines), 1, result.stderr)
                self.assertIn("alpha.faulty", lines[0])

    def test_a_non_string_context_on_session_start_is_a_fault(self) -> None:
        for context in (7, None, ["x"]):
            with self.subTest(context=context):
                repository = self.repository(single(
                    "orient", verdict_script({"verdict": "allow", "context": context}), "session-start"))
                result = repository.run("session-start", start_payload(repository.root))
                self.assertEqual((result.returncode, result.stdout), (0, ""))
                lines = stderr_lines(result)
                self.assertEqual(len(lines), 1, result.stderr)
                self.assertIn("alpha.orient", lines[0])

    def test_a_fault_beside_a_valid_block_still_blocks(self) -> None:
        skills = {"alpha": ("alpha", ["claude-code"], manifest(hook("alpha", "broken"), hook("alpha", "gate")),
                            {"scripts/broken.py": FAULTS["unknown verdict"],
                             "scripts/gate.py": verdict_script({"verdict": "block", "reason": "real block"})})}
        repository = self.repository(skills)
        result = repository.run("stop", stop_payload(repository.root))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"decision": "block", "reason": "real block"})
        lines = stderr_lines(result)
        self.assertEqual(len(lines), 1)
        self.assertIn("alpha.broken", lines[0])
        self.assertNotIn("alpha.gate", lines[0])

    def test_every_hook_failing_still_exits_zero_and_allows(self) -> None:
        names = ["exit", "text", "array", "case"]
        scripts = [FAULTS["non-zero exit"], FAULTS["not JSON"], FAULTS["JSON array"], FAULTS["verdict in another case"]]
        skills = {"alpha": ("alpha", ["claude-code"], manifest(*(hook("alpha", name) for name in names)),
                            {f"scripts/{name}.py": script for name, script in zip(names, scripts)})}
        repository = self.repository(skills)
        result = repository.run("stop", stop_payload(repository.root))
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        lines = stderr_lines(result)
        self.assertEqual(len(lines), 4)
        for name, line in zip(names, lines):
            self.assertIn(f"alpha.{name}", line)

    def test_a_missing_script_is_a_fault(self) -> None:
        skills = {"alpha": ("alpha", ["claude-code"], manifest(hook("alpha", "gone")), {})}
        repository = self.repository(skills)
        result = repository.run("stop", stop_payload(repository.root))
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertEqual(len(stderr_lines(result)), 1)
        self.assertIn("alpha.gone", result.stderr)

    def test_an_unreadable_payload_still_runs_the_hooks_and_exits_zero(self) -> None:
        repository = self.repository(single("gate", verdict_script({"verdict": "block", "reason": "held"})))
        result = repository.run("stop", b"not json at all")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"decision": "block", "reason": "held"})


class InProcess(DispatcherCase):
    """Rows that call the dispatcher in this process, to scale its budget and watch its process starts."""

    def dispatch(self, repository: FixtureRepository, event: str, payload: dict[str, object]) -> tuple[str, list[str]]:
        return hooks.dispatch("claude-code", event, json.dumps(payload).encode(),
                              clean_environment(CLAUDE_PROJECT_DIR=str(repository.root)))


class BudgetTests(InProcess):
    def test_the_dispatcher_finishes_within_the_overall_budget(self) -> None:
        sleeper = "import time\ntime.sleep(30)\n"
        names = ["block", "slow-one", "slow-two", "late"]
        scripts = {
            "scripts/block.py": verdict_script({"verdict": "block", "reason": "early block"}),
            "scripts/slow-one.py": sleeper,
            "scripts/slow-two.py": sleeper,
            "scripts/late.py": "open('late-ran', 'w').close()\n" + ALLOW,
        }
        skills = {"alpha": ("alpha", ["claude-code"], manifest(*(hook("alpha", name) for name in names)), scripts)}
        repository = self.repository(skills, runtime=False)
        with mock.patch.multiple(hooks, OVERALL_BUDGET=3.0, HOOK_BUDGET=1.2, MINIMUM_START=0.8):
            started = time.monotonic()
            output, errors = self.dispatch(repository, "stop", stop_payload(repository.root))
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 3.0 + 0.5)
        self.assertEqual(json.loads(output), {"decision": "block", "reason": "early block"})
        self.assertEqual(len(errors), 3, errors)
        self.assertIn("alpha.slow-one", errors[0])
        self.assertIn("timed out", errors[0])
        self.assertIn("alpha.slow-two", errors[1])
        self.assertIn("timed out", errors[1])
        self.assertIn("alpha.late", errors[2])
        self.assertIn("skipped", errors[2])
        self.assertFalse((repository.root / "late-ran").exists())

    def test_a_timed_out_hook_whose_child_holds_its_output_still_returns(self) -> None:
        # The hook leaves a child behind that keeps the hook's stdout open. The
        # dispatcher must not wait for that pipe to close.
        script = ("import subprocess, sys, time\n"
                  "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                  "open('child.pid', 'w').write(str(child.pid))\n"
                  "time.sleep(30)\n")
        repository = self.repository(single("holder", script), runtime=False)
        with mock.patch.multiple(hooks, OVERALL_BUDGET=3.0, HOOK_BUDGET=1.0, MINIMUM_START=0.5):
            started = time.monotonic()
            output, errors = self.dispatch(repository, "stop", stop_payload(repository.root))
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 3.0)
        self.assertEqual(output, "")
        self.assertEqual(len(errors), 1)
        self.assertIn("alpha.holder", errors[0])
        self.assertIn("timed out", errors[0])
        # The child the hook left behind was stopped with it, not orphaned.
        child = int((repository.root / "child.pid").read_text(encoding="utf-8"))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _alive(child):
            time.sleep(0.05)
        self.assertFalse(_alive(child), f"the hook's child {child} is still running")

    def test_the_real_budget_sits_under_the_entry_timeout(self) -> None:
        self.assertEqual((hooks.OVERALL_BUDGET, hooks.HOOK_BUDGET, hooks.MINIMUM_START), (25.0, 20.0, 2.0))


class FilterTests(InProcess):
    def test_a_codex_only_hook_is_neither_run_nor_counted(self) -> None:
        script = "open('codex-ran', 'w').close()\n" + verdict_script({"verdict": "block", "reason": "codex"})
        skills = {"alpha": ("alpha", ["claude-code", "codex"],
                            manifest(hook("alpha", "codex-only", hosts=["codex"])),
                            {"scripts/codex-only.py": script})}
        repository = self.repository(skills)
        result = repository.run("stop", stop_payload(repository.root))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertFalse((repository.root / "codex-ran").exists())
        self.assertEqual(hooks.declared_hooks(repository.generation, "claude-code", "stop")[0], [])

    def test_no_hook_for_the_event_prints_nothing_and_starts_no_process(self) -> None:
        repository = self.repository(single("gate", verdict_script({"verdict": "block", "reason": "x"})),
                                     runtime=False)
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("a process started")) as popen:
            output, errors = self.dispatch(repository, "session-start", start_payload(repository.root))
        self.assertEqual((output, errors), ("", []))
        popen.assert_not_called()

    def test_no_manifest_at_all_prints_nothing_and_starts_no_process(self) -> None:
        skills = {"alpha": ("alpha", ["claude-code"], None, {})}
        repository = self.repository(skills)
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("a process started")) as popen:
            output, errors = self.dispatch(repository, "stop", stop_payload(repository.root))
        self.assertEqual((output, errors), ("", []))
        popen.assert_not_called()
        result = repository.run("stop", stop_payload(repository.root))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_no_generation_prints_nothing(self) -> None:
        repository = self.repository(single("gate", verdict_script({"verdict": "block", "reason": "x"})),
                                     runtime=False)
        (repository.root / ".agent-skills" / "current").unlink()
        output, errors = self.dispatch(repository, "stop", stop_payload(repository.root))
        self.assertEqual((output, errors), ("", []))


class ProjectRootTests(DispatcherCase):
    def test_without_the_project_variable_the_root_is_the_payload_cwds_top_level(self) -> None:
        script = "import os\n" + "import json\nprint(json.dumps({'verdict': 'block', 'reason': os.getcwd()}))\n"
        repository = self.repository(single("where", script))
        nested = repository.root / "deep" / "er"
        nested.mkdir(parents=True)
        elsewhere = self.directory / "elsewhere"
        elsewhere.mkdir()
        result = repository.run("stop", stop_payload(nested), project_dir=False, cwd=elsewhere)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"decision": "block", "reason": str(repository.root)})

    def test_the_project_variable_wins_over_the_payload_cwd(self) -> None:
        script = "import os, json\nprint(json.dumps({'verdict': 'block', 'reason': os.getcwd()}))\n"
        repository = self.repository(single("where", script))
        other = self.directory / "other-repository"
        other.mkdir()
        subprocess.run(["git", "init", "--quiet", str(other)], check=True, capture_output=True)
        result = repository.run("stop", stop_payload(other))
        self.assertEqual(json.loads(result.stdout), {"decision": "block", "reason": str(repository.root)})

    def test_no_root_at_all_allows_with_one_line(self) -> None:
        repository = self.repository(single("gate", verdict_script({"verdict": "block", "reason": "x"})))
        outside = self.directory / "not-a-repository"
        outside.mkdir()
        result = repository.run("stop", stop_payload(outside), project_dir=False, cwd=outside)
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertEqual(len(stderr_lines(result)), 1, result.stderr)


class NoShellTests(DispatcherCase):
    def test_argv_items_reach_the_hook_verbatim(self) -> None:
        hostile = ["$(touch pwned)", "`touch pwned`", "a; touch pwned", "x | y && z", "*", "'\"", "$HOME"]
        script = "import json, sys\nprint(json.dumps({'verdict': 'block', 'reason': json.dumps(sys.argv[1:])}))\n"
        repository = self.repository(single("echo", script, argv=["scripts/echo.py", *hostile]))
        result = repository.run("stop", stop_payload(repository.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(json.loads(result.stdout)["reason"]), hostile)
        self.assertFalse((repository.root / "pwned").exists())

    def test_the_hook_reads_the_payload_on_stdin_and_runs_without_bytecode(self) -> None:
        script = ("import json, os, sys\npayload = json.load(sys.stdin)\n"
                  "print(json.dumps({'verdict': 'block', 'reason': json.dumps("
                  "[payload['session_id'], os.environ.get('PYTHONDONTWRITEBYTECODE'), sys.dont_write_bytecode])}))\n")
        repository = self.repository(single("stdin", script))
        result = repository.run("stop", stop_payload(repository.root))
        self.assertEqual(json.loads(json.loads(result.stdout)["reason"]), ["0b8f2c1e-session", "1", True])

    def test_a_script_outside_the_skill_is_not_run(self) -> None:
        for argv in (["../beta/scripts/x.py"], ["/bin/sh"], ["scripts/../../beta/scripts/x.py"]):
            with self.subTest(argv=argv):
                skills = {
                    "alpha": ("alpha", ["claude-code"], manifest(hook("alpha", "escape", argv=argv)), {}),
                    "beta": ("beta", ["claude-code"], None,
                             {"scripts/x.py": "open('escaped', 'w').close()\n"
                                              + verdict_script({"verdict": "block", "reason": "escaped"})}),
                }
                repository = self.repository(skills)
                result = repository.run("stop", stop_payload(repository.root))
                self.assertEqual((result.returncode, result.stdout), (0, ""))
                self.assertFalse((repository.root / "escaped").exists())
                self.assertIn("alpha.escape", result.stderr)


class SessionStartTests(DispatcherCase):
    def test_contexts_join_in_run_order_and_print_hook_specific_output(self) -> None:
        skills = {
            "bravo": ("bravo", ["claude-code"], manifest(hook("bravo", "tail", "session-start")),
                      {"scripts/tail.py": verdict_script({"verdict": "allow", "context": "from bravo"})}),
            "alpha": ("alpha", ["claude-code"], manifest(hook("alpha", "head", "session-start"),
                                                         hook("alpha", "quiet", "session-start")),
                      {"scripts/head.py": verdict_script({"verdict": "allow", "context": "from alpha"}),
                       "scripts/quiet.py": ALLOW}),
        }
        repository = self.repository(skills, catalog_order=["bravo", "alpha"])
        for source in ("startup", "resume", "clear", "compact"):
            with self.subTest(source=source):
                result = repository.run("session-start", start_payload(repository.root, source))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout), {"hookSpecificOutput": {
                    "additionalContext": "from alpha\nfrom bravo", "hookEventName": "SessionStart"}})

    def test_the_joined_context_is_cut_at_nine_thousand_characters(self) -> None:
        skills = {"alpha": ("alpha", ["claude-code"], manifest(hook("alpha", "one", "session-start"),
                                                               hook("alpha", "two", "session-start")),
                            {"scripts/one.py": verdict_script({"verdict": "allow", "context": "a" * 6000}),
                             "scripts/two.py": verdict_script({"verdict": "warn", "reason": "note",
                                                               "context": "b" * 6000})})}
        repository = self.repository(skills)
        result = repository.run("session-start", start_payload(repository.root))
        output = json.loads(result.stdout)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(len(context), 9000)
        self.assertEqual(context, ("a" * 6000 + "\n" + "b" * 6000)[:9000])
        self.assertEqual(output["systemMessage"], "note")

    def test_an_empty_context_gives_no_output(self) -> None:
        repository = self.repository(single("orient", verdict_script({"verdict": "allow", "context": ""}),
                                            "session-start"))
        result = repository.run("session-start", start_payload(repository.root))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_a_stop_hook_does_not_run_on_session_start(self) -> None:
        repository = self.repository(single("gate", "open('ran', 'w').close()\n" + ALLOW))
        result = repository.run("session-start", start_payload(repository.root))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertFalse((repository.root / "ran").exists())


class BytecodeTests(DispatcherCase):
    def test_a_dispatch_leaves_no_bytecode_in_the_generation(self) -> None:
        script = ("import json, subprocess, sys\nfrom pathlib import Path\n"
                  "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
                  "import sibling\n"
                  "here = str(Path(__file__).resolve().parent)\n"
                  "subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, sys.argv[1]); import child_module',"
                  " here], check=True)\n"
                  "print(json.dumps({'verdict': 'block', 'reason': sibling.REASON}))\n")
        extra = {"scripts/sibling.py": "REASON = 'from a sibling'\n", "scripts/child_module.py": "VALUE = 1\n"}
        repository = self.repository(single("imports", script, extra=extra))
        result = repository.run("stop", stop_payload(repository.root))
        self.assertEqual(json.loads(result.stdout), {"decision": "block", "reason": "from a sibling"},
                         result.stderr)
        left = [str(path) for path in repository.generation.rglob("*")
                if path.name == "__pycache__" or path.suffix == ".pyc"]
        self.assertEqual(left, [])

    def test_the_control_writes_bytecode_without_the_guards(self) -> None:
        # The same import, run without -B or the variable, does write bytecode
        # here, so the row above can't pass because nothing ever would.
        repository = self.repository(single("imports", ALLOW, extra={"scripts/sibling.py": "VALUE = 1\n"}),
                                     runtime=False)
        folder = repository.generation / "skills" / "alpha" / "scripts"
        subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import sibling",
                        str(folder)], check=True, env=clean_environment())
        self.assertTrue((folder / "__pycache__").is_dir())


class ManifestFindingTests(unittest.TestCase):
    """The checks `validate.py` runs on one skill's manifest, before the corpus rows in test_registry."""

    OUTPUTS = ("skills/alpha/SKILL.md", "skills/alpha/hooks.json", "skills/alpha/scripts/gate.py")

    def findings(self, document: object, *, hosts: tuple[str, ...] = ("claude-code", "codex")) -> list[str]:
        return [finding.rule_id for finding in hooks.manifest_findings(
            document, skill_id="alpha", skill_folder="skills/alpha", skill_hosts=hosts, outputs=self.OUTPUTS)]

    def valid(self) -> dict[str, object]:
        return manifest(hook("alpha", "gate", argv=["scripts/gate.py", "--mode", "x y"]),
                        hook("alpha", "orient", "session-start", hosts=["claude-code", "codex"],
                             argv=["scripts/gate.py"]))

    def test_a_manifest_matching_the_contract_validates(self) -> None:
        self.assertEqual(self.findings(self.valid()), [])

    def test_schema_failures(self) -> None:
        cases = {
            "not an object": [],
            "wrong version": {"hooks": [], "schemaVersion": "skill-hooks/2"},
            "extra member": {**self.valid(), "extra": 1},
            "hooks not a list": {"hooks": {}, "schemaVersion": "skill-hooks/1"},
            "hook not an object": manifest("alpha.gate"),
            "missing argv": manifest({k: v for k, v in hook("alpha", "gate").items() if k != "argv"}),
            "empty argv": manifest(hook("alpha", "gate") | {"argv": []}),
            "argv item not a string": manifest(hook("alpha", "gate") | {"argv": ["scripts/gate.py", 3]}),
            "empty hosts": manifest(hook("alpha", "gate") | {"hosts": []}),
            "repeated host": manifest(hook("alpha", "gate") | {"hosts": ["claude-code", "claude-code"]}),
            "id not lowercase words": manifest(hook("alpha", "gate") | {"id": "alpha.Gate_Hook"}),
            "id without a name": manifest(hook("alpha", "gate") | {"id": "alpha"}),
            "id of another skill": manifest(hook("alpha", "gate") | {"id": "beta.gate"}),
            "event not a string": manifest(hook("alpha", "gate") | {"event": ["stop"]}),
            "unknown member on a hook": manifest(hook("alpha", "gate") | {"matcher": "*"}),
        }
        for name, document in cases.items():
            with self.subTest(case=name):
                self.assertEqual(self.findings(document), ["hooks.schema"])

    def test_an_unknown_event(self) -> None:
        for event in ("pre-tool-use", "Stop", "session_start"):
            with self.subTest(event=event):
                self.assertEqual(self.findings(manifest(hook("alpha", "gate", event))), ["hooks.event"])

    def test_a_host_outside_the_skills_hosts(self) -> None:
        self.assertEqual(self.findings(manifest(hook("alpha", "gate", hosts=["codex"])), hosts=("claude-code",)),
                         ["hooks.host"])
        self.assertEqual(self.findings(manifest(hook("alpha", "gate", hosts=["cursor"]))), ["hooks.host"])

    def test_a_script_outside_the_skill(self) -> None:
        for script in ("../beta/scripts/gate.py", "/usr/bin/python3", "scripts/../../beta/x.py", "scripts\\gate.py",
                       "./scripts/gate.py"):
            with self.subTest(script=script):
                self.assertEqual(self.findings(manifest(hook("alpha", "gate", argv=[script]))), ["hooks.argv"])

    def test_a_script_not_among_the_skills_outputs(self) -> None:
        self.assertEqual(self.findings(manifest(hook("alpha", "gate", argv=["scripts/missing.py"]))), ["hooks.argv"])
        self.assertEqual(self.findings(manifest(hook("alpha", "gate", argv=["scripts"]))), ["hooks.argv"])

    def test_the_schema_file_agrees_with_the_shape_checks(self) -> None:
        try:
            import jsonschema
        except ImportError:  # pragma: no cover - the test extra carries it
            self.skipTest("jsonschema is not installed")
        schema = json.loads((ROOT / "schemas" / "skill-hooks.schema.json").read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        self.assertEqual(list(validator.iter_errors(self.valid())), [])
        for document in ([], {"hooks": [], "schemaVersion": "skill-hooks/2"},
                         manifest(hook("alpha", "gate") | {"argv": []}),
                         manifest(hook("alpha", "gate") | {"hosts": []}),
                         manifest(hook("alpha", "gate") | {"id": "alpha.Gate"}),
                         manifest(hook("alpha", "gate", "pre-tool-use")),
                         manifest(hook("alpha", "gate", hosts=["cursor"])),
                         manifest(hook("alpha", "gate") | {"matcher": "*"})):
            with self.subTest(document=document):
                self.assertNotEqual(list(validator.iter_errors(document)), [])
                self.assertNotEqual(self.findings(document), [])


if __name__ == "__main__":
    unittest.main()
