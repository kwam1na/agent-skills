"""Region claims follow the work: release and carry on landing, the carve, touching claims, the all-at-once report, claims-check and lead-claims."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.quiet_git import quieten

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/orchestrate-work/scripts"


def _load(name: str):
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(f"region_claims_{name}", SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


regions = _load("region_ownership")


class Repo:
    """A small repository: one file of numbered lines, changed and committed by the case."""

    def __init__(self, case: unittest.TestCase) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="region-claims-"))
        case.addCleanup(shutil.rmtree, self.root, True)
        self.git("init", "-q", "-b", "main")
        quieten(self.root)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=fx", "-c", "user.email=fx@example.test", "-c",
                               "commit.gpgsign=false", *args], cwd=self.root, check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, files: dict[str, list[str]], message: str = "change") -> str:
        for path, lines in files.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("".join(lines))
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")


def numbered(count: int, name: str = "line") -> list[str]:
    return [f"{name} {i}\n" for i in range(1, count + 1)]


def claim(lane: str, base: str, start: int, end: int, story: str | None = None, path: str = "f.txt") -> dict:
    made = {"lane": lane, "path": path, "baseHead": base, "start": start, "end": end}
    return {**made, "story": story} if story else made


class SettleTests(unittest.TestCase):
    """Decision 2 on the claims alone: what a landing releases, carries, keeps and marks for a regrant."""

    def setUp(self) -> None:
        self.repo = Repo(self)
        self.lines = numbered(30)
        self.base = self.repo.commit({"f.txt": self.lines, "g.txt": numbered(5)}, "base")

    def land(self, lines: list[str]) -> str:
        return self.repo.commit({"f.txt": lines}, "land")

    def test_the_story_s_claim_is_released_and_its_lane_s_claim_is_carried_over_the_same_lines(self) -> None:
        own = claim("lane-1", self.base, 3, 8, "S1")
        lane = claim("lane-1", self.base, 10, 16)
        other = claim("lane-2", self.base, 20, 25)
        # The story replaced line 4 inside its own claim and lines 12 and 13 with three inside its lane's.
        landed = self.lines[:3] + ["four\n"] + self.lines[4:11] + ["a\n", "b\n", "c\n"] + self.lines[13:]
        merge = self.land(landed)
        result = regions.settle(self.repo.root, [own, lane, other], "S1", "lane-1", merge)
        self.assertEqual(result["released"], [own])
        self.assertEqual(result["carried"], [(lane, claim("lane-1", merge, 10, 17))])
        self.assertEqual(result["regions"], [claim("lane-1", merge, 10, 17), other])
        self.assertEqual(result["regrant"], [])
        # The carried claim covers the same lines at the merge, with the landed hunk inside.
        self.assertEqual(landed[9:16], self.lines[9:11] + ["a\n", "b\n", "c\n"] + self.lines[13:15])

    def test_a_carried_claim_keeps_its_story_and_is_released_when_that_story_lands(self) -> None:
        later = claim("lane-1", self.base, 10, 16, "S2")
        merge = self.land(self.lines[:11] + ["inside\n"] + self.lines[11:])
        result = regions.settle(self.repo.root, [later], "S1", "lane-1", merge)
        self.assertEqual(result["regions"], [{"lane": "lane-1", "path": "f.txt", "baseHead": merge, "start": 10,
                                              "end": 17, "story": "S2"}])
        second = self.repo.commit({"f.txt": self.lines[:11] + ["inside\n", "more\n"] + self.lines[11:]})
        released = regions.settle(self.repo.root, result["regions"], "S2", "lane-1", second)
        self.assertEqual((released["released"], released["regions"]), (result["regions"], []))

    def test_a_lane_claim_a_landed_hunk_crosses_at_an_edge_is_marked_for_a_regrant_not_carried(self) -> None:
        lane = claim("lane-1", self.base, 10, 16)
        merge = self.land(self.lines[:14] + ["x\n", "y\n", "z\n"] + self.lines[17:])  # replaces lines 15-17
        result = regions.settle(self.repo.root, [lane], "S1", "lane-1", merge)
        self.assertEqual(result["carried"], [])
        [marked] = result["regrant"]
        self.assertEqual({name: marked[name] for name in regions.COORDINATES}, lane)
        self.assertEqual(marked["regrant"]["merge"], merge)
        self.assertIn("crosses the span's edge", marked["regrant"]["reason"])
        self.assertEqual(result["regions"], [marked])

    def test_another_lane_s_claim_at_the_edge_of_a_landed_insertion_keeps_its_place_and_needs_a_regrant(self) -> None:
        own = claim("lane-1", self.base, 5, 10, "S1")
        neighbour = claim("lane-2", self.base, 10, 14)
        # The story's insertion after its last line sits at the edge both claims share.
        merge = self.land(self.lines[:9] + ["appended\n"] + self.lines[9:])
        result = regions.settle(self.repo.root, [own, neighbour], "S1", "lane-1", merge)
        self.assertEqual(result["carried"], [])
        [marked] = result["regrant"]
        self.assertEqual({name: marked[name] for name in regions.COORDINATES}, neighbour)
        self.assertEqual(result["regions"], [marked])

    def test_a_replaced_last_line_leaves_the_neighbouring_claim_mapping_as_it_was(self) -> None:
        own = claim("lane-1", self.base, 5, 10, "S1")
        neighbour = claim("lane-2", self.base, 10, 14)
        merge = self.land(self.lines[:8] + ["nine replaced\n"] + self.lines[9:])
        result = regions.settle(self.repo.root, [own, neighbour], "S1", "lane-1", merge)
        self.assertEqual((result["regions"], result["regrant"]), ([neighbour], []))

    def test_claims_the_landing_leaves_alone_keep_their_base_and_coordinates(self) -> None:
        untouched = claim("lane-2", self.base, 20, 25)
        elsewhere = claim("lane-2", self.base, 1, 3, path="g.txt")
        merge = self.land(["top\n"] + self.lines)  # shifts every line of f.txt: lane-2's claim still maps
        later = claim("lane-3", merge, 2, 4)  # granted at the merge: the merge is already in its history
        result = regions.settle(self.repo.root, [untouched, elsewhere, later], "S1", "lane-1", merge)
        self.assertEqual(result["regions"], [untouched, elsewhere, later])
        self.assertEqual((result["carried"], result["regrant"], result["released"]), ([], [], []))

    def test_the_merge_commit_is_found_for_a_squash_and_for_a_merge(self) -> None:
        main = self.base
        self.repo.git("checkout", "-q", "-b", "story")
        synced = self.repo.commit({"f.txt": ["story\n", *self.lines]})
        self.repo.git("checkout", "-q", "main")
        tree = self.repo.git("rev-parse", f"{synced}^{{tree}}")
        squash = self.repo.git("commit-tree", tree, "-p", main, "-m", "squash")
        self.repo.git("update-ref", "refs/heads/main", squash)
        self.assertEqual(regions.merge_commit(self.repo.root, "main", synced), squash)
        merged = self.repo.git("commit-tree", tree, "-p", squash, "-p", synced, "-m", "merge")
        self.repo.git("update-ref", "refs/heads/main", merged)
        self.assertEqual(regions.merge_commit(self.repo.root, "main", synced), squash)
        self.assertIsNone(regions.merge_commit(self.repo.root, main, synced))


class CarveTests(unittest.TestCase):
    """Decisions 4 and 5 on the claims alone: a carve's whole result, and touching claims made one."""

    def setUp(self) -> None:
        self.repo = Repo(self)
        self.lines = numbered(40)
        self.old = self.repo.commit({"f.txt": self.lines}, "old")
        self.base = self.repo.commit({"f.txt": ["header\n", *self.lines]}, "base")

    def test_gives_cut_two_lanes_claims_and_every_written_claim_is_anchored_at_the_carve_s_base(self) -> None:
        one = claim("lane-1", self.old, 2, 12)          # [3, 13) at the base
        two = claim("lane-2", self.old, 14, 24, "S2")   # [15, 25) at the base
        far = claim("lane-3", self.old, 30, 35)         # no give touches it
        result = regions.carve(self.repo.root, [one, two, far], "f.txt", self.base,
                               [{"lane": "lane-3", "start": 10, "end": 18, "story": "S3"}])
        self.assertEqual(result["claims"], [
            claim("lane-1", self.base, 3, 10), claim("lane-2", self.base, 18, 25, "S2"), far,
            claim("lane-3", self.base, 10, 18, "S3")])
        self.assertEqual(result["removed"], [one, two])
        self.assertEqual(result["added"], [claim("lane-1", self.base, 3, 10), claim("lane-2", self.base, 18, 25, "S2"),
                                           claim("lane-3", self.base, 10, 18, "S3")])

    def test_a_carve_is_refused_whole_when_a_claim_on_the_path_does_not_map_or_gives_overlap(self) -> None:
        stale = claim("lane-1", self.base, 1, 3)  # the header line is not in the old blob
        with self.assertRaisesRegex(regions.RegionError, "outside the target base's history"):
            regions.carve(self.repo.root, [stale], "f.txt", self.old, [{"lane": "lane-2", "start": 5, "end": 9}])
        with self.assertRaisesRegex(regions.RegionError, "overlap"):
            regions.carve(self.repo.root, [], "f.txt", self.base,
                          [{"lane": "lane-2", "start": 5, "end": 9}, {"lane": "lane-3", "start": 8, "end": 12}])

    def test_touching_claims_of_one_lane_and_story_become_one_and_others_stay_apart(self) -> None:
        claims = [claim("lane-1", self.old, 2, 6), claim("lane-1", self.base, 7, 9),   # touch at 7 on the base
                  claim("lane-1", self.base, 9, 11, "S1"),                             # another story
                  claim("lane-2", self.base, 11, 13),                                   # another lane
                  claim("lane-1", self.base, 14, 16), claim("lane-1", self.base, 17, 19)]  # a line apart
        joined, runs = regions.coalesce(self.repo.root, claims, self.base)
        self.assertEqual(joined, [claim("lane-1", self.base, 3, 9), claims[2], claims[3], claims[4], claims[5]])
        self.assertEqual(runs, [claims[:2]])


