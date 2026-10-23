from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.delivery_views_fixtures import RENDER, Repository, Runs, block, config, render, views_common


FAR = 10_000
TRACKER = {"name": "Tracker", "issueUrl": "https://tracker.example/issue/{key}", "keyPattern": "^ABC-[0-9]+$"}


def run_export(run_id: str, ticket: str, last_at: str, *, rounds: int = 1, open_: bool = False) -> dict:
    return {
        "spec": "delivery-run-export/2",
        "runId": run_id,
        "events": [{"kind": "run.started", "at": "2026-10-20T09:00:00Z"}] + [
            {"kind": "review.round.closed", "at": last_at,
             "payload": {"round": number, "outcome": "aligned", "findings": {"P1": 1}}} for number in range(1, rounds + 1)],
        "summary": {"ticket": ticket, "open": open_, "startedAt": "2026-10-20T09:00:00Z", "lastAt": last_at,
                    "durationSeconds": 5400, "roundsClosed": rounds, "findings": {"P0": 0, "P1": 1, "P2": 0, "P3": 2}},
        "readout": {"status": "incomplete" if open_ else "complete"},
    }


def legacy(branch: str) -> dict:
    return {"schemaVersion": 1, "generatedAt": "2026-08-13T17:38:52.076Z", "branch": branch, "status": "pass",
            "reviewLoop": {"iterationCount": 5, "findingCounts": {"P0": 0, "P1": 1, "P2": 2, "P3": 3}}}


def inventory(*rows: dict) -> dict:
    return {"spec": "run-inventory/1", "runs": list(rows)}


def listed(run_id: str, size: int = 100) -> dict:
    return {"runId": run_id, "status": "complete", "open": False, "durationSeconds": 60, "bytes": size}


