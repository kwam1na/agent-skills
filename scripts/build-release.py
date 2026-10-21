#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_skills.distribution import package_release, publish_release
from agent_skills.release import ReleaseError, build_release, verify_release
from agent_skills.validate import canonical_json


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--root", type=Path, default=ROOT)
    build.add_argument("--archive", type=Path, required=True)
    build.add_argument("--metadata", type=Path, required=True)
    build.add_argument("--release-id", required=True)
    build.add_argument("--profile", default="core")
    build.add_argument("--runtime-directory", type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--metadata", type=Path, required=True)
    verify.add_argument("--extract-to", type=Path)
    npm = subparsers.add_parser("npm")
    npm.add_argument("--archive", type=Path, required=True)
    npm.add_argument("--metadata", type=Path, required=True)
    npm.add_argument("--output-directory", type=Path, required=True)
    npm.add_argument("--package-name", required=True)
    npm.add_argument("--package-version", required=True)
    publish = subparsers.add_parser("publish")
    publish.add_argument("--root", type=Path, default=ROOT)
    publish.add_argument("--archive", type=Path, required=True)
    publish.add_argument("--metadata", type=Path, required=True)
    publish.add_argument("--tag", required=True)
    args = parser.parse_args()
    try:
        if args.command == "build":
            result = build_release(
                args.root,
                args.archive,
                args.metadata,
                release_id=args.release_id,
                profile_id=args.profile,
                runtime_directory=args.runtime_directory,
            )
        elif args.command == "npm":
            result = package_release(args.archive, args.metadata, args.output_directory,
                                     package_name=args.package_name, package_version=args.package_version)
        elif args.command == "publish":
            result = publish_release(args.root, args.archive, args.metadata, tag=args.tag)
        else:
            result = verify_release(args.archive, args.metadata, extract_to=args.extract_to)
    except (ReleaseError, OSError, subprocess.SubprocessError) as error:
        print(canonical_json({"error": str(error), "ok": False}), end="")
        return 1
    print(
        canonical_json(
            {
                "archiveSha256": result.archive_sha256,
                "ok": True,
                "profile": result.profile_id,
                "releaseId": result.release_id,
            }
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
