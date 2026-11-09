"""The declared posture through what ships: the built runtime's resolver, a built product's four commands, and
the same vectors `run_delivery.py`'s reader answers."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

from agent_skills.lifecycle import Lifecycle
from built_product import build_product
from lifecycle_support import initialize_repository, run_git

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "product-runtime" / "kernel.mjs"
VECTORS = ROOT / "tests" / "fixtures" / "delivery-posture-vectors.json"
POSTURE_PATH = ".agents/delivery-posture.json"
COMMANDS = ("verify", "gate", "record", "resume")
# What each refuses with on a candidate with no record and no preparation, whatever `gate` case the base is in.
GATED_BLOCKERS = {"verify": "[delivery_record_missing]", "gate": "[preparation_missing]", "record": "[preparation_missing]", "resume": "[resume_context_missing]"}
# A hanging git is stopped at the resolver's own 15 second bound, and it reads more than once in the worst case.
READ_LIMIT_SECONDS = 90
# Valid for the runtime's config check, and as erasable TypeScript it needs nothing installed.
CONSUMER_CONFIG = json.loads(r"""{"gateId": "posture-fixture.pr-admission", "baseRef": "origin/main", "storageNamespace": "delivery-harness/", "acceptedEnvelopeSpecs": ["delivery-evidence/1"], "identityVersions": ["delivery-harness-tree/v1"], "computingIdentityVersion": "delivery-harness-tree/v1", "reviewNeutral": [{"prefix": "docs/reports/"}, {"prefix": "docs/solutions/"}, {"prefix": "telemetry/delivery-runs/"}, {"prefix": "delivery/records/"}], "recordNeutral": [{"prefix": "delivery/records/"}], "pathClassification": {"generated": [{"kind": "prefix", "value": "packages/conformance/vectors/"}], "test": [{"kind": "glob", "value": "**/*.test.ts"}], "lockfile": [{"kind": "glob", "value": "**/package-lock.json"}]}, "activationThreshold": 1, "providers": [{"id": "delivery-harness.independent-review", "findingCodes": []}], "agentEnvSignals": ["CLAUDE_CODE", "CLAUDECODE"], "ciPolicies": [{"id": "github-actions", "requiredEnv": [{"variable": "GITHUB_ACTIONS", "equals": "true"}, {"variable": "CI", "equals": "true"}]}], "ciPolicyEnvKey": "DELIVERY_HARNESS_CI_POLICY", "preparationWiringPaths": ["harness.config.ts"], "preparationCommands": [], "obligations": [{"id": "review.green", "activation": {"kind": "relevant_change"}, "freshness": "exact_candidate", "providers": ["delivery-harness.independent-review"], "acceptedPayloadSpecs": ["review.green/2", "review.green/1"], "allowedResolutionKinds": ["satisfied_evidence", "waived", "not_applicable"], "humanWaiverAllowed": true, "minimumAttestationLevel": "self", "ciDelegationPolicyIds": [], "remediation": {"default": [{"id": "run-the-review", "summary": "Run the code review and submit its evidence manifest with `delivery-harness submit-evidence`.", "kind": "manual_action"}]}, "waivableCodes": ["review_evidence_missing", "stale_evidence", "evidence_not_green", "unresolved_actionable_findings"], "nonWaivableCodes": ["ambiguous_records", "malformed_record", "unknown_provider", "live_provider_missing", "ambiguous_live_provider", "live_provider_failed", "resolution_not_allowed"]}], "deliveryRecordPath": "delivery/records/record.json", "deliveryRecordVerification": {"baseMovement": "stale"}, "sensitivePaths": [{"id": "gate-kernel", "patterns": [{"kind": "prefix", "value": "packages/kernel/src/"}]}]}""")
RESOLVE_SCRIPT = """
const { resolveDeliveryPosture } = await import(process.argv[1]);
console.log(JSON.stringify(await resolveDeliveryPosture({ rootDir: process.argv[2], baseRef: process.argv[3] })));
"""


def declaration(posture: str) -> str:
    return json.dumps({"schemaVersion": "delivery-posture/1", "posture": posture, "reason": "A fixture's reason."}) + "\n"


def resolved_by_runtime(clone: Path, env: dict[str, str] | None = None, base: str = "origin/main") -> dict:
    """The exported resolver in the built `kernel.mjs`, by itself in a real Node process.

    `git` in the environment may be a stub, so Node is found first, from this process's own environment."""
    node = subprocess.run(["/bin/sh", "-c", "command -v node"], capture_output=True, text=True, check=True).stdout.strip()
    result = subprocess.run([node, "--no-warnings", "--input-type=module", "-e", RESOLVE_SCRIPT, KERNEL.as_uri(), str(clone), base],
                            env=env, capture_output=True, text=True, timeout=READ_LIMIT_SECONDS)
    if result.returncode != 0:
        raise AssertionError(f"the runtime's resolver failed: {result.stderr}")
    return json.loads(result.stdout)


