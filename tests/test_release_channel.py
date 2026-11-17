from __future__ import annotations

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


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_channel.py"


def load_channel():
    spec = importlib.util.spec_from_file_location("release_channel", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


channel = load_channel()


def git(root: Path, *arguments: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "tag.gpgSign=false", "-c", "commit.gpgSign=false",
                           *arguments], cwd=root, capture_output=True, text=True, check=True)
    return done.stdout


class VersionTests(unittest.TestCase):
    def test_version_tags_are_accepted_and_everything_else_is_refused(self) -> None:
        for tag in ("v1.2.3", "v1.2.3-rc.1", "v0.0.0", "v10.20.30-rc.40"):
            with self.subTest(tag=tag):
                self.assertIsNotNone(channel.parse_version(tag))
        for tag in ("1.2.3", "v1.2", "v01.2.3", "v1.02.3", "v1.2.03", "v1.2.3-rc.01", "v1.2.3-beta", "some-set-v1", "", "v1.2.3\n", "v1.2.3-rc."):
            with self.subTest(tag=tag):
                self.assertIsNone(channel.parse_version(tag))

    def test_the_channel_and_the_release_command_agree_on_what_a_version_is(self) -> None:
        spec = importlib.util.spec_from_file_location("cut_release_for_channel", ROOT / "scripts" / "cut-release.py")
        command = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(command)
        for tag in ("v1.2.3", "v1.2.3-rc.1", "v01.2.3", "v1.2", "v1.2.3-beta", "run-delivery-v1", ""):
            with self.subTest(tag=tag):
                self.assertEqual(channel.parse_version(tag) is not None, command.valid_version(tag))

    def test_a_candidate_sorts_below_its_final_and_numbers_compare_as_numbers(self) -> None:
        ordered = ["v0.9.9", "v1.2.3-rc.1", "v1.2.3-rc.2", "v1.2.3-rc.10", "v1.2.3", "v1.2.4-rc.1", "v1.10.0", "v2.0.0"]
        self.assertEqual(sorted(reversed(ordered), key=channel.parse_version), ordered)

    def test_a_final_is_explicitly_latest_and_a_candidate_is_a_prerelease_and_explicitly_not(self) -> None:
        self.assertEqual(channel.release_flags("v1.2.3"), ["--latest"])
        self.assertEqual(channel.release_flags("v1.2.3-rc.1"), ["--prerelease", "--latest=false"])


