"""Seats, per-seat partitions and the causal ingest of a shared record.

An unshared record is characterized first: its journal validation, its links, ``wait`` and the writer pin stay as
they are while a shared record adds seats, partitions and origins. The multi-machine cases build their own bare
remote and clones through ``tests/collab_remotes.py`` and observe a peer only by reading its record's files.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_skills

from quiet_git import quieten
from tests.collab_remotes import (assert_fast_forwards_only, bare_remote as shared_bare_remote, clone, forced_updates,
                                  ref_updates)

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = Path(agent_skills.__file__).resolve().parents[1]
SKILL = PRODUCT_ROOT / "skills/orchestrate-work"
SCRIPTS = SKILL / "scripts"
TOOL = SCRIPTS / "delivery_state.py"
MACHINE = {"cores": 16, "load5": 1.0, "diskFreeBytes": 500 * 2**30, "swapUsedBytes": 2**30}
LEDGER = {"capacity": 4, "used": 0, "running": [], "waiting": [], "overrides": []}


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *arguments], cwd=repository, check=True,
                          capture_output=True, text=True).stdout.strip()


def bare_remote(root: Path, name: str = "remote.git") -> Path:
    """A bare remote under ``root`` whose main holds one commit, logging every ref update and allowing forced ones."""
    remote = shared_bare_remote(root / name)
    seed = root / f"{name}.seed"
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(seed)], check=True, capture_output=True)
    quieten(seed)
    (seed / "README.md").write_text("A shared delivery's fixture.\n", encoding="utf-8")
    git(seed, "add", "README.md")
    git(seed, "-c", "user.name=Seed", "-c", "user.email=seed@example.invalid", "commit", "--quiet", "-m", "Seed")
    git(seed, "push", "--quiet", str(remote), "main")
    return remote


def assert_fast_forward_only(remote: Path) -> None:
    """Every partition ref on ``remote`` has a logged update, and each update was a fast-forward: the shared helper's
    check, ref by ref."""
    refs = sorted({ref for ref, _, _ in ref_updates(remote, ("refs/agent-skills/*",))})
    if not refs:
        raise AssertionError(f"{remote} logged no update to a partition ref")
    for ref in refs:
        assert_fast_forwards_only(remote, ref)


def _load(name: str):
    # Importing the skill's modules here must not leave bytecode beside them.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


state_tool = _load("delivery_state")
collab = _load("collab")
collab.state_tool = state_tool
KEY = "V26-1"
# Each clone's base remote, as the binding would name it.
REMOTE = "origin"
HEAD = "4e66a0571b2c3d4e5f60718293a4b5c6d7e8f901"
# The fields D6 marks local, which differ between machines by design.
LOCAL = ("version",)
LOCAL_META = ("updatedAt", "writerGeneration", "closing")


def wait(directory: Path, after: int | None) -> tuple[int, dict]:
    return state_tool.wait_for_lead(directory, after=after, timeout=0, poll=0, read_machine=lambda _: MACHINE,
                                    read_ledger=lambda: LEDGER, sleep=lambda _: None)


def files(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in directory.iterdir()
            if path.is_file() and path.name in (state_tool.STATE_FILE, state_tool.JOURNAL_FILE)}


class UnsharedRecordCharacterizationTests(unittest.TestCase):
    """What an unshared record does today, which a shared record must leave exactly as it is."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name) / "record"
        state_tool.init(self.directory, "Unshared")

    def decide(self, **links: list[int]) -> dict:
        return state_tool.append_journal(self.directory, kind="decision.recorded",
                                         data={"text": "Take the left lane.", "why": "A fixture."}, **links)

    def test_journal_ids_run_from_one_and_an_entry_carries_no_origin(self) -> None:
        first, second = self.decide(), self.decide(because=[1])
        self.assertEqual((first["id"], second["id"]), (1, 2))
        self.assertNotIn("origin", first)
        self.assertNotIn("collab", state_tool.read_state(self.directory)["meta"])
        entries = state_tool.read_journal(self.directory)
        self.assertEqual(state_tool.journal_errors(entries), [])
        self.assertIn("entry 2: ids must run in sequence from 1",
                      state_tool.journal_errors([entries[0], dict(entries[1], id=3)]))
        self.assertIn("entry 1/because: entry 1 is not an earlier entry of this journal",
                      state_tool.journal_errors([dict(entries[1], id=1), dict(entries[0], id=2)]))

    def test_because_and_answers_name_earlier_entries_and_answers_only_the_kinds_the_registry_lists(self) -> None:
        self.decide()
        before = files(self.directory)
        for links, said in (({"because": [2]}, "/because: entry 2 is not an earlier entry of this journal"),
                            ({"because": [1, 1]}, "/because: names an entry twice")):
            with self.subTest(links=links):
                with self.assertRaises(state_tool.StateError) as refused:
                    self.decide(**links)
                self.assertEqual(refused.exception.code, "entry_invalid")
                self.assertIn(said, str(refused.exception))
                self.assertEqual(files(self.directory), before)
        with self.assertRaises(state_tool.StateError) as refused:
            state_tool.append_journal(self.directory, kind="blocker.resolved", data={"why": "Done."}, answers=[1])
        self.assertIn("/answers: a blocker.resolved does not answer a decision.recorded", str(refused.exception))
        blocker = state_tool.append_journal(self.directory, kind="blocker.recorded", data={"text": "Stuck."})
        resolved = state_tool.append_journal(self.directory, kind="blocker.resolved", data={"why": "Done."},
                                             answers=[blocker["id"]])
        self.assertEqual(resolved["answers"], [2])

    def test_wait_after_wakes_on_a_later_entry_of_a_wait_kind_only(self) -> None:
        self.decide()
        code, quiet = wait(self.directory, 0)
        self.assertEqual((code, quiet["woke"], quiet["events"]), (3, "expired", []))
        state_tool.append_journal(self.directory, kind="blocker.recorded", data={"text": "Stuck."})
        code, woke = wait(self.directory, 1)
        self.assertEqual((code, woke["woke"], [event["id"] for event in woke["events"]]), (0, "event", [2]))
        self.assertEqual(wait(self.directory, 2)[1]["woke"], "expired")

    def test_the_writer_pin_refuses_another_generation_and_leaves_the_record(self) -> None:
        first, second = ({"releaseId": "test-old", "archiveSha256": "a" * 64},
                         {"releaseId": "test-new", "archiveSha256": "b" * 64})
        with mock.patch.object(state_tool, "writer_generation", return_value=first):
            state_tool.upgrade_writer(self.directory, expected="unpinned", reason="Pin the fixture")
        before = files(self.directory)
        with mock.patch.object(state_tool, "writer_generation", return_value=second):
            with self.assertRaises(state_tool.StateError) as refused:
                self.decide()
        self.assertEqual(refused.exception.code, "writer_generation_mismatch")
        self.assertEqual(files(self.directory), before)
        with mock.patch.object(state_tool, "writer_generation", return_value=first):
            self.assertEqual(self.decide()["id"], 1)



