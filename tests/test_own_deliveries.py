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


# Every test module, by its module name: the lead selects checks by these ids. A module added later is selectable once
# its entry is added here and in the binding; until then a JSON list still selects it.
CATALOGUE_MODULES = (
    "adoption", "bootstrap_qualification", "catalog", "compound_learning_corpus", "core_boundary", "corpus_copy",
    "cut_release", "declared_mechanics_corpus", "delivery_posture_vectors", "delivery_provider",
    "delivery_provider_rails_contract", "delivery_views", "delivery_views_adopter", "delivery_views_history",
    "delivery_views_page", "delivery_views_plan", "delivery_views_plan_fixtures", "delivery_views_render",
    "delivery_views_review_projection", "delivery_views_snapshots", "diagnosis", "documented_install_surface",
    "fault_injection", "forge_adapters", "historical_qualifications", "hooks", "host_exposure", "jira_profile", "lane_rows",
    "lane_ruling_corpus", "lead_wait", "lifecycle", "linear_profile", "linear_qualification", "measure_test_writes",
    "merge_occurrence", "merge_queue", "no_current_set_names", "orchestration_adopter", "orchestration_render",
    "orchestration_state", "own_deliveries", "product_descriptors", "product_launcher", "product_release",
    "profile_inventory", "provenance", "provider_qualification", "qualification", "readme", "receipt", "registry",
    "release_archive", "release_channel", "release_entry_budget", "release_integration", "review_and_compounding",
    "review_base_reopening", "review_bound_corpus", "review_grace", "review_orchestration", "round_brief_template_corpus",
    "router", "run_delivery", "run_delivery_hooks", "run_delivery_plan_review", "run_delivery_supervisor", "run_delivery_tracker",
    "run_event_emission_corpus", "run_tests", "runless_reviews", "test_dependency_guard", "test_environment",
    "tracker_contract", "update_provenance", "validate", "workflow_graph_contract", "workflow_observation", "workflows",
)
CATALOGUE_IDS = tuple("tests.test_" + name for name in CATALOGUE_MODULES)
GUARDS = ["tests.test_linear_qualification", "tests.test_registry"]
# Modules that run as scripts, holding no unittest case.
SCRIPT_MODULES = ("tests.test_registry",)
STATE_TOOL = ".agent-skills/current/skills/orchestrate-work/scripts/delivery_state.py"
PUBLISHER = ".agent-skills/current/skills/run-delivery/scripts/tracker_publisher.py"


def _git(cwd: Path, *args: str) -> str:
    identity = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false"]
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
        cls.remote = top / "remote.git"
        _git(top, "clone", "-q", "--bare", str(seed), str(cls.remote))
        cls.clone = top / "clone"
        _git(top, "clone", "-q", str(cls.remote), str(cls.clone))
        shutil.rmtree(seed)
        python = str(Path(sys.executable).parent)
        cls.environment = {"PATH": os.pathsep.join([python, os.environ["PATH"]]), "HOME": cls.temporary.name,
                           "PYTHONDONTWRITEBYTECODE": "1", "LINEAR_API_KEY": "fixture", "GITHUB_TOKEN": "fixture"}

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


class StandingBinding(unittest.TestCase):
    def test_it_holds_no_machine_and_no_delivery(self) -> None:
        for name in ("orchestration.json", "delivery-views.json", "run-delivery.json", "delivery/candidate.py"):
            text = (ROOT / ".agents" / name).read_text(encoding="utf-8")
            self.assertNotIn("/Users/", text, name)
            self.assertNotIn("delivery-artifacts", text, name)
            self.assertNotIn("backlog-followups", text, name)
        binding = json.loads((ROOT / ".agents/orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["base"], {"remote": "origin", "ref": "main"})
        self.assertEqual(binding["state"]["liveHome"], "line-map")
        self.assertNotIn("PATH", binding["environment"])
        renderer = next(entry for entry in binding["state"]["renderers"] if entry["id"] == "line-map")
        self.assertTrue(renderer["argv"][2].startswith(".agent-skills/current/"), renderer["argv"])
        # Any later delivery's tickets, not this one's.
        pattern = binding["state"]["forgeSnapshot"]["argv"]
        self.assertEqual(pattern[pattern.index("--subject-pattern") + 1], "V26-[0-9]+")

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
        self.assertEqual([entry["id"] for entry in catalogue], list(CATALOGUE_IDS))
        for entry in catalogue:
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
        self.assertEqual(selection["readers"], [{"paths": ["skills/delivery-views/scripts/snapshot.py"],
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
        # Corpus validation still runs on every candidate, as it did before the catalogue.
        self.assertEqual([entry["id"] for entry in binding["alwaysRun"]], ["validate-registry"])
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

    def test_the_checks_section_names_the_binding_s_guards_and_readers(self) -> None:
        binding = json.loads((ROOT / ".agents/orchestration.json").read_text(encoding="utf-8"))
        selection = binding["selection"]
        text = " ".join((ROOT / "docs/own-deliveries.md").read_text(encoding="utf-8").split())
        section = text[text.index("## Checks"):text.index("## Later deliveries")]
        first, second = selection["guards"]
        self.assertIn(f"Two guards join every selection, named or not: `{first}`,", section)
        self.assertIn(f"and `{second}`.", section)
        self.assertEqual(len(selection["guards"]), 2)
        snapshot, notes = selection["readers"]
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


if __name__ == "__main__":
    unittest.main()