class ReportTests(unittest.TestCase):
    """Decision 6: every hunk outside the lane's spans is named, not only the first."""

    def test_check_change_names_every_hunk_outside_and_problems_reads_the_same(self) -> None:
        repo = Repo(self)
        lines = numbered(20)
        base = repo.commit({"f.txt": lines})
        head = repo.commit({"f.txt": ["first\n", *lines[1:9], "inside\n", *lines[10:17], "near the end\n", *lines[18:]]})
        mine = [claim("lane-1", base, 8, 13)]
        spans, rows = regions.change_report(repo.root, "f.txt", base, head, mine)
        self.assertEqual(spans, [(8, 13)])
        self.assertEqual([(row["where"], row["claim"] is not None) for row in rows],
                         [("[1, 2)", False), ("[10, 11)", True), ("[18, 19)", False)])
        with self.assertRaisesRegex(regions.RegionError, r"\[1, 2\), \[18, 19\) are outside a single owned span"):
            regions.check_change(repo.root, "f.txt", base, head, mine)
        found = regions.problems(repo.root, "lane-1", base, head, ["f.txt"], mine)
        self.assertIn("[1, 2), [18, 19) are outside", found["f.txt"])



# -- the commands ------------------------------------------------------------------------------------------------

def command_fixture(case: type, stories: dict[str, str]):
    """One delivery for a class: the supervised fixture, a third lane, ``stories`` added on their lanes, and the
    record started. Returns the fixture and the delivery file."""
    from tests.test_run_delivery import Fixture, add_story, installed_release, sh
    from tests.test_run_delivery_supervisor import supervised
    for name, value in vars(installed_release()).items():
        setattr(case, name, value)
    fx = Fixture(case.generation, case.receipt)
    case.addClassCleanup(fx.close)
    path = supervised(fx)
    d = json.loads(path.read_text())
    d["lanes"]["lane-3"] = {"owns": []}
    path.write_text(json.dumps(d, indent=2))
    for key, lane in stories.items():
        add_story(fx, path, key, lane=lane)
    sh("git", "push", "-q", "origin", "feature/FX-1", cwd=fx.main)
    code, out = fx.flow("lead-init", cwd=fx.main)
    assert code == 0, out
    return fx, path