def composed(machine: "Machine") -> dict:
    """A record's state as every machine must compose it: without the fields that are each machine's own, and with the
    merge regionsSettled names by its origin, since local ids differ between machines."""
    state = machine.state()
    shared = {name: value for name, value in state.items() if name not in LOCAL}
    if state.get("regionsSettled"):
        shared["regionsSettled"] = next(entry["origin"] for entry in machine.journal()
                                        if entry["id"] == state["regionsSettled"])
    shared["meta"] = {name: value for name, value in state["meta"].items() if name not in LOCAL_META}
    shared["meta"]["collab"] = {name: value for name, value in state["meta"]["collab"].items()
                                if name not in ("peers", "heldReason")}
    return shared


class Machine:
    """One machine's clone and the shared record in it. Its files are read directly: a peer is never asked to ingest."""

    def __init__(self, repo: Path) -> None:
        self.repo, self.directory = repo, repo / "state"

    def state(self) -> dict:
        return json.loads((self.directory / state_tool.STATE_FILE).read_text(encoding="utf-8"))

    def journal(self) -> list[dict]:
        text = (self.directory / state_tool.JOURNAL_FILE).read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines()]

    def origins(self) -> list[tuple[str, int, str]]:
        return [(entry["origin"]["seat"], entry["origin"]["seq"], entry["kind"]) for entry in self.journal()]

    def local_id(self, seat: str, seq: int) -> int:
        return next(entry["id"] for entry in self.journal()
                    if (entry["origin"]["seat"], entry["origin"]["seq"]) == (seat, seq))

    def seat(self) -> str | None:
        return state_tool.local_seat(self.repo, KEY)

    def push(self) -> dict:
        return collab.push(self.directory, remote=REMOTE)

    def take(self) -> dict:
        collab.fetch(self.directory, remote=REMOTE, key=KEY)
        return collab.ingest(self.directory, key=KEY)

    def sync(self) -> dict:
        self.push()
        return self.take()

    def decide(self, text: str = "Take the left lane.", **links) -> dict:
        return state_tool.append_journal(self.directory, kind="decision.recorded",
                                         data={"text": text, "why": "A fixture."}, **links)

    def patch(self, changes: dict, writer: str = "lead") -> dict:
        return state_tool.patch(self.directory, writer=writer, version=self.state()["version"], changes=changes)

    def surfaces(self, remote: Path) -> dict:
        """What a refused write must leave as it was: the record's files and this seat's refs, local and remote."""
        seat = self.seat()
        refs = {}
        for where, ref in ((self.repo, f"refs/agent-skills/{KEY}/{seat}"), (remote, f"refs/agent-skills/{KEY}/{seat}")):
            found = subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=where, capture_output=True, text=True)
            refs[f"{where.name}:{ref}"] = found.stdout.strip()
        return {**(files(self.directory) if self.directory.exists() else {}), **refs}


class SharedFixture(unittest.TestCase):
    """One bare remote and three clones, built once for the class and copied for each test, so a test costs only the
    git processes its own exchange starts."""

    @classmethod
    def setUpClass(cls) -> None:
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.template = Path(folder.name) / "template"
        cls.template.mkdir()
        remote = bare_remote(cls.template)
        for name in ("a", "b", "c"):
            clone(remote, cls.template / name, name=name.upper())

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / "machines"
        shutil.copytree(self.template, self.root, symlinks=True)
        for name in ("a", "b", "c"):
            config = self.root / name / ".git/config"
            config.write_text(config.read_text(encoding="utf-8").replace(str(self.template), str(self.root)),
                              encoding="utf-8")
        self.remote = self.root / "remote.git"
        self.a, self.b, self.c = (Machine(self.root / name) for name in ("a", "b", "c"))
        self.base = {"ref": "main", "head": git(self.a.repo, "rev-parse", "HEAD")}

    def open(self) -> str:
        collab.open_record(self.a.directory, key=KEY, headline="Shared", base=self.base)
        self.a.push()
        return self.a.seat()

    def join(self, machine: Machine, admit: bool = True) -> str:
        """``machine`` ingests the record, asks to join and, with ``admit``, is admitted by a; everyone catches up."""
        machine.take()
        collab.request_seat(machine.directory)
        machine.push()
        self.a.take()
        if admit:
            collab.admit_seat(self.a.directory, machine.seat())
            self.a.push()
            machine.take()
        return machine.seat()

    def plant(self, seat: str, held: list[dict], *, through: int | None = None) -> None:
        """Push a partition for ``seat`` by hand: its journal as the remote holds it, the slice ``held`` and, given, another
        ``through``. Anyone who can push may."""
        ref = f"refs/agent-skills/{KEY}/{seat}"
        tip = git(self.remote, "rev-parse", ref)
        partition = json.loads(git(self.remote, "cat-file", "blob", f"{tip}:partition.json"))
        partition["slice"] = held
        if through is not None:
            partition["through"] = through
        work = self.root / "planter"
        if not work.exists():
            clone(self.remote, work, name="Planter")
        blobs = {}
        for name, text in (("journal.jsonl", git(self.remote, "cat-file", "blob", f"{tip}:journal.jsonl") + "\n"),
                           ("partition.json", state_tool.dumps(partition))):
            blobs[name] = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=work, input=text, text=True,
                                         capture_output=True, check=True).stdout.strip()
        git(work, "fetch", "--quiet", str(self.remote), f"{ref}:{ref}")
        tree = subprocess.run(["git", "mktree"], cwd=work, capture_output=True, text=True, check=True,
                              input="".join(f"100644 blob {blobs[name]}\t{name}\n" for name in sorted(blobs))).stdout.strip()
        commit = git(work, "commit-tree", tree, "-p", tip, "-m", "Planted")
        git(work, "push", "--quiet", str(self.remote), f"{commit}:{ref}")

    def give(self, scope: str, machine: Machine) -> None:
        """a, holding every scope, passes ``scope`` to ``machine``, which reads the transfer before it writes."""
        collab.give_scope(self.a.directory, scope, machine.seat())
        self.a.push()
        machine.take()

    def exchange(self, *machines: Machine) -> None:
        """Every machine pushes, then every machine ingests, so each has every other's latest."""
        for machine in machines:
            machine.push()
        for machine in machines:
            machine.take()

    def assertConverged(self, *machines: Machine) -> None:
        first = machines[0]
        for other in machines[1:]:
            self.assertEqual(sorted(other.origins()), sorted(first.origins()))
            self.assertEqual(composed(other), composed(first))
        for machine in machines:
            self.assertEqual(state_tool.journal_errors(machine.journal()), [])