class OrderingGuardTests(unittest.TestCase):
    """Run against a temporary repository with real tags."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        git(self.root, "init", "-q")
        self.first = self.commit("first")
        self.second = self.commit("second")

    def commit(self, name: str) -> str:
        git(self.root, "commit", "-q", "--allow-empty", "-m", name)
        return git(self.root, "rev-parse", "HEAD").strip()

    def tag(self, name: str, commit: str | None = None) -> None:
        git(self.root, "tag", "-a", "-m", name, name, commit or "HEAD")

    def assert_refused(self, tag: str, phrase: str) -> None:
        with self.assertRaises(channel.ChannelError) as caught:
            channel.check_order(tag, self.root)
        self.assertIn(phrase, str(caught.exception))

    def test_a_first_version_passes_with_no_earlier_version_tag_and_old_set_tags_present(self) -> None:
        self.tag("run-delivery-v1", self.first)
        self.tag("a-later-set-v1")
        self.tag("v1.2.3")
        channel.check_order("v1.2.3", self.root)

    def test_a_tag_that_is_not_a_version_is_refused_before_anything_else(self) -> None:
        self.tag("some-set-v1")
        self.assert_refused("some-set-v1", "not a version tag")

    def test_a_lower_version_is_refused(self) -> None:
        self.tag("v2.0.0", self.first)
        self.tag("v1.9.0")
        self.assert_refused("v1.9.0", "v2.0.0")

    def test_a_candidate_of_a_published_final_is_refused(self) -> None:
        self.tag("v1.2.3", self.first)
        self.tag("v1.2.3-rc.1")
        self.assert_refused("v1.2.3-rc.1", "v1.2.3")

    def test_every_version_tag_counts_not_only_the_nearest(self) -> None:
        self.tag("v3.0.0", self.first)
        self.tag("v1.0.0", self.first)
        self.tag("v2.0.0")
        self.assert_refused("v2.0.0", "v3.0.0")

    def test_a_commit_behind_the_highest_version_tag_is_refused(self) -> None:
        self.tag("v1.0.0")
        self.tag("v1.1.0", self.first)
        self.assert_refused("v1.1.0", "does not descend")

    def test_a_commit_on_a_diverged_line_is_refused(self) -> None:
        self.tag("v1.0.0")
        git(self.root, "checkout", "-q", "-b", "side", self.first)
        self.commit("side")
        self.tag("v1.1.0")
        self.assert_refused("v1.1.0", "does not descend")

    def test_a_re_run_for_the_same_tag_is_not_refused_by_the_guard_itself(self) -> None:
        self.tag("v1.0.0", self.first)
        self.tag("v1.1.0")
        channel.check_order("v1.1.0", self.root)
        channel.check_order("v1.1.0", self.root)

    def test_promoting_a_candidate_on_its_own_commit_is_accepted(self) -> None:
        self.tag("v1.2.3-rc.1")
        self.tag("v1.2.3")
        channel.check_order("v1.2.3", self.root)

    def test_tags_that_only_look_like_versions_do_not_count(self) -> None:
        self.tag("v9.9", self.first)
        self.tag("v09.0.0", self.first)
        self.tag("v9.0.0-beta", self.first)
        self.tag("v1.0.0")
        channel.check_order("v1.0.0", self.root)


class NotesTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        git(self.root, "init", "-q")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "first")
        self.output = self.root / "out"
        self.output.mkdir()
        for profile, digest in (("core", "a" * 64), ("linear", "b" * 64)):
            (self.output / f"{profile}-v1.2.3.release.json").write_text(
                json.dumps({"profile": profile, "releaseId": f"{profile}-v1.2.3", "archiveSha256": digest}), encoding="utf-8")

    def test_a_signed_annotation_loses_its_signature_and_keeps_the_rest(self) -> None:
        for patch, (begin, end) in enumerate((("-----BEGIN PGP SIGNATURE-----", "-----END PGP SIGNATURE-----"),
                                              ("-----BEGIN SSH SIGNATURE-----", "-----END SSH SIGNATURE-----"),
                                              ("-----BEGIN SIGNED MESSAGE-----", "-----END SIGNED MESSAGE-----"))):
            with self.subTest(begin=begin):
                name = f"v1.0.{patch}"
                git(self.root, "tag", "-a", "-m", f"What changed.\n\nSecond paragraph.\n\n{begin}\n\nabc123\n{end}", name)
                annotation = channel.annotation(name, self.root)
                self.assertEqual(annotation, "What changed.\n\nSecond paragraph.")
                notes = channel.render_notes(annotation, self.output, "generated")
                self.assertNotIn("SIGNATURE", notes)
                self.assertNotIn("SIGNED MESSAGE", notes)
                self.assertNotIn("abc123", notes)

    @unittest.skipUnless(shutil.which("ssh-keygen"), "really signing a tag needs ssh-keygen")
    def test_a_really_signed_tag_gives_only_its_message(self) -> None:
        key = self.root / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        git(self.root, "-c", "gpg.format=ssh", "-c", f"user.signingkey={key}", "tag", "-s", "-m", "Signed message.", "v1.2.3")
        self.assertIn("BEGIN SSH SIGNATURE", git(self.root, "cat-file", "tag", "v1.2.3"))
        self.assertEqual(channel.annotation("v1.2.3", self.root), "Signed message.")

    def test_an_empty_annotation_still_gives_the_table_and_the_list(self) -> None:
        git(self.root, "tag", "-a", "-m", "", "v1.2.3")
        annotation = channel.annotation("v1.2.3", self.root)
        self.assertEqual(annotation, "")
        notes = channel.render_notes(annotation, self.output, "## What's Changed\n* one\n")
        self.assertTrue(notes.startswith("## Archives"), notes)
        self.assertIn("## What's Changed\n* one", notes)

    def test_the_notes_are_annotation_then_every_digest_then_the_generated_list(self) -> None:
        notes = channel.render_notes("The annotation.", self.output, "## What's Changed\n* one\n")
        positions = [notes.index(text) for text in ("The annotation.", "a" * 64, "b" * 64, "## What's Changed")]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("`core-v1.2.3`", notes)
        self.assertIn("`linear-v1.2.3`", notes)
        self.assertTrue(notes.endswith("\n"))

    def test_notes_with_no_release_file_are_refused_rather_than_published_without_digests(self) -> None:
        for path in self.output.iterdir():
            path.unlink()
        with self.assertRaises(channel.ChannelError):
            channel.render_notes("", self.output, "generated")

    def test_a_final_lists_changes_since_the_last_final_so_promoting_a_candidate_still_has_a_list(self) -> None:
        tags = ["v1.0.0", "v1.1.0-rc.1", "v1.1.0-rc.2", "run-delivery-v1", "v1.1"]
        self.assertEqual(channel.previous_tag("v1.1.0", tags), "v1.0.0")
        self.assertEqual(channel.previous_tag("v1.1.0-rc.2", tags), "v1.1.0-rc.1")
        self.assertEqual(channel.previous_tag("v1.1.0-rc.1", tags), "v1.0.0")
        self.assertIsNone(channel.previous_tag("v1.0.0", tags))
        self.assertIsNone(channel.previous_tag("v0.1.0-rc.1", ["run-delivery-v1"]))


FAKE_GH = """#!{python}
import json, os, sys
folder = os.environ["FAKE_GH"]
with open(os.path.join(folder, "script.json"), encoding="utf-8") as stream:
    script = json.load(stream)
