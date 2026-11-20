"""What the stored runtime accepts and shows for the run lifecycle every delivery workflow follows.

The runtime is characterized, not changed. Each vector in ``tests/fixtures/run-events/acceptance.json`` is run again
through the stored runtime's own CLI, so a later runtime that refuses a planned emission fails here. Each driven
scenario below is run again too, and its ``runs list --json``, ``runs view <id> --json`` and ``runs show <id> --json``
are compared, apart from times, ids and sizes, with the output captured under ``tests/fixtures/run-events/runs/``.
The live runs page is built against those captured files, so a runtime whose output changes shape fails here first.

The driven runs are evidence of what the runtime accepts and shows, not of what the skills say: the skills' wording is
pinned by ``tests/test_run_event_emission_corpus.py``.

Every run happens in a temporary git repository, never in the checkout, under Node 22.6 or newer. Where Node is
missing or older, the tests fail naming the need rather than skip.

``python3 -B tests/test_run_event_acceptance.py --write`` drives the scenarios once and rewrites the captured files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "product-runtime"
FIXTURES = ROOT / "tests" / "fixtures" / "run-events"
VECTORS = FIXTURES / "acceptance.json"
CAPTURED = FIXTURES / "runs"
TEMPLATE = FIXTURES / "fixture-repository"
NODE_MINIMUM = (22, 6)
NODE_NEED = "Node.js 22.6 or newer on PATH runs the stored runtime these tests characterize"
COMMAND_SECONDS = 60
HOST = "claude-code"
WORKFLOW = {"releaseId": "linear-v0.0.0", "profile": "linear"}
# Stands in for a host-supplied path in what is stored, so the captured files carry no temporary folder.
STORED_COMMON_DIR = "/fixture/.git"


def find_node() -> str | None:
    node = shutil.which("node")
    if node is None:
        return None
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    match = re.fullmatch(r"v(\d+)\.(\d+)\.\d+", version)
    return node if match and (int(match[1]), int(match[2])) >= NODE_MINIMUM else None


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *arguments], cwd=repository, check=True,
                          capture_output=True, text=True).stdout.strip()


def fixture_environment() -> dict[str, str]:
    """The process environment without anything that would make the runtime treat the run as CI or another host's."""
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith(("GITHUB_", "DELIVERY_HARNESS_", "GIT_")) and name != "CI"}
    environment.update({"GIT_AUTHOR_NAME": "Run Events", "GIT_AUTHOR_EMAIL": "run-events@example.invalid",
                        "GIT_COMMITTER_NAME": "Run Events", "GIT_COMMITTER_EMAIL": "run-events@example.invalid",
                        "GIT_AUTHOR_DATE": "2026-11-20T09:00:00Z", "GIT_COMMITTER_DATE": "2026-11-20T09:00:00Z"})
    return environment


def make_template(folder: Path) -> Path:
    """A fixture repository on `candidate`, its `main` pushed to a bare `origin` beside it, with no installation."""
    repository = folder / "repository"
    shutil.copytree(TEMPLATE, repository)
    remote = folder / "remote.git"
    environment = fixture_environment()
    for arguments, where in ((["init", "-q", "--initial-branch=main"], repository),
                             (["add", "-A"], repository),
                             (["commit", "-qm", "the fixture"], repository),
                             (["init", "-q", "--bare", "--initial-branch=main", str(remote)], folder),
                             (["remote", "add", "origin", "../remote.git"], repository),
                             (["push", "-q", "origin", "main"], repository),
                             (["fetch", "-q", "origin"], repository),
                             (["checkout", "-q", "-b", "candidate"], repository)):
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *arguments], cwd=where, check=True,
                       capture_output=True, env=environment)
    return repository


