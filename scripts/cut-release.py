#!/usr/bin/env python3
"""Build, verify and qualify a full release set from the tree.

The output directory ends up holding exactly five archives, five release files
and four records, or none of them. An earlier set in that directory is removed
before anything else happens, and a new set appears only once every product
verifies and every record passes. The command owns the output directory: it
removes every release file there, of any version, and refuses a directory that
holds anything else.

Exit codes: 0 when the set is published; 1 when a build, verification or
qualifier fails; 2 for a refused version or output directory.
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


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_skills.release import RUNTIME_FILES, _path_digest, _release_view, build_release, verify_release


RUNTIME_SOURCE = ROOT / "product-runtime"
PRODUCTS = ("core", "linear", "bootstrap", "run-delivery", "jira")
RECORDS = ("core", "provider", "bootstrap", "linear")
VERSION_PATTERN = r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-rc\.(?:0|[1-9][0-9]*))?"
VERSION = re.compile(VERSION_PATTERN)
EARLIER_OUTPUT = re.compile(
    rf"(?:(?:{'|'.join(PRODUCTS)})-{VERSION_PATTERN}\.(?:zip|release\.json)"
    rf"|(?:{'|'.join(RECORDS)})-{VERSION_PATTERN}\.json)"
)


class Refusal(Exception):
    """The request itself is refused; nothing was built."""


def valid_version(version: str) -> bool:
    return VERSION.fullmatch(version) is not None


def named_outputs(version: str) -> set[str]:
    return {
        *(f"{profile}-{version}.zip" for profile in PRODUCTS),
        *(f"{profile}-{version}.release.json" for profile in PRODUCTS),
        *(f"{record}-{version}.json" for record in RECORDS),
    }


def rebound_runtime(profile: str, directory: Path) -> Path:
    """A copy of the stored runtime whose descriptor names this profile's workflow bytes."""
    stored = {path.name: path for path in RUNTIME_SOURCE.iterdir()}
    if set(stored) != RUNTIME_FILES:
        raise ValueError("cut_release.runtime: the store does not hold exactly the runtime files")
    descriptor = stored["runtime.json"].read_bytes()
    current = json.loads(descriptor)["workflowContentSha256"].encode("utf-8")
    if descriptor.count(current) != 1:
        raise ValueError("cut_release.runtime: the workflow digest does not occur exactly once")
    view = _release_view(ROOT, profile)
    workflow_digest = _path_digest(sorted(view), view).encode("utf-8")
    directory.mkdir(parents=True)
    for name, path in stored.items():
        (directory / name).write_bytes(path.read_bytes())
    (directory / "runtime.json").write_bytes(descriptor.replace(current, workflow_digest))
    return directory


def build_products(version: str, directory: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="agent-skills-cut-release-runtime-") as temporary:
        for profile in PRODUCTS:
            build_release(
                ROOT,
                directory / f"{profile}-{version}.zip",
                directory / f"{profile}-{version}.release.json",
                release_id=f"{profile}-{version}",
                profile_id=profile,
                runtime_directory=rebound_runtime(profile, Path(temporary) / profile),
            )


def _run_qualifier(name: str, arguments: list[str], record: Path, work: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / f"qualify-{name}.py"), *arguments, "--record", str(record)],
        cwd=work,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        # Some qualifiers report only on stdout, and the record goes with the temporary directory.
        reason = _failed_scenarios(record) or result.stderr.strip()[-2000:] or result.stdout.strip()[-2000:]
        raise ValueError(f"cut_release.qualifier: {name} exited {result.returncode}: {reason}")
    _require_passed(record)


def _failed_scenarios(record: Path) -> str:
    try:
        scenarios = json.loads(record.read_text(encoding="utf-8"))["scenarios"]
        return "; ".join(
            f"{scenario['id']}: {scenario.get('failure', 'failed')}"
            for scenario in scenarios
            if scenario["result"] != "passed"
        )
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def _require_passed(record: Path) -> None:
    # A qualifier's exit code alone does not say so: some exit 0 having written their record.
    summary = json.loads(record.read_text(encoding="utf-8")).get("summary", {})
    if summary.get("result") != "passed":
        raise ValueError(f"cut_release.record: {record.name} did not pass: {_failed_scenarios(record)}")


def _qualify(version: str, directory: Path, work: Path) -> None:
    def exact(profile: str, prefix: str = "") -> list[str]:
        return [
            f"--{prefix}archive", str(directory / f"{profile}-{version}.zip"),
            f"--{prefix}metadata", str(directory / f"{profile}-{version}.release.json"),
        ]

    core_record = directory / f"core-{version}.json"
    _run_qualifier("core", exact("core"), core_record, work)
    _run_qualifier("provider", exact("core"), directory / f"provider-{version}.json", work)
    _run_qualifier("bootstrap", exact("bootstrap"), directory / f"bootstrap-{version}.json", work)
    _run_qualifier(
        "linear",
        [*exact("core", "core-"), "--core-record", str(core_record), *exact("linear", "linear-")],
        directory / f"linear-{version}.json",
        work,
    )


def _check_set(version: str, directory: Path) -> None:
    if {path.name for path in directory.iterdir()} != named_outputs(version):
        raise ValueError("cut_release.outputs: the set does not hold exactly the named files")
    for profile in PRODUCTS:
        verify_release(directory / f"{profile}-{version}.zip", directory / f"{profile}-{version}.release.json")
    for record in RECORDS:
        _require_passed(directory / f"{record}-{version}.json")


def build_set(version: str, directory: Path, work: Path) -> None:
    build_products(version, directory)
    _qualify(version, directory, work)
    _check_set(version, directory)


def _publish(built: Path, out: Path, version: str) -> None:
    # The set appears by one rename, so no interruption leaves part of it at the output path.
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=out.parent, prefix=f".{out.name}."))
    try:
        for path in built.iterdir():
            shutil.copyfile(path, staging / path.name)
        _check_set(version, staging)
        staging.chmod(0o755)
        os.rename(staging, out)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _refuse_a_tracked_output(out: Path) -> None:
    if not out.is_relative_to(ROOT):
        return
    ignored = out != ROOT and subprocess.run(
        ["git", "check-ignore", "-q", f"{out.relative_to(ROOT).as_posix()}/"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    ).returncode == 0
    if not ignored:
        raise Refusal("the output directory inside the checkout must be ignored")


def _clear_earlier_set(out: Path) -> None:
    """Remove what an earlier run left, and refuse a directory holding anything else."""
    if not out.exists():
        return
    if not out.is_dir():
        raise Refusal("the output path is not a directory")
    for path in out.iterdir():
        if EARLIER_OUTPUT.fullmatch(path.name) and not path.is_dir():
            path.unlink()
    if any(out.iterdir()):
        raise Refusal("the output directory holds files no release run wrote")
    out.rmdir()


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(arguments)
    out = args.out.resolve()
    try:
        _refuse_a_tracked_output(out)
        _clear_earlier_set(out)
        if not valid_version(args.version):
            raise Refusal("the version must be vMAJOR.MINOR.PATCH, optionally -rc.N, with no leading zeros")
    except Refusal as error:
        print(f"cut-release refused: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"cut-release failed: an earlier set may remain in {out}: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    try:
        with tempfile.TemporaryDirectory(prefix="agent-skills-cut-release-", ignore_cleanup_errors=True) as temporary:
            built = Path(temporary) / "set"
            work = Path(temporary) / "work"
            built.mkdir()
            work.mkdir()
            build_set(args.version, built, work)
            _publish(built, out, args.version)
    except Exception as error:
        print(f"cut-release failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(f"cut-release: {args.version} published to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
