"""Shared fixtures for the delivery views' renderer tests: the source run view, its round reports and a
runs command that answers from a table."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "delivery-views" / "scripts"
RENDER = SCRIPTS / "render.py"
SOURCE = ROOT / "tests" / "fixtures" / "delivery-views" / "line-map" / "source"
sys.path.insert(0, str(SCRIPTS))

import render  # noqa: E402
import views_common  # noqa: E402


VIEW = json.loads((SOURCE / "run-view.json").read_text(encoding="utf-8"))
LISTED = {"runId": "run-7", "status": "incomplete", "open": True, "durationSeconds": 3000, "bytes": 4000}
RUN_ID = "run-7"
FIX = "abc1234".ljust(40, "0")
TREES = {1: "1" * 40, 2: "2" * 40}
SPEC = "review-round-report/1"


def report(number: int, **overrides) -> dict:
    raised = [
        {"id": "r1-f1", "lens": "lens.adversarial-testing", "severity": "P1", "summary": "A retry after a timeout charges twice", "state": "open", "deferredIssue": None},
        {"id": "r1-f2", "lens": "lens.outcome-correctness", "severity": "P2", "summary": "The receipt keeps the first attempt's total", "state": "open", "deferredIssue": None},
        {"id": "r1-f3", "lens": "lens.adversarial-testing", "severity": "P2", "summary": "No test covers a dropped second attempt", "state": "open", "deferredIssue": None},
    ] if number == 1 else [
        {"id": "r2-f1", "lens": "lens.outcome-correctness", "severity": "P3", "summary": "The retry log line repeats the order id", "state": "open", "deferredIssue": None},
    ]
    resolved = [] if number == 1 else [
        {"id": "r1-f1", "state": "fixed", "fixCommit": FIX, "deferredIssue": None},
        {"id": "r1-f2", "state": "dropped", "fixCommit": None, "deferredIssue": None},
    ]
    return {"spec": SPEC, "runId": RUN_ID, "ticket": "PAY-2361", "round": number, "roundId": f"round-{number}",
            "candidateTreeSha": TREES[number], "closedAt": "2026-10-22T04:40:00Z", "raised": raised, "resolved": resolved,
            **overrides}


def reading(body, artifact_id: str | None = None, **metadata) -> dict:
    """The product's reading of an attachment, as ``runs artifact <run> <id> --json`` prints it."""
    data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode("utf-8")
    number = body.get("round") if isinstance(body, dict) else None
    return {"spec": "run-artifact/1", "runId": RUN_ID, "ok": True, "base64": base64.b64encode(data).decode("ascii"),
            "metadata": {"artifactId": artifact_id or f"artifact-round-{number}", "digest": hashlib.sha256(data).hexdigest(),
                         "sizeBytes": len(data), "round": number, "roundId": f"round-{number}",
                         "candidateTreeSha": TREES.get(number), **metadata}}


def readings(**entries) -> dict:
    return {"artifact-round-1": reading(report(1)), "artifact-round-2": reading(report(2)), **entries}


def reference(number: int, artifact_id: str | None, item_id: str | None = None) -> dict:
    """A report reference as the run view lists it; an unavailable one names no attachment."""
    item = {"id": item_id or f"report-round-{number}", "label": "review",
            "fields": [{"label": "Report", "value": item_id or f"report-round-{number}"},
                       {"label": "Availability", "value": "referenced" if artifact_id else "unavailable"}]}
    if artifact_id:
        item["artifactId"] = artifact_id
    return item


def _observed(finding: str, state: str) -> dict:
    return {"id": finding, "label": finding, "fields": [{"label": "State", "value": state}]}


def report_view(references: list | None = None) -> dict:
    """The source run view with report references (one a round by default) and its findings' observed states."""
    references = references if references is not None else [reference(1, "artifact-round-1"), reference(2, "artifact-round-2")]
    history = [_observed("r1-f1", "unresolved"), _observed("r1-f2", "unresolved"), _observed("r1-f3", "unresolved"),
               _observed("r2-f1", "unresolved"), _observed("r1-f1", "resolved"), _observed("r1-f2", "resolved")]
    return {**VIEW, "sections": [*VIEW["sections"], {"id": "reports", "items": references},
                                 {"id": "finding-history", "items": history}]}


def document_naming(queue: list, planned: list = ()) -> dict:
    return {"state": {
        "queue": [{"subject": f"PAY-{index + 1}", "runId": run_id} for index, run_id in enumerate(queue)],
        "planned": [{"subject": f"PAY-{index + 101}", **({"runId": run_id} if run_id else {})} for index, run_id in enumerate(planned)],
    }}