def queue_element(subject: str) -> dict:
    return {"order": 1, "subject": subject, "title": f"Candidate {subject}", "lane": "lane-1", "stage": "queued",
            "head": HEAD, "syncedHead": None, "leadInputs": [], "reportedRounds": 1, "runId": None, "checks": []}


def planned_element(subject: str, **fields: object) -> dict:
    return {"subject": subject, "title": f"Story {subject}", "lane": "lane-1", "stage": "planned", **fields}


class ExchangeTests(SharedFixture):
    """Two or three records exchange partitions and compose the same origins and state."""

    def test_two_records_reach_the_same_origins_and_state_and_the_joiner_starts_from_the_first_seats_start(self) -> None:
        first = self.open()
        second = self.join(self.b)
        joined = self.b.journal()
        self.assertEqual((joined[0]["kind"], joined[0]["id"], joined[0]["origin"]["seat"]), ("record.started", 1, first))
        request = next(entry for entry in joined if entry["kind"] == "seat.requested")
        self.assertEqual(request["origin"], {"seat": second, "seq": 1, "seen": {first: 2}})
        self.b.decide("Kofi's call.")
        self.a.patch({"meta": {"headline": "Renamed by a"}})
        self.exchange(self.a, self.b)
        self.assertConverged(self.a, self.b)
        self.assertEqual(self.b.state()["meta"]["headline"], "Renamed by a")
        self.assertEqual(self.a.state()["meta"]["collab"]["seats"],
                         {first: {"label": "A", "admitted": True, "approver": True},
                          second: {"label": "B", "admitted": True, "approver": True}})
        self.assertEqual(self.a.state()["meta"]["collab"]["peers"][second]["through"], 2)
        assert_fast_forward_only(self.remote)

    def test_concurrent_queue_elements_with_different_subjects_survive_on_both_machines_in_one_order(self) -> None:
        self.open()
        self.join(self.b)
        self.give("lane:lane-1", self.b)
        self.a.patch({"queue": [queue_element("APP-1")]})
        self.b.patch({"queue": [queue_element("APP-2")]})
        self.exchange(self.a, self.b)
        self.assertConverged(self.a, self.b)
        self.assertEqual(sorted(item["subject"] for item in self.a.state()["queue"]), ["APP-1", "APP-2"])

    def test_a_peer_entrys_because_links_to_the_local_id_of_the_entry_it_names(self) -> None:
        first = self.open()
        self.join(self.b)
        cause = self.a.decide("The cause.")
        self.a.push()
        self.b.decide("Only on b, so b's local ids run ahead of a's.")
        self.b.take()
        effect = self.b.decide("The effect.", because=[self.b.local_id(first, cause["origin"]["seq"])])
        self.b.push()
        self.a.take()
        ingested = next(entry for entry in self.a.journal() if entry["origin"] == effect["origin"])
        self.assertNotEqual(self.a.local_id(first, cause["origin"]["seq"]), self.b.local_id(first, cause["origin"]["seq"]))
        self.assertEqual(ingested["because"], [cause["id"]])
        self.assertEqual(ingested["data"], effect["data"])

    def test_with_three_seats_an_entry_waits_until_the_entry_its_seen_names_is_ingested(self) -> None:
        self.open()
        second = self.join(self.b)
        third = self.join(self.c)
        self.b.take()
        told = self.b.decide("b says it first.")
        self.b.push()
        self.c.take()
        answer = self.c.decide("c answers it.", because=[self.c.local_id(second, told["origin"]["seq"])])
        self.assertEqual(answer["origin"]["seen"][second], told["origin"]["seq"])
        self.c.push()
        collab.fetch(self.a.directory, remote=REMOTE)
        git(self.a.repo, "update-ref", "-d", f"refs/agent-skills-peers/{KEY}/{second}")
        waited = collab.ingest(self.a.directory)
        self.assertEqual(waited["ingested"], [])
        self.assertNotIn(answer["origin"], [entry["origin"] for entry in self.a.journal()])
        caught = self.a.take()
        self.assertEqual([(entry["seat"], entry["seq"]) for entry in caught["ingested"]],
                         [(second, told["origin"]["seq"]), (third, answer["origin"]["seq"])])
        self.assertEqual(state_tool.journal_errors(self.a.journal()), [])

    def test_a_settled_merge_and_a_close_name_their_own_local_ids_of_the_same_origin_on_both_machines(self) -> None:
        first = self.open()
        self.join(self.b)
        self.b.decide("Only on b, so its local ids run ahead.")
        merged = state_tool.append_journal(self.a.directory, kind="candidate.merged", data={"head": HEAD},
                                           subject="APP-1")
        self.a.patch({"regionsSettled": merged["id"]})
        closed = state_tool.append_journal(self.a.directory, kind="delivery.closed", data={})
        self.exchange(self.a, self.b)
        self.assertConverged(self.a, self.b)
        for machine in (self.a, self.b):
            with self.subTest(machine=machine.repo.name):
                state = machine.state()
                self.assertEqual(state["regionsSettled"], machine.local_id(first, merged["origin"]["seq"]))
                self.assertEqual(state["meta"]["closing"]["started"]["entry"],
                                 machine.local_id(first, closed["origin"]["seq"]))
        self.assertNotEqual(self.a.local_id(first, merged["origin"]["seq"]), self.b.local_id(first, merged["origin"]["seq"]))

    def test_a_planned_edit_and_a_concurrent_acceptance_end_with_the_story_queued_whichever_arrives_first(self) -> None:
        for first_in in ("a", "b"):
            with self.subTest(arrives_first=first_in):
                self.setUp()
                self.open()
                self.join(self.b)
                # b's lane accepts its story: it holds lane-1.
                self.give("lane:lane-1", self.b)
                self.a.patch({"planned": [planned_element("APP-1")]})
                self.exchange(self.a, self.b)
                self.a.patch({"planned": [planned_element("APP-1", after=["APP-0"])]})
                # b's lane accepts APP-1: the acceptance takes it from planned to the queue.
                self.b.patch({"planned": [], "queue": [queue_element("APP-1")]})
                order = (self.a, self.b) if first_in == "a" else (self.b, self.a)
                for machine in order:
                    machine.push()
                for machine in (*order, *order):
                    machine.take()
                self.assertConverged(self.a, self.b)
                for machine in (self.a, self.b):
                    state = machine.state()
                    self.assertEqual(state.get("planned"), [])
                    self.assertEqual([item["subject"] for item in state["queue"]], ["APP-1"])

    def test_after_a_removal_a_seat_that_may_create_the_element_makes_it_again_and_one_that_may_not_leaves_it_gone(self) -> None:
        self.open()
        self.join(self.b)
        third = self.join(self.c, admit=False)
        # b removes lane-1's planned story as its lane's acceptance would: it holds lane-1.
        self.give("lane:lane-1", self.b)
        self.a.patch({"planned": [planned_element("APP-1"), planned_element("APP-2")]})
        self.exchange(self.a, self.b)
        self.b.patch({"planned": [planned_element("APP-2")]})
        self.exchange(self.a, self.b)
        for machine in (self.a, self.b):
            self.assertEqual([item["subject"] for item in machine.state()["planned"]], ["APP-2"])
        # c, never admitted, says it writes the removed story and removes the other: neither counts anywhere.
        self.plant(third, [{"path": "planned[subject=APP-1]", "value": True, "origin": {"seat": third, "seq": 1}},
                           {"path": "planned[subject=APP-1]/title", "value": "Back", "origin": {"seat": third, "seq": 1}},
                           {"path": "planned[subject=APP-2]", "removed": True, "origin": {"seat": third, "seq": 1}}])
        self.exchange(self.a, self.b)
        for machine in (self.a, self.b):
            self.assertEqual([item["subject"] for item in machine.state()["planned"]], ["APP-2"])
        # a, which may create it, writes it again after the removal reached it.
        self.a.decide("APP-1 is back in the plan.")
        self.a.patch({"planned": [planned_element("APP-1", title="Back"), planned_element("APP-2")]})
        self.exchange(self.a, self.b)
        self.assertConverged(self.a, self.b)
        self.assertEqual(sorted(item["subject"] for item in self.b.state()["planned"]), ["APP-1", "APP-2"])
        self.assertEqual(next(item for item in self.b.state()["planned"] if item["subject"] == "APP-1")["title"], "Back")

    def test_an_element_removed_on_one_machine_is_gone_on_both_and_an_unpermitted_removal_is_ignored(self) -> None:
        self.open()
        self.join(self.b)
        third = self.join(self.c, admit=False)
        # b removes lane-1's planned story as its lane's acceptance would: it holds lane-1.
        self.give("lane:lane-1", self.b)
        self.a.patch({"planned": [planned_element("APP-1"), planned_element("APP-2")]})
        self.exchange(self.a, self.b)
        self.b.patch({"planned": [planned_element("APP-2")]})
        self.b.push()
        # b's removal names the element it removed, a's, by a's origin and the digest of the value carried.
        tip = git(self.remote, "rev-parse", f"refs/agent-skills/{KEY}/{self.b.seat()}")
        removal = next(item for item in json.loads(git(self.remote, "cat-file", "blob", f"{tip}:partition.json"))["slice"]
                       if item["path"] == "planned[subject=APP-1]")
        created = json.loads((self.a.directory / state_tool.FIELD_ORIGINS_FILE).read_text())["planned[subject=APP-1]"]
        self.assertEqual(removal, {"path": "planned[subject=APP-1]", "removed": True,
                                   "origin": {"seat": self.b.seat(), "seq": removal["origin"]["seq"],
                                              "seen": {seat: seq for seat, seq in state_tool.seat_counts(self.b.journal()).items()
                                                       if seat != self.b.seat()}},
                                   "replaces": {"seat": created["seat"], "seq": created["seq"],
                                                "digest": state_tool.value_digest(True)}})
        self.plant(third, [{"path": "planned[subject=APP-2]", "removed": True, "origin": {"seat": third, "seq": 1}}])
        self.exchange(self.a, self.b)
        self.assertConverged(self.a, self.b)
        for machine in (self.a, self.b):
            self.assertEqual([item["subject"] for item in machine.state()["planned"]], ["APP-2"])

    def test_a_checkout_with_no_seat_ingests_every_admitted_partition_and_cannot_write_as_the_lead(self) -> None:
        self.open()
        self.join(self.b)
        self.b.decide("b's word.")
        self.exchange(self.a, self.b)
        taken = self.c.take()
        self.assertIsNone(self.c.seat())
        self.assertEqual(sorted(self.c.origins()), sorted(self.a.origins()))
        self.assertEqual(composed(self.c), composed(self.a))
        self.assertEqual(len(taken["ingested"]), len(self.a.journal()))
        before = self.c.surfaces(self.remote)
        for write in (lambda: self.c.patch({"meta": {"headline": "c's"}}), lambda: self.c.decide("c's word.")):
            with self.assertRaises(state_tool.StateError) as refused:
                write()
            self.assertEqual(refused.exception.code, "collab.seat_missing")
            self.assertEqual(self.c.surfaces(self.remote), before)

    def test_the_sync_loop_fetches_peers_by_explicit_refspec_and_leaves_the_configured_one_and_the_forge_namespaces(self) -> None:
        first = self.open()
        self.join(self.b)
        fetch = git(self.b.repo, "config", "--get-all", "remote.origin.fetch")
        heads = git(self.b.repo, "ls-remote", "--heads", "--tags", "origin")
        self.a.decide("Something for b to fetch.")
        self.a.push()
        loop = subprocess.run([sys.executable, "-B", str(SCRIPTS / "collab.py"), "serve", "--dir", str(self.b.directory), "--remote", REMOTE,
                               "--passes", "1"], capture_output=True, text=True, timeout=120)
        self.assertEqual(loop.returncode, 0, loop.stderr)
        self.assertIn(self.b.local_id(first, 4), [entry["id"] for entry in self.b.journal()])
        self.assertEqual(git(self.b.repo, "config", "--get-all", "remote.origin.fetch"), fetch)
        self.assertEqual(git(self.b.repo, "ls-remote", "--heads", "--tags", "origin"), heads)
        self.assertEqual(git(self.b.repo, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags", "refs/remotes"),
                         "refs/heads/main\nrefs/remotes/origin/HEAD\nrefs/remotes/origin/main")

    def test_wait_after_wakes_on_an_ingested_peer_entry_of_a_wait_kind(self) -> None:
        self.open()
        self.join(self.b)
        through = self.a.journal()[-1]["id"]
        state_tool.append_journal(self.b.directory, kind="blocker.recorded", data={"text": "b is stuck."})
        self.b.push()
        self.assertEqual(wait(self.a.directory, through)[1]["woke"], "expired")
        self.a.take()
        code, woke = wait(self.a.directory, through)
        self.assertEqual((code, woke["woke"], [event["kind"] for event in woke["events"]]), (0, "event", ["blocker.recorded"]))


# D4's two lists of the integer fields of delivery-state/1: the journal ids a shared record carries by origin or derives
# on each machine, and every other integer, with the seq this story adds for each peer.
JOURNAL_IDS = {"regionsSettled", "queue[].leadSelection.reuse.decision", "meta.closing.started.entry",
               "meta.closing.finished.entry"}
NOT_JOURNAL_IDS = {"version", "queue[].order", "queue[].reportedRounds", "laneRegions[].start", "laneRegions[].end",
                   "meta.checkHistory.runs[]", "meta.checkHistory.checks.*.seconds[]", "meta.checkHistory.checks.*.flakes",
                   "meta.checking.now.steps[].expectedSeconds", "queue[].leadSelection.checks[].timeoutSeconds",
                   "meta.collab.peers.*.through"}


def integer_paths(schema: dict) -> set[str]:
    """Every integer field of ``schema``, by path, following $ref, items, additionalProperties and anyOf."""
    found: set[str] = set()

    def walk(node: object, path: str, following: tuple[str, ...]) -> None:
        if not isinstance(node, dict):
            return
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[1]
            if name not in following:
                walk(schema["$defs"][name], path, (*following, name))
        kinds = node.get("type")
        if kinds == "integer" or isinstance(kinds, list) and "integer" in kinds:
            found.add(path)
        for name, child in node.get("properties", {}).items():
            walk(child, f"{path}.{name}" if path else name, following)
        walk(node.get("items"), f"{path}[]", following)
        walk(node.get("additionalProperties"), f"{path}.*", following)
        for choice in node.get("anyOf", []):
            walk(choice, path, following)
    walk(schema, "", ())
    return found


class IntegerFieldTests(unittest.TestCase):
    """Every integer field of the record is a journal id the partition translates, or on D4's other list."""

    def test_the_walk_finds_exactly_d4s_two_lists_and_a_planted_integer_behind_a_ref_turns_it_red(self) -> None:
        schema = state_tool.load_schema("delivery-state/1")
        self.assertEqual(integer_paths(schema), JOURNAL_IDS | NOT_JOURNAL_IDS)
        self.assertEqual(len(JOURNAL_IDS | NOT_JOURNAL_IDS), 15)
        planted = json.loads(json.dumps(schema))
        planted["$defs"]["planted"] = {"type": "object", "properties": {"count": {"type": "integer"}}}
        planted["properties"]["lanes"]["items"]["properties"]["planted"] = {"$ref": "#/$defs/planted"}
        self.assertEqual(integer_paths(planted) - (JOURNAL_IDS | NOT_JOURNAL_IDS), {"lanes[].planted.count"})


class HoldAndSplitTests(SharedFixture):
    """A partition from another writer generation is held until this machine upgrades; a copied checkout that writes as
    the same seat is split from it by whichever chain reached the remote first."""

    def test_a_partition_from_another_generation_is_held_named_and_ingested_after_the_local_upgrade(self) -> None:
        old, new = ({"releaseId": "test-1", "archiveSha256": "a" * 64}, {"releaseId": "test-1", "archiveSha256": "b" * 64})
        with mock.patch.object(state_tool, "writer_generation", return_value=old):
            self.open()
            self.assertEqual(self.a.state()["meta"]["writerGeneration"], old)
            self.join(self.b)
        second = self.b.seat()
        with mock.patch.object(state_tool, "writer_generation", return_value=new):
            state_tool.upgrade_writer(self.b.directory, expected="a" * 64, reason="b applied the next release")
            told = self.b.decide("Written on the new release.")
            self.b.push()
        with mock.patch.object(state_tool, "writer_generation", return_value=old):
            taken = self.a.take()
            self.assertEqual(taken["held"], {second: "generation_differs"})
            self.assertNotIn(told["origin"], [entry["origin"] for entry in self.a.journal()])
            state = self.a.state()
            self.assertEqual(state["meta"]["collab"]["peers"][second]["heldReason"], "generation_differs")
            held = [entry for entry in self.a.journal() if entry["kind"] == "partition.held"]
            self.assertEqual([entry["data"] for entry in held], [{"seat": second, "reason": "generation_differs"}])
            again = self.a.take()
            self.assertEqual(len([entry for entry in self.a.journal() if entry["kind"] == "partition.held"]), 1, again)
        with mock.patch.object(state_tool, "writer_generation", return_value=new):
            state_tool.upgrade_writer(self.a.directory, expected="a" * 64, reason="a applied the next release")
            self.a.take()
        self.assertIn(told["origin"], [entry["origin"] for entry in self.a.journal()])
        self.assertIsNone(self.a.state()["meta"]["collab"]["peers"][second]["heldReason"])
        resumed = [entry for entry in self.a.journal() if entry["kind"] == "partition.resumed"]
        self.assertEqual([entry["data"] for entry in resumed], [{"seat": second}])
        self.assertEqual(resumed[0]["answers"], [held[0]["id"]])

    def copied(self) -> Machine:
        """A copy of a's checkout, record and all, as a person copying the folder makes: it writes as a's seat."""
        copy = Machine(self.root / "a-copy")
        shutil.copytree(self.a.repo, copy.repo, symlinks=True)
        self.assertEqual(copy.seat(), self.a.seat())
        return copy

    def test_a_copied_checkout_that_pushes_first_wins_and_the_original_is_split_either_way_round(self) -> None:
        for first_push in ("copy", "original"):
            with self.subTest(pushes_first=first_push):
                self.setUp()
                seat = self.open()
                self.join(self.b)
                self.a.push()
                twin = self.copied()
                ours = self.a.decide("Written in the original.")
                theirs = twin.decide("Written in the copy.")
                winner, loser = (twin, self.a) if first_push == "copy" else (self.a, twin)
                kept = theirs if first_push == "copy" else ours
                lost = ours if first_push == "copy" else theirs
                winner.push()
                with self.assertRaises(state_tool.StateError) as refused:
                    loser.push()
                self.assertEqual(refused.exception.code, "collab.seat_split")
                # Its unpushed chain is kept aside, holding what only it wrote.
                aside = git(loser.repo, "rev-parse", f"refs/agent-skills-split/{KEY}/{seat}")
                self.assertEqual(aside, git(loser.repo, "rev-parse", f"refs/agent-skills/{KEY}/{seat}"))
                self.assertIn(lost["data"]["text"], git(loser.repo, "cat-file", "blob", f"{aside}:journal.jsonl"))
                self.assertEqual(loser.state()["meta"]["collab"]["heldReason"], "seat_split")
                before = loser.surfaces(self.remote)
                for write in (lambda: loser.decide("Later."), lambda: loser.patch({"meta": {"headline": "Later"}}),
                              loser.push):
                    with self.assertRaises(state_tool.StateError) as later:
                        write()
                    self.assertEqual(later.exception.code, "collab.seat_split")
                    self.assertEqual(loser.surfaces(self.remote), before)
                self.b.take()
                origins = [entry["origin"] for entry in self.b.journal()]
                self.assertIn(kept["origin"], origins)
                self.assertNotIn(lost["data"], [entry["data"] for entry in self.b.journal()])
                assert_fast_forward_only(self.remote)

    def test_the_remote_helper_finds_only_fast_forwards_and_names_a_planted_forced_push(self) -> None:
        seat = self.open()
        self.a.decide("A second commit on the chain.")
        self.a.push()
        assert_fast_forward_only(self.remote)
        ref = f"refs/agent-skills/{KEY}/{seat}"
        first = git(self.remote, "rev-list", "--max-parents=0", ref)
        git(self.a.repo, "push", "--quiet", "--force", "origin", f"{first}:{ref}")
        self.assertEqual([update[0] for update in forced_updates(self.remote)], [ref])
        with self.assertRaises(AssertionError):
            assert_fast_forward_only(self.remote)
        self.assertEqual(forced_updates(self.remote, ("refs/heads/*",)), [])


class RefusalTests(SharedFixture):
    """Each refusal leaves the record, the local partition ref and the remote's as they were, beside its allowed case."""

    def test_a_write_with_no_seat_or_from_an_unadmitted_seat_is_refused_and_the_admitted_seat_writes(self) -> None:
        self.open()
        self.b.take()
        before = self.b.surfaces(self.remote)
        with self.assertRaises(state_tool.StateError) as refused:
            self.b.decide("No seat yet.")
        self.assertEqual(refused.exception.code, "collab.seat_missing")
        self.assertEqual(self.b.surfaces(self.remote), before)
        collab.request_seat(self.b.directory)
        self.b.push()
        before = self.b.surfaces(self.remote)
        for write in (lambda: self.b.decide("Not admitted yet."), lambda: self.b.patch({"meta": {"headline": "b's"}}),
                      lambda: collab.label_seat(self.b.directory, "Kofi M.")):
            with self.assertRaises(state_tool.StateError) as refused:
                write()
            self.assertEqual(refused.exception.code, "collab.seat_not_admitted")
            self.assertEqual(self.b.surfaces(self.remote), before)
        self.a.take()
        collab.admit_seat(self.a.directory, self.b.seat())
        self.a.push()
        self.b.take()
        self.assertEqual(self.b.decide("Admitted now.")["origin"]["seat"], self.b.seat())
        labeled = collab.label_seat(self.b.directory, "Kofi M.")
        self.assertEqual(self.b.state()["meta"]["collab"]["seats"][self.b.seat()]["label"], "Kofi M.")
        self.assertEqual(labeled["data"], {"seat": self.b.seat(), "label": "Kofi M."})

    def test_an_unadmitted_seats_partition_contributes_only_its_request(self) -> None:
        first = self.open()
        third = self.join(self.c, admit=False)
        ref = f"refs/agent-skills/{KEY}/{third}"
        tip = git(self.remote, "rev-parse", ref)
        partition = json.loads(git(self.remote, "cat-file", "blob", f"{tip}:partition.json"))
        request = json.loads(git(self.remote, "cat-file", "blob", f"{tip}:journal.jsonl"))
        # Anyone who can push may plant a later entry; it is never ingested while the seat is unadmitted.
        planted = {**request, "kind": "decision.recorded", "data": {"text": "Planted.", "why": "A fixture."},
                   "origin": {"seat": third, "seq": 2, "seen": {first: 2}}}
        partition["through"] = 2
        work = self.c.repo
        blobs = [subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=work, input=text, text=True,
                                capture_output=True, check=True).stdout.strip()
                 for text in (json.dumps(request) + "\n" + json.dumps(planted) + "\n", state_tool.dumps(partition))]
        tree = subprocess.run(["git", "mktree"], cwd=work, capture_output=True, text=True, check=True,
                              input=f"100644 blob {blobs[0]}\tjournal.jsonl\n100644 blob {blobs[1]}\tpartition.json\n").stdout.strip()
        git(work, "push", "--quiet", "origin", f"{git(work, 'commit-tree', tree, '-p', tip, '-m', 'Planted')}:{ref}")
        self.a.take()
        from_third = [entry["kind"] for entry in self.a.journal() if entry["origin"]["seat"] == third]
        self.assertEqual(from_third, ["seat.requested"])
        self.assertEqual(self.a.state()["meta"]["collab"]["seats"][third]["admitted"], False)
        collab.admit_seat(self.a.directory, third)
        self.a.take()
        self.assertEqual([entry["kind"] for entry in self.a.journal() if entry["origin"]["seat"] == third],
                         ["seat.requested", "decision.recorded"])

    def test_no_environment_variable_sets_or_overrides_the_seat(self) -> None:
        first = self.open()
        self.b.take()
        planted = {"AGENT_SKILLS_SEAT": first, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"agent-skills.seat.{KEY}",
                   "GIT_CONFIG_VALUE_0": first, "GIT_CONFIG_PARAMETERS": f"'agent-skills.seat.{KEY}={first}'",
                   "GIT_DIR": str(self.a.repo / ".git")}
        with mock.patch.dict(os.environ, planted):
            self.assertIsNone(state_tool.local_seat(self.b.directory, KEY))
            self.assertEqual(state_tool.local_seat(self.a.directory, KEY), first)
            with self.assertRaises(state_tool.StateError) as refused:
                self.b.decide("Not a's.")
        self.assertEqual(refused.exception.code, "collab.seat_missing")
        code, said = self.cli(self.b.directory, {**os.environ, **planted}, "journal", "--kind", "decision.recorded",
                              "--text", "Not a's.", "--why", "A fixture.")
        self.assertEqual((code, said["code"]), (1, "collab.seat_missing"))

    def cli(self, directory: Path, environment: dict, *arguments: str) -> tuple[int, dict]:
        ran = subprocess.run([sys.executable, "-B", str(TOOL), "--dir", str(directory), *arguments],
                             capture_output=True, text=True, env=environment)
        return ran.returncode, json.loads(ran.stdout)

    def test_the_sync_writer_is_the_tools_own_and_a_patch_may_not_write_as_it(self) -> None:
        self.open()
        before = self.a.surfaces(self.remote)
        version = str(self.a.state()["version"])
        code, said = self.cli(self.a.directory, dict(os.environ), "patch", "--writer", "sync", "--version", version,
                              "--patch", '{"meta":{"headline":"As sync"}}')
        self.assertEqual((code, said["code"]), (1, "collab.sync_writer_reserved"))
        self.assertEqual(self.a.surfaces(self.remote), before)
        code, said = self.cli(self.a.directory, dict(os.environ), "patch", "--writer", "lead", "--version", version,
                              "--patch", '{"meta":{"headline":"As the lead"}}')
        self.assertEqual((code, said["state"]["meta"]["headline"]), (0, "As the lead"))

    def test_the_state_tools_collab_steps_push_fetch_and_ingest_and_a_record_is_opened_once(self) -> None:
        self.open()
        state_tool.append_journal(self.a.directory, kind="decision.recorded", data={"text": "Pushed.", "why": "A fixture."})
        code, pushed = self.cli(self.a.directory, dict(os.environ), "collab-push", "--remote", REMOTE)
        self.assertEqual((code, pushed["pushed"]), (0, True), pushed)
        code, fetched = self.cli(self.b.directory, dict(os.environ), "collab-fetch", "--remote", REMOTE, "--key", KEY)
        self.assertEqual((code, fetched["seats"]), (0, [self.a.seat()]), fetched)
        code, ingested = self.cli(self.b.directory, dict(os.environ), "collab-ingest", "--key", KEY)
        self.assertEqual((code, len(ingested["ingested"])), (0, 3), ingested)
        before = self.a.surfaces(self.remote)
        with self.assertRaises(state_tool.StateError) as refused:
            collab.open_record(self.a.directory, key=KEY, headline="Again", base=self.base)
        self.assertEqual(refused.exception.code, "collab.record_exists")
        self.assertEqual(self.a.surfaces(self.remote), before)

    def test_a_lead_patch_never_makes_a_record_shared_or_edits_its_seats(self) -> None:
        unshared = self.root / "unshared"
        state_tool.init(unshared, "Unshared")
        for record, changes in ((unshared, {"meta": {"collab": {"key": KEY, "seats": {}, "peers": {}}}}),):
            with self.assertRaises(state_tool.StateError) as refused:
                state_tool.patch(record, writer="lead", version=1, changes=changes)
            self.assertEqual(refused.exception.code, "field_not_owned")
        self.open()
        with self.assertRaises(state_tool.StateError) as refused:
            self.a.patch({"meta": {"collab": {"seats": {self.a.seat(): {"label": "Renamed"}}}}})
        self.assertEqual(refused.exception.code, "field_not_owned")