reply = script["view"]
sys.stdout.write(reply.get("stdout", ""))
sys.stderr.write(reply.get("stderr", ""))
sys.exit(reply.get("code", 0))
"""


class CommandLineTests(unittest.TestCase):
    """The exit code and output are all release.yml consumes, so the commands themselves run here."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "first")
        self.commit = git(self.repo, "rev-parse", "HEAD").strip()

    def run_command(self, *arguments: str, cwd: Path | None = None, environment: dict | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *arguments], cwd=cwd or self.repo, capture_output=True, text=True,
                              env={**os.environ, **(environment or {})})

    def fake_gh(self, **replies) -> dict:
        folder = self.root / "fake"
        folder.mkdir(exist_ok=True)
        (folder / "script.json").write_text(json.dumps(replies), encoding="utf-8")
        tool = folder / "gh"
        tool.write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
        tool.chmod(0o755)
        return {"FAKE_GH": str(folder), "PATH": f"{folder}{os.pathsep}{os.environ['PATH']}"}

    def test_order_passes_with_exit_zero_and_refuses_with_exit_one(self) -> None:
        git(self.repo, "tag", "-a", "-m", "m", "v2.0.0")
        git(self.repo, "tag", "-a", "-m", "m", "v1.0.0")
        git(self.repo, "tag", "-a", "-m", "m", "v3.0.0-rc.1")
        self.assertEqual(self.run_command("order", "--tag", "v3.0.0-rc.1").returncode, 0)
        refused = self.run_command("order", "--tag", "v1.0.0")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("v3.0.0-rc.1", refused.stderr)
        self.assertEqual(self.run_command("order", "--tag", "some-set-v1").returncode, 1)

    def test_a_refusal_says_a_tag_that_never_published_may_be_deleted(self) -> None:
        git(self.repo, "tag", "-a", "-m", "m", "v9.0.0")
        git(self.repo, "tag", "-a", "-m", "m", "v1.0.0")
        self.assertIn("never published", self.run_command("order", "--tag", "v1.0.0").stderr)

    def test_order_fails_closed_when_git_cannot_answer(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        self.assertEqual(self.run_command("order", "--tag", "v1.0.0", cwd=outside).returncode, 1)
        git(self.repo, "tag", "-a", "-m", "m", "v1.0.0")
        self.assertEqual(self.run_command("order", "--tag", "v2.0.0").returncode, 1, "the tag being checked does not exist")

    def test_flags_prints_one_flag_a_line(self) -> None:
        self.assertEqual(self.run_command("flags", "--tag", "v1.2.3").stdout.split(), ["--latest"])
        self.assertEqual(self.run_command("flags", "--tag", "v1.2.3-rc.1").stdout.split(), ["--prerelease", "--latest=false"])
        self.assertEqual(self.run_command("flags", "--tag", "nope").returncode, 1)

    def test_previous_starts_from_a_published_release_not_a_tag_that_never_published(self) -> None:
        published = self.root / "published.txt"
        published.write_text("v1.0.0\nrun-delivery-v1\n", encoding="utf-8")
        for name in ("v1.0.0", "v1.1.0", "v1.1.1"):
            git(self.repo, "tag", "-a", "-m", "m", name)
        done = self.run_command("previous", "--tag", "v1.1.1", "--published", str(published))
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "v1.0.0"))
        published.write_text("run-delivery-v1\n", encoding="utf-8")
        done = self.run_command("previous", "--tag", "v1.1.1", "--published", str(published))
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_notes_prints_the_annotation_the_digests_and_the_list(self) -> None:
        git(self.repo, "tag", "-a", "-m", "The annotation.", "v1.2.3")
        output = self.root / "out"
        output.mkdir()
        (output / "core-v1.2.3.release.json").write_text(
            json.dumps({"profile": "core", "releaseId": "core-v1.2.3", "archiveSha256": "a" * 64}), encoding="utf-8")
        generated = self.root / "generated.md"
        generated.write_text("## What's Changed\n", encoding="utf-8")
        arguments = ("notes", "--tag", "v1.2.3", "--out", str(output), "--generated", str(generated))
        done = self.run_command(*arguments)
        self.assertEqual(done.returncode, 0, done.stderr)
        positions = [done.stdout.index(text) for text in ("The annotation.", "a" * 64, "## What's Changed")]
        self.assertEqual(positions, sorted(positions))
        (output / "core-v1.2.3.release.json").unlink()
        self.assertEqual(self.run_command(*arguments).returncode, 1)

    @unittest.skipUnless(os.name == "posix", "the fake gh is a POSIX script, and the release job runs on Ubuntu")
    def test_existing_tells_absent_from_present_and_stops_on_anything_else(self) -> None:
        arguments = ("existing", "--tag", "v1.0.0")
        done = self.run_command(*arguments, environment=self.fake_gh(view={"stdout": "title: v1.0.0\n"}))
        self.assertEqual((done.returncode, done.stdout), (0, "publish=false\n"))
        done = self.run_command(*arguments, environment=self.fake_gh(view={"stderr": "release not found\n", "code": 1}))
        self.assertEqual((done.returncode, done.stdout), (0, "publish=true\n"))
        done = self.run_command(*arguments, environment=self.fake_gh(view={"stderr": "HTTP 502: Bad Gateway\n", "code": 1}))
        self.assertEqual((done.returncode, done.stdout), (1, ""))

    def test_there_is_no_command_that_reads_ci(self) -> None:
        done = self.run_command("ci", "--tag", "v1.0.0", "--repo", "owner/name", "--commit", self.commit)
        self.assertEqual(done.returncode, 2)
        self.assertIn("invalid choice: 'ci'", done.stderr)
        source = SCRIPT.read_text(encoding="utf-8")
        for withdrawn in ("actions/workflows", "actions/runs", "workflow_runs", "gh run", '"api"', "time.sleep", "wait_for_success"):
            with self.subTest(withdrawn=withdrawn):
                self.assertNotIn(withdrawn, source)
        for name in ("decide", "wait_for_success", "Decision", "REQUIRED_JOBS", "INFORMING_JOBS", "WAIT_LIMIT_SECONDS"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(channel, name))


