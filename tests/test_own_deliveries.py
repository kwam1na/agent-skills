"""This repository delivers through the generation it has installed: a fresh clone is set up, and the corpus is unmoved."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ".agent-skills/current/skills/run-delivery/scripts/run_delivery.py"


# Every test module, by its module name: the lead selects checks by these ids. A story that adds a module adds its
# entry here and in the binding in the same diff, and both its hand-off and its acceptance can name it from its head.
CATALOGUE_MODULES = (
    "adoption", "bootstrap_qualification", "catalog", "compound_learning_corpus", "core_boundary", "corpus_copy",
    "cut_release", "declared_mechanics_corpus", "delivery_posture_vectors", "delivery_provider",
    "delivery_provider_rails_contract", "delivery_views", "delivery_views_adopter", "delivery_views_history",
    "delivery_views_hosted", "delivery_views_hosted_projection", "delivery_views_page", "delivery_views_plan", "delivery_views_plan_fixtures", "delivery_views_render",
    "delivery_views_review_projection", "delivery_views_snapshots", "diagnosis", "documented_install_surface",
    "fault_injection", "forge_adapters", "historical_qualifications", "hive_knowledge_adapter", "hooks", "host_exposure", "jira_profile", "journal_kinds", "journal_shape", "knowledge_library", "lane_rows",
    "lane_ruling_corpus", "lead_wait", "lifecycle", "linear_profile", "linear_qualification", "measure_test_writes",
    "merge_occurrence", "merge_queue", "narrator", "no_current_set_names", "orchestration_adopter", "orchestration_render",
    "orchestration_state", "own_deliveries", "product_descriptors", "product_launcher", "product_release",
    "profile_inventory", "provenance", "provider_qualification", "qualification", "readme", "receipt", "registry",
    "release_archive", "release_channel", "release_entry_budget", "release_integration", "review_and_compounding",
    "review_base_reopening", "review_bound_corpus", "review_grace", "review_orchestration", "round_brief_template_corpus",
    "router", "run_delivery", "run_delivery_hooks", "run_delivery_hosted", "run_delivery_peers", "run_delivery_plan_review",
    "run_delivery_pr_review",
    "run_delivery_pr_review_gates", "run_delivery_supervisor", "run_delivery_tracker", "run_event_emission_corpus",
    "run_tests", "runless_reviews", "suite_budget", "test_dependency_guard", "test_environment", "tracker_contract", "update_provenance",
    "validate", "waits", "waits_review", "workflow_graph_contract", "workflow_observation", "workflows",
)
CATALOGUE_IDS = tuple("tests.test_" + name for name in CATALOGUE_MODULES)
# Test modules left out of the catalogue on purpose, each as (module name, why): none today.
UNCATALOGUED_MODULES: tuple[tuple[str, str], ...] = ()
# A change to any test module brings the guard below, so a module added without its entry fails in the queue.
TEST_MODULE_READER = {"paths": ["tests/test_*.py"], "checks": ["tests.test_own_deliveries"]}
SERVICE_IDS = ('hosted.install', 'hosted.typecheck', 'hosted.build', 'hosted.test',
               'hosted.browser-install', 'hosted.browser')
GUARDS = ["tests.test_linear_qualification", "tests.test_registry"]
SKILL_PROSE_READERS = ["tests.test_declared_mechanics_corpus", "tests.test_lane_ruling_corpus",
                       "tests.test_review_bound_corpus", "tests.test_round_brief_template_corpus",
                       "tests.test_run_event_emission_corpus"]
# Modules that run as scripts, holding no unittest case.
SCRIPT_MODULES = ("tests.test_registry",)
STATE_TOOL = ".agent-skills/current/skills/orchestrate-work/scripts/delivery_state.py"
PUBLISHER = ".agent-skills/current/skills/run-delivery/scripts/tracker_publisher.py"


def _git(cwd: Path, *args: str) -> str:
    identity = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "-c", "maintenance.auto=false", "-c", "gc.auto=0"]
    return subprocess.run(["git", *identity, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _generation_module(name: str, path: str):
    """A script of the installed generation, imported as its own commands run it, without writing bytecode into it."""
    import importlib.util
    previous, folder = sys.dont_write_bytecode, str((ROOT / path).parent)
    sys.dont_write_bytecode = True
    sys.path.insert(0, folder)
    try:
        spec = importlib.util.spec_from_file_location(name, ROOT / path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
        sys.path.remove(folder)
    return module


def _section(heading: str, following: str) -> str:
    text = " ".join((ROOT / "docs/own-deliveries.md").read_text(encoding="utf-8").split())
    return text[text.index(f"## {heading}"):text.index(f"## {following}")]


class FreshClone(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        top = Path(os.path.realpath(cls.temporary.name))
        # What HEAD holds, on a base of its own, since a checkout under test may have no main branch to clone.
        seed = top / "seed"
        seed.mkdir()
        tree = subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(seed)], input=tree, check=True)
        _git(seed, "init", "-q", "-b", "main")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "seed")
        binding = json.loads((seed / ".agents/orchestration.json").read_text(encoding="utf-8"))
        base = binding["base"]["ref"]
        if base != "main":
            _git(seed, "branch", base)
        cls.remote = top / "remote.git"
        _git(top, "clone", "-q", "--bare", str(seed), str(cls.remote))
        cls.clone = top / "clone"
        _git(top, "clone", "-q", str(cls.remote), str(cls.clone))
        shutil.rmtree(seed)
        python = str(Path(sys.executable).parent)
        cls.environment = {"PATH": os.pathsep.join([python, os.environ["PATH"]]), "HOME": cls.temporary.name,
                           "PYTHONDONTWRITEBYTECODE": "1", "LINEAR_API_KEY": "fixture", "GITHUB_TOKEN": "fixture",
                           "AGENT_SKILLS_DELIVERY_PUBLISH_TOKEN": "fixture-publishing-" + "a" * 32}

    def doctor(self, cwd: Path | None = None, *, without: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
        environment = {key: value for key, value in self.environment.items() if key not in without}
        return subprocess.run(["python3", "-B", TOOL, "doctor", "--offline"], cwd=cwd or self.clone, env=environment,
                              capture_output=True, text=True, timeout=120)

    def test_the_setup_check_passes_from_a_fresh_clone(self) -> None:
        done = self.doctor()
        lines = done.stdout.splitlines()
        self.assertEqual([line for line in lines if line.split()[0] not in ("ok", "skipped")], [], done.stdout + done.stderr)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(any(line.startswith("ok") and "release on the delivery base" in line for line in lines), done.stdout)
        self.assertTrue(any(line.startswith("ok") and "orchestration binding" in line for line in lines), done.stdout)

    def test_missing_publishing_credential_warns_without_selecting_a_delivery(self) -> None:
        done = self.doctor(without=("AGENT_SKILLS_DELIVERY_PUBLISH_TOKEN",))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        warnings = [line for line in done.stdout.splitlines() if line.split()[0] not in ("ok", "skipped")]
        self.assertEqual(len(warnings), 1, done.stdout + done.stderr)
        self.assertRegex(warnings[0], r"^warn\s+hosted delivery: hosted: configured; no delivery selected; "
                                     r"AGENT_SKILLS_DELIVERY_PUBLISH_TOKEN is unset;")
        self.assertNotIn("Traceback", done.stdout + done.stderr)
        self.assertFalse((self.clone / ".orchestration/state").exists())

    def test_a_missing_piece_is_named(self) -> None:
        done = self.doctor(without=("LINEAR_API_KEY",))
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertRegex(done.stdout, r"(?m)^missing\s+tracker snapshot token: LINEAR_API_KEY is unset")

        binding = self.clone / ".agents/orchestration.json"
        kept = binding.read_bytes()
        self.addCleanup(binding.write_bytes, kept)
        binding.unlink()
        done = self.doctor()
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertRegex(done.stdout, r"(?m)^missing\s+orchestration binding: no \.agents/orchestration\.json")

    def test_stray_bytecode_in_the_generation_moves_nothing(self) -> None:
        stray = self.clone / ".agent-skills/current/skills/run-delivery/scripts/__pycache__"
        stray.mkdir()
        self.addCleanup(shutil.rmtree, stray)
        (stray / "run_delivery.cpython-311.pyc").write_bytes(b"stray")
        generation = self.doctor()
        self.assertRegex(generation.stdout, r"(?m)^ok\s+release generation")

        environment = {**self.environment, "PYTHONPATH": str(self.clone)}
        validated = subprocess.run(["python3", "-B", "scripts/validate-registry.py"], cwd=self.clone, env=environment,
                                   capture_output=True, text=True, timeout=120)
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        with tempfile.TemporaryDirectory() as out:
            built = subprocess.run(["python3", "-B", "scripts/cut-release.py", "--version", "v99.0.0-rc.1", "--out", out + "/release"],
                                   cwd=self.clone, env=environment, capture_output=True, text=True, timeout=600)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            self.assertTrue((Path(out) / "release/linear-v99.0.0-rc.1.zip").is_file())


# The archive published and attested for the repository's installed delivery generation.
ATTESTED_ARCHIVES = {
    "linear-v2.1.0-rc.1": "e7feafe885899be8bbb2ec76b27eff4d2a27c4b75fe0fbdb080565c49685ea1c",
    "linear-v2.2.0": "716a4fc4faf371d7ac5bff392958232610c7e1561e6879b28c63bfd2cb721e68",
    "linear-v2.3.0": "cf47a56998e6e9d599053523da596747ce318b294ff6da2f110a955f516d7ed5",
    "linear-v2.4.0": "40c95a2fce838307a7ba4598a6881a6fa126e2743a34387908cfb62151d076ff",
    "linear-v2.5.0-rc.1": "1293e7f2082626bb31c41236efbda5a88e9a16c5b47894175b02cf2a4c263a34",
    "linear-v2.5.0": "8bd3c1ad6bfd5320d665acbf6a2c09fc412f4cd94dab6b87a6d2a5a035c37440",
    "linear-v2.5.1-rc.1": "781dcb94ef83d3fdd50a843a573179c820b9b1ee4f9ecc59bd2b56d22549c33f",
    "linear-v2.5.1": "1ea6f84cf44dd40f0c4bb242dabcfbafa2ba673f3dd028dfcc5558552ba95f96",
    "linear-v2.5.2-rc.1": "a1d890352faecaaf8cfc8813b6246154c40c4577cb2d642e0cd9c45d6ffd09f4",
    "linear-v2.5.2": "85766f57568c0db1588b0f8a90441cac7a3b71a671c668acca866a8afcfaa331",
    "linear-v3.0.0": "7ff4b0e53430d539806d9bb0a10fc8eb8d129bded68e5c603e22c71b1010e7c8",
}
HOOK_COMMAND = 'python3 -B "${CLAUDE_PROJECT_DIR}/.agent-skills/current/__main__.py" hook claude-code %s || exit 1'


def _hook_entry(event: str) -> dict:
    return {"hooks": [{"type": "command", "command": HOOK_COMMAND % event, "timeout": 30}]}


class StandingBinding(unittest.TestCase):
    def test_this_repository_enforces_the_lead_s_wait(self) -> None:
        settings = json.loads((ROOT / ".agents/run-delivery.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["hooks"], {"lead.may-stop": "enforce"})

    def test_the_committed_settings_hold_exactly_the_two_dispatcher_entries(self) -> None:
        settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings, {"hooks": {"SessionStart": [_hook_entry("session-start")],
                                              "Stop": [_hook_entry("stop")]}})
        receipt = json.loads((ROOT / ".agent-skills/active.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["hooks"], {"created": True, "events": ["session-start", "stop"],
                                            "file": ".claude/settings.json"})

    def test_the_installed_delivery_generation_matches_the_attested_archive(self) -> None:
        receipt = json.loads((ROOT / ".agent-skills/active.json").read_text(encoding="utf-8"))
        self.assertIn(receipt["release"]["releaseId"], ATTESTED_ARCHIVES)
        archive_digest = ATTESTED_ARCHIVES[receipt["release"]["releaseId"]]
        # The attested archive's own digest, which the release notes and its .release.json carry: a generation
        # holding the same release id but other bytes fails here. Applying a later release updates this pin with it.
        self.assertEqual(receipt["generation"], archive_digest)
        self.assertEqual(receipt["release"]["archiveSha256"], archive_digest)
        # The installed lifecycle's own verdict: every committed generation file matches the receipt's digests,
        # the projections are current, and the hook entries match the receipt.
        done = subprocess.run(["python3", "-B", ".agent-skills/current/__main__.py", "--root", str(ROOT), "status"],
                              cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True,
                              text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        status = json.loads(done.stdout)
        self.assertEqual((status["blockers"], status["lifecycle"]), ([], "current"), done.stdout)
        self.assertEqual(status["active"]["generation"], archive_digest)
        self.assertEqual(receipt["release"]["profile"], "linear")
        current = os.readlink(ROOT / ".agent-skills/current")
        self.assertEqual(current, "generations/" + receipt["generation"])
        manifest = json.loads((ROOT / ".agent-skills/current/skills/run-delivery/hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(hook["event"] for hook in manifest["hooks"]), ["session-start", "stop"])

    def test_the_hooks_section_says_what_runs_how_to_turn_it_off_and_that_the_folder_is_trusted(self) -> None:
        section = _section("Hooks", "Setting a machine up")
        self.assertIn(" ".join((HOOK_COMMAND % "stop").split()), section)
        for phrase in ("`lead.may-stop`, on every stop.", "`delivery.orient`, on every session start",
                       "This repository sets it to `enforce` in `.agents/run-delivery.json`",
                       "To turn a hook off, or to `warn`, for one delivery, set it in `hooks` in "
                       "`.agents/run-delivery.json` on that delivery's base.",
                       '"hooks": {"lead.may-stop": "off"}',
                       "Claude Code runs a project's hooks only once the folder is trusted."):
            self.assertIn(phrase, section)

    def test_it_holds_no_machine_and_no_delivery(self) -> None:
        for name in ("orchestration.json", "delivery-views.json", "run-delivery.json", "delivery/candidate.py"):
            text = (ROOT / ".agents" / name).read_text(encoding="utf-8")
            self.assertNotIn("/Users/", text, name)
            self.assertNotIn("delivery-artifacts", text, name)
            self.assertNotIn("backlog-followups", text, name)
        binding = json.loads((ROOT / ".agents/orchestration.json").read_text(encoding="utf-8"))
        deliveries = [json.loads(path.read_text(encoding="utf-8"))
                      for path in (ROOT / ".agents/deliveries").glob("*.json")]
        branch = _git(ROOT, "branch", "--show-current").strip()
        self.assert_standing_base(binding, deliveries, branch=self.delivery_check_branch(branch, os.environ))
        self.assertEqual(binding["state"]["liveHome"], "line-map")
        self.assertNotIn("PATH", binding["environment"])
        renderer = next(entry for entry in binding["state"]["renderers"] if entry["id"] == "line-map")
        self.assertTrue(renderer["argv"][2].startswith(".agent-skills/current/"), renderer["argv"])
        # Any later delivery's tickets, not this one's.
        pattern = binding["state"]["forgeSnapshot"]["argv"]
        self.assertEqual(pattern[pattern.index("--subject-pattern") + 1], "V26-[0-9]+")

    @staticmethod
    def delivery_check_branch(branch: str, environment) -> str:
        if environment.get("GITHUB_EVENT_NAME") == "pull_request":
            return environment.get("GITHUB_BASE_REF") or branch
        if environment.get("GITHUB_EVENT_NAME") == "push":
            return environment.get("GITHUB_REF_NAME") or branch
        return branch

    def assert_standing_base(self, binding: dict, deliveries: list[dict], *, branch: str = "") -> None:
        # A detached check checkout can establish its mode from the committed
        # binding and delivery declaration alone.
        self.assertEqual(binding["base"]["remote"], "origin")
        base = binding["base"]["ref"]
        if branch == "main" or branch.startswith("integrate/"):
            self.assertEqual(base, branch, "the standing binding must match the checked base")
        if base != "main":
            self.assertRegex(base, r"^integrate/[^/]+$")
            self.assertIn("integrate/*", binding.get("bases", []))
            self.assertEqual([d.get("integrationTarget") for d in deliveries if d.get("baseBranch") == base],
                             ["main"], "the active integration binding needs one matching delivery declaration")

    def test_standing_binding_accepts_only_declared_integration_mode(self) -> None:
        binding = {"base": {"remote": "origin", "ref": "main"}, "bases": ["main", "integrate/*"]}
        declaration = {"baseBranch": "integrate/fixture", "integrationTarget": "main"}
        self.assert_standing_base(binding, [declaration])
        integration = {**binding, "base": {**binding["base"], "ref": "integrate/fixture"}}
        self.assert_standing_base(integration, [declaration], branch="integrate/fixture")
        self.assert_standing_base(integration, [declaration])
        for branch, environment in (
            ("main", {}),
            ("", {"GITHUB_EVENT_NAME": "push", "GITHUB_REF_NAME": "main"}),
            ("", {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_BASE_REF": "main"}),
        ):
            target = self.delivery_check_branch(branch, environment)
            with self.subTest(branch=branch, environment=environment), self.assertRaises(AssertionError):
                self.assert_standing_base(integration, [declaration], branch=target)
        for declarations in ([], [{"baseBranch": "integrate/fixture"}],
                             [{**declaration, "integrationTarget": "other"}], [declaration, declaration]):
            with self.subTest(declarations=declarations), self.assertRaises(AssertionError):
                self.assert_standing_base(integration, declarations)

    def test_the_tracker_publisher_posts_linear_updates_and_no_host_publisher_is_left(self) -> None:
        binding = json.loads((ROOT / ".agents/orchestration.json").read_text(encoding="utf-8"))
        publishers = binding["state"]["publishers"]
        # The entry lead-setup adds, from the generation this repository runs, and nothing else.
        tracker = _generation_module("own_tracker_publisher", PUBLISHER)
        self.assertEqual(publishers, [tracker.publisher_entry(PUBLISHER)])
        self.assertEqual(publishers[0]["argv"][:3], ["python3", "-B", PUBLISHER])
        self.assertFalse([entry for entry in publishers if "kind" in entry or "argv" not in entry], publishers)
        # The Linear snapshot implies the adapter, reading its token from the variable the snapshot names.
        flow = json.loads((ROOT / ".agents/run-delivery.json").read_text(encoding="utf-8"))
        self.assertNotIn("tracker", flow)
        conf = tracker.settings(ROOT, flow, binding)
        self.assertEqual((conf["mode"], conf["problems"], conf["unrunnable"], conf["publisher"]),
                         ("publisher", [], [], "tracker"), conf)
        self.assertEqual((conf["adapter"]["name"], conf["adapter"]["tokenEnv"]), ("linear", "LINEAR_API_KEY"), conf)
        snapshot = binding["state"]["trackerSnapshot"]["argv"]
        self.assertEqual(snapshot[snapshot.index("--token-env") + 1], "LINEAR_API_KEY")

    def test_the_candidate_step_takes_the_key_only_where_a_title_opens_with_it(self) -> None:
        def candidate(key: str, *titles: str) -> subprocess.CompletedProcess:
            with tempfile.TemporaryDirectory() as folder:
                pulls = [{"headRefName": f"branch-{index}", "headRefOid": "a" * 40, "title": title} for index, title in enumerate(titles)]
                gh = Path(folder) / "gh"
                gh.write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(pulls) + "\nEOF\n", encoding="utf-8")
                gh.chmod(0o755)
                return subprocess.run([sys.executable, "-B", str(ROOT / ".agents/delivery/candidate.py"), key], capture_output=True, text=True,
                                      env={**os.environ, "PATH": os.pathsep.join([folder, os.environ["PATH"]])})

        for title in ("WORK-1: the opened form", "[WORK-1]: the bracketed form"):
            done = candidate("WORK-1", title, "WORK-2: Follow up on WORK-1", "WORK-12: a longer key")
            self.assertEqual(json.loads(done.stdout)["branch"], "branch-0", done.stderr)
        self.assertEqual(candidate("WORK-1", "WORK-2: Follow up on WORK-1").returncode, 1)
        # A change request for two tickets names both, and each resolves to it, as the forge snapshot counts it.
        for key in ("WORK-1", "WORK-2"):
            done = candidate(key, "[WORK-1, WORK-2]: one change for both", "WORK-12: a longer key")
            self.assertEqual(json.loads(done.stdout)["branch"], "branch-0", done.stderr)
        # A key named only after the opening list, or one the list holds only as a prefix, is not one it names.
        self.assertEqual(candidate("WORK-1", "[WORK-2, WORK-12]: Follow up on WORK-1").returncode, 1)
        # A key in brackets further on isn't one the title opens with either.
        self.assertEqual(candidate("WORK-1", "WORK-2: Follow up on [WORK-1]").returncode, 1)
        # The title is read exactly as the forge snapshot reads it: a space-separated list and a leading space count,
        # an opened key with no space after its colon doesn't.
        self.assertEqual(json.loads(candidate("WORK-2", "[WORK-1 WORK-2]: space-separated").stdout)["branch"], "branch-0")
        self.assertEqual(json.loads(candidate("WORK-1", " WORK-1: leading space").stdout)["branch"], "branch-0")
        self.assertEqual(candidate("WORK-1", "WORK-1:no space").returncode, 1)
        # Two change requests that both name the key are refused, as two that open with it are.
        done = candidate("WORK-2", "[WORK-1, WORK-2]: one change", "WORK-2: another change")
        self.assertEqual(done.returncode, 1)
        self.assertIn("expected one open change request, found 2", done.stderr)

    def test_the_lock_listing_is_pinned_and_prints_what_the_lock_lists(self) -> None:
        # The merge queue regenerates a digest-only conflict in the lock and stops one where a candidate added or removed
        # an output. It tells them apart only through this listing, so without it, or with one that lists nothing, a
        # candidate's new outputs are dropped silently.
        binding = json.loads((ROOT / ".agents/orchestration.json").read_text(encoding="utf-8"))
        regenerable = binding["regenerable"]
        self.assertIn("provenance.lock.json", regenerable["paths"])
        entries = [entry for entry in regenerable.get("listings", []) if entry.get("path") == "provenance.lock.json"]
        self.assertEqual(entries, [{"path": "provenance.lock.json",
                                    "argv": ["python3", "-B", "scripts/update-provenance.py", "--list-outputs"]}])

        def listing(lock: str) -> list[str]:
            # Run as merge_queue.listed_outputs does: one version of the lock on stdin, from the root, with no file
            # argument, in the binding's environment over this one.
            done = subprocess.run(entries[0]["argv"], cwd=ROOT, input=lock, capture_output=True, text=True, timeout=120,
                                  env={**os.environ, **binding.get("environment", {})})
            self.assertEqual(done.returncode, 0, done.stderr)
            return json.loads(done.stdout)

        def listed(lock: str) -> list[str]:  # what the lock JSON itself lists, sorted and once each
            return sorted({output["path"] for entry in json.loads(lock)["entries"] for output in entry["outputs"]})

        committed = _git(ROOT, "show", "HEAD:provenance.lock.json")
        self.assertGreater(len(listed(committed)), 1)
        self.assertEqual(listing(committed), listed(committed))
        # A version that isn't the one on disk, as the merge base and a candidate aren't: the listing reads its stdin.
        other = json.loads(committed)
        dropped = other["entries"][0]["outputs"].pop()["path"]
        added = "skills/fixture-only/ADDED.md"
        other["entries"][-1]["outputs"].append({"path": added, "sha256": "0" * 64})
        other = json.dumps(other, indent=2) + "\n"
        self.assertNotIn(dropped, listed(other))
        self.assertIn(added, listed(other))
        self.assertEqual(listing(other), listed(other))

    def test_the_lead_selects_by_catalogue_id_and_the_guards_join_every_selection(self) -> None:
        binding = json.loads((ROOT / ".agents/orchestration.json").read_text(encoding="utf-8"))
        selection = binding["selection"]
        self.assertTrue(selection["lead"])
        catalogue = selection["catalogue"]
        self.assertEqual([entry["id"] for entry in catalogue], [*CATALOGUE_IDS,*SERVICE_IDS])
        for entry in catalogue[:len(CATALOGUE_IDS)]:
            # Each id is a test module that exists, run on its own through the worktree's environment.
            module = entry["id"]
            source = ROOT / (module.replace(".", "/") + ".py")
            self.assertTrue(source.is_file(), module)
            if module in SCRIPT_MODULES:
                # unittest finds no case in it and passes having run nothing, so the entry runs it as the script it is.
                self.assertNotIn("TestCase", source.read_text(encoding="utf-8"), module)
                self.assertEqual(entry["argv"], [".venv/bin/python", "-B", source.relative_to(ROOT).as_posix()])
            else:
                self.assertIn("TestCase", source.read_text(encoding="utf-8"), module)
                # Through the suite's runner, as the fallback runs, since a serial unittest of a large module can time out.
                self.assertEqual(entry["argv"], [".venv/bin/python", "-B", "scripts/run-tests.py", "--workers", "4", "-p",
                                                 source.name])
            self.assertEqual((entry["cwd"], entry["prerequisites"]), (".", []), module)
            # The run-delivery module is the slowest, so it alone gets the ceiling.
            self.assertEqual(entry["timeoutSeconds"], 5400 if module == "tests.test_run_delivery" else 3600, module)
            self.assertLessEqual(entry["timeoutSeconds"], binding["checkTimeoutCeiling"], module)
            self.assertTrue(entry["reasons"], module)
        self.assertEqual(selection["guards"], GUARDS)
        self.assertEqual(selection["readers"][:2], [{"paths": ["skills/delivery-views/scripts/snapshot.py"],
                                                 "checks": ["tests.test_delivery_views_adopter",
                                                            "tests.test_delivery_views_snapshots"]},
                                                {"paths": ["docs/solutions/**"],
                                                 "checks": ["tests.test_compound_learning_corpus"]}])
        # A runner entry's pattern selects its own module's units and nothing else.
        guard = next(entry for entry in catalogue if entry["id"] == "tests.test_linear_qualification")
        listed = subprocess.run([sys.executable, "-B", *guard["argv"][2:], "--list"], cwd=ROOT, capture_output=True, text=True,
                                timeout=120)
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
        units = [line.split()[0] for line in listed.stdout.splitlines() if line.strip()]
        self.assertTrue(units, listed.stdout)
        self.assertEqual([unit for unit in units if not unit.startswith("tests.test_linear_qualification.")], [], units)
        # Corpus validation still runs on every candidate, as it did before the catalogue, and so do the plan-record
        # fixtures, so every change to a record's shape is checked against them.
        self.assertEqual([entry["id"] for entry in binding["alwaysRun"]], ["validate-registry", "plan-record-fixtures"])
        self.assertNotIn("validate-registry", CATALOGUE_IDS)

        with tempfile.TemporaryDirectory() as folder:
            top = Path(os.path.realpath(folder))
            repo = top / "repo"
            (repo / "skills/delivery-views/scripts").mkdir(parents=True)
            (repo / "skills/delivery-views/scripts/snapshot.py").write_text("one\n", encoding="utf-8")
            (repo / "README.md").write_text("one\n", encoding="utf-8")
            _git(top, "init", "-q", "-b", "main", str(repo))
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "base")
            base = _git(repo, "rev-parse", "HEAD").strip()

            def candidate(path: str) -> str:
                _git(repo, "checkout", "-q", "-B", "work", base)
                (repo / path).write_text("two\n", encoding="utf-8")
                _git(repo, "commit", "-q", "-am", "work")
                return _git(repo, "rev-parse", "HEAD").strip()

            def select(head: str, checks: str) -> dict:
                state = top / "state"
                shutil.rmtree(state, ignore_errors=True)
                tool = [sys.executable, "-B", str(ROOT / STATE_TOOL), "--dir", str(state)]
                subprocess.run([*tool, "init", "--headline", "Fixture"], check=True, capture_output=True, text=True)
                item = {"order": 1, "subject": "WORK-1", "title": "Work", "lane": "lane-1", "stage": "queued", "head": head,
                        "syncedHead": None, "leadInputs": [], "reportedRounds": 1, "runId": None, "checks": []}
                lane = {"id": "lane-1", "title": "Lane", "focus": "", "state": "working", "waitingOn": None,
                        "since": "2026-11-09T00:00:00Z"}
                patched = subprocess.run([*tool, "patch", "--writer", "lead", "--version", "1", "--patch", "-"],
                                         input=json.dumps({"lanes": [lane], "queue": [item]}), capture_output=True, text=True)
                self.assertEqual(patched.returncode, 0, patched.stdout + patched.stderr)
                done = subprocess.run([*tool, "select", "--subject", "WORK-1",
                                       "--binding", str(ROOT / ".agents/orchestration.json"), "--head", head, "--checks", checks, "--reason", "the story's sensors",
                                       "--repo", str(repo), "--base-head", base], capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                return json.loads(done.stdout)

            entries = {entry["id"]: entry for entry in catalogue}
            done = select(candidate("README.md"), "tests.test_own_deliveries,tests.test_run_delivery")
            chosen = done["item"]["leadSelection"]["checks"]
            # The ids resolve to the catalogue's entries, in the order named, with the guards added after them.
            self.assertEqual([check["id"] for check in chosen],
                             ["tests.test_own_deliveries", "tests.test_run_delivery", *GUARDS])
            self.assertEqual(chosen, [entries[check["id"]] for check in chosen])
            # A selection naming a guard selects it once.
            done = select(candidate("README.md"), "tests.test_registry")
            self.assertEqual([check["id"] for check in done["item"]["leadSelection"]["checks"]],
                             ["tests.test_registry", "tests.test_linear_qualification"])
            # A change to the snapshot producer brings the line map's outside adopter, which once broke main unselected.
            done = select(candidate("skills/delivery-views/scripts/snapshot.py"), "tests.test_run_delivery")
            self.assertEqual([check["id"] for check in done["item"]["leadSelection"]["checks"]],
                             ["tests.test_run_delivery", *GUARDS, "tests.test_delivery_views_adopter",
                              "tests.test_delivery_views_snapshots"])

    def test_a_changed_note_brings_the_corpus_check_and_only_a_note_does(self) -> None:
        # Through the installed generation's own resolver, as select resolves a lead's selection.
        import importlib.util
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location("own_delivery_state", ROOT / STATE_TOOL)
            tool = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tool)
        finally:
            sys.dont_write_bytecode = previous
        binding = json.loads((ROOT / ".agents/orchestration.json").read_text(encoding="utf-8"))
        for changed, brought in ((["docs/solutions/test-failures/a-note-2026-11-10.md"], True),
                                 (["docs/solutions/best-practices/another-2026-11-10.md", "README.md"], True),
                                 (["docs/solutions.md", "docs/own-deliveries.md"], False)):
            with self.subTest(changed=changed):
                checks, added = tool.resolve_selection([], binding, lambda: changed)
                ids = [check["id"] for check in checks]
                self.assertEqual("tests.test_compound_learning_corpus" in ids, brought, ids)
                if brought:
                    self.assertIn({"id": "tests.test_compound_learning_corpus", "why": f"reads {changed[0]}"}, added)

    def test_every_test_module_is_catalogued(self) -> None:
        found = {path.stem.removeprefix("test_") for path in (ROOT / "tests").glob("test_*.py")}
        excluded = [name for name, _ in UNCATALOGUED_MODULES]
        self.assertTrue(all(why.strip() for _, why in UNCATALOGUED_MODULES), "each exclusion gives its reason")
        both = sorted(set(CATALOGUE_MODULES) & set(excluded))
        self.assertEqual(both, [], f"catalogued and excluded both: {both}")
        expected = set(CATALOGUE_MODULES) | set(excluded)
        missing, extra = sorted(found - expected), sorted(expected - found)
        self.assertEqual((missing, extra), ([], []),
                         f"test modules in neither the catalogue nor UNCATALOGUED_MODULES: {missing}; listed but not "
                         f"found under tests/: {extra}")

    def test_a_changed_test_module_brings_the_catalogue_guard(self) -> None:
        import importlib.util
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location("own_delivery_state", ROOT / STATE_TOOL)
            tool = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tool)
        finally:
            sys.dont_write_bytecode = previous
        binding = json.loads((ROOT / ".agents/orchestration.json").read_text(encoding="utf-8"))
        readers = binding["selection"]["readers"]
        # Before the hosted service's reader, which the integration recipe takes as the last one.
        self.assertEqual(readers[-2], TEST_MODULE_READER)
        self.assertEqual(readers[-1]["checks"][0], "hosted.install")
        for changed, brought in ((["tests/test_brand_new.py"], True), (["tests/test_run_delivery.py", "README.md"], True),
                                 (["tests/fixtures/test_data.py", "tests/corpus_copy.py", "docs/own-deliveries.md"], False)):
            with self.subTest(changed=changed):
                checks, added = tool.resolve_selection([], binding, lambda: changed)
                ids = [check["id"] for check in checks]
                self.assertEqual("tests.test_own_deliveries" in ids, brought, ids)
                if brought:
                    self.assertIn({"id": "tests.test_own_deliveries", "why": f"reads {changed[0]}"}, added)

    def test_a_changed_skill_page_brings_the_corpus_tests_that_read_skill_prose(self) -> None:
        # A journal kind named in a skill that doesn't own it once reached the base twice, unselected both times.
        import importlib.util
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec = importlib.util.spec_from_file_location("own_delivery_state", ROOT / STATE_TOOL)
            tool = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tool)
        finally:
            sys.dont_write_bytecode = previous
        binding = json.loads((ROOT / ".agents/orchestration.json").read_text(encoding="utf-8"))
        prose = binding["selection"]["readers"][2]
        self.assertEqual(prose, {"paths": ["skills/**/*.md"], "checks": SKILL_PROSE_READERS})
        text = " ".join((ROOT / "docs/own-deliveries.md").read_text(encoding="utf-8").split())
        self.assertIn("A change to any Markdown file under `skills/` brings the five corpus tests that read the "
                      "shipped skill prose, `tests.test_run_event_emission_corpus` among them,", text)
        for changed, brought in ((["skills/run-delivery/SKILL.md"], True),
                                 (["README.md", "skills/orchestrate-work/references/orchestration-guide.md"], True),
                                 (["skills/run-delivery/scripts/run_delivery.py", "docs/own-deliveries.md"], False)):
            with self.subTest(changed=changed):
                checks, added = tool.resolve_selection([], binding, lambda: changed)
                ids = [check["id"] for check in checks]
                for reader in SKILL_PROSE_READERS:
                    self.assertEqual(reader in ids, brought, ids)
                if brought:
                    page = next(path for path in changed if path.startswith("skills/"))
                    self.assertIn({"id": "tests.test_run_event_emission_corpus", "why": f"reads {page}"}, added)

    def test_the_checks_section_names_the_binding_s_guards_and_readers(self) -> None:
        binding = json.loads((ROOT / ".agents/orchestration.json").read_text(encoding="utf-8"))
        selection = binding["selection"]
        text = " ".join((ROOT / "docs/own-deliveries.md").read_text(encoding="utf-8").split())
        section = text[text.index("## Checks"):text.index("## Later deliveries")]
        first, second = selection["guards"]
        self.assertIn(f"Two guards join every selection, named or not: `{first}`,", section)
        self.assertIn(f"and `{second}`.", section)
        self.assertEqual(len(selection["guards"]), 2)
        snapshot, notes = selection["readers"][:2]
        (path,) = snapshot["paths"]
        adopter, snapshots = snapshot["checks"]
        self.assertIn(f"A change to `{path}` also brings `{adopter}` and `{snapshots}`,", section)
        (folder,) = notes["paths"]
        (corpus,) = notes["checks"]
        self.assertIn(f"A change under `{folder.removesuffix('**')}` brings `{corpus}`, which pins each note's shape.",
                      section)
        self.assertIn("The lead selects by catalogue id.", section)

    def test_the_delivery_folders_are_ignored(self) -> None:
        for path in (".orchestration/lanes/x", ".orchestration/state/state.json", ".worktrees/x", ".claude/worktrees/x"):
            done = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT)
            self.assertEqual(done.returncode, 0, path)


# Each way scripts/run-tests.py can report a unit as failed, and one that passes.
RUNNER_KINDS = """
import os, time, unittest

