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
CREDENTIALS = {"JIRA_EMAIL": "lead@app.example", "JIRA_API_TOKEN": "jira-sentinel-4c1d",
               "BITBUCKET_EMAIL": "lead@app.example", "BITBUCKET_TOKEN": "bitbucket-sentinel-9e2a"}
# Each token, and the Basic authorization value it is sent in.
SECRETS = tuple(secret for user, token in (("JIRA_EMAIL", "JIRA_API_TOKEN"), ("BITBUCKET_EMAIL", "BITBUCKET_TOKEN"))
                for secret in (CREDENTIALS[token],
                               base64.b64encode(f"{CREDENTIALS[user]}:{CREDENTIALS[token]}".encode()).decode()))
SEARCH = "https://example.atlassian.net/rest/api/3/search/jql?"
PARENT = "https://example.atlassian.net/rest/api/3/issue/APP-10?fields=status"
API = "https://api.bitbucket.org/2.0/repositories/example/app/"


class OutsideAdopter:
    """The adopter's repository with the release installed, and how its operator runs things."""

    def __init__(self, root: Path, archive: Path, metadata: Path) -> None:
        self.root = root
        self.repository = root / "example app"
        shutil.copytree(FIXTURE / "repository", self.repository)
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.email=lead@app.example", "-c", "user.name=Lead", "commit", "-qm", "Initial")
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


