from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .catalog import dependency_findings, resolve_profile
from .errors import Finding


BASELINE_REVISION = "342850073464a2c9b6198d32d11475f5fa5cbe53"
SCHEMA_VERSIONS = {
    "catalog.json": "agent-skills-catalog/1",
    "provenance.lock.json": "agent-skills-provenance/1",
}
RULE_IDS = (
    "catalog.complete", "core.boundary", "core.leakage", "dependency.cycle",
    "dependency.reference", "dependency.required_closure", "document.canonical",
    "inventory.anti_vacuity", "path.case_unique", "path.safe",
    "path.symlink_contained", "profile.closure", "profile.reference",
    "provenance.coverage", "provenance.digest", "provenance.license",
    "router.deterministic", "router.repository_precedence", "schema.supported",
    "skill.frontmatter", "skill.reference", "skill.structure", "tracker.contract",
)
EXPECTED_SCAN_ROOTS = (
    ("catalog", "catalog.json"),
    ("fixture-controls", "tests/fixtures"),
    ("profiles", "profiles"),
    ("scenario-controls", "tests/scenarios"),
    ("provenance", "provenance.lock.json"),
    ("runtime", "agent_skills"),
    ("schemas", "schemas"),
    ("skills", "skills"),
)
LINK_RE = re.compile(r"\[[^]]*\]\(([^)]+)\)")
RESOURCE_RE = re.compile(
    r"(?<![\w./-])((?:references|agents|scripts|assets)/[\w./-]+\.[A-Za-z0-9]+)"
)
LEAKAGE_PATTERNS = (
    ("athena-name", re.compile(r"\bathena\b", re.IGNORECASE)),
    ("v26-ticket", re.compile(r"\bV26-\d+\b", re.IGNORECASE)),
    ("machine-path", re.compile(r"(?:/Users/[^\s`]+|[A-Za-z]:\\Users\\[^\s`]+)")),
    ("private-url", re.compile(r"https?://(?:[^\s/]*\.)?athena-os\.app\b", re.IGNORECASE)),
    ("project-url", re.compile(r"https?://linear\.app/v26-labs/", re.IGNORECASE)),
    (
        "provider-boundary",
        re.compile(
            r"\b(?:linear|mcp|https?|sdk|oauth|credentials?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "secret-literal",
        re.compile(
            r"(?:api[_-]?key|password|secret|access[_-]?token)"
            r"\s*[:=]\s*['\"]?[A-Za-z0-9_./+-]{8,}",
            re.IGNORECASE,
        ),
    ),
)
CORE_RUNTIME_PATHS = (
    "agent_skills/capabilities.py",
    "agent_skills/fake_tracker.py",
    "agent_skills/router.py",
    "agent_skills/workflows.py",
)


def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def sha256_bytes(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def safe_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    return (
        not path.is_absolute()
        and path.as_posix() == value
        and ".." not in path.parts
        and not windows_path.drive
        and not windows_path.is_absolute()
    )


@dataclass(frozen=True)
class ValidationResult:
    findings: tuple[Finding, ...]
    profiles: tuple[str, ...]
    rules_checked: tuple[str, ...]
    scan_roots: tuple[str, ...]
    skills: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "findingCount": len(self.findings),
            "findings": [finding.as_dict() for finding in self.findings],
            "ok": not self.findings,
            "profiles": list(self.profiles),
            "rulesChecked": list(self.rules_checked),
            "scanRoots": list(self.scan_roots),
            "skills": list(self.skills),
        }

    def to_json(self) -> str:
        return canonical_json(self.as_dict())


class CorpusReader:
    def __init__(self, root: Path, findings: list[Finding]) -> None:
        self.root = root.resolve()
        self.findings = findings

    def load_json(self, relative: str) -> object | None:
        path = self.root / relative
        try:
            contents = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            self.findings.append(Finding("catalog.complete", relative, "missing document"))
            return None
        try:
            value = json.loads(contents)
        except json.JSONDecodeError as error:
            self.findings.append(Finding("document.canonical", relative, f"invalid JSON: {error.msg}"))
            return None
        if canonical_json(value) != contents:
            self.findings.append(Finding("document.canonical", relative, f"noncanonical serialization: {relative}"))
        return value


def _validate_inventory(reader: CorpusReader) -> tuple[str, ...]:
    inventory = reader.load_json("validation-inventory.json")
    if not isinstance(inventory, dict):
        return ()
    scan_roots = inventory.get("scanRoots")
    if not isinstance(scan_roots, list) or not scan_roots:
        reader.findings.append(
            Finding(
                "inventory.anti_vacuity",
                "validation-inventory.json",
                "scan roots must not be empty",
            )
        )
        root_ids: tuple[str, ...] = ()
    else:
        declared_roots = [
            (item.get("id"), item.get("path"))
            for item in scan_roots
            if isinstance(item, dict)
        ]
        if declared_roots != list(EXPECTED_SCAN_ROOTS):
            reader.findings.append(
                Finding(
                    "inventory.anti_vacuity",
                    "validation-inventory.json",
                    "scan root mapping drift",
                )
            )
        root_ids = tuple(
            item.get("id")
            for item in scan_roots
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        )
        for item in scan_roots:
            if not isinstance(item, dict) or not safe_relative_path(item.get("path")):
                reader.findings.append(
                    Finding(
                        "inventory.anti_vacuity",
                        "validation-inventory.json",
                        "invalid scan root",
                    )
                )
                continue
            target = reader.root / item["path"]
            if not target.exists() or (target.is_dir() and not any(target.iterdir())):
                reader.findings.append(Finding("inventory.anti_vacuity", item["path"], "scan root is missing or empty"))
    rules = inventory.get("rules")
    inventory_rules: set[str] = set()
    fixture_ids: set[str] = set()
    for path in sorted((reader.root / "tests" / "fixtures").rglob("*.json")):
        try:
            fixture = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(fixture, dict) and isinstance(fixture.get("id"), str):
            fixture_ids.add(fixture["id"])
    if not isinstance(rules, list) or not rules:
        reader.findings.append(
            Finding(
                "inventory.anti_vacuity",
                "validation-inventory.json",
                "rules must not be empty",
            )
        )
    else:
        for rule in rules:
            if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
                reader.findings.append(
                    Finding(
                        "inventory.anti_vacuity",
                        "validation-inventory.json",
                        "invalid rule record",
                    )
                )
                continue
            rule_id = rule["id"]
            inventory_rules.add(rule_id)
            if rule.get("enabled") is not True:
                reader.findings.append(Finding("inventory.anti_vacuity", rule_id, "validation rule is disabled"))
            if not rule.get("positiveFixtures") or not rule.get("negativeFixtures"):
                reader.findings.append(
                    Finding(
                        "inventory.anti_vacuity",
                        rule_id,
                        "rule lacks positive or negative fixture coverage",
                    )
                )
            else:
                controls = (*rule["positiveFixtures"], *rule["negativeFixtures"])
                if any(not isinstance(control, str) or control not in fixture_ids for control in controls):
                    reader.findings.append(
                        Finding(
                            "inventory.anti_vacuity",
                            rule_id,
                            "rule names a missing fixture control",
                        )
                    )
    for rule_id in sorted(set(RULE_IDS) - inventory_rules):
        reader.findings.append(Finding("inventory.anti_vacuity", rule_id, "checked rule is absent from inventory"))
    for rule_id in sorted(inventory_rules - set(RULE_IDS)):
        reader.findings.append(Finding("inventory.anti_vacuity", rule_id, "inventory names an unknown rule"))
    if not inventory.get("scenarios"):
        reader.findings.append(
            Finding(
                "inventory.anti_vacuity",
                "validation-inventory.json",
                "scenarios must not be empty",
            )
        )
    lifecycle_scenarios = inventory.get("lifecycleScenarios")
    if not isinstance(lifecycle_scenarios, list) or not lifecycle_scenarios:
        reader.findings.append(
            Finding(
                "inventory.anti_vacuity",
                "validation-inventory.json",
                "lifecycle scenarios must not be empty",
            )
        )
    else:
        lifecycle_ids = [
            scenario.get("id")
            for scenario in lifecycle_scenarios
            if isinstance(scenario, dict)
        ]
        if (
            inventory.get("expectedLifecycleScenarioCount") != len(lifecycle_scenarios)
            or len(lifecycle_ids) != len(lifecycle_scenarios)
            or len(lifecycle_ids) != len(set(lifecycle_ids))
        ):
            reader.findings.append(
                Finding(
                    "inventory.anti_vacuity",
                    "validation-inventory.json",
                    "lifecycle scenario coverage drift",
                )
            )
    return root_ids


def _load_profiles(reader: CorpusReader) -> dict[str, dict]:
    profiles: dict[str, dict] = {}
    folded_ids: dict[str, str] = {}
    profiles_dir = reader.root / "profiles"
    if not profiles_dir.is_dir():
        reader.findings.append(Finding("catalog.complete", "profiles", "missing profile directory"))
        return profiles
    for path in sorted(profiles_dir.glob("*.json")):
        relative = path.relative_to(reader.root).as_posix()
        document = reader.load_json(relative)
        if not isinstance(document, dict):
            continue
        profile_id = document.get("id")
        if not isinstance(profile_id, str) or not profile_id:
            reader.findings.append(Finding("profile.reference", relative, "profile lacks string id"))
        elif profile_id in profiles:
            reader.findings.append(Finding("profile.reference", relative, f"duplicate profile id: {profile_id}"))
        else:
            folded = profile_id.casefold()
            prior = folded_ids.get(folded)
            if prior is not None and prior != profile_id:
                reader.findings.append(
                    Finding(
                        "path.case_unique",
                        relative,
                        f"case-fold profile id collision with {prior}",
                    )
                )
            folded_ids[folded] = profile_id
            profiles[profile_id] = document
    return profiles


def _validate_scenarios(
    reader: CorpusReader, profiles: dict[str, dict], skills: dict[str, dict]
) -> None:
    inventory = reader.load_json("validation-inventory.json")
    if not isinstance(inventory, dict):
        return
    scenarios = inventory.get("scenarios")
    if not isinstance(scenarios, list):
        return
    if inventory.get("expectedScenarioCount") != len(scenarios):
        reader.findings.append(
            Finding("inventory.anti_vacuity", "validation-inventory.json", "scenario count drift")
        )
    scenario_ids = [scenario.get("id") for scenario in scenarios if isinstance(scenario, dict)]
    if len(scenario_ids) != len(scenarios) or len(set(scenario_ids)) != len(scenario_ids):
        reader.findings.append(
            Finding("inventory.anti_vacuity", "validation-inventory.json", "invalid or duplicate scenario id")
        )
    covered_core: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        profile_id = scenario.get("profile")
        skill_id = scenario.get("skill")
        hosts = scenario.get("hosts")
        if (
            not isinstance(profile_id, str)
            or profile_id not in profiles
            or not isinstance(skill_id, str)
            or skill_id not in skills
        ):
            reader.findings.append(
                Finding("inventory.anti_vacuity", str(scenario.get("id")), "scenario names an unknown profile or skill")
            )
            continue
        try:
            selected = resolve_profile(profile_id, profiles)
        except ValueError:
            continue
        if skill_id not in selected:
            reader.findings.append(
                Finding("inventory.anti_vacuity", scenario["id"], "scenario skill is not selected by profile")
            )
        if (
            not isinstance(hosts, list)
            or not hosts
            or any(host not in skills[skill_id].get("hosts", []) for host in hosts)
        ):
            reader.findings.append(
                Finding("inventory.anti_vacuity", scenario["id"], "scenario host cell is empty or undeclared")
            )
        if profile_id == "core":
            covered_core.add(skill_id)
    try:
        core_members = set(resolve_profile("core", profiles))
    except (KeyError, ValueError):
        core_members = set()
    if covered_core != core_members:
        reader.findings.append(
            Finding("inventory.anti_vacuity", "core", "core scenario coverage drift")
        )


def _check_path_safety(reader: CorpusReader, catalog_skills: list[dict]) -> bool:
    identities: dict[str, str] = {}
    for skill in catalog_skills:
        for value in (skill.get("id"), skill.get("path")):
            if not isinstance(value, str):
                continue
            folded = value.casefold()
            prior = identities.get(folded)
            if prior is not None and prior != value:
                reader.findings.append(Finding("path.case_unique", value, f"case-fold collision with {prior}"))
            identities[folded] = value
        path = skill.get("path")
        if isinstance(path, str) and not safe_relative_path(path):
            reader.findings.append(Finding("path.safe", path, "unsafe catalog path"))
    skills_root = reader.root / "skills"
    if skills_root.is_symlink() and not skills_root.resolve().is_relative_to(reader.root):
        reader.findings.append(
            Finding("path.symlink_contained", "skills", "skills root symlink escapes corpus root")
        )
        return False
    if not skills_root.is_dir():
        return False
    for directory, directory_names, filenames in os.walk(skills_root, followlinks=False):
        for name in sorted((*directory_names, *filenames)):
            path = Path(directory) / name
            relative = path.relative_to(reader.root).as_posix()
            folded = relative.casefold()
            prior = identities.get(folded)
            if prior is not None and prior != relative:
                reader.findings.append(Finding("path.case_unique", relative, f"case-fold collision with {prior}"))
            identities[folded] = relative
            if path.is_symlink() and not path.resolve().is_relative_to(reader.root):
                reader.findings.append(Finding("path.symlink_contained", relative, "symlink escapes corpus root"))
    return True


def _frontmatter(skill_path: Path) -> tuple[dict[str, str] | None, str]:
    try:
        contents = skill_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None, ""
    lines = contents.splitlines()
    if not lines or lines[0] != "---":
        return None, contents
    try:
        closing = lines.index("---", 1)
    except ValueError:
        return None, contents
    values: dict[str, str] = {}
    for line in lines[1:closing]:
        if ":" not in line:
            return None, contents
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values, contents


def _resource_references(contents: str) -> list[str]:
    references: set[str] = set()
    for value in LINK_RE.findall(contents):
        target = value.split("#", 1)[0].strip()
        if target and not re.match(r"^[a-z]+://", target) and not target.startswith("#"):
            references.add(target)
    references.update(RESOURCE_RE.findall(contents))
    return sorted(references)


def _validate_skills(reader: CorpusReader, skills: dict[str, dict]) -> None:
    for skill_id, skill in sorted(skills.items()):
        relative = skill.get("path")
        expected = f"skills/{skill_id}/SKILL.md"
        if relative != expected:
            reader.findings.append(Finding("skill.structure", str(relative), f"skill path must be {expected}"))
        if not safe_relative_path(relative):
            continue
        skill_path = reader.root / relative
        if not skill_path.resolve().is_relative_to(reader.root):
            reader.findings.append(
                Finding("path.symlink_contained", relative, "skill path escapes corpus root")
            )
            continue
        if not skill_path.is_file():
            reader.findings.append(Finding("skill.structure", relative, "missing SKILL.md"))
            continue
        frontmatter, _ = _frontmatter(skill_path)
        if (
            frontmatter is None
            or frontmatter.get("name") != skill_id
            or not frontmatter.get("description")
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill_id)
        ):
            reader.findings.append(Finding("skill.frontmatter", relative, "invalid name/description frontmatter"))
        skill_root = skill_path.parent.resolve()

        def scan(document: Path, depth: int, trail: tuple[Path, ...]) -> None:
            if document in trail:
                reader.findings.append(
                    Finding(
                        "skill.reference",
                        document.relative_to(reader.root).as_posix(),
                        "circular resource reference",
                    )
                )
                return
            try:
                text = document.read_text(encoding="utf-8")
            except (FileNotFoundError, OSError):
                return
            for reference in _resource_references(text):
                if not safe_relative_path(reference):
                    reader.findings.append(Finding("path.safe", relative, f"unsafe skill reference: {reference}"))
                    continue
                target = (document.parent / reference).resolve()
                if not target.is_relative_to(skill_root):
                    reader.findings.append(
                        Finding(
                            "path.safe",
                            relative,
                            f"skill reference escapes skill: {reference}",
                        )
                    )
                elif not target.is_file():
                    reader.findings.append(
                        Finding(
                            "skill.reference",
                            relative,
                            f"missing relative reference: {reference}",
                        )
                    )
                elif depth >= 2:
                    reader.findings.append(
                        Finding(
                            "skill.reference",
                            relative,
                            f"resource reference depth exceeds 2: {reference}",
                        )
                    )
                elif target.suffix.lower() == ".md":
                    scan(target, depth + 1, (*trail, document))

        scan(skill_path.resolve(), 0, ())


