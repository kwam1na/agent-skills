"""Authority scopes of a shared record: who holds each, who may write each field, admission, and the sync filter.

Every scope has one holder, derived from the journal: the first seat holds them all until it passes one on. A local
write to a field only the delivery holder writes is refused elsewhere, and a peer's value counts only if its seat held a
scope that may write it when it wrote it. The machines are the partitions tests' fixture: one bare remote and three
clones, each a seat.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests import test_collab_partitions as partitions

state_tool, collab, KEY = partitions.state_tool, partitions.collab, partitions.KEY
LANE_ROW = {"id": "lane-1", "title": "Lane 1", "focus": "Idle", "state": "idle", "waitingOn": None,
            "since": "2026-11-24T00:00:00Z"}
PLAN = "---\ntitle: A plan\n---\n\n# A plan\n\n## Scope\n\nWhat it does.\n\n## Decisions\n\nWhat binds it.\n"


class ScopeFixture(partitions.SharedFixture):
    """a opens the record and admits b, so a holds every scope."""

    def setUp(self) -> None:
        super().setUp()
        self.first = self.open()
        self.second = self.join(self.b)

    def journal(self, machine: partitions.Machine) -> list[dict]:
        return machine.journal()

    def refused(self, code: str, write, machine: partitions.Machine) -> state_tool.StateError:
        before = machine.surfaces(self.remote)
        with self.assertRaises(state_tool.StateError) as refused:
            write()
        self.assertEqual(refused.exception.code, code)
        self.assertEqual(machine.surfaces(self.remote), before)
        return refused.exception


class WriteAndTransferTests(ScopeFixture):
    """Only a scope's holder writes its fields, passes it on, or, holding delivery, admits a seat."""

    def test_a_non_holders_write_to_a_delivery_field_is_refused_naming_the_holder_and_the_holders_is_applied(self) -> None:
        said = self.refused("scope.not_held", lambda: self.b.patch({"meta": {"headline": "b's"}}), self.b)
        self.assertIn(f"seat {self.first}", str(said))
        self.assertEqual(self.a.patch({"meta": {"headline": "a's"}})["meta"]["headline"], "a's")
        # A journalled patch is checked the same way.
        self.a.patch({"finish": [{"id": "acs", "text": "It works.", "met": False}]})
        self.exchange(self.a, self.b)
        self.refused("scope.not_held", lambda: state_tool.journal_patched(
            self.b.directory, version=self.b.state()["version"], selector="finish[id=acs]", changes={"met": True},
            kind="decision.recorded", data={"text": "Met.", "why": "A fixture."}), self.b)

    def test_only_the_holder_passes_a_scope_on(self) -> None:
        self.refused("scope.not_held", lambda: collab.give_scope(self.b.directory, "delivery", self.second), self.b)
        given = collab.give_scope(self.a.directory, "lane:lane-1", self.second)
        self.assertEqual(given["data"], {"scope": "lane:lane-1", "from": self.first, "to": self.second})
        self.refused("scope.not_held", lambda: collab.give_scope(self.a.directory, "lane:lane-1", self.second), self.a)
        journal = self.a.journal()
        self.assertEqual((state_tool.holder(journal, "lane:lane-1"), state_tool.holder(journal, "lane:lane-2")),
                         (self.second, self.first))
        self.assertTrue(state_tool.holds(journal, self.second, "lane:*"))
        # A transfer only its holder made counts: a forged one, from a seat that does not hold the scope, does not.
        forged = {**journal[-1], "origin": {"seat": self.second, "seq": 99, "seen": {}},
                  "data": {"scope": "delivery", "from": self.second, "to": self.second}}
        self.assertEqual(state_tool.holder([*journal, forged], "delivery"), self.first)

    def test_a_seat_admitted_by_a_non_holder_is_refused_and_re_admission_by_the_holder_changes_its_approver(self) -> None:
        third = self.join(self.c, admit=False)
        self.b.take()
        self.refused("scope.not_held", lambda: collab.admit_seat(self.b.directory, third), self.b)
        code, said = self.cli(self.b, "seat", "admit", "--seat", third)
        self.assertEqual((code, said["code"]), (1, "scope.not_held"))
        # An admission journalled straight from a seat that does not hold delivery counts on no machine.
        state_tool.append_journal(self.b.directory, kind="seat.admitted",
                                  data={"seat": third, "label": "C", "approver": True})
        self.exchange(self.a, self.b)
        for machine in (self.a, self.b):
            self.assertEqual(machine.state()["meta"]["collab"]["seats"][third]["admitted"], False)
            self.assertFalse(collab.permitted(machine.journal(), third))
        code, said = self.cli(self.a, "seat", "admit", "--seat", third, "--no-approver")
        self.assertEqual(code, 0, said)
        self.assertEqual(self.a.state()["meta"]["collab"]["seats"][third]["approver"], False)
        again = collab.admit_seat(self.a.directory, third, approver=True)
        self.assertEqual(again["data"]["approver"], True)
        self.assertEqual(self.a.state()["meta"]["collab"]["seats"][third]["approver"], True)
        self.assertEqual(collab.admit_seat(self.a.directory, third, approver=True)["id"], again["id"])

    def cli(self, machine: partitions.Machine, *arguments: str) -> tuple[int, dict]:
        ran = subprocess.run([sys.executable, "-B", str(partitions.TOOL), "--dir", str(machine.directory), *arguments],
                             capture_output=True, text=True, env=dict(os.environ))
        return ran.returncode, json.loads(ran.stdout)

    def test_the_scope_commands_request_decline_and_give(self) -> None:
        self.b.take()
        code, asked = self.cli(self.b, "scope", "request", "--scope", "queue", "--reason", "I'm on tonight.")
        self.assertEqual(code, 0, asked)
        self.b.push()
        self.a.take()
        request = self.a.local_id(self.second, asked["entry"]["origin"]["seq"])
        self.assertEqual([entry["id"] for entry in collab.open_requests(self.a.journal())], [request])
        code, said = self.cli(self.a, "scope", "decline", "--request", str(request))
        self.assertEqual((code, said["entry"]["answers"]), (0, [request]), said)
        self.assertEqual(collab.open_requests(self.a.journal()), [])
        code, said = self.cli(self.a, "scope", "give", "--scope", "queue", "--to", self.second)
        self.assertEqual(code, 0, said)
        self.assertEqual(state_tool.holder(self.a.journal(), "queue"), self.second)
        # A request answered by a transfer stays answered once the scope comes back.
        code, asked = self.cli(self.a, "scope", "request", "--scope", "lane:lane-2", "--reason", "Mine.")
        self.assertEqual((code, asked["code"]), (1, "scope.held"))
        self.a.push()
        self.b.take()
        collab.request_scope(self.b.directory, "lane:lane-2", "I'll take lane 2.")
        self.b.push()
        self.a.take()
        collab.give_scope(self.a.directory, "lane:lane-2", self.second)
        self.a.push()
        self.b.take()
        collab.give_scope(self.b.directory, "lane:lane-2", self.first)
        self.b.push()
        self.a.take()
        self.assertEqual([entry["data"]["scope"] for entry in collab.open_requests(self.a.journal())], [])
        self.assertEqual(self.cli(self.a, "scope", "give", "--scope", "nowhere", "--to", self.second)[1]["code"],
                         "scope.invalid")
        self.refused("collab.seat_unknown", lambda: collab.give_scope(self.a.directory, "lane:lane-3", "s-000000000000"),
                     self.a)
        self.assertEqual(collab.auto_grants(self.b.directory, enabled=True), [])


