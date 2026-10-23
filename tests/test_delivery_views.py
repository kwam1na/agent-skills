"""The delivery-views rails: configuration, links, bounded commands, paths, round reports and release.

These cover the helpers every delivery-views script shares; the renderers and
snapshot adapters that use them carry their own tests.
"""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

import agent_skills
from agent_skills.receipt import validate_receipt
from agent_skills.release import ReleaseError, build_release, verify_release


ROOT = Path(__file__).resolve().parents[1]
# The product under test: this repository, or the extracted release candidate
# that qualification puts first on the path.
PRODUCT_ROOT = Path(agent_skills.__file__).resolve().parents[1]
COMMON = PRODUCT_ROOT / "skills/delivery-views/scripts/views_common.py"

_spec = importlib.util.spec_from_file_location("views_common", COMMON)
views = importlib.util.module_from_spec(_spec)
sys.modules["views_common"] = views
_spec.loader.exec_module(views)

CONFIG = {
    "spec": "delivery-views-config/1",
    "title": "Delivery",
    "tracker": {
        "name": "Jira",
        "issueUrl": "https://eshyft.atlassian.net/browse/{key}",
        "keyPattern": "^(SHFT|EDD)-[0-9]+$",
    },
    "forges": [
        {
            "name": "Bitbucket",
            "repository": "eshyft/eshyftnew",
            "pullUrl": "https://bitbucket.org/{repository}/pull-requests/{number}",
            "commitUrl": "https://bitbucket.org/{repository}/commits/{sha}",
        }
    ],
    "runs": {"argv": ["python3", "-B", ".agent-skills/current", "--root", "{root}", "harness"], "timeoutSeconds": 10},
    "findings": {"reportSpecs": ["review-round-report/1", "athena-review-round-report/1"]},
    "lenses": {"lens.security": {"name": "Security", "icon": "shield"}},
    "scrubEnv": ["JIRA_API_TOKEN", "BITBUCKET_TOKEN"],
    "exports": {"directory": "telemetry/delivery-runs", "ignore": ["delivery-record--*"]},
}

# Two reports the existing recorder wrote, verbatim but for their spec and work item.
RAISED_REPORT = {
    "candidateTreeSha": "d3f076a3a8f68433e178966f27daa3ce9d83ae57",
    "closedAt": "2026-10-23T00:42:16Z",
    "raised": [
        {"deferredIssue": None, "id": "r1-f1", "lens": "lens.outcome-correctness", "severity": "P3", "state": "open",
         "summary": "A changed-file rerun that changes a severity prints two warnings, and the second wrongly says the run lacks review.round.closed"},
        {"deferredIssue": None, "id": "r1-f2", "lens": "lens.adversarial-testing", "severity": "P2", "state": "open",
         "summary": "The fixed-without-a-commit refusal test passes on the unknown-id check, so removing the fixCommit rule goes undetected"},
        {"deferredIssue": None, "id": "r1-f3", "lens": "lens.adversarial-testing", "severity": "P3", "state": "open",
         "summary": "The duplicate resolution id refusal has no test"},
    ],
    "resolved": [],
    "round": 1,
    "roundId": "round-1",
    "runId": "run-37483ff027a5a901",
    "spec": "review-round-report/1",
    "ticket": "SHFT-2836",
}
RESOLVED_REPORT = {
    "candidateTreeSha": "ed011ee841be5d14e5449b3d8017ea7254e95a7d",
    "closedAt": "2026-10-23T00:44:16Z",
    "raised": [],
    "resolved": [
        {"deferredIssue": None, "fixCommit": "61f086d4a23b50bab1ee78433b1aa8e0e604267a", "id": "r1-f1", "state": "fixed"},
        {"deferredIssue": None, "fixCommit": "61f086d4a23b50bab1ee78433b1aa8e0e604267a", "id": "r1-f2", "state": "fixed"},
    ],
    "round": 2,
    "roundId": "round-2",
    "runId": "run-37483ff027a5a901",
    "spec": "review-round-report/1",
    "ticket": "SHFT-2836",
}


