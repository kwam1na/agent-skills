#!/usr/bin/env python3
"""Small, offline sensor for the v1 catalog/profile/provenance documents."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINE_REVISION = "342850073464a2c9b6198d32d11475f5fa5cbe53"
EXPECTED_VERSIONS = {
    "catalog.json": "agent-skills-catalog/1",
    "provenance.lock.json": "agent-skills-provenance/1",
}


def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def load_json(path: Path, findings: list[str]) -> object | None:
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        findings.append(f"missing document: {path.relative_to(ROOT)}")
        return None
    try:
        value = json.loads(contents)
    except json.JSONDecodeError as error:
        findings.append(f"invalid JSON: {path.relative_to(ROOT)}: {error.msg}")
        return None
    if canonical_json(value) != contents:
        findings.append(f"noncanonical serialization: {path.relative_to(ROOT)}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_profile(profile_id: str, profiles: dict[str, dict], seen: tuple[str, ...] = ()) -> list[str]:
    if profile_id in seen:
        raise ValueError(f"profile include cycle: {' -> '.join((*seen, profile_id))}")
    profile = profiles[profile_id]
    members: list[str] = []
    for included in profile["includes"]:
        members.extend(resolve_profile(included, profiles, (*seen, profile_id)))
    members.extend(profile["members"])
    return sorted(set(members))


def validate(root: Path) -> tuple[list[str], dict[str, object] | None, dict[str, dict], dict[str, dict]]:
    global ROOT
    ROOT = root.resolve()
    findings: list[str] = []
    catalog = load_json(ROOT / "catalog.json", findings)
    provenance = load_json(ROOT / "provenance.lock.json", findings)
    profiles: dict[str, dict] = {}

    for schema_name in ("catalog", "profile", "provenance"):
        schema = load_json(ROOT / "schemas" / f"{schema_name}.schema.json", findings)
        if not isinstance(schema, dict) or not isinstance(schema.get("$id"), str):
            findings.append(f"invalid schema identity: schemas/{schema_name}.schema.json")

    profiles_dir = ROOT / "profiles"
    if not profiles_dir.is_dir():
        findings.append("missing directory: profiles")
    else:
        for path in sorted(profiles_dir.glob("*.json")):
            document = load_json(path, findings)
            if not isinstance(document, dict):
                continue
            profile_id = document.get("id")
            if not isinstance(profile_id, str):
                findings.append(f"profile without string id: {path.relative_to(ROOT)}")
            elif profile_id in profiles:
                findings.append(f"duplicate profile id: {profile_id}")
            else:
                profiles[profile_id] = document

    if not isinstance(catalog, dict) or not isinstance(provenance, dict):
        return findings, catalog if isinstance(catalog, dict) else None, profiles, {}
    for filename, expected_version in EXPECTED_VERSIONS.items():
        document = catalog if filename == "catalog.json" else provenance
        if document.get("schemaVersion") != expected_version:
            findings.append(f"unsupported schema version: {filename}")

    provenance_entries = provenance.get("entries")
    if not isinstance(provenance_entries, list):
        findings.append("provenance entries must be a list")
        provenance_by_id: dict[str, dict] = {}
    else:
        provenance_by_id = {
            entry.get("id"): entry
            for entry in provenance_entries
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        }
        if len(provenance_by_id) != len(provenance_entries):
            findings.append("duplicate or invalid provenance id")

    skills = catalog.get("skills")
    skills_by_id: dict[str, dict] = {}
    if not isinstance(skills, list):
        findings.append("catalog skills must be a list")
    else:
        skills_by_id = {
            skill.get("id"): skill
            for skill in skills
            if isinstance(skill, dict) and isinstance(skill.get("id"), str)
        }
        if len(skills_by_id) != len(skills):
            findings.append("duplicate or invalid catalog skill id")
        if [skill.get("id") for skill in skills] != sorted(skills_by_id):
            findings.append("catalog skills are not sorted by id")

    discovered_paths = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "skills").glob("*/SKILL.md")
    )
    catalog_paths = sorted(skill.get("path") for skill in skills_by_id.values())
    if catalog_paths != discovered_paths:
        findings.append("catalog skill paths do not exactly match shipped SKILL.md files")

    shipped_files = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "skills").rglob("*")
        if path.is_file()
    )
    recorded_outputs = sorted(
        output.get("path")
        for entry in provenance_by_id.values()
        for output in entry.get("outputs", [])
        if isinstance(output, dict)
    )
    if recorded_outputs != shipped_files:
        findings.append("provenance outputs do not exactly match shipped files")

    for skill_id, skill in skills_by_id.items():
        provenance_id = skill.get("provenanceId")
        if provenance_id not in provenance_by_id:
            findings.append(f"missing provenance record for skill: {skill_id}")
        for profile_id in skill.get("profiles", []):
            if profile_id not in profiles:
                findings.append(f"unknown profile member: {skill_id} -> {profile_id}")

    for profile_id, profile in profiles.items():
        if profile.get("schemaVersion") != "agent-skills-profile/1":
            findings.append(f"unsupported schema version: profiles/{profile_id}.json")
        includes = profile.get("includes")
        members = profile.get("members")
        if not isinstance(includes, list) or not isinstance(members, list):
            findings.append(f"profile members/includes must be lists: {profile_id}")
            continue
        for included in includes:
            if included not in profiles:
                findings.append(f"unknown included profile: {profile_id} -> {included}")
        for member in members:
            if member not in skills_by_id:
                findings.append(f"unknown profile member: {profile_id} -> {member}")

    if not findings:
        for profile_id, profile in profiles.items():
            try:
                members = resolve_profile(profile_id, profiles)
            except ValueError as error:
                findings.append(str(error))
                continue
            files = sorted(
                output["path"]
                for member in members
                for output in provenance_by_id[skills_by_id[member]["provenanceId"]]["outputs"]
            )
            expected_files = profile.get("expectedFiles")
            if expected_files is not None and expected_files != files:
                findings.append(f"profile expectedFiles drift: {profile_id}")
        for skill_id, skill in skills_by_id.items():
            selected_by = sorted(
                profile_id
                for profile_id in profiles
                if skill_id in resolve_profile(profile_id, profiles)
            )
            if skill.get("profiles") != selected_by:
                findings.append(f"catalog/profile membership drift: {skill_id}")

    for provenance_id, entry in provenance_by_id.items():
        source = entry.get("source", {})
        if source.get("revision") != BASELINE_REVISION:
            findings.append(f"unapproved source revision: {provenance_id}")
        if not entry.get("license"):
            findings.append(f"missing license: {provenance_id}")
        outputs = entry.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            findings.append(f"missing outputs: {provenance_id}")
            continue
        for output in outputs:
            path = ROOT / output.get("path", "")
            if not path.is_file():
                findings.append(f"missing provenance output: {provenance_id} -> {output.get('path')}")
            elif output.get("sha256") != sha256(path):
                findings.append(f"provenance digest drift: {provenance_id} -> {output.get('path')}")

    return findings, catalog, profiles, skills_by_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--profile")
    args = parser.parse_args()
    findings, catalog, profiles, skills = validate(args.root)
    if findings:
        print(canonical_json({"ok": False, "findings": sorted(findings)}), end="")
        return 1
    if args.profile:
        if args.profile not in profiles:
            print(canonical_json({"ok": False, "findings": [f"unknown profile: {args.profile}"]}), end="")
            return 1
        provenance = json.loads((args.root / "provenance.lock.json").read_text(encoding="utf-8"))
        entries = {entry["id"]: entry for entry in provenance["entries"]}
        members = resolve_profile(args.profile, profiles)
        files = sorted(
            output["path"]
            for member in members
            for output in entries[skills[member]["provenanceId"]]["outputs"]
        )
        print(canonical_json({"files": files, "members": members, "profile": args.profile}), end="")
    else:
        print(canonical_json({"ok": True, "profiles": sorted(profiles), "skills": sorted(skills)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