def _validate_core_leakage(
    reader: CorpusReader,
    profiles: dict[str, dict],
    skills: dict[str, dict],
    provenance: dict[str, dict],
) -> None:
    if "core" not in profiles:
        return
    try:
        members = resolve_profile("core", profiles)
    except (KeyError, ValueError):
        return
    for member in members:
        skill = skills.get(member)
        provenance_id = skill.get("provenanceId") if skill else None
        entry = provenance.get(provenance_id) if isinstance(provenance_id, str) else None
        if not entry:
            continue
        for output in entry.get("outputs", []):
            relative = output.get("path")
            if not safe_relative_path(relative):
                continue
            parts = PurePosixPath(relative).parts
            if len(parts) >= 2 and parts[-2] == "agents":
                required_host = {"openai.yaml": "codex", "claude-code.yaml": "claude-code"}.get(parts[-1])
                if required_host is None or required_host not in skill.get("hosts", []):
                    reader.findings.append(
                        Finding("core.leakage", relative, "undeclared host extension")
                    )
            try:
                path = reader.root / relative
                if not path.resolve().is_relative_to(reader.root):
                    reader.findings.append(
                        Finding("path.symlink_contained", relative, "core output escapes corpus root")
                    )
                    continue
                contents = path.read_text(encoding="utf-8")
            except (FileNotFoundError, OSError, UnicodeDecodeError):
                continue
            for class_id, pattern in LEAKAGE_PATTERNS:
                if pattern.search(contents):
                    reader.findings.append(Finding("core.leakage", relative, f"forbidden {class_id} literal"))
    for relative in CORE_RUNTIME_PATHS:
        try:
            contents = (reader.root / relative).read_text(encoding="utf-8")
        except (FileNotFoundError, OSError, UnicodeDecodeError):
            reader.findings.append(
                Finding("core.boundary", relative, "missing core runtime module")
            )
            continue
        for class_id, pattern in LEAKAGE_PATTERNS:
            if pattern.search(contents):
                reader.findings.append(
                    Finding("core.boundary", relative, f"forbidden {class_id} literal")
                )


