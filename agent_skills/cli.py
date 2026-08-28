from __future__ import annotations

import argparse
from pathlib import Path

from .generations import inspect_release
from .lifecycle import Lifecycle, require_mutation_authority
from .locking import RepositoryLock
from .validate import canonical_json


def _release_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)


def _maintenance(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--maintenance",
        action="store_true",
        help="confirm explicit maintainer repository authority",
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="agent-skills")
    root.add_argument("--root", type=Path, default=Path.cwd())
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("recovery-plan")
    validate = commands.add_parser("validate")
    _release_arguments(validate)
    plan = commands.add_parser("plan")
    _release_arguments(plan)
    diff = commands.add_parser("diff")
    _release_arguments(diff)
    for name in ("install", "update"):
        command = commands.add_parser(name)
        _release_arguments(command)
        _maintenance(command)
    adopt = commands.add_parser("adopt")
    _release_arguments(adopt)
    adopt.add_argument("--skill", required=True)
    adopt.add_argument("--expected-prior-sha256", required=True)
    _maintenance(adopt)
    for name in ("rollback", "remove", "recover"):
        command = commands.add_parser(name)
        _maintenance(command)
    unlock = commands.add_parser("break-stale-lock")
    _maintenance(unlock)
    return root


def _validated_release(archive: Path, metadata: Path) -> dict[str, object]:
    version = inspect_release(archive, metadata)
    return {
        "ok": True,
        "release": version["release"],
        "schemaVersion": "agent-skills-validation/1",
    }


def main(arguments: list[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    try:
        if args.command == "validate":
            output = _validated_release(args.archive, args.metadata)
            print(canonical_json(output), end="")
            return 0
        lifecycle = Lifecycle(args.root)
        if args.command == "status":
            output = lifecycle.status().as_dict()
        elif args.command == "recovery-plan":
            status = lifecycle.status().as_dict()
            output = {
                "blockers": status["blockers"],
                "lifecycle": status["lifecycle"],
                "recoveryPlan": status["recoveryPlan"],
                "schemaVersion": "agent-skills-recovery-plan/1",
            }
        elif args.command == "plan":
            output = lifecycle.plan(args.archive, args.metadata)
        elif args.command == "diff":
            output = lifecycle.diff(args.archive, args.metadata)
        elif args.command == "install":
            output = lifecycle.install(
                args.archive,
                args.metadata,
                maintainer=args.maintenance,
            )
        elif args.command == "adopt":
            output = lifecycle.adopt(
                args.archive,
                args.metadata,
                skill=args.skill,
                expected_prior_sha256=args.expected_prior_sha256,
                maintainer=args.maintenance,
            )
        elif args.command == "update":
            output = lifecycle.update(
                args.archive,
                args.metadata,
                maintainer=args.maintenance,
            )
        elif args.command == "rollback":
            output = lifecycle.rollback(maintainer=args.maintenance)
        elif args.command == "remove":
            output = lifecycle.remove(maintainer=args.maintenance)
        elif args.command == "recover":
            output = lifecycle.recover(maintainer=args.maintenance)
        else:
            require_mutation_authority(args.maintenance)
            RepositoryLock.break_stale(lifecycle.lock_path)
            output = {
                "action": "break-stale-lock",
                "schemaVersion": "agent-skills-operation/1",
            }
    except (OSError, ValueError) as error:
        message = str(error)
        code = message.split(":", 1)[0] if ":" in message else "lifecycle-error"
        print(
            canonical_json(
                {
                    "error": {"code": code, "message": message},
                    "ok": False,
                    "schemaVersion": "agent-skills-error/1",
                }
            ),
            end="",
        )
        return 1
    print(canonical_json(output), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