class AutoGrantTests(ScopeFixture):
    """With collab.autoGrant on, the delivery holder passes a requested section nobody holds; never another."""

    def setUp(self) -> None:
        super().setUp()
        self.third = self.join(self.c)
        collab.give_scope(self.a.directory, "plan:## Decisions", self.third)
        self.a.push()
        self.b.take()
        for scope in ("plan:## Scope", "plan:## Decisions", "queue"):
            collab.request_scope(self.b.directory, scope, "I'm writing it.")
        self.b.push()
        self.a.take()

    def test_a_request_for_an_unclaimed_section_is_granted_and_one_for_a_held_section_or_the_queue_is_not(self) -> None:
        settings = self.root / "run-delivery.json"
        settings.write_text(json.dumps({"collab": {"autoGrant": True}}), encoding="utf-8")
        self.assertTrue(collab.auto_grant_setting(settings))
        given = collab.auto_grants(self.a.directory, enabled=collab.auto_grant_setting(settings))
        self.assertEqual([entry["data"]["scope"] for entry in given], ["plan:## Scope"])
        journal = self.a.journal()
        self.assertEqual(state_tool.holder(journal, "plan:## Scope"), self.second)
        self.assertEqual(state_tool.holder(journal, "plan:## Decisions"), self.third)
        self.assertEqual(state_tool.holder(journal, "queue"), self.first)
        self.assertEqual(sorted(entry["data"]["scope"] for entry in collab.open_requests(journal)),
                         ["plan:## Decisions", "queue"])
        self.assertEqual(state_tool.held_sections(journal, self.second, PLAN), ["## Scope"])
        self.assertEqual(state_tool.held_sections(journal, self.first, PLAN), ["(preamble)", "# A plan"])
        # A section nobody holds goes with delivery, wherever delivery goes.
        self.give("delivery", self.b)
        journal = self.b.journal()
        self.assertEqual(state_tool.holder(journal, "plan:# A plan"), self.second)
        self.assertEqual(state_tool.held_sections(journal, self.second, PLAN), ["(preamble)", "# A plan", "## Scope"])

    def test_with_auto_grant_off_the_request_stays_open(self) -> None:
        settings = self.root / "run-delivery.json"
        settings.write_text(json.dumps({"collab": {"autoGrant": False}}), encoding="utf-8")
        self.assertEqual(collab.auto_grants(self.a.directory, enabled=collab.auto_grant_setting(settings)), [])
        self.assertFalse(collab.auto_grant_setting(self.root / "absent.json"))
        # Only the delivery holder grants: b, which holds no delivery, grants nothing even with its own requests open.
        self.assertEqual(collab.auto_grants(self.b.directory, enabled=True), [])
        self.assertEqual(len(collab.open_requests(self.a.journal())), 3)
        self.assertEqual(state_tool.holder(self.a.journal(), "plan:## Scope"), self.first)


