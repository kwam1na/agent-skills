#!/usr/bin/env python3
"""The decisions a version tag has to pass before it publishes, for release.yml.

Commands:

    order --tag TAG                      refuse a tag that is not a version, or would go backwards
    ci --tag TAG --commit SHA --repo R   wait for CI's result on that commit, and require success
    flags --tag TAG                      print the `gh release create` latest flags, one a line
    existing --tag TAG                   print publish=false if the release exists, publish=true if it does not
    previous --tag TAG --published FILE  print the published tag the generated list starts from, if any
    notes --tag TAG --out DIR --generated FILE    print the release notes

Exit codes: 0 when the decision passes; 1 when it refuses. Nothing here publishes.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-rc\.(0|[1-9][0-9]*))?")
SIGNATURE = re.compile(r"(?m)^-----BEGIN (?:PGP SIGNATURE|SSH SIGNATURE|SIGNED MESSAGE)-----$")
DEFAULT_BRANCH = "main"
CI_WORKFLOW = "CI"
REQUIRED_JOBS = (
    "validate (ubuntu-latest, 3.11)",
    "cut-release",
)
# Informs whoever tags. Its result, or its absence, neither allows nor blocks a release, and nothing waits for it.
INFORMING_JOBS = (
    "validate (macos-latest, 3.11)",
)
PASS, WAIT, REFUSE, MISSING = "pass", "wait", "refuse", "missing"
WAIT_LIMIT_SECONDS = 150 * 60
# A run appears a few seconds after its push, so a tag pushed straight after a merge can arrive first.
MISSING_LIMIT_SECONDS = 5 * 60
POLL_SECONDS = 60


NEVER_PUBLISHED = "; a version tag that never published may be deleted to clear this"


class ChannelError(Exception):
    """The decision refuses, or its input could not be read."""


@dataclass(frozen=True)
class Decision:
    state: str
    reason: str


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


def decide(runs: list[dict], jobs_for: Callable[[int], list[dict]], commit: str, branch: str = DEFAULT_BRANCH) -> Decision:
    """CI's verdict on exactly this commit, on a push to the default branch, from its gating jobs alone.

    The run's own status and conclusion carry the informing jobs too, so they say only whether a job yet to appear may still come.
    """
    matching = [run for run in runs if run.get("head_sha") == commit and run.get("event") == "push"
                and run.get("head_branch") == branch and run.get("name") == CI_WORKFLOW]
    if not matching:
        return Decision(MISSING, f"no {CI_WORKFLOW} run for {commit} on a push to {branch}")
    run = max(matching, key=lambda item: item.get("run_number", 0))
    jobs = [job for job in jobs_for(run["id"]) if job.get("name") not in INFORMING_JOBS]
    finished = [job for job in jobs if job.get("status") == "completed"]
    failed = [f"{job.get('name')}: {job.get('conclusion')}" for job in finished if job.get("conclusion") != "success"]
    if failed:
        return Decision(REFUSE, f"{CI_WORKFLOW} run {run['id']} has jobs that did not succeed: {'; '.join(failed)}")
    named = {job.get("name") for job in finished}
    unfinished = [job.get("name") for job in jobs if job not in finished] + [name for name in REQUIRED_JOBS if name not in {job.get("name") for job in jobs}]
    if unfinished and run.get("status") != "completed":
        return Decision(WAIT, f"{CI_WORKFLOW} run {run['id']} is {run.get('status')}, without {', '.join(unfinished)}")
    missing = [name for name in REQUIRED_JOBS if name not in named]
    if missing or unfinished:
        return Decision(REFUSE, f"{CI_WORKFLOW} run {run['id']} has no finished job named {', '.join(missing or unfinished)}")
    return Decision(PASS, f"{CI_WORKFLOW} run {run['id']} succeeded with every job publishing requires")


def _say(line: str) -> None:
    # Flushed, so a wait is visible as it goes rather than only when it ends.
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def wait_for_success(decision: Callable[[], Decision], *, limit: float = WAIT_LIMIT_SECONDS,
                     missing_limit: float = MISSING_LIMIT_SECONDS, interval: float = POLL_SECONDS,
                     clock: Callable[[], float] | None = None, sleep: Callable[[float], None] | None = None,
                     say: Callable[[str], None] | None = None) -> bool:
    """Whether CI passed, waiting out a run still going. Anything but a pass publishes nothing."""
    clock, sleep, say = clock or time.monotonic, sleep or time.sleep, say or _say
    started = clock()
    missing_since = None
    while True:
        try:
            current = decision()
        except ChannelError as error:
            current = Decision(WAIT, f"could not read CI's result: {error}")
        say(current.reason)
        if current.state == PASS:
            return True
        if current.state == REFUSE:
            return False
        now = clock()
        if current.state == MISSING:
            missing_since = now if missing_since is None else missing_since
            if now - missing_since + interval > missing_limit:
                say(f"gave up: {current.reason}")
                return False
        else:
            missing_since = None
        if now - started + interval > limit:
            say(f"gave up after waiting {limit / 60:.0f} minutes: {current.reason}")
            return False
        sleep(interval)


def _gh_json(path: str) -> dict:
    done = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise ChannelError(f"gh api {path} failed: {done.stderr.strip()}")
    try:
        document = json.loads(done.stdout)
    except json.JSONDecodeError as error:
        raise ChannelError(f"gh api {path} did not return JSON: {error}") from error
    if not isinstance(document, dict):
        raise ChannelError(f"gh api {path} did not return an object")
    return document


def release_absent(tag: str) -> bool:
    """Whether no release exists for the tag. Anything but a clear answer refuses."""
    done = subprocess.run(["gh", "release", "view", tag], capture_output=True, text=True, check=False)
    if done.returncode == 0:
        return False
    if "release not found" in done.stderr:
        return True
    raise ChannelError(f"could not tell whether {tag} is published: {done.stderr.strip()}")


def _ci_decision(repo: str, commit: str, branch: str) -> Decision:
    runs = _gh_json(f"repos/{repo}/actions/workflows/ci.yml/runs?head_sha={commit}&event=push&branch={branch}&per_page=100")
    return decide(runs.get("workflow_runs", []), lambda run_id: _gh_json(
        f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100").get("jobs", []), commit, branch)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("order", "ci", "flags", "existing", "previous", "notes"):
        command = commands.add_parser(name)
        command.add_argument("--tag", required=True)
        if name == "ci":
            command.add_argument("--commit", required=True)
            command.add_argument("--repo", required=True)
        if name == "previous":
            command.add_argument("--published", type=Path, required=True)
        if name == "notes":
            command.add_argument("--out", type=Path, required=True)
            command.add_argument("--generated", type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path.cwd()
    try:
        if args.command == "order":
            check_order(args.tag, root)
        elif args.command == "ci":
            if not wait_for_success(lambda: _ci_decision(args.repo, args.commit, DEFAULT_BRANCH)):
                raise ChannelError(f"CI has not passed on {args.commit}, so {args.tag} publishes nothing")
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
