from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.quiet_git import quiet_clone, quieten
from tests.delivery_views_fixtures import (
    FIX, LISTED, RUN_ID, VIEW, Repository, Runs, block, config, document_naming, reading, readings, reference,
    render, report, report_view, table, views_common,
)


FAR = 10_000


def summarize(view, listed=LISTED, read=None, specs=("review-round-report/1",)):
    summary = render.summarize_run_view(view, listed)
    return summary if read is None else render.with_round_reports(summary, view, read, list(specs))


class OutcomeWordsTests(unittest.TestCase):
    def test_every_approving_or_change_asking_outcome_reads_in_two_words_and_anything_else_is_kept(self) -> None:
        self.assertEqual(
            [render.outcome_word(value) for value in ["approve", "approved", "Aligned", "changes", "changes-requested",
                                                      "changes_requested", "request changes", "blocked", "", 7]],
            ["approve", "approve", "approve", "changes", "changes", "changes", "changes", "blocked", None, None])


class RunViewSummaryTests(unittest.TestCase):
    def test_reads_rounds_verdicts_findings_waits_work_and_latest_activity_with_the_inventory_timing(self) -> None:
        self.assertEqual(render.summarize_run_view(VIEW, LISTED), {
            "runId": "run-7", "ticket": None, "open": True, "status": "incomplete", "startedAt": None,
            "lastAt": "2026-10-22T05:05:00Z", "durationSeconds": 3000, "rounds": 2, "findings": None,
            "roundDetail": [
                {"round": 1, "at": "2026-10-22T04:40:00Z", "outcome": "changes", "findings": {"P0": 0, "P1": 1, "P2": 2, "P3": 0},
                 "verdicts": {"lens.outcome-correctness": "approve", "lens.adversarial-testing": "changes"}},
                {"round": 2, "at": "2026-10-22T04:58:00Z", "outcome": "approve", "findings": {"P0": 0, "P1": 0, "P2": 0, "P3": 1},
                 "verdicts": {"lens.outcome-correctness": "approve", "lens.adversarial-testing": "approve"}},
            ],
            "waits": [{"label": "Lead input", "detail": "Waiting on: lead · Reason: Needs the payment sandbox key · Since: 2026-10-22T04:50:00Z"}],
            "work": [{"label": "verify", "detail": "State: running · Next step: Wait for the gate"}],
            "activity": {"label": "verify · running", "at": "2026-10-22T05:05:00Z"},
        })

    def test_takes_the_latest_activity_from_running_work_as_well_as_finished_attempts(self) -> None:
        def at(phase, state, when):
            return {"label": phase, "fields": [{"label": "Phase", "value": phase}, {"label": "State", "value": state},
                                               {"label": "Last observed", "value": when}]}
        view = {"spec": "run-view/1", "sections": [
            {"id": "work", "items": [at("gate", "running", "2026-10-22T06:00:00Z")]},
            {"id": "activity-history", "items": [at("prepare", "completed", "2026-10-22T05:00:00Z")]},
        ]}
        summary = render.summarize_run_view(view, {"runId": "run-w"})
        self.assertEqual(summary["activity"], {"label": "gate · running", "at": "2026-10-22T06:00:00Z"})
        self.assertEqual(summary["lastAt"], "2026-10-22T06:00:00Z")

    def test_a_declared_round_not_yet_closed_has_no_outcome(self) -> None:
        view = {"spec": "run-view/1", "sections": [{"id": "reviews", "items": [
            {"label": "Declared review round", "fields": [{"label": "Round", "value": "3"}]},
            {"label": "lens.a", "fields": [{"label": "Round", "value": "3"}, {"label": "Verdict", "value": "approved"}]},
        ]}]}
        self.assertEqual(render.summarize_run_view(view, {"runId": "run-9"})["roundDetail"],
                         [{"round": 3, "at": None, "outcome": None, "findings": None, "verdicts": {"lens.a": "approve"}}])

    def test_the_summary_stays_bounded_however_busy_the_run_is(self) -> None:
        view = {"spec": "run-view/1", "sections": [
            {"id": "waiting", "items": [{"label": f"wait {index}", "fields": [{"label": "Reason", "value": "x" * 5000}]} for index in range(40)]},
            {"id": "work", "items": [{"label": f"work {index}", "fields": []} for index in range(40)]},
            {"id": "evidence", "items": [{"label": "review.round.closed", "fields": [
                {"label": "Reported at", "value": "2026-10-22T04:40:00Z"},
                {"label": "Observation", "value": json.dumps({"round": index + 1, "outcome": "approved",
                                                              "findings": {"P0": 0, "P1": 0, "P2": 0, "P3": 0}})}]}
                for index in range(500)]},
        ]}
        summary = render.summarize_run_view(view, {"runId": "run-busy"})
        self.assertEqual(len(summary["roundDetail"]), 12)
        self.assertEqual(summary["roundDetail"][11]["round"], 500)
        self.assertEqual(summary["rounds"], 500)
        self.assertEqual((len(summary["waits"]), len(summary["work"])), (5, 5))
        self.assertLessEqual(len(summary["waits"][0]["detail"]), 400)
        self.assertLess(len(json.dumps(summary)), 8000)

    def test_anything_that_is_not_a_run_view_has_no_summary(self) -> None:
        self.assertIsNone(render.summarize_run_view({"spec": "delivery-run-export/2"}, {"runId": "run-1"}))
        self.assertIsNone(render.summarize_run_view(None, {"runId": "run-1"}))


class RoundReportTests(unittest.TestCase):
    def rounds(self, view, read=None, specs=("review-round-report/1",)):
        return summarize(view, {**LISTED, "runId": RUN_ID}, readings() if read is None else read, specs)["roundDetail"]

    def test_attaches_each_rounds_findings_with_their_latest_observed_state_and_the_findings_it_settled(self) -> None:
        first, second = self.rounds(report_view())
        self.assertEqual(first["raised"], [
            {"id": "r1-f1", "lens": "lens.adversarial-testing", "severity": "P1", "summary": "A retry after a timeout charges twice", "state": "resolved", "filed": "open", "deferredIssue": None},
            {"id": "r1-f2", "lens": "lens.outcome-correctness", "severity": "P2", "summary": "The receipt keeps the first attempt's total", "state": "resolved", "filed": "open", "deferredIssue": None},
            {"id": "r1-f3", "lens": "lens.adversarial-testing", "severity": "P2", "summary": "No test covers a dropped second attempt", "state": "unresolved", "filed": "open", "deferredIssue": None},
        ])
        self.assertEqual(first["settled"], [])
        self.assertEqual([[note["id"], note["state"]] for note in second["raised"]], [["r2-f1", "unresolved"]])
        self.assertEqual(second["settled"], [
            {"id": "r1-f1", "state": "fixed", "fixCommit": FIX, "deferredIssue": None},
            {"id": "r1-f2", "state": "dropped", "fixCommit": None, "deferredIssue": None},
        ])
        self.assertNotIn("detailsUnavailable", first)

    def test_the_latest_observation_wins_whichever_way_it_moved(self) -> None:
        view = report_view()
        history = next(section for section in view["sections"] if section["id"] == "finding-history")
        history["items"].append({"id": "r1-f1", "label": "r1-f1", "fields": [{"label": "State", "value": "unresolved"}]})
        self.assertEqual(next(note for note in self.rounds(view)[0]["raised"] if note["id"] == "r1-f1")["state"], "unresolved")
        # A finding the run observed no state for keeps the report's own, in the run's words.
        history["items"].clear()
        self.assertEqual([note["state"] for note in self.rounds(view)[0]["raised"]], ["unresolved"] * 3)

    def test_a_round_with_no_report_reference_is_left_as_it_was(self) -> None:
        view = report_view([reference(2, "artifact-round-2")])
        first, second = self.rounds(view)
        self.assertEqual(first, render.summarize_run_view(view, {**LISTED, "runId": RUN_ID})["roundDetail"][0])
        self.assertEqual(len(second["raised"]), 1)

    def test_details_are_unavailable_when_no_reference_reads_as_the_rounds_report(self) -> None:
        cases = [
            ("an unavailable reference", report_view([reference(1, None), reference(2, "artifact-round-2")]), readings()),
            ("a missing attachment", report_view(), readings(**{"artifact-round-1": None})),
            ("a digest failure", report_view(), readings(**{"artifact-round-1": reading(report(1), digest="0" * 64)})),
            ("another run's report", report_view(), readings(**{"artifact-round-1": reading(report(1, runId="run-8"))})),
            ("another round's report", report_view(), readings(**{"artifact-round-1": reading(report(2))})),
            ("another round id", report_view(), readings(**{"artifact-round-1": reading(report(1, roundId="round-9"))})),
            ("another candidate", report_view(), readings(**{"artifact-round-1": reading(report(1, candidateTreeSha="9" * 40))})),
            ("an unlisted spec", report_view(), readings(**{"artifact-round-1": reading(report(1, spec="other-report/1"))})),
            ("a boolean round", report_view(), readings(**{"artifact-round-1": reading(report(1, round=True), round=True)})),
            ("something not a report", report_view(), readings(**{"artifact-round-1": reading("{not json")})),
        ]
        for name, view, read in cases:
            with self.subTest(name):
                first, second = self.rounds(view, read)
                self.assertEqual((first.get("detailsUnavailable"), first.get("raised")), (True, None))
                self.assertEqual(first["findings"], {"P0": 0, "P1": 1, "P2": 2, "P3": 0})
                self.assertEqual(len(second["raised"]), 1)

    def test_any_successful_capture_wins_whatever_the_order(self) -> None:
        read = readings(**{"artifact-bad": None})
        for references in ([reference(1, "artifact-bad"), reference(1, "artifact-round-1")],
                           [reference(1, "artifact-round-1"), reference(1, "artifact-bad")],
                           [reference(1, None), reference(1, "artifact-round-1")]):
            self.assertEqual(len(self.rounds(report_view(references), read)[0]["raised"]), 3)

    def test_both_report_specs_are_read_in_preference_order(self) -> None:
        older = report(1, spec="older-report/1", raised=[])
        read = readings(**{"artifact-old": reading(older, "artifact-old")})
        view = report_view([reference(1, "artifact-old"), reference(1, "artifact-round-1")])
        # The configured order decides, not the order of the references.
        self.assertEqual(len(self.rounds(view, read, ("review-round-report/1", "older-report/1"))[0]["raised"]), 3)
        self.assertEqual(self.rounds(view, read, ("older-report/1", "review-round-report/1"))[0]["raised"], [])
        # Either spec alone is read.
        self.assertEqual(self.rounds(report_view([reference(1, "artifact-old")]), read, ("older-report/1",))[0]["raised"], [])

    def test_hostile_report_text_stays_plain_and_a_fix_commit_must_be_a_commit_id(self) -> None:
        hostile = report(2, raised=[{"id": "r2-f1", "lens": "lens.outcome-correctness", "severity": "P3",
                                     "summary": "a\u0000b\u001bc\u007fd\n" + "x" * 300, "state": "open",
                                     "deferredIssue": "javascript:x\u0000"}],
                         resolved=[{"id": "r1-f1", "state": "fixed", "fixCommit": "javascript:alert(1)", "deferredIssue": None},
                                   {"id": "r1-f2", "state": "fixed", "fixCommit": FIX.upper(), "deferredIssue": None}])
        second = self.rounds(report_view(), readings(**{"artifact-round-2": reading(hostile)}))[1]
        self.assertRegex(second["raised"][0]["summary"], r"^a b c d x+$")
        self.assertEqual(len(second["raised"][0]["summary"]), 280)
        # A deferred issue is free text the page shows as text; one with a control character is dropped.
        self.assertIsNone(second["raised"][0]["deferredIssue"])
        self.assertEqual([entry["fixCommit"] for entry in second["settled"]], [None, None])

    def test_a_fix_commit_with_a_trailing_newline_is_not_a_commit_id(self) -> None:
        resolved = [{"id": "r1-f1", "state": "fixed", "fixCommit": "a" * 40 + "\n", "deferredIssue": None}]
        second = self.rounds(report_view(), readings(**{"artifact-round-2": reading(report(2, resolved=resolved))}))[1]
        self.assertIsNone(second["settled"][0]["fixCommit"])


