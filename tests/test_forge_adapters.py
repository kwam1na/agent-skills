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
import urllib.parse
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
elif sys.argv[1] == "api":
    if os.environ.get("FAKE_GH_API_FAIL"):
        sys.exit("gh: HTTP 401: Bad credentials for token " + os.environ.get("GH_TOKEN", ""))
    # As GitHub's matching-refs does: every ref that starts with the asked-for name, with its case, from FAKE_GH_REFS.
    asked = "refs/" + sys.argv[2].split("/git/matching-refs/", 1)[1]
    refs = json.loads(os.environ.get("FAKE_GH_REFS", "{}"))
    print(json.dumps([{"ref": "refs/heads/" + name, "object": {"sha": sha, "type": "commit"}}
                      for name, sha in refs.items() if ("refs/heads/" + name).startswith(asked)]))
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


HEAD = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"


class GitHubCandidateTests(unittest.TestCase):
    """`candidate` turns a story key into its conventional branch and that branch's tip, as the queue's step needs."""

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
                    "FAKE_GH_LOG": str(self.log), "FLOW_FORGE_REPOSITORY": "o/n"}

    def candidate(self, refs: dict[str, str], *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-B", str(ADAPTERS / "forge_github.py"), "candidate", *argv],
                              env={**self.env, "FAKE_GH_REFS": json.dumps(refs)}, capture_output=True, text=True,
                              timeout=60)

    def test_a_key_resolves_to_its_feature_branch_and_its_tip(self) -> None:
        # A longer key and a branch below this one share the name's start; neither is this story's branch.
        refs = {"feature/X-1": HEAD, "feature/X-10": OTHER, "feature/X-1/spike": OTHER, "main": OTHER}
        done = self.candidate(refs, "--key", "X-1")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"branch": "feature/X-1", "head": HEAD})

    def test_another_prefix_names_another_branch(self) -> None:
        done = self.candidate({"feature/X-1": OTHER, "story/X-1": HEAD}, "--key", "X-1", "--prefix", "story/")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"branch": "story/X-1", "head": HEAD})

    def test_a_missing_branch_is_refused(self) -> None:
        # A branch that differs only in case is not this story's branch either.
        for refs in ({"feature/X-10": OTHER}, {"feature/x-1": OTHER}):
            with self.subTest(refs=refs):
                done = self.candidate(refs, "--key", "X-1")
                self.assertNotEqual(done.returncode, 0)
                self.assertEqual(done.stdout, "")
                self.assertIn("github: no branch feature/X-1 on o/n; not resolved", done.stderr)

    def test_a_name_a_branch_under_another_case_of_the_prefix_answers_to_is_refused(self) -> None:
        done = self.candidate({"feature/X-1": HEAD, "Feature/X-1": OTHER}, "--key", "X-1")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout, "")
        self.assertIn("github: 2 branches on o/n answer to feature/X-1 (Feature/X-1, feature/X-1), not one; "
                      "not resolved", done.stderr)

    def test_only_a_full_hash_is_a_tip(self) -> None:
        sha256 = HEAD + HEAD[:24]
        done = self.candidate({"feature/X-1": sha256}, "--key", "X-1")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"branch": "feature/X-1", "head": sha256})
        for tip in (HEAD[:12], HEAD + "0"):
            with self.subTest(tip=tip):
                done = self.candidate({"feature/X-1": tip}, "--key", "X-1")
                self.assertNotEqual(done.returncode, 0)
                self.assertEqual(done.stdout, "")
                self.assertIn("not a full commit hash", done.stderr)

    def test_a_failing_gh_api_is_reported_without_its_token(self) -> None:
        done = subprocess.run([sys.executable, "-B", str(ADAPTERS / "forge_github.py"), "candidate", "--key", "X-1"],
                              env={**self.env, "FAKE_GH_API_FAIL": "1", "GH_TOKEN": "ghp-secret-value"},
                              capture_output=True, text=True, timeout=60)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout, "")
        self.assertTrue(done.stderr.startswith("gh api:"), done.stderr)
        self.assertNotIn("ghp-secret-value", done.stderr)
        self.assertIn("[redacted]", done.stderr)

    def test_a_name_several_branches_answer_to_is_refused(self) -> None:
        # Names that differ only in case are one branch to a case-insensitive checkout.
        done = self.candidate({"feature/X-1": HEAD, "feature/x-1": OTHER}, "--key", "X-1")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout, "")
        self.assertIn("github: 2 branches on o/n answer to feature/X-1 (feature/X-1, feature/x-1), not one; "
                      "not resolved", done.stderr)


class BitbucketCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = importlib.util.spec_from_file_location("forge_bitbucket_for_candidate_tests",
                                                           ADAPTERS / "forge_bitbucket.py")
        self.branches: dict[str, str] = {}
        self.calls: list[str] = []
        self.page_size = 100

    def answer(self, request, timeout=None):
        self.calls.append(request.full_url)
        url = urllib.parse.urlsplit(request.full_url)
        query = urllib.parse.parse_qs(url.query).get("q", [""])[0]
        # As Bitbucket's `~` does: every branch whose name contains the text, ignoring case.
        text = query.split('"')[1].lower() if '"' in query else ""
        values = [{"name": name, "target": {"hash": sha}} for name, sha in self.branches.items() if text in name.lower()]
        assert url.path.endswith("/refs/branches") and query.startswith("name ~ "), request.full_url
        # Paged, as Bitbucket pages: `page_size` values a page, and a `next` link while more are left.
        page = int(urllib.parse.parse_qs(url.query).get("page", ["1"])[0])
        body = {"values": values[(page - 1) * self.page_size:page * self.page_size]}
        if page * self.page_size < len(values):
            body["next"] = request.full_url.split("&page=")[0] + f"&page={page + 1}"
        return contextlib.closing(io.BytesIO(json.dumps(body).encode()))

    def candidate(self, *argv: str) -> dict:
        module = importlib.util.module_from_spec(self.spec)
        with mock.patch.dict(os.environ, {"FLOW_FORGE_REPOSITORY": "ws/repo", "FLOW_FORGE_TOKEN_ENV": "FX_TOKEN",
                                          "FX_TOKEN": "bb-secret"}), \
                mock.patch.object(sys, "argv", ["forge_bitbucket.py", "candidate", *argv]), \
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
            self.candidate(*argv)
        return str(refusal.exception.code)

    def test_a_key_resolves_to_its_feature_branch_and_its_full_tip(self) -> None:
        self.branches = {"feature/X-1": HEAD, "feature/X-10": OTHER, "feature/X-1/spike": OTHER, "main": OTHER}
        self.assertEqual(self.candidate("--key", "X-1"), {"branch": "feature/X-1", "head": HEAD})

    def test_another_prefix_names_another_branch(self) -> None:
        self.branches = {"feature/X-1": OTHER, "story/X-1": HEAD}
        self.assertEqual(self.candidate("--key", "X-1", "--prefix", "story/"), {"branch": "story/X-1", "head": HEAD})

    def test_a_missing_branch_is_refused(self) -> None:
        # A branch that differs only in case is not this story's branch either.
        for branches in ({"feature/X-10": OTHER}, {"feature/x-1": OTHER}):
            with self.subTest(branches=branches):
                self.branches = branches
                self.assertIn("bitbucket: no branch feature/X-1 on ws/repo; not resolved", self.refused("--key", "X-1"))

    def test_a_name_several_branches_answer_to_is_refused(self) -> None:
        self.branches = {"feature/X-1": HEAD, "feature/x-1": OTHER}
        self.assertIn("bitbucket: 2 branches on ws/repo answer to feature/X-1 (feature/X-1, feature/x-1), not one; "
                      "not resolved", self.refused("--key", "X-1"))

    def test_a_name_a_branch_under_another_case_of_the_prefix_answers_to_is_refused(self) -> None:
        self.branches = {"feature/X-1": HEAD, "Feature/X-1": OTHER}
        self.assertIn("bitbucket: 2 branches on ws/repo answer to feature/X-1 (Feature/X-1, feature/X-1), not one; "
                      "not resolved", self.refused("--key", "X-1"))

    def test_every_page_of_the_listing_is_read(self) -> None:
        # The name's longer neighbours fill the first pages; the branch, or its twin, is only on a later one.
        neighbours = {f"feature/X-1{n}": OTHER for n in range(5)}
        self.page_size = 2
        self.branches = {**neighbours, "feature/X-1": HEAD}
        self.assertEqual(self.candidate("--key", "X-1"), {"branch": "feature/X-1", "head": HEAD})
        self.assertGreater(len(self.calls), 1)
        self.branches = {"feature/X-1": HEAD, **neighbours, "feature/x-1": OTHER}
        self.assertIn("2 branches on ws/repo answer to feature/X-1", self.refused("--key", "X-1"))

    def test_only_a_full_hash_is_a_tip(self) -> None:
        # The queue takes only a full object name; a short or overlong one must not reach it.
        sha256 = HEAD + HEAD[:24]
        self.branches = {"feature/X-1": sha256}
        self.assertEqual(self.candidate("--key", "X-1"), {"branch": "feature/X-1", "head": sha256})
        for tip in (HEAD[:12], HEAD + "0"):
            with self.subTest(tip=tip):
                self.branches = {"feature/X-1": tip}
                self.assertIn("not a full commit hash", self.refused("--key", "X-1"))


