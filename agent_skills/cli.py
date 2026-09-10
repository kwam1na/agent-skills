from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from .generations import inspect_release
from .lifecycle import Lifecycle, require_mutation_authority
from .locking import RepositoryLock
from .product import product_status, reconcile_policy, run_runtime
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
    root.add_argument("--product", action="store_true", help="require runtime and reconciled policy readiness")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    harness = commands.add_parser("harness", add_help=False)
    harness.add_argument("--help", "-h", action="store_true", dest="runtime_help")
    harness.add_argument("arguments", nargs=argparse.REMAINDER)
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
        command.add_argument(
            "--bootstrap-policy",
            action="store_true",
            help="compile a missing first policy from explicit adopter bootstrap inputs",
        )
        _maintenance(command)
    apply = commands.add_parser("apply")
    _release_arguments(apply)
    apply.add_argument(
        "--bootstrap-policy",
        action="store_true",
        help="compile a missing first policy from explicit adopter bootstrap inputs",
    )
    _maintenance(apply)
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
        if getattr(args, "bootstrap_policy", False) and not args.product:
            raise ValueError(
                "product.bootstrap_requires_product: --bootstrap-policy requires --product"
            )
        if args.command == "validate":
            output = _validated_release(args.archive, args.metadata)
            print(canonical_json(output), end="")
            return 0
        lifecycle = Lifecycle(args.root)
        if args.command == "harness":
            return run_runtime(lifecycle, (["--help"] if args.runtime_help else []) + args.arguments)
        if args.product and args.command in {"apply", "install", "update"}:
            version = inspect_release(args.archive, args.metadata)
            if "runtime/runtime.json" not in {record["path"] for record in version["files"]}:
                raise ValueError("product.runtime_missing: supplied release has no delivery runtime")
        if args.command == "status":
            if args.product:
                with RepositoryLock(lifecycle.lock_path, shared=True):
                    output = product_status(lifecycle)
            else:
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
        elif args.command == "apply":
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
        if args.product and args.command in {"apply", "install", "update", "rollback", "recover"}:
            with RepositoryLock(lifecycle.lock_path):
                reconcile_policy(
                    lifecycle,
                    check=False,
                    bootstrap=args.command in {"apply", "install", "update"}
                    and args.bootstrap_policy,
                )
                output["status"] = product_status(lifecycle)
            if not output["status"]["productReady"]:
                raise ValueError("product.policy_unready: lifecycle changed; retry recover to reconcile policy")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
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
    return 1 if args.product and args.command == "status" and not output.get("productReady") else 0


if __name__ == "__main__":
    raise SystemExit(main())
