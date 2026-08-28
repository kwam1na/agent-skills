from __future__ import annotations

from .errors import Finding


def resolve_profile(
    profile_id: str,
    profiles: dict[str, dict],
    seen: tuple[str, ...] = (),
) -> list[str]:
    if profile_id in seen:
        raise ValueError(f"profile include cycle: {' -> '.join((*seen, profile_id))}")
    profile = profiles[profile_id]
    includes = profile.get("includes")
    declared_members = profile.get("members")
    if not isinstance(includes, list) or not isinstance(declared_members, list):
        raise ValueError(f"invalid profile structure: {profile_id}")
    members: list[str] = []
    for included in includes:
        if isinstance(included, str) and included in profiles:
            members.extend(resolve_profile(included, profiles, (*seen, profile_id)))
    members.extend(member for member in declared_members if isinstance(member, str))
    return sorted(set(members))


def dependency_findings(
    skills: dict[str, dict], profiles: dict[str, dict]
) -> list[Finding]:
    findings: list[Finding] = []
    graph: dict[str, list[str]] = {}
    for skill_id, skill in skills.items():
        dependencies = skill.get("dependencies", [])
        if not isinstance(dependencies, list):
            findings.append(Finding("dependency.reference", skill_id, "dependencies must be a list"))
            graph[skill_id] = []
            continue
        graph[skill_id] = []
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                findings.append(Finding("dependency.reference", skill_id, "invalid dependency record"))
                continue
            dependency_id = dependency.get("id")
            requirement = dependency.get("requirement")
            if not isinstance(dependency_id, str) or dependency_id not in skills:
                findings.append(Finding("dependency.reference", skill_id, f"unresolved dependency: {dependency_id}"))
            else:
                graph[skill_id].append(dependency_id)
            if requirement not in {"required", "routing"}:
                findings.append(
                    Finding(
                        "dependency.reference",
                        skill_id,
                        f"unsupported dependency requirement: {requirement}",
                    )
                )

    reported_cycles: set[tuple[str, ...]] = set()

    def visit(node: str, path: tuple[str, ...]) -> None:
        if node in path:
            cycle = (*path[path.index(node) :], node)
            normalized = tuple(sorted(set(cycle)))
            if normalized not in reported_cycles:
                reported_cycles.add(normalized)
                findings.append(Finding("dependency.cycle", node, f"dependency cycle: {' -> '.join(cycle)}"))
            return
        for dependency in graph.get(node, []):
            visit(dependency, (*path, node))

    for skill_id in sorted(graph):
        visit(skill_id, ())

    for profile_id in sorted(profiles):
        try:
            selected = set(resolve_profile(profile_id, profiles))
        except (KeyError, ValueError):
            continue
        for skill_id in sorted(selected & skills.keys()):
            dependencies = skills[skill_id].get("dependencies")
            if not isinstance(dependencies, list):
                dependencies = []
            for dependency in dependencies:
                if not isinstance(dependency, dict) or dependency.get("requirement") != "required":
                    continue
                dependency_id = dependency.get("id")
                if not isinstance(dependency_id, str) or dependency_id not in selected:
                    findings.append(
                        Finding(
                            "dependency.required_closure",
                            f"profiles/{profile_id}.json",
                            f"required dependency not selected: {skill_id} -> {dependency_id}",
                        )
                    )
    return findings