class RecoveryTests(SharedFixture):
    """The base-move writer checks the seat, a refused push still reads the peers, and a lost record comes back whole."""

    def move(self, machine: Machine) -> dict:
        generation = {"releaseId": "test-1", "archiveSha256": "a" * 64}
        with mock.patch.object(state_tool, "writer_generation", return_value=generation):
            return state_tool.complete_base_move_locked(
                machine.directory, from_head=HEAD, to_head="b" * 40, default_head="c" * 40, expected_base=None,
                expected_writer="unpinned", reason="The base moved")

    def test_a_base_move_from_an_unadmitted_or_split_checkout_is_refused_and_an_admitted_seat_moves_it(self) -> None:
        self.open()
        self.join(self.c, admit=False)
        before = self.c.surfaces(self.remote)
        with self.assertRaises(state_tool.StateError) as refused:
            self.move(self.c)
        self.assertEqual(refused.exception.code, "collab.seat_not_admitted")
        self.assertEqual(self.c.surfaces(self.remote), before)
        moved = self.move(self.a)
        self.assertEqual(moved["meta"]["baseHead"], "b" * 40)
        self.assertEqual(self.a.journal()[-1]["kind"], "base.moved")
        with mock.patch.object(state_tool, "writer_generation", return_value=moved["meta"]["writerGeneration"]):
            collab._mark_split(self.a.directory)
        before = self.a.surfaces(self.remote)
        with self.assertRaises(state_tool.StateError) as refused:
            self.move(self.a)
        self.assertEqual(refused.exception.code, "collab.seat_split")
        self.assertEqual(self.a.surfaces(self.remote), before)

    def test_a_partition_push_runs_the_checkouts_own_pre_push_hook(self) -> None:
        self.open()
        second = self.join(self.b)
        marker = self.root / "pre-push-ran"
        hook = self.b.repo / ".git" / "hooks" / "pre-push"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f"#!/bin/sh\ncat > '{marker}'\n", encoding="utf-8")
        hook.chmod(0o755)
        self.b.decide("Pushed through the hook.")
        self.assertTrue(self.b.push()["pushed"])
        self.assertIn(f"refs/agent-skills/{KEY}/{second}", marker.read_text(encoding="utf-8"))

    def test_a_push_the_remote_refuses_still_fetches_and_ingests_the_peers(self) -> None:
        self.open()
        second = self.join(self.b)
        hook = self.remote / "hooks" / "pre-receive"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f"#!/bin/sh\nif grep -q '{second}'; then echo refused >&2; exit 1; fi\n", encoding="utf-8")
        hook.chmod(0o755)
        told = self.a.decide("For b to read.")
        self.a.push()
        self.b.decide("b's own, which the remote refuses.")
        with self.assertRaises(state_tool.StateError) as refused:
            collab.sync_once(self.b.directory, remote=REMOTE)
        self.assertEqual(refused.exception.code, "collab.push_failed")
        self.assertIn(told["origin"], [entry["origin"] for entry in self.b.journal()])

    def test_a_lost_record_is_rebuilt_from_the_remote_with_its_own_seats_chain(self) -> None:
        self.open()
        second = self.join(self.b)
        own = self.b.decide("b's own, before the loss.")
        self.b.push()
        origins = sorted(self.b.origins())
        shutil.rmtree(self.b.directory)
        rebuilt = self.b.take()
        self.assertEqual(sorted(self.b.origins()), origins, rebuilt)
        self.assertNotIn(second, self.b.state()["meta"]["collab"]["peers"])
        after = self.b.decide("b's own, after the rebuild.")
        self.assertEqual(after["origin"]["seq"], own["origin"]["seq"] + 1)
        self.b.push()
        self.a.take()
        self.assertIn(after["origin"], [entry["origin"] for entry in self.a.journal()])