def _git_repository(directory: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(directory)], check=True)
    return directory.resolve()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class ConfigTests(unittest.TestCase):
    def refused(self, config: object, path: str) -> None:
        with self.assertRaises(views.ConfigError) as caught:
            views.validate_config(config)
        self.assertTrue(str(caught.exception).startswith(path + ":"), str(caught.exception))

    def changed(self, edit) -> dict:
        config = copy.deepcopy(CONFIG)
        edit(config)
        return config

    def test_a_complete_config_validates_and_fills_defaults(self) -> None:
        config = views.validate_config(copy.deepcopy(CONFIG))
        self.assertEqual(config["title"], "Delivery")
        self.assertEqual(config["lenses"]["lens.outcome-correctness"]["icon"], "bullseye")
        self.assertEqual(config["lenses"]["lens.adversarial-testing"]["icon"], "spy")
        self.assertEqual(config["lenses"]["lens.security"], {"name": "Security", "icon": "shield"})
        renamed = views.validate_config(self.changed(
            lambda c: c["lenses"].update({"lens.outcome-correctness": {"name": "Correctness"}})))
        self.assertEqual(renamed["lenses"]["lens.outcome-correctness"], {"name": "Correctness", "icon": "bullseye"})
        self.assertIsNone(views.validate_config(self.changed(
            lambda c: c["lenses"].update({"lens.other": {"name": "Other"}})))["lenses"]["lens.other"]["icon"])
        minimal = views.validate_config({"spec": "delivery-views-config/1"})
        self.assertEqual(minimal["title"], "Delivery")
        self.assertEqual(minimal["findings"], {"reportSpecs": ["review-round-report/1"]})
        self.assertEqual(minimal["forges"], [])
        self.assertIsNone(minimal["runs"])
        self.assertEqual(minimal["scrubEnv"], [])

    def test_refusals_name_the_key_path(self) -> None:
        cases = [
            ("", "not an object", []),
            ("spec", "wrong spec", {"spec": "delivery-views-config/2"}),
            ("colour", "unknown top-level key", self.changed(lambda c: c.update(colour="red"))),
            ("tracker.host", "unknown nested key", self.changed(lambda c: c["tracker"].update(host="x"))),
            ("forges[0].branchUrl", "unknown forge key", self.changed(lambda c: c["forges"][0].update(branchUrl="x"))),
            ("lenses.lens.security.colour", "unknown lens key",
             self.changed(lambda c: c["lenses"]["lens.security"].update(colour="red"))),
            ("tracker.issueUrl", "http template",
             self.changed(lambda c: c["tracker"].update(issueUrl="http://eshyft.atlassian.net/browse/{key}"))),
            ("tracker.issueUrl", "javascript template",
             self.changed(lambda c: c["tracker"].update(issueUrl="javascript:alert({key})"))),
            ("forges[0].pullUrl", "empty host",
             self.changed(lambda c: c["forges"][0].update(pullUrl="https:///{repository}/pull-requests/{number}"))),
            ("forges[0].commitUrl", "unknown placeholder",
             self.changed(lambda c: c["forges"][0].update(commitUrl="https://bitbucket.org/{repository}/commits/{hash}"))),
            ("tracker.issueUrl", "placeholder of another key",
             self.changed(lambda c: c["tracker"].update(issueUrl="https://eshyft.atlassian.net/{repository}/{key}"))),
            ("tracker.issueUrl", "placeholder in the host",
             self.changed(lambda c: c["tracker"].update(issueUrl="https://{key}/browse"))),
            ("runs.argv[3]", "unknown argv placeholder",
             self.changed(lambda c: c["runs"]["argv"].__setitem__(3, "{home}"))),
            ("tracker.keyPattern", "long pattern",
             self.changed(lambda c: c["tracker"].update(keyPattern="^" + "A" * 200 + "$"))),
            ("tracker.keyPattern", "pattern that does not compile",
             self.changed(lambda c: c["tracker"].update(keyPattern="^(SHFT-[0-9]+$"))),
            ("title", "oversize title", self.changed(lambda c: c.update(title="T" * 81))),
            ("tracker.name", "oversize tracker name", self.changed(lambda c: c["tracker"].update(name="N" * 41))),
            ("lenses.lens.security.name", "oversize lens name",
             self.changed(lambda c: c["lenses"]["lens.security"].update(name="N" * 41))),
            ("lenses.lens.security.icon", "unshipped icon",
             self.changed(lambda c: c["lenses"]["lens.security"].update(icon="skull"))),
            ("scrubEnv[0]", "not an environment name", self.changed(lambda c: c["scrubEnv"].__setitem__(0, "A=B"))),
            ("runs.timeoutSeconds", "zero timeout", self.changed(lambda c: c["runs"].update(timeoutSeconds=0))),
            ("exports.directory", "escaping directory",
             self.changed(lambda c: c["exports"].update(directory="../elsewhere"))),
            ("findings.reportSpecs", "empty specs", self.changed(lambda c: c["findings"].update(reportSpecs=[]))),
        ]
        for path, label, config in cases:
            with self.subTest(label):
                self.refused(config, path)

    def test_text_at_its_cap_is_accepted(self) -> None:
        views.validate_config(self.changed(lambda c: c.update(title="T" * 80)))
        views.validate_config(self.changed(lambda c: c["tracker"].update(keyPattern="^" + "A" * 198 + "$")))


class LinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = views.validate_config(copy.deepcopy(CONFIG))
        self.tracker = self.config["tracker"]
        self.forge = self.config["forges"][0]

    def test_configured_links(self) -> None:
        self.assertEqual(views.issue_link("SHFT-12", self.tracker), "https://eshyft.atlassian.net/browse/SHFT-12")
        self.assertIsNone(views.issue_link("OTHER-12", self.tracker))
        self.assertIsNone(views.issue_link("notes SHFT-12 x", {**self.tracker, "keyPattern": "SHFT-[0-9]+"}))
        sha = "0123456789abcdef0123456789abcdef01234567"
        self.assertEqual(views.commit_link(sha, self.forge), f"https://bitbucket.org/eshyft/eshyftnew/commits/{sha}")
        self.assertIsNone(views.commit_link(sha.upper(), self.forge))
        self.assertIsNone(views.commit_link("0123456", self.forge))

    def test_placeholder_values_are_encoded(self) -> None:
        template = "https://example.org/a/{key}/b"
        for value, expected in (
            ("x/y", "x%2Fy"),
            ("x?y", "x%3Fy"),
            ("x#y", "x%23y"),
            ("x%2Fy", "x%252Fy"),
            ("..x", "..x"),
        ):
            with self.subTest(value=value):
                self.assertEqual(views.fill(template, {"key": value}), f"https://example.org/a/{expected}/b")
        repository = "https://example.org/{repository}/commits/{sha}"
        self.assertEqual(views.fill(repository, {"repository": "team/repo", "sha": "a"}),
                         "https://example.org/team/repo/commits/a")
        self.assertEqual(views.fill(repository, {"repository": "team/../x?y", "sha": "a#b"}),
                         "https://example.org/team/..%2Fx%3Fy/commits/a%23b")
        # A dot segment moves a browser's path even encoded, so it renders as text.
        for values in ({"key": ".."}, {"key": "."}):
            with self.subTest(values=values):
                self.assertIsNone(views.link(template, values))
        self.assertIsNone(views.link(repository, {"repository": "../repo", "sha": "a"}))
        self.assertIsNone(views.link(repository, {"repository": "team/..", "sha": "a"}))
        self.assertIsNone(views.link(repository, {"repository": "team/repo", "sha": ".."}))
        self.assertEqual(views.link(repository, {"repository": "team/../x", "sha": "a"}),
                         "https://example.org/team/..%2Fx/commits/a")

    def test_a_produced_url_off_the_template_origin_renders_as_text(self) -> None:
        template = self.forge["pullUrl"]
        self.assertTrue(views.same_origin("https://bitbucket.org/eshyft/eshyftnew/pull-requests/3", template))
        self.assertTrue(views.same_origin("https://bitbucket.org:443/eshyft/eshyftnew/pull-requests/3", template))
        self.assertTrue(views.same_origin("https://BitBucket.org/x", template))
        for url in (
            "https://bitbucket.org.evil.com/eshyft/eshyftnew/pull-requests/3",
            "https://bitbucket.org:8443/eshyft/eshyftnew/pull-requests/3",
            "http://bitbucket.org/eshyft/eshyftnew/pull-requests/3",
            "https://user@bitbucket.org/x",
        ):
            with self.subTest(url=url):
                self.assertFalse(views.same_origin(url, template))
        # A placeholder in the host can never produce a link on another host.
        self.assertIsNone(views.link("https://{key}.atlassian.net/browse", {"key": "evil.com"}))
        self.assertIsNone(views.link("https://{key}.atlassian.net/browse", {"key": "eshyft"}))

    def test_default_port_templates_equal_portless_ones(self) -> None:
        self.assertTrue(views.same_origin("https://bitbucket.org/x", "https://bitbucket.org:443/{repository}"))
        self.assertEqual(views.link("https://bitbucket.org:443/{repository}", {"repository": "a/b"}),
                         "https://bitbucket.org:443/a/b")

    def test_change_requests(self) -> None:
        forges = self.config["forges"]
        pull = "https://bitbucket.org/eshyft/eshyftnew/pull-requests/34"
        self.assertEqual(views.change_request_link("#34", forges), pull)
        self.assertEqual(views.change_request_link("eshyftnew#34", forges), pull)
        self.assertEqual(views.change_request_link(pull, forges), pull)
        self.assertEqual(views.change_request_link("https://bitbucket.org:443/eshyft/eshyftnew/pull-requests/34", forges),
                         "https://bitbucket.org:443/eshyft/eshyftnew/pull-requests/34")
        for value in (
            "other#34",
            "#34a",
            "#",
            "https://bitbucket.org.evil.com/eshyft/eshyftnew/pull-requests/34",
            "https://bitbucket.org/eshyft/eshyftnew/pull-requests/34/../../x",
            "https://bitbucket.org/eshyft/eshyftnew/pull-requests/34?x=1",
            "javascript:alert(1)",
            "\x00https://bitbucket.org/eshyft/eshyftnew/pull-requests/34",
            " https://bitbucket.org/eshyft/eshyftnew/pull-requests/34",
            "https://bitbucket.org/eshyft/eshy\tftnew/pull-requests/34",
            "https://bitbucket.org/eshyft/eshyftnew/pull-requests/34\n",
        ):
            with self.subTest(value=value):
                self.assertIsNone(views.change_request_link(value, forges))
        self.assertIsNone(views.change_request_link("#34", []))