if __name__ == "__main__":
    unittest.main()


# -- the PR review: each comment's account, and a transient failure's exit 75 ----------------------------------

# A gh that answers `pr view` with FAKE_GH_VIEW, or fails as gh does, with FAKE_GH_FAIL on stderr and exit 1.
REVIEW_GH = r'''#!/usr/bin/env python3
import os, sys
if os.environ.get("FAKE_GH_FAIL"):
    sys.exit(os.environ["FAKE_GH_FAIL"])
if sys.argv[1:3] != ["pr", "view"]:
    sys.exit("fake gh: unexpected " + " ".join(sys.argv[1:]))
print(os.environ["FAKE_GH_VIEW"])
'''


class GitHubReviewsTests(unittest.TestCase):
    URL = "https://github.com/o/n/pull/3"

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "bin").mkdir()
        gh = root / "bin" / "gh"
        gh.write_text(REVIEW_GH.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1))
        gh.chmod(0o755)
        view = {"headRefOid": HEAD, "statusCheckRollup": [],
                "reviews": [{"author": {"login": "review-bot"}, "submittedAt": "2026-01-01T00:00:30Z", "body": "fine"}]}
        self.env = {**os.environ, "PATH": f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}",
                    "FLOW_FORGE_REPOSITORY": "o/n", "FAKE_GH_VIEW": json.dumps(view)}

    def reviews(self, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-B", str(ADAPTERS / "forge_github.py"), "reviews", "--url", self.URL,
                               "--head", HEAD], env={**self.env, **env}, capture_output=True, text=True, timeout=60)

    def test_each_review_carries_its_authors_login_as_its_account(self) -> None:
        done = self.reviews()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["reviews"],
                         [{"author": "review-bot", "account": "review-bot", "at": "2026-01-01T00:00:30Z", "body": "fine"}])

    def test_a_finished_check_carries_its_completed_at_as_done_at(self) -> None:
        view = {"headRefOid": HEAD, "reviews": [], "statusCheckRollup": [
            {"name": "build", "conclusion": "SUCCESS", "startedAt": "2026-01-01T00:00:01Z",
             "completedAt": "2026-01-01T00:05:00Z"},
            {"name": "test", "status": "IN_PROGRESS", "startedAt": "2026-01-01T00:00:02Z",
             "completedAt": "0001-01-01T00:00:00Z"},
            {"context": "lint", "state": "PENDING"}]}
        done = self.reviews(FAKE_GH_VIEW=json.dumps(view))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["checks"], [
            {"name": "build", "state": "passed", "at": "2026-01-01T00:05:00Z", "doneAt": "2026-01-01T00:05:00Z"},
            {"name": "test", "state": "pending", "at": "0001-01-01T00:00:00Z"},
            {"name": "lint", "state": "pending", "at": ""}])

    def test_a_transient_failure_exits_75_and_any_other_exits_1(self) -> None:
        for said, code in (
            ("HTTP 503: Service Unavailable (https://api.github.com/graphql)", 75),
            ("HTTP 429: API rate limit exceeded", 75),
            ("Post \"https://api.github.com/graphql\": net/http: request canceled (Client.Timeout exceeded)", 75),
            ("Post \"https://api.github.com/graphql\": dial tcp 140.82.112.6:443: connect: connection refused", 75),
            ("HTTP 401: Bad credentials (https://api.github.com/graphql)", 1),
            ("HTTP 404: Not Found (https://api.github.com/repos/o/n/pulls/3)", 1),
        ):
            with self.subTest(said=said):
                done = self.reviews(FAKE_GH_FAIL=said)
                self.assertEqual(done.returncode, code, done.stderr)
                self.assertIn(said.split(" (")[0][:30], done.stderr)