class SyncFilterTests(ScopeFixture):
    """A peer's value counts only if its seat held a scope that may write it when it wrote it."""

    def test_a_delivery_field_from_a_seat_that_does_not_hold_delivery_is_ignored_and_the_holders_is_applied(self) -> None:
        headline = json.loads((self.a.directory / state_tool.FIELD_ORIGINS_FILE).read_text())["meta/headline"]
        replaces = {"seat": headline["seat"], "seq": headline["seq"],
                    "digest": state_tool.value_digest(self.a.state()["meta"]["headline"])}
        self.plant(self.second, [{"path": "meta/headline", "value": "b's", "replaces": replaces,
                                  "origin": {"seat": self.second, "seq": 1}}])
        taken = self.a.take()
        self.assertEqual((taken["held"], self.a.state()["meta"]["headline"]), ({}, "Shared"))
        self.a.patch({"meta": {"headline": "a's"}})
        self.a.push()
        self.b.take()
        self.assertEqual(self.b.state()["meta"]["headline"], "a's")

    def test_a_value_written_while_its_seat_held_the_lane_counts_and_one_written_after_it_passed_it_on_does_not(self) -> None:
        self.a.patch({"lanes": [LANE_ROW]})
        self.give("lane:lane-1", self.b)
        # b journals nothing after taking the lane: its own last entry is still its request to join, from before the
        # transfer, so only the seen its write carries says it held lane-1.
        own = [entry for entry in self.b.journal() if entry["origin"]["seat"] == self.second]
        self.assertEqual(own[-1]["kind"], "seat.requested")
        self.focus(self.b, "b's, holding lane-1")
        collab.give_scope(self.b.directory, "lane:lane-1", self.first)
        self.b.push()
        # a ingests the transfer, which comes after the value's origin, and then the value: it counts.
        self.a.take()
        self.assertEqual(state_tool.holder(self.a.journal(), "lane:lane-1"), self.first)
        self.assertEqual(self.lane_focus(self.a), "b's, holding lane-1")
        # b's lane rows are not refused locally until the lane checks land; its peers ignore the write.
        self.focus(self.b, "b's, after passing lane-1 on")
        self.b.push()
        self.a.take()
        self.assertEqual(self.lane_focus(self.a), "b's, holding lane-1")

    def focus(self, machine: partitions.Machine, text: str) -> None:
        state_tool.patch(machine.directory, writer="lead", version=machine.state()["version"],
                         selector="lanes[id=lane-1]", changes={"focus": text})

    def lane_focus(self, machine: partitions.Machine) -> str:
        return next(row for row in machine.state()["lanes"] if row["id"] == "lane-1")["focus"]

    def test_a_queue_element_a_lane_makes_starts_with_the_queues_fields_at_their_starting_values(self) -> None:
        self.give("lane:lane-1", self.b)
        self.b.patch({"queue": [partitions.queue_element("APP-9")]})
        partition, _ = collab.build_partition(self.b.directory)
        planted = [{**item, "value": "merged"} if item["path"] == "queue[subject=APP-9]/stage" else item
                   for item in partition["slice"]]
        self.plant(self.second, planted)
        self.a.take()
        self.assertEqual([item["stage"] for item in self.a.state()["queue"] if item["subject"] == "APP-9"], [])

    def test_concurrent_follow_ups_from_delivery_and_a_lane_both_survive(self) -> None:
        self.give("lane:lane-1", self.b)
        self.a.patch({"followups": [{"id": "F-1", "title": "From the lead.", "reference": None}]})
        self.b.patch({"followups": [{"id": "F-2", "title": "From lane-1's acceptance.", "reference": None}]})
        self.exchange(self.a, self.b)
        self.assertConverged(self.a, self.b)
        self.assertEqual(sorted(item["id"] for item in self.a.state()["followups"]), ["F-1", "F-2"])