class ExportSummaryTests(unittest.TestCase):
    def test_reads_the_ticket_state_timing_rounds_and_findings_of_a_run_export(self) -> None:
        summary = render.summarize_run_export(run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z", open_=True))
        self.assertEqual(summary, {
            "runId": "run-a", "ticket": "ABC-1", "open": True, "status": "incomplete", "startedAt": "2026-10-20T09:00:00Z",
            "lastAt": "2026-10-20T10:30:00Z", "durationSeconds": 5400, "rounds": 1,
            "findings": {"P0": 0, "P1": 1, "P2": 0, "P3": 2},
            "roundDetail": [{"round": 1, "at": "2026-10-20T10:30:00Z", "outcome": "approve",
                             "findings": {"P0": 0, "P1": 1, "P2": 0, "P3": 0}}]})

    def test_keeps_the_latest_rounds_however_many_closed(self) -> None:
        detail = render.summarize_run_export(run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z", rounds=20))["roundDetail"]
        self.assertEqual([entry["round"] for entry in detail], list(range(9, 21)))

    def test_anything_that_is_not_a_run_export_has_no_summary(self) -> None:
        for value in (None, [], {"spec": "run-view/1"}, {"spec": 7}, legacy("codex/abc-1-x")):
            self.assertIsNone(render.summarize_run_export(value))

    def test_an_older_telemetry_record_takes_its_ticket_from_its_branch_by_the_trackers_key_pattern(self) -> None:
        self.assertEqual(render._legacy_summary(legacy("codex/abc-12-review-scope"), TRACKER)["ticket"], "ABC-12")
        self.assertIsNone(render._legacy_summary(legacy("claude/realign-finish-line"), TRACKER)["ticket"])
        self.assertIsNone(render._legacy_summary(legacy("codex/abc-12-review-scope"), None)["ticket"])


class CollectHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.runs = Runs(self.root)
        self.cache = self.root / "state" / ".cache" / "delivery-views" / "history.json"
        self.exports = self.root / "exports"
        self.exports.mkdir()

    def tearDown(self) -> None:
        self.folder.cleanup()

    def write(self, name: str, value: object) -> None:
        (self.exports / name).write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")

    def collect(self, document=None, *, deadline=FAR, runs=True, exports=True, ignore=("delivery-record--*",)):
        overrides = {"tracker": TRACKER}
        if exports:
            overrides["exports"] = {"directory": "exports", "ignore": list(ignore)}
        chosen = config(self.runs if runs else None, **overrides)
        return render.collect_history(document or {}, chosen, self.root, self.cache, time.monotonic() + deadline,
                                      "2026-10-22T12:00:00Z")

    def answer(self, rows: list[dict], shows: dict) -> None:
        table = {"runs list --json": inventory(*rows)}
        table.update({f"runs show {run_id} --json": value for run_id, value in shows.items()})
        self.runs.answer(table)

    def test_lists_each_run_and_export_once_newest_first_skipping_the_ignored_patterns(self) -> None:
        self.answer([listed("run-a"), listed("run-b")], {
            "run-a": run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z"),
            "run-b": run_export("run-b", "ABC-2", "2026-10-21T10:30:00Z")})
        self.write("run-b.json", run_export("run-b", "ABC-STALE", "2026-10-21T09:10:00Z"))
        self.write("run-c.json", run_export("run-c", "ABC-3", "2026-10-19T10:00:00Z"))
        self.write("2026-08-13-codex-abc-9-x.json", legacy("codex/abc-9-x"))
        self.write("delivery-record--0c21.json", {"gateId": "gate"})
        self.write("notes.txt", "not json")
        history = self.collect()
        self.assertEqual([(row["key"], row["source"]) for row in history["rows"]], [
            ("run-b", "product"), ("run-a", "product"), ("run-c", "export"), ("2026-08-13-codex-abc-9-x.json", "telemetry")])
        self.assertEqual(history["rows"][0]["summary"]["ticket"], "ABC-2")
        self.assertEqual(history["notes"], [])

    def test_an_ignore_pattern_is_a_whole_name_pattern(self) -> None:
        self.write("run-a.json", run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z"))
        self.write("run-b.json", run_export("run-b", "ABC-2", "2026-10-20T10:30:00Z"))
        self.assertEqual([row["key"] for row in self.collect(runs=False, ignore=["run-a*"])["rows"]], ["run-b"])
        self.assertEqual(len(self.collect(runs=False, ignore=["run-"])["rows"]), 2)

    def test_names_the_delivery_the_record_gives_a_run(self) -> None:
        self.answer([listed("run-a"), listed("run-b")], {
            "run-a": run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z"),
            "run-b": run_export("run-b", "ABC-2", "2026-10-21T10:30:00Z")})
        document = {"state": {"meta": {"headline": "Epic", "trackerParent": "ABC-0"},
                              "queue": [{"subject": "ABC-1", "runId": "run-a"}]}}
        rows = {row["key"]: row for row in self.collect(document)["rows"]}
        self.assertEqual(rows["run-a"]["delivery"], {"headline": "Epic", "parent": "ABC-0", "subject": "ABC-1"})
        self.assertIsNone(rows["run-b"]["delivery"])

    def test_without_exports_configured_no_directory_is_read(self) -> None:
        self.answer([], {})
        self.write("run-c.json", run_export("run-c", "ABC-3", "2026-10-19T10:00:00Z"))
        self.assertEqual(self.collect(exports=False)["rows"], [])

    def test_without_runs_configured_no_command_runs_and_the_exports_are_listed(self) -> None:
        self.answer([listed("run-a")], {"run-a": run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z")})
        self.write("run-c.json", run_export("run-c", "ABC-3", "2026-10-19T10:00:00Z"))
        history = self.collect(runs=False)
        self.assertEqual([row["key"] for row in history["rows"]], ["run-c"])
        self.assertEqual(history["notes"], [])
        self.assertEqual(self.runs.calls(), [])

    def test_an_unreadable_run_list_is_one_note_and_the_exports_are_still_listed(self) -> None:
        self.runs.answer({"runs list --json": None})
        self.write("run-c.json", run_export("run-c", "ABC-3", "2026-10-19T10:00:00Z"))
        history = self.collect()
        self.assertEqual([row["key"] for row in history["rows"]], ["run-c"])
        self.assertEqual(history["notes"], ["The product's run list could not be read; only the tracked exports are listed."])

    def test_an_unchanged_run_is_not_read_again_and_a_moved_one_is(self) -> None:
        self.answer([listed("run-a")], {"run-a": run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z")})
        first = self.collect()
        self.answer([listed("run-a")], {})
        self.assertEqual(self.collect(), first)
        self.assertEqual(self.runs.calls(), ["runs list --json"])
        self.answer([listed("run-a", 200)], {"run-a": run_export("run-a", "ABC-1", "2026-10-21T10:30:00Z")})
        self.assertEqual(self.collect()["rows"][0]["summary"]["lastAt"], "2026-10-21T10:30:00Z")
        self.assertEqual(self.runs.calls(), ["runs list --json", "runs show run-a --json"])

    def test_a_run_past_the_deadline_is_noted_unread_and_asked_for_again(self) -> None:
        self.answer([listed("run-a")], {"run-a": run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z")})
        history = self.collect(deadline=0)
        self.assertEqual(history["notes"], ["The product's run list could not be read; only the tracked exports are listed."])
        self.answer([listed("run-a")], {"run-a": run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z")})
        self.assertEqual(self.collect()["rows"][0]["summary"]["ticket"], "ABC-1")
        self.assertEqual(self.runs.calls(), ["runs list --json", "runs show run-a --json"])

    def test_a_run_whose_show_fails_is_listed_with_a_note_and_not_cached(self) -> None:
        self.answer([listed("run-x")], {"run-x": None})
        [row] = self.collect()["rows"]
        self.assertEqual(row["note"], "Its run could not be read from the product.")
        self.assertEqual(views_common.read_runs_cache(self.cache), {})


class HistoryBudgetTests(unittest.TestCase):
    def test_the_oldest_rows_give_up_their_summaries_first(self) -> None:
        rows = [{"key": f"run-{index}", "source": "product", "file": None, "label": None, "note": None, "delivery": None,
                 "summary": render.summarize_run_export(run_export(f"run-{index}", "ABC-1", "2026-10-20T10:30:00Z", rounds=12))}
                for index in range(4)]
        history = {"asOf": "2026-10-22T12:00:00Z", "rows": rows, "notes": []}
        full = len(render.embed(history).encode("utf-8"))
        kept = render.history_within_budget(history, budget=full - 10)
        self.assertEqual([row["note"] for row in kept["rows"]], [None, None, None, "Its details are over the page's size budget."])
        self.assertEqual(kept["rows"][3]["summary"]["roundDetail"], [])
        self.assertLessEqual(len(render.embed(kept).encode("utf-8")), full - 10)
        self.assertEqual(render.history_within_budget(history, budget=full), history)


class HistoryCommandTests(unittest.TestCase):
    RENDERERS = [
        {"id": "line-map", "argv": ["python3", "-B", "render.py", "line-map"], "output": "line-map.html", "timeoutSeconds": 30},
        {"id": "history", "argv": ["python3", "-B", "render.py", "history"], "output": "history.html", "timeoutSeconds": 60},
    ]

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self) -> None:
        self.folder.cleanup()

    def render(self, document: object, raw_config: dict, *extra: str, renderers=None) -> subprocess.CompletedProcess:
        Repository(self.root, raw_config, renderers if renderers is not None else self.RENDERERS)
        return subprocess.run([sys.executable, "-B", str(RENDER), "history", "--config", ".agents/delivery-views.json",
                               "--binding", ".agents/orchestration.json", *extra], cwd=self.root,
                              input=document if isinstance(document, bytes) else json.dumps(document).encode("utf-8"),
                              capture_output=True, timeout=120)

    def test_prints_a_live_page_with_the_history_and_its_view(self) -> None:
        (self.root / "exports").mkdir()
        (self.root / "exports" / "run-c.json").write_text(json.dumps(run_export("run-c", "ABC-3", "2026-10-19T10:00:00Z")))
        result = self.render({"state": {}}, {"spec": views_common.CONFIG_SPEC, "exports": {"directory": "exports"}},
                             "--now", "2026-10-22T12:00:00Z")
        self.assertEqual(result.returncode, 0, result.stderr)
        page = result.stdout.decode("utf-8")
        self.assertIn('<meta name="delivery-state-live">', page)
        history = block(page, "delivery-history")
        self.assertEqual((history["asOf"], [row["key"] for row in history["rows"]]), ("2026-10-22T12:00:00Z", ["run-c"]))
        self.assertEqual(block(page, "delivery-history-view")["lineMap"], "line-map.html")
        self.assertEqual(block(page, "delivery-history-view")["exports"], "exports")

    def test_names_no_line_map_when_the_binding_declares_none_and_takes_its_time_from_the_clock(self) -> None:
        result = self.render({}, {"spec": views_common.CONFIG_SPEC}, renderers=[self.RENDERERS[1]])
        self.assertEqual(result.returncode, 0, result.stderr)
        page = result.stdout.decode("utf-8")
        view = block(page, "delivery-history-view")
        self.assertEqual((view["lineMap"], view["exports"]), (None, None))
        self.assertRegex(block(page, "delivery-history")["asOf"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

    def test_a_document_that_is_not_json_exits_non_zero(self) -> None:
        result = self.render(b"{not json", {"spec": views_common.CONFIG_SPEC})
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"history: the delivery document on stdin is not JSON", result.stderr)

    def test_a_missing_config_exits_non_zero_naming_it(self) -> None:
        Repository(self.root, {"spec": views_common.CONFIG_SPEC}, self.RENDERERS)
        result = subprocess.run([sys.executable, "-B", str(RENDER), "history", "--config", ".agents/missing.json",
                                 "--binding", ".agents/orchestration.json"], cwd=self.root, input=b"{}",
                                capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"--config .agents/missing.json: not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