FAKE_RUNS = r'''
import json, os, sys, time
table = json.load(open(sys.argv[1], encoding="utf-8"))
key = " ".join(sys.argv[2:])
with open(sys.argv[1] + ".calls", "a", encoding="utf-8") as calls:
    calls.write(key + "\n")
answer = table.get(key)
if isinstance(answer, dict) and "sleep" in answer:
    # Each slow read leaves its start and end, so a test can count how many ran at once.
    with open(sys.argv[1] + ".spans", "a", encoding="utf-8") as spans:
        spans.write(f"1 {time.time()}\n")
    time.sleep(answer["sleep"])
    with open(sys.argv[1] + ".spans", "a", encoding="utf-8") as spans:
        spans.write(f"-1 {time.time()}\n")
    answer = answer.get("then")
if isinstance(answer, dict) and "bytes" in answer:
    sys.stdout.write("x" * answer["bytes"])
    raise SystemExit(0)
if isinstance(answer, dict) and "exit" in answer:
    raise SystemExit(answer["exit"])
if answer is None:
    raise SystemExit(1)
sys.stdout.write(answer if isinstance(answer, str) else json.dumps(answer))
'''


class Runs:
    """A runs command in a directory: it answers each argument list from a table and logs the calls."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.script = directory / "fake-runs.py"
        self.table = directory / "runs-table.json"
        self.script.write_text(FAKE_RUNS, encoding="utf-8")
        self.answer({})

    def answer(self, table: dict) -> None:
        self.table.write_text(json.dumps(table), encoding="utf-8")
        for suffix in (".calls", ".spans"):
            Path(str(self.table) + suffix).unlink(missing_ok=True)

    def calls(self) -> list[str]:
        calls = Path(str(self.table) + ".calls")
        return calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []

    def most_at_once(self) -> int:
        """The most slow reads that were running at the same time."""
        spans = Path(str(self.table) + ".spans")
        events = sorted((float(at), int(step)) for step, at in
                        (line.split() for line in spans.read_text(encoding="utf-8").splitlines())) if spans.exists() else []
        running = most = 0
        for _, step in events:
            running += step
            most = max(most, running)
        return most

    @property
    def argv(self) -> list[str]:
        return [sys.executable, "-B", str(self.script), str(self.table)]


def table(inventory: list | None, views: dict, artifacts: dict | None = None, run_id: str = RUN_ID) -> dict:
    """A runs table: the inventory, each run's view and each attachment's reading."""
    answers = {"runs list --json": {"spec": "run-inventory/1", "runs": inventory} if inventory is not None else None}
    for name, view in views.items():
        answers[f"runs view {name} --json"] = view
    for artifact, read in (artifacts or {}).items():
        answers[f"runs artifact {run_id} {artifact} --json"] = read
    return answers


def config(runs: Runs | None = None, **overrides) -> dict:
    raw = {"spec": views_common.CONFIG_SPEC, **overrides}
    if runs is not None:
        raw["runs"] = {"argv": runs.argv, "timeoutSeconds": overrides.get("timeout", 10)}
    raw.pop("timeout", None)
    return views_common.validate_config(raw)


class Repository:
    """A Git repository holding a configuration and a binding, for running render.py as a renderer."""

    def __init__(self, directory: Path, raw_config: dict, renderers: list | None = None, state: str = "state"):
        self.root = directory
        subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
        (directory / ".agents").mkdir()
        (directory / ".agents" / "delivery-views.json").write_text(json.dumps(raw_config), encoding="utf-8")
        renderers = renderers if renderers is not None else [
            {"id": "line-map", "argv": ["python3", "-B", "render.py", "line-map"], "output": "line-map.html", "timeoutSeconds": 30}]
        (directory / ".agents" / "orchestration.json").write_text(
            json.dumps({"state": {"directory": state, "renderers": renderers}}), encoding="utf-8")

    def render(self, document: object, *extra: str, cwd: Path | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-B", str(RENDER), "line-map", "--config", ".agents/delivery-views.json",
                               "--binding", ".agents/orchestration.json", *extra], cwd=cwd or self.root,
                              input=json.dumps(document).encode("utf-8"), capture_output=True, timeout=timeout)


def block(page: str, block_id: str):
    import re
    found = re.search(rf'<script type="application/json" id="{block_id}">([^<]*)</script>', page)
    return json.loads(found.group(1)) if found else None

