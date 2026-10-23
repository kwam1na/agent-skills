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

    def test_a_lowercase_key_pattern_still_finds_the_key(self) -> None:
        lowercase = {**TRACKER, "keyPattern": "^abc-[0-9]+$"}
        self.assertEqual(render._legacy_summary(legacy("codex/abc-12-review-scope"), lowercase)["ticket"], "ABC-12")


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

    def test_without_exports_the_run_list_note_names_no_exports(self) -> None:
        self.runs.answer({"runs list --json": None})
        self.assertEqual(self.collect(exports=False)["notes"], ["The product's run list could not be read."])

    def test_an_unchanged_run_is_not_read_again_and_a_moved_one_is(self) -> None:
        self.answer([listed("run-a")], {"run-a": run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z")})
        first = self.collect()
        self.answer([listed("run-a")], {})
        self.assertEqual(self.collect(), first)
        self.assertEqual(self.runs.calls(), ["runs list --json"])
        self.answer([listed("run-a", 200)], {"run-a": run_export("run-a", "ABC-1", "2026-10-21T10:30:00Z")})
        self.assertEqual(self.collect()["rows"][0]["summary"]["lastAt"], "2026-10-21T10:30:00Z")
        self.assertEqual(self.runs.calls(), ["runs list --json", "runs show run-a --json"])

    def test_an_unreadable_run_list_keeps_the_cache_and_a_run_no_longer_listed_leaves_it(self) -> None:
        self.answer([listed("run-a"), listed("run-b")], {
            "run-a": run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z"),
            "run-b": run_export("run-b", "ABC-2", "2026-10-21T10:30:00Z")})
        self.collect()
        self.runs.answer({"runs list --json": None})
        self.collect()
        self.assertEqual(sorted(views_common.read_runs_cache(self.cache)), ["run-a", "run-b"])
        self.answer([listed("run-a")], {})
        self.assertEqual([row["key"] for row in self.collect()["rows"]], ["run-a"])
        self.assertEqual(self.runs.calls(), ["runs list --json"])
        self.assertEqual(sorted(views_common.read_runs_cache(self.cache)), ["run-a"])

    def test_shows_past_the_deadline_are_noted_and_not_cached(self) -> None:
        slow = {"sleep": 2, "then": run_export("run-b", "ABC-2", "2026-10-21T10:30:00Z")}
        self.answer([listed("run-a"), listed("run-b"), listed("run-c")], {
            "run-a": run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z"), "run-b": slow, "run-c": slow})
        rows = {row["key"]: row for row in self.collect(deadline=1)["rows"]}
        self.assertIsNone(rows["run-a"]["note"])
        for run_id in ("run-b", "run-c"):
            self.assertEqual(rows[run_id]["note"], "Its run could not be read from the product.")
        self.assertEqual(sorted(views_common.read_runs_cache(self.cache)), ["run-a"])

    def test_a_missing_export_directory_lists_nothing_and_fails_nothing(self) -> None:
        self.exports.rmdir()
        self.assertEqual(self.collect(runs=False)["rows"], [])

    def test_an_export_that_is_not_text_or_too_deep_is_noted_and_a_directory_is_not_an_export(self) -> None:
        (self.exports / "binary.json").write_bytes(b"\xff\xfe{}")
        self.write("deep.json", "[" * 100000)
        (self.exports / "d.json").mkdir()
        rows = {row["key"]: row["note"] for row in self.collect(runs=False)["rows"]}
        self.assertEqual(rows, {"binary.json": "This export could not be read.", "deep.json": "This export could not be read."})

    def test_an_unreadable_export_of_a_listed_run_leaves_the_products_row(self) -> None:
        self.answer([listed("run-a")], {"run-a": run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z")})
        self.write("run-a.json", "{ not json")
        [row] = self.collect()["rows"]
        self.assertEqual((row["key"], row["source"], row["note"]), ("run-a", "product", None))

    def test_a_run_export_without_a_run_id_takes_its_file_name(self) -> None:
        exported = run_export("run-z", "ABC-1", "2026-10-20T10:30:00Z")
        del exported["runId"]
        self.write("run-z.json", exported)
        [row] = self.collect(runs=False)["rows"]
        self.assertEqual((row["key"], row["summary"]["runId"]), ("run-z", "run-z"))

    def test_a_telemetry_record_without_a_branch_is_not_a_run_export(self) -> None:
        self.write("old.json", {"schemaVersion": 1, "generatedAt": "2026-08-13T17:38:52.076Z"})
        [row] = self.collect(runs=False)["rows"]
        self.assertEqual((row["source"], row["note"]), ("export", "This file is not a run export."))

    def test_tied_rows_are_ordered_by_key_in_code_point_order(self) -> None:
        for run_id in ("run-a", "run-B", "run_c"):
            self.write(f"{run_id}.json", run_export(run_id, "ABC-1", "2026-10-20T10:30:00Z"))
        self.assertEqual([row["key"] for row in self.collect(runs=False)["rows"]], ["run-B", "run-a", "run_c"])

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
        rows = [{"key": f"run-{index}", "source": "product", "file": None, "label": None, "note": None,
                 "delivery": {"headline": "Epic", "parent": None, "subject": "ABC-1"},
                 "summary": render.summarize_run_export(run_export(f"run-{index}", "ABC-1", "2026-10-20T10:30:00Z", rounds=12))}
                for index in range(4)]
        history = {"asOf": "2026-10-22T12:00:00Z", "rows": rows, "notes": []}
        full = len(render.embed(history).encode("utf-8"))
        kept = render.history_within_budget(history, budget=full - 10)
        self.assertEqual([row["note"] for row in kept["rows"]], [None, None, None, "Its details are over the page's size budget."])
        self.assertEqual(kept["rows"][3]["summary"]["roundDetail"], [])
        self.assertIsNone(kept["rows"][3]["delivery"])
        self.assertLessEqual(len(render.embed(kept).encode("utf-8")), full - 10)
        self.assertEqual(render.history_within_budget(history, budget=full), history)

    def test_a_row_with_an_oversized_label_still_keeps_the_page_within_the_budget(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "exports").mkdir()
        (root / "exports" / "big.json").write_text(json.dumps(legacy("codex/" + "x" * (5 * 1024 * 1024))), encoding="utf-8")
        chosen = config(exports={"directory": "exports"})
        history = render.collect_history({}, chosen, root, root / "cache.json", time.monotonic() + FAR, "2026-10-22T12:00:00Z")
        kept = render.history_within_budget(history)
        self.assertLessEqual(len(render.embed(kept).encode("utf-8")), render.PAGE_BUDGET_BYTES)
        self.assertIsNone(kept["rows"][0]["label"])


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
        self.assertEqual(block(page, "delivery-history-view"), {"lineMap": None, "runs": False, "exports": None})
        self.assertRegex(block(page, "delivery-history")["asOf"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

    def test_the_renderers_declared_timeout_caps_every_runs_command(self) -> None:
        runs = Runs(self.root)
        runs.answer({"runs list --json": inventory(listed("run-a")),
                     "runs show run-a --json": {"sleep": 30, "then": run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z")}})
        renderers = [{**self.RENDERERS[1], "timeoutSeconds": 6}]
        started = time.monotonic()
        result = self.render({}, {"spec": views_common.CONFIG_SPEC, "runs": {"argv": runs.argv, "timeoutSeconds": 10}},
                             renderers=renderers)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - started, 10)
        [row] = block(result.stdout.decode("utf-8"), "delivery-history")["rows"]
        self.assertEqual(row["note"], "Its run could not be read from the product.")

    def test_the_page_inlines_its_own_and_the_run_summarys_assets(self) -> None:
        result = self.render({}, {"spec": views_common.CONFIG_SPEC})
        page = result.stdout.decode("utf-8")
        for asset in ("history.css", "history.js", "run-summary.css", "run-summary.js"):
            self.assertIn((render.ASSETS / asset).read_text(encoding="utf-8"), page, asset)

    def test_a_state_dir_holds_the_history_cache_and_the_bindings_directory_is_never_touched(self) -> None:
        runs = Runs(self.root)
        runs.answer({"runs list --json": inventory(listed("run-a")),
                     "runs show run-a --json": run_export("run-a", "ABC-1", "2026-10-20T10:30:00Z")})
        with tempfile.TemporaryDirectory() as elsewhere:
            served = Path(elsewhere) / "record"
            served.mkdir()
            result = self.render({}, {"spec": views_common.CONFIG_SPEC, "runs": {"argv": runs.argv, "timeoutSeconds": 10}},
                                 "--state-dir", str(served))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(views_common.read_runs_cache(views_common.runs_cache_path(served).with_name("history.json"))), ["run-a"])
        self.assertFalse((self.root / "state").exists(), "the binding's directory is neither read nor written")

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
