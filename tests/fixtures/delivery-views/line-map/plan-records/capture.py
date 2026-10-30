#!/usr/bin/env python3
"""Capture the record documents behind the plan chapter's synced and merged proof shapes.

Each document is what `delivery_state.py show` prints for a record a real runner wrote: two lanes hand off reviewed
work, one with its native review outcomes captured, the base moves, and the runner syncs each candidate onto the new
base, which gives it a new `syncedHead`, then merges it. Captured three times: once both are handed off, once both
are synced (the first has merged by then, the second is checked), and once both have merged.

    python3 tests/fixtures/delivery-views/line-map/plan-records/capture.py [<out directory>]

Commit ids and times differ on every run, so the capture writes them as they came; tests compare the documents'
shapes, not those values. Temporary paths never reach the record.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))

from tests import test_merge_queue as harness  # noqa: E402

state_tool = harness.state_tool
LENSES = ("lens.outcome-correctness", "lens.adversarial-testing")


def review(adopter: harness.Adopter, subject: str, lane: str, number: int, verdict: str) -> None:
    at = state_tool.now()
    state_tool.append_journal(adopter.state, kind="review.round", subject=subject, lane=lane,
                              summary=f"{subject}: review round {number}",
                              review={"round": number, "startedAt": at, "finishedAt": at,
                                      "verdicts": {lens: verdict for lens in LENSES},
                                      "findings": {"P0": 0, "P1": 0 if verdict == "approve" else 1, "P2": 0, "P3": 0}})


def outcome(subject: str, number: int, candidate: str) -> dict:
    entries = []
    for lens in LENSES:
        binding = {"subjectRef": subject, "candidateRef": candidate, "round": number}
        entries.append({"lens": lens, "state": "obtained", "binding": binding, "result": {
            "schemaVersion": "review-lens-result/1", "lensId": lens, **binding, "outcome": "aligned",
            "findings": [], "resolvedFindings": [], "evidence": {"startCommit": candidate}}})
    return {"round": number, "outcome": "aligned", "entries": entries}


def capture(out: Path) -> None:
    work = Path(tempfile.mkdtemp(prefix="plan-records-")).resolve()
    try:
        adopter = harness.Adopter(work)
        state = state_tool.read_state(adopter.state)
        lanes = [{"id": lane, "title": f"Lane {lane[-1]}", "focus": "", "state": "working", "waitingOn": None,
                  "since": state_tool.now()} for lane in ("lane-1", "lane-2")]
        planned = [{"subject": "journal-sync", "title": "Synced with a journaled review", "lane": "lane-1", "stage": "review"},
                   {"subject": "native-sync", "title": "Synced with a captured native review", "lane": "lane-2", "stage": "review"}]
        state_tool.patch(adopter.state, writer="lead", version=state["version"],
                         changes={"lanes": lanes, "planned": planned,
                                  "finish": [{"id": "landed", "text": "Both candidates land", "met": False}]})
        heads = {}
        for subject, lane, path in (("journal-sync", "lane-1", "src/app.txt"), ("native-sync", "lane-2", "src/lib.txt")):
            heads[subject] = adopter.lane_commit(subject, {path: f"{subject}\n"})
            review(adopter, subject, lane, 1, "changes")
            review(adopter, subject, lane, 2, "approve")
            state_tool.handoff(adopter.state, lane=lane, subject=subject, head=heads[subject],
                               change_request=f"#{len(heads)}", rounds=2)
        rounds = []
        for number in (1, 2):
            path = work / f"native-round-{number}.json"
            path.write_text(json.dumps(outcome("native-sync", number, heads["native-sync"])) + "\n")
            rounds.append(path)
        state_tool.capture_review(adopter.state, subject="native-sync", candidate=heads["native-sync"], outcomes=rounds)
        shown = {"handed": state_tool.show(adopter.state)}
        adopter.advance_base({"src/other.txt": "the base moved\n"})

        def before_merge(runner, subject):
            if "synced" not in shown and all(adopter.item(name)["syncedHead"] for name in heads):
                shown["synced"] = state_tool.show(adopter.state)

        adopter.runner(seams={"before_merge": before_merge}).run_command("run", ["journal-sync", "native-sync"])
        shown["merged"] = state_tool.show(adopter.state)
        for name, document in shown.items():
            for item in document["state"]["queue"]:
                assert name == "handed" or item["syncedHead"] not in (None, item["head"]), (name, item)
            text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
            assert str(work) not in text, "a temporary path reached the record"
            (out / f"{name}.json").write_text(text, encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    capture(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent)
