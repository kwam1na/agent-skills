"""A delivery whose key is also one of its stories' keys keeps its record beside the lane checkouts, under the work
directory's `deliveries/<key>/`, rather than in that story's own lane checkout. Every other delivery keeps today's
default, and an explicit stateDirectory or DELIVERY_STATE still wins.

The precedence rows call `Config.state_of` in this process on a stand-in configuration; the end-to-end rows build the
characterization fixture's delivery under a colliding key and run lead-init, lane-start and doctor on it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.test_run_delivery import (POSIX_ONLY, Fixture, bytecode_under, commit, doctor_binding, doctor_lines,
                                     installed_release, plan_delivery, setup_delivery, sh)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/run-delivery/scripts/run_delivery.py"
STATE = ".orchestration/state"
RECORD_ROW = "delivery record folder"


def _load():
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("run_delivery_key_collision", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPT.parent))
        sys.dont_write_bytecode = previous


run_delivery = _load()


class StateOfTests(unittest.TestCase):
    """Where `Config.state_of` puts a delivery's record, on a stand-in for the configuration it reads."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.main = Path(temporary.name)
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("DELIVERY_STATE", None)

    def config(self, work: str = ".orchestration", state: str = STATE) -> types.SimpleNamespace:
        c = types.SimpleNamespace(main=self.main, work=work, state_rel=state,
                                  lane_checkout=lambda key: self.main / work / "lanes" / key)
        c.record_apart = lambda key: run_delivery.Config.record_apart(c, key)
        return c

    def state_of(self, c: types.SimpleNamespace, key: str, d: dict) -> Path:
        return run_delivery.Config.state_of(c, key, d)

    def record(self, folder: Path) -> Path:
        folder.mkdir(parents=True)
        (folder / "state.json").write_text("{}\n")
        return folder

    def test_a_colliding_key_keeps_its_record_under_the_work_directorys_deliveries(self) -> None:
        d = {"stories": {"KEY-1": {}, "KEY-2": {}}}
        self.assertEqual(self.state_of(self.config(), "KEY-1", d),
                         self.main / ".orchestration/deliveries/KEY-1" / STATE)
        # Under a configured work directory, under its deliveries/.
        self.assertEqual(self.state_of(self.config(work="work"), "KEY-1", d),
                         self.main / "work/deliveries/KEY-1" / STATE)

    def test_a_key_no_story_has_keeps_todays_default(self) -> None:
        self.assertEqual(self.state_of(self.config(), "EPIC-1", {"stories": {"KEY-1": {}}}),
                         self.main / ".orchestration/lanes/EPIC-1" / STATE)

    def test_a_named_state_directory_wins_and_delivery_state_wins_over_both(self) -> None:
        d = {"stories": {"KEY-1": {}}, "stateDirectory": "records/KEY-1"}
        self.assertEqual(self.state_of(self.config(), "KEY-1", d), self.main / "records/KEY-1")
        # The binding's state.directory names the record's folder inside either default path; an absolute one is
        # the one place for every key.
        self.assertEqual(self.state_of(self.config(state="records"), "KEY-1", {"stories": {"KEY-1": {}}}),
                         self.main / ".orchestration/deliveries/KEY-1/records")
        absolute = self.record(self.main / "absolute")
        self.assertEqual(self.state_of(self.config(state=str(absolute)), "KEY-1", {"stories": {"KEY-1": {}}}),
                         absolute)
        os.environ["DELIVERY_STATE"] = str(self.main / "elsewhere")
        self.assertEqual(self.state_of(self.config(), "KEY-1", d), self.main / "elsewhere")
        self.assertEqual(self.state_of(self.config(), "KEY-1", {"stories": {"KEY-1": {}}}), self.main / "elsewhere")

    def test_a_record_already_at_either_default_path_is_used_where_it_is(self) -> None:
        c = self.config()
        lead = self.record(self.main / ".orchestration/lanes/KEY-1" / STATE)
        # A running delivery whose key collides keeps the record it started with.
        self.assertEqual(self.state_of(c, "KEY-1", {"stories": {"KEY-1": {}}}), lead)
        apart = self.record(self.main / ".orchestration/deliveries/EPIC-1" / STATE)
        # Once its colliding story is removed from the delivery file, the record apart is still the one found.
        self.assertEqual(self.state_of(c, "EPIC-1", {"stories": {"KEY-2": {}}}), apart)

    def test_a_record_at_both_default_paths_is_refused_naming_both(self) -> None:
        lead = self.record(self.main / ".orchestration/lanes/KEY-1" / STATE)
        apart = self.record(self.main / ".orchestration/deliveries/KEY-1" / STATE)
        for d in ({"stories": {"KEY-1": {}}}, {"stories": {}}):
            with self.subTest(d=d):
                with self.assertRaises(run_delivery.Stop) as refused:
                    self.state_of(self.config(), "KEY-1", d)
                self.assertIn(str(lead), str(refused.exception))
                self.assertIn(str(apart), str(refused.exception))


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class CollidingDeliveryTests(unittest.TestCase):
    """The fixture's delivery, keyed FX-2 like its first story, with no stateDirectory: led from the main checkout."""

    @classmethod
    def setUpClass(cls) -> None:
        for name, value in vars(installed_release()).items():
            setattr(cls, name, value)

    def tearDown(self) -> None:
        self.assertEqual(bytecode_under(self.generation), [], "the case left bytecode in the installed generation")

    def fixture(self, key: str = "FX-2", planned: bool = False) -> Fixture:
        fx = Fixture(self.generation, self.receipt)
        self.addCleanup(fx.close)
        path = setup_delivery(fx)
        d = json.loads(path.read_text())
        del d["stateDirectory"]
        d["delivery"] = key
        path.unlink()
        (path.parent / f"{key}.json").write_text(json.dumps(d, indent=2))
        if planned:  # the plan chapter on, which lead-plan change needs
            plan_delivery(fx, path.parent / f"{key}.json")
        (fx.main / ".gitignore").write_text(".orchestration/\nout/\nlanes/\n")
        # The fixture's complete binding: its state.directory names the record's folder, not where it is kept.
        (fx.main / ".agents/orchestration.json").write_text(json.dumps(doctor_binding(), indent=2) + "\n")
        conf = json.loads((fx.main / ".agents/run-delivery.json").read_text())
        del conf["gates"]  # the binding's fallback checks are the gates
        (fx.main / ".agents/run-delivery.json").write_text(json.dumps(conf, indent=2))
        fx.env.update({"FX_TRACKER_USER": "lead@example.test", "FX_TRACKER_TOKEN": "tracker-sentinel"})
        commit(fx.main, f"FX-1: the delivery as {key}, with its record in the default place")
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
        return fx

    def doctor(self, fx: Fixture, key: str = "FX-2") -> tuple[str, str]:
        code, out = fx.flow("doctor", "--delivery", key, "--offline", cwd=fx.main)
        self.out = out
        lines = doctor_lines(out)
        self.assertIn(RECORD_ROW, lines, out)
        return lines[RECORD_ROW]

    def test_the_delivery_inits_and_its_storys_lane_starts_with_the_record_apart(self) -> None:
        fx = self.fixture()
        apart = fx.main.resolve() / ".orchestration/deliveries/FX-2" / STATE
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertTrue((apart / "state.json").is_file(), out)
        # lead-init prints the record's folder, and how to choose another.
        self.assertIn(f"FX-2 is also a story's key, so its record is kept in {apart}", out)
        self.assertIn("set stateDirectory in", out)
        code, out = fx.flow("--delivery", "FX-2", "lead-assign", "FX-2", "--lane", "lane-1", "--title", "lane-1",
                            cwd=fx.main)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lane-start", "FX-2", "--base", "feature/FX-1", cwd=fx.main)
        self.assertEqual(code, 0, out)
        lane = fx.main / "lanes/FX-2"
        self.assertTrue((lane / ".git").exists(), out)
        self.assertFalse((lane / STATE).exists(), "the record is not in the story's lane checkout")
        stages = {item["subject"]: item["stage"] for item in json.loads((apart / "state.json").read_text())["planned"]}
        self.assertEqual(stages["FX-2"], "building")

    def test_after_the_colliding_story_is_removed_the_record_apart_is_still_found(self) -> None:
        fx = self.fixture(planned=True)
        apart = fx.main.resolve() / ".orchestration/deliveries/FX-2" / STATE
        code, out = fx.flow("lead-init", cwd=fx.main)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-plan", "change", "--kind", "finish", "--finish", "acs", "--covers",
                            json.dumps({"criteria": "all", "proofs": ["merged"]}), "--why", "W", cwd=fx.main)
        self.assertEqual(code, 0, out)
        code, out = fx.flow("lead-plan", "change", "--kind", "removed", "--subjects", "FX-2", "--why", "W",
                            cwd=fx.main)
        self.assertEqual(code, 0, out)
        self.assertNotIn("FX-2", json.loads((fx.main / ".agents/deliveries/FX-2.json").read_text())["stories"])
        status, detail = self.doctor(fx)
        self.assertEqual(status, "ok", self.out)
        self.assertIn(f"{apart} (the work directory's deliveries/<key>/, where the record already is)", detail)
        self.assertFalse((fx.main / "lanes/FX-2" / STATE).exists())

    def test_doctor_warns_of_the_folder_and_says_wrong_when_git_does_not_ignore_it(self) -> None:
        fx = self.fixture()
        status, detail = self.doctor(fx)
        self.assertEqual(status, "warn", self.out)
        self.assertIn(str(fx.main.resolve() / ".orchestration/deliveries/FX-2" / STATE), detail)
        self.assertIn("stateDirectory", detail)
        (fx.main / ".gitignore").write_text("out/\nlanes/\n")
        status, detail = self.doctor(fx)
        self.assertEqual(status, "wrong", detail)
        self.assertIn(".orchestration/deliveries/FX-2/.orchestration/state/ isn't ignored by git", detail)

    def test_a_key_no_story_has_keeps_its_record_in_the_lead_checkout_and_doctor_warns_of_nothing(self) -> None:
        fx = self.fixture("FX-1")
        status, detail = self.doctor(fx, "FX-1")
        self.assertEqual(status, "ok", detail)
        self.assertIn(str(fx.main.resolve() / "lanes/FX-1" / STATE), detail)
        code, out = fx.flow("doctor", "--delivery", "FX-1", "--offline", cwd=fx.main)
        self.assertNotIn("also a story's key", out)


if __name__ == "__main__":
    unittest.main()