def briefed(briefs: dict, extra: dict | None = None, view_references: list | None = None):
    """A round 1 report carrying briefs, its readings and its view."""
    body = report(1, briefs=briefs)
    read = readings(**{"artifact-round-1": reading(body), **(extra or {})})
    references = [reference(1, "artifact-round-1"), reference(2, "artifact-round-2"), *(view_references or [])]
    return report_view(references), read


def brief_reference(artifact: str, available: bool = True) -> dict:
    return reference(1, artifact if available else None, item_id=f"brief-round-1-round-1-{artifact}")


class BriefTests(unittest.TestCase):
    def round_one(self, briefs, extra=None, view_references=None):
        view, read = briefed(briefs, extra, view_references)
        return summarize(view, read=read)["roundDetail"][0]

    def test_each_lens_brief_and_result_become_their_text_or_null_when_not_recorded(self) -> None:
        first = self.round_one({"lens.outcome-correctness": {"brief": "b-1", "result": None}},
                               {"b-1": reading("Check the retry.", "b-1")}, [brief_reference("b-1")])
        self.assertEqual(first["briefs"], {"lens.outcome-correctness": {"brief": {"text": "Check the retry.", "shortened": False},
                                                                        "result": None}})

    def test_a_report_without_briefs_records_none(self) -> None:
        self.assertNotIn("briefs", summarize(report_view(), read=readings())["roundDetail"][0])

    def test_an_unreadable_or_unreferenced_text_is_unavailable(self) -> None:
        cases = {
            "a failed read": ({"b-1": None}, [brief_reference("b-1")]),
            "a digest failure": ({"b-1": reading("x", "b-1", digest="0" * 64)}, [brief_reference("b-1")]),
            "an unavailable reference": ({"b-1": reading("x", "b-1")}, [brief_reference("b-1", available=False)]),
            "no reference": ({"b-1": reading("x", "b-1")}, []),
        }
        for name, (extra, references) in cases.items():
            with self.subTest(name):
                first = self.round_one({"lens.a": {"brief": "b-1", "result": None}}, extra, references)
                self.assertEqual(first["briefs"]["lens.a"]["brief"], {"unavailable": True})

    def test_a_text_over_64_kib_is_shortened_at_a_character_boundary(self) -> None:
        # A three-byte character straddles the limit: it is left out whole, never split.
        data = b"a" * (64 * 1024 - 1) + "€".encode("utf-8") + b"tail"
        shown = render._brief_text(data)
        self.assertTrue(shown["shortened"])
        self.assertEqual(shown["text"], "a" * (64 * 1024 - 1))
        self.assertEqual(render._brief_text("€".encode("utf-8") * 10), {"text": "€" * 10, "shortened": False})

    def test_continuation_bytes_at_the_limit_are_never_walked_back_over_the_whole_text(self) -> None:
        shown = render._brief_text(b"\x80" * 70000)
        self.assertTrue(shown["shortened"])
        self.assertGreaterEqual(len(shown["text"]), 64 * 1024 - 3)
        self.assertEqual(set(shown["text"]), {"\ufffd"})

    def test_a_binary_text_renders_with_replacement_characters(self) -> None:
        shown = render._brief_text(bytes(range(256)) * 400)
        self.assertTrue(shown["shortened"])
        self.assertIn("�", shown["text"])
        json.dumps(shown)

    def test_brief_and_result_references_are_never_read_as_round_reports(self) -> None:
        view = report_view([reference(1, "artifact-round-1"), brief_reference("artifact-round-2")])
        self.assertEqual(render.report_references(view), {1: ["artifact-round-1"]})

    def test_the_page_budget_keeps_run_summaries_and_drops_the_oldest_rounds_texts_first(self) -> None:
        text = {"text": "t" * 1000, "shortened": False}
        def run(run_id):
            return {"runId": run_id, "roundDetail": [
                {"round": number, "briefs": {"lens.a": {"brief": dict(text), "result": dict(text)}}} for number in (1, 2)]}
        runs = {"run-a": run("run-a"), "run-b": run("run-b")}
        bare = {run_id: {"runId": run_id, "roundDetail": [
            {"round": number, "briefs": {"lens.a": {"brief": {"unavailable": True}, "result": {"unavailable": True}}}}
            for number in (1, 2)]} for run_id in runs}
        base = len(render.embed(bare).encode("utf-8"))
        kept = render.within_budget(runs, ["run-a", "run-b"], budget=base + 2 * 1050)
        def shown(run_id, index):
            return [part for part in ("brief", "result")
                    if "text" in kept[run_id]["roundDetail"][index]["briefs"]["lens.a"][part]]
        # The newest stop's newest round keeps its texts; everything older is unavailable.
        self.assertEqual(shown("run-b", 1), ["brief", "result"])
        self.assertEqual([shown("run-b", 0), shown("run-a", 1), shown("run-a", 0)], [[], [], []])
        self.assertEqual(kept["run-a"]["roundDetail"][0]["briefs"]["lens.a"]["brief"], {"unavailable": True})
        self.assertNotIn("unavailable", kept["run-a"])

    def test_a_malformed_cached_brief_is_skipped_not_a_failure(self) -> None:
        runs = {"r1": {"runId": "r1", "roundDetail": [{"round": 1, "briefs": {"lens.a": "oops", "lens.b": {"brief": 5}}}]}}
        self.assertEqual(render.within_budget(runs, ["r1"]), runs)

    def test_run_data_over_the_budget_leaves_the_oldest_stops_runs_unavailable(self) -> None:
        def run(run_id):
            return {"runId": run_id, "roundDetail": [], "work": [{"label": "x" * 1_500_000, "detail": ""}]}
        named = ["run-old", "run-mid", "run-new"]
        kept = render.within_budget({run_id: run(run_id) for run_id in named}, named)
        self.assertEqual(kept["run-old"], {"runId": "run-old", "unavailable": True})
        self.assertNotIn("unavailable", kept["run-new"])
        self.assertLessEqual(len(render.embed(kept).encode("utf-8")), render.PAGE_BUDGET_BYTES)


