#!/usr/bin/env python3
"""Publishing a version tag from this machine, and the decisions it has to pass first.

Commands:

    publish --tag TAG [--dry-run]        check the pushed tag, build it with cut-release and publish its release
    order --tag TAG                      refuse a tag that is not a version, or would go backwards
    flags --tag TAG                      print the `gh release create` latest flags, one a line
    existing --tag TAG                   print publish=false if the release exists, publish=true if it does not
    previous --tag TAG --published FILE  print the published tag the generated list starts from, if any
    notes --tag TAG --out DIR --generated FILE    print the release notes

Exit codes: 0 when the decision passes or the release is published; 1 when it refuses. Only `publish` publishes,
and nothing here reads CI: a release rests on the full suite passing locally before the tag is pushed, where a
flaky test, one the release doesn't change that passes when rerun on its own, does not hold it.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable


VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-rc\.(0|[1-9][0-9]*))?")
SIGNATURE = re.compile(r"(?m)^-----BEGIN (?:PGP SIGNATURE|SSH SIGNATURE|SIGNED MESSAGE)-----$")


NEVER_PUBLISHED = "; a version tag that never published may be deleted to clear this"


class ChannelError(Exception):
    """The decision refuses, or its input could not be read."""


def parse_version(tag: str) -> tuple[int, int, int, int, int] | None:
    """A sort key for a version tag, with a candidate below its final; None for anything else."""
    match = VERSION.fullmatch(tag)
    if match is None:
        return None
    major, minor, patch, candidate = match.groups()
    final = candidate is None
    return int(major), int(minor), int(patch), 1 if final else 0, 0 if final else int(candidate)


def release_flags(tag: str) -> list[str]:
    key = parse_version(tag)
    if key is None:
        raise ChannelError(f"{tag!r} is not a version tag")
    return ["--latest"] if key[3] else ["--prerelease", "--latest=false"]


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True, check=False)


def _version_tags(names: Iterable[str]) -> list[str]:
    return [name for name in names if parse_version(name) is not None]


def check_order(tag: str, root: Path) -> None:
    """Refuse a tag that is not above every other version tag, or whose commit is behind the highest."""
    key = parse_version(tag)
    if key is None:
        raise ChannelError(f"{tag!r} is not a version tag: use vMAJOR.MINOR.PATCH, optionally -rc.N")
    listed = _git(root, "tag", "--list")
    if listed.returncode != 0:
        raise ChannelError(f"could not list tags: {listed.stderr.strip()}")
    others = [name for name in _version_tags(listed.stdout.splitlines()) if name != tag]
    if not others:
        return
    highest = max(others, key=parse_version)
    if parse_version(highest) >= key:
        raise ChannelError(f"{tag} is not above existing version tag {highest}{NEVER_PUBLISHED}")
    ancestor = _git(root, "merge-base", "--is-ancestor", f"refs/tags/{highest}^{{commit}}", f"refs/tags/{tag}^{{commit}}")
    if ancestor.returncode == 1:
        raise ChannelError(f"{tag} does not descend from the highest version tag, {highest}{NEVER_PUBLISHED}")
    if ancestor.returncode != 0:
        raise ChannelError(f"could not compare {tag} with {highest}: {ancestor.stderr.strip()}")


def previous_tag(tag: str, tags: Iterable[str]) -> str | None:
    """Where the generated list starts, given the published tags.

    A final skips candidates, so promoting one still lists its changes.
    """
    key = parse_version(tag)
    if key is None:
        raise ChannelError(f"{tag!r} is not a version tag")
    earlier = [name for name in _version_tags(tags) if parse_version(name) < key]
    if key[3]:
        earlier = [name for name in earlier if parse_version(name)[3]]
    return max(earlier, key=parse_version) if earlier else None


def annotation(tag: str, root: Path) -> str:
    """The tag's message, without any signature block."""
    shown = _git(root, "for-each-ref", "--format=%(contents)", f"refs/tags/{tag}")
    if shown.returncode != 0:
        raise ChannelError(f"could not read {tag}: {shown.stderr.strip()}")
    signature = SIGNATURE.search(shown.stdout)
    return (shown.stdout[:signature.start()] if signature else shown.stdout).strip()


def render_notes(message: str, output: Path, generated: str) -> str:
    """The annotation, when there is one, then the digest table, then GitHub's generated list."""
    metadata = sorted(output.glob("*.release.json"))
    if not metadata:
        raise ChannelError(f"no release files in {output}")
    table = "## Archives\n\n| Profile | Release id | Archive sha256 |\n| --- | --- | --- |\n"
    for path in metadata:
        release = json.loads(path.read_text(encoding="utf-8"))
        table += f"| {release['profile']} | `{release['releaseId']}` | `{release['archiveSha256']}` |\n"
    table += ("\nEach archive's `.release.json` records the same sha256. Check the downloaded zip against it"
              " (`shasum -a 256`) before installing, then install it as the README at this tag describes.\n")
    parts = [message.strip(), table.strip(), generated.strip()]
    return "\n\n".join(part for part in parts if part) + "\n"


def release_absent(tag: str) -> bool:
    """Whether no release exists for the tag. Anything but a clear answer refuses."""
    done = subprocess.run(["gh", "release", "view", tag], capture_output=True, text=True, check=False)
    if done.returncode == 0:
        return False
    if "release not found" in done.stderr:
        return True
    raise ChannelError(f"could not tell whether {tag} is published: {done.stderr.strip()}")


def _run(argv: list[str], cwd: Path, environment: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False, env=environment)