class CommandCase(unittest.TestCase):
    fx = None

    def flow(self, *argv, cwd: Path | None = None, ok: bool | None = True) -> str:
        code, out = self.fx.flow(*argv, cwd=cwd or self.fx.main)
        if ok is True:
            self.assertEqual(code, 0, out)
        elif ok is False:
            self.assertNotEqual(code, 0, out)
        return out

    def push_base(self, files: dict[str, list[str]], message: str = "FX-1: the base moves") -> str:
        from tests.test_run_delivery import commit, sh
        for path, lines in files.items():
            (self.fx.main / path).parent.mkdir(parents=True, exist_ok=True)
            (self.fx.main / path).write_text("".join(lines))
        commit(self.fx.main, message)
        sh("git", "push", "-q", "origin", "feature/FX-1", cwd=self.fx.main)
        return sh("git", "rev-parse", "HEAD", cwd=self.fx.main).stdout.strip()

    def land(self, key: str, files: dict[str, list[str]]) -> str:
        """A story's landing as the queue leaves it: its change on the base, then its candidate.merged."""
        head = self.push_base(files, f"Merge feature/{key}")
        self.state("journal", "--kind", "candidate.merged", "--subject", key, "--data", json.dumps({"head": head}))
        return head

    def state(self, *argv) -> None:
        from tests.test_run_delivery_supervisor import state_tool
        state_tool(self.fx, *argv)

    def record(self) -> dict:
        from tests.test_run_delivery_supervisor import record
        return record(self.fx)

    def claims(self, path: str) -> list[dict]:
        return [claim for claim in self.record()["state"].get("laneRegions", []) if claim["path"] == path]

    def entries(self, kind: str, since: int = 0) -> list[dict]:
        return [entry for entry in self.record()["journal"] if entry["kind"] == kind and entry["id"] > since]

    def last_id(self) -> int:
        return self.record()["journal"][-1]["id"]

    def one_pass(self) -> dict:
        from tests.test_run_delivery_supervisor import passes
        self.flow("lead-run", "--once")
        return passes(self.fx)[-1]

    def grant(self, path: str, lane: str, base: str, start: int, end: int, *more, ok: bool | None = True) -> str:
        return self.flow("lead-own", "--region", path, "--lane", lane, "--base", base, "--start", start, "--end", end,
                         *more, ok=ok)


