"""Validate and map half-open line claims against committed Git blobs."""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from pathlib import Path


class RegionError(Exception):
    pass


HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
REGULAR = {b"100644", b"100755"}


def valid_path(path: str) -> bool:
    return "\0" not in path and all(part not in ("", ".", "..") for part in path.split("/"))


def owners(ownership: dict, path: str) -> list[str]:
    return [lane for lane, patterns in ownership.items() if any(fnmatch.fnmatchcase(path, g) for g in patterns)]


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    # Commit and blob reads must use the checkout's object store and literal paths.
    env = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_CEILING_DIRECTORIES",
                 "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                 "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_LITERAL_PATHSPECS", "GIT_ICASE_PATHSPECS"):
        env.pop(name, None)
    result = subprocess.run(["git", "--no-replace-objects", "--literal-pathspecs", *args], cwd=repo,
                            env=env, capture_output=True)
    if check and result.returncode:
        raise RegionError(f"git {args[0]} failed: {result.stderr.decode(errors='replace').strip()}")
    return result


def changed_paths(repo: Path, base: str, head: str) -> list[str]:
    result = git(repo, "diff", "--name-only", "--no-renames", "--ignore-submodules=none", "-z", base, head)
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def entry(repo: Path, commit: str, path: str) -> tuple[bytes, str]:
    listed = git(repo, "ls-tree", "-z", "--full-tree", commit, "--", path).stdout
    if listed:
        metadata, name = listed[:-1].split(b"\t", 1)
        mode, kind, oid = metadata.split()
        if name == os.fsencode(path) and mode in REGULAR and kind == b"blob":
            return mode, oid.decode("ascii")
    raise RegionError("file creation, deletion, rename and type changes require whole-file ownership")


def lines(repo: Path, oid: str) -> list[bytes]:
    blob = git(repo, "cat-file", "blob", oid).stdout
    if b"\0" in blob:
        raise RegionError("binary changes require whole-file ownership")
    result = blob.split(b"\n")
    return result if result[-1] else result[:-1]


def require_text_diff(repo: Path, path: str, base: str, head: str) -> None:
    # A repository may mark a NUL-free blob as binary for this path.
    stats = git(repo, "diff", "--numstat", "-z", "--no-renames", "--no-textconv", "--no-ext-diff",
                base, head, "--", path).stdout
    if stats.startswith(b"-\t-\t"):
        raise RegionError("binary changes require whole-file ownership")


def hunks(repo: Path, old: str, new: str, algorithm: str = "myers") -> list[tuple[int, int, int]]:
    patch = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames",
                "--no-indent-heuristic", "--inter-hunk-context=0", "--unified=0", "--output-indicator-new=+",
                "--output-indicator-old=-", "--output-indicator-context= ",
                f"--diff-algorithm={algorithm}", old, new).stdout
    result = []
    for match in HUNK.finditer(patch):
        first, removed, _, added = (int(n) if n is not None else 1 for n in match.groups())
        # Git describes an empty old range by the line before the insertion.
        result.append((first if removed else first + 1, removed, added))
    if not result:
        raise RegionError("Git could not classify the change as text hunks")
    return result


def validate_claim(repo: Path, claim: dict) -> None:
    _, oid = entry(repo, claim["baseHead"], claim["path"])
    if not 1 <= claim["start"] < claim["end"] <= len(lines(repo, oid)) + 1:
        raise RegionError("the span must name existing lines in the base blob")


def remap(start: int, end: int, edits: list[tuple[int, int, int]]) -> tuple[int, int]:
    shift = 0
    for first, removed, added in edits:
        if (removed and first < end and first + removed > start) or (not removed and start <= first <= end):
            raise RegionError("a base edit intersects the span or its insertion boundary")
        if first + removed <= start:
            shift += added - removed
    return start + shift, end + shift


def mapped(repo: Path, claim: dict, base: str) -> tuple[int, int]:
    anchor, path = claim["baseHead"], claim["path"]
    start, end = claim["start"], claim["end"]
    try:
        validate_claim(repo, claim)
        if anchor == base:
            return start, end
        if git(repo, "merge-base", "--is-ancestor", anchor, base, check=False).returncode:
            raise RegionError("the claim's base is outside the target base's history")
        old_mode, old = entry(repo, anchor, path)
        new_mode, new = entry(repo, base, path)
        if old_mode != new_mode:
            raise RegionError("the base changed the file mode")
        if old == new:
            return start, end
        require_text_diff(repo, path, anchor, base)
        before, after = lines(repo, old), lines(repo, new)
        result = remap(start, end, hunks(repo, old, new))
        alternate = remap(start, end, hunks(repo, old, new, "histogram"))
        if result != alternate or remap(*result, hunks(repo, new, old)) != (start, end):
            raise RegionError("Git produced different line mappings")
        first, last = result
        # A neighboring identical line could belong to either side of a shift.
        # Check for that ambiguity even if the diff algorithms picked the same side.
        if after[first - 1:last - 1] != before[start - 1:end - 1]:
            raise RegionError("the claimed lines differ on the target base")
        for old_edge, new_edge in ((start - 1, first - 1), (end - 1, last - 1)):
            old_neighbors = before[max(0, old_edge - 1):old_edge + 1]
            new_neighbors = after[max(0, new_edge - 1):new_edge + 1]
            if old_neighbors != new_neighbors and any(
                len(pair) == 2 and pair[0] == pair[1] for pair in (old_neighbors, new_neighbors)
            ):
                raise RegionError("repeated lines make a span boundary ambiguous")
        return result
    except RegionError as error:
        raise RegionError(f"{path} [{start}, {end}) at {anchor[:12]}: {error}. Regrant it against the current base") from error


def check_change(repo: Path, path: str, base: str, head: str, claims: list[dict]) -> None:
    if not claims:
        raise RegionError("the lane has no region claim")
    old_mode, old = entry(repo, base, path)
    new_mode, new = entry(repo, head, path)
    if old_mode != new_mode:
        raise RegionError("mode changes require whole-file ownership")
    require_text_diff(repo, path, base, head)
    lines(repo, new)
    spans = [mapped(repo, claim, base) for claim in claims]
    for first, removed, _ in hunks(repo, old, new):
        if removed:
            allowed = any(start <= first and first + removed <= end for start, end in spans)
        else:
            allowed = any(start < first < end for start, end in spans)
        if not allowed:
            where = f"[{first}, {first + removed})" if removed else f"insertion before line {first}"
            raise RegionError(f"{where} is outside a single owned span {spans} at {base[:12]}. Revise the claim before review")


def problems(repo: Path, lane: str, base: str, head: str, changed: list[str], regions: list[dict]) -> dict[str, str]:
    by_path: dict[str, list[dict]] = {}
    for claim in regions:
        by_path.setdefault(claim["path"], []).append(claim)
    outside = {}
    for path in changed:
        if path not in by_path:
            continue
        try:
            check_change(repo, path, base, head, [claim for claim in by_path[path] if claim["lane"] == lane])
        except RegionError as error:
            outside[path] = str(error)
    return outside
