#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from agent_skills.release import ReleaseError, build_release, verify_release
from agent_skills.validate import canonical_json
from qualification_pending import MarkerError, load_marker


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
    args = parser.parse_args()
    if args.command == "build":
        # A release is never cut while any qualification record is stale.
        try:
            marker = load_marker(args.root)
        except MarkerError as error:
            print(canonical_json({"error": str(error), "ok": False}), end="")
            return 1
        if marker is not None:
            print(
                canonical_json({
                    "error": "qualification is pending; re-earn the named sets and delete the marker before cutting a release",
                    "ok": False,
                    "qualificationPending": marker["sets"],
                }),
                end="",
            )
            return 1
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
        else:
            result = verify_release(args.archive, args.metadata, extract_to=args.extract_to)
    except ReleaseError as error:
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