class Fails(unittest.TestCase):
    def test_fails(self):
        self.assertEqual(1, 2)

class Errors(unittest.TestCase):
    def test_errors(self):
        raise RuntimeError("boom")

class Unexpected(unittest.TestCase):
    @unittest.expectedFailure
    def test_passes_unexpectedly(self):
        pass

class Crashes(unittest.TestCase):
    def test_crashes(self):
        os._exit(9)

class Hangs(unittest.TestCase):
    def test_hangs(self):
        time.sleep(120)

class SubTests(unittest.TestCase):
    def test_sub(self):
        with self.subTest(i=1):
            self.fail("always")

class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raise RuntimeError("fixture")

    def test_under_fixture(self):
        pass

class Passes(unittest.TestCase):
    def test_passes(self):
        pass
"""


class FullHealthRerunMember(unittest.TestCase):
    """The fullHealthRerun member this file gives, against the runner's own output for every kind of failure."""

    def test_each_failure_the_runner_reports_is_named_for_a_rerun_or_left_unrechecked(self) -> None:
        import re
        text = (ROOT / "docs/own-deliveries.md").read_text(encoding="utf-8")
        member = json.loads(re.search(r"```json fullHealthRerun\n(.*?)\n```", text, re.S).group(1))
        tmp = Path(tempfile.mkdtemp(prefix="full-health-rerun-member-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "probe").mkdir()
        (tmp / "probe/__init__.py").write_text("")
        (tmp / "probe/test_kinds.py").write_text(RUNNER_KINDS)
        run = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/run-tests.py"), "-s", "probe", "-t", ".",
                              "--workers", "2", "--unit-timeout", "5"], cwd=tmp, capture_output=True, text=True,
                             timeout=120)
        out = run.stdout + run.stderr
        self.assertEqual(run.returncode, 1, out)
        named = [match.group(1) for match in re.finditer(member["failurePattern"], out)]
        self.assertEqual(sorted(named), ["probe.test_kinds.Errors.test_errors", "probe.test_kinds.Fails.test_fails",
                                         "probe.test_kinds.Unexpected.test_passes_unexpectedly"], out)
        # Each failure line the runner prints is either named for a rerun or matched as unrechecked, line by line.
        lines = re.findall(r"(?m)^(?:FAIL|ERROR|UNEXPECTED SUCCESS|CRASHED|TIMED OUT): .*$", out)
        self.assertEqual(len(lines), 7, out)
        unrechecked = [line for line in lines if re.search(member["unrecheckedPattern"], line)]
        self.assertEqual(len(unrechecked), 4, out)
        for line in lines:
            with self.subTest(line=line):
                self.assertTrue(re.fullmatch(member["failurePattern"].replace("(?m)", ""), line)
                                or line in unrechecked, line)
                self.assertFalse(re.fullmatch(member["failurePattern"].replace("(?m)", ""), line) and line in unrechecked,
                                 f"named and unrechecked at once: {line}")
        # The member's argv, its interpreter this one since the probe has no .venv: a test that ran is matched, and
        # the named failures fail again.
        rerun = lambda tests: subprocess.run(
            [sys.executable, *[arg for element in member["argv"][1:]
                               for arg in (tests if element == "{tests}" else [element])]],
            cwd=tmp, capture_output=True, text=True, timeout=60)
        passed = rerun(["probe.test_kinds.Passes.test_passes"])
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        self.assertRegex(passed.stdout + passed.stderr, member["ranPattern"])
        self.assertNotEqual(rerun(named).returncode, 0)


