"""An outside adopter gets the delivery views from a built release with configuration only.

The adopter tracks its epics in Jira and reviews on Bitbucket. Its repository
holds no view or snapshot code of its own: only `.agents/delivery-views.json`
and the binding. It installs a built `delivery-views` release archive, not
this source tree, and every command below runs from that installation through
the binding's own argv, from inside the repository, as an operator would.
Provider answers come from recorded responses through a replay directory put
on PYTHONPATH; no credential is real.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_skills.lifecycle import Lifecycle
from agent_skills.release import build_release


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/delivery-views-adopter"
SNAPSHOTS = ROOT / "tests/fixtures/delivery-views-snapshots"
JIRA = json.loads((SNAPSHOTS / "jira.json").read_text(encoding="utf-8"))
BITBUCKET = json.loads((SNAPSHOTS / "bitbucket.json").read_text(encoding="utf-8"))
SKILL = ROOT / "skills/delivery-views/SKILL.md"
INSTALLED = ".agent-skills/current/skills"
# Stand-in credentials: they only have to be present, and must never be written anywhere.
CREDENTIALS = {"JIRA_EMAIL": "lead@shift.example", "JIRA_API_TOKEN": "jira-sentinel-4c1d",
               "BITBUCKET_EMAIL": "lead@shift.example", "BITBUCKET_TOKEN": "bitbucket-sentinel-9e2a"}
# Each token, and the Basic authorization value it is sent in.
SECRETS = tuple(secret for user, token in (("JIRA_EMAIL", "JIRA_API_TOKEN"), ("BITBUCKET_EMAIL", "BITBUCKET_TOKEN"))
                for secret in (CREDENTIALS[token],
                               base64.b64encode(f"{CREDENTIALS[user]}:{CREDENTIALS[token]}".encode()).decode()))
SEARCH = "https://eshyft.atlassian.net/rest/api/3/search/jql?"
API = "https://api.bitbucket.org/2.0/repositories/eshyft/eshyftnew/"


class OutsideAdopter:
    """The adopter's repository with the release installed, and how its operator runs things."""

    def __init__(self, root: Path, archive: Path, metadata: Path) -> None:
        self.root = root
        self.repository = root / "shift planner"
        shutil.copytree(FIXTURE / "repository", self.repository)
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.email=lead@shift.example", "-c", "user.name=Lead", "commit", "-qm", "Initial")
        with mock.patch.dict(os.environ, {"AGENT_SKILLS_EXECUTION_CONTEXT": "", "GITHUB_EVENT_NAME": ""}):
            Lifecycle(self.repository).install(archive, metadata, maintainer=True)
        # The documented argv starts with python3; this one is the interpreter under test.
        bin_directory = root / "bin"
        bin_directory.mkdir()
        (bin_directory / "python3").symlink_to(sys.executable)
        self.log = root / "requests.jsonl"
        self.environment = {
            **{key: value for key, value in os.environ.items() if key not in CREDENTIALS},
            **CREDENTIALS,
            "PATH": f"{bin_directory}{os.pathsep}{os.environ.get('PATH', '')}",
            "PYTHONPATH": str(FIXTURE / "replay"),
        }
        self.binding = json.loads((self.repository / ".agents/orchestration.json").read_text(encoding="utf-8"))
        self.state = self.repository / self.binding["state"]["directory"]

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.repository, check=True, capture_output=True)

    def installed(self, relative: str) -> Path:
        return self.repository / INSTALLED / relative

    def run(self, argv: list[str], *, cwd: Path | None = None, replay: list[dict] | None = None,
            expect: int = 0) -> subprocess.CompletedProcess:
        environment = dict(self.environment)
        if replay is not None:
            script = self.root / "replay.json"
            script.write_text(json.dumps({"exchanges": replay, "log": str(self.log)}), encoding="utf-8")
            environment["DELIVERY_VIEWS_REPLAY"] = str(script)
        completed = subprocess.run(argv, cwd=cwd or self.repository, env=environment, capture_output=True, text=True,
                                   timeout=120)
        if completed.returncode != expect:
            raise AssertionError(f"{argv[:4]} exited {completed.returncode}: {completed.stdout[-2000:]} "
                                 f"{completed.stderr[-2000:]}")
        return completed

    def state_tool(self, *args: str, replay: list[dict] | None = None) -> dict:
        completed = self.run(["python3", "-B", str(self.installed("orchestrate-work/scripts/delivery_state.py")),
                              "--dir", str(self.state), *args], replay=replay)
        return json.loads(completed.stdout)

    def requests(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


def _exchange(url: str, body: object, status: int = 200, method: str = "GET") -> dict:
    return {"method": method, "url": url, "status": status, "headers": {}, "body": body}


class OutsideAdopterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build = Path(tempfile.mkdtemp(prefix="delivery views release "))
        cls.archive = cls.build / "delivery-views.zip"
        cls.metadata = cls.build / "delivery-views.release.json"
        build_release(ROOT, cls.archive, cls.metadata, release_id="outside-adopter", profile_id="delivery-views")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.build, True)

    def setUp(self) -> None:
        self.temporary = Path(tempfile.mkdtemp(prefix="outside adopter "))
        self.addCleanup(shutil.rmtree, self.temporary, True)
        self.adopter = OutsideAdopter(self.temporary, self.archive, self.metadata)

    def test_the_repository_holds_configuration_only(self) -> None:
        own = sorted(str(path.relative_to(FIXTURE / "repository")) for path in (FIXTURE / "repository").rglob("*")
                     if path.is_file())
        self.assertEqual(own, [".agents/delivery-views.json", ".agents/orchestration.json", "README.md", "src/app.js"])
        for path in (FIXTURE / "repository").rglob("*"):
            if path.is_file():
                self.assertNotRegex(path.read_text(encoding="utf-8").lower(), r"athena|linear|github")
        # Everything the binding runs for the views comes from the installed release.
        state = self.adopter.binding["state"]
        for argv, script in ((state["trackerSnapshot"]["argv"], "snapshot.py"),
                             (state["forgeSnapshot"]["argv"], "snapshot.py"),
                             *((renderer["argv"], "render.py") for renderer in state["renderers"])):
            self.assertEqual(argv[:3], ["python3", "-B", f"{INSTALLED}/delivery-views/scripts/{script}"])
            self.assertTrue((self.adopter.repository / argv[2]).is_file())

    def test_the_binding_and_configuration_validate_from_the_installed_release(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "installed_delivery_state", self.adopter.installed("orchestrate-work/scripts/delivery_state.py"))
        installed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installed)
        self.assertEqual(installed.validate(self.adopter.binding, installed.load_schema("orchestration-binding/1")), [])
        # The configuration check runs from a subdirectory: paths resolve from the repository top level.
        checked = self.adopter.run(["python3", "-B", str(self.adopter.installed("delivery-views/scripts/views_common.py")),
                                    "check", "--config", ".agents/delivery-views.json", "--binding",
                                    ".agents/orchestration.json"], cwd=self.adopter.repository / "src")
        self.assertEqual(json.loads(checked.stdout)["stateDirectory"], str(self.adopter.state.resolve()))

    def test_both_snapshots_run_through_the_binding_and_reconcile_into_the_record(self) -> None:
        adopter = self.adopter
        adopter.state_tool("init", "--headline", "Shift planner epic")
        version = adopter.state_tool("show")["state"]["version"]
        adopter.state_tool("patch", "--writer", "lead", "--version", str(version), "--patch",
                           json.dumps({"meta": {"trackerParent": "SHFT-10"}}))
        snapshots = adopter.root / "snapshots"
        snapshots.mkdir()

        taken = adopter.state_tool("tracker-snapshot", "--binding", ".agents/orchestration.json", "--output",
                                   str(snapshots / "tracker.json"),
                                   replay=[_exchange(SEARCH, JIRA["page-1"]), _exchange(SEARCH, JIRA["page-2"])])
        self.assertTrue(taken["taken"], taken)
        tracker = json.loads((snapshots / "tracker.json").read_text(encoding="utf-8"))
        self.assertEqual({item["subject"]: item["state"] for item in tracker["items"]},
                         {"SHFT-11": "backlog", "SHFT-12": "started", "SHFT-13": "completed", "SHFT-14": "canceled"})
        reconciled = adopter.state_tool("reconcile", "--snapshot", str(snapshots / "tracker.json"))
        # The record takes in the tracker by its own rules: an open ticket becomes a follow-up, a started one
        # planned work, a completed one a report, and a canceled one nothing.
        self.assertEqual([change["subject"] for change in reconciled["changes"]], ["SHFT-11", "SHFT-12"])
        self.assertEqual([report["subject"] for report in reconciled["reports"]], ["SHFT-13"])
        record = adopter.state_tool("show")["state"]
        self.assertEqual([item["subject"] for item in record["planned"]], ["SHFT-12"])
        self.assertEqual([item["id"] for item in record["followups"]], ["SHFT-11"])

        taken = adopter.state_tool("forge-snapshot", "--binding", ".agents/orchestration.json", "--output",
                                   str(snapshots / "forge.json"), replay=[
                                       _exchange(API + "pullrequests?", BITBUCKET["page-1"]),
                                       _exchange(API + "pullrequests?page=2", BITBUCKET["page-2"]),
                                       _exchange(API + "commit/aaaaaaaaaaaa", BITBUCKET["commit-a"]),
                                       _exchange(API + "commit/cccccccccccc", BITBUCKET["commit-c"]),
                                   ])
        self.assertTrue(taken["taken"], taken)
        forge = json.loads((snapshots / "forge.json").read_text(encoding="utf-8"))
        self.assertEqual({item["subject"]: (item["state"], item.get("changeRequest")) for item in forge["items"]}, {
            "SHFT-11": ("merged", "https://bitbucket.org/eshyft/eshyftnew/pull-requests/31"),
            "SHFT-12": ("open", "https://bitbucket.org/eshyft/eshyftnew/pull-requests/34"),
        })
        reconciled = adopter.state_tool("reconcile", "--snapshot", str(snapshots / "tracker.json"), "--snapshot",
                                        str(snapshots / "forge.json"))
        self.assertTrue(reconciled["ok"], reconciled)
        record = adopter.state_tool("show")["state"]
        planned = {item["subject"]: item for item in record["queue"] + record["planned"]}
        self.assertEqual(planned["SHFT-12"].get("changeRequest"),
                         "https://bitbucket.org/eshyft/eshyftnew/pull-requests/34")

        # Every recorded answer was asked for, in order, with credentials sent only to each provider's own
        # origin; and no credential, raw or encoded, was written anywhere.
        requests = adopter.requests()
        self.assertEqual([request["authorization"] for request in requests], ["Basic"] * 6)
        for request, prefix in zip(requests, (SEARCH, SEARCH, API + "pullrequests?", API + "pullrequests?page=2",
                                              API + "commit/aaaaaaaaaaaa", API + "commit/cccccccccccc")):
            self.assertTrue(request["url"].startswith(prefix), request["url"])
        self.assertNotIn("nextPageToken", requests[0]["url"])
        self.assertIn("nextPageToken=token-page-2", requests[1]["url"])
        self.assertEqual({item["subject"]: item.get("head") for item in forge["items"]},
                         {"SHFT-11": "a" * 40, "SHFT-12": "c" * 24 + "0123456789abcdef"})
        self.assert_no_credential(adopter.state, snapshots, adopter.log)

    def test_a_failing_provider_keeps_the_last_snapshot_and_names_no_credential(self) -> None:
        adopter = self.adopter
        adopter.state_tool("init", "--headline", "Shift planner epic")
        version = adopter.state_tool("show")["state"]["version"]
        adopter.state_tool("patch", "--writer", "lead", "--version", str(version), "--patch",
                           json.dumps({"meta": {"trackerParent": "SHFT-10"}}))
        output = adopter.root / "tracker.json"
        result = adopter.state_tool("tracker-snapshot", "--binding", ".agents/orchestration.json", "--output",
                                    str(output), replay=[_exchange(SEARCH, JIRA["unauthorized"], status=401)])
        self.assertFalse(result["taken"])
        self.assertFalse(output.exists())
        for secret in SECRETS:
            self.assertNotIn(secret, json.dumps(result))
        self.assert_no_credential(adopter.state)

    def assert_no_credential(self, *places: Path) -> None:
        for place in places:
            for path in [place, *place.rglob("*")] if place.is_dir() else [place]:
                if path.is_file():
                    contents = path.read_text(encoding="utf-8", errors="replace")
                    for secret in SECRETS:
                        self.assertNotIn(secret, contents, path.name)

    def recorded(self, *, hostile: str | None = None) -> None:
        """The record after both snapshots, taken through the binding, are reconciled into it."""
        adopter = self.adopter
        jira, bitbucket = json.loads(json.dumps(JIRA)), json.loads(json.dumps(BITBUCKET))
        if hostile is not None:
            jira["page-1"]["issues"][1]["fields"]["summary"] = hostile
            jira["page-1"]["issues"][0]["fields"]["description"] = {
                "type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": hostile}]}]}
            bitbucket["page-1"]["values"][2]["title"] = f"SHFT-12 {hostile}"
        adopter.state_tool("init", "--headline", "Shift planner epic")
        version = adopter.state_tool("show")["state"]["version"]
        adopter.state_tool("patch", "--writer", "lead", "--version", str(version), "--patch",
                           json.dumps({"meta": {"trackerParent": "SHFT-10"}}))
        snapshots = adopter.root / "snapshots"
        snapshots.mkdir(exist_ok=True)
        adopter.state_tool("tracker-snapshot", "--binding", ".agents/orchestration.json", "--output",
                           str(snapshots / "tracker.json"),
                           replay=[_exchange(SEARCH, jira["page-1"]), _exchange(SEARCH, jira["page-2"])])
        adopter.state_tool("reconcile", "--snapshot", str(snapshots / "tracker.json"))
        adopter.state_tool("forge-snapshot", "--binding", ".agents/orchestration.json", "--output",
                           str(snapshots / "forge.json"), replay=[
                               _exchange(API + "pullrequests?", bitbucket["page-1"]),
                               _exchange(API + "pullrequests?page=2", bitbucket["page-2"]),
                               _exchange(API + "commit/aaaaaaaaaaaa", bitbucket["commit-a"]),
                               _exchange(API + "commit/cccccccccccc", bitbucket["commit-c"]),
                           ])
        adopter.state_tool("reconcile", "--snapshot", str(snapshots / "tracker.json"), "--snapshot",
                           str(snapshots / "forge.json"))

    def open_page(self, page: Path, hostile: str) -> dict:
        node = shutil.which("node")
        if node is None:
            self.fail("node is not installed: run python3 scripts/bootstrap-test-environment.py before running the suite.")
        completed = subprocess.run([node, str(FIXTURE / "open-page.mjs"), str(page), hostile], cwd=ROOT,
                                   capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def rendered(self, hostile: str) -> None:
        """Both pages, rendered from the record by the binding's renderers."""
        self.recorded(hostile=hostile)
        adopter = self.adopter
        # The render wrapper runs the binding's renderers from where the operator starts it: the top level,
        # since the binding names each script by its path in the repository.
        rendered = adopter.run(["python3", "-B", str(adopter.installed("orchestrate-work/scripts/render_state.py")),
                                "--dir", str(adopter.state), "--binding", ".agents/orchestration.json"])
        result = json.loads(rendered.stdout)
        self.assertTrue(result["ok"], rendered.stdout)
        self.assertEqual([renderer["id"] for renderer in result["renderers"]], ["line-map", "history"])

    def test_the_line_map_renders_from_the_release_with_only_tracker_and_forge_links(self) -> None:
        hostile = "</script><img src=x onerror=alert(1)> <b>bold</b> & \"quoted\" <!--"
        self.rendered(hostile)
        adopter = self.adopter
        page = adopter.state / "line-map.html"
        text = page.read_text(encoding="utf-8")
        self.assertIn('<meta name="delivery-state-live">', text)
        self.assertIn("<title>Shift planner delivery</title>", text)
        self.assertNotIn("</script><img", text)
        shown = self.open_page(page, hostile)
        # The planned piece opens a detail; the follow-up is listed with its ticket's link.
        self.assertEqual(shown["subjects"], ["SHFT-12"])
        # Besides its own chapters, the line map links only the history page beside it and Jira and Bitbucket.
        self.assertIn("delivery-history.html", shown["hrefs"])
        outside = [href for href in shown["hrefs"] if not href.startswith("#") and href != "delivery-history.html"]
        self.assertTrue(outside)
        for href in outside:
            with self.subTest(href=href):
                self.assertRegex(href, r"^https://(eshyft\.atlassian\.net/browse/(SHFT|EDD)-[0-9]+"
                                       r"|bitbucket\.org/eshyft/eshyftnew/(pull-requests/[0-9]+|commits/[0-9a-f]{40}))$")
        for key in ("SHFT-10", "SHFT-11", "SHFT-12"):
            self.assertIn(f"https://eshyft.atlassian.net/browse/{key}", outside)
        self.assertIn("https://bitbucket.org/eshyft/eshyftnew/pull-requests/34", outside)
        # The hostile titles and summaries reached the reader as text, and no markup in them became an element.
        self.assertTrue(shown["shownAsText"])
        self.assertNotIn(hostile, shown["head"])
        # Rendering ran with the credentials exported: nothing it wrote holds one.
        self.assert_no_credential(adopter.state)
        self.assertEqual(shown["elements"], 0)

    def test_the_history_page_renders_from_the_release_linked_with_the_line_map(self) -> None:
        hostile = "</script><img src=x onerror=alert(1)> <b>bold</b> & \"quoted\" <!--"
        self.rendered(hostile)
        adopter = self.adopter
        page = adopter.state / "delivery-history.html"
        text = page.read_text(encoding="utf-8")
        self.assertIn('<meta name="delivery-state-live">', text)
        self.assertNotIn("</script><img", text)
        shown = self.open_page(page, hostile)
        # With no runs command and no exports, the history lists nothing and links only back to the line map.
        # The record's text never reaches this page, so the checks below guard it rather than prove inertness;
        # the line map's test proves the record's hostile text stays inert.
        self.assertEqual(shown["hrefs"], ["line-map.html"])
        self.assertNotIn("SHFT-12", text)
        self.assertNotIn(hostile, shown["head"])
        self.assertEqual(shown["elements"], 0)
        self.assert_no_credential(adopter.state)


class AdopterGuideTests(unittest.TestCase):
    """The skill's guide shows this adopter's configuration and commands as they are."""

    def blocks(self) -> list[object]:
        text = SKILL.read_text(encoding="utf-8")
        return [json.loads(block) for block in re.findall(r"```json\n(.*?)```", text, re.S)
                if block.lstrip().startswith("{")]

    def test_the_jira_and_bitbucket_example_is_the_fixture(self) -> None:
        blocks = self.blocks()
        config = json.loads((FIXTURE / "repository/.agents/delivery-views.json").read_text(encoding="utf-8"))
        self.assertIn(config, blocks)
        self.assertEqual(config["scrubEnv"], ["JIRA_API_TOKEN", "JIRA_EMAIL", "BITBUCKET_TOKEN", "BITBUCKET_EMAIL"])
        state = json.loads((FIXTURE / "repository/.agents/orchestration.json").read_text(encoding="utf-8"))["state"]
        declared = [block for block in blocks if isinstance(block, dict) and "trackerSnapshot" in block]
        self.assertIn({key: state[key] for key in ("renderers", "liveHome", "trackerSnapshot", "forgeSnapshot")},
                      declared)

    def test_the_linear_and_github_example_validates(self) -> None:
        spec = importlib.util.spec_from_file_location("views_common_for_guide",
                                                      ROOT / "skills/delivery-views/scripts/views_common.py")
        views = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = views
        spec.loader.exec_module(views)
        configs = [block for block in self.blocks() if isinstance(block, dict) and "spec" in block]
        trackers = {config["tracker"]["name"] for config in configs}
        self.assertEqual(trackers, {"Jira", "Linear"})
        for config in configs:
            with self.subTest(tracker=config["tracker"]["name"]):
                views.validate_config(config)
        declared = [block for block in self.blocks() if isinstance(block, dict) and "trackerSnapshot" in block]
        kinds = {tuple(block[source]["argv"][block[source]["argv"].index("--kind") + 1] for source in
                       ("trackerSnapshot", "forgeSnapshot")) for block in declared}
        self.assertEqual(kinds, {("jira", "bitbucket"), ("linear", "github")})
        # Each documented snapshot command is one the shipped script accepts.
        snapshot = importlib.util.spec_from_file_location("snapshot_for_guide",
                                                          ROOT / "skills/delivery-views/scripts/snapshot.py")
        sys.path.insert(0, str(ROOT / "skills/delivery-views/scripts"))
        try:
            snapshot_module = importlib.util.module_from_spec(snapshot)
            sys.modules[snapshot.name] = snapshot_module
            snapshot.loader.exec_module(snapshot_module)
            for block in declared:
                for source in ("trackerSnapshot", "forgeSnapshot"):
                    with self.subTest(source=source):
                        snapshot_module._parser().parse_args(block[source]["argv"][3:])
        finally:
            sys.path.remove(str(ROOT / "skills/delivery-views/scripts"))
        # Each documented state block is one the render wrapper accepts.
        spec = importlib.util.spec_from_file_location("render_state_for_guide",
                                                      ROOT / "skills/orchestrate-work/scripts/render_state.py")
        render_state = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = render_state
        sys.path.insert(0, str(ROOT / "skills/orchestrate-work/scripts"))
        try:
            spec.loader.exec_module(render_state)
        finally:
            sys.path.remove(str(ROOT / "skills/orchestrate-work/scripts"))
        for block in declared:
            renderers, _ = render_state.parse_config({"directory": "delivery", "publishers": [], **block})
            self.assertEqual([renderer["id"] for renderer in renderers], ["line-map", "history"])


if __name__ == "__main__":
    unittest.main()
