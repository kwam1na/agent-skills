from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

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
            {"id": "r1-f1", "lens": "lens.adversarial-testing", "severity": "P1", "summary": "A retry after a timeout charges twice", "state": "resolved", "deferredIssue": None},
            {"id": "r1-f2", "lens": "lens.outcome-correctness", "severity": "P2", "summary": "The receipt keeps the first attempt's total", "state": "resolved", "deferredIssue": None},
            {"id": "r1-f3", "lens": "lens.adversarial-testing", "severity": "P2", "summary": "No test covers a dropped second attempt", "state": "unresolved", "deferredIssue": None},
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
        # Six at a time, two rounds of reads fit the deadline and the third does not.
        names = [f"run-{index}" for index in range(18)]
        listed = [{"runId": name, "bytes": 1} for name in names]
        self.runs.answer(table(listed, {name: {"sleep": 1, "then": VIEW} for name in names}))
        started = time.monotonic()
        runs = self.collect(document_naming(names), deadline=2.5)
        self.assertLess(time.monotonic() - started, 5)
        unavailable = [name for name in names if runs[name].get("unavailable")]
        self.assertTrue(unavailable and unavailable == names[-len(unavailable):], runs)
        self.assertTrue(set(json.loads(self.cache.read_text())["runs"]).isdisjoint(unavailable))
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
    ESHYFT = config(tracker={"name": "Jira", "issueUrl": "https://eshyft.atlassian.net/browse/{key}", "keyPattern": "^(SHFT|EDD)-[0-9]+$"},
                    forges=[{"name": "Bitbucket", "repository": "eshyft/eshyftnew",
                             "pullUrl": "https://bitbucket.org/{repository}/pull-requests/{number}",
                             "commitUrl": "https://bitbucket.org/{repository}/commits/{sha}"}])

    def test_jira_and_bitbucket_configuration_produce_their_addresses(self) -> None:
        document = {"state": {"meta": {"trackerParent": "SHFT-1"}, "queue": [
            {"subject": "SHFT-12", "changeRequest": "https://bitbucket.org/eshyft/eshyftnew/pull-requests/34"},
            {"subject": "EDD-3", "changeRequest": "#35"},
            {"subject": "OTHER-1", "changeRequest": "https://bitbucket.org/someone/else/pull-requests/1"}]}}
        sha = "a" * 40
        runs = {"run-1": {"roundDetail": [{"settled": [{"fixCommit": sha}, {"fixCommit": "javascript:alert(1)"}]}]}}
        links = render.page_links(document, runs, self.ESHYFT)
        self.assertEqual(links["issues"], {"SHFT-1": "https://eshyft.atlassian.net/browse/SHFT-1",
                                           "SHFT-12": "https://eshyft.atlassian.net/browse/SHFT-12",
                                           "EDD-3": "https://eshyft.atlassian.net/browse/EDD-3"})
        self.assertEqual(links["changes"], {
            "https://bitbucket.org/eshyft/eshyftnew/pull-requests/34": {"label": "#34", "href": "https://bitbucket.org/eshyft/eshyftnew/pull-requests/34"},
            "#35": {"label": "#35", "href": "https://bitbucket.org/eshyft/eshyftnew/pull-requests/35"}})
        self.assertEqual(links["commits"], {sha: f"https://bitbucket.org/eshyft/eshyftnew/commits/{sha}"})

    def test_with_no_tracker_or_forge_nothing_is_linked(self) -> None:
        document = {"state": {"queue": [{"subject": "SHFT-12", "changeRequest": "#34"}]}}
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


if __name__ == "__main__":
    unittest.main()