class BoundedCommandTests(unittest.TestCase):
    def python(self, code: str) -> list[str]:
        return [sys.executable, "-c", code]

    def test_json_output_is_returned(self) -> None:
        self.assertEqual(views.run_json(self.python("print('{\"a\": 1}')"), timeout=10), {"a": 1})

    def test_unavailable_outcomes(self) -> None:
        cases = (
            ("non-zero exit", self.python("import sys; print('{}'); sys.exit(3)"), "exited with 3"),
            ("non-JSON", self.python("print('not json')"), "not JSON"),
            ("oversize", self.python("import sys; sys.stdout.write('[' + '1,' * (9 * 1024 * 1024) + '1]')"), "16 MiB"),
            ("unstartable", ["/nonexistent/command"], "could not start"),
            ("deeply nested", self.python("print('[' * 200000)"), "not JSON"),
        )
        for label, argv, reason in cases:
            with self.subTest(label):
                with self.assertRaises(views.RunUnavailable) as caught:
                    views.run_json(argv, timeout=20)
                self.assertIn(reason, str(caught.exception))

    def test_stdin_is_closed(self) -> None:
        self.assertEqual(views.run_json(self.python("import sys, json; print(json.dumps(sys.stdin.read()))"), timeout=10), "")

    def test_scrubbed_variables_never_reach_the_child(self) -> None:
        code = "import os, json; print(json.dumps({k: os.environ.get(k) for k in ('SCRUBBED_TOKEN', 'KEPT_VALUE')}))"
        os.environ["SCRUBBED_TOKEN"] = "sentinel-secret"
        os.environ["KEPT_VALUE"] = "kept"
        try:
            seen = views.run_json(self.python(code), timeout=10, scrub=["SCRUBBED_TOKEN"])
        finally:
            del os.environ["SCRUBBED_TOKEN"], os.environ["KEPT_VALUE"]
        self.assertEqual(seen, {"SCRUBBED_TOKEN": None, "KEPT_VALUE": "kept"})

    @unittest.skipIf(os.name == "nt", "process groups are POSIX here")
    def test_a_hang_is_killed_at_the_timeout_with_no_child_left(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pid_file = Path(temporary) / "child.pid"
            code = (
                "import subprocess, sys, time\n"
                f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
                "time.sleep(60)\n"
            )
            started = time.monotonic()
            with self.assertRaises(views.RunUnavailable) as caught:
                views.run_json(self.python(code), timeout=1.5)
            self.assertLess(time.monotonic() - started, 10)
            self.assertIn("timed out", str(caught.exception))
            child = int(pid_file.read_text())
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("the hung command's child is still running")

    @unittest.skipIf(os.name == "nt", "process groups are POSIX here")
    def test_a_child_holding_stdout_open_does_not_hold_the_run(self) -> None:
        code = (
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print('{}')\n"
        )
        started = time.monotonic()
        self.assertEqual(views.run_json(self.python(code), timeout=20), {})
        self.assertLess(time.monotonic() - started, 10)

    @unittest.skipIf(os.name == "nt", "process groups are POSIX here")
    def test_a_descendant_that_left_the_group_does_not_hold_the_run(self) -> None:
        escaped = "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)\n"
        for label, tail, timeout in (("hang", "time.sleep(60)\n", 2), ("exit", "print('{}')\n", 20)):
            with self.subTest(label):
                started = time.monotonic()
                with contextlib.suppress(views.RunUnavailable):
                    views.run_json(self.python("import subprocess, sys, time\n" + escaped + tail), timeout=timeout)
                self.assertLess(time.monotonic() - started, 10)

    def test_the_runs_argv_takes_the_repository_root(self) -> None:
        config = views.validate_config(copy.deepcopy(CONFIG))
        self.assertEqual(views.runs_argv(config, Path("/repo")),
                         ["python3", "-B", ".agent-skills/current", "--root", "/repo", "harness"])
        self.assertIsNone(views.runs_argv(views.validate_config({"spec": "delivery-views-config/1"}), Path("/repo")))


class PathTests(unittest.TestCase):
    def repository(self, directory: Path, *, config: object = CONFIG, timeout: int | None = None) -> Path:
        root = _git_repository(directory / "repo")
        _write_json(root / ".agents/delivery-views.json", config)
        renderer = {"id": "line-map", "argv": ["python3", "-B", ".agent-skills/current/skills/delivery-views/scripts/line_map.py"],
                    "output": "line-map.html"}
        if timeout is not None:
            renderer["timeoutSeconds"] = timeout
        _write_json(root / ".agents/orchestration.json",
                    {"state": {"directory": "delivery", "renderers": [renderer], "publishers": []}})
        (root / "src/deep").mkdir(parents=True)
        return root

    def check(self, cwd: Path, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-B", str(COMMON), "check", *arguments], cwd=cwd,
                              capture_output=True, text=True, timeout=60)

    def test_a_script_launched_from_a_subdirectory_resolves_from_the_top_level(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.repository(Path(temporary))
            result = self.check(root / "src/deep", "--config", ".agents/delivery-views.json",
                                "--binding", ".agents/orchestration.json")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["root"], str(root))
            self.assertEqual(report["stateDirectory"], str(root / "delivery"))
            inputs = views.load_inputs(".agents/delivery-views.json", ".agents/orchestration.json", cwd=root / "src")
            self.assertEqual(inputs.root, root)
            self.assertEqual(inputs.state_directory, root / "delivery")
            self.assertEqual(inputs.config["tracker"]["name"], "Jira")

    def test_missing_config_or_binding_exits_non_zero_naming_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.repository(Path(temporary))
            for arguments, named in (
                (["--config", ".agents/absent.json", "--binding", ".agents/orchestration.json"], ".agents/absent.json"),
                (["--config", ".agents/delivery-views.json", "--binding", ".agents/nope.json"], ".agents/nope.json"),
            ):
                with self.subTest(named=named):
                    result = self.check(root, *arguments)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(named, result.stderr)
                    self.assertEqual(result.stdout, "")
            result = self.check(root, "--binding", ".agents/orchestration.json")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--config", result.stderr)

    def test_an_invalid_config_exits_non_zero_naming_the_key_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.repository(Path(temporary), config={"spec": "delivery-views-config/1", "colour": "red"})
            result = self.check(root, "--config", ".agents/delivery-views.json", "--binding", ".agents/orchestration.json")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("colour", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_outside_a_repository_the_script_names_the_cause(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.check(Path(temporary), "--config", "a.json", "--binding", "b.json")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("repository", result.stderr)

    def test_the_runs_deadline_stays_inside_the_renderer_timeout(self) -> None:
        script = "line_map.py"
        binding = lambda timeout: {"state": {"directory": "d", "renderers": [
            {"id": "line-map", "argv": ["python3", f".agent-skills/current/skills/delivery-views/scripts/{script}"],
             "output": "line-map.html", **({"timeoutSeconds": timeout} if timeout is not None else {})}]}}
        self.assertEqual(views.runs_deadline(binding(15), script, 20), 10)
        self.assertEqual(views.runs_deadline(binding(30), script, 20), 20)
        self.assertEqual(views.runs_deadline(binding(30), script, 40), 25)
        self.assertEqual(views.runs_deadline(binding(4), script, 20), 0)
        # Undeclared timeout: the state tool's default of 60 seconds.
        self.assertEqual(views.runs_deadline(binding(None), script, 20), 20)
        self.assertEqual(views.runs_deadline(binding(None), script, 90), 55)
        # A script the binding does not declare keeps the default cap.
        self.assertEqual(views.runs_deadline(binding(15), "history.py", 90), 55)

    def test_cache_and_theme_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "one"
            second = Path(temporary) / "two"
            self.assertEqual(views.runs_cache_path(first), first / ".cache/delivery-views/runs.json")
            self.assertNotEqual(views.theme_key(first), views.theme_key(second))
            self.assertRegex(views.theme_key(first), r"^delivery-views:[0-9a-f]{16}:theme$")
            cache = views.runs_cache_path(first)
            self.assertEqual(views.read_runs_cache(cache), {})
            views.write_runs_cache(cache, {"run-a": {"x": 1}})
            self.assertEqual(views.read_runs_cache(cache), {"run-a": {"x": 1}})
            self.assertEqual(json.loads(cache.read_text())["spec"], "delivery-views-runs/2")
            cache.write_text("{corrupt", encoding="utf-8")
            self.assertEqual(views.read_runs_cache(cache), {})
            cache.write_text(json.dumps({"spec": "delivery-views-runs/0", "runs": {"a": 1}}), encoding="utf-8")
            self.assertEqual(views.read_runs_cache(cache), {})
            # An unwritable cache only costs a re-read.
            blocked = Path(temporary) / "file"
            blocked.write_text("x")
            views.write_runs_cache(blocked / "runs.json", {"run-a": {}})
            self.assertEqual(sorted(p.name for p in cache.parent.iterdir()), ["runs.json"])


class RoundReportTests(unittest.TestCase):
    def test_existing_reports_validate_with_only_their_spec_changed(self) -> None:
        for report in (RAISED_REPORT, RESOLVED_REPORT):
            with self.subTest(round=report["round"]):
                self.assertIsNone(views.report_problem(report))
                self.assertIsNone(views.report_problem({**report, "spec": "athena-review-round-report/1"},
                                                       spec="athena-review-round-report/1"))

    def test_briefs_are_optional(self) -> None:
        briefed = {**RAISED_REPORT, "briefs": {
            "lens.outcome-correctness": {"brief": "brief-round-1-round-1-lens-outcome-correctness",
                                         "result": "result-round-1-round-1-lens-outcome-correctness"},
            "lens.adversarial-testing": {"brief": None, "result": None},
        }}
        self.assertIsNone(views.report_problem(briefed))
        self.assertIsNone(views.report_problem({**RAISED_REPORT, "briefs": {}}))

    def test_refusals(self) -> None:
        def raised(**changes):
            report = copy.deepcopy(RAISED_REPORT)
            report["raised"][0].update(changes)
            return report

        def resolved(**changes):
            report = copy.deepcopy(RESOLVED_REPORT)
            report["resolved"][0].update(changes)
            return report

        cases = (
            ("other spec", {**RAISED_REPORT, "spec": "athena-review-round-report/1"}, "spec"),
            ("unknown key", {**RAISED_REPORT, "extra": 1}, "extra"),
            ("missing key", {k: v for k, v in RAISED_REPORT.items() if k != "closedAt"}, "closedAt"),
            ("long ticket", {**RAISED_REPORT, "ticket": "T" * 65}, "ticket"),
            ("round zero", {**RAISED_REPORT, "round": 0}, "round"),
            ("bad tree", {**RAISED_REPORT, "candidateTreeSha": "xyz"}, "candidateTreeSha"),
            ("bad time", {**RAISED_REPORT, "closedAt": "yesterday"}, "closedAt"),
            ("out of order id", raised(id="r1-f9"), "raised[0].id"),
            ("bad severity", raised(severity="P4"), "raised[0].severity"),
            ("bad lens", raised(lens="Lens One"), "raised[0].lens"),
            ("empty summary", raised(summary=""), "raised[0].summary"),
            ("long summary", raised(summary="s" * 281), "raised[0].summary"),
            ("control in summary", raised(summary="a\nb"), "raised[0].summary"),
            ("deferred without issue", raised(state="deferred"), "raised[0].deferredIssue"),
            ("issue without deferral", raised(deferredIssue="SHFT-1"), "raised[0].deferredIssue"),
            ("fixed without commit", resolved(fixCommit=None), "resolved[0].fixCommit"),
            ("dropped with commit", resolved(state="dropped"), "resolved[0].fixCommit"),
            ("resolution of a later round", resolved(id="r2-f1"), "resolved[0].id"),
            ("resolved twice", resolved(id="r1-f2"), "resolved[1].id"),
            ("resolved deferral without issue", resolved(state="deferred", fixCommit=None), "resolved[0].deferredIssue"),
            ("issue with a line break", raised(state="deferred", deferredIssue="SHFT-1\n"), "raised[0].deferredIssue"),
            ("line separator in summary", raised(summary="a\u2028b"), "raised[0].summary"),
            ("bad briefs lens", {**RAISED_REPORT, "briefs": {"Bad Lens": {"brief": None, "result": None}}},
             "briefs.Bad Lens"),
            ("bad briefs", {**RAISED_REPORT, "briefs": {"lens.a": {"brief": "x"}}}, "briefs.lens.a"),
            ("bad artifact id", {**RAISED_REPORT, "briefs": {"lens.a": {"brief": "a b", "result": None}}},
             "briefs.lens.a.brief"),
        )
        for label, report, path in cases:
            with self.subTest(label):
                problem = views.report_problem(report)
                self.assertIsNotNone(problem)
                self.assertTrue(problem.startswith(path), problem)
        # A summary of 280 code points, beyond 280 UTF-16 units, is still within the cap.
        self.assertIsNone(views.report_problem(raised(summary="\U0001F600" * 280)))
        self.assertIsNone(views.report_problem({**RAISED_REPORT, "ticket": None}))
        deferred = raised(state="deferred", deferredIssue="SHFT-99 follow-up")
        self.assertIsNone(views.report_problem(deferred))


class ReleaseTests(unittest.TestCase):
    def build(self, directory: Path, profile: str, root: Path = ROOT):
        archive = directory / f"{profile}.zip"
        metadata = directory / f"{profile}.release.json"
        verified = build_release(root, archive, metadata, release_id=f"{profile}-test", profile_id=profile)
        return verified, archive, metadata

    def test_the_linear_archive_ships_delivery_views(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, archive, _ = self.build(Path(temporary), "linear")
            with zipfile.ZipFile(archive) as opened:
                names = set(opened.namelist())
            self.assertIn("skills/delivery-views/SKILL.md", names)
            self.assertIn("skills/delivery-views/scripts/views_common.py", names)
            self.assertIn("profiles/delivery-views.json", names)

    def test_a_delivery_views_archive_builds_verifies_and_its_receipt_validates(self) -> None:
        from agent_skills.lifecycle import Lifecycle
        from lifecycle_support import initialize_repository
        from unittest import mock

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            verified, archive, metadata = self.build(directory, "delivery-views")
            self.assertEqual(verified.profile_id, "delivery-views")
            extracted = directory / "extracted"
            verify_release(archive, metadata, extract_to=extracted)
            self.assertTrue((extracted / "skills/delivery-views/scripts/views_common.py").is_file())
            self.assertTrue((extracted / "skills/orchestrate-work/scripts/render_state.py").is_file())
            self.assertFalse((extracted / "skills/linear-tracker-adapter").exists())
            repository = initialize_repository(directory)
            with mock.patch.dict(os.environ, {"AGENT_SKILLS_EXECUTION_CONTEXT": "", "GITHUB_EVENT_NAME": ""}):
                result = Lifecycle(repository).install(archive, metadata, maintainer=True)
            self.assertEqual(result["status"]["lifecycle"], "current")
            receipt = json.loads((repository / ".agent-skills/active.json").read_text(encoding="utf-8"))
            validate_receipt(receipt)
            self.assertEqual(receipt["release"]["profile"], "delivery-views")

    def test_a_missing_included_profile_is_refused_not_skipped(self) -> None:
        import shutil

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            copy_root = directory / "corpus"
            shutil.copytree(ROOT, copy_root, ignore=shutil.ignore_patterns(".venv", ".git", ".worktrees", "__pycache__",
                                                                           "*.egg-info", "qualifications"))
            (copy_root / "profiles/delivery-views.json").unlink()
            from agent_skills import release

            with self.assertRaisesRegex(ReleaseError, "delivery-views"):
                release._release_view(copy_root.resolve(), "linear")
            _write_json(copy_root / "profiles/delivery-views.json",
                        {"id": "delivery-views", "includes": [{"x": 1}], "members": []})
            with self.assertRaisesRegex(ReleaseError, "delivery-views is malformed"):
                release._release_view(copy_root.resolve(), "linear")


if __name__ == "__main__":
    unittest.main()