class LeadSuite(unittest.TestCase):
    def test_the_lead_s_full_suite_is_run_with_lead_suite_and_the_budget(self) -> None:
        text = " ".join((ROOT / "docs/own-deliveries.md").read_text(encoding="utf-8").split())
        self.assertIn("lead-suite --at <commit> -- --budget tests/suite-budget.json", text)
        self.assertIn("appends its result, with the commit, the platform and the interpreter's version, to the flow "
                      "log's `<delivery>.suite.jsonl`. Cite that line when marking the finish line.", text)


class TrackerUpdatesAndCredentials(unittest.TestCase):
    """The document says what the switched binding does: the tracker publisher posts with LINEAR_API_KEY, and an agent
    shell gets the key the way the operator's never reaches it."""

    def test_nothing_says_linear_is_reached_only_through_the_host(self) -> None:
        text = " ".join((ROOT / "docs/own-deliveries.md").read_text(encoding="utf-8").split()).lower()
        for phrase in ("reachable only through the host", "only through the host's connector", "only through the host's tools",
                       "host-only tracker", "by this corpus's rule", "linear needs a host publisher", "host publisher",
                       "host outbox", "outbox", "the host's tools", "with those tools", "acks", "`ack`"):
            with self.subTest(phrase=phrase):
                self.assertFalse(phrase in text, phrase)

    def test_the_tracker_publisher_posts_with_the_linear_key(self) -> None:
        section = _section("Tracker updates", "Checks")
        self.assertIn("The binding names run-delivery's tracker publisher, which posts this repository's deliveries' "
                      "Linear updates and moves through Linear's API with `LINEAR_API_KEY`, with no lead action.", section)
        self.assertIn("A record a host-mode generation opened is seeded on the publisher's first run: every entry at or "
                      "below the `journalThrough` the last acknowledgement left in `published.json` is skipped, and "
                      "nothing is posted twice.", section)
        adapter = ".agent-skills/current/skills/run-delivery/adapters/tracker_linear.py"
        self.assertIn(f"python3 -B {adapter} markers --ticket <key>", section)
        self.assertTrue((ROOT / adapter).is_file(), adapter)
        # The ledger the lead reads is the one the installed publisher writes.
        tracker = _generation_module("own_tracker_publisher", PUBLISHER)
        self.assertIn(f"and records it in the ledger, `{tracker.LEDGER}` beside the record.", section)
        self.assertIn("A post Linear refuses shows in the line map's Needs you box.", section)
        self.assertIn("The binding configures no status moves, so starting, reviewing and closing a ticket stay with "
                      "people and Linear's GitHub integration.", section)

    def test_the_credentials_give_an_agent_shell_the_key_from_the_keychain(self) -> None:
        section = _section("Credentials", "Tracker updates")
        self.assertIn("`LINEAR_API_KEY`, a personal key from Linear's settings, for the tracker publisher and the tracker "
                      "snapshot.", section)
        self.assertIn("An agent's shell never sees a variable exported in the operator's terminal.", section)
        self.assertIn('security add-generic-password -a "$USER" -s LINEAR_API_KEY -w', section)
        self.assertIn('export LINEAR_API_KEY="$(security find-generic-password -a "$USER" -s LINEAR_API_KEY -w)"', section)
        self.assertIn('export GITHUB_TOKEN="$(gh auth token)"', section)
        # How the plan's doctor fix line is met here: the setup check names the variable, and this recipe sets it.
        self.assertIn("The setup check names `LINEAR_API_KEY` when it's unset, and this recipe is its fix here.", section)
        self.assertIn("Then export both from `~/.zshenv`:", section)
        # The interactive-only file is named once, as what an agent's shell doesn't read.
        self.assertEqual(section.count("~/.zshrc"), 1, section)
        self.assertIn("zsh reads `~/.zshenv` for every shell, the non-interactive one an agent's tool runs included, "
                      "and `~/.zshrc` only for an interactive one.", section)