class LandingTests(CommandCase):
    """Decisions 1 to 3 through the supervisor's pass: each merge settled once, in order, under the ownership lock."""

    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        cls.fx, _ = command_fixture(cls, {"FX-5": "lane-1", "FX-6": "lane-1", "FX-7": "lane-2", "FX-8": "lane-3"})
        doc = json.loads(subprocess.run([sys.executable, "-B", cls.fx.release / "skills/orchestrate-work/scripts/delivery_state.py",
                                         "--dir", cls.fx.tmp / "record", "show"], capture_output=True, text=True,
                                        check=True).stdout)
        cls.initialized = doc["state"].get("regionsSettled")

    def test_a_landing_releases_its_story_s_claim_carries_its_lane_s_and_flags_the_rest(self) -> None:
        lines = numbered(30, "a")
        base = self.push_base({"lib/a.txt": lines, "lib/b.txt": numbered(12, "b")})
        self.assertEqual(self.initialized, 0)
        self.one_pass()
        self.assertEqual(self.record()["state"]["regionsSettled"], 0)
        self.grant("lib/a.txt", "lane-1", base, 3, 8, "--story", "FX-5")
        self.grant("lib/a.txt", "lane-1", base, 10, 16)
        self.grant("lib/a.txt", "lane-2", base, 20, 25)
        self.grant("lib/b.txt", "lane-2", base, 4, 8)
        # FX-6 is in flight on a branch that began before the landing.
        from tests.test_run_delivery import sh
        sh("git", "push", "-q", "origin", f"{base}:refs/heads/feature/FX-6", cwd=self.fx.main)
        since = self.last_id()
        landed = [*lines[:3], "a four\n", *lines[4:11], "x\n", "y\n", "z\n", *lines[13:18], "crossing\n", *lines[20:]]
        merge = self.land("FX-5", {"lib/a.txt": landed, "lib/b.txt": numbered(5, "b") + ["b over\n"] + numbered(12, "b")[6:]})
        merged_id = self.last_id()
        last = self.one_pass()
        state = self.record()["state"]
        self.assertEqual(state["regionsSettled"], merged_id, last.get("regions"))
        a = self.claims("lib/a.txt")
        self.assertEqual(a[0], {"lane": "lane-1", "path": "lib/a.txt", "baseHead": merge, "start": 10, "end": 17})
        self.assertEqual({k: a[1][k] for k in ("lane", "baseHead", "start", "end")},
                         {"lane": "lane-2", "baseHead": base, "start": 20, "end": 25})
        self.assertEqual(a[1]["regrant"]["merge"], merge)
        [released] = self.entries("ownership.released", since)
        self.assertEqual((released["lane"], released["subject"], released["data"]["span"], released["data"]["merge"]),
                         ("lane-1", "FX-5", {"first": 3, "after": 8}, merge))
        [carried] = self.entries("ownership.carried", since)
        self.assertEqual(carried["data"]["from"], {"first": 10, "after": 16})
        self.assertEqual(carried["data"]["to"], {"first": 10, "after": 17})
        self.assertEqual(carried["data"]["inFlight"], ["FX-6"])
        regrants = [item for item in last["needsYou"] if item["kind"] == "regrant"]
        self.assertEqual(sorted(item["text"].split(" on ")[1].split(" ")[0] for item in regrants), ["lib/a.txt", "lib/b.txt"])
        self.assertTrue(all(item["holding"] is False for item in regrants))
        self.assertEqual(sorted(item["said"]["headline"] for item in regrants),
                         ["Lane 2's lines in lib/a.txt need a regrant", "Lane 2's lines in lib/b.txt need a regrant"])
        self.assertTrue(all(item["said"]["clears"] and item["said"]["why"] for item in regrants))
        # A grant to another lane on the file succeeds past the flagged claim, and names it.
        out = self.grant("lib/a.txt", "lane-3", merge, 26, 30)
        self.assertIn("lane-2's [20, 25)", out)
        self.assertIn("needs a regrant", out)
        # The flagged claim is regranted on a.txt, and revoked on b.txt: each clears its item.
        out = self.grant("lib/a.txt", "lane-2", merge, 21, 25)
        self.assertIn("replaces lane-2's [20, 25)", out)
        [b_claim] = self.claims("lib/b.txt")
        self.flow("lead-own", "--revoke-region", "lib/b.txt", "--lane", "lane-2", "--base", base, "--start", 4, "--end", 8)
        self.assertEqual(self.claims("lib/b.txt"), [])
        last = self.one_pass()
        self.assertEqual([item for item in last["needsYou"] if item["kind"] == "regrant"], [])
        self.assertFalse(any("regrant" in claim for claim in self.claims("lib/a.txt")))

    def test_the_settle_waits_for_the_ownership_lock(self) -> None:
        import fcntl
        lines = numbered(10, "l")
        base = self.push_base({"lib/l.txt": lines})
        self.one_pass()
        self.grant("lib/l.txt", "lane-1", base, 2, 5, "--story", "FX-5")
        self.land("FX-5", {"lib/l.txt": ["l top\n", *lines]})
        settled = self.record()["state"]["regionsSettled"]
        with (self.fx.tmp / "record/lane-ownership.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            passing = subprocess.Popen([sys.executable, "-B", str(self.fx.tool), "lead-run", "--once"], cwd=self.fx.main,
                                       env=self.fx.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.addCleanup(lambda: passing.poll() is None and (passing.kill(), passing.wait()))
            import time
            deadline = time.monotonic() + 60
            # The pass goes on past the settle only once the lock is free: until then the claim stays.
            while time.monotonic() < deadline and passing.poll() is None and not (self.fx.tmp / "flow-log").exists():
                time.sleep(0.2)
            time.sleep(3)
            self.assertIsNone(passing.poll())
            self.assertEqual(self.record()["state"]["regionsSettled"], settled)
            self.assertEqual(len(self.claims("lib/l.txt")), 1)
        self.assertEqual(passing.wait(timeout=120), 0)
        self.assertEqual(self.claims("lib/l.txt"), [])
        self.assertGreater(self.record()["state"]["regionsSettled"], settled)

    def test_merges_settle_in_order_once_and_a_record_from_an_earlier_release_is_seeded(self) -> None:
        lines = numbered(20, "c")
        base = self.push_base({"lib/c.txt": lines})
        self.one_pass()
        self.grant("lib/c.txt", "lane-2", base, 2, 6, "--story", "FX-7")
        self.grant("lib/c.txt", "lane-3", base, 10, 14, "--story", "FX-8")
        since = self.last_id()
        first = self.land("FX-7", {"lib/c.txt": ["c top\n", *lines]})
        second = self.land("FX-8", {"lib/c.txt": ["c top\n", *lines, "c end\n"]})
        # Granted at the second merge on the file the first changed: already in its history.
        self.grant("lib/c.txt", "lane-1", second, 16, 19)
        after_grant = self.claims("lib/c.txt")
        self.one_pass()
        released = self.entries("ownership.released", since)
        self.assertEqual([(entry["subject"], entry["data"]["merge"]) for entry in released], [("FX-7", first), ("FX-8", second)])
        self.assertEqual(self.claims("lib/c.txt"), [after_grant[-1]])
        self.assertEqual(self.record()["state"]["regionsSettled"], self.entries("candidate.merged")[-1]["id"])
        # A supervisor restarted after a settle repeats nothing.
        settled = self.record()["state"]["regionsSettled"]
        self.one_pass()
        self.assertEqual(self.entries("ownership.released", since), released)
        self.assertEqual(self.record()["state"]["regionsSettled"], settled)

        # A record from an earlier release, with past merges and no settled id: seeded at the latest, settling none.
        d_lines = numbered(20, "d")
        base = self.push_base({"lib/d.txt": d_lines})
        self.one_pass()
        self.grant("lib/d.txt", "lane-1", base, 4, 10)
        self.grant("lib/d.txt", "lane-2", base, 12, 16)
        self.land("FX-6", {"lib/d.txt": [*d_lines[:5], "d inside\n", *d_lines[5:13], "d crossing\n", *d_lines[14:]]})
        state = self.record()["state"]
        self.state("patch", "--writer", "lead", "--version", str(state["version"]), "--patch", json.dumps({"regionsSettled": None}))
        before, since = self.claims("lib/d.txt"), self.last_id()
        last = self.one_pass()
        self.assertEqual(self.claims("lib/d.txt"), before)
        self.assertEqual([e["kind"] for e in self.record()["journal"] if e["id"] > since and e["kind"].startswith("ownership.")], [])
        self.assertEqual([item for item in last["needsYou"] if item["kind"] == "regrant"
                          and "lib/d.txt" in item["text"]], [])
        self.assertEqual(self.record()["state"]["regionsSettled"], self.entries("candidate.merged")[-1]["id"])



# A carve that meets version_stale: the first patch it sends finds a concurrent write already made, which changes a
# claim on the carved path, so the record moved under the carve's first read.
RACING_CARVE = r"""
import importlib.util, json, sys
tool, change = sys.argv[1], json.loads(sys.argv[2])
spec = importlib.util.spec_from_file_location("run_delivery", tool)
module = importlib.util.module_from_spec(spec)
sys.modules["run_delivery"] = module
spec.loader.exec_module(module)
real, fired = module.state_tool, []
def racing(*args, check=True):
    if args and args[0] == "patch" and not fired:
        fired.append(True)
        doc = module.load_state()
        regions = [dict(claim, **change["to"]) if all(claim.get(k) == v for k, v in change["match"].items()) else claim
                   for claim in doc["state"]["laneRegions"]]
        real("patch", "--writer", "lead", "--version", str(doc["state"]["version"]), "--set",
             json.dumps({"/laneRegions": regions}))
    return real(*args, check=check)
module.state_tool = racing
sys.argv = [tool, *sys.argv[3:]]
module.main()
"""


class GrantCarveTests(CommandCase):
    """Decisions 1, 3, 4 and 5 through lead-own: stories, grants past a stale claim, the carve, touching claims."""

    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        cls.fx, _ = command_fixture(cls, {"FX-5": "lane-1", "FX-7": "lane-2", "FX-8": "lane-3"})

    def unchanged(self, path: str, before: list[dict]) -> None:
        self.assertEqual(self.claims(path), before)

    def test_a_claim_names_a_story_of_its_lane_that_the_record_holds(self) -> None:
        base = self.push_base({"lib/s.txt": numbered(10, "s")})
        out = self.grant("lib/s.txt", "lane-1", base, 2, 4, "--story", "FX-99", ok=False)
        self.assertIn("FX-99", out)
        out = self.grant("lib/s.txt", "lane-1", base, 2, 4, "--story", "FX-7", ok=False)
        self.assertIn("FX-7 is lane-2's story, not lane-1's", out)
        self.unchanged("lib/s.txt", [])
        self.grant("lib/s.txt", "lane-1", base, 2, 4, "--story", "FX-5")
        self.grant("lib/s.txt", "lane-1", base, 6, 8)
        self.assertEqual([claim.get("story") for claim in self.claims("lib/s.txt")], ["FX-5", None])
        state = self.record()["state"]
        wrong = [dict(claim, story="FX-99") if claim["path"] == "lib/s.txt" else claim for claim in state["laneRegions"]]
        from tests.test_run_delivery import sh
        done = sh(sys.executable, "-B", self.fx.release / "skills/orchestrate-work/scripts/delivery_state.py", "--dir",
                  self.fx.tmp / "record", "patch", "--writer", "lead", "--version", state["version"], "--set",
                  json.dumps({"/laneRegions": wrong}), check=False)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("/story: name a story the record holds", done.stdout + done.stderr)

    def test_a_claim_that_no_longer_maps_refuses_no_grant_and_a_grant_still_refuses_its_own_problems(self) -> None:
        lines = numbered(24, "e")
        base = self.push_base({"lib/e.txt": lines})
        self.grant("lib/e.txt", "lane-1", base, 4, 8)
        self.grant("lib/e.txt", "lane-2", base, 14, 18)
        newer = self.push_base({"lib/e.txt": [*lines[:7], "e edge\n", *lines[7:]]})  # at lane-1's end: it no longer maps
        before = self.claims("lib/e.txt")
        out = self.grant("lib/e.txt", "lane-3", newer, 10, 13)
        self.assertIn("lane-1's [4, 8) at", out)
        self.assertIn("needs a regrant", out)
        before = self.claims("lib/e.txt")
        out = self.grant("lib/e.txt", "lane-3", base, 6, 10, ok=False)  # its own span doesn't map to the current base
        self.assertIn("Regrant it against the current base", out)
        self.unchanged("lib/e.txt", before)
        out = self.grant("lib/e.txt", "lane-3", newer, 17, 20, ok=False)
        self.assertIn("overlaps lane-2's [15, 19)", out)
        self.unchanged("lib/e.txt", before)

    def test_a_carve_writes_the_whole_new_set_of_claims_or_nothing(self) -> None:
        lines = numbered(40, "f")
        base = self.push_base({"lib/f.txt": lines})
        self.grant("lib/f.txt", "lane-1", base, 2, 12)
        self.grant("lib/f.txt", "lane-2", base, 14, 24, "--story", "FX-7")
        newer = self.push_base({"lib/f.txt": ["f top\n", *lines]})
        # A --base older than the current base, whose result doesn't map to it: the base inserted above line 1.
        before = self.claims("lib/f.txt")
        out = self.flow("lead-own", "--carve", "lib/f.txt", "--base", base, "--give", "lane-3:1-2", ok=False)
        self.assertIn("lib/f.txt [1, 2)", out)
        self.unchanged("lib/f.txt", before)
        since = self.last_id()
        out = self.flow("lead-own", "--carve", "lib/f.txt", "--give", "lane-3:10-18@FX-8", "--give", "lane-1:30-33")
        self.assertIn(f"carved lib/f.txt at {newer}", out)
        claim_ = lambda lane, start, end, story=None: claim(lane, newer, start, end, story, "lib/f.txt")
        self.assertEqual(self.claims("lib/f.txt"), [claim_("lane-1", 3, 10), claim_("lane-2", 18, 25, "FX-7"),
                                                    claim_("lane-3", 10, 18, "FX-8"), claim_("lane-1", 30, 33)])
        [carved] = self.entries("ownership.carved", since)
        self.assertEqual(carved["data"]["base"]["head"], newer)
        self.assertEqual([(c["lane"], c["start"], c["end"], c["baseHead"]) for c in carved["data"]["removed"]],
                         [("lane-1", 2, 12, base), ("lane-2", 14, 24, base)])
        self.assertEqual(len(carved["data"]["added"]), 4)
        before = self.claims("lib/f.txt")
        for gives, said in ((["lane-3:34-38", "lane-1:36-39"], "overlap"), (["lane-9:34-38"], "lane-9"),
                            (["lane-3:34-38@FX-7"], "FX-7 is lane-2's story")):
            with self.subTest(gives=gives):
                out = self.flow("lead-own", "--carve", "lib/f.txt", *[a for g in gives for a in ("--give", g)], ok=False)
                self.assertIn(said, out)
                self.unchanged("lib/f.txt", before)
        # A claim on the path that no longer maps to --base refuses the carve.
        self.push_base({"lib/f.txt": ["f top\n", *lines[:31], "f edge\n", *lines[31:]]})  # at lane-1's [30, 33)
        out = self.flow("lead-own", "--carve", "lib/f.txt", "--give", "lane-2:36-39", ok=False)
        self.assertIn("the carve of lib/f.txt changes nothing", out)
        self.assertIn("Regrant it against the current base", out)
        self.unchanged("lib/f.txt", before)

    def test_a_carve_that_meets_a_concurrent_write_is_computed_again_over_it(self) -> None:
        lines = numbered(30, "g")
        base = self.push_base({"lib/g.txt": lines})
        self.grant("lib/g.txt", "lane-2", base, 10, 20)
        since = self.last_id()
        from tests.test_run_delivery import sh
        change = {"match": {"path": "lib/g.txt", "lane": "lane-2"}, "to": {"end": 18}}
        done = sh(sys.executable, "-B", "-c", RACING_CARVE, self.fx.tool, json.dumps(change), "lead-own", "--carve",
                  "lib/g.txt", "--give", "lane-3:16-22", cwd=self.fx.main, env=self.fx.env, check=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.claims("lib/g.txt"), [claim("lane-2", base, 10, 16, path="lib/g.txt"),
                                                    claim("lane-3", base, 16, 22, path="lib/g.txt")])
        [carved] = self.entries("ownership.carved", since)
        self.assertEqual([(c["lane"], c["start"], c["end"]) for c in carved["data"]["removed"]], [("lane-2", 10, 18)])

    def test_touching_spans_of_one_lane_and_story_become_one_and_others_stay_two(self) -> None:
        lines = numbered(30, "h")
        base = self.push_base({"lib/h.txt": lines})
        self.grant("lib/h.txt", "lane-1", base, 2, 6)
        out = self.grant("lib/h.txt", "lane-1", base, 6, 9)
        self.assertIn("made one claim", out)
        self.assertEqual(self.claims("lib/h.txt"), [claim("lane-1", base, 2, 9, path="lib/h.txt")])
        # An edit replacing lines across the old boundary now lies in one span.
        head = self.push_base({"lib/h.txt": [*lines[:4], "h five\n", "h six\n", *lines[6:]]}, "FX-1: across the boundary")
        regions_ = _load("region_ownership")
        regions_.check_change(self.fx.main, "lib/h.txt", base, head, self.claims("lib/h.txt"))
        self.grant("lib/h.txt", "lane-2", head, 9, 12)
        self.grant("lib/h.txt", "lane-1", head, 12, 14, "--story", "FX-5")
        self.grant("lib/h.txt", "lane-1", head, 14, 16)
        self.grant("lib/h.txt", "lane-1", head, 17, 19)
        self.assertEqual(len(self.claims("lib/h.txt")), 5)
        self.grant("lib/h.txt", "lane-3", head, 16, 17)



class ClaimsCheckTests(CommandCase):
    """Decisions 6 to 8: every problem in one refusal, claims-check's requests, and lead-claims against the queue."""

    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        cls.fx, _ = command_fixture(cls, {"FX-5": "lane-1", "FX-7": "lane-2", "FX-8": "lane-3"})

    def requests(self, out: str) -> list[list[str]]:
        import shlex
        return [shlex.split(line.split("run_delivery.py ", 1)[1].split("   (", 1)[0])
                for line in out.splitlines() if "run_delivery.py lead-own " in line]

    def test_claims_check_lists_what_it_needs_and_the_lead_s_requests_settle_it(self) -> None:
        from tests.test_run_delivery import commit, sh
        k = numbered(30, "k")
        base = self.push_base({"lib/k.txt": k, "lib/del.txt": ["gone\n"], "lib/mode.sh": ["echo\n"],
                               "lib/whole.txt": ["whole\n"]})
        self.grant("lib/k.txt", "lane-1", base, 5, 10)
        self.grant("lib/k.txt", "lane-2", base, 20, 25)
        wt = self.fx.lane("FX-5")
        doc = self.record()
        self.assertIn("nothing more to claim", self.flow("claims-check", "FX-5", cwd=wt))
        self.assertEqual(self.record(), doc)
        # Another lane's change lands on the base after the branch began.
        self.push_base({"lib/k.txt": [*k[:21], "k landed\n", *k[22:]]})
        (wt / "lib/k.txt").write_text("".join([*k[:6], "k inside\n", *k[7:14], "k outside\n", *k[15:]]))
        (wt / "lib/new.txt").write_text("new\n")
        (wt / "lib/del.txt").unlink()
        (wt / "lib/mode.sh").chmod(0o755)
        (wt / "lib/whole.txt").write_text("changed\n")
        commit(wt, "FX-5: five changes")
        code, out = self.fx.flow("claims-check", "FX-5", cwd=wt)
        self.assertEqual(code, 1, out)
        for said in ("lib/k.txt [7, 8): inside lane-1's", "lib/k.txt [15, 16): outside lane-1's spans",
                     "lib/new.txt: needs a whole-file claim", "lib/del.txt: needs a whole-file claim",
                     "lib/mode.sh: needs a whole-file claim", "lib/whole.txt: needs a whole-file claim"):
            self.assertIn(said, out)
        self.assertNotIn("[22, 23)", out)
        requests = self.requests(out)
        self.assertEqual(len(requests), 5, out)
        for argv in requests:
            self.flow(*argv)
        self.assertIn("nothing more to claim", self.flow("claims-check", "FX-5", cwd=wt))
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-5", cwd=wt)
        out = self.flow("lead-claims", "FX-5")
        self.assertIn("FX-5 (lane-1), its pushed branch feature/FX-5", out)
        self.assertIn("the queue would accept its ownership", out)

    def test_every_problem_is_refused_at_once_and_lead_claims_judges_as_the_queue_does(self) -> None:
        from tests.test_run_delivery import sh
        from tests.test_run_delivery_supervisor import hand_off, queue_run
        r = numbered(30, "r")
        base = self.push_base({"lib/r.txt": r})
        self.grant("lib/r.txt", "lane-2", base, 4, 20)
        self.flow("lead-own", "--grant", "lib/r-new.txt", "--lane", "lane-2")
        changed = [*r[:4], "r five\n", *r[5:15], "r sixteen\n", *r[16:]]
        refusals = {}

        def narrowed(wt: Path, url: str) -> None:
            self.flow("lead-own", "--revoke-region", "lib/r.txt", "--lane", "lane-2", "--base", base, "--start", 4, "--end", 20)
            self.flow("lead-own", "--revoke", "lib/r-new.txt", "--lane", "lane-2")
            self.grant("lib/r.txt", "lane-2", base, 8, 12)
            refusals["prepare"] = self.flow("review-prepare", "FX-7", "--round", "2", cwd=wt, ok=False)
            refusals["handoff"] = self.flow("handoff", "FX-7", "--pr", url, cwd=wt, ok=False)
            self.flow("lead-own", "--revoke-region", "lib/r.txt", "--lane", "lane-2", "--base", base, "--start", 8, "--end", 12)
            self.grant("lib/r.txt", "lane-2", base, 4, 20)
            self.flow("lead-own", "--grant", "lib/r-new.txt", "--lane", "lane-2")

        wt = hand_off(self.fx, "FX-7", "lane-2", files={"lib/r.txt": "".join(changed), "lib/r-new.txt": "new\n"},
                      before=narrowed)
        handed = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
        self.flow("lead-own", "--revoke", "lib/r-new.txt", "--lane", "lane-2")
        self.flow("lead-own", "--revoke-region", "lib/r.txt", "--lane", "lane-2", "--base", base, "--start", 4, "--end", 20)
        self.grant("lib/r.txt", "lane-2", base, 8, 12)
        refusals["accept"] = self.flow("lead-accept", "FX-7", ok=False)
        for step, out in refusals.items():
            with self.subTest(step=step):
                self.assertIn("writer_not_owned" if step != "accept" else "changes files outside lane-2's", out)
                self.assertIn("lib/r.txt: [5, 6), [16, 17) are outside a single owned span", out)
                self.assertIn("lib/r-new.txt", out)
        self.flow("lead-own", "--revoke-region", "lib/r.txt", "--lane", "lane-2", "--base", base, "--start", 8, "--end", 12)
        self.grant("lib/r.txt", "lane-2", base, 4, 20)
        self.flow("lead-own", "--grant", "lib/r-new.txt", "--lane", "lane-2")
        self.flow("lead-accept", "FX-7")
        # A pushed branch that conflicts with the base.
        conflicting = self.fx.lane("FX-8")
        (conflicting / "lib/r.txt").write_text("".join([*r[:25], "r mine\n", *r[26:]]))
        sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "FX-8: conflicting", cwd=conflicting)
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-8", cwd=conflicting)
        # The base moves with an insertion at lane-2's span's end, so its claim no longer maps.
        self.push_base({"lib/r.txt": [*r[:19], "r at the edge\n", *r[19:25], "r theirs\n", *r[26:]]})
        out = self.flow("lead-claims")
        self.assertIn(f"FX-7 (lane-2), its handed head {handed[:12]}", out)
        self.assertIn("the queue would refuse it: region ownership refused lane-2's changes: lib/r.txt", out)
        self.assertIn("FX-8 (lane-3), its pushed branch feature/FX-8", out)
        self.assertIn("conflicts with", out)
        self.assertNotIn("FX-2", out)
        reason = out.split("region ownership refused lane-2's changes: ", 1)[1].splitlines()[0]
        queue_run(self.fx, "FX-7")
        [stopped] = [entry for entry in self.entries("candidate.stopped") if entry["subject"] == "FX-7"]
        self.assertEqual(stopped["data"]["reason"], "unreviewed_change")
        self.assertIn(reason, stopped["data"]["detail"])
        # A newer push after the hand-off: lead-claims checks the handed head.
        (wt / "lib/r-new.txt").write_text("newer\n")
        sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "FX-7: newer", cwd=wt)
        sh("git", "push", "-q", "origin", "HEAD:feature/FX-7", cwd=wt)
        self.assertIn(f"FX-7 (lane-2), its handed head {handed[:12]}", self.flow("lead-claims", "FX-7"))


if __name__ == "__main__":
    unittest.main()
