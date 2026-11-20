from __future__ import annotations

import hashlib
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
    """The exit code and output are what an operator and the publish command read, so the commands themselves run here."""

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

    @unittest.skipUnless(os.name == "posix", "the fake gh is a POSIX script, and releases are published from macOS")
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
        for withdrawn in ("actions/workflows", "actions/runs", "workflow_runs", "gh run", "time.sleep", "wait_for_success"):
            with self.subTest(withdrawn=withdrawn):
                self.assertNotIn(withdrawn, source)
        for name in ("decide", "wait_for_success", "Decision", "REQUIRED_JOBS", "INFORMING_JOBS", "WAIT_LIMIT_SECONDS"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(channel, name))


FAKE_PUBLISH_GH = """#!{python}
import json, os, sys
folder = os.environ["FAKE_GH"]
with open(os.path.join(folder, "script.json"), encoding="utf-8") as stream:
    script = json.load(stream)
argv = sys.argv[1:]
call = {{"argv": argv}}
if "--notes-file" in argv:
    with open(argv[argv.index("--notes-file") + 1], encoding="utf-8") as stream:
        call["notes"] = stream.read()
with open(os.path.join(folder, "calls.jsonl"), "a", encoding="utf-8") as stream:
    stream.write(json.dumps(call) + "\\n")
key = " ".join(argv[:2]) if argv[0] in ("release", "repo") else argv[0]
reply = script.get(key, {{"stdout": {{"repo view": "owner/name\\n", "api": "## What's Changed\\n* a change\\n"}}.get(key, "")}})
sys.stdout.write(reply.get("stdout", ""))
sys.stderr.write(reply.get("stderr", ""))
sys.exit(reply.get("code", 0))
"""


FAKE_CUT_RELEASE = """import hashlib, json, os, sys
if os.environ.get("FAIL_CUT"):
    sys.exit("cut-release: a qualifier failed")
version = sys.argv[sys.argv.index("--version") + 1]
out = sys.argv[sys.argv.index("--out") + 1]
os.makedirs(out)
archive = os.path.join(out, f"core-{version}.zip")
with open(archive, "wb") as stream:
    stream.write(b"archive")
digest = hashlib.sha256(b"archive").hexdigest()
with open(os.path.join(out, f"core-{version}.release.json"), "w", encoding="utf-8") as stream:
    json.dump({"profile": "core", "releaseId": f"core-{version}", "archiveSha256": digest}, stream)
"""


