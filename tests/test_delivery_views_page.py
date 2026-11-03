from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_skills.validate import LEAKAGE_PATTERNS


ROOT = Path(__file__).resolve().parents[1]
LANE = ROOT / "tests" / "delivery_views_page"
REPORTER = LANE / "reporter.mjs"
NODE_MINIMUM = 20
SETUP = "run python3 scripts/bootstrap-test-environment.py before running the suite."


def lane_files(lane: Path = LANE) -> list[Path]:
    return sorted(lane.glob("*.test.mjs"))


def find_node() -> str | None:
    node = shutil.which("node")
    if node is None:
        return None
    version = subprocess.run(
        [node, "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    major = version.removeprefix("v").split(".", 1)[0]
    return node if major.isdigit() and int(major) >= NODE_MINIMUM else None


def stop_tree(process: subprocess.Popen) -> None:
    """Kill a process and what it started, by pid.

    Not by group: the lane shares its worker's group, for the test runner's own
    stop to reach it, so killing that group would kill the worker too.
    """
    if os.name == "nt":
        process.kill()
        return
    # Held still first, so it starts nothing after the read.
    process.send_signal(signal.SIGSTOP)
    table = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, check=False).stdout
    children: dict[int, list[int]] = {}
    for line in table.splitlines():
        pid, parent = map(int, line.split())
        children.setdefault(parent, []).append(pid)
    found, pending = [], [process.pid]
    while pending:
        found.append(pending.pop())
        pending.extend(children.get(found[-1], []))
    for pid in found:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def run_lane(node: str, files: list[Path], timeout: float = 600) -> str | None:
    """Run the page tests and return why the lane failed, or None."""
    process = subprocess.Popen(
        [
            node,
            "--test",
            f"--test-reporter={REPORTER.as_uri()}",
            "--test-reporter-destination=stdout",
            *(str(path) for path in files),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_tree(process)
        # What had finished narrows a hang down to the tests that had not. Bounded, as anything missed holds the pipes.
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired as expired:
            process.kill()
            stdout = expired.stdout.decode("utf-8", "replace") if isinstance(expired.stdout, bytes) else expired.stdout or ""
            stderr = ""
        return (
            f"The page tests did not finish in {timeout:g}s. Reported so far:\n{stdout.strip()}"
            + (f"\nTheir errors:\n{stderr.strip()}" if stderr.strip() else "")
        )
    except BaseException:
        stop_tree(process)
        raise
    result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    events = []
    for line in result.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if result.returncode != 0:
        failed = [
            f"{event['name']}: {event['error']}"
            for event in events
            if event["outcome"] == "fail" and not event["suite"]
        ]
        return "The page tests failed:\n" + "\n".join(failed or [result.stderr.strip()])
    skipped = [event["name"] for event in events if event["skipped"]]
    if skipped:
        return "Page tests were skipped or marked todo: " + ", ".join(skipped)
    # Node reports each file as a top-level entry of its own; only the tests
    # declared inside a file count as executed tests.
    executed = [
        event
        for event in events
        if not event["suite"]
        and event["file"]
        and not (event["nesting"] == 0 and Path(event["name"]).name == Path(event["file"]).name)
    ]
    if not executed:
        return "No page tests ran."
    listed = {path.resolve() for path in files}
    ran = {Path(event["file"]).resolve() for event in executed}
    if ran != listed:
        unlisted = sorted(path.name for path in ran - listed)
        silent = sorted(path.name for path in listed - ran)
        return (
            "The executed page-test files differ from the lane's file list: "
            f"ran but unlisted {unlisted}, listed but ran no tests {silent}."
        )
    return None


class PageLaneTests(unittest.TestCase):
    def test_page_lane_passes(self) -> None:
        node = find_node()
        if node is None:
            self.fail(f"Node.js {NODE_MINIMUM} or newer is missing; {SETUP}")
        if not (ROOT / "node_modules" / "jsdom" / "package.json").is_file():
            self.fail(f"The page-test lane's packages are missing; {SETUP}")
        failure = run_lane(node, lane_files())
        if failure is not None:
            self.fail(failure)

    def test_lane_files_carry_no_leaked_literals(self) -> None:
        # The lane is development-only and web-specific, so a provider word is
        # allowed; every other leakage class applies as it does to the corpus.
        paths = [ROOT / "package.json", ROOT / "package-lock.json", *sorted(LANE.iterdir())]
        for path in paths:
            contents = path.read_text(encoding="utf-8")
            for class_id, pattern in LEAKAGE_PATTERNS:
                if class_id == "provider-boundary":
                    continue
                with self.subTest(path=path.name, literal=class_id):
                    self.assertIsNone(pattern.search(contents))


@unittest.skipIf(find_node() is None, "the lane's own failure modes need Node")
class PageLaneFailureTests(unittest.TestCase):
    def test_a_lane_that_hangs_is_reported_as_a_failure_rather_than_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hanging = Path(temporary) / "hangs.test.mjs"
            hanging.write_text(
                'import { test } from "node:test";\n'
                'test("hangs", () => new Promise((resolve) => setTimeout(resolve, 15000)));\n',
                encoding="utf-8",
            )
            failure = run_lane(find_node(), [hanging], timeout=2)
        self.assertIn("did not finish in 2s", failure or "")

    @unittest.skipIf(os.name == "nt", "POSIX processes")
    def test_a_lane_that_hangs_leaves_no_test_process_behind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorded = Path(temporary) / "pid"
            hanging = Path(temporary) / "hangs.test.mjs"
            hanging.write_text(
                'import { writeFileSync } from "node:fs";\n'
                'import { test } from "node:test";\n'
                f"writeFileSync({json.dumps(str(recorded))}, String(process.pid));\n"
                'test("hangs", () => new Promise((resolve) => setTimeout(resolve, 120000)));\n',
                encoding="utf-8",
            )
            failure = run_lane(find_node(), [hanging], timeout=8)
            self.assertIn("did not finish in 8s", failure or "")
            pid = int(recorded.read_text(encoding="utf-8"))

        def running() -> bool:
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
            return bool(state.stdout.strip()) and not state.stdout.strip().startswith("Z")

        self.addCleanup(lambda: running() and os.kill(pid, signal.SIGKILL))
        deadline = time.monotonic() + 5
        while running() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(running(), "the hung lane's test process outlived the timeout")

    def write(self, directory: Path, name: str, body: str) -> Path:
        path = directory / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_missing_node_fails_with_one_setup_message(self) -> None:
        environment = os.environ.copy()
        with tempfile.TemporaryDirectory() as temporary:
            # An empty PATH hides every Node installation; the interpreter
            # starts by its absolute path.
            environment["PATH"] = temporary
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "tests.test_delivery_views_page.PageLaneTests.test_page_lane_passes",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("Ran 1 test", output)
        self.assertIn("Node.js 20 or newer is missing", output)
        self.assertIn("scripts/bootstrap-test-environment.py", output)
        self.assertNotIn("ERROR:", output)

    def test_zero_executed_tests_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            empty = self.write(Path(temporary), "empty.test.mjs", "")
            self.assertEqual(run_lane(find_node(), [empty]), "No page tests ran.")

    def test_listed_file_that_runs_no_tests_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            ran = self.write(
                directory, "ran.test.mjs", 'import { test } from "node:test";\ntest("ran", () => {});\n'
            )
            empty = self.write(directory, "empty.test.mjs", "")
            failure = run_lane(find_node(), [ran, empty])
        self.assertIsNotNone(failure)
        self.assertIn("listed but ran no tests ['empty.test.mjs']", failure)

    def test_executed_file_missing_from_the_list_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.write(
                directory,
                "unlisted.test.mjs",
                'import { test } from "node:test";\ntest("unlisted", () => {});\n',
            )
            listed = self.write(
                directory,
                "listed.test.mjs",
                'import { test } from "node:test";\nimport "./unlisted.test.mjs";\n'
                'test("listed", () => {});\n',
            )
            failure = run_lane(find_node(), [listed])
        self.assertIsNotNone(failure)
        self.assertIn("ran but unlisted ['unlisted.test.mjs']", failure)

    def test_skipped_and_todo_tests_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            deferred = self.write(
                Path(temporary),
                "deferred.test.mjs",
                'import { test } from "node:test";\n'
                'test.skip("skipped page check", () => {});\n'
                'test.todo("todo page check", () => { throw new Error("broken todo"); });\n',
            )
            failure = run_lane(find_node(), [deferred])
        self.assertEqual(
            failure, "Page tests were skipped or marked todo: skipped page check, todo page check"
        )

    def test_failing_page_test_fails_the_lane(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            failing = self.write(
                Path(temporary),
                "failing.test.mjs",
                'import { test } from "node:test";\n'
                'test("breaks", () => { throw new Error("page broke"); });\n',
            )
            failure = run_lane(find_node(), [failing])
        self.assertIsNotNone(failure)
        self.assertIn("breaks: Error: page broke", failure)


if __name__ == "__main__":
    unittest.main()