class Fixture:
    """One copy of the template: its own repository, origin and run store."""

    def __init__(self, node: str, template: Path, folder: Path) -> None:
        shutil.copytree(template.parent, folder)
        self.node = node
        self.repository = folder / template.name
        self.environment = fixture_environment()
        self.tree = git(self.repository, "rev-parse", "HEAD^{tree}")

    def runtime(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([self.node, "--experimental-strip-types", "--no-warnings", "--import",
                               (RUNTIME / "bootstrap.mjs").as_uri(), str(RUNTIME / "cli.mjs"), *arguments],
                              cwd=self.repository, capture_output=True, text=True, env=self.environment,
                              timeout=COMMAND_SECONDS)

    def emit(self, kind: str, event_id: str, payload: dict[str, Any], *, force: bool = False,
             run: str | None = None) -> subprocess.CompletedProcess[str]:
        arguments = ["emit", kind, "--event-id", event_id, "--json", json.dumps(self.bind(payload))]
        if kind == "run.started":
            arguments[2:2] = ["--version", "2"]
        if force:
            arguments.append("--force")
        if run is not None:
            arguments += ["--run", run]
        return self.runtime(*arguments)

    def bind(self, value: Any) -> Any:
        if value == "{tree}":
            return self.tree
        if isinstance(value, dict):
            return {key: self.bind(member) for key, member in value.items()}
        if isinstance(value, list):
            return [self.bind(member) for member in value]
        return value

    def json(self, *arguments: str) -> dict[str, Any]:
        result = self.runtime(*arguments)
        if result.returncode != 0:
            raise AssertionError(f"`{' '.join(arguments)}` exited {result.returncode}: {result.stderr}")
        return json.loads(result.stdout)


def started_run(result: subprocess.CompletedProcess[str]) -> str:
    match = re.match(r"started run (run-[0-9a-f]+)", result.stdout)
    if result.returncode != 0 or match is None:
        raise AssertionError(f"run.started was refused: {result.stdout}{result.stderr}")
    return match[1]


def matches(expected: Any, actual: Any) -> bool:
    if expected == "*":
        return actual is not None
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(matches(value, actual.get(key)) for key, value in expected.items())
    return expected == actual


# The driven scenarios. Each step is one emission (`emit`, with its event id and payload), a runtime command
# (`command`), or the start of a second run in the same worktree (`emit` of `run.started` with `force`). `{tree}` is
# the fixture's HEAD^{tree}: no candidate exists yet when these runs' stages start.

def stage(name: str, attempt: int, state: str, next_step: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"activityId": f"stage-{name}", "attemptId": f"stage-{name}-{attempt}",
                               "candidateTreeSha": "{tree}", "phase": name, "state": state, "owner": HOST}
    if attempt > 1:
        # The runtime refuses a second attempt of an activity that does not name the attempt it supersedes, and
        # every state of that attempt repeats the binding.
        payload["supersedesAttemptId"] = f"stage-{name}-{attempt - 1}"
    if next_step is not None:
        payload["nextStep"] = next_step
    return payload


def wait(activity: str, attempt: int, wait_id: str, waiting_on: str, reason: str, next_action: str) -> dict[str, Any]:
    return {"activityId": f"stage-{activity}", "attemptId": f"stage-{activity}-{attempt}", "candidateTreeSha": "{tree}",
            "waitId": wait_id, "owner": "operator" if waiting_on == "human" else HOST, "waitingOn": waiting_on,
            "reason": reason, "nextAction": next_action, "scope": "This request only; it grants no authority."}


def resolved(activity: str, attempt: int, wait_id: str, resolution: str) -> dict[str, Any]:
    return {"activityId": f"stage-{activity}", "attemptId": f"stage-{activity}-{attempt}", "candidateTreeSha": "{tree}",
            "waitId": wait_id, "resolution": resolution, "scope": "This request only; it grants no authority."}


def lens(state: str, round_number: int = 1) -> dict[str, Any]:
    payload: dict[str, Any] = {"activityId": "review-lens-outcome", "attemptId": f"lens-outcome-{round_number}",
                               "candidateTreeSha": "{tree}", "roundId": f"round-{round_number}", "round": round_number,
                               "lensId": "lens.outcome-correctness", "state": state, "owner": "reviewer",
                               "phase": "review"}
    if state == "running":
        payload["nextStep"] = "Complete the selected lens."
    return payload


def work(slice_number: int, state: str, next_step: str) -> dict[str, Any]:
    return {"activityId": f"execute-slice-{slice_number}", "attemptId": f"execute-slice-{slice_number}-1",
            "candidateTreeSha": "{tree}", "phase": "execute", "state": state, "owner": HOST, "nextStep": next_step}


START = {"host": HOST, "workflow": WORKFLOW}
ROUND_OPENED = {"round": 1, "roundId": "round-1", "bound": 4, "candidateTreeSha": "{tree}",
                "lenses": ["lens.outcome-correctness", "lens.adversarial-testing"]}
ROUND_CLOSED = {"round": 1, "roundId": "round-1", "candidateTreeSha": "{tree}", "outcome": "aligned",
                "findings": {"P0": 0, "P1": 0, "P2": 1, "P3": 0},
                "cost": {"unit": "subagent-tokens", "total": 48000, "reportedBy": HOST, "coverage": "complete"}}

SCENARIOS: dict[str, list[dict[str, Any]]] = {
    # A request routed through plan and then execute, ended: gates, a decision, a blocker, a change request, a nested
    # review entered and exited, a lens attempt under phase review, a resolved human wait, and an end with cost.
    "ended-plan-then-execute": [
        {"emit": "run.started", "id": "start", "payload": START},
        {"emit": "activity.observed", "id": "plan-in", "payload": stage("plan", 1, "running", "Write the plan.")},
        {"emit": "wait.started", "id": "approval-wait",
         "payload": wait("plan", 1, "plan-approval", "human", "The plan needs the operator's approval.",
                         "Approve the plan or ask for changes.")},
        {"emit": "wait.resolved", "id": "approval-resolved",
         "payload": resolved("plan", 1, "plan-approval", "The operator approved the plan.")},
        {"emit": "activity.observed", "id": "plan-out", "payload": stage("plan", 1, "completed")},
        {"emit": "activity.observed", "id": "execute-in",
         "payload": stage("execute", 1, "running", "Implement the first slice.")},
        {"emit": "ticket.read", "id": "ticket", "payload": {"ticket": "V26-0001", "tracker": "linear"}},
        {"emit": "decision.recorded", "id": "branch",
         "payload": {"fork": "the branch name", "choice": "claude/V26-0001, the repository's convention"}},
        {"emit": "gate.reported", "id": "gate-registry",
         "payload": {"command": "scripts/validate-registry.py", "outcome": "pass", "durationMs": 4200}},
        {"emit": "gate.reported", "id": "gate-suite",
         "payload": {"command": "scripts/run-tests.py", "outcome": "fail", "durationMs": 615000}},
        {"emit": "blocker.recorded", "id": "blocker",
         "payload": {"code": "review.deferral-untracked", "summary": "One deferral has no tracked follow-up."}},
        {"emit": "pr.opened", "id": "pr",
         "payload": {"url": "https://github.com/example/repository/pull/1", "candidateTreeSha": "{tree}"}},
        {"emit": "activity.observed", "id": "review-in", "payload": stage("review", 1, "running", "Run round 1.")},
        {"emit": "review.round.opened", "id": "round-1-opened", "payload": ROUND_OPENED},
        {"emit": "activity.observed", "id": "lens-running", "payload": lens("running")},
        {"emit": "activity.observed", "id": "lens-completed", "payload": lens("completed")},
        {"emit": "review.round.closed", "id": "round-1-closed", "payload": ROUND_CLOSED},
        {"emit": "activity.observed", "id": "review-out", "payload": stage("review", 1, "completed")},
        {"emit": "activity.observed", "id": "execute-out", "payload": stage("execute", 1, "completed")},
        {"emit": "run.ended", "id": "end",
         "payload": {"result": "complete", "cost": {"unit": "subagent-tokens", "total": 152352, "reportedBy": HOST,
                                                    "coverage": "partial"}}},
    ],
    # An execute stage inside a plan stage, still open: the runtime's own prepare and verify record their phases, a
    # lens attempt runs under phase review inside a nested review stage that exits, execute work goes on, and the
    # operator is waited on.
    "open-execute-inside-plan": [
        {"emit": "run.started", "id": "start", "payload": START},
        {"emit": "activity.observed", "id": "plan-in", "payload": stage("plan", 1, "running", "Plan the change.")},
        {"emit": "activity.observed", "id": "execute-in",
         "payload": stage("execute", 1, "running", "Implement the first slice.")},
        {"command": "prepare"},
        {"command": "verify"},
        {"emit": "activity.observed", "id": "review-in", "payload": stage("review", 1, "running", "Run round 1.")},
        {"emit": "activity.observed", "id": "lens-running", "payload": lens("running")},
        {"emit": "activity.observed", "id": "lens-completed", "payload": lens("completed")},
        {"emit": "activity.observed", "id": "review-out", "payload": stage("review", 1, "completed")},
        {"emit": "activity.observed", "id": "slice-2", "payload": work(2, "running", "Fix the round's finding.")},
        {"emit": "gate.reported", "id": "gate-registry",
         "payload": {"command": "scripts/validate-registry.py", "outcome": "pass", "durationMs": 3900}},
        {"emit": "wait.started", "id": "approval-wait",
         "payload": wait("execute", 1, "push-approval", "human", "Pushing the branch needs the operator's grant.",
                         "Grant the push or keep the branch local.")},
    ],
    # Review entered, exited, and entered again after more execute work, still open.
    "open-review-entered-again": [
        {"emit": "run.started", "id": "start", "payload": START},
        {"emit": "activity.observed", "id": "plan-in", "payload": stage("plan", 1, "running")},
        {"emit": "activity.observed", "id": "plan-out", "payload": stage("plan", 1, "completed")},
        {"emit": "activity.observed", "id": "execute-in", "payload": stage("execute", 1, "running")},
        {"emit": "activity.observed", "id": "review-in", "payload": stage("review", 1, "running", "Run round 1.")},
        {"emit": "activity.observed", "id": "review-out", "payload": stage("review", 1, "completed")},
        {"emit": "activity.observed", "id": "slice-2", "payload": work(2, "completed", "Fix round 1's finding.")},
        {"emit": "activity.observed", "id": "review-again", "payload": stage("review", 2, "running", "Run round 2.")},
    ],
    # Execute exits, review runs and exits, and execute is entered again, still open.
    "open-execute-entered-again": [
        {"emit": "run.started", "id": "start", "payload": START},
        {"emit": "activity.observed", "id": "plan-in", "payload": stage("plan", 1, "running")},
        {"emit": "activity.observed", "id": "plan-out", "payload": stage("plan", 1, "completed")},
        {"emit": "activity.observed", "id": "execute-in", "payload": stage("execute", 1, "running")},
        {"emit": "activity.observed", "id": "execute-out", "payload": stage("execute", 1, "completed")},
        {"emit": "activity.observed", "id": "review-in", "payload": stage("review", 1, "running", "Run round 1.")},
        {"emit": "activity.observed", "id": "review-out", "payload": stage("review", 1, "completed")},
        {"emit": "activity.observed", "id": "execute-again",
         "payload": stage("execute", 2, "running", "Fix round 1's finding.")},
    ],
    # An open run waiting on an agent: no one is needed.
    "open-waiting-on-an-agent": [
        {"emit": "run.started", "id": "start", "payload": START},
        {"emit": "activity.observed", "id": "diagnose-in",
         "payload": stage("diagnose", 1, "running", "Trace the failing assertion.")},
        {"emit": "wait.started", "id": "agent-wait",
         "payload": wait("diagnose", 1, "reproduction", "agent", "A subagent is reproducing the failure.",
                         "Compare its observation with the prediction.")},
    ],
    # A run left open, then displaced by the next start's --force, and that start's run, ended partial.
    "displaced-then-partial": [
        {"emit": "run.started", "id": "start", "payload": START},
        {"emit": "activity.observed", "id": "execute-in", "payload": stage("execute", 1, "running")},
        {"emit": "run.started", "id": "start-again", "force": True, "payload": START},
        {"emit": "activity.observed", "id": "execute-in-again",
         "payload": stage("execute", 1, "running", "Implement the slice.")},
        {"emit": "activity.observed", "id": "execute-stopped", "payload": stage("execute", 1, "interrupted")},
        {"emit": "run.ended", "id": "end",
         "payload": {"result": "partial", "cost": {"coverage": "unreported", "reportedBy": HOST}}},
    ],
}


def drive(fixture: Fixture, steps: list[dict[str, Any]]) -> list[str]:
    """Run ``steps`` in ``fixture`` and return the runs they started, in order. Any refusal fails."""
    runs: list[str] = []
    for step in steps:
        if "command" in step:
            # Its own outcome is the fixture's, not this characterization's: verify blocks on a fixture with no
            # record. What counts is that the command recorded its phase, which the captured view shows.
            fixture.runtime(step["command"])
            continue
        result = fixture.emit(step["emit"], step["id"], step["payload"], force=step.get("force", False),
                              run=None if step["emit"] == "run.started" else runs[-1])
        if step["emit"] == "run.started":
            runs.append(started_run(result))
        elif result.returncode != 0:
            raise AssertionError(f"{step['emit']} {step['id']} was refused: {result.stdout}{result.stderr}")
    return runs


def capture(fixture: Fixture, runs: list[str]) -> dict[str, Any]:
    """The runtime's own output for the driven runs: the inventory, and each run's view and export."""
    return {"list": fixture.json("runs", "list", "--json"),
            "runs": {run: {"view": fixture.json("runs", "view", run, "--json"),
                           "show": fixture.json("runs", "show", run, "--json")} for run in runs}}


TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z")
RUN_ID = re.compile(r"\brun-[0-9a-f]{16}\b")
GENERATED_ID = re.compile(r"\b(?:(command|wait)-)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


def stored(captured: dict[str, Any], runs: list[str], common_dir: str) -> dict[str, Any]:
    """``captured`` as it is checked in: run ids renumbered in the order the runs started, other ids the runtime
    made renumbered in order of first appearance, its times moved onto one fixed day keeping their order, and the
    fixture's folder replaced."""
    text = json.dumps(captured).replace(common_dir, STORED_COMMON_DIR)
    run_ids = {run: f"run-{index + 1:016x}" for index, run in enumerate(runs)}
    generated: dict[str, str] = {}
    text = RUN_ID.sub(lambda m: run_ids.setdefault(m[0], f"run-{len(run_ids) + 1:016x}"), text)
    text = GENERATED_ID.sub(lambda m: generated.setdefault(m[0], f"{m[1] or 'id'}-{len(generated) + 1:04d}"), text)
    seconds = sorted({stamp[:19] for stamp in TIMESTAMP.findall(text)})
    moved = {second: f"2026-11-20T09:{index // 60:02d}:{index % 60:02d}" for index, second in enumerate(seconds)}
    text = TIMESTAMP.sub(lambda m: moved[m[0][:19]] + m[0][19:], text)
    return json.loads(text)


# What legitimately differs between two drives of the same scenario: times, how long a step took, how fresh an
# observation is, and the sizes that follow from those. Ids are already renumbered by `stored`.
DURATION_KEYS = {"durationSeconds", "bytes", "implementationSeconds", "reviewSeconds", "tailSeconds", "totalMs"}
RUNTIME_COMMANDS = {"prepare", "verify"}
VARYING_FIELDS = {"Elapsed since start", "Freshness", "Last observed", "Reported at", "Started", "Ended", "Duration",
                  "Age", "Elapsed"}


def comparable(value: Any) -> Any:
    """``value`` with what varies between drives masked. Runs started in the same second list in either order."""
    if isinstance(value, dict) and value.get("spec") == "run-inventory/1":
        value = {**value, "runs": sorted(value["runs"], key=lambda run: run["runId"])}
    if isinstance(value, dict):
        if "label" in value and "value" in value and value["label"] in VARYING_FIELDS:
            return {**value, "value": "<varies>"}
        # A runtime command's own duration varies; a reported gate's is the one the fixture supplied.
        varying = DURATION_KEYS | ({"durationMs"} if value.get("command") in RUNTIME_COMMANDS else set())
        return {key: "<varies>" if key in varying and isinstance(member, (int, float)) else comparable(member)
                for key, member in value.items()}
    if isinstance(value, list):
        return [comparable(member) for member in value]
    if isinstance(value, str):
        value = TIMESTAMP.sub("<time>", value)
        value = re.sub(r'("command":"(?:prepare|verify)","outcome":"[a-z]+","durationMs":)\d+', r"\1<varies>", value)
        return re.sub(r"^\d+(?:\.\d+)?(?:ms|s|m|h)(?: \d+(?:ms|s|m))*$", "<varies>", value)
    return value


def drive_scenario(node: str, template: Path, folder: Path, name: str) -> dict[str, Any]:
    fixture = Fixture(node, template, folder / name)
    runs = drive(fixture, SCENARIOS[name])
    return stored(capture(fixture, runs), runs, str(fixture.repository / ".git"))


class RunEventAcceptanceTests(unittest.TestCase):
    """The stored runtime, driven through its CLI in temporary repositories."""

    node: str
    template: Path
    folder: Path

    @classmethod
    def setUpClass(cls) -> None:
        node = find_node()
        if node is None:
            raise AssertionError(f"{NODE_NEED}; none was found")
        cls.node = node
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.folder = Path(temporary.name)
        cls.template = make_template(cls.folder / "template")

    def fixture(self, name: str) -> Fixture:
        return Fixture(self.node, self.template, self.folder / name)

    def test_the_stored_runtime_is_the_one_characterized(self) -> None:
        declared = json.loads((RUNTIME / "runtime.json").read_text(encoding="utf-8"))
        vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
        self.assertEqual(declared["runtimeVersion"], vectors["runtimeVersion"])

    def test_the_runtime_writes_version_2_with_artifact_capture(self) -> None:
        capabilities = self.fixture("capabilities").json("runs", "capabilities", "--json")
        self.assertEqual(capabilities["spec"], "run-capabilities/1")
        self.assertIn("run-event/2", capabilities["writerVersions"])
        self.assertIs(capabilities["artifactCapture"], True)

    def test_each_acceptance_vector(self) -> None:
        vectors = json.loads(VECTORS.read_text(encoding="utf-8"))["vectors"]
        self.assertGreaterEqual(len(vectors), 7)
        for vector in vectors:
            with self.subTest(vector=vector["id"]):
                fixture = self.fixture(f"vector-{vector['id']}")
                self.assertFalse((fixture.repository / ".agent-skills").exists(), "the fixture has an installation")
                runs: list[str] = []
                for step in vector["steps"]:
                    if "command" in step:
                        result = fixture.runtime(step["command"])
                        self.assertEqual(result.returncode, step["exit"], result.stdout + result.stderr)
                        continue
                    result = fixture.emit(step["emit"], step["eventId"], step["payload"],
                                          force=step.get("force", False),
                                          run=None if step["emit"] == "run.started" or not runs else runs[-1])
                    if step["expect"] == "accepted":
                        self.assertEqual(result.returncode, 0, f"{step['eventId']}: {result.stdout}{result.stderr}")
                        if step["emit"] == "run.started":
                            runs.append(started_run(result))
                    else:
                        self.assertEqual(result.returncode, 1, f"{step['eventId']} was accepted: {result.stdout}")
                        self.assertIn(f"[{step['code']}]", result.stderr)
                events = fixture.json("runs", "show", runs[-1], "--json")["events"]
                for expected in vector["journal"]:
                    entry = fixture.bind(expected)
                    self.assertTrue(any(matches(entry, event) for event in events),
                                    f"no journal entry matches {entry}")

    def test_a_refused_start_leaves_the_open_run_current(self) -> None:
        fixture = self.fixture("refused-start")
        first = started_run(fixture.emit("run.started", "start", START))
        refused = fixture.emit("run.started", "start-again", START)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("[run_already_current]", refused.stderr)
        inventory = fixture.json("runs", "list", "--json")
        self.assertEqual(inventory["current"], first)
        self.assertEqual([run["runId"] for run in inventory["runs"]], [first])

    def test_a_forced_start_records_the_open_run_as_displaced(self) -> None:
        fixture = self.fixture("forced-start")
        first = started_run(fixture.emit("run.started", "start", START))
        second = started_run(fixture.emit("run.started", "start-again", START, force=True))
        started = fixture.json("runs", "show", second, "--json")["events"][0]
        self.assertEqual(started["payload"]["displacedRunId"], first)
        inventory = fixture.json("runs", "list", "--json")
        self.assertEqual(inventory["current"], second)
        self.assertEqual({run["runId"]: run["open"] for run in inventory["runs"]}, {first: True, second: True})

    def test_the_captured_output_is_what_the_runtime_shows(self) -> None:
        self.assertEqual(sorted(path.stem for path in CAPTURED.glob("*.json")), sorted(SCENARIOS))
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                captured = json.loads((CAPTURED / f"{name}.json").read_text(encoding="utf-8"))
                fresh = drive_scenario(self.node, self.template, self.folder, name)
                self.assertEqual(comparable(fresh), comparable(captured))


def write_captures() -> None:
    node = find_node()
    if node is None:
        raise SystemExit(f"{NODE_NEED}; none was found")
    CAPTURED.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        template = make_template(folder / "template")
        for name in SCENARIOS:
            captured = drive_scenario(node, template, folder, name)
            (CAPTURED / f"{name}.json").write_text(json.dumps(captured, indent=2, ensure_ascii=False) + "\n",
                                                   encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        write_captures()
    else:
        unittest.main()