def expect_taken(case: unittest.TestCase, result: dict) -> None:
    """A snapshot step's result says it took the snapshot: it exits 0 even when its command failed."""
    case.assertTrue(result.get("ok") and result.get("taken"), result)


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
        own = sorted(path.relative_to(FIXTURE / "repository").as_posix() for path in (FIXTURE / "repository").rglob("*")
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
        adopter.state_tool("init", "--headline", "Example app epic")
        version = adopter.state_tool("show")["state"]["version"]
        adopter.state_tool("patch", "--writer", "lead", "--version", str(version), "--patch",
                           json.dumps({"meta": {"trackerParent": "APP-10"}}))
        snapshots = adopter.root / "snapshots"
        snapshots.mkdir()

        # The replay answers the children's pages only: the parent's own issue, read once after them, is unreachable.
        # That leaves the parent's state unknown and still takes the children's snapshot.
        taken = adopter.state_tool("tracker-snapshot", "--binding", ".agents/orchestration.json", "--output",
                                   str(snapshots / "tracker.json"),
                                   replay=[_exchange(SEARCH, JIRA["page-1"]), _exchange(SEARCH, JIRA["page-2"])])
        self.assertTrue(taken["taken"], taken)
        self.assertEqual([request["url"] for request in adopter.requests()][-1], PARENT)
        tracker = json.loads((snapshots / "tracker.json").read_text(encoding="utf-8"))
        self.assertNotIn("parent", tracker)
        self.assertEqual({item["subject"]: item["state"] for item in tracker["items"]},
                         {"APP-11": "backlog", "APP-12": "started", "APP-13": "completed", "APP-14": "canceled"})
        reconciled = adopter.state_tool("reconcile", "--snapshot", str(snapshots / "tracker.json"))
        # The record takes in the tracker by its own rules: an open ticket becomes a follow-up, a started one
        # planned work, a completed one a report, and a canceled one nothing.
        self.assertEqual([change["subject"] for change in reconciled["changes"]], ["APP-11", "APP-12"])
        self.assertEqual([report["subject"] for report in reconciled["reports"]], ["APP-13"])
        record = adopter.state_tool("show")["state"]
        self.assertEqual([item["subject"] for item in record["planned"]], ["APP-12"])
        self.assertEqual([item["id"] for item in record["followups"]], ["APP-11"])

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
            "APP-11": ("merged", "https://bitbucket.org/example/app/pull-requests/31"),
            "APP-12": ("open", "https://bitbucket.org/example/app/pull-requests/34"),
        })
        reconciled = adopter.state_tool("reconcile", "--snapshot", str(snapshots / "tracker.json"), "--snapshot",
                                        str(snapshots / "forge.json"))
        self.assertTrue(reconciled["ok"], reconciled)
        record = adopter.state_tool("show")["state"]
        planned = {item["subject"]: item for item in record["queue"] + record["planned"]}
        self.assertEqual(planned["APP-12"].get("changeRequest"),
                         "https://bitbucket.org/example/app/pull-requests/34")

        # Every recorded answer was asked for, in order, and the parent's issue once after the children, with
        # credentials sent only to each provider's own origin; and no credential, raw or encoded, was written anywhere.
        requests = adopter.requests()
        prefixes = (SEARCH, SEARCH, PARENT, API + "pullrequests?", API + "pullrequests?page=2",
                    API + "commit/aaaaaaaaaaaa", API + "commit/cccccccccccc")
        self.assertEqual([request["authorization"] for request in requests], ["Basic"] * len(prefixes))
        for request, prefix in zip(requests, prefixes, strict=True):
            self.assertTrue(request["url"].startswith(prefix), request["url"])
        self.assertNotIn("nextPageToken", requests[0]["url"])
        self.assertIn("nextPageToken=token-page-2", requests[1]["url"])
        self.assertEqual({item["subject"]: item.get("head") for item in forge["items"]},
                         {"APP-11": "a" * 40, "APP-12": "c" * 24 + "0123456789abcdef"})
        self.assert_no_credential(adopter.state, snapshots, adopter.log)

    def test_a_failing_provider_keeps_the_last_snapshot_and_names_no_credential(self) -> None:
        adopter = self.adopter
        adopter.state_tool("init", "--headline", "Example app epic")
        version = adopter.state_tool("show")["state"]["version"]
        adopter.state_tool("patch", "--writer", "lead", "--version", str(version), "--patch",
                           json.dumps({"meta": {"trackerParent": "APP-10"}}))
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
            bitbucket["page-1"]["values"][2]["title"] = f"APP-12 {hostile}"
        adopter.state_tool("init", "--headline", "Example app epic")
        version = adopter.state_tool("show")["state"]["version"]
        adopter.state_tool("patch", "--writer", "lead", "--version", str(version), "--patch",
                           json.dumps({"meta": {"trackerParent": "APP-10"}}))
        snapshots = adopter.root / "snapshots"
        snapshots.mkdir(exist_ok=True)
        # The children's pages, then the parent's own issue, which the snapshot reads once by its key. The step is
        # best-effort and exits 0 when its command fails, so whether it took the snapshot is read from its result.
        taken = adopter.state_tool("tracker-snapshot", "--binding", ".agents/orchestration.json", "--output",
                                   str(snapshots / "tracker.json"), replay=[
                                       _exchange(SEARCH, jira["page-1"]), _exchange(SEARCH, jira["page-2"]),
                                       _exchange(PARENT, {"key": "APP-10", "fields": {"status": {
                                           "name": "In Progress", "statusCategory": {"key": "indeterminate"}}}})])
        expect_taken(self, taken)
        adopter.state_tool("reconcile", "--snapshot", str(snapshots / "tracker.json"))
        taken = adopter.state_tool("forge-snapshot", "--binding", ".agents/orchestration.json", "--output",
                                   str(snapshots / "forge.json"), replay=[
                                       _exchange(API + "pullrequests?", bitbucket["page-1"]),
                                       _exchange(API + "pullrequests?page=2", bitbucket["page-2"]),
                                       _exchange(API + "commit/aaaaaaaaaaaa", bitbucket["commit-a"]),
                                       _exchange(API + "commit/cccccccccccc", bitbucket["commit-c"]),
                                   ])
        expect_taken(self, taken)
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
        self.assertIn("<title>Example app delivery</title>", text)
        self.assertNotIn("</script><img", text)
        shown = self.open_page(page, hostile)
        # The planned piece opens a detail; the follow-up is listed with its ticket's link.
        self.assertEqual(shown["subjects"], ["APP-12"])
        # Besides its own chapters, the line map links only the history page beside it and Jira and Bitbucket.
        self.assertIn("delivery-history.html", shown["hrefs"])
        outside = [href for href in shown["hrefs"] if not href.startswith("#") and href != "delivery-history.html"]
        self.assertTrue(outside)
        for href in outside:
            with self.subTest(href=href):
                self.assertRegex(href, r"^https://(example\.atlassian\.net/browse/(APP|ABC)-[0-9]+"
                                       r"|bitbucket\.org/example/app/(pull-requests/[0-9]+|commits/[0-9a-f]{40}))$")
        for key in ("APP-10", "APP-11", "APP-12"):
            self.assertIn(f"https://example.atlassian.net/browse/{key}", outside)
        self.assertIn("https://bitbucket.org/example/app/pull-requests/34", outside)
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
        self.assertNotIn("APP-12", text)
        self.assertNotIn(hostile, shown["head"])
        self.assertEqual(shown["elements"], 0)
        self.assert_no_credential(adopter.state)


    def test_two_installed_adopters_publish_isolated_cuts_and_archive_status_without_credentials(self):
        """Exact installed bytes; only HTTPS socket connection is a loopback fixture seam."""
        import hashlib
        import threading
        import zipfile
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from tests.test_delivery_views_hosted import FakeAPI, hosted as receiver_fixture
        first=self.adopter
        second_root=self.temporary/'second';second_root.mkdir()
        second=OutsideAdopter(second_root,self.archive,self.metadata)
        tokens={'repo_one':'SYNTHETIC_INSTALLED_ONE_'+'a'*40,'repo_two':'SYNTHETIC_INSTALLED_TWO_'+'b'*40}
        apis={key:FakeAPI() for key in tokens};requests=[];errors=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):self.api()
            def do_POST(self):self.api()
            def api(self):
                try:
                    parts=self.path.split('/');repo=parts[4];operation=parts[-1]
                    body=self.rfile.read(int(self.headers.get('Content-Length',0))) or None
                    requests.append((self.path,dict(self.headers),body))
                    if self.headers.get('Authorization')!='Bearer '+tokens[repo]:
                        self.send_response(401);self.end_headers();self.wfile.write(b'{"error":"unauthorized"}');return
                    try:answer=apis[repo](self.command,operation,body,self.headers)
                    except receiver_fixture.PublishError as error:
                        self.send_response(404);self.end_headers();self.wfile.write(json.dumps({'error':error.code}).encode());return
                    self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                    self.wfile.write(json.dumps(answer).encode())
                except Exception as error:
                    errors.append(error);self.send_error(500)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        # External test harness, never an installed-file edit or adopter implementation.
        seam=self.temporary/'socket-seam';seam.mkdir()
        (seam/'sitecustomize.py').write_text(
            'import http.client\nReal=http.client.HTTPConnection\n'
            f'def fixture(host,port=None,timeout=None,context=None):\n assert host=="deliveries.example.com"\n return Real("127.0.0.1",{server.server_port},timeout=timeout)\n'
            'http.client.HTTPSConnection=fixture\n')
        driver='''import importlib.util,json,sys
from pathlib import Path
from types import SimpleNamespace
path=Path(sys.argv[1]);spec=importlib.util.spec_from_file_location('installed_hosted',path)
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
a=SimpleNamespace(config='.agents/delivery-views.json',binding='.agents/orchestration.json',key='APP-1',state_dir=None)
i=h.load_inputs(a)
p=h.Publisher(i.state_directory,i.config['hosted'],a.key,secret_values=[__import__('os').environ['FIXTURE_PUBLISH_TOKEN']])
p.api=h.child_transport(a,i)
print(json.dumps(p.step(lambda:h.child_collect(a,i))))
'''
        identities=[];fingerprints=[];snapshots=[];receipts=[]
        archive_sha=hashlib.sha256(self.archive.read_bytes()).hexdigest()
        with zipfile.ZipFile(self.archive) as archive:
            names=archive.namelist()
            for name in ('hosted.py','public_projection.py'):
                self.assertIn('skills/delivery-views/scripts/'+name,names)
            self.assertIn('skills/delivery-views/references/hosted-snapshot.schema.json',names)
            self.assertFalse(any(name.startswith(('hosted/','node_modules/','.orchestration/','.agent-skills/'))
                                 or name.endswith(('.dev.vars','.env','package-lock.json','hosted-identity.json',
                                                   'hosted-status.json')) for name in names))
            for name in names:
                for token in (*tokens.values(),*SECRETS):self.assertNotIn(token.encode(),archive.read(name),name)
        for adopter,repo in ((first,'repo_one'),(second,'repo_two')):
            config_path=adopter.repository/'.agents/delivery-views.json'
            config=json.loads(config_path.read_text())
            config['hosted']={'origin':'https://deliveries.example.com','repositoryId':repo,'tokenEnv':'FIXTURE_PUBLISH_TOKEN'}
            config_path.write_text(json.dumps(config))
            adopter.state_tool('init','--headline',repo)
            env=dict(adopter.environment);env.update(FIXTURE_PUBLISH_TOKEN=tokens[repo],PYTHONPATH=str(seam))
            before={p.relative_to(adopter.repository/'.agent-skills/current').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (adopter.repository/'.agent-skills/current').rglob('*') if p.is_file()}
            completed=subprocess.run([sys.executable,'-B','-c',driver,str(adopter.installed('delivery-views/scripts/hosted.py'))],
                cwd=adopter.repository,env=env,capture_output=True,text=True,timeout=45)
            self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
            answer=json.loads(completed.stdout)
            self.assertEqual(answer['state'],'current')
            identity=json.loads((adopter.state/'hosted-identity.json').read_text())
            identities.append(identity['publicId'])
            payload=next(call[2] for call in apis[repo].calls if call[1]=='snapshots')
            snapshots.append(payload);fingerprints.append(json.loads(payload)['sourceFingerprint'])
            self.assertEqual(answer['receipt']['digest'],hashlib.sha256(payload).hexdigest())
            # Same identity survives a second exact collection, which confirms instead of uploading.
            again=subprocess.run([sys.executable,'-B','-c',driver,str(adopter.installed('delivery-views/scripts/hosted.py'))],
                cwd=adopter.repository,env=env,capture_output=True,text=True,timeout=45)
            self.assertEqual(again.returncode,0,again.stdout+again.stderr)
            self.assertEqual(apis[repo].calls[-1][1],'confirm')
            archive=adopter.repository/'archived';shutil.copytree(adopter.state,archive)
            # The ordinary installed status command reads copied archive sidecars and starts no owner.
            adopter.environment={key:value for key,value in adopter.environment.items() if key not in CREDENTIALS}
            before_status=len(requests)
            status=adopter.run([sys.executable,'-B',str(adopter.installed('delivery-views/scripts/hosted.py')),'status',
                '--config','.agents/delivery-views.json','--binding','.agents/orchestration.json',
                '--state-dir',str(archive),'--key','APP-1'])
            self.assertIn(identity['publicId'],status.stdout)
            self.assertEqual(len(requests),before_status)
            self.assertFalse((archive/'hosted-owner.json').exists())
            for folder in (archive,adopter.repository/'.agents'):
                for path in folder.rglob('*'):
                    if path.is_file():
                        for token in (*tokens.values(),*SECRETS):self.assertNotIn(token.encode(),path.read_bytes(),str(path))
            after={p.relative_to(adopter.repository/'.agent-skills/current').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in (adopter.repository/'.agent-skills/current').rglob('*') if p.is_file()}
            self.assertEqual(before,after)
            self.assertEqual(Lifecycle(adopter.repository).status().blockers,())
            receipt=json.loads((adopter.repository/'.agent-skills/active.json').read_text())
            self.assertEqual(receipt['release']['archiveSha256'],archive_sha)
            receipts.append({'repositoryId':repo,'archiveSha256':archive_sha,
                             'installedFiles':len(before),'unchangedInstalledBytes':before==after,
                             'generation':receipt['generation'],'publicId':identity['publicId'],
                             'snapshotDigest':answer['receipt']['digest']})
        self.assertNotEqual(*identities);self.assertNotEqual(*fingerprints)
        self.assertEqual(errors,[])
        self.assertTrue(requests)
        for path,headers,body in requests:
            for token in (*tokens.values(),*SECRETS):
                self.assertNotIn(token,path)
                self.assertNotIn(token,repr({k:v for k,v in headers.items() if k.lower()!='authorization'}))
                self.assertNotIn(token.encode(),body or b'')
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(),archive_sha)
        self.qualification_evidence={'archiveSha256':archive_sha,'adopters':receipts,
            'transportSeam':'external HTTPS socket substitution to loopback fixture receiver',
            'installedFilesEdited':False,'productionOrOffMachineProof':False}


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