@unittest.skipUnless(os.name == "posix", "the fake gh is a POSIX script, and releases are published from macOS")
class PublishTests(unittest.TestCase):
    """The whole publish command against a real origin, with gh and cut-release stood in for."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.origin = self.root / "origin.git"
        git(self.root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.repo = self.root / "repo"
        git(self.root, "clone", "-q", str(self.origin), str(self.repo))
        git(self.repo, "checkout", "-q", "-b", "main")
        (self.repo / "scripts").mkdir()
        (self.repo / "scripts/cut-release.py").write_text(FAKE_CUT_RELEASE, encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "first")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "tag", "-a", "v1.0.0", "-m", "The annotation.")
        git(self.repo, "push", "-q", "origin", "v1.0.0")
        self.gh = self.root / "fake"
        self.gh.mkdir()
        tool = self.gh / "gh"
        tool.write_text(FAKE_PUBLISH_GH.format(python=sys.executable), encoding="utf-8")
        tool.chmod(0o755)
        self.script({"release view": {"stderr": "release not found\n", "code": 1}})

    def script(self, replies: dict) -> None:
        (self.gh / "script.json").write_text(json.dumps(replies), encoding="utf-8")

    def publish(self, tag: str, *extra: str, environment: dict | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-B", str(SCRIPT), "publish", "--tag", tag, "--python", sys.executable, *extra],
                              cwd=self.repo, capture_output=True, text=True,
                              env={**os.environ, "FAKE_GH": str(self.gh), "PATH": f"{self.gh}{os.pathsep}{os.environ['PATH']}",
                                   **(environment or {})})

    def calls(self) -> list[dict]:
        log = self.gh / "calls.jsonl"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    def created(self) -> list[dict]:
        return [call for call in self.calls() if call["argv"][:2] == ["release", "create"]]

    def worktrees(self) -> int:
        return git(self.repo, "worktree", "list", "--porcelain").count("worktree ")

    def test_a_tag_publishes_every_built_file_with_its_notes_and_flags(self) -> None:
        done = self.publish("v1.0.0")
        self.assertEqual(done.returncode, 0, done.stderr)
        [create] = self.created()
        argv = create["argv"]
        self.assertEqual(argv[:6], ["release", "create", "v1.0.0", "--verify-tag", "--title", "v1.0.0"])
        self.assertIn("--latest", argv)
        self.assertEqual(sorted(Path(arg).name for arg in argv if arg.startswith(str(Path(tempfile.gettempdir()).resolve())) or "core-" in arg),
                         ["core-v1.0.0.release.json", "core-v1.0.0.zip"])
        positions = [create["notes"].index(text) for text in ("The annotation.", hashlib.sha256(b"archive").hexdigest(), "## What's Changed")]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(self.worktrees(), 1)

    def test_a_candidate_publishes_as_a_prerelease_that_is_not_latest(self) -> None:
        git(self.repo, "tag", "-a", "v1.1.0-rc.1", "-m", "A candidate.")
        git(self.repo, "push", "-q", "origin", "v1.1.0-rc.1")
        self.assertEqual(self.publish("v1.1.0-rc.1").returncode, 0)
        [create] = self.created()
        self.assertIn("--prerelease", create["argv"])
        self.assertIn("--latest=false", create["argv"])

    def test_a_dry_run_builds_and_prints_the_folder_and_publishes_nothing(self) -> None:
        done = self.publish("v1.0.0", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stderr)
        folder = Path(done.stdout.strip())
        self.addCleanup(shutil.rmtree, folder.parent, True)
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ["core-v1.0.0.release.json", "core-v1.0.0.zip"])
        self.assertEqual(self.created(), [])

    def test_a_lightweight_tag_is_refused_before_anything_is_built(self) -> None:
        git(self.repo, "tag", "v1.1.0")
        git(self.repo, "push", "-q", "origin", "v1.1.0")
        done = self.publish("v1.1.0")
        self.assertEqual(done.returncode, 1)
        self.assertIn("not an annotated tag", done.stderr)
        self.assertIn("may be deleted", done.stderr)
        self.assertEqual(self.created(), [])
        self.assertEqual(self.worktrees(), 1)

    def test_a_tag_on_a_commit_not_merged_to_main_is_refused(self) -> None:
        git(self.repo, "checkout", "-q", "-b", "side")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "unmerged")
        git(self.repo, "tag", "-a", "v1.1.0", "-m", "Unmerged.")
        git(self.repo, "push", "-q", "origin", "v1.1.0")
        done = self.publish("v1.1.0")
        self.assertEqual(done.returncode, 1)
        self.assertIn("not merged to main", done.stderr)
        self.assertEqual(self.created(), [])

    def test_a_tag_only_this_machine_has_is_refused(self) -> None:
        git(self.repo, "tag", "-a", "v1.1.0", "-m", "Never pushed.")
        done = self.publish("v1.1.0")
        self.assertEqual(done.returncode, 1)
        self.assertIn("fetching v1.1.0 failed", done.stderr)
        self.assertEqual(self.created(), [])

    def test_a_published_release_is_never_replaced(self) -> None:
        self.script({"release view": {"stdout": "title: v1.0.0\n"}})
        done = self.publish("v1.0.0")
        self.assertEqual(done.returncode, 1)
        self.assertIn("never replaced", done.stderr)
        self.assertEqual(self.created(), [])

    def test_a_failed_build_publishes_nothing_and_leaves_no_checkout(self) -> None:
        done = self.publish("v1.0.0", environment={"FAIL_CUT": "1"})
        self.assertEqual(done.returncode, 1)
        self.assertIn("cut-release failed", done.stderr)
        self.assertEqual(self.created(), [])
        self.assertEqual(self.worktrees(), 1)

    def test_the_command_builds_in_a_fresh_environment_and_never_replaces_or_attests(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('(tree / ".python-version")', source)
        self.assertIn('"uv", "pip", "install", "--quiet", "--python", python, "-e", f"{tree}[test]"', source)
        self.assertIn('"--verify-tag"', source)
        for withdrawn in ("--clobber", '"upload"', "attest", '"delete"'):
            with self.subTest(withdrawn=withdrawn):
                self.assertNotIn(withdrawn, source)

    def test_no_workflow_publishes_a_release(self) -> None:
        self.assertFalse((ROOT / ".github/workflows/release.yml").exists())
        for workflow in (ROOT / ".github/workflows").glob("*.yml"):
            text = workflow.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow.name):
                self.assertNotIn("gh release create", text)
                self.assertNotRegex(text, r"(?m)^    tags:")


class ContinuousIntegrationTests(unittest.TestCase):
    def test_ci_runs_only_cut_release_and_no_tests(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"(?m)^  ([a-z-]+):$", workflow.split("\njobs:\n", 1)[1]), ["cut-release"])
        for withdrawn in ("run-tests.py", "macos", "full-matrix", "matrix", "unittest"):
            with self.subTest(withdrawn=withdrawn):
                self.assertNotIn(withdrawn, workflow)
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

    def test_ci_runs_on_every_change_request_and_each_push_to_main(self) -> None:
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