class CollectRunsTests(unittest.TestCase):
    # Its deadline cases time real reads, which a runner with every core busy can make miss.
    run_serially = True

    LISTED = [{"runId": "run-7", "status": "incomplete", "open": True, "durationSeconds": 3000, "bytes": 4000},
              {"runId": "run-8", "status": "complete", "open": False, "durationSeconds": 900, "bytes": 1200}]

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.runs = Runs(self.root)
        self.cache = self.root / "state" / ".cache" / "delivery-views" / "runs.json"

    def tearDown(self) -> None:
        self.folder.cleanup()

    def collect(self, document, deadline=FAR, **overrides):
        return render.collect_runs(document, config(self.runs, **overrides), self.root, self.cache, time.monotonic() + deadline)

    def test_summarizes_each_stops_run_through_the_runs_view_joined_on_run_id(self) -> None:
        self.runs.answer(table(self.LISTED, {"run-7": VIEW, "run-8": VIEW}))
        runs = self.collect(document_naming(["run-7", None], ["run-8"]))
        calls = self.runs.calls()
        self.assertEqual(calls[0], "runs list --json")
        self.assertCountEqual(calls[1:], ["runs view run-7 --json", "runs view run-8 --json"])
        self.assertEqual(runs, {"run-7": render.summarize_run_view(VIEW, self.LISTED[0]),
                                "run-8": render.summarize_run_view(VIEW, self.LISTED[1])})

    def test_an_unchanged_run_is_not_read_again_and_a_moved_one_is(self) -> None:
        document = document_naming(["run-7", "run-8"])
        self.runs.answer(table(self.LISTED, {"run-7": VIEW, "run-8": VIEW}))
        fresh = self.collect(document)
        later = [{**self.LISTED[0], "durationSeconds": 3100, "open": False, "status": "complete"}, self.LISTED[1]]
        self.runs.answer(table(later, {}))
        self.assertEqual(self.collect(document), {
            **fresh, "run-7": {**fresh["run-7"], "durationSeconds": 3100, "open": False, "status": "complete"}})
        self.assertEqual(self.runs.calls(), ["runs list --json"])
        self.runs.answer(table([{**self.LISTED[0], "bytes": 4800, "durationSeconds": 3600}, self.LISTED[1]], {"run-7": VIEW}))
        self.assertEqual(self.collect(document)["run-7"]["durationSeconds"], 3600)
        self.assertEqual(self.runs.calls(), ["runs list --json", "runs view run-7 --json"])

    def test_an_unknown_or_unreadable_run_is_unavailable_and_asked_for_again(self) -> None:
        self.runs.answer(table([*self.LISTED, {"runId": "run-bad", "bytes": 1}, {"runId": "run-garbled", "bytes": 1}],
                               {"run-7": VIEW, "run-garbled": "{not json"}))
        runs = self.collect(document_naming(["run-7", "run-gone", "run-bad", "run-garbled"]))
        for run_id in ("run-gone", "run-bad", "run-garbled"):
            self.assertEqual(runs[run_id], {"runId": run_id, "unavailable": True})
        self.assertNotIn("runs view run-gone --json", self.runs.calls())
        self.runs.answer(table([*self.LISTED, {"runId": "run-bad", "bytes": 1}], {"run-bad": VIEW}))
        self.assertNotIn("unavailable", self.collect(document_naming(["run-bad"]))["run-bad"])
        self.assertEqual(self.runs.calls(), ["runs list --json", "runs view run-bad --json"])

    def test_every_run_is_unavailable_when_the_inventory_cannot_be_read(self) -> None:
        self.runs.answer(table(None, {"run-7": VIEW}))
        self.assertEqual(self.collect(document_naming(["run-7"])), {"run-7": {"runId": "run-7", "unavailable": True}})
        self.assertEqual(self.runs.calls(), ["runs list --json"])

    def test_nothing_is_asked_when_no_stop_names_a_run(self) -> None:
        self.runs.answer(table(self.LISTED, {}))
        self.assertEqual(self.collect(document_naming([None, None])), {})
        self.assertEqual(self.collect({"state": {"queue": "x"}}), {})
        self.assertEqual(self.runs.calls(), [])

    def test_no_runs_configured_runs_no_command(self) -> None:
        self.runs.answer(table(self.LISTED, {"run-7": VIEW}))
        self.assertEqual(render.collect_runs(document_naming(["run-7"]), config(), self.root, self.cache, time.monotonic() + FAR), {})
        self.assertEqual(self.runs.calls(), [])

    def test_a_cache_of_another_spec_is_rebuilt(self) -> None:
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text(json.dumps({"spec": "delivery-views-runs/0", "runs": {
            "run-7": {"bytes": 4000, "summary": render.summarize_run_view(VIEW, self.LISTED[0])}}}))
        self.runs.answer(table(self.LISTED, {"run-7": VIEW}))
        self.collect(document_naming(["run-7"]))
        self.assertEqual(self.runs.calls(), ["runs list --json", "runs view run-7 --json"])
        self.assertEqual(json.loads(self.cache.read_text())["spec"], "delivery-views-runs/2")

    def test_a_cache_read_with_other_report_specs_is_read_again(self) -> None:
        older = report(1, spec="older-report/1")
        answers = table(self.LISTED, {"run-7": report_view()}, readings(**{"artifact-round-1": reading(older)}))
        self.runs.answer(answers)
        before = self.collect(document_naming(["run-7"]))["run-7"]["roundDetail"][0]
        self.assertTrue(before.get("detailsUnavailable"))
        self.assertIn("run-7", json.loads(self.cache.read_text())["runs"])
        self.runs.answer(answers)
        after = self.collect(document_naming(["run-7"]), findings={"reportSpecs": ["review-round-report/1", "older-report/1"]})
        self.assertEqual(self.runs.calls()[:2], ["runs list --json", "runs view run-7 --json"])
        self.assertEqual(len(after["run-7"]["roundDetail"][0]["raised"]), 3)

    def test_a_deeply_nested_cache_is_rebuilt(self) -> None:
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text("[" * 200000)
        self.runs.answer(table(self.LISTED, {"run-7": VIEW}))
        self.assertNotIn("unavailable", self.collect(document_naming(["run-7"]))["run-7"])

    def test_a_corrupt_cache_is_rebuilt_keeping_only_the_runs_the_record_names(self) -> None:
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text("{broken")
        self.runs.answer(table(self.LISTED, {"run-7": VIEW, "run-8": VIEW}))
        self.collect(document_naming(["run-7", "run-8"]))
        self.collect(document_naming(["run-7"]))
        self.assertEqual(list(json.loads(self.cache.read_text())["runs"]), ["run-7"])

    def test_an_unwritable_cache_only_costs_a_read(self) -> None:
        self.cache.parent.parent.mkdir(parents=True)
        # The cache's directory is a file, so nothing can be written there.
        self.cache.parent.write_text("not a directory")
        self.runs.answer(table(self.LISTED, {"run-7": VIEW}))
        first = self.collect(document_naming(["run-7"]))
        second = self.collect(document_naming(["run-7"]))
        self.assertEqual(first, second)
        self.assertEqual(self.runs.calls(), ["runs list --json", "runs view run-7 --json"] * 2)

    def test_each_round_report_is_read_once_and_none_again_while_the_run_is_unchanged(self) -> None:
        view = report_view()
        self.runs.answer(table(self.LISTED, {"run-7": view}, readings()))
        runs = self.collect(document_naming(["run-7"]))
        self.assertEqual(self.runs.calls(), ["runs list --json", "runs view run-7 --json",
                                             "runs artifact run-7 artifact-round-1 --json", "runs artifact run-7 artifact-round-2 --json"])
        self.assertEqual(runs["run-7"], summarize(view, self.LISTED[0], readings()))
        self.runs.answer(table(self.LISTED, {}))
        self.assertEqual(self.collect(document_naming(["run-7"])), runs)
        self.assertEqual(self.runs.calls(), ["runs list --json"])

    def test_a_run_whose_reports_cannot_be_read_keeps_those_rounds_with_details_unavailable(self) -> None:
        self.runs.answer(table(self.LISTED, {"run-7": report_view()}, {"artifact-round-2": readings()["artifact-round-2"]}))
        detail = self.collect(document_naming(["run-7"]))["run-7"]["roundDetail"]
        self.assertEqual([[entry["round"], entry.get("detailsUnavailable", False), len(entry["raised"]) if "raised" in entry else None]
                          for entry in detail], [[1, True, None], [2, False, 1]])

    def test_briefs_and_results_are_read_after_their_report(self) -> None:
        view, read = briefed({"lens.a": {"brief": "b-1", "result": "r-1"}}, {"b-1": reading("brief", "b-1"), "r-1": reading("result", "r-1")},
                             [brief_reference("b-1"), brief_reference("r-1")])
        self.runs.answer(table(self.LISTED, {"run-7": view}, read))
        first = self.collect(document_naming(["run-7"]))["run-7"]["roundDetail"][0]
        self.assertEqual(self.runs.calls()[-2:], ["runs artifact run-7 b-1 --json", "runs artifact run-7 r-1 --json"])
        self.assertEqual(first["briefs"]["lens.a"]["result"], {"text": "result", "shortened": False})

    def test_a_hung_oversize_failing_or_garbled_run_is_unavailable_and_the_rest_render(self) -> None:
        listed = [{"runId": name, "bytes": 1} for name in ("run-hang", "run-big", "run-exit", "run-text", "run-ok")]
        self.runs.answer({**table(listed, {"run-ok": VIEW, "run-text": "not json", "run-exit": {"exit": 3},
                                           "run-big": {"bytes": 17 * 1024 * 1024}, "run-hang": {"sleep": 30, "then": VIEW}})})
        started = time.monotonic()
        runs = self.collect(document_naming([entry["runId"] for entry in listed]), timeout=1)
        self.assertLess(time.monotonic() - started, 15)
        for name in ("run-hang", "run-big", "run-exit", "run-text"):
            self.assertEqual(runs[name], {"runId": name, "unavailable": True})
        self.assertNotIn("unavailable", runs["run-ok"])

    def test_a_report_or_brief_read_past_the_deadline_is_unavailable_and_the_run_uncached(self) -> None:
        view, read = briefed({"lens.a": {"brief": "b-1", "result": None}}, {"b-1": {"sleep": 30, "then": reading("brief", "b-1")}},
                             [brief_reference("b-1")])
        read["artifact-round-2"] = {"sleep": 30, "then": read["artifact-round-2"]}
        self.runs.answer(table(self.LISTED, {"run-7": view}, read))
        started = time.monotonic()
        # One command may take 10 seconds, but the render's shared deadline is 2.
        detail = self.collect(document_naming(["run-7"]), deadline=2)["run-7"]["roundDetail"]
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(detail[0]["briefs"]["lens.a"]["brief"], {"unavailable": True})
        self.assertTrue(detail[1].get("detailsUnavailable"))
        self.assertEqual(json.loads(self.cache.read_text())["runs"], {})

    def test_a_read_that_times_out_alone_keeps_the_run_uncached(self) -> None:
        read = readings()
        read["artifact-round-2"] = {"sleep": 30, "then": read["artifact-round-2"]}
        self.runs.answer(table(self.LISTED, {"run-7": report_view()}, read))
        self.assertTrue(self.collect(document_naming(["run-7"]), timeout=1)["run-7"]["roundDetail"][1].get("detailsUnavailable"))
        self.assertEqual(json.loads(self.cache.read_text())["runs"], {})

    def test_runs_past_the_deadline_are_unavailable_and_stay_uncached(self) -> None:
        # Six at a time, two rounds of reads fit the deadline, the third is still running at it, and the fourth can
        # only be handed out after it. Which reads miss it is otherwise the machine's: a read that stalls in the second
        # round misses it too, as on a loaded runner. So each run is held to what its own read did: a read handed out
        # after the deadline is refused, one still running at the deadline is stopped, and only a read that finished in
        # time is shown and cached.
        names = [f"run-{index}" for index in range(24)]
        listed = [{"runId": name, "bytes": 1} for name in names]
        self.runs.answer(table(listed, {name: {"sleep": 1, "then": VIEW} for name in names}))
        reads, lock, run_json = {}, threading.Lock(), views_common.run_json

        def observed(argv, **options):  # every command the deadline let through, with when it ran and how it ended
            key, began, ended = argv[len(self.runs.argv):], time.monotonic(), "failed"
            try:
                answer = run_json(argv, **options)
                ended = "read"
                return answer
            finally:
                if key[:2] == ["runs", "view"]:
                    with lock:
                        reads[key[2]] = (ended, began, time.monotonic())

        with mock.patch.object(views_common, "run_json", observed):
            started = time.monotonic()
            deadline = started + 2.5
            runs = render.collect_runs(document_naming(names), config(self.runs), self.root, self.cache, deadline)
        self.assertLess(time.monotonic() - started, 5)
        # Reads are handed out in order, so the runs refused at the deadline are the last ones, and the fourth round's
        # are among them.
        refused = [name for name in names if name not in reads]
        self.assertEqual(refused, names[len(names) - len(refused):])
        self.assertTrue(set(names[18:]) <= set(refused), refused)
        for name in names:
            with self.subTest(run=name):
                if name in refused:
                    self.assertTrue(runs[name].get("unavailable"))
                    continue
                ended, began, finished = reads[name]
                self.assertLess(began, deadline + 0.25, "a read began after the deadline")
                # Whether it finished or was stopped, no read outlives the deadline.
                self.assertLess(finished, deadline + 0.25, "a read ran past the deadline")
                self.assertEqual(bool(runs[name].get("unavailable")), ended == "failed")
        shown = [name for name in names if not runs[name].get("unavailable")]
        # The deadline did bite, and not on everything.
        self.assertTrue(shown and len(shown) < len(names), shown)
        # A read handed out before the deadline runs until it: some read was running at the deadline and was stopped
        # there, not refused while time remained.
        self.assertTrue(any(ended == "failed" for ended, _, _ in reads.values()), reads)
        self.assertEqual(sorted(json.loads(self.cache.read_text())["runs"]), sorted(shown))
        # The next render reads them again.
        self.runs.answer(table(listed, {name: VIEW for name in names}))
        self.assertFalse(any(run.get("unavailable") for run in self.collect(document_naming(names)).values()))

    def test_runs_are_read_six_at_a_time(self) -> None:
        names = [f"run-{index}" for index in range(13)]
        listed = [{"runId": name, "bytes": 1} for name in names]
        self.runs.answer(table(listed, {name: {"sleep": 0.5, "then": VIEW} for name in names}))
        started = time.monotonic()
        runs = self.collect(document_naming(names))
        self.assertLess(time.monotonic() - started, 0.5 * len(names) / 2)
        self.assertEqual(self.runs.most_at_once(), 6)
        self.assertFalse(any(run.get("unavailable") for run in runs.values()))

    def test_one_runs_failed_read_does_not_keep_a_concurrent_run_from_the_cache(self) -> None:
        listed = [{"runId": "run-bad", "bytes": 1}, {"runId": "run-good", "bytes": 1}]
        self.runs.answer(table(listed, {"run-good": {"sleep": 0.5, "then": VIEW}}))
        runs = self.collect(document_naming(["run-bad", "run-good"]))
        self.assertTrue(runs["run-bad"].get("unavailable"))
        self.assertFalse(runs["run-good"].get("unavailable"))
        self.assertEqual(sorted(json.loads(self.cache.read_text())["runs"]), ["run-good"])

    def test_a_hanging_read_holds_only_its_own_run(self) -> None:
        names = ["run-hang"] + [f"run-{index}" for index in range(12)]
        listed = [{"runId": name, "bytes": 1} for name in names]
        views = {name: {"sleep": 0.5, "then": VIEW} for name in names}
        views["run-hang"] = {"sleep": 60, "then": VIEW}
        self.runs.answer(table(listed, views))
        started = time.monotonic()
        runs = self.collect(document_naming(names), timeout=2)
        # One at a time this takes about 8 s; six at a time, about 2 s.
        self.assertLess(time.monotonic() - started, 4)
        self.assertTrue(runs["run-hang"].get("unavailable"))
        self.assertFalse(any(runs[name].get("unavailable") for name in names[1:]))
        self.assertEqual(sorted(json.loads(self.cache.read_text())["runs"]), sorted(names[1:]))