class ConcurrentValueTests(SharedFixture):
    """Which of two values seats wrote to one path every machine keeps: the write that replaced the other, and
    between concurrent writes, the higher seat id. The seat ids are fixed so a tie names its winner."""

    HIGH, LOW = "s-bbbbbbbbbbbb", "s-aaaaaaaaaaaa"

    def setUp(self) -> None:
        super().setUp()
        for machine, seat in ((self.a, self.HIGH), (self.b, self.LOW)):
            git(machine.repo, "config", "--local", f"agent-skills.seat.{KEY}", seat)
        self.open()
        self.join(self.b)
        self.a.patch({"lanes": [{"id": "lane-1", "title": "Lane 1", "focus": "Idle", "state": "idle", "waitingOn": None,
                                 "since": "2026-11-24T00:00:00Z"}]})
        # Lane 1's rows are its lane's holder's and the queue's: b takes the lane, a keeps the queue.
        self.give("lane:lane-1", self.b)

    def focuses(self) -> list[str]:
        return [next(row for row in machine.state()["lanes"] if row["id"] == "lane-1")["focus"]
                for machine in (self.a, self.b)]

    def focus(self, machine: Machine, text: str) -> None:
        """A write to lane-1's focus, which its lane's holder, b, and the queue's, a, may both make."""
        state_tool.patch(machine.directory, writer="lead", version=machine.state()["version"],
                         selector="lanes[id=lane-1]", changes={"focus": text})

    def test_the_lower_seats_overwrite_of_the_higher_seats_value_stands_on_both_machines(self) -> None:
        self.focus(self.a, "a's")
        self.exchange(self.a, self.b)
        self.focus(self.b, "b's, over a's")
        self.exchange(self.a, self.b)
        self.assertEqual(self.focuses(), ["b's, over a's"] * 2)
        self.assertConverged(self.a, self.b)

    def test_concurrent_values_go_to_the_higher_seat_on_both_machines(self) -> None:
        self.focus(self.a, "a's")
        self.focus(self.b, "b's")
        self.exchange(self.a, self.b)
        self.assertEqual(self.focuses(), ["a's"] * 2)
        self.assertConverged(self.a, self.b)

    def test_an_overwrite_follows_only_the_value_it_replaced_not_a_later_one_at_the_same_seq(self) -> None:
        self.focus(self.a, "a's first")
        self.exchange(self.a, self.b)
        seq = self.a.journal()[-1]["origin"]["seq"]
        self.focus(self.b, "b's, over a's first")
        self.b.push()
        # a writes again at the same seq before it reads b's: b's write replaced a's first value, not this one.
        self.focus(self.a, "a's second")
        self.assertEqual(self.a.journal()[-1]["origin"]["seq"], seq)
        self.exchange(self.a, self.b)
        self.assertEqual(self.focuses(), ["a's second"] * 2)
        self.assertConverged(self.a, self.b)