def _must(done: subprocess.CompletedProcess[str], what: str) -> str:
    if done.returncode != 0:
        raise ChannelError(f"{what} failed: {(done.stderr or done.stdout).strip()[-800:]}")
    return done.stdout


def _tag_commit(tag: str, root: Path) -> str:
    """Fetch the pushed tag object itself, so a lightweight local copy or a moved tag cannot stand in for it."""
    _must(_git(root, "fetch", "--force", "origin", f"refs/tags/{tag}:refs/tags/{tag}"), f"fetching {tag}")
    return _must(_git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}"), f"resolving {tag}").strip()


def check_tag(tag: str, root: Path) -> str:
    """Every guard a tag passes before anything is built; returns the commit it names."""
    if parse_version(tag) is None:
        raise ChannelError(f"{tag!r} is not a version tag: use vMAJOR.MINOR.PATCH, optionally -rc.N")
    commit = _tag_commit(tag, root)
    _must(_git(root, "fetch", "origin", "main"), "fetching main")
    if _git(root, "cat-file", "-t", f"refs/tags/{tag}").stdout.strip() != "tag":
        raise ChannelError(f"{tag} is not an annotated tag{NEVER_PUBLISHED}")
    merged = _git(root, "merge-base", "--is-ancestor", commit, "refs/remotes/origin/main")
    if merged.returncode == 1:
        raise ChannelError(f"{tag} names a commit not merged to main{NEVER_PUBLISHED}")
    _must(merged, f"checking {tag} is merged to main")
    check_order(tag, root)
    if not release_absent(tag):
        raise ChannelError(f"{tag} is already published, and a release is never replaced")
    return commit


def build(tree: Path, tag: str, work: Path, python: str | None) -> Path:
    """Run cut-release on the tagged tree in its own environment, pinned by the tree's .python-version."""
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if python is None:
        pin = (tree / ".python-version").read_text(encoding="utf-8").strip()
        venv = work / "venv"
        _must(_run(["uv", "venv", "--quiet", "--python", pin, str(venv)], tree), "creating the build environment")
        python = str(venv / "bin" / "python")
        _must(_run(["uv", "pip", "install", "--quiet", "--python", python, "-e", f"{tree}[test]"], tree, environment),
              "installing the release command's dependencies")
    out = work / "release"
    _must(_run([python, "-B", "scripts/cut-release.py", "--version", tag, "--out", str(out)], tree, environment),
          "cut-release")
    if _git(tree, "status", "--porcelain").stdout.strip():
        raise ChannelError("cut-release changed the tagged tree")
    return out


def publish(tag: str, root: Path, *, python: str | None = None, dry_run: bool = False) -> Path:
    """Check, build and publish one version tag from this machine. Returns the folder holding the release files."""
    commit = check_tag(tag, root)
    work = Path(tempfile.mkdtemp(prefix=f"agent-skills-publish-{tag}-"))
    tree = work / "tree"
    _must(_git(root, "worktree", "add", "--detach", str(tree), commit), "checking out the tagged tree")
    try:
        try:
            out = build(tree, tag, work, python)
        finally:
            _git(root, "worktree", "remove", "--force", str(tree))
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    # The build takes minutes: the tag is checked again before anything is published.
    if _tag_commit(tag, root) != commit:
        raise ChannelError(f"{tag} moved during the build, so nothing is published")
    check_order(tag, root)
    listed = _must(_run(["gh", "release", "list", "--exclude-drafts", "--limit", "1000", "--json", "tagName",
                         "--jq", ".[].tagName"], root), "listing published releases")
    previous = previous_tag(tag, listed.splitlines())
    repository = _must(_run(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], root),
                       "naming the repository").strip()
    generate = ["gh", "api", "-X", "POST", f"repos/{repository}/releases/generate-notes", "-f", f"tag_name={tag}"]
    if previous is not None:
        generate += ["-f", f"previous_tag_name={previous}"]
    generated = _must(_run([*generate, "--jq", ".body"], root), "generating the change list")
    notes = work / "notes.md"
    notes.write_text(render_notes(annotation(tag, root), out, generated), encoding="utf-8")
    if dry_run:
        return out
    files = sorted(str(path) for path in out.iterdir())
    _must(_run(["gh", "release", "create", tag, "--verify-tag", "--title", tag, "--notes-file", str(notes),
                *release_flags(tag), *files], root), f"publishing {tag}")
    shutil.rmtree(work, ignore_errors=True)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("publish", "order", "flags", "existing", "previous", "notes"):
        command = commands.add_parser(name)
        command.add_argument("--tag", required=True)
        if name == "publish":
            command.add_argument("--dry-run", action="store_true", help="stop before publishing and print the release folder")
            command.add_argument("--python", help="build with this interpreter instead of a fresh environment")
        if name == "previous":
            command.add_argument("--published", type=Path, required=True)
        if name == "notes":
            command.add_argument("--out", type=Path, required=True)
            command.add_argument("--generated", type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path.cwd()
    try:
        if args.command == "publish":
            out = publish(args.tag, root, python=args.python, dry_run=args.dry_run)
            if args.dry_run:
                print(out)
        elif args.command == "order":
            check_order(args.tag, root)
        elif args.command == "flags":
            print("\n".join(release_flags(args.tag)))
        elif args.command == "existing":
            print(f"publish={'true' if release_absent(args.tag) else 'false'}")
        elif args.command == "previous":
            previous = previous_tag(args.tag, args.published.read_text(encoding="utf-8").splitlines())
            if previous is not None:
                print(previous)
        else:
            generated = args.generated.read_text(encoding="utf-8")
            sys.stdout.write(render_notes(annotation(args.tag, root), args.out, generated))
    except ChannelError as error:
        print(f"release_channel: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
