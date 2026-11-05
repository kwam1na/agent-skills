"""The shipped forge adapters merge a branch's pull request only into the base the merge step names."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_skills


# The product under test: this repository, or the extracted release candidate qualification puts first on the path.
PRODUCT_ROOT = Path(agent_skills.__file__).resolve().parents[1]
ADAPTERS = PRODUCT_ROOT / "skills/run-delivery/adapters"

# A gh that logs each call, lists FAKE_GH_PRS open pull requests targeting FAKE_GH_BASE (none named when it is unset)
# with only the fields --json asks for, and, as gh does, refuses a merge pinned to a head other than "abc".
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
command = sys.argv[1:3]
if command == ["pr", "list"]:
    count = int(os.environ.get("FAKE_GH_PRS", "1"))
    fields = sys.argv[sys.argv.index("--json") + 1].split(",")
    listed = []
    for i in range(count):
        entry = {"url": "https://github.com/o/n/pull/%d" % (3 + i)}
        if "FAKE_GH_BASE" in os.environ:
            entry["baseRefName"] = os.environ["FAKE_GH_BASE"]
        listed.append({key: value for key, value in entry.items() if key in fields})
    print(json.dumps(listed))
elif command == ["pr", "merge"]:
    if sys.argv[sys.argv.index("--match-head-commit") + 1] != "abc":
        sys.exit("gh: head branch was modified; not merged")
    print("done")
else:
    sys.exit("fake gh: unexpected " + " ".join(sys.argv[1:]))
'''


class GitHubMergeDestinationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "bin").mkdir()
        gh = root / "bin" / "gh"
        gh.write_text(FAKE_GH.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1))
        gh.chmod(0o755)
        self.log = root / "gh.jsonl"
        self.log.write_text("")
        self.env = {**os.environ, "PATH": f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}",
                    "FAKE_GH_LOG": str(self.log), "FLOW_FORGE_REPOSITORY": "o/n", "FAKE_GH_BASE": "release-1"}

    def merge(self, *argv: str, **env: str) -> subprocess.CompletedProcess:
        self.log.write_text("")
        return subprocess.run([sys.executable, "-B", str(ADAPTERS / "forge_github.py"), "merge", *argv],
                              env={**self.env, **env}, capture_output=True, text=True, timeout=60)

    def merges(self) -> list[list[str]]:
        return [call for call in map(json.loads, self.log.read_text().splitlines()) if call[:2] == ["pr", "merge"]]

    def test_a_pull_request_targeting_the_destination_merges_at_the_head(self) -> None:
        done = self.merge("--source", "feature/X-1", "--head", "abc", "--strategy", "squash",
                          "--destination", "release-1")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"url": "https://github.com/o/n/pull/3", "state": "merged"})
        self.assertEqual(self.merges(), [["pr", "merge", "https://github.com/o/n/pull/3", "--squash",
                                          "--match-head-commit", "abc", "--repo", "o/n"]])

    def test_a_pull_request_retargeted_to_another_base_is_refused_and_named(self) -> None:
        # release-10 has the destination as a prefix: the comparison is exact.
        for base in ("release-2", "release-10"):
            with self.subTest(base=base):
                done = self.merge("--source", "feature/X-1", "--head", "abc", "--strategy", "merge",
                                  "--destination", "release-1", FAKE_GH_BASE=base)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn(f"github: feature/X-1's open pull request targets {base}, not release-1; not merged",
                              done.stderr)
                self.assertEqual(self.merges(), [], "a merge was attempted on a refusal")

    def test_a_pull_request_whose_base_is_not_reported_is_refused(self) -> None:
        env = {key: value for key, value in self.env.items() if key != "FAKE_GH_BASE"}
        self.log.write_text("")
        done = subprocess.run([sys.executable, "-B", str(ADAPTERS / "forge_github.py"), "merge", "--source",
                               "feature/X-1", "--head", "abc", "--strategy", "merge", "--destination", "release-1"],
                              env=env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("targets None, not release-1; not merged", done.stderr)
        self.assertEqual(self.merges(), [])

    def test_without_a_destination_the_merge_is_as_before(self) -> None:
        done = self.merge("--source", "feature/X-1", "--head", "abc", "--strategy", "merge", FAKE_GH_BASE="release-2")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(self.merges()), 1)

    def test_the_head_and_one_open_pull_request_checks_are_unchanged(self) -> None:
        for argv, env, words in (
            (["--head", "abc"], {"FAKE_GH_PRS": "0"}, "feature/X-1 has no open pull requests, not one; not merged"),
            (["--head", "abc"], {"FAKE_GH_PRS": "2"}, "feature/X-1 has 2 open pull requests, not one; not merged"),
            ([], {}, "merge --source needs --head"),
        ):
            with self.subTest(words=words):
                done = self.merge("--source", "feature/X-1", "--strategy", "merge", "--destination", "release-1",
                                  *argv, **env)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn(words, done.stderr)
                self.assertEqual(self.merges(), [])
        # A moved head is still refused by gh itself, at the head the merge was pinned to.
        done = self.merge("--source", "feature/X-1", "--head", "def", "--strategy", "merge", "--destination", "release-1")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("head branch was modified", done.stderr)

    def test_a_destination_without_a_source_is_refused(self) -> None:
        done = self.merge("--url", "https://github.com/o/n/pull/3", "--head", "abc", "--strategy", "merge",
                          "--destination", "release-1")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("merge --destination applies only with --source", done.stderr)
        self.assertEqual(self.merges(), [])


class BitbucketMergeDestinationTests(unittest.TestCase):
    URL = "https://bitbucket.org/ws/repo/pull-requests/7"
    API = "https://api.bitbucket.org/2.0/repositories/ws/repo/pullrequests/7"

    def setUp(self) -> None:
        self.spec = importlib.util.spec_from_file_location("forge_bitbucket_for_destination_tests",
                                                           ADAPTERS / "forge_bitbucket.py")
        self.calls: list[tuple[str, str, dict | None]] = []
        self.open_prs = 1
        self.target = "release-1"  # the pull request's base as read just before the merge; None leaves it out
        self.listed_target = None  # its base in the open-pull-request listing, when that differs

    def answer(self, request, timeout=None):
        self.calls.append((request.get_method(), request.full_url, json.loads(request.data) if request.data else None))
        listed = {"branch": {"name": self.listed_target or self.target}}
        if "?q=" in request.full_url:
            body = {"values": [{"links": {"html": {"href": f"https://bitbucket.org/ws/repo/pull-requests/{7 + i}"}},
                                "destination": listed} for i in range(self.open_prs)]}
        elif request.get_method() == "GET":
            body = {"source": {"commit": {"hash": "abc123def456"}}}
            if self.target is not None:
                body["destination"] = {"branch": {"name": self.target}}
        else:
            body = {}
        return contextlib.closing(io.BytesIO(json.dumps(body).encode()))

    def merge(self, *argv: str) -> dict:
        module = importlib.util.module_from_spec(self.spec)
        self.calls.clear()
        with mock.patch.dict(os.environ, {"FLOW_FORGE_REPOSITORY": "ws/repo", "FLOW_FORGE_TOKEN_ENV": "FX_TOKEN",
                                          "FX_TOKEN": "bb-secret"}), \
                mock.patch.object(sys, "argv", ["forge_bitbucket.py", "merge", *argv]), \
                mock.patch("urllib.request.urlopen", self.answer), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            previous = sys.dont_write_bytecode
            sys.dont_write_bytecode = True
            try:
                self.spec.loader.exec_module(module)
                module.main()
            finally:
                sys.dont_write_bytecode = previous
        return json.loads(out.getvalue())

    def refused(self, *argv: str) -> str:
        with self.assertRaises(SystemExit) as refusal:
            self.merge(*argv)
        self.assertFalse([call for call in self.calls if call[0] == "POST"], "merged on a refusal")
        return str(refusal.exception.code)

    def test_a_pull_request_targeting_the_destination_merges_at_the_head(self) -> None:
        self.assertEqual(self.merge("--source", "feature/X-1", "--head", "abc123def456", "--strategy", "squash",
                                    "--destination", "release-1"), {"url": self.URL, "state": "merged"})
        self.assertEqual(self.calls[-2:], [("GET", self.API, None),
                                           ("POST", self.API + "/merge", {"merge_strategy": "squash",
                                                                          "close_source_branch": False})])

    def test_a_pull_request_retargeted_to_another_base_is_refused_and_named(self) -> None:
        # release-10 has the destination as a prefix: the comparison is exact.
        for base in ("release-2", "release-10"):
            with self.subTest(base=base):
                self.target = base
                message = self.refused("--source", "feature/X-1", "--head", "abc123def456", "--strategy", "merge",
                                       "--destination", "release-1")
                self.assertIn(f"bitbucket: feature/X-1's open pull request targets {base}, not release-1; not merged",
                              message)

    def test_the_base_is_read_from_the_pull_request_just_before_the_merge(self) -> None:
        # Listed against the destination, retargeted before the merge: the fresh read decides.
        self.listed_target, self.target = "release-1", "release-2"
        message = self.refused("--source", "feature/X-1", "--head", "abc123def456", "--strategy", "merge",
                               "--destination", "release-1")
        self.assertIn("targets release-2, not release-1; not merged", message)

    def test_a_pull_request_whose_base_is_not_reported_is_refused(self) -> None:
        self.listed_target, self.target = "release-1", None
        message = self.refused("--source", "feature/X-1", "--head", "abc123def456", "--strategy", "merge",
                               "--destination", "release-1")
        self.assertIn("targets None, not release-1; not merged", message)

    def test_without_a_destination_the_merge_is_as_before(self) -> None:
        self.target = "release-2"
        self.assertEqual(self.merge("--source", "feature/X-1", "--head", "abc123def456", "--strategy", "merge"),
                         {"url": self.URL, "state": "merged"})

    def test_the_head_and_one_open_pull_request_checks_are_unchanged(self) -> None:
        for count, argv, words in ((0, ["--head", "abc123def456"], "feature/X-1 has no open pull requests"),
                                   (2, ["--head", "abc123def456"], "feature/X-1 has 2 open pull requests"),
                                   (1, ["--head", "fff000"], "is at abc123def456, not fff000; not merged"),
                                   (1, [], "merge --source needs --head")):
            with self.subTest(words=words):
                self.open_prs = count
                self.assertIn(words, self.refused("--source", "feature/X-1", "--strategy", "merge",
                                                  "--destination", "release-1", *argv))

    def test_a_destination_without_a_source_is_refused(self) -> None:
        message = self.refused("--url", self.URL, "--head", "abc123def456", "--strategy", "merge",
                               "--destination", "release-1")
        self.assertIn("merge --destination applies only with --source", message)
        self.assertEqual(self.calls, [], "nothing was asked of the forge")


if __name__ == "__main__":
    unittest.main()
