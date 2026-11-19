"""Validate and map half-open line claims against committed Git blobs."""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import tempfile
from pathlib import Path


class RegionError(Exception):
    pass


HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
REGULAR = {b"100644", b"100755"}


def valid_path(path: str) -> bool:
    return "\0" not in path and all(part not in ("", ".", "..") for part in path.split("/"))


def owners(ownership: dict, path: str) -> list[str]:
    return [lane for lane, patterns in ownership.items() if any(fnmatch.fnmatchcase(path, g) for g in patterns)]


def git(repo: Path, *args: str, check: bool = True,
        objects: str | None = None) -> subprocess.CompletedProcess:
    # Commit and blob reads must use the checkout's object store and literal paths.
    env = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_CEILING_DIRECTORIES",
                 "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                 "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_LITERAL_PATHSPECS", "GIT_ICASE_PATHSPECS"):
        env.pop(name, None)
    env["GIT_ATTR_NOSYSTEM"] = "1"
    if objects is not None:
        env["GIT_OBJECT_DIRECTORY"] = objects
    result = subprocess.run(["git", "--no-replace-objects", "--literal-pathspecs", "-c",
                             f"core.attributesFile={os.devnull}", *args], cwd=repo,
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
    return [line + b"\n" for line in result[:-1]] + ([result[-1]] if result[-1] else [])


def require_text(repo: Path, commit: str, path: str) -> None:
    # A private repository shares the source objects without inheriting
    # worktree, index or info/attributes overrides.
    objects = os.fsdecode(git(repo, "rev-parse", "--path-format=absolute", "--git-path", "objects").stdout.rstrip(b"\n"))
    object_format = git(repo, "rev-parse", "--show-object-format").stdout.decode("ascii").strip()
    with tempfile.TemporaryDirectory(prefix="region-attributes-") as directory:
        private = Path(directory)
        git(private, "init", "--bare", "--quiet", "--template=", f"--object-format={object_format}")
        git(private, "read-tree", commit, objects=objects)
        attrs = git(private, "check-attr", "--cached", "-z", "diff", "--", path, objects=objects).stdout
    _, _, driver, _ = attrs.split(b"\0")
    binary = driver == b"unset"
    if driver not in (b"unset", b"set", b"unspecified"):
        setting = git(repo, "config", "--bool", "--get", f"diff.{os.fsdecode(driver)}.binary", check=False)
        binary = setting.stdout.strip() == b"true"
    if binary:
        raise RegionError("binary changes require whole-file ownership")


def hunks(repo: Path, old: str, new: str, algorithm: str = "myers") -> list[tuple[int, int, int]]:
    patch = git(repo, "diff", "--text", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames",
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
    require_text(repo, claim["baseHead"], claim["path"])
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


def refuse_ambiguous_shifts(
    before: list[bytes], after: list[bytes], start: int, end: int, edits: list[tuple[int, int, int]],
) -> None:
    """Check equivalent positions for a hunk that can slide over repeated lines."""
    shift, previous_end = 0, 0
    for i, (first, removed, added) in enumerate(edits):
        old = first - 1
        left = right = old
        while left > previous_end and before[left + removed - 1] == after[left + shift + added - 1]:
            left -= 1
        next_start = edits[i + 1][0] - 1 if i + 1 < len(edits) else len(before)
        while right + removed < next_start and before[right] == after[right + shift]:
            right += 1
        if (left != old or right != old) and left + 1 <= end and right + max(1, removed) >= start:
            raise RegionError("repeated lines make the span's mapping ambiguous")
        previous_end = old + removed
        shift += added - removed


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
        require_text(repo, base, path)
        if old == new:
            return start, end
        before, after = lines(repo, old), lines(repo, new)
        edits = hunks(repo, old, new)
        result = remap(start, end, edits)
        refuse_ambiguous_shifts(before, after, start, end, edits)
        alternate = remap(start, end, hunks(repo, old, new, "histogram"))
        if result != alternate or remap(*result, hunks(repo, new, old)) != (start, end):
            raise RegionError("Git produced different line mappings")
        first, last = result
        if after[first - 1:last - 1] != before[start - 1:end - 1]:
            raise RegionError("the claimed lines differ on the target base")
        return result
    except RegionError as error:
        raise RegionError(f"{path} [{start}, {end}) at {anchor[:12]}: {error}. Regrant it against the current base") from error


def change_report(repo: Path, path: str, base: str, head: str,
                  claims: list[dict]) -> tuple[list[tuple[int, int]], list[dict]]:
    """Every hunk of ``path`` from ``base`` to ``head``, each with the claim that holds it or None: the claims' spans
    mapped to ``base``, and one row a hunk, {first, removed, added, where, claim, span}. A creation, deletion, mode or
    binary change raises, since only a whole-file claim holds one."""
    old_mode, old = entry(repo, base, path)
    new_mode, new = entry(repo, head, path)
    if old_mode != new_mode:
        raise RegionError("mode changes require whole-file ownership")
    require_text(repo, head, path)
    lines(repo, new)
    spans = [mapped(repo, claim, base) for claim in claims]
    rows = []
    for first, removed, added in hunks(repo, old, new):
        holder = next((index for index, (start, end) in enumerate(spans)
                       if (start <= first and first + removed <= end if removed else start < first < end)), None)
        rows.append({"first": first, "removed": removed, "added": added,
                     "where": f"[{first}, {first + removed})" if removed else f"insertion before line {first}",
                     "claim": None if holder is None else claims[holder],
                     "span": None if holder is None else spans[holder]})
    return spans, rows


def check_change(repo: Path, path: str, base: str, head: str, claims: list[dict]) -> None:
    if not claims:
        raise RegionError("the lane has no region claim")
    spans, rows = change_report(repo, path, base, head, claims)
    outside = [row["where"] for row in rows if row["claim"] is None]
    if outside:
        raise RegionError(f"{', '.join(outside)} {'is' if len(outside) == 1 else 'are'} outside a single owned span "
                          f"{spans} at {base[:12]}. Revise the claim before review")


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


def queue_problems(repo: Path, state: dict, lane: str, accepted_paths: list[str], base: str,
                   head: str) -> dict[str, str]:
    """The merge queue's ownership check of a synced tree against the base it syncs with, by path: each region-claimed
    path the lane changed outside its spans, and each path accepted as region-owned that the lane no longer holds.
    The queue and lead-claims's preview run this one function. Raises RegionError when Git can't say."""
    regions = state.get("laneRegions", [])
    accepted = set(accepted_paths)
    if not regions and not accepted:
        return {}
    regional_paths = {claim["path"] for claim in regions}
    changed = changed_paths(repo, base, head)
    outside = problems(repo, lane, base, head, changed, regions)
    for path in sorted(accepted.intersection(changed) - regional_paths):
        if owners(state.get("laneOwnership") or {}, path) != [lane]:
            outside[path] = "the lane has no current whole-file or region claim"
    return outside


# -- claims that follow the work ---------------------------------------------------------------------------------

COORDINATES = ("lane", "path", "baseHead", "start", "end")


def same_claim(one: dict, other: dict) -> bool:
    """Whether two claims are one by their coordinates, whatever story or regrant mark either carries."""
    return all(one.get(name) == other.get(name) for name in COORDINATES)


def claim_at(claim: dict, base: str, start: int, end: int) -> dict:
    """``claim``'s lane, path and story, over [start, end) at ``base``, without a regrant mark."""
    moved = {"lane": claim["lane"], "path": claim["path"], "baseHead": base, "start": start, "end": end}
    return {**moved, "story": claim["story"]} if claim.get("story") else moved


def is_strict_ancestor(repo: Path, older: str, newer: str) -> bool:
    return older != newer and not git(repo, "merge-base", "--is-ancestor", older, newer, check=False).returncode


def merge_commit(repo: Path, base: str, synced: str) -> str | None:
    """The commit on ``base``'s first-parent line that landed ``synced``: its tree, on a first parent that ``synced``
    contains. A squash, a merge commit and a fast-forward are all found. None when ``base`` doesn't hold it yet."""
    tree = git(repo, "rev-parse", f"{synced}^{{tree}}").stdout.decode().strip()
    listed = git(repo, "log", "--first-parent", "--format=%H %T %P", base).stdout.decode().splitlines()
    for line in listed:
        commit, commit_tree, *parents = line.split()
        if commit_tree != tree:
            continue
        if commit == synced or (parents and not git(repo, "merge-base", "--is-ancestor", parents[0], synced,
                                                    check=False).returncode):
            return commit
    return None


def absorb(start: int, end: int, edits: list[tuple[int, int, int]]) -> tuple[int, int]:
    """[start, end) after ``edits``, each hunk that touches it lying wholly inside it and absorbed into it."""
    shift = inner = 0
    for first, removed, added in edits:
        if removed:
            if first + removed <= start:
                shift += added - removed
            elif first >= end:
                continue
            elif start <= first and first + removed <= end:
                inner += added - removed
            else:
                raise RegionError(f"the landed hunk [{first}, {first + removed}) crosses the span's edge")
        elif first < start:
            shift += added
        elif first > end:
            continue
        elif start < first < end:
            inner += added
        else:
            raise RegionError(f"the landed insertion before line {first} sits at the span's edge")
    if end + inner <= start:
        raise RegionError("the landed change removed every line of the span")
    return start + shift, end + shift + inner


def carried(repo: Path, claim: dict, target: str) -> tuple[int, int]:
    """Where ``claim``'s lines are at ``target`` when every hunk between that touches them lies wholly inside them:
    the lead's boundary check, with the hunks inside absorbed. Raises RegionError when a hunk crosses an edge."""
    anchor, path = claim["baseHead"], claim["path"]
    validate_claim(repo, claim)
    old_mode, old = entry(repo, anchor, path)
    new_mode, new = entry(repo, target, path)
    if old_mode != new_mode:
        raise RegionError("the landing changed the file mode")
    require_text(repo, target, path)
    if old == new:
        return claim["start"], claim["end"]
    span = absorb(claim["start"], claim["end"], hunks(repo, old, new))
    if span != absorb(claim["start"], claim["end"], hunks(repo, old, new, "histogram")):
        raise RegionError("Git produced different line mappings")
    return span


def settle(repo: Path, regions: list[dict], story: str, lane: str, merge: str) -> dict:
    """The claims after ``story`` of ``lane`` landed at ``merge``: its own claims released; each other claim on a path
    the merge changed, anchored strictly before it, mapped to it, and kept as it is when it maps; one of ``lane``'s
    that doesn't, carried to ``merge`` when the landing's hunks lie wholly inside it; any other marked as needing a
    regrant, with its reason. Returns {regions, released, carried: [(old, new)], regrant: [claim]}."""
    parent = git(repo, "rev-parse", "--verify", "-q", f"{merge}^1", check=False).stdout.decode().strip()
    changed = set(changed_paths(repo, parent, merge)) if parent else set()
    result: dict = {"regions": [], "released": [], "carried": [], "regrant": []}
    for claim in regions:
        if claim.get("story") == story:
            result["released"].append(claim)
            continue
        if claim["path"] not in changed or not is_strict_ancestor(repo, claim["baseHead"], merge):
            result["regions"].append(claim)
            continue
        try:
            mapped(repo, claim, merge)
            result["regions"].append(claim)
            continue
        except RegionError as error:
            reason = str(error)
        if claim["lane"] == lane:
            try:
                start, end = carried(repo, claim, merge)
                moved = claim_at(claim, merge, start, end)
                for other in regions:
                    if other is claim or other["path"] != claim["path"] or other.get("story") == story:
                        continue
                    try:
                        held = mapped(repo, other, merge)
                    except RegionError:
                        continue
                    if overlapping((start, end), held):
                        raise RegionError(f"carried to [{start}, {end}) it would overlap {other['lane']}'s "
                                          f"[{held[0]}, {held[1]}) at {merge[:12]}")
                result["regions"].append(moved)
                result["carried"].append((claim, moved))
                continue
            except RegionError as error:
                reason = f"{claim['path']} [{claim['start']}, {claim['end']}) at {claim['baseHead'][:12]}: {error}"
        marked = claim if claim.get("regrant") else {**claim, "regrant": {"reason": reason[:600], "merge": merge}}
        result["regions"].append(marked)
        result["regrant"].append(marked)
    return result


def overlapping(one: tuple[int, int], other: tuple[int, int]) -> bool:
    return one[0] < other[1] and other[0] < one[1]


def coalesce(repo: Path, claims: list[dict], base: str) -> tuple[list[dict], list[list[dict]]]:
    """``claims`` with each run of one lane's touching or overlapping claims on one path, of one story or of none,
    once mapped to ``base``, replaced by one claim anchored at ``base``. A claim that doesn't map to ``base`` is left
    alone. Returns the claims, the merged one where the first of its run stood, and each run merged."""
    spans: dict[int, tuple[int, int]] = {}
    for index, claim in enumerate(claims):
        try:
            spans[index] = mapped(repo, claim, base)
        except RegionError:
            continue
    groups: dict[tuple, list[int]] = {}
    for index in spans:
        claim = claims[index]
        groups.setdefault((claim["lane"], claim["path"], claim.get("story")), []).append(index)
    replaced: dict[int, dict | None] = {}
    runs = []
    for members in groups.values():
        members.sort(key=lambda index: spans[index])
        run = [members[0]]
        for index in [*members[1:], None]:
            if index is not None and spans[index][0] <= max(spans[i][1] for i in run):
                run.append(index)
                continue
            if len(run) > 1:
                first = min(run)
                start, end = min(spans[i][0] for i in run), max(spans[i][1] for i in run)
                replaced.update({i: None for i in run})
                replaced[first] = claim_at(claims[first], base, start, end)
                runs.append([claims[i] for i in sorted(run)])
            run = [index] if index is not None else []
    out = [replaced.get(index, claim) for index, claim in enumerate(claims)]
    return [claim for claim in out if claim is not None], runs


def carve(repo: Path, claims: list[dict], path: str, base: str, gives: list[dict]) -> dict:
    """The claims on ``path`` after a carve at ``base``: every claim mapped to ``base``, each give's lines cut out of
    the claims it overlaps, the remnants kept with their lanes and stories, the gives added, and touching claims of
    one lane and story made one. Only what the carve cuts, adds or merges is anchored at ``base``; a claim no give
    touches is kept as it is. Returns {claims, removed, added}. Raises RegionError when a claim on the path doesn't
    map to ``base``, or the gives overlap each other."""
    ordered = sorted(gives, key=lambda give: (give["start"], give["end"]))
    for one, other in zip(ordered, ordered[1:]):
        if overlapping((one["start"], one["end"]), (other["start"], other["end"])):
            raise RegionError(f"the gives [{one['start']}, {one['end']}) and [{other['start']}, {other['end']}) overlap")
    on_path = [claim for claim in claims if claim["path"] == path]
    result = []
    for claim in on_path:
        start, end = mapped(repo, claim, base)
        pieces = [(start, end)]
        for give in ordered:
            cut = []
            for first, last in pieces:
                if not overlapping((first, last), (give["start"], give["end"])):
                    cut.append((first, last))
                    continue
                if first < give["start"]:
                    cut.append((first, give["start"]))
                if give["end"] < last:
                    cut.append((give["end"], last))
            pieces = cut
        if pieces == [(start, end)]:
            result.append(claim)
        else:
            result += [claim_at(claim, base, first, last) for first, last in pieces]
    for give in ordered:
        added = {"lane": give["lane"], "path": path, "baseHead": base, "start": give["start"], "end": give["end"]}
        result.append({**added, "story": give["story"]} if give.get("story") else added)
    result, _ = coalesce(repo, result, base)
    for claim in result:
        validate_claim(repo, claim)
    removed = [claim for claim in on_path if not any(claim is kept for kept in result)]
    added = [claim for claim in result if not any(claim is kept for kept in on_path)]
    others = [claim for claim in claims if claim["path"] != path]
    return {"claims": others + result, "removed": removed, "added": added}