class BitbucketReviewsTests(unittest.TestCase):
    URL = "https://bitbucket.org/ws/repo/pull-requests/7"
    API = "https://api.bitbucket.org/2.0/repositories/ws/repo"

    def setUp(self) -> None:
        self.spec = importlib.util.spec_from_file_location("forge_bitbucket_for_review_tests",
                                                           ADAPTERS / "forge_bitbucket.py")
        self.calls: list[str] = []
        self.failure: Exception | None = None

    def answer(self, request, timeout=None):
        """Only the head's statuses and the PR's comments are answered; any other path fails the row."""
        self.calls.append(request.full_url)
        if self.failure is not None:
            raise self.failure
        if request.full_url == f"{self.API}/commit/{HEAD}/statuses?pagelen=100":
            body = {"values": [{"name": "pipe", "state": "SUCCESSFUL", "created_on": "2026-01-01T00:00:10+00:00",
                                "updated_on": "2026-01-01T00:09:10+00:00"},
                               {"name": "lint", "state": "INPROGRESS", "created_on": "2026-01-01T00:00:11+00:00",
                                "updated_on": "2026-01-01T00:01:00+00:00"},
                               {"name": "test", "state": "FAILED", "created_on": "2026-01-01T00:00:12+00:00",
                                "updated_on": "2026-01-01T00:02:00+00:00"},
                               {"name": "old", "state": "SUCCESSFUL", "created_on": "2026-01-01T00:00:13+00:00"}]}
        elif request.full_url == f"{self.API}/pullrequests/7/comments?pagelen=100":
            body = {"values": [{"user": {"display_name": "Review Bot", "uuid": "{u-1}"}, "updated_on": "2026-01-01T00:00:30+00:00",
                                "content": {"raw": "fine"}}]}
        else:
            raise AssertionError(f"an unexpected path: {request.full_url}")
        return contextlib.closing(io.BytesIO(json.dumps(body).encode()))

    def reviews(self) -> dict:
        module = importlib.util.module_from_spec(self.spec)
        with mock.patch.dict(os.environ, {"FLOW_FORGE_REPOSITORY": "ws/repo", "FLOW_FORGE_TOKEN_ENV": "FX_TOKEN",
                                          "FX_TOKEN": "bb-secret"}), \
                mock.patch.object(sys, "argv", ["forge_bitbucket.py", "reviews", "--url", self.URL, "--head", HEAD]), \
                mock.patch("urllib.request.urlopen", self.answer), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            previous = sys.dont_write_bytecode
            sys.dont_write_bytecode = True
            try:
                self.spec.loader.exec_module(module)
                try:
                    module.main()
                except SystemExit as stopped:
                    return {"exit": stopped.code, "stderr": err.getvalue()}
            finally:
                sys.dont_write_bytecode = previous
        return json.loads(out.getvalue())

    def test_a_finished_status_carries_its_last_update_as_done_at(self) -> None:
        """A status no longer in progress finished when it was last updated; one still running has no doneAt."""
        self.assertEqual(self.reviews()["checks"], [
            {"name": "pipe", "state": "passed", "at": "2026-01-01T00:00:10+00:00", "doneAt": "2026-01-01T00:09:10+00:00"},
            {"name": "lint", "state": "pending", "at": "2026-01-01T00:00:11+00:00"},
            {"name": "test", "state": "failed", "at": "2026-01-01T00:00:12+00:00", "doneAt": "2026-01-01T00:02:00+00:00"},
            {"name": "old", "state": "passed", "at": "2026-01-01T00:00:13+00:00"}])  # no update time: no doneAt

    def test_each_comment_carries_its_authors_uuid_as_its_account(self) -> None:
        self.assertEqual(self.reviews()["reviews"], [{"author": "Review Bot", "account": "{u-1}",
                                                      "at": "2026-01-01T00:00:30+00:00", "body": "fine"}])

    def test_a_transient_failure_exits_75_and_any_other_exits_1(self) -> None:
        import urllib.error

        def http(code: int) -> urllib.error.HTTPError:
            return urllib.error.HTTPError(f"{self.API}/x", code, "said", {}, io.BytesIO(b"forge says no"))
        for what, failure, code in (
            ("a 503", http(503), 75),
            ("a 429", http(429), 75),
            ("a timeout", TimeoutError("timed out"), 75),
            ("a connection error", urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")), 75),
            ("a 401", http(401), 1),
            ("a 404", http(404), 1),
        ):
            with self.subTest(what):
                self.failure = failure
                answer = self.reviews()
                # sys.exit(message) exits 1, the message its code; exit 75 prints the message first.
                exited = 1 if isinstance(answer["exit"], str) else answer["exit"]
                said = answer["stderr"] if exited == 75 else answer["exit"]
                self.assertEqual(exited, code, answer)
                self.assertIn("bitbucket GET /commit/", said)
                self.assertNotIn("bb-secret", said)