def validate_corpus(root: Path) -> ValidationResult:
    findings: list[Finding] = []
    reader = CorpusReader(root, findings)
    scan_roots = _validate_inventory(reader)
    catalog = reader.load_json("catalog.json")
    provenance_document = reader.load_json("provenance.lock.json")
    profiles = _load_profiles(reader)
    for schema_name in ("catalog", "profile", "provenance", "receipt", "release-manifest"):
        relative = f"schemas/{schema_name}.schema.json"
        schema = reader.load_json(relative)
        if not isinstance(schema, dict) or not isinstance(schema.get("$id"), str):
            findings.append(Finding("schema.supported", relative, "invalid schema identity"))
    if not isinstance(catalog, dict) or not isinstance(provenance_document, dict):
        return _result(findings, profiles, {}, scan_roots)
    for filename, expected in SCHEMA_VERSIONS.items():
        document = catalog if filename == "catalog.json" else provenance_document
        if document.get("schemaVersion") != expected:
            findings.append(Finding("schema.supported", filename, "unsupported schema version"))

    provenance_entries = provenance_document.get("entries")
    if not isinstance(provenance_entries, list):
        findings.append(Finding("provenance.coverage", "provenance.lock.json", "entries must be a list"))
        provenance_entries = []
    provenance: dict[str, dict] = {}
    for entry in provenance_entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or entry["id"] in provenance:
            findings.append(
                Finding(
                    "provenance.coverage",
                    "provenance.lock.json",
                    "duplicate or invalid provenance id",
                )
            )
            continue
        provenance[entry["id"]] = entry
    if [entry.get("id") for entry in provenance_entries if isinstance(entry, dict)] != sorted(provenance):
        findings.append(
            Finding(
                "document.canonical",
                "provenance.lock.json",
                "provenance entries are not sorted by id",
            )
        )

    skill_entries = catalog.get("skills")
    if not isinstance(skill_entries, list):
        findings.append(Finding("catalog.complete", "catalog.json", "skills must be a list"))
        skill_entries = []
    skills: dict[str, dict] = {}
    for skill in skill_entries:
        if not isinstance(skill, dict) or not isinstance(skill.get("id"), str) or skill["id"] in skills:
            findings.append(Finding("catalog.complete", "catalog.json", "duplicate or invalid skill id"))
            continue
        skills[skill["id"]] = skill
    if [skill.get("id") for skill in skill_entries if isinstance(skill, dict)] != sorted(skills):
        findings.append(Finding("document.canonical", "catalog.json", "skills are not sorted by id"))
    skills_root_contained = _check_path_safety(
        reader, [skill for skill in skill_entries if isinstance(skill, dict)]
    )
    discovered = (
        sorted(
            path.relative_to(reader.root).as_posix()
            for path in (reader.root / "skills").glob("*/SKILL.md")
            if path.is_file()
        )
        if skills_root_contained
        else []
    )
    catalog_paths = sorted(skill.get("path") for skill in skills.values() if isinstance(skill.get("path"), str))
    if discovered != catalog_paths:
        findings.append(Finding("catalog.complete", "skills", "catalog does not exactly match shipped SKILL.md files"))

    recorded_outputs: list[str] = []
    for provenance_id, entry in sorted(provenance.items()):
        source = entry.get("source")
        if not isinstance(source, dict):
            findings.append(Finding("schema.supported", provenance_id, "provenance source must be an object"))
            source = {}
        if source.get("revision") != BASELINE_REVISION:
            findings.append(Finding("provenance.coverage", provenance_id, "unapproved source revision"))
        if not entry.get("owner"):
            findings.append(Finding("provenance.license", provenance_id, f"missing ownership: {provenance_id}"))
        if not entry.get("license"):
            findings.append(Finding("provenance.license", provenance_id, f"missing license: {provenance_id}"))
        if not isinstance(entry.get("classification"), str) or not entry["classification"]:
            findings.append(
                Finding(
                    "provenance.coverage",
                    provenance_id,
                    f"missing classification: {provenance_id}",
                )
            )
        if entry.get("distribution") not in {"releasable", "source-only"}:
            findings.append(
                Finding(
                    "provenance.coverage",
                    provenance_id,
                    f"invalid distribution: {provenance_id}",
                )
            )
        outputs = entry.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            findings.append(Finding("provenance.coverage", provenance_id, "missing outputs"))
            continue
        output_paths = [output.get("path") for output in outputs if isinstance(output, dict)]
        if len(output_paths) != len(outputs) or output_paths != sorted(output_paths):
            findings.append(
                Finding(
                    "document.canonical",
                    provenance_id,
                    f"provenance outputs are not sorted by path: {provenance_id}",
                )
            )
        for output in outputs:
            if not isinstance(output, dict):
                continue
            relative = output.get("path")
            if not safe_relative_path(relative):
                findings.append(Finding("path.safe", provenance_id, f"unsafe provenance path: {relative}"))
                continue
            recorded_outputs.append(relative)
            path = reader.root / relative
            if not path.resolve().is_relative_to(reader.root):
                findings.append(
                    Finding("path.symlink_contained", relative, "provenance output escapes corpus root")
                )
                continue
            if not path.is_file():
                findings.append(Finding("provenance.coverage", relative, "missing provenance output"))
            elif output.get("sha256") != sha256_bytes(path.read_bytes()):
                findings.append(Finding("provenance.digest", relative, "provenance digest drift"))
    shipped_files = (
        sorted(
            path.relative_to(reader.root).as_posix()
            for path in (reader.root / "skills").rglob("*")
            if path.is_file()
        )
        if skills_root_contained
        else []
    )
    if sorted(recorded_outputs) != shipped_files:
        findings.append(Finding("provenance.coverage", "skills", "provenance does not exactly cover shipped files"))

    for skill_id, skill in sorted(skills.items()):
        required_fields = ("dependencies", "externalTools", "hosts", "path", "profiles", "provenanceId")
        if any(field not in skill for field in required_fields):
            findings.append(Finding("schema.supported", skill_id, "catalog skill is missing required fields"))
        if "classification" in skill:
            findings.append(
                Finding(
                    "provenance.coverage",
                    skill_id,
                    f"catalog duplicates provenance classification: {skill_id}",
                )
            )
        provenance_id = skill.get("provenanceId")
        if not isinstance(provenance_id, str) or provenance_id not in provenance:
            findings.append(
                Finding(
                    "provenance.coverage",
                    skill_id,
                    f"missing provenance record for skill: {skill_id}",
                )
            )
        declared_profiles = skill.get("profiles")
        if not isinstance(declared_profiles, list):
            findings.append(Finding("schema.supported", skill_id, "catalog profiles must be a list"))
            declared_profiles = []
        for profile_id in declared_profiles:
            if not isinstance(profile_id, str):
                findings.append(Finding("schema.supported", skill_id, "catalog profile ids must be strings"))
                continue
            if profile_id not in profiles:
                findings.append(
                    Finding(
                        "profile.reference",
                        skill_id,
                        f"unknown profile member: {skill_id} -> {profile_id}",
                    )
                )
    for profile_id, profile in sorted(profiles.items()):
        if profile.get("schemaVersion") != "agent-skills-profile/1":
            findings.append(Finding("schema.supported", f"profiles/{profile_id}.json", "unsupported schema version"))
        includes = profile.get("includes")
        members = profile.get("members")
        if not isinstance(includes, list) or not isinstance(members, list):
            findings.append(Finding("profile.reference", profile_id, "members/includes must be lists"))
            continue
        for included in includes:
            if not isinstance(included, str):
                findings.append(Finding("schema.supported", profile_id, "included profile ids must be strings"))
                continue
            if included not in profiles:
                findings.append(Finding("profile.reference", profile_id, f"unknown included profile: {included}"))
        for member in members:
            if not isinstance(member, str):
                findings.append(Finding("schema.supported", profile_id, "profile member ids must be strings"))
                continue
            if member not in skills:
                findings.append(
                    Finding(
                        "profile.reference",
                        profile_id,
                        f"unknown profile member: {profile_id} -> {member}",
                    )
                )
            elif (
                not isinstance(skills[member].get("provenanceId"), str)
                or provenance.get(skills[member]["provenanceId"], {}).get("distribution")
                != "releasable"
            ):
                findings.append(
                    Finding(
                        "profile.closure",
                        profile_id,
                        f"non-releasable profile member: {profile_id} -> {member}",
                    )
                )
        try:
            resolved = resolve_profile(profile_id, profiles)
        except ValueError as error:
            findings.append(Finding("profile.closure", profile_id, str(error)))
            continue
        files = sorted(
            output["path"]
            for member in resolved
            if member in skills and isinstance(skills[member].get("provenanceId"), str)
            for output in provenance.get(skills[member]["provenanceId"], {}).get("outputs", [])
            if isinstance(output, dict) and "path" in output
        )
        if profile.get("expectedFiles") is not None and profile["expectedFiles"] != files:
            findings.append(Finding("profile.closure", profile_id, "expectedFiles drift"))
    for skill_id, skill in sorted(skills.items()):
        selected_by: list[str] = []
        for profile_id in sorted(profiles):
            try:
                if skill_id in resolve_profile(profile_id, profiles):
                    selected_by.append(profile_id)
            except ValueError:
                pass
        if skill.get("profiles") != selected_by:
            findings.append(Finding("profile.closure", skill_id, "catalog/profile membership drift"))
        provenance_id = skill.get("provenanceId")
        distribution = provenance.get(provenance_id, {}).get("distribution") if isinstance(provenance_id, str) else None
        if distribution == "releasable" and not selected_by:
            findings.append(Finding("profile.closure", skill_id, "releasable skill lacks profile"))
        if distribution == "source-only" and selected_by:
            findings.append(Finding("profile.closure", skill_id, "source-only skill is selected"))
    _validate_scenarios(reader, profiles, skills)
    findings.extend(dependency_findings(skills, profiles))
    _validate_skills(reader, skills)
    _validate_core_leakage(reader, profiles, skills, provenance)
    return _result(findings, profiles, skills, scan_roots)