class LinkTests(unittest.TestCase):
    EXAMPLE = config(tracker={"name": "Jira", "issueUrl": "https://example.atlassian.net/browse/{key}", "keyPattern": "^(APP|ABC)-[0-9]+$"},
                    forges=[{"name": "Bitbucket", "repository": "example/app",
                             "pullUrl": "https://bitbucket.org/{repository}/pull-requests/{number}",
                             "commitUrl": "https://bitbucket.org/{repository}/commits/{sha}"}])

    def test_jira_and_bitbucket_configuration_produce_their_addresses(self) -> None:
        document = {"state": {"meta": {"trackerParent": "APP-1"}, "queue": [
            {"subject": "APP-12", "changeRequest": "https://bitbucket.org/example/app/pull-requests/34"},
            {"subject": "ABC-3", "changeRequest": "#35"},
            {"subject": "OTHER-1", "changeRequest": "https://bitbucket.org/someone/else/pull-requests/1"}]}}
        sha = "a" * 40
        runs = {"run-1": {"roundDetail": [{"settled": [{"fixCommit": sha}, {"fixCommit": "javascript:alert(1)"}]}]}}
        links = render.page_links(document, runs, self.EXAMPLE)
        self.assertEqual(links["issues"], {"APP-1": "https://example.atlassian.net/browse/APP-1",
                                           "APP-12": "https://example.atlassian.net/browse/APP-12",
                                           "ABC-3": "https://example.atlassian.net/browse/ABC-3"})
        self.assertEqual(links["changes"], {
            "https://bitbucket.org/example/app/pull-requests/34": {"label": "#34", "href": "https://bitbucket.org/example/app/pull-requests/34", "repository": "example/app"},
            "#35": {"label": "#35", "href": "https://bitbucket.org/example/app/pull-requests/35", "repository": "example/app"},
            "https://bitbucket.org/someone/else/pull-requests/1": {"label": "#1", "href": "https://bitbucket.org/someone/else/pull-requests/1", "repository": "someone/else"}})
        self.assertEqual(links["commits"], {sha: f"https://bitbucket.org/example/app/commits/{sha}"})

    def test_cross_repository_github_links_have_short_labels_and_repository_metadata(self) -> None:
        forge = {"name": "GitHub", "repository": "v26-technologies/athena",
                 "pullUrl": "https://github.com/{repository}/pull/{number}",
                 "commitUrl": "https://github.com/{repository}/commit/{sha}"}
        urls = [f"https://github.com/v26-technologies/agent-delivery-harness/pull/{n}" for n in (180, 181)]
        unknown = "https://unknown.test/a/b/pull/180"
        wrong_path = "https://github.com/a/b/issues/180"
        document = {"state": {"queue": [{"changeRequest": value} for value in [*urls, unknown, wrong_path]]}}
        links = render.page_links(document, {}, config(forges=[forge, self.EXAMPLE["forges"][0]]))["changes"]
        self.assertEqual(links, {url: {"label": f"#{n}", "href": url, "repository": "v26-technologies/agent-delivery-harness"}
                                 for n, url in zip((180, 181), urls)})
        other = "https://bitbucket.org/another/repo/pull-requests/180"
        links = render.page_links({"state": {"planned": [{"changeRequest": other}]}}, {},
                                  config(forges=[forge, self.EXAMPLE["forges"][0]]))["changes"]
        self.assertEqual(links[other], {"label": "#180", "href": other, "repository": "another/repo"})

    def test_a_native_review_and_an_entrys_facts_name_commits_and_the_story_is_told_unless_left_out(self) -> None:
        reviewed, handed = "b" * 40, "c" * 40
        document = {"state": {"queue": [{"subject": "APP-12"}]}, "journal": [
            {"id": 1, "at": "2026-11-16T10:00:00Z", "kind": "candidate.handed", "lane": "lane-1", "subject": "APP-12",
             "data": {"head": handed, "checks": [], "changeRequest": "#36"}}]}
        reviews = {"APP-12": {"roundDetail": [{"candidateRef": reviewed}]}}
        links = render.page_links(document, {}, self.EXAMPLE, reviews)
        self.assertEqual(set(links["commits"]), {reviewed, handed})
        self.assertEqual(set(links["changes"]), {"#36"})
        self.assertEqual(set(render.page_links(document, {}, self.EXAMPLE)["commits"]), {handed})
        told = render.line_map_data(document, {}, self.EXAMPLE, theme_key="local", history=None)
        self.assertEqual(told["document"]["story"]["schemaVersion"], "story/1")
        untold = render.line_map_data(document, {}, self.EXAMPLE, theme_key="local", history=None, story=False)
        self.assertNotIn("story", untold["document"])

    def test_a_commit_a_facts_words_name_cut_short_is_linked_and_a_plain_word_or_number_is_not(self) -> None:
        commits, changes = [], []
        render._facts_named({"text": "Reverted 1a2b3c4 and deadbeef7, not 1234567, decafbad or 1a2b3c",
                             "why": "See 0123456789abcdef0123456789abcdef01234567 too."}, commits, changes)
        # A full id is a commit only as a fact of its own: in words, an id cut short is taken, and a full one is not.
        self.assertEqual(commits, ["1a2b3c4", "deadbeef7"])
        self.assertEqual(changes, [])
        forge = self.EXAMPLE["forges"][0]
        for sha in ("1a2b3c4", "a" * 39, "a" * 40):
            with self.subTest(sha=sha):
                self.assertEqual(views_common.commit_link(sha, forge), f"https://bitbucket.org/example/app/commits/{sha}")
        for sha in ("1a2b3c", "a" * 41, "1A2B3C4", "1a2b3c4 "):
            with self.subTest(sha=sha):
                self.assertIsNone(views_common.commit_link(sha, forge))
        document = {"state": {"queue": [{"subject": "APP-12"}]}, "journal": [
            {"id": 1, "at": "2026-11-16T10:00:00Z", "kind": "decision.recorded", "lane": None, "subject": "APP-12",
             "data": {"text": "Keep 1a2b3c4", "why": "It holds."}}]}
        self.assertEqual(render.page_links(document, {}, self.EXAMPLE)["commits"],
                         {"1a2b3c4": "https://bitbucket.org/example/app/commits/1a2b3c4"})

    def test_the_skill_says_a_row_opens_to_nothing_and_its_sentence_links_its_evidence(self) -> None:
        text = " ".join((Path(__file__).resolve().parents[1] / "skills/delivery-views/SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("A row opens to nothing; the ticket, the commit, in full or cut short, and the change request its "
                      "sentence names are links, and they lead to the evidence. Only a run of bookkeeping folds, listing "
                      "its entries.", text)

    def test_with_no_tracker_or_forge_nothing_is_linked(self) -> None:
        document = {"state": {"queue": [{"subject": "APP-12", "changeRequest": "#34"}]}}
        self.assertEqual(render.page_links(document, {}, config()), {"issues": {}, "changes": {}, "commits": {}})


class RendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.runs = Runs(self.root)

    def tearDown(self) -> None:
        self.folder.cleanup()

    def repository(self, **overrides) -> Repository:
        raw = {"spec": views_common.CONFIG_SPEC, "runs": {"argv": self.runs.argv, "timeoutSeconds": 5}, **overrides}
        return Repository(self.root, raw)

    def test_renders_from_a_subdirectory_of_the_repository(self) -> None:
        repository = self.repository()
        (self.root / "deep" / "er").mkdir(parents=True)
        self.runs.answer(table([LISTED], {"run-7": VIEW}))
        result = repository.render(document_naming(["run-7"]), cwd=self.root / "deep" / "er")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(block(result.stdout.decode(), "delivery-runs")["run-7"]["rounds"], 2)
        self.assertTrue((self.root / "state" / ".cache" / "delivery-views" / "runs.json").is_file())

    def test_no_runs_configured_gives_no_run_section_and_runs_no_command(self) -> None:
        repository = Repository(self.root, {"spec": views_common.CONFIG_SPEC})
        self.runs.answer(table([LISTED], {"run-7": VIEW}))
        page = repository.render(document_naming(["run-7"])).stdout.decode()
        self.assertEqual(block(page, "delivery-runs"), {})
        self.assertFalse(block(page, "delivery-view")["runs"])
        self.assertEqual(self.runs.calls(), [])

    def test_a_missing_config_or_binding_fails_naming_it(self) -> None:
        repository = self.repository()
        (self.root / ".agents" / "delivery-views.json").unlink()
        result = repository.render({"state": {}})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(".agents/delivery-views.json", result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_a_document_that_is_not_an_object_fails(self) -> None:
        result = self.repository().render([1, 2])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")

    def test_a_state_dir_wins_over_the_bindings_directory_which_is_never_touched(self) -> None:
        repository = self.repository()
        self.runs.answer(table([LISTED], {"run-7": VIEW}))
        with tempfile.TemporaryDirectory() as elsewhere:
            served = Path(elsewhere) / "record"
            served.mkdir()
            result = repository.render(document_naming(["run-7"]), "--state-dir", str(served))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(views_common.runs_cache_path(served).is_file())
            self.assertIn(views_common.theme_key(served), result.stdout.decode())
        self.assertFalse((self.root / "state").exists(), "the binding's directory is neither read nor written")

    def test_two_state_directories_have_distinct_cache_files_and_theme_keys(self) -> None:
        first, second = self.root / "one", self.root / "two"
        self.assertNotEqual(views_common.runs_cache_path(first), views_common.runs_cache_path(second))
        self.assertNotEqual(views_common.theme_key(first), views_common.theme_key(second))
        repository = self.repository()
        page = repository.render({"state": {}}).stdout.decode()
        self.assertEqual(block(page, "delivery-view")["themeKey"], views_common.theme_key(self.root / "state"))

    def test_the_history_link_names_the_declared_history_renderers_output(self) -> None:
        line_map = {"argv": ["python3", "-B", "scripts/render.py", "line-map"], "output": "line-map.html"}
        history = {"argv": ["python3", "-B", "scripts/render.py", "history"], "output": "delivery-history.html"}
        self.assertEqual(render._renderer_output({"state": {"renderers": [line_map, history]}}, "history"), "delivery-history.html")
        self.assertEqual(render._renderer_output({"state": {"renderers": [line_map, history]}}, "line-map"), "line-map.html")
        self.assertIsNone(render._renderer_output({"state": {"renderers": [line_map]}}, "history"))
        self.assertIsNone(render._renderer_output({"state": {"renderers": [{**history, "argv": ["python3", "other.py", "history"]}]}}, "history"))

    def test_the_runs_deadline_is_capped_by_this_renderers_declared_timeout(self) -> None:
        binding = {"state": {"renderers": [
            {"argv": ["python3", "render.py", "history"], "timeoutSeconds": 60},
            {"argv": ["python3", "render.py", "line-map"], "timeoutSeconds": 15}]}}
        self.assertEqual(render._runs_deadline(binding, "line-map", 20), 10)
        self.assertEqual(render._runs_deadline(binding, "history", 20), 20)

    def test_concurrent_renders_all_succeed_and_leave_a_readable_cache(self) -> None:
        repository = self.repository()
        names = [f"run-{index}" for index in range(4)]
        self.runs.answer(table([{"runId": name, "bytes": 1} for name in names], {name: VIEW for name in names}))
        results = []
        threads = [threading.Thread(target=lambda: results.append(repository.render(document_naming(names)))) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([result.returncode for result in results], [0] * 4)
        for result in results:
            self.assertEqual(set(block(result.stdout.decode(), "delivery-runs")), set(names))
        cache = json.loads((self.root / "state" / ".cache" / "delivery-views" / "runs.json").read_text())
        self.assertEqual(set(cache["runs"]), set(names))
        self.assertEqual([path.name for path in (self.root / "state" / ".cache" / "delivery-views").iterdir()], ["runs.json"])

    def test_the_embedded_blocks_hold_no_raw_markup_or_line_separators(self) -> None:
        hostile = "</script><img src=x onerror=alert(1)>  <!--"
        page = self.repository(title="</script><img src=x>").render({"state": {"meta": {"headline": hostile}}}).stdout.decode()
        self.assertNotIn("</script><img", page)
        self.assertNotIn(" ", page)
        self.assertEqual(block(page, "delivery-state")["state"]["meta"]["headline"], hostile)
        self.assertIn("<title>&lt;/script&gt;&lt;img", page)



def plan_document(changes: int = 2, text: str = "Because the operator asked.") -> dict:
    """A valid delivery-plan/1 document with `changes` changes, oldest first."""
    unit = {"subject": "PAY-1", "title": "Pay", "criteria": [{"id": "AC1", "text": "It pays."}],
            "proofs": ["review", "merged"]}
    finish = [{"id": "acs", "label": "Criteria", "text": "Every criterion is proven", "at": "2026-10-30T10:00:00Z",
               "covers": {"criteria": "all", "proofs": ["review"]}}]
    return {
        "schemaVersion": "delivery-plan/1", "updated": "2026-10-30T12:00:00Z",
        "outcome": {"summary": "Payments work.", "text": "Every payment goes through."},
        "requiredLenses": ["lens.outcome-correctness"],
        "proofKinds": [{"id": "review", "label": "Review", "source": "record"},
                       {"id": "merged", "label": "Merged", "source": "record"}],
        "units": [unit], "finish": finish,
        "approved": {"at": "2026-10-30T09:00:00Z", "snapshot": {"units": [unit], "finish": finish}},
        "changes": [{"id": f"c{index}", "at": f"2026-10-30T11:{index // 60:02d}:{index % 60:02d}Z", "kind": "changed",
                     "subjects": ["PAY-1"], "by": "the operator", "why": text, "title": text, "source": "log",
                     "ref": "https://tracker.test/PAY-1", "before": text, "after": text} for index in range(changes)],
        "evidence": {},
    }


class PlanRenderTests(unittest.TestCase):
    """The line map embeds the plan chapter's input as C3 and C10 say: nothing without it, one line for a file it
    can't trust, and a valid plan within its share."""

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.runs = Runs(self.root)
        self.repository = Repository(self.root, {"spec": views_common.CONFIG_SPEC,
                                                 "runs": {"argv": self.runs.argv, "timeoutSeconds": 5}})
        self.runs.answer(table([LISTED], {"run-7": VIEW}))
        (self.root / "state").mkdir()
        self.plan_path = self.root / "state" / "plan.json"

    def tearDown(self) -> None:
        self.folder.cleanup()

    def page(self) -> str:
        result = self.repository.render(document_naming(["run-7"]))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode("utf-8")

    def test_without_a_plan_the_page_has_no_plan_block(self) -> None:
        page = self.page()
        # The page script looks the block up by its id; the page itself carries no block, and no plan.
        self.assertNotIn('id="delivery-plan"', page)
        self.assertIsNone(block(page, "delivery-plan"))
        self.assertEqual(render.render_line_map({"state": {}}, {}, config(), theme_key="k", history=None),
                         render.render_line_map({"state": {}}, {}, config(), theme_key="k", history=None, plan=None))

    def test_an_unreadable_plan_renders_as_if_absent_plus_one_line_naming_the_key_path(self) -> None:
        self.page()  # the first render fills the runs cache; compare cached pages with cached pages
        absent = self.page()
        secret = "SENTINEL-not-to-be-echoed"
        broken = plan_document()
        broken["units"][0][secret] = secret
        cases = {
            "an unknown key": (json.dumps(broken), "units[0]: holds an unknown key"),
            "not JSON": ("{" + secret, "plan.json: isn't JSON"),
            "over 1 MiB": (" " * (views_common.PLAN_LIMIT_BYTES + 1), "plan.json: holds"),
            "a dangling link": (None, "plan.json: doesn't exist"),
        }
        for name, (content, opening) in cases.items():
            with self.subTest(case=name):
                self.plan_path.unlink(missing_ok=True)
                if content is None:
                    self.plan_path.symlink_to(self.root / "nowhere.json")
                else:
                    self.plan_path.write_text(content, encoding="utf-8")
                page = self.page()
                self.assertEqual(block(page, "delivery-plan"), {"unreadable": block(page, "delivery-plan")["unreadable"]})
                message = block(page, "delivery-plan")["unreadable"]
                self.assertTrue(message.startswith(opening), message)
                self.assertNotIn(secret, page)
                self.assertNotIn(str(self.root), message)
                # Everything else is as if there were no plan.
                self.assertEqual(re.sub(r'<script type="application/json" id="delivery-plan">[^<]*</script>\n', "", page),
                                 absent)

    def test_a_valid_plan_is_embedded_whole_and_takes_its_share_from_the_runs(self) -> None:
        plan = plan_document()
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        page = self.page()
        self.assertEqual(block(page, "delivery-plan"), {"plan": plan})
        self.assertEqual(render.PLAN_SHARE_BYTES, 512 * 1024)
        # Runs that fit the page alone, but not beside the plan's share: the oldest stop's run gives way.
        big = "x" * (1200 * 1024)
        runs = {f"run-{index}": {"runId": f"run-{index}", "note": big} for index in range(3)}
        document = {"state": {"queue": [{"subject": f"PAY-{index}", "runId": f"run-{index}"} for index in range(3)]}}
        without = block(render.render_line_map(document, runs, config(), theme_key="k", history=None), "delivery-runs")
        self.assertFalse(any(run.get("unavailable") for run in without.values()))
        within = block(render.render_line_map(document, runs, config(), theme_key="k", history=None,
                                              plan=render.plan_block(plan)), "delivery-runs")
        # A plan that can't be read leaves the runs as if there were none.
        unreadable = block(render.render_line_map(document, runs, config(), theme_key="k", history=None,
                                                  plan={"unreadable": "plan.json: isn't JSON (at line 1)"}),
                           "delivery-runs")
        self.assertEqual(unreadable, without)
        self.assertTrue(within["run-0"].get("unavailable"))
        self.assertFalse(within["run-2"].get("unavailable"))

    def test_an_oversized_plan_is_clipped_oldest_changes_first_within_its_share_and_says_so(self) -> None:
        plan = plan_document(changes=500, text="t" * 400)
        self.assertGreater(len(render.embed(plan).encode("utf-8")), render.PLAN_SHARE_BYTES)
        self.assertLessEqual(len(json.dumps(plan).encode("utf-8")), views_common.PLAN_LIMIT_BYTES)
        embedded = render.plan_block(plan)
        self.assertLessEqual(len(render.embed(embedded).encode("utf-8")), render.PLAN_SHARE_BYTES)
        kept = embedded["plan"]
        # The snapshot and every count are kept.
        self.assertEqual(kept["approved"], plan["approved"])
        self.assertEqual([c["id"] for c in kept["changes"]], [c["id"] for c in plan["changes"]])
        self.assertEqual(kept["units"], plan["units"])
        self.assertEqual(kept["finish"], plan["finish"])
        # The oldest changes lost their text first; the newest kept theirs.
        clipped = [c["id"] for c in kept["changes"] if c.get("clipped")]
        self.assertEqual(clipped, [c["id"] for c in plan["changes"][:len(clipped)]])
        self.assertTrue(clipped)
        self.assertEqual(kept["changes"][-1], plan["changes"][-1])
        for change in kept["changes"][:len(clipped)]:
            self.assertEqual({k: change[k] for k in ("why", "title", "source", "ref", "before", "after")},
                             {"why": "", "title": "", "source": "", "ref": "", "before": "", "after": ""})
        self.assertEqual(embedded["clipped"], {"changes": len(clipped), "texts": 0})
        # Whatever the text length, the block fits its share or says it doesn't.
        for length in (373, 401, 450, 520, 600):
            for changes in (300, 420, 500):
                with self.subTest(length=length, changes=changes):
                    plan = plan_document(changes=changes, text="t" * length)
                    if len(json.dumps(plan).encode("utf-8")) > views_common.PLAN_LIMIT_BYTES:
                        continue
                    embedded = render.plan_block(plan)
                    # Only change text is over, and clipping it is enough: the block fits, and says nothing else.
                    self.assertLessEqual(len(render.embed(embedded).encode("utf-8")), render.PLAN_SHARE_BYTES)
                    self.assertEqual(set(embedded.get("clipped", {"changes": 0, "texts": 0})), {"changes", "texts"})
        # Oldest first by time, a fractional second included.
        plan = plan_document(changes=2, text="t" * 400)
        plan["changes"][0]["at"], plan["changes"][1]["at"] = "2026-10-30T11:00:00.5Z", "2026-10-30T11:00:00Z"
        embedded = render.plan_block(plan, share=len(render.embed({"plan": plan}).encode("utf-8")) - 100)
        self.assertEqual([c.get("clipped", False) for c in embedded["plan"]["changes"]], [False, True])
        # Oldest by time, not by position: the last change in the list is the earliest.
        plan = plan_document(changes=500, text="t" * 400)
        plan["changes"][-1]["at"] = "2026-10-29T00:00:00Z"
        clipped_ids = {c["id"] for c in render.plan_block(plan)["plan"]["changes"] if c.get("clipped")}
        self.assertIn(plan["changes"][-1]["id"], clipped_ids)
        self.assertNotIn(plan["changes"][-2]["id"], clipped_ids)
        plan = plan_document(changes=500, text="t" * 400)
        embedded = render.plan_block(plan)
        # On the page, too.
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        self.assertEqual(block(self.page(), "delivery-plan"), embedded)

    def test_past_the_changes_other_text_outside_the_snapshot_is_clipped_and_the_snapshot_never(self) -> None:
        plan = plan_document(changes=0)
        plan["units"] = [{"subject": f"PAY-{index}", "title": "t" * 300,
                          "criteria": [{"id": f"AC{index}-{n}", "text": "c" * 500} for n in range(5)],
                          "proofs": ["review"]} for index in range(200)]
        embedded = render.plan_block(plan)
        self.assertLessEqual(len(render.embed(embedded).encode("utf-8")), render.PLAN_SHARE_BYTES)
        self.assertGreater(embedded["clipped"]["texts"], 0)
        self.assertNotIn("overShare", embedded["clipped"])
        kept = embedded["plan"]
        self.assertEqual([[c["id"] for c in u["criteria"]] for u in kept["units"]],
                         [[c["id"] for c in u["criteria"]] for u in plan["units"]])
        self.assertEqual(kept["approved"], plan["approved"])
        self.assertEqual(kept["outcome"]["summary"], plan["outcome"]["summary"])
        # A snapshot over the share on its own is kept whole; the block says it is over, and the page gives it room.
        plan = plan_document(changes=0)
        plan["approved"]["snapshot"]["units"] = [{"subject": f"PAY-{index}", "title": "t",
                                                  "criteria": [{"id": f"AC{index}-{n}", "text": "c" * 560}
                                                               for n in range(5)], "proofs": ["review"]}
                                                 for index in range(200)]
        embedded = render.plan_block(plan)
        self.assertEqual(embedded["plan"]["approved"], plan["approved"])
        self.assertTrue(embedded["clipped"]["overShare"])
        size = len(render.embed(embedded).encode("utf-8"))
        self.assertGreater(size, render.PLAN_SHARE_BYTES)
        page = render.render_line_map({"state": {}}, {}, config(), theme_key="k", history=None, plan=embedded)
        self.assertEqual(block(page, "delivery-plan"), embedded)
        # Runs that fit beside a 512 KiB share, but not beside this block: the page still keeps its budget.
        each = (render.PAGE_BUDGET_BYTES - 2 - render.PLAN_SHARE_BYTES - 4096) // 3 - 64
        runs = {f"run-{index}": {"runId": f"run-{index}", "note": "x" * each} for index in range(3)}
        document = {"state": {"queue": [{"subject": f"PAY-{index}", "runId": f"run-{index}"} for index in range(3)]}}
        page = render.render_line_map(document, runs, config(), theme_key="k", history=None, plan=embedded)
        kept = block(page, "delivery-runs")
        self.assertTrue(kept["run-0"].get("unavailable"))
        self.assertLessEqual(len(render.embed(kept).encode("utf-8")) + size, render.PAGE_BUDGET_BYTES)

    def test_every_share_across_the_clipping_boundaries_is_met_exactly(self) -> None:
        # Shares a few bytes apart, between all-clipped and nothing-clipped: the running count and the note's cost
        # must be exact enough that the block always fits, and never claims it doesn't.
        plan = plan_document(changes=40, text="t" * 400)
        floor = len(render.embed(render.plan_block(plan, share=0)).encode("utf-8"))
        whole = len(render.embed({"plan": plan}).encode("utf-8"))
        for share in range(floor, whole, 7):
            embedded = render.plan_block(plan, share=share)
            self.assertLessEqual(len(render.embed(embedded).encode("utf-8")), share, share)
            self.assertNotIn("overShare", embedded.get("clipped", {}), share)

    def test_a_plan_review_gives_up_its_oldest_texts_first_and_keeps_every_verdict_count_and_commit(self) -> None:
        lens = "lens.outcome-correctness"
        plan = plan_document(changes=2)

        def entry(number: int) -> dict:
            return {"round": number, "candidateRef": f"{number:040x}", "at": "2026-10-30T10:00:00Z",
                    "outcome": "changes", "verdicts": {lens: "changes"}, "findings": {"P0": 0, "P1": 10, "P2": 0, "P3": 0},
                    "raised": [{"id": f"f{number}-{i}", "lens": lens, "severity": "P1", "summary": "s" * 280,
                                "state": "unresolved"} for i in range(10)],
                    "settled": [{"id": f"f{number - 1}-{i}", "lens": lens, "state": "fixed", "fixCommit": f"{number:040x}",
                                 "resolution": "r" * 600} for i in range(10)] if number > 1 else []}

        plan["planReview"] = {"lenses": [lens], "state": "open", "rounds": [entry(n) for n in range(1, 4)]}
        views_common.validate_plan(plan)
        whole = len(render.embed({"plan": plan}).encode("utf-8"))
        self.assertEqual(render.plan_block(plan, share=whole + 100), {"plan": plan})
        # A little over: the oldest round's resolutions go first, one by one; summaries and changes stay.
        embedded = render.plan_block(plan, share=whole - 3000)
        rounds = embedded["plan"]["planReview"]["rounds"]
        self.assertEqual([note["resolution"] for note in rounds[1]["settled"]][:5], [""] * 5)
        self.assertIn("r" * 600, [note["resolution"] for note in rounds[1]["settled"]])
        self.assertEqual({note["resolution"] for note in rounds[2]["settled"]}, {"r" * 600})
        self.assertEqual({note["summary"] for entry in rounds for note in entry["raised"]}, {"s" * 280})
        self.assertEqual([change["why"] for change in embedded["plan"]["changes"]], [plan["changes"][0]["why"]] * 2)
        self.assertEqual(embedded["clipped"], {"changes": 0, "texts": 6})
        # All the way: every review text goes, and every entry's coverage, verdicts, counts and fix commits stay.
        embedded = render.plan_block(plan, share=1024)
        kept = embedded["plan"]["planReview"]
        self.assertEqual({note["resolution"] for entry in kept["rounds"] for note in entry["settled"]}, {""})
        self.assertEqual({note["summary"] for entry in kept["rounds"] for note in entry["raised"]}, {""})
        for before, after in zip(plan["planReview"]["rounds"], kept["rounds"]):
            for key in ("round", "candidateRef", "verdicts", "findings", "outcome"):
                self.assertEqual(after[key], before[key], key)
            self.assertEqual([note["fixCommit"] for note in after["settled"]], [note["fixCommit"] for note in before["settled"]])
            self.assertEqual([note["id"] for note in after["raised"]], [note["id"] for note in before["raised"]])

    def test_the_second_pass_clips_the_least_needed_text_first_and_never_the_summary(self) -> None:
        plan = plan_document(changes=0)
        plan["proofKinds"].append({"id": "in-app", "label": "In-app", "source": "producer"})
        plan["units"] = [{"subject": f"PAY-{index}", "title": "T" * 500,
                          "criteria": [{"id": f"AC{index}-{n}", "text": "c" * 500} for n in range(2)],
                          "proofs": ["review", "in-app"]} for index in range(40)]
        plan["evidence"] = {f"PAY-{index}": {"in-app": {"state": "waived", "head": "a" * 40, "reason": "r" * 500}}
                            for index in range(40)}
        plan["approvals"] = [{"text": "a" * 500, "state": "waiting"} for _ in range(40)]
        plan["finish"] = [{**plan["finish"][0], "id": f"f{index}", "text": "f" * 500} for index in range(30)]
        plan["finish"][0]["covers"] = {"criteria": "all", "proofs": ["review"]}
        plan["outcome"]["text"] = "o" * 2000
        plan["outOfScope"] = ["s" * 500 for _ in range(40)]
        whole = len(render.embed({"plan": plan}).encode("utf-8"))
        texts = lambda kept: {
            "reasons": [e["in-app"]["reason"] for e in kept["evidence"].values()],
            "approvals": [a["text"] for a in kept["approvals"]], "finish": [f["text"] for f in kept["finish"]],
            "criteria": [c["text"] for u in kept["units"] for c in u["criteria"]],
            "titles": [u["title"] for u in kept["units"]], "outcome": [kept["outcome"]["text"]],
            "outOfScope": kept["outOfScope"]}
        # Partway: reasons, approvals and finish texts go, then some criteria; titles, outcome and outOfScope stay.
        kept = texts(render.plan_block(plan, share=whole - 120 * 520)["plan"])
        for name in ("reasons", "approvals", "finish"):
            self.assertEqual(set(kept[name]), {""}, name)
        self.assertIn("", kept["criteria"])
        self.assertIn("c" * 500, kept["criteria"])
        for name in ("titles", "outcome", "outOfScope"):
            self.assertNotIn("", kept[name], name)
        # All the way: everything but the summary, the ids and the snapshot.
        embedded = render.plan_block(plan, share=1024)
        kept = texts(embedded["plan"])
        for name, values in kept.items():
            self.assertEqual(set(values), {""}, name)
        self.assertEqual(embedded["plan"]["outcome"]["summary"], plan["outcome"]["summary"])
        self.assertEqual(embedded["plan"]["approved"], plan["approved"])
        self.assertTrue(embedded["clipped"]["overShare"])



class NeedsBlockTests(unittest.TestCase):
    """The renderer's read of the supervisor's needs-you.json: bounded, refused whole when it can't be trusted."""

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.state = Path(holder.name)

    def write(self, written: object) -> None:
        (self.state / render.NEEDS_FILE).write_text(json.dumps(written))

    def listed(self, items: list, running: bool = True) -> dict:
        return {"spec": render.NEEDS_SPEC, "running": running, "pass": 1, "at": "2026-01-01T00:00:00Z",
                "staleAfter": "2026-01-01T00:02:00Z", "items": items}

    def test_no_file_is_no_block(self) -> None:
        self.assertIsNone(render.read_needs_block(self.state))

    def test_texts_are_cut_and_items_capped_with_the_rest_counted(self) -> None:
        item = lambda n: {"holding": n % 2 == 0, "source": "s" * 700, "text": f"item {n}", "clears": "c"}
        self.write(self.listed([item(n) for n in range(render.NEEDS_ITEMS + 3)]))
        block = render.read_needs_block(self.state)
        self.assertEqual(len(block["items"]), render.NEEDS_ITEMS)
        self.assertEqual(block["more"], 3)
        self.assertEqual(len(block["items"][0]["source"]), render.NEEDS_TEXT)
        self.assertTrue(block["items"][0]["source"].endswith("…"))
        self.assertEqual(block["items"][0]["text"], "item 0")

    def test_a_rows_facts_and_words_and_the_lists_own_words_pass_through_and_nothing_else(self) -> None:
        item = {"holding": True, "source": "s", "text": "t", "clears": "c", "kind": "stopped", "who": "lead",
                "lane": "lane-1", "subject": "APP-1", "state": "stopped", "since": "2026-01-01T00:00:00Z",
                "blocker": {"subject": "APP-0", "lane": "lane-2", "state": "review", "extra": "x"},
                "said": {"headline": "h", "why": "w" * 700, "clears": "c", "who": "The lead", "extra": "x"},
                "fields": {"detail": "x"}, "waiting": 2}
        written = self.listed([item])
        written.update(summary="1 thing needs the lead.", words={"heading": "H", "holding": "M", "age": "for {age}", "x": "y"})
        self.write(written)
        block = render.read_needs_block(self.state)
        [row] = block["items"]
        self.assertEqual(set(row), {"holding", "source", "text", "clears", "kind", "who", "lane", "subject", "state", "since",
                                    "blocker", "said"})
        self.assertEqual(row["blocker"], {"subject": "APP-0", "lane": "lane-2", "state": "review"})
        self.assertEqual(set(row["said"]), {"headline", "why", "clears", "who"})
        self.assertEqual(len(row["said"]["why"]), render.NEEDS_TEXT)
        self.assertEqual((block["summary"], block["words"]), ("1 thing needs the lead.", {"heading": "H", "holding": "M", "age": "for {age}"}))

    def test_a_stopped_list_needs_no_stale_after(self) -> None:
        written = self.listed([], running=False)
        del written["staleAfter"]
        self.write(written)
        self.assertEqual(render.read_needs_block(self.state), {"running": False, "at": "2026-01-01T00:00:00Z", "items": []})

    def test_a_symlink_an_oversized_file_or_one_bad_item_is_unreadable(self) -> None:
        elsewhere = self.state / "elsewhere.json"
        elsewhere.write_text(json.dumps(self.listed([])))
        (self.state / render.NEEDS_FILE).symlink_to(elsewhere)
        self.assertEqual(render.read_needs_block(self.state), {"unreadable": True})
        (self.state / render.NEEDS_FILE).unlink()
        # A list that is valid in every way but its size.
        padded = self.listed([{"holding": True, "source": "s", "text": "t" * render.NEEDS_LIMIT_BYTES, "clears": "c"}])
        self.write(padded)
        self.assertGreater((self.state / render.NEEDS_FILE).stat().st_size, render.NEEDS_LIMIT_BYTES)
        self.assertEqual(render.read_needs_block(self.state), {"unreadable": True})
        padded["items"][0]["text"] = "t"
        self.write(padded)
        self.assertEqual(render.read_needs_block(self.state)["items"][0]["text"], "t")
        for bad in ({"holding": True, "source": "s", "text": "t"}, {"holding": 1, "source": "s", "text": "t", "clears": "c"}):
            with self.subTest(bad=bad):
                self.write(self.listed([bad]))
                self.assertEqual(render.read_needs_block(self.state), {"unreadable": True})

class LeftOpenTests(unittest.TestCase):
    """What a closed delivery left open, from its record and in the table's words: each stopped piece, the base when its
    last word is a break, and each source's report. An open delivery, or a closed one that left nothing open, has none."""

    def record(self, *kinds: str) -> dict:
        journal = [{"id": n, "at": f"2026-01-01T00:0{n}:00Z", "kind": kind, "lane": None, "subject": None, "data": {}}
                   for n, kind in enumerate(kinds, 1)]
        return {"state": {"meta": {}, "lanes": [], "planned": [], "queue": [
            {"subject": "APP-1", "lane": "lane-1", "stage": "stopped", "checks": [{"id": "e2e", "title": "end to end", "state": "failed"}]},
            {"subject": "APP-2", "lane": "lane-1", "stage": "merged", "checks": []}]}, "journal": journal}

    def test_a_closed_delivery_names_what_it_left_open_in_the_tables_words(self) -> None:
        document = self.record("delivery.closed")
        document["state"]["meta"]["sourceReports"] = [{"subject": "APP-3", "note": "its ticket moved"}]
        self.assertEqual(render.left_open(document), {"heading": "Left open at close", "rows": [
            {"kind": "stopped", "subject": "APP-1", "lane": "lane-1", "state": "stopped",
             "said": {"headline": "APP-1 was stopped", "why": "end to end failed"}},
            {"kind": "report", "said": {"headline": "A report for the lead on APP-3", "why": "its ticket moved"}}]})
        broken = self.record("base.broken", "delivery.closed")
        broken["journal"][0]["data"] = {"source": "queue", "detail": "Main is red"}
        self.assertEqual([row["said"]["headline"] for row in render.left_open(broken)["rows"]],
                         ["APP-1 was stopped", "The main line is broken"])
        self.assertEqual([row["kind"] for row in render.left_open(self.record("base.broken", "base.repaired", "delivery.closed"))["rows"]],
                         ["stopped"])

    def test_an_open_or_reopened_delivery_and_one_that_left_nothing_open_have_none(self) -> None:
        self.assertIsNone(render.left_open(self.record()))
        for reopening in ("lane.assigned", "candidate.handed", "check.started"):
            self.assertIsNone(render.left_open(self.record("delivery.closed", reopening)), reopening)
        quiet = self.record("delivery.closed")
        quiet["state"]["queue"] = []
        self.assertIsNone(render.left_open(quiet))
        self.assertIsNone(render.left_open({"journal": [{"kind": "delivery.closed"}]}))


STORIES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "delivery-views" / "story"
NARRATED = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "narrator"
# The narrator's journals whose stories the page's tests draw, each kept beside them as the narrator tells it.
# Each names only invented pieces, so the corpus keeps no ticket id.
STORY_NAMES = ("standing-break", "thread-at-story", "bookkeeping-run", "batch-of-three", "decision-on-a-break", "break-stops-a-piece")


def narrated(name: str) -> bytes:
    """A named journal's story, as the narrator tells it, in the bytes its fixture holds."""
    folder = NARRATED / name
    journal = [json.loads(line) for line in (folder / "journal.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    state = json.loads((folder / "state.json").read_text(encoding="utf-8")) if (folder / "state.json").is_file() else None
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8")) if (folder / "plan.json").is_file() else None
    story = render.narrator.narrate(journal, state=state, plan=plan)
    return (json.dumps(story, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


class IntegrationDriftTests(unittest.TestCase):
    """How far the target moved while the delivery worked, and how far the integration's current head is behind it,
    counted at render time against the remote-tracking ref, and absent whenever git can't say."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(tempfile.mkdtemp(prefix="integration-drift-"))
        cls.addClassCleanup(lambda: __import__("shutil").rmtree(cls.folder, ignore_errors=True))
        origin, cls.repo, other = cls.folder / "origin.git", cls.folder / "lead", cls.folder / "other"
        git = lambda where, *argv: subprocess.run(["git", *argv], cwd=where, check=True, capture_output=True,
                                                  text=True).stdout.strip()
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        quieten(origin)
        cls.folder.joinpath("seed").mkdir()
        seed = cls.folder / "seed"
        git(seed, "init", "-q", "-b", "main")
        quieten(seed)

        def change(where: Path, name: str) -> str:
            (where / name).write_text(name + "\n")
            git(where, "add", name)
            git(where, "-c", "user.name=F", "-c", "user.email=f@example.test", "commit", "-q", "-m", name)
            return git(where, "rev-parse", "HEAD")
        cls.started = change(seed, "start")
        git(seed, "push", "-q", str(origin), "main")
        quiet_clone(origin, cls.repo)
        quiet_clone(origin, other)
        # The integration base works beside the target, which moves twice; the lead catches up, then fixes.
        git(cls.repo, "checkout", "-q", "-b", "integrate/app")
        change(cls.repo, "story")
        change(other, "target-1")
        change(other, "target-2")
        git(other, "push", "-q", "origin", "main")
        git(cls.repo, "fetch", "-q", "origin")
        git(cls.repo, "-c", "user.name=F", "-c", "user.email=f@example.test", "merge", "-q", "--no-ff", "-m", "catch up",
            "origin/main")
        cls.merge = git(cls.repo, "rev-parse", "HEAD")
        cls.fix = change(cls.repo, "fix")
        # The target moves again after the catch-up; the lead's local main stays where it started.
        change(other, "target-3")
        git(other, "push", "-q", "origin", "main")
        git(cls.repo, "fetch", "-q", "origin")
        assert git(cls.repo, "rev-parse", "main") == cls.started

    def document(self, *facts: tuple[str, dict], target: str = "main", base_head: str | None = None) -> dict:
        journal = [{"id": 1, "at": "2026-11-18T10:00:00Z", "kind": "record.started", "lane": None, "subject": None,
                    "data": {"base": {"ref": "integrate/app", "head": self.started}}}]
        for number, (kind, data) in enumerate(facts, start=2):
            journal.append({"id": number, "at": "2026-11-18T10:00:00Z", "kind": kind, "lane": None, "subject": "APP",
                            "data": data})
        return {"state": {"meta": {"headline": "App", "trackerParent": "APP", "baseHead": base_head or self.merge,
                                   "integration": {"base": "integrate/app", "target": target, "remote": "origin"}},
                          "queue": [], "planned": []}, "journal": journal}

    def drift(self, document: dict):
        data = render.line_map_data(document, {}, config(), theme_key="local", history=None, repository=self.repo)
        return data.get("integration", {}).get("drift")

    def test_the_counts_are_against_the_remote_target_from_the_start_and_from_the_current_head(self) -> None:
        merged = ("integration.merged", {"target": "main", "targetHead": "a" * 40, "head": self.merge, "conflicts": []})
        suite = ("integration.suite", {"head": self.fix, "result": "passed", "seconds": 3})
        # Three target commits since the start; one since the fix, the newest head a fact names; none on local main.
        self.assertEqual(self.drift(self.document(merged, suite)), {"moved": 3, "behind": 1})
        # A round under the delivery's key names a head too, and a landing names none: the round's is the current head.
        landed = ("integration.landed", {"target": "main", "head": self.merge})
        self.assertEqual(self.drift(self.document(suite, ("review.round", {"round": 1, "head": self.started,
                                                                            "startedAt": "2026-11-18T10:00:00Z",
                                                                            "finishedAt": "2026-11-18T10:00:00Z",
                                                                            "verdicts": {}, "findings": {}}), landed)),
                         {"moved": 3, "behind": 3})

    def test_the_page_embeds_the_drift_the_renderer_counted_and_no_block_without_it(self) -> None:
        document = self.document(("integration.merged", {"target": "main", "targetHead": "a" * 40, "head": self.merge,
                                                         "conflicts": []}))
        with mock.patch.object(render, "integration_drift", return_value={"moved": 3, "behind": 1}) as counted:
            page = render.render_line_map(document, {}, config(), theme_key="local", history=None)
        self.assertEqual(block(page, "delivery-integration"), {"drift": {"moved": 3, "behind": 1}})
        self.assertEqual(counted.call_count, 1)
        with mock.patch.object(render, "integration_drift", return_value=None):
            page = render.render_line_map(document, {}, config(), theme_key="local", history=None)
        self.assertIsNone(block(page, "delivery-integration"))

    def test_with_no_integration_fact_behind_counts_from_the_base_head(self) -> None:
        self.assertEqual(self.drift(self.document(base_head=self.merge)), {"moved": 3, "behind": 1})
        self.assertEqual(self.drift(self.document(base_head=self.started)), {"moved": 3, "behind": 3})

    def test_a_missing_ref_a_git_that_times_out_or_is_absent_and_no_integration_leave_it_absent(self) -> None:
        self.assertIsNone(self.drift(self.document(target="nowhere")))
        real = subprocess.run
        calls = []

        def slow(argv, **kwargs):
            calls.append(kwargs.get("timeout"))
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        with mock.patch.object(render.subprocess, "run", side_effect=slow):
            self.assertIsNone(self.drift(self.document()))
        self.assertEqual(calls, [5])
        with mock.patch.object(render.subprocess, "run", side_effect=FileNotFoundError("git")):
            self.assertIsNone(self.drift(self.document()))
        plain = self.document()
        del plain["state"]["meta"]["integration"]
        with mock.patch.object(render.subprocess, "run", side_effect=AssertionError("no git call")):
            data = render.line_map_data(plain, {}, config(), theme_key="local", history=None, repository=self.repo)
        self.assertNotIn("integration", data)
        self.assertIs(subprocess.run, real)


class StoryFixtureTests(unittest.TestCase):
    """The stories the page's tests draw are the narrator's, byte for byte, for named journals of its own."""

    def test_each_story_the_page_draws_is_the_narrators_for_its_journal(self) -> None:
        self.assertEqual(sorted(path.name for path in STORIES.glob("*.json")), sorted(f"{name}.json" for name in STORY_NAMES))
        for name in STORY_NAMES:
            with self.subTest(name=name):
                self.assertEqual((STORIES / f"{name}.json").read_bytes(), narrated(name),
                                 f"tests/fixtures/delivery-views/story/{name}.json is not the narrator's story; write it again from its journal")


if __name__ == "__main__":
    unittest.main()


# The runs page, read from the runtime's captured output (tests/fixtures/run-events/runs): each run's own runs show and runs view, changed only in
# its id and the hour it ran (tests/fixtures/delivery-views/runs/build.py).
RUNS_CASE = Path(__file__).resolve().parent / "fixtures" / "delivery-views" / "runs"
CAPTURED = json.loads((RUNS_CASE / "runs-commands.json").read_text(encoding="utf-8"))
ABSENT = {"spec": "delivery-state-absent/1"}
INSIDE_PLAN, AGENT = "run-open-execute-inside-plan-1", "run-open-waiting-on-an-agent-1"
REVIEW_AGAIN, EXECUTE_AGAIN = "run-open-review-entered-again-1", "run-open-execute-entered-again-1"
ENDED, LEFT_OPEN, PARTIAL = "run-ended-plan-then-execute-1", "run-displaced-then-partial-1", "run-displaced-then-partial-2"


class RunsPageTests(unittest.TestCase):
    """What `render.py runs` reads of every listed run, and the pages each view prints without a record."""

    # Its unavailable case waits on a read held past the render's deadline: bounded, but a real process.
    run_quietly = True

    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = tempfile.TemporaryDirectory()
        cls.root = Path(cls.folder.name)
        cls.runs = Runs(cls.root)
        cls.runs.answer(CAPTURED)
        document = {"state": {"queue": [{"subject": "V26-0001", "runId": ENDED}],
                              "planned": [{"subject": "PAY-101", "runId": AGENT}]}}
        cls.page = render.collect_run_page(document, config(cls.runs), cls.root, cls.root / "cache.json",
                                           time.monotonic() + FAR)
        cls.by_id = {entry["runId"]: entry for entry in cls.page["runs"]}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.folder.cleanup()

    def test_the_fixture_is_the_captured_runtime_output_changed_only_in_ids_and_times(self) -> None:
        built = subprocess.run([sys.executable, "-B", str(RUNS_CASE / "build.py"), "--check"], capture_output=True)
        self.assertEqual(built.returncode, 0, "runs-commands.json differs from a rebuild of the captured runs")

    def test_the_payload_holds_every_captured_run_with_what_each_shows(self) -> None:
        self.assertEqual([entry["runId"] for entry in self.page["runs"]],
                         [INSIDE_PLAN, EXECUTE_AGAIN, REVIEW_AGAIN, AGENT, ENDED, LEFT_OPEN, PARTIAL])
        self.assertEqual(self.page["notes"], [])
        ended = self.by_id[ENDED]
        self.assertEqual((ended["section"], ended["stages"], ended["currentStage"], ended["waits"], ended["needsYou"]),
                         ("ended", ["plan", "execute", "review"], None, [], False))
        self.assertEqual(ended["gates"], [
            {"command": "scripts/validate-registry.py", "outcome": "pass", "durationMs": 4200},
            {"command": "scripts/run-tests.py", "outcome": "fail", "durationMs": 615000}])
        self.assertEqual(ended["decisions"], [{"fork": "the branch name", "choice": "claude/V26-0001, the repository's convention",
                                               "text": None, "why": None}])
        self.assertEqual(ended["blockers"], [{"code": "review.deferral-untracked",
                                              "summary": "One deferral has no tracked follow-up."}])
        self.assertEqual(ended["changeRequest"], "https://github.com/example/repository/pull/1")
        self.assertEqual(ended["result"], {"result": "complete", "unit": "subagent-tokens", "total": 152352, "coverage": "partial"})
        self.assertEqual([round_["round"] for round_ in ended["summary"]["roundDetail"]], [1])
        self.assertEqual((ended["ticket"], ended["piece"]), ("V26-0001", "V26-0001"))
        agent = self.by_id[AGENT]
        self.assertEqual(agent["piece"], "PAY-101")
        self.assertEqual((agent["stages"], agent["currentStage"], agent["activity"]["nextStep"]),
                         (["diagnose"], "diagnose", "Trace the failing assertion."))
        self.assertEqual(agent["waits"], [{"waitId": "reproduction", "waitingOn": "agent", "owner": "claude-code",
                                           "reason": "A subagent is reproducing the failure.",
                                           "nextAction": "Compare its observation with the prediction.",
                                           "since": "2026-11-20T05:00:00Z", "needsYou": False}])
        partial = self.by_id[PARTIAL]
        self.assertEqual((partial["section"], partial["result"]["result"], partial["displaces"]), ("ended", "partial", LEFT_OPEN))
        for entry in self.page["runs"]:
            self.assertNotIn("unavailable", entry)

    def test_the_open_execute_run_inside_plan_draws_three_stages_and_no_verify_or_lens(self) -> None:
        run = self.by_id[INSIDE_PLAN]
        # Its runtime prepare and verify phases and its lens attempt with phase review add no stage.
        self.assertEqual((run["stages"], run["currentStage"]), (["plan", "execute", "review"], "execute"))
        self.assertEqual(run["activity"], {"activityId": "execute-slice-2", "phase": "execute", "state": "running",
                                           "nextStep": "Fix the round's finding.", "at": "2026-11-20T08:00:02Z"})
        self.assertEqual([gate["command"] for gate in run["gates"]], ["scripts/validate-registry.py"])
        self.assertEqual([(wait["waitId"], wait["needsYou"]) for wait in run["waits"]], [("push-approval", True)])
        self.assertTrue(run["needsYou"])

    def test_a_stage_entered_again_is_current_and_still_drawn_once_in_first_entry_order(self) -> None:
        self.assertEqual((self.by_id[REVIEW_AGAIN]["stages"], self.by_id[REVIEW_AGAIN]["currentStage"]),
                         (["plan", "execute", "review"], "review"))
        self.assertEqual((self.by_id[EXECUTE_AGAIN]["stages"], self.by_id[EXECUTE_AGAIN]["currentStage"]),
                         (["plan", "execute", "review"], "execute"))

    def test_only_the_unresolved_human_wait_needs_you(self) -> None:
        # The ended run's human wait was resolved, and the agent wait is no one's to answer here.
        self.assertEqual([entry["runId"] for entry in self.page["runs"] if entry["needsYou"]], [INSIDE_PLAN])

    def test_a_displaced_run_is_listed_with_the_ended_runs_never_under_now(self) -> None:
        left = self.by_id[LEFT_OPEN]
        self.assertEqual((left["open"], left["section"], left["displacedBy"]), (True, "ended", PARTIAL))
        self.assertEqual([entry["runId"] for entry in self.page["runs"] if entry["section"] == "now"],
                         [INSIDE_PLAN, EXECUTE_AGAIN, REVIEW_AGAIN, AGENT])

    def test_a_run_whose_view_fails_or_passes_the_deadline_is_unavailable_and_the_others_render(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            runs = Runs(Path(folder))
            runs.answer({**CAPTURED, f"runs view {AGENT} --json": None, f"runs show {PARTIAL} --json": None,
                         f"runs view {REVIEW_AGAIN} --json": {"sleep": 30, "then": CAPTURED[f"runs view {REVIEW_AGAIN} --json"]}})
            page = render.collect_run_page(ABSENT, config(runs), Path(folder), Path(folder) / "cache.json", time.monotonic() + 5)
        shown = {entry["runId"]: entry for entry in page["runs"]}
        self.assertTrue(shown[AGENT]["unavailable"] and shown[REVIEW_AGAIN]["unavailable"] and shown[PARTIAL]["unavailable"])
        self.assertEqual((shown[AGENT]["section"], shown[REVIEW_AGAIN]["open"]), ("now", True))
        self.assertEqual(sorted(run_id for run_id, entry in shown.items() if not entry.get("unavailable")),
                         sorted([INSIDE_PLAN, EXECUTE_AGAIN, ENDED, LEFT_OPEN]))

    def test_a_run_that_grew_is_read_again_and_an_unchanged_one_comes_from_the_cache(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runs = Runs(root)
            runs.answer(CAPTURED)
            # The runs page's own cache, beside the line map's, which holds another shape.
            cache = views_common.runs_cache_path(root / "state").with_name("runs-page.json")
            render.collect_run_page(ABSENT, config(runs), root, cache, time.monotonic() + FAR)
            show = json.loads(CAPTURED[f"runs show {INSIDE_PLAN} --json"])
            show["events"].append({"seq": 17, "kind": "gate.reported", "at": "2026-11-20T08:00:03Z",
                                   "payload": {"command": "scripts/run-tests.py", "outcome": "pass", "durationMs": 2500}})
            listed = json.loads(CAPTURED["runs list --json"])
            for row in listed["runs"]:
                row["bytes"] += 200 if row["runId"] == INSIDE_PLAN else 0
            runs.answer({**CAPTURED, "runs list --json": json.dumps(listed), f"runs show {INSIDE_PLAN} --json": json.dumps(show)})
            again = {entry["runId"]: entry for entry in
                     render.collect_run_page(ABSENT, config(runs), root, cache, time.monotonic() + FAR)["runs"]}
            asked = runs.calls()
        self.assertEqual([gate["command"] for gate in again[INSIDE_PLAN]["gates"]],
                         ["scripts/validate-registry.py", "scripts/run-tests.py"])
        self.assertEqual(sorted(call for call in asked if not call.startswith("runs list")),
                         sorted([f"runs show {INSIDE_PLAN} --json", f"runs view {INSIDE_PLAN} --json"]))
        self.assertEqual(again[ENDED]["gates"], self.by_id[ENDED]["gates"])
        self.assertFalse(views_common.runs_cache_path(root / "state").exists())

    def test_over_the_budget_the_oldest_ended_runs_give_up_their_details_first(self) -> None:
        size = len(render.embed(self.page).encode("utf-8"))
        partial = len(render.embed(self.by_id[PARTIAL]).encode("utf-8"))
        # Room for all but part of the oldest ended run: it alone is cut.
        cut = render.run_page_within_budget(self.page, size - partial // 2)
        clipped = [entry["runId"] for entry in cut["runs"] if entry.get("clipped")]
        self.assertEqual(clipped, [PARTIAL])
        self.assertLessEqual(len(render.embed(cut).encode("utf-8")), size - partial // 2)
        everything = render.run_page_within_budget(self.page, 1)
        self.assertTrue(all(entry.get("clipped") for entry in everything["runs"]))
        self.assertEqual(render.run_page_within_budget(self.page), self.page)

    def test_each_view_renders_from_the_absent_document(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            runs = Runs(Path(folder))
            runs.answer(CAPTURED)
            renderers = [{"id": name, "argv": ["python3", "-B", "render.py", name], "output": output, "timeoutSeconds": 30}
                         for name, output in (("line-map", "line-map.html"), ("history", "history.html"), ("runs", "runs.html"))]
            repository = Repository(Path(folder), {"spec": views_common.CONFIG_SPEC,
                                                   "runs": {"argv": runs.argv, "timeoutSeconds": 10}}, renderers)
            pages = {}
            for name in ("runs", "line-map", "history"):
                done = subprocess.run([sys.executable, "-B", str(render.__file__), name, "--config", ".agents/delivery-views.json",
                                       "--binding", ".agents/orchestration.json"], cwd=repository.root,
                                      input=json.dumps(ABSENT).encode("utf-8"), capture_output=True, timeout=120)
                self.assertEqual(done.returncode, 0, done.stderr)
                pages[name] = done.stdout.decode("utf-8")
            cached = sorted(path.name for path in (Path(folder) / "state" / ".cache" / "delivery-views").iterdir())
        # The runs page keeps its own cache: the line map's holds another shape.
        self.assertEqual(cached, ["runs-page.json"])
        listed = [entry["runId"] for entry in block(pages["runs"], "delivery-run-page")["runs"]]
        self.assertEqual(sorted(listed), sorted(self.by_id))
        for name in ("line-map", "history"):
            with self.subTest(view=name):
                self.assertIn("No delivery is under way.", pages[name])
                self.assertIn('href="runs.html"', pages[name])
                self.assertIn('<meta name="delivery-state-live">', pages[name])
                self.assertIsNone(block(pages[name], "delivery-state"))

    def test_a_lens_attempt_or_a_runtime_phase_never_reopens_a_stage(self) -> None:
        def observed(seq, activity, phase, state):
            return {"seq": seq, "kind": "activity.observed", "at": f"2026-11-20T09:00:0{seq}Z",
                    "payload": {"activityId": activity, "attemptId": f"{activity}-1", "phase": phase, "state": state}}
        exported = {"spec": "delivery-run-export/2", "runId": "run-1", "summary": {}, "readout": {}, "events": [
            observed(1, "stage-execute", "execute", "running"), observed(2, "stage-review", "review", "running"),
            observed(3, "stage-review", "review", "completed"),
            # After review exits: a lens attempt and a runtime verify, neither a stage.
            observed(4, "review-lens-outcome", "review", "running"), observed(5, "command-0002", "verify", "running")]}
        shown = render.run_of_events(exported)
        self.assertEqual((shown["stages"], shown["currentStage"]), (["execute", "review"], "execute"))
        self.assertEqual(shown["activity"]["activityId"], "command-0002")
        # Once the verify and the lens end, the run is doing what it did before them: execute, and its next step.
        events = exported["events"]
        events[0]["payload"]["nextStep"] = "Build it."
        events.extend([observed(6, "command-0002", "verify", "completed"), observed(7, "review-lens-outcome", "review", "completed")])
        self.assertEqual(render.run_of_events(exported)["activity"],
                         {"activityId": "stage-execute", "phase": "execute", "state": "running", "nextStep": "Build it.",
                          "at": "2026-11-20T09:00:01Z"})


class LiveRunsPageTests(unittest.TestCase):
    """The real runs renderer as the live server's liveRuns page, with no record."""

    run_quietly = True

    def test_live_over_an_empty_state_directory_answers_with_the_inventorys_runs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runs = Runs(root)
            runs.answer(CAPTURED)
            argv = [sys.executable, "-B", str(render.__file__)]
            inputs = ["--config", ".agents/delivery-views.json", "--binding", ".agents/orchestration.json"]
            renderers = [{"id": "line-map", "argv": [*argv, "line-map", *inputs], "output": "line-map.html", "timeoutSeconds": 60},
                         {"id": "runs", "argv": [*argv, "runs", *inputs], "output": "runs.html", "timeoutSeconds": 60}]
            repository = Repository(root, {"spec": views_common.CONFIG_SPEC, "runs": {"argv": runs.argv, "timeoutSeconds": 10}},
                                    renderers)
            binding = json.loads((root / ".agents" / "orchestration.json").read_text(encoding="utf-8"))
            binding["state"]["liveHome"] = "line-map"
            binding["state"]["liveRuns"] = {"renderer": "runs", "inventory": {"argv": [*runs.argv, "runs"]}}
            (root / ".agents" / "orchestration.json").write_text(json.dumps(binding), encoding="utf-8")
            (root / "state").mkdir()
            wrapper = Path(render.__file__).resolve().parents[2] / "orchestrate-work" / "scripts" / "render_state.py"
            live = subprocess.Popen([sys.executable, "-B", str(wrapper), "live", "--dir", "state", "--binding", ".agents/orchestration.json"],
                                    cwd=repository.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                address = json.loads(live.stdout.readline())["live"]
                deadline = time.monotonic() + 60
                status, page = None, ""
                while time.monotonic() < deadline:
                    try:
                        with urllib.request.urlopen(f"http://{address}/", timeout=10) as answer:
                            status, page = answer.status, answer.read().decode("utf-8")
                            break
                    except urllib.error.HTTPError:
                        time.sleep(0.2)
                self.assertEqual(status, 200)
                shown = block(page, "delivery-run-page")
                listed = [row["runId"] for row in json.loads(CAPTURED["runs list --json"])["runs"]]
                self.assertEqual(sorted(entry["runId"] for entry in shown["runs"]), sorted(listed))
                # The fixture's store is not on this machine, so the server says the runs aren't watched.
                self.assertEqual(block(page, "delivery-run-page-view")["watched"], "unwatched")
                self.assertIn("onRuns", page)
            finally:
                # Stopped by the PID this test recorded.
                live.terminate()
                live.wait(10)
                for stream in (live.stdout, live.stderr):
                    stream.close()