class ShapeAndHoldTests(SharedFixture):
    """A shared journal's origins are checked, a partition that would break the record is held, an idle ingest
    writes nothing, and the sync loop pushes a local write."""

    def test_journal_errors_refuse_a_shared_journals_tampered_origins(self) -> None:
        first = self.open()
        self.a.decide("One.")
        self.a.decide("Two.")
        journal = self.a.journal()
        self.assertEqual(state_tool.journal_errors(journal), [])
        tampered = {
            "a skipped seq": (2, {"seq": 4}, "entry 3/origin/seq: seat {first}'s entries run in sequence from 1"),
            "seen names its own seat": (2, {"seen": {first: 1}}, "entry 3/origin/seen: a seat's seen names only other seats, not {first}"),
            "seen names a later entry": (2, {"seen": {"s-000000000000": 1}},
                                         "entry 3/origin/seen: names entry 1 of seat s-000000000000, which is not an earlier entry of this journal"),
        }
        for name, (index, change, said) in tampered.items():
            with self.subTest(tampered=name):
                copy = json.loads(json.dumps(journal))
                copy[index]["origin"].update(change)
                self.assertIn(said.format(first=first), state_tool.journal_errors(copy))
        mixed = json.loads(json.dumps(journal))
        del mixed[3]["origin"]
        self.assertIn("entry 4: every entry of a shared record carries its origin, and no entry of an unshared one does",
                      state_tool.journal_errors(mixed))

    def test_a_partition_that_would_break_the_record_is_held_as_invalid_and_journalled_once(self) -> None:
        for case, held, through in (
                ("an invalid value", [{"path": "meta/headline", "value": 5}], None),
                ("a through its journal lacks", [], 3)):
            with self.subTest(case=case):
                self.setUp()
                self.open()
                second = self.join(self.b)
                # b holds delivery, so the planted headline is its to write, and only its validity is left to judge.
                self.give("delivery", self.b)
                before = composed(self.a)
                # The planted value replaces a's headline, so it is applied, and held, whatever the seat ids are.
                headline = json.loads((self.a.directory / state_tool.FIELD_ORIGINS_FILE).read_text())["meta/headline"]
                replaces = {"seat": headline["seat"], "seq": headline["seq"],
                            "digest": state_tool.value_digest(self.a.state()["meta"]["headline"])}
                seen = {first: count for first, count in state_tool.seat_counts(self.a.journal()).items() if first != second}
                self.plant(second, [{**item, "origin": {"seat": second, "seq": 1, "seen": seen}, "replaces": replaces}
                                    for item in held], through=through)
                taken = self.a.take()
                self.assertEqual(taken["held"], {second: "slice_invalid"})
                self.assertEqual(self.a.state()["meta"]["collab"]["peers"][second]["heldReason"], "slice_invalid")
                self.assertEqual([entry["data"] for entry in self.a.journal() if entry["kind"] == "partition.held"],
                                 [{"seat": second, "reason": "slice_invalid"}])
                self.assertEqual(composed(self.a), before)
                version = self.a.state()["version"]
                self.a.take()
                self.assertEqual(self.a.state()["version"], version)
                self.assertEqual(len([entry for entry in self.a.journal() if entry["kind"] == "partition.held"]), 1)

    def test_an_ingest_with_nothing_new_leaves_the_record_as_it_is(self) -> None:
        self.open()
        self.join(self.b)
        self.a.take()
        before = files(self.a.directory)
        self.a.take()
        self.assertEqual(files(self.a.directory), before)

    def test_one_pass_of_the_sync_loop_pushes_a_local_write(self) -> None:
        self.open()
        self.join(self.b)
        told = self.b.decide("Written before the loop runs.")
        loop = subprocess.run([sys.executable, "-B", str(SCRIPTS / "collab.py"), "serve", "--dir", str(self.b.directory), "--remote", REMOTE,
                               "--passes", "1"], capture_output=True, text=True, timeout=120)
        self.assertEqual(loop.returncode, 0, loop.stderr)
        self.a.take()
        self.assertIn(told["origin"], [entry["origin"] for entry in self.a.journal()])


if __name__ == "__main__":
    unittest.main()