def _result(
    findings: list[Finding],
    profiles: dict[str, dict],
    skills: dict[str, dict],
    scan_roots: tuple[str, ...],
) -> ValidationResult:
    return ValidationResult(
        tuple(sorted(set(findings))),
        tuple(sorted(profiles)),
        RULE_IDS,
        tuple(sorted(scan_roots)),
        tuple(sorted(skills)),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--profile")
    args = parser.parse_args()
    result = validate_corpus(args.root)
    if result.findings:
        print(result.to_json(), end="")
        return 1
    if args.profile:
        root = args.root.resolve()
        profiles = {
            document["id"]: document
            for path in sorted((root / "profiles").glob("*.json"))
            if isinstance(
                (document := json.loads(path.read_text(encoding="utf-8"))), dict
            )
        }
        if args.profile not in profiles:
            print(canonical_json({"findings": [f"unknown profile: {args.profile}"], "ok": False}), end="")
            return 1
        catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
        provenance = json.loads((root / "provenance.lock.json").read_text(encoding="utf-8"))
        skills = {skill["id"]: skill for skill in catalog["skills"]}
        entries = {entry["id"]: entry for entry in provenance["entries"]}
        members = resolve_profile(args.profile, profiles)
        files = sorted(
            output["path"]
            for member in members
            for output in entries[skills[member]["provenanceId"]]["outputs"]
        )
        print(canonical_json({"files": files, "members": members, "profile": args.profile}), end="")
    else:
        print(result.to_json(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