class LabelTests(ScopeFixture):
    """A label is display only: swapping two seats' labels, or giving both one, changes no holder and no refusal."""

    def test_holders_refusals_and_lane_ownership_are_the_same_whatever_the_labels(self) -> None:
        self.give("lane:lane-1", self.b)
        self.a.patch({"laneOwnership": {"lane-1": ["skills/a/*"]}})
        self.exchange(self.a, self.b)

        def seen() -> tuple:
            journal = self.b.journal()
            try:
                self.b.patch({"meta": {"headline": "b's"}})
                refusal = None
            except state_tool.StateError as error:
                refusal = error.code
            return (state_tool.holder(journal, "delivery"), state_tool.holder(journal, "lane:lane-1"),
                    state_tool.holds(journal, self.second, "lane:lane-1"), refusal, self.b.state()["laneOwnership"])
        before = seen()
        self.assertEqual(before, (self.first, self.second, True, "scope.not_held", {"lane-1": ["skills/a/*"]}))
        for first_label, second_label in (("B", "A"), ("Same", "Same")):
            with self.subTest(labels=(first_label, second_label)):
                collab.label_seat(self.a.directory, first_label)
                collab.label_seat(self.b.directory, second_label)
                self.exchange(self.a, self.b)
                seats = self.b.state()["meta"]["collab"]["seats"]
                self.assertEqual((seats[self.first]["label"], seats[self.second]["label"]), (first_label, second_label))
                self.assertEqual(seen(), before)