class RuntimeExportTests(unittest.TestCase):
    def test_the_built_kernel_exports_the_resolver_and_its_result_type(self) -> None:
        types = (ROOT / "product-runtime" / "kernel.d.mts").read_text(encoding="utf-8")
        # The export statements, since an interface declared but left out of them also holds the name.
        exported = " ".join(re.findall(r"export (?:type )?\{[^}]*\}", types))
        self.assertRegex(exported, r"\bresolveDeliveryPosture\b")
        self.assertRegex(exported, r"\bDeliveryPostureResult\b")
        with tempfile.TemporaryDirectory() as folder:
            result = resolved_by_runtime(Path(folder))
        self.assertEqual(result["posture"], "gate")
        self.assertEqual(result["code"], "posture_unreadable")
        self.assertEqual(result["source"], "fallback")
        self.assertEqual(set(result), {"posture", "reason", "source", "baseTipSha", "code"})


@unittest.skipIf(os.name == "nt", "POSIX git")
class RuntimeTrustTests(unittest.TestCase):
    """The rules only the harness's reader holds, which the shared vectors leave out."""

    def repository(self, folder: Path) -> tuple[Path, Path]:
        """(a clone whose origin's default is main, a seed that publishes to that origin)."""
        remote, seed, clone = folder / "remote.git", folder / "seed", folder / "clone"
        for path in (seed,):
            path.mkdir()
        run_git(folder, "init", "-q", "--bare", "--initial-branch=main", str(remote))
        run_git(seed, "init", "-q", "--initial-branch=main")
        run_git(seed, "config", "user.email", "tests@example.invalid")
        run_git(seed, "config", "user.name", "Posture Tests")
        run_git(seed, "remote", "add", "origin", str(remote))
        self.publish(seed, None)
        run_git(folder, "clone", "-q", str(remote), str(clone))
        return clone, seed

    def publish(self, seed: Path, content: str | None, branch: str = "main") -> str:
        path = seed / POSTURE_PATH
        path.parent.mkdir(exist_ok=True)
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(content, encoding="utf-8")
        (seed / "touched").write_text(run_git(seed, "rev-parse", "--short", "HEAD") if (seed / ".git/refs/heads/main").exists() else "first", encoding="utf-8")
        run_git(seed, "add", "-A")
        run_git(seed, "commit", "-qm", "publish", "--allow-empty")
        run_git(seed, "push", "-qf", "origin", f"HEAD:refs/heads/{branch}")
        return run_git(seed, "rev-parse", "HEAD")

    def test_a_branch_naming_itself_as_the_base_gets_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            clone, seed = self.repository(Path(folder))
            self.publish(seed, declaration("focused"), "self")
            run_git(clone, "fetch", "-q", "origin")
            result = resolved_by_runtime(clone, base="origin/self")
            self.assertEqual((result["posture"], result["code"], result["source"]), ("gate", "posture_unreadable", "fallback"))

    def test_a_read_finds_the_new_tip_and_leaves_the_tracking_ref(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            clone, seed = self.repository(Path(folder))
            before = run_git(clone, "rev-parse", "refs/remotes/origin/main")
            tip = self.publish(seed, declaration("focused"))
            result = resolved_by_runtime(clone)
            self.assertEqual((result["posture"], result["baseTipSha"], result["code"]), ("focused", tip, None))
            self.assertEqual(run_git(clone, "rev-parse", "refs/remotes/origin/main"), before)

    def test_a_failed_fetch_needs_both_refs_to_say_focused(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            clone, seed = self.repository(Path(folder))
            earlier = self.publish(seed, declaration("gate"))
            resolved_by_runtime(clone)
            self.publish(seed, declaration("focused"))
            run_git(clone, "fetch", "-q", "origin")
            run_git(clone, "remote", "set-url", "origin", str(Path(folder) / "gone.git"))
            result = resolved_by_runtime(clone)
            self.assertEqual((result["posture"], result["baseTipSha"], result["code"]), ("gate", earlier, None))

    def test_offline_a_branch_naming_itself_as_the_base_gets_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            clone, seed = self.repository(Path(folder))
            self.publish(seed, declaration("focused"), "self")
            run_git(clone, "fetch", "-q", "origin")
            run_git(clone, "remote", "set-url", "origin", str(Path(folder) / "gone.git"))
            result = resolved_by_runtime(clone, base="origin/self")
            self.assertEqual((result["posture"], result["code"], result["source"]), ("gate", "posture_unreadable", "fallback"))

    def test_a_failed_fetch_does_not_read_a_stale_focused_over_a_restored_gate(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            clone, seed = self.repository(Path(folder))
            self.publish(seed, declaration("focused"))
            resolved_by_runtime(clone)
            tip = self.publish(seed, declaration("gate"))
            run_git(clone, "fetch", "-q", "origin")
            run_git(clone, "remote", "set-url", "origin", str(Path(folder) / "gone.git"))
            result = resolved_by_runtime(clone)
            self.assertEqual((result["posture"], result["baseTipSha"], result["code"]), ("gate", tip, None))

    def test_a_failed_fetch_with_two_focused_tips_reports_the_tracking_refs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            clone, seed = self.repository(Path(folder))
            self.publish(seed, declaration("focused"))
            resolved_by_runtime(clone)
            tip = self.publish(seed, declaration("focused"))
            run_git(clone, "fetch", "-q", "origin")
            run_git(clone, "remote", "set-url", "origin", str(Path(folder) / "gone.git"))
            result = resolved_by_runtime(clone)
            self.assertEqual((result["posture"], result["baseTipSha"], result["code"]), ("focused", tip, None))

    def test_a_repository_in_the_tree_does_not_answer_for_an_unconfigured_origin(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            clone, seed = self.repository(Path(folder))
            self.publish(seed, declaration("focused"))
            run_git(Path(folder), "clone", "-q", "--bare", str(Path(folder) / "remote.git"), str(clone / "origin"))
            run_git(clone, "remote", "remove", "origin")
            self.assertIn("refs/heads/main", run_git(clone, "ls-remote", "origin", "refs/heads/main"))
            result = resolved_by_runtime(clone)
            self.assertEqual((result["posture"], result["code"], result["source"]), ("gate", "posture_unreadable", "fallback"))


@unittest.skipIf(os.name == "nt", "POSIX product entry")
class BuiltProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.environment = mock.patch.dict(os.environ)
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)
        # A pull-request CI event or a declared non-maintainer context would make the install refuse.
        os.environ.pop("GITHUB_EVENT_NAME", None)
        os.environ.pop("AGENT_SKILLS_EXECUTION_CONTEXT", None)
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        cls.archive, cls.metadata = build_product(Path(cls.folder.name), "linear", "posture-fixture")

    def consumer(self, folder: Path, declared: str | None) -> Path:
        """An adopter with the product installed, a real origin, and this at the path on its base, if anything."""
        repository = initialize_repository(folder)
        run_git(repository, "branch", "-M", "main")
        (repository / "harness.config.ts").write_text(f"export default {json.dumps(CONSUMER_CONFIG)};\n", encoding="utf-8")
        if declared is not None:
            (repository / ".agents").mkdir(exist_ok=True)
            (repository / POSTURE_PATH).write_text(declared, encoding="utf-8")
        run_git(repository, "add", "-A")
        run_git(repository, "commit", "-qm", "the declaration")
        remote = folder / "remote.git"
        run_git(folder, "init", "-q", "--bare", "--initial-branch=main", str(remote))
        run_git(repository, "remote", "add", "origin", str(remote))
        run_git(repository, "push", "-q", "origin", "main")
        run_git(repository, "fetch", "-q", "origin")
        run_git(repository, "remote", "set-head", "origin", "main")
        run_git(repository, "checkout", "-q", "-b", "candidate")
        Lifecycle(repository).install(self.archive, self.metadata, maintainer=True)
        # Committed, or every gated command refuses on the untracked install rather than for its own reason.
        run_git(repository, "add", "-A")
        run_git(repository, "commit", "-qm", "the install")
        return repository

    def harness(self, repository: Path, *command: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-B", str(repository / ".agent-skills/current"), "--root", str(repository), "harness", *command],
                              text=True, capture_output=True, timeout=READ_LIMIT_SECONDS)

    def written(self, repository: Path) -> dict[str, bytes]:
        """Every file a command could write: the tree, and the harness's stores in the git directory."""
        roots = [repository, repository / ".git/managed-delivery", repository / ".git/delivery-harness"]
        written: dict[str, bytes] = {}
        for root in roots:
            for path in root.rglob("*") if root.exists() else ():
                relative = path.relative_to(repository).as_posix()
                if path.is_file() and not path.is_symlink() and not relative.startswith(".agent-skills/") and (root != repository or not relative.startswith(".git/")):
                    written[relative] = path.read_bytes()
        return written

    def test_each_command_exits_0_naming_the_posture_and_source_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repository = self.consumer(Path(folder), declaration("focused"))
            tip = run_git(repository, "rev-parse", "refs/remotes/origin/main")
            # A current run, so that a command journaling its completion would show.
            started = self.harness(repository, "emit", "run.started", "--json", json.dumps({"host": "codex", "workflow": {"releaseId": "fixture", "profile": "linear"}}))
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            before = self.written(repository)
            self.assertTrue([name for name in before if name.startswith(".git/managed-delivery/")], "the run store isn't in use")
            for command in COMMANDS:
                with self.subTest(command=command):
                    result = self.harness(repository, command)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, "outcome: focused\nposture: focused\nreason: A fixture's reason.\n"
                                                    f"source: origin/main\nbase tip: {tip}\n")
                    self.assertEqual(self.written(repository), before)

    def assert_gated(self, declared: str | None, first_lines: dict[str, list[str]], then: Callable[[Path], None] | None = None) -> None:
        """The command's own refusal and nothing else on stdout, after any lines the case adds on stderr.

        ``then`` changes the installed consumer before any command runs."""
        with tempfile.TemporaryDirectory() as folder:
            repository = self.consumer(Path(folder), declared)
            if then is not None:
                then(repository)
            for command, blocker in GATED_BLOCKERS.items():
                with self.subTest(command=command):
                    result = self.harness(repository, command)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, "")
                    # Node's own warnings differ by version, and none is the command's.
                    lines = [line for line in result.stderr.splitlines() if not line.startswith("(node:") and "--trace-warnings" not in line]
                    expected = first_lines.get(command, [])
                    for index, prefix in enumerate(expected):
                        self.assertTrue(lines[index].startswith(prefix), lines)
                    self.assertTrue(lines[len(expected)].startswith(blocker), lines)

    def test_each_command_gives_only_its_own_refusal_under_a_valid_gate(self) -> None:
        self.assert_gated(declaration("gate"), {})

    def test_each_command_gives_only_its_own_refusal_with_no_declaration(self) -> None:
        self.assert_gated(None, {})

    def test_each_command_gives_only_its_own_refusal_with_a_plain_base_that_reads_as_unreadable(self) -> None:
        # Focused at the tip a plain `main` names, so only the rule that the base names origin/<branch> gates it.
        with mock.patch.dict(CONSUMER_CONFIG, {"baseRef": "main"}):
            self.assert_gated(declaration("focused"), {})

    def test_a_pushed_branch_naming_itself_as_the_base_while_carrying_focused_is_gated(self) -> None:
        # Only the default branch answers, so a candidate can't declare its own posture by naming itself as the base.
        def publish_focused(repository: Path) -> None:
            (repository / ".agents").mkdir(exist_ok=True)
            (repository / POSTURE_PATH).write_text(declaration("focused"), encoding="utf-8")
            run_git(repository, "add", "-A")
            run_git(repository, "commit", "-qm", "the candidate declares focused")
            run_git(repository, "push", "-q", "origin", "candidate")
            run_git(repository, "fetch", "-q", "origin")
            self.assertEqual(run_git(repository, "rev-parse", "refs/remotes/origin/candidate"), run_git(repository, "rev-parse", "HEAD"))
            self.assertEqual(run_git(repository, "symbolic-ref", "refs/remotes/origin/HEAD"), "refs/remotes/origin/main")

        with mock.patch.dict(CONSUMER_CONFIG, {"baseRef": "origin/candidate"}):
            self.assert_gated(None, {}, publish_focused)

    def test_a_malformed_declaration_adds_its_code_and_reason_before_the_refusal(self) -> None:
        code = f"posture_malformed: {POSTURE_PATH} on origin/main is malformed"
        self.assert_gated("not json\n", {command: [code] for command in GATED_BLOCKERS})

    def installed_cli(self, repository: Path, directory: Path, *command: str) -> subprocess.CompletedProcess:
        """The installed runtime's own CLI, run from ``directory``.

        The launcher only runs at its repository's top level, so this is how a command reaches the resolver from any other
        directory: the way a host that runs the runtime directly, or from a subdirectory, would."""
        runtime = next((repository / ".agent-skills" / "generations").glob("*/runtime"))
        node = subprocess.run(["/bin/sh", "-c", "command -v node"], capture_output=True, text=True, check=True).stdout.strip()
        return subprocess.run([node, "--experimental-strip-types", "--import", (runtime / "bootstrap.mjs").as_uri(), str(runtime / "cli.mjs"), *command],
                              cwd=directory, text=True, capture_output=True, timeout=READ_LIMIT_SECONDS)

    def assert_refused_outside_root(self, result: subprocess.CompletedProcess, directory: Path) -> None:
        """Gated, with the not-a-root line on stderr ahead of verify's own blocker, and nothing on stdout."""
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        lines = [line for line in result.stderr.splitlines() if not line.startswith("(node:") and "--trace-warnings" not in line]
        self.assertTrue(lines[0].startswith("posture_unreadable: "), lines)
        self.assertIn(f"{directory} is not a repository root it may be read from", lines[0])
        self.assertTrue(lines[1].startswith(GATED_BLOCKERS["verify"]), lines)

    def test_verify_from_a_plain_subdirectory_of_the_root_is_gated(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repository = self.consumer(Path(folder), declaration("focused"))
            subdirectory = repository / "packages" / "app"
            subdirectory.mkdir(parents=True)
            # A package with its own config, as in a monorepo, so the command gets as far as reading the posture.
            (subdirectory / "harness.config.ts").write_text(f"export default {json.dumps(CONSUMER_CONFIG)};\n", encoding="utf-8")
            run_git(repository, "add", "-A")
            run_git(repository, "commit", "-qm", "a package")
            # The control: from the root, the same CLI reads the base's focused.
            control = self.installed_cli(repository, repository, "verify")
            self.assertEqual(control.returncode, 0, control.stdout + control.stderr)
            self.assertEqual(control.stdout, self.focused_outcome(repository))
            self.assert_refused_outside_root(self.installed_cli(repository, subdirectory, "verify"), subdirectory.resolve())

    def focused_outcome(self, repository: Path) -> str:
        tip = run_git(repository, "rev-parse", "refs/remotes/origin/main")
        return f"outcome: focused\nposture: focused\nreason: A fixture's reason.\nsource: origin/main\nbase tip: {tip}\n"

    def focused_remote(self, folder: Path) -> Path:
        """Another repository's remote, whose main declares focused and carries the same config."""
        other = folder / "other"
        other.mkdir()
        seed = initialize_repository(other)
        run_git(seed, "branch", "-M", "main")
        (seed / "harness.config.ts").write_text(f"export default {json.dumps(CONSUMER_CONFIG)};\n", encoding="utf-8")
        (seed / ".agents").mkdir()
        (seed / POSTURE_PATH).write_text(declaration("focused"), encoding="utf-8")
        run_git(seed, "add", "-A")
        run_git(seed, "commit", "-qm", "focused")
        remote = other / "remote.git"
        run_git(other, "init", "-q", "--bare", "--initial-branch=main", str(remote))
        run_git(seed, "remote", "add", "origin", str(remote))
        run_git(seed, "push", "-q", "origin", "main")
        return remote

    def test_verify_from_a_nested_repository_whose_origin_declares_focused_is_gated(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repository = self.consumer(Path(folder), declaration("focused"))
            remote = self.focused_remote(Path(folder))
            nested = repository / "vendor" / "nested"
            run_git(repository, "clone", "-q", str(remote), str(nested))
            run_git(nested, "remote", "set-head", "origin", "main")
            # The control: from its own place, outside any work tree, the same clone reads its focused.
            alone = Path(folder) / "alone"
            run_git(Path(folder), "clone", "-q", str(remote), str(alone))
            run_git(alone, "remote", "set-head", "origin", "main")
            control = self.installed_cli(repository, alone, "verify")
            self.assertEqual(control.returncode, 0, control.stdout + control.stderr)
            self.assertEqual(control.stdout, self.focused_outcome(alone))
            self.assert_refused_outside_root(self.installed_cli(repository, nested, "verify"), nested.resolve())

    def test_verify_from_an_initialised_submodule_whose_origin_declares_focused_is_gated(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repository = self.consumer(Path(folder), declaration("focused"))
            remote = self.focused_remote(Path(folder))
            run_git(repository, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(remote), "modules/sub")
            run_git(repository, "commit", "-qm", "a submodule")
            sub = repository / "modules" / "sub"
            run_git(sub, "remote", "set-head", "origin", "main")
            self.assert_refused_outside_root(self.installed_cli(repository, sub, "verify"), sub.resolve())

    def test_verify_from_a_clone_whose_parent_is_a_bare_repository_is_gated(self) -> None:
        # No work tree holds the parent, so only the walk over the directories above the clone refuses it.
        with tempfile.TemporaryDirectory() as folder:
            repository = self.consumer(Path(folder), declaration("focused"))
            remote = self.focused_remote(Path(folder))
            holder = Path(folder) / "holder.git"
            run_git(Path(folder), "clone", "-q", "--bare", str(remote), str(holder))
            nested = holder / "nested"
            run_git(Path(folder), "clone", "-q", str(remote), str(nested))
            run_git(nested, "remote", "set-head", "origin", "main")
            self.assert_refused_outside_root(self.installed_cli(repository, nested, "verify"), nested.resolve())

    def test_verify_from_a_linked_worktree_nested_under_the_root_reads_focused(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repository = self.consumer(Path(folder), declaration("focused"))
            lane = repository / ".orchestration" / "lanes" / "lane-one"
            run_git(repository, "worktree", "add", "-q", "--detach", str(lane))
            result = self.harness(lane, "verify")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout, self.focused_outcome(repository))


@unittest.skipIf(os.name == "nt", "POSIX git stubs")
class CrossReaderVectorTests(unittest.TestCase):
    """Both readers answer every vector, so a drift between them shows here.

    The harness's reader also holds the base to the remote's default branch, and every vector's remote has `main` as
    that default, so the rule is not what's compared."""

    @classmethod
    def setUpClass(cls) -> None:
        from tests.test_run_delivery import posture_vector_repo
        cls.posture_vector_repo = staticmethod(posture_vector_repo)
        spec = importlib.util.spec_from_file_location("run_delivery_posture_reader", ROOT / "skills/run-delivery/scripts/run_delivery.py")
        assert spec is not None and spec.loader is not None
        cls.tool = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.tool
        cls.addClassCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(cls.tool)
        cls.file = json.loads(VECTORS.read_text(encoding="utf-8"))

    def test_both_readers_give_each_vector_its_posture_code_and_source(self) -> None:
        self.assertGreater(len(self.file["vectors"]), 0)
        for vector in self.file["vectors"]:
            with self.subTest(vector=vector["id"]), tempfile.TemporaryDirectory() as folder:
                clone, env = self.posture_vector_repo(vector, Path(folder))
                with mock.patch.dict(os.environ, env, clear=True):
                    python = self.tool.resolve_delivery_posture(clone, "origin", "main", seconds=15)
                node_env = dict(env)
                runtime = resolved_by_runtime(clone, node_env)
                pick = lambda result: {key: result[key] for key in ("posture", "code", "source")}
                self.assertEqual(pick(runtime), vector["expect"], runtime)
                self.assertEqual(pick(runtime), pick(python), (runtime, python))


if __name__ == "__main__":
    unittest.main()