class WorkflowWiringTests(unittest.TestCase):
    """Text pins cover only the wiring; the decisions above run for real."""

    def setUp(self) -> None:
        self.workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        head, jobs = self.workflow.split("\njobs:\n", 1)
        self.head = head
        guard, publish = jobs.split("\n  publish:\n", 1)
        self.guard, self.publish = guard, publish

    def assert_line(self, job: str, line: str) -> None:
        # A whole line, so a suffix such as `|| true` cannot neutralise a guard and leave this green.
        self.assertRegex(job, rf"(?m)^ *{re.escape(line)}$")

    def test_every_guard_is_a_whole_line_that_nothing_can_excuse(self) -> None:
        same = 'test "$(git rev-parse "refs/tags/$TAG^{commit}")" = "$GITHUB_SHA"'
        order = 'python scripts/release_channel.py order --tag "$TAG"'
        fetch = 'git fetch --force --tags origin "refs/tags/$TAG:refs/tags/$TAG"'
        for line in (fetch, same, order, 'test "$(git cat-file -t "refs/tags/$TAG")" = tag',
                     'git merge-base --is-ancestor "refs/tags/$TAG^{commit}" origin/main',
                     'run: python scripts/release_channel.py existing --tag "$TAG" >> "$GITHUB_OUTPUT"'):
            with self.subTest(line=line):
                self.assert_line(self.guard, line)
        for line in (fetch, same, order, 'gh release create "$TAG" --verify-tag --title "$TAG" --notes-file "$RUNNER_TEMP/notes.md" $flags "$OUT"/*'):
            with self.subTest(line=line):
                self.assert_line(self.publish, line)
        self.assertEqual(self.guard.count("set -euo pipefail"), 1)
        self.assertEqual(self.publish.count("set -euo pipefail"), 1)
        self.assertNotIn("|| true", self.workflow)

    def test_the_conditions_that_decide_whether_a_guard_runs_are_pinned_too(self) -> None:
        self.assert_line(self.guard, "publish: ${{ steps.existing.outputs.publish }}")
        self.assert_line(self.publish, "if: needs.guard.outputs.publish == 'true'")
        self.assertEqual(len(re.findall(r"(?m)^ *(?:- )?if: ", self.guard)), 0)
        self.assertEqual(len(re.findall(r"(?m)^ *(?:- )?if: ", self.publish)), 1)
        for forbidden in ("set +e", "exit 0", "always()", "cancelled()", "failure()"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.workflow)

    def test_only_version_tags_trigger_publish(self) -> None:
        self.assertRegex(self.head, r'(?m)^on:\n  push:\n    tags: \["v\[0-9\]\*"\]$')
        self.assertNotIn("branches:", self.head)
        self.assertNotIn("workflow_dispatch", self.head)
        self.assertNotIn("*-v", self.head)

    def test_write_permissions_appear_only_on_the_publish_job(self) -> None:
        self.assertRegex(self.head, r"(?m)^permissions: \{\}$")
        self.assertRegex(self.guard, r"(?m)^    permissions:\n      contents: read\n    outputs:$")
        self.assertNotIn("write", self.guard)
        self.assertRegex(self.publish, r"(?m)^    permissions:\n      contents: write\n      id-token: write\n      attestations: write$")
        self.assertEqual(self.workflow.count(": write"), 3)

    def test_no_job_calls_the_matrix(self) -> None:
        self.assertNotIn("uses: ./.github/workflows/ci.yml", self.workflow)

    def test_no_step_reads_ci_or_waits_for_it(self) -> None:
        for withdrawn in ("release_channel.py ci", "actions: read", "actions/workflows", "actions/runs", "gh run", "gh workflow",
                          "workflow_run", "check-runs", "check-suites", "commits/$GITHUB_SHA/status", "REPOSITORY/commits"):
            with self.subTest(withdrawn=withdrawn):
                self.assertNotIn(withdrawn, self.workflow)
        self.assertNotRegex(self.workflow, r"(?i)\bCI's (success|result|run)\b|\bwait(s|ing)? (for|on) CI\b|\brequires? CI\b")
        self.assertEqual(re.findall(r"scripts/release_channel\.py (\w+)", self.workflow),
                         ["order", "existing", "order", "previous", "notes", "flags"])
        self.assertEqual(re.findall(r"(?m)^      - name: (.+)$", self.guard), ["Check the tag", "Stop if the release already exists"])
        # No wait is left for the guard to outlast.
        self.assertLessEqual(int(re.search(r"(?m)^    timeout-minutes: (\d+)$", self.guard).group(1)), 15)

    def test_every_guard_precedes_publishing_and_the_publish_job_needs_the_guard(self) -> None:
        guards = ('test "$(git cat-file -t "refs/tags/$TAG")" = tag', 'git merge-base --is-ancestor "refs/tags/$TAG^{commit}" origin/main',
                  'scripts/release_channel.py order --tag "$TAG"', 'scripts/release_channel.py existing --tag "$TAG"')
        for guard in guards:
            with self.subTest(guard=guard):
                self.assertIn(guard, self.guard)
        self.assertRegex(self.publish, r"(?m)^    needs: guard$")
        self.assertNotIn("gh release create", self.guard)

    def test_a_moved_tag_publishes_nothing_because_both_jobs_tie_it_to_the_commit_they_run_on(self) -> None:
        same = 'test "$(git rev-parse "refs/tags/$TAG^{commit}")" = "$GITHUB_SHA"'
        fetch = 'git fetch --force --tags origin "refs/tags/$TAG:refs/tags/$TAG"'
        for job in (self.guard, self.publish):
            self.assertIn(same, job)
            self.assertLess(job.index(fetch), job.index(same))
        self.assertLess(self.guard.index(same), self.guard.index('release_channel.py order'))
        self.assertLess(self.publish.index(same), self.publish.index("gh release create"))

    def test_the_publish_step_checks_the_order_again_after_the_build(self) -> None:
        recheck = self.publish.index('scripts/release_channel.py order --tag "$TAG"')
        self.assertLess(self.publish.index("scripts/cut-release.py"), recheck)
        self.assertLess(self.publish.index('git fetch --force --tags origin'), recheck)
        self.assertLess(recheck, self.publish.index("gh release create"))

    def test_an_existing_release_stops_later_jobs_and_is_never_replaced(self) -> None:
        self.assertIn('scripts/release_channel.py existing --tag "$TAG" >> "$GITHUB_OUTPUT"', self.guard)
        for forbidden in ("--clobber", "gh release upload", "gh release delete"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.workflow)

    def test_provenance_precedes_publishing_and_both_cover_every_output_file(self) -> None:
        cut = self.publish.index("scripts/cut-release.py")
        provenance = self.publish.index("actions/attest-build-provenance@")
        release = self.publish.index("gh release create")
        self.assertLess(cut, provenance)
        self.assertLess(provenance, release)
        self.assertIn("subject-path: ${{ runner.temp }}/release/*", self.publish)
        self.assertIn('"$OUT"/*', self.publish[release:])
        self.assertNotIn("if: always()", self.publish)
        self.assertNotIn("continue-on-error", self.workflow)

    def test_publish_installs_the_test_extra_on_ubuntu_with_cis_python(self) -> None:
        self.assertIn('python -m pip install -e ".[test]"', self.publish)
        self.assertRegex(self.publish, r"(?m)^    runs-on: ubuntu-latest$")
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn('python-version: "3.11"', self.publish)
        self.assertIn('python-version: "3.11"', ci.split("\n  cut-release:\n", 1)[1])
        self.assertIn('python -m pip install -e ".[test]"', ci.split("\n  cut-release:\n", 1)[1])

    def test_ci_still_defines_the_validate_matrix_and_cut_release(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        systems = json.loads(re.search(r"\) && '(\[[^']*\])' \|\| '", workflow).group(1))
        self.assertEqual(systems, ["ubuntu-latest", "macos-latest"])
        self.assertIn('python-version: ["3.11"]', workflow)
        self.assertEqual(re.findall(r"(?m)^  ([a-z-]+):$", workflow.split("\njobs:\n", 1)[1]), ["validate", "cut-release"])
        self.assertNotRegex(workflow, r"(?i)gates? (publishing|a release|releases)")

    def test_ci_proves_the_release_path_with_nothing_above_read_permission(self) -> None:
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertRegex(ci, r"(?m)^permissions:\n  contents: read$")
        self.assertNotIn("write", ci.split("\njobs:\n", 1)[0])
        self.assertNotIn("workflow_call", ci)
        job = ci.split("\n  cut-release:\n", 1)[1]
        self.assertRegex(job, r"(?m)^    runs-on: ubuntu-latest$")
        self.assertIn('scripts/cut-release.py --version v0.0.0-rc.0 --out "$RUNNER_TEMP/release"', job)
        self.assertIn('git status --porcelain', job)

    def test_every_ci_system_sizes_its_workers_from_the_runner(self) -> None:
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"(?m)^    timeout-minutes: (\d+)$", ci), ["120", "120"])
        self.assertEqual(re.findall(r"(?m)^.*scripts/run-tests\.py.*$", ci),
                         ["""        run: python scripts/run-tests.py --workers "$(python -c 'import os; print(os.cpu_count())')\""""])
        self.assertRegex(ci, r"(?m)^      - shell: bash\n        run: python scripts/run-tests\.py ")

    def test_ci_runs_ubuntu_and_adds_macos_on_a_push_to_main_or_with_the_label(self) -> None:
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("""        os: ${{ fromJSON((github.event_name == 'push' """
                      """|| contains(github.event.pull_request.labels.*.name, 'full-matrix')) """
                      """&& '["ubuntu-latest", "macos-latest"]' || '["ubuntu-latest"]') }}\n""", ci)
        self.assertNotIn("windows-latest", ci)
        self.assertRegex(ci, r"(?m)^  push:\n    branches: \[main\]$")
        self.assertRegex(ci, r"(?m)^on:\n  pull_request:\n  push:$")
        self.assertNotIn("labeled", ci)
        self.assertNotIn(" if:", ci.split("\n  cut-release:\n", 1)[1].split("steps:", 1)[0], "it runs on every change request")

    def test_a_superseded_change_request_run_stops_and_a_run_on_main_is_never_cancelled(self) -> None:
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        head = ci.split("\njobs:\n", 1)[0]
        # A group keeps one pending run even without cancel-in-progress, so a group shared on main would drop a merge's run.
        self.assertRegex(head, r"(?m)^concurrency:\n"
                               r"(?:  #.*\n)*"
                               r"  group: \$\{\{ github\.event_name == 'pull_request' && format\('ci-\{0\}', github\.event\.pull_request\.number\) \|\| github\.run_id \}\}\n"
                               r"  cancel-in-progress: \$\{\{ github\.event_name == 'pull_request' \}\}$")

    def test_the_release_flags_and_notes_come_from_the_channel(self) -> None:
        self.assertIn('scripts/release_channel.py flags --tag "$TAG"', self.publish)
        self.assertIn("scripts/release_channel.py notes ", self.publish)
        self.assertIn('scripts/release_channel.py previous --tag "$TAG" --published "$RUNNER_TEMP/published.txt"', self.publish)
        self.assert_line(self.publish, 'gh release list --exclude-drafts --limit 1000 --json tagName --jq \'.[].tagName\' > "$RUNNER_TEMP/published.txt"')
        self.assertIn("--notes-file", self.publish)
        self.assertNotIn("--generate-notes", self.publish)


if __name__ == "__main__":
    unittest.main()