class FieldMapTests(unittest.TestCase):
    """Every delivery-state/1 property has a row, and an unshared record ignores scopes."""

    def test_every_property_has_a_row_and_one_added_without_a_row_is_named(self) -> None:
        schema = state_tool.load_schema("delivery-state/1")
        self.assertEqual(state_tool.field_map_gaps(schema), [])
        for place, expected in (("top", "top: planted"), ("meta", "meta: planted"), ("lanes", "lanes: planted"),
                                ("queue", "queue: planted"), ("planned", "planned: planted")):
            with self.subTest(place=place):
                planted = json.loads(json.dumps(schema))
                if place == "top":
                    target = planted["properties"]
                elif place == "meta":
                    target = planted["properties"]["meta"]["properties"]
                else:
                    node = planted["properties"][place]["items"]
                    target = (planted["$defs"][node["$ref"].rsplit("/", 1)[1]] if "$ref" in node else node)["properties"]
                target["planted"] = {"type": "string"}
                self.assertEqual(state_tool.field_map_gaps(planted), [expected])

    def test_the_map_names_the_d6_rows(self) -> None:
        state = {"meta": {}, "lanes": [LANE_ROW], "queue": [{"subject": "K", "lane": "lane-2"}],
                 "planned": [{"subject": "J", "lane": None}]}
        for path, scopes in (("meta/headline", ("delivery",)), ("meta/baseHead", ("queue",)), ("meta/updatedAt", "local"),
                             ("version", "local"), ("schemaVersion", "tool"), ("liveFlakes", ("queue",)),
                             ("lanes[id=lane-1]/focus", ("lane:lane-1", "queue")), ("lanes[id=lane-1]/title", ("delivery",)),
                             ("queue[subject=K]/stage", ("queue",)), ("queue[subject=K]/head", ("lane:lane-2",)),
                             ("queue[subject=K]", ("lane:lane-2", "delivery")), ("planned[subject=J]/title", ("delivery",)),
                             ("followups[id=F]", ("delivery", "lane:*")), ("regionsSettled", ("delivery",))):
            with self.subTest(path=path):
                self.assertEqual(state_tool.path_scopes(state, path), scopes)

    def test_an_unshared_record_ignores_scopes(self) -> None:
        folder = partitions.tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        directory = Path(folder.name) / "record"
        state_tool.init(directory, "Unshared")
        written = state_tool.patch(directory, writer="lead", version=1, changes={"meta": {"headline": "Renamed"},
                                                                                "lanes": [LANE_ROW]})
        self.assertEqual(written["meta"]["headline"], "Renamed")
        self.assertEqual(state_tool.scope_violations(directory, state_tool.initial_state("x"), written), [])
        self.assertIsNone(state_tool.holder(state_tool.read_journal(directory), "delivery"))


class WaitKindTests(ScopeFixture):
    """A peer's feedback, request for a scope and request to join each wake the lead's wait."""

    def test_each_new_wait_kind_wakes_wait_once_ingested(self) -> None:
        self.assertLessEqual({"feedback.raised", "scope.requested", "seat.requested"}, state_tool.WAIT_KINDS)
        through = self.a.journal()[-1]["id"]
        collab.request_scope(self.b.directory, "queue", "I'm on tonight.")
        state_tool.append_journal(self.b.directory, kind="feedback.raised",
                                  data={"anchor": "## Scope", "text": "Leaves out Windows.", "blocking": True})
        self.b.push()
        self.c.take()
        collab.request_seat(self.c.directory)
        self.c.push()
        self.a.take()
        code, woke = partitions.wait(self.a.directory, through)
        self.assertEqual((code, sorted(event["kind"] for event in woke["events"])),
                         (0, ["feedback.raised", "scope.requested", "seat.requested"]))


if __name__ == "__main__":
    unittest.main()