class LeadChoosesChecksAtAcceptance(unittest.TestCase):
    """Under selection.lead the lead names a candidate's checks, or the fallback, as it accepts it; a lane names the
    checks it ran as it hands off."""

    def test_the_checks_section_says_acceptance_chooses_the_checks_or_the_full_suite(self) -> None:
        section = _section("Checks", "Later deliveries")
        self.assertIn("lead-accept <key> --checks tests.test_run_delivery,tests.test_lead_wait --reason \"<why these "
                      "cover the diff>\"", section)
        self.assertIn("`lead-accept` refuses a candidate with neither `--checks` nor `--full-suite`, and prints a "
                      "proposed `--checks`", section)
        self.assertIn("`--full-suite` accepts it on the fallback, the whole suite, as the lead's recorded decision.",
                      section)
        self.assertIn("A lane names the catalogue ids it ran with `handoff --checks`", section)
        # The selection is made at acceptance, not by a separate call to the state tool.
        self.assertNotIn(" select --subject", section)
        self.assertNotIn("With no selection the queue runs the whole suite.", section)



class HostedServiceBoundaryTests(unittest.TestCase):
    def test_service_ci_and_catalogue_keep_dependencies_in_the_service(self):
        workflow=json.loads((ROOT/'.github/workflows/hosted-delivery-viewer.yml').read_text())
        self.assertEqual(workflow['permissions'],{'contents':'read'})
        self.assertEqual(workflow['on']['push']['branches'],['main','integrate/hosted-deliveries'])
        required=['hosted/delivery-viewer/**','skills/delivery-views/assets/**',
                  'skills/delivery-views/scripts/render.py',
                  'skills/delivery-views/references/hosted-snapshot.schema.json',
                  'tests/fixtures/delivery-views/hosted/**','.github/workflows/hosted-delivery-viewer.yml']
        for event in ('push','pull_request'):
            self.assertEqual(workflow['on'][event]['paths'],required)
        job=workflow['jobs']['service']
        for field in ('if','continue-on-error'):self.assertNotIn(field,job)
        for step in job['steps']:
            self.assertNotIn('continue-on-error',step)
            if step.get('uses','').startswith('actions/upload-artifact@'):
                self.assertEqual(step.get('if'),'always()')
            else:self.assertNotIn('if',step)
        self.assertEqual(job['defaults']['run']['working-directory'],'hosted/delivery-viewer')
        self.assertEqual([step['with']['node-version'] for step in job['steps'] if step.get('uses','').startswith('actions/setup-node@')],['22.23.5'])
        commands=[step['run'] for step in job['steps'] if 'run' in step]
        self.assertEqual(commands,['npm ci --ignore-scripts --no-audit --no-fund --engine-strict',
                                   'npm run typecheck','npm run build','npm test',
                                   'npx playwright install --with-deps chromium','npm run test:browser'])
        self.assertNotIn('deploy', ' '.join(commands))
        binding=json.loads((ROOT/'.agents/orchestration.json').read_text())
        rows={entry['id']:entry for entry in binding['selection']['catalogue']}
        for key in SERVICE_IDS:self.assertEqual(rows[key]['cwd'],'hosted/delivery-viewer')
        expected=[commands[0].split(),['npm','run','typecheck'],['npm','run','build'],
                  ['npm','test'],['npx','playwright','install','chromium'],['npm','run','test:browser']]
        for key,argv in zip(SERVICE_IDS,expected):self.assertEqual(rows[key]['argv'],argv,key)
        self.assertEqual(rows['hosted.typecheck']['prerequisites'],['hosted.install'])
        self.assertEqual(rows['hosted.build']['prerequisites'],['hosted.install'])
        self.assertEqual(rows['hosted.test']['prerequisites'],['hosted.install'])
        self.assertEqual(rows['hosted.install']['prerequisites'],[])
        self.assertEqual(rows['hosted.browser-install']['prerequisites'],['hosted.install'])
        self.assertEqual(rows['hosted.browser']['prerequisites'],['hosted.build','hosted.browser-install'])
        for path in required:
            matched=[entry for entry in binding['selection']['readers'] if path in entry['paths']]
            self.assertTrue(any(entry['checks']==list(SERVICE_IDS) for entry in matched),path)
        root=json.loads((ROOT/'package.json').read_text())
        self.assertNotIn('wrangler',root.get('devDependencies',{}))
        self.assertNotIn('playwright',root.get('devDependencies',{}))

    def test_service_readers_resolve_dependencies_without_loading_service_for_other_changes(self):
        tool=_generation_module('hosted_selection',STATE_TOOL)
        binding=json.loads((ROOT/'.agents/orchestration.json').read_text())
        for changed in ('hosted/delivery-viewer/src/worker.ts','skills/delivery-views/assets/line-map.js',
                        'skills/delivery-views/scripts/render.py',
                        'skills/delivery-views/references/hosted-snapshot.schema.json',
                        'tests/fixtures/delivery-views/hosted/valid-plan-review.json',
                        '.github/workflows/hosted-delivery-viewer.yml','docs/own-deliveries.md'):
            with self.subTest(changed=changed):
                checks,_=tool.resolve_selection([],binding,lambda:[changed])
                ids={row['id'] for row in checks}
                self.assertEqual(set(SERVICE_IDS).issubset(ids),changed!='docs/own-deliveries.md')
                for row in checks:self.assertLessEqual(set(row['prerequisites']),ids)

    def test_integration_recipe_carries_only_named_checks_and_refuses_collisions(self):
        import copy
        text=(ROOT/'docs/own-deliveries.md').read_text()
        self.assertIn("PYTHONOPTIMIZE=0 python3 - <<'PY'",text)
        start=text.index('python3 - <<\'PY\'\n',text.index('Preserve only the reviewed hosted check additions'))
        recipe=text[start+len('python3 - <<\'PY\'\n'):text.index('\nPY\n',start)]
        source=json.loads((ROOT/'.agents/orchestration.json').read_text())
        source['selection']['catalogue'].sort(key=lambda row:
            (row['id'] in SERVICE_IDS,SERVICE_IDS.index(row['id'])
             if row['id'] in SERVICE_IDS else row['id']))
        allowed=['tests.test_delivery_views_hosted','tests.test_delivery_views_hosted_projection',
                 'tests.test_run_delivery_hosted',*SERVICE_IDS]
        target=copy.deepcopy(source)
        target['base']={'remote':'standing','ref':'standing-main'}
        target['prepare']=[['standing-only']]
        target['selection']['catalogue']=[r for r in target['selection']['catalogue'] if r['id'] not in allowed]
        reader=target['selection']['readers'].pop()
        for mode in ('add','already-identical','target-extension','partial-target','target-python-order','target-service-order','collision','reader-collision','missing-reader','not-restored','missing-source-check','duplicate-source-check'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'.agents/orchestration.json';path.parent.mkdir()
                old=copy.deepcopy(target);candidate=copy.deepcopy(source)
                if mode=='partial-target':
                    old['selection']['catalogue']=[r for r in old['selection']['catalogue'] if r['id']<allowed[2]]
                    old['selection']['catalogue'].extend(copy.deepcopy(next(r for r in source['selection']['catalogue'] if r['id']==key))
                                                        for key in (SERVICE_IDS[0],SERVICE_IDS[2],SERVICE_IDS[4],SERVICE_IDS[5]))
                if mode=='target-python-order':old['selection']['catalogue'][:2]=reversed(old['selection']['catalogue'][:2])
                if mode=='target-service-order':
                    old['selection']['catalogue'].extend(copy.deepcopy(next(r for r in source['selection']['catalogue'] if r['id']==key))
                                                        for key in reversed(SERVICE_IDS[:2]))
                if mode in ('already-identical','collision'):
                    row=copy.deepcopy(next(r for r in source['selection']['catalogue'] if r['id']==allowed[0]))
                    if mode=='collision':row['argv']=['unreviewed']
                    at=next((i for i,r in enumerate(old['selection']['catalogue']) if r['id']>row['id']),len(old['selection']['catalogue']))
                    old['selection']['catalogue'].insert(at,row)
                if mode=='target-extension':
                    row=copy.deepcopy(old['selection']['catalogue'][0])
                    row['id']='tests.test_delivery_views_hosted_extra'
                    at=next(i for i,r in enumerate(old['selection']['catalogue']) if r['id']>row['id'])
                    old['selection']['catalogue'].insert(at,row)
                if mode=='reader-collision':old['selection']['readers'].append({**reader,'checks':['unreviewed']})
                if mode=='missing-reader':candidate['selection']['readers'].remove(reader)
                if mode=='missing-source-check':
                    candidate['selection']['catalogue']=[r for r in candidate['selection']['catalogue'] if r['id']!='hosted.test']
                if mode=='duplicate-source-check':
                    candidate['selection']['catalogue'].append(copy.deepcopy(next(r for r in candidate['selection']['catalogue'] if r['id']=='hosted.test')))
                written=copy.deepcopy(old)
                if mode=='not-restored':written['merge']=['branch-specific']
                path.write_text(json.dumps(written,indent=2)+'\n');before=path.read_bytes()
                answers={'origin/main:.agents/orchestration.json':old,
                         'origin/integrate/<name>:.agents/orchestration.json':candidate}
                prefix='import subprocess,json\nanswers='+repr(answers)+'\nsubprocess.check_output=lambda args:json.dumps(answers[args[2]]).encode()\n'
                done=subprocess.run([sys.executable,'-B','-c',prefix+recipe],cwd=directory,
                                    env={**os.environ,'PYTHONOPTIMIZE':'0'},capture_output=True,text=True,timeout=10)
                if mode not in ('add','already-identical','target-extension','partial-target'):
                    self.assertNotEqual(done.returncode,0,mode)
                    self.assertIn('AssertionError',done.stderr)
                    self.assertEqual(path.read_bytes(),before)
                    continue
                self.assertEqual(done.returncode,0,done.stderr)
                merged=json.loads(path.read_text())
                expected=sorted({r['id'] for r in old['selection']['catalogue'] if r['id'].startswith('tests.')} |
                                {key for key in allowed if key.startswith('tests.')})
                self.assertEqual([r['id'] for r in merged['selection']['catalogue']],[*expected,*SERVICE_IDS])
                self.assertEqual([r['id'] for r in merged['selection']['catalogue'] if r['id'] in allowed],allowed)
                self.assertEqual(merged['selection']['readers'],[*old['selection']['readers'],reader])
                old_ids={r['id'] for r in old['selection']['catalogue']}
                self.assertEqual([r for r in merged['selection']['catalogue'] if r['id'] in old_ids],old['selection']['catalogue'])
                merged['selection']['catalogue']=old['selection']['catalogue']
                merged['selection']['readers']=old['selection']['readers']
                self.assertEqual(merged,old)

    def test_nonsecret_production_config_is_exact_and_live_proof_stays_separate(self):
        config=json.loads((ROOT/'hosted/delivery-viewer/wrangler.jsonc').read_text())
        self.assertEqual(config['routes'],[{'pattern':'deliveries.v26technologies.com','custom_domain':True}])
        self.assertFalse(config['workers_dev']);self.assertFalse(config['preview_urls'])
        self.assertFalse(config['observability']['enabled'])
        self.assertNotIn('vars',config)
        doc=(ROOT/'docs/hosted-deliveries.md').read_text()
        for phrase in ('Published candidate','Exact deployed bytes','No live proof is claimed',
                       'npx wrangler secret put PUBLISHERS','npx wrangler deploy',
                       'same commit','two repositories','disconnect','catch-up'):
            self.assertIn(phrase,doc)


if __name__ == "__main__":
    unittest.main()
