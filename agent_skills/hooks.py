"""Skill hooks: the manifest a skill declares, and the dispatcher a host runs.

A skill declares its hooks in ``hooks.json`` beside its ``SKILL.md``. Each hook
names an event, the hosts it serves and the script that decides. The script
reads the host's payload on stdin and prints one neutral verdict: ``allow``,
``warn`` with a reason, or ``block`` with a reason, and on ``session-start``
optionally a ``context`` string. Only this module knows a host's own input and
output shapes, and it serves one host, Claude Code.

The dispatcher fails open: anything a hook does other than print a valid
verdict and exit 0 counts as ``allow``, with one stderr line naming the hook,
and the dispatcher itself always exits 0.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Iterable, Mapping

from .catalog import resolve_profile
from .errors import Finding


SCHEMA_VERSION = "skill-hooks/1"
MANIFEST_NAME = "hooks.json"
EVENTS = ("session-start", "stop")
HOSTS = ("claude-code", "codex")
# The hosts this dispatcher writes output for. A hook may declare another
# catalog host; it is valid and never run here.
DISPATCHED_HOSTS = ("claude-code",)
HOOK_KEYS = frozenset({"argv", "event", "hosts", "id"})
WORDS = r"[a-z0-9]+(?:-[a-z0-9]+)*"
HOOK_ID = re.compile(rf"({WORDS})\.({WORDS})")
VERDICTS = ("allow", "warn", "block")

# Seconds. The installed entry gives the whole command 30; the dispatcher keeps
# within 25, gives each hook at most 20 of what is left, and starts no hook with
# less than 2 left.
OVERALL_BUDGET = 25.0
HOOK_BUDGET = 20.0
MINIMUM_START = 2.0
# Characters, under the host's own limit of 10,000 for added context.
CONTEXT_LIMIT = 9000
# Seconds to wait for a killed hook's output to close before abandoning it.
REAP_WAIT = 0.5
# Seconds each hook's budget also leaves for stopping, reaping and combining,
# so the dispatcher ends within OVERALL_BUDGET. REAP_WAIT and this together
# stay below MINIMUM_START, so every hook that starts has a positive budget;
# tests/test_hooks.py pins that.
EXIT_MARGIN = 0.25
PROJECT_VARIABLE = "CLAUDE_PROJECT_DIR"


# The manifest -----------------------------------------------------------------


def script_problem(script: object, skill_folder: str, outputs: Iterable[str] | None) -> str | None:
    """Why ``script`` can't be a hook's script, or ``None`` when it can.

    The script is a normalized relative path inside the skill's folder. When
    ``outputs`` is given, it must also be one of them.
    """
    if not isinstance(script, str) or not script or "\\" in script:
        return "the script must be a relative path inside the skill"
    path = PurePosixPath(script)
    if path.is_absolute() or path.as_posix() != script or any(part in {".", ".."} for part in path.parts):
        return f"the script {script!r} lies outside the skill"
    if outputs is not None and f"{skill_folder}/{script}" not in set(outputs):
        return f"the script {script!r} is not among the skill's outputs"
    return None


def hook_problems(entry: object, *, skill_id: str, skill_folder: str, skill_hosts: Iterable[str],
                  outputs: Iterable[str] | None) -> list[tuple[str, str]]:
    """Each ``(finding code, message)`` that keeps one manifest entry from being a hook."""
    if not isinstance(entry, dict) or set(entry) != HOOK_KEYS:
        return [("hooks.schema", f"a hook must be an object with exactly {', '.join(sorted(HOOK_KEYS))}")]
    problems: list[tuple[str, str]] = []
    hook_id = entry["id"]
    matched = HOOK_ID.fullmatch(hook_id) if isinstance(hook_id, str) else None
    if matched is None:
        problems.append(("hooks.schema", f"invalid hook id {hook_id!r}: use <skill-id>.<name> in lowercase words"))
    elif matched.group(1) != skill_id:
        problems.append(("hooks.schema", f"hook id {hook_id!r} must start with the skill id {skill_id!r}"))
    event = entry["event"]
    if not isinstance(event, str):
        problems.append(("hooks.schema", "event must be a string"))
    elif event not in EVENTS:
        problems.append(("hooks.event", f"unknown event {event!r}: use one of {', '.join(EVENTS)}"))
    hosts = entry["hosts"]
    if (not isinstance(hosts, list) or not hosts or not all(isinstance(host, str) for host in hosts)
            or len(set(hosts)) != len(hosts)):
        problems.append(("hooks.schema", "hosts must be a non-empty list of distinct strings"))
    else:
        allowed = {host for host in skill_hosts if isinstance(host, str)}
        for host in hosts:
            if host not in HOSTS or host not in allowed:
                problems.append(("hooks.host", f"host {host!r} is not among the skill's hosts"))
    argv = entry["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        problems.append(("hooks.schema", "argv must be a non-empty list of non-empty strings"))
    else:
        problem = script_problem(argv[0], skill_folder, outputs)
        if problem is not None:
            problems.append(("hooks.argv", problem))
    return problems


def manifest_findings(document: object, *, skill_id: str, skill_folder: str, skill_hosts: Iterable[str],
                      outputs: Iterable[str]) -> list[Finding]:
    """Every finding for one skill's manifest, ``skill_folder`` being its folder in the corpus."""
    artifact = f"{skill_folder}/{MANIFEST_NAME}"
    if (not isinstance(document, dict) or set(document) != {"hooks", "schemaVersion"}
            or document["schemaVersion"] != SCHEMA_VERSION or not isinstance(document["hooks"], list)):
        return [Finding("hooks.schema", artifact,
                        f"a manifest must be {{\"schemaVersion\": \"{SCHEMA_VERSION}\", \"hooks\": [...]}}")]
    outputs = tuple(outputs)
    findings = []
    for index, entry in enumerate(document["hooks"]):
        name = entry.get("id") if isinstance(entry, dict) and isinstance(entry.get("id"), str) else f"hooks[{index}]"
        for code, message in hook_problems(entry, skill_id=skill_id, skill_folder=skill_folder,
                                           skill_hosts=skill_hosts, outputs=outputs):
            findings.append(Finding(code, artifact, f"{name}: {message}"))
    return findings


def manifest_hook_ids(document: object) -> list[str]:
    """The string ids a manifest declares, in manifest order, for the profile-wide duplicate check."""
    if not isinstance(document, dict) or not isinstance(document.get("hooks"), list):
        return []
    return [entry["id"] for entry in document["hooks"]
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)]


# The active generation's hooks --------------------------------------------------


@dataclass(frozen=True)
class Hook:
    id: str
    command: tuple[str, ...]
    problem: str | None = None


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def declared_hooks(generation: Path, host: str, event: str) -> tuple[list[Hook], list[str]]:
    """The hooks ``generation``'s profile declares for ``host`` and ``event``, in run order.

    Run order is the profile's members as ``resolve_profile`` returns them,
    sorted by id, then each manifest's own order. The second item is a line for
    each manifest that can't be read; its hooks are left out.
    """
    try:
        release = _read_json(generation / "release-manifest.json")
        profiles: dict[str, dict] = {}
        for path in sorted((generation / "profiles").glob("*.json")):
            document = _read_json(path)
            if isinstance(document, dict) and isinstance(document.get("id"), str):
                profiles[document["id"]] = document
        members = resolve_profile(release["profile"], profiles)
        catalog = {skill["id"]: skill for skill in _read_json(generation / "catalog.json")["skills"]}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [], [_line("generation", f"cannot read the active generation: {error}")]
    found: list[Hook] = []
    errors: list[str] = []
    for member in members:
        try:
            _member_hooks(generation, member, catalog.get(member), host, event, found, errors)
        except Exception as error:  # noqa: BLE001 - one malformed skill never costs the others their hooks
            errors.append(_line(member, f"its hooks cannot be read: {type(error).__name__}: {error}"))
    return found, errors


def declared_events(generation: Path, host: str) -> list[str]:
    """The events, sorted, for which ``generation``'s profile declares at least one hook for ``host``."""
    return [event for event in EVENTS if declared_hooks(generation, host, event)[0]]


def _member_hooks(generation: Path, member: str, skill: object, host: str, event: str, found: list[Hook],
                  errors: list[str]) -> None:
    """Add one profile member's hooks for ``host`` and ``event`` to ``found``, in manifest order."""
    path = skill.get("path") if isinstance(skill, dict) else None
    folder = PurePosixPath(path).parent if isinstance(path, str) else None
    if folder is None or script_problem(folder.as_posix(), "", None) is not None:
        errors.append(_line(member, "the catalog gives no folder inside the generation"))
        return
    skill_folder = generation / folder
    manifest = skill_folder / MANIFEST_NAME
    if not manifest.is_file():
        return
    try:
        document = _read_json(manifest)
    except (OSError, ValueError, RecursionError) as error:
        errors.append(_line(f"{member}/{MANIFEST_NAME}", f"cannot be read: {type(error).__name__}"))
        return
    if (not isinstance(document, dict) or document.get("schemaVersion") != SCHEMA_VERSION
            or not isinstance(document.get("hooks"), list)):
        errors.append(_line(f"{member}/{MANIFEST_NAME}", "is not a skill-hooks/1 manifest"))
        return
    hosts = skill.get("hosts") if isinstance(skill.get("hosts"), list) else []
    for index, entry in enumerate(document["hooks"]):
        if not isinstance(entry, dict):
            errors.append(_line(f"{member}/{MANIFEST_NAME}", f"hooks[{index}] is not an object"))
            continue
        if entry.get("event") != event or not isinstance(entry.get("hosts"), list) or host not in entry["hosts"]:
            continue
        hook_id = entry["id"] if isinstance(entry.get("id"), str) else f"{member}/hooks[{index}]"
        problems = hook_problems(entry, skill_id=member, skill_folder=folder.as_posix(), skill_hosts=hosts,
                                 outputs=None)
        if problems:
            found.append(Hook(hook_id, (), "; ".join(message for _, message in problems)))
            continue
        # The lexical check above can't see a symlink: the resolved script must
        # still lie inside the resolved skill folder.
        script = skill_folder / entry["argv"][0]
        try:
            contained = script.resolve(strict=True).is_relative_to(skill_folder.resolve(strict=True))
        except (OSError, ValueError):  # ValueError: a path holding a NUL byte
            contained = False
        if not contained or not script.is_file():
            found.append(Hook(hook_id, (), f"the script {entry['argv'][0]!r} is missing or outside the skill"))
            continue
        found.append(Hook(hook_id, (sys.executable, "-B", str(script), *entry["argv"][1:])))


# Dispatch -----------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reason: str | None = None
    context: str | None = None


class HookFault(Exception):
    """A hook did something other than print one valid verdict and exit 0."""


def _line(name: str, message: str) -> str:
    return f"agent-skills hook: {' '.join(name.split())}: {' '.join(message.split())}"


def parse_verdict(stdout: bytes, event: str) -> Verdict:
    try:
        value = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise HookFault(f"printed something other than one JSON object ({type(error).__name__})") from None
    if not isinstance(value, dict):
        raise HookFault(f"printed a JSON {type(value).__name__}, not an object")
    verdict = value.get("verdict")
    if not isinstance(verdict, str) or verdict not in VERDICTS:
        raise HookFault(f"printed no known verdict ({verdict!r}): use allow, warn or block")
    reason = value.get("reason")
    if verdict != "allow" and (not isinstance(reason, str) or not reason):
        raise HookFault(f"printed {verdict} without a non-empty string reason")
    context = None
    if "context" in value:
        if event != "session-start":
            raise HookFault("printed a context, which only session-start takes")
        if not isinstance(value["context"], str):
            raise HookFault("printed a context that is not a string")
        context = value["context"]
    return Verdict(verdict, reason if verdict != "allow" else None, context)


def _stop(process: subprocess.Popen) -> None:
    """Stop a hook and everything it started in its own process group."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - Windows is not validated
            process.kill()
    except (ProcessLookupError, PermissionError):
        pass


def run_hook(hook: Hook, event: str, payload: bytes, root: Path, environment: Mapping[str, str],
             budget: float) -> Verdict:
    """Run one hook within ``budget`` seconds and return its verdict, or raise ``HookFault``."""
    child_environment = dict(environment)
    child_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        process = subprocess.Popen(
            hook.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=root,
            env=child_environment, start_new_session=os.name == "posix",
        )
    except OSError as error:
        raise HookFault(f"could not start ({error})") from error
    try:
        stdout, stderr = process.communicate(payload, timeout=budget)
    except subprocess.TimeoutExpired:
        _stop(process)
        try:
            process.communicate(timeout=REAP_WAIT)
        except subprocess.TimeoutExpired:
            # Something outside the group still holds the output open.
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        process.wait()
        raise HookFault(f"timed out after {budget:.1f}s") from None
    finally:
        if process.poll() is None:  # pragma: no cover - only on an unexpected error above
            _stop(process)
            process.wait()
    if process.returncode != 0:
        last = next((line for line in reversed(stderr.decode("utf-8", "replace").splitlines()) if line.strip()), "")
        detail = f": {last.strip()[:200]}" if last else ""
        raise HookFault(f"exited with status {process.returncode}{detail}")
    return parse_verdict(stdout, event)


def project_root(environment: Mapping[str, str], payload: Mapping[str, object]) -> Path | None:
    """The project the session belongs to: the host's variable, else the payload cwd's Git top level."""
    configured = environment.get(PROJECT_VARIABLE)
    if configured:
        path = Path(configured)
        return path.resolve() if path.is_dir() else None
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        return None
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, env=dict(environment),
                                capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    top = result.stdout.strip()
    return Path(top).resolve() if result.returncode == 0 and top else None


def combine(verdicts: list[Verdict], *, stop_hook_active: bool) -> dict[str, object]:
    """Claude Code's output for the verdicts, in run order; empty when every hook allows without context."""
    blocks: list[str] = []
    warns: list[str] = []
    contexts: list[str] = []
    for verdict in verdicts:
        kind = "warn" if verdict.verdict == "block" and stop_hook_active else verdict.verdict
        if kind == "block":
            blocks.append(verdict.reason or "")
        elif kind == "warn":
            warns.append(verdict.reason or "")
        if verdict.context:
            contexts.append(verdict.context)
    output: dict[str, object] = {}
    if blocks:
        output["decision"] = "block"
        output["reason"] = "\n".join(blocks)
    if warns:
        output["systemMessage"] = "\n".join(warns)
    if contexts:
        output["hookSpecificOutput"] = {
            "additionalContext": "\n".join(contexts)[:CONTEXT_LIMIT],
            "hookEventName": "SessionStart",
        }
    return output


def dispatch(host: str, event: str, payload: bytes, environment: Mapping[str, str]) -> tuple[str, list[str]]:
    """What the dispatcher prints on stdout, and its stderr lines, for one host event."""
    started = time.monotonic()
    if host not in DISPATCHED_HOSTS or event not in EVENTS:
        return "", [_line(f"{host} {event}", "this dispatcher serves no such host event")]
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        parsed = None
    document = parsed if isinstance(parsed, dict) else {}
    root = project_root(environment, document)
    if root is None:
        return "", [_line("project", f"no project root: {PROJECT_VARIABLE} is unset and the payload's cwd "
                                     "is not inside a Git repository")]
    current = root / ".agent-skills" / "current"
    if not current.exists():
        return "", []
    generation = current.resolve()
    declared, errors = declared_hooks(generation, host, event)
    verdicts: list[Verdict] = []
    for hook in declared:
        if hook.problem is not None:
            errors.append(_line(hook.id, hook.problem))
            continue
        remaining = OVERALL_BUDGET - (time.monotonic() - started)
        # A killed hook may take REAP_WAIT more to give up its output, and
        # stopping it takes a little more, so its own budget leaves that room
        # inside the overall one.
        budget = min(HOOK_BUDGET, remaining - REAP_WAIT - EXIT_MARGIN)
        if remaining < MINIMUM_START:
            errors.append(_line(hook.id, f"skipped: {max(remaining, 0):.1f}s of the budget left"))
            continue
        try:
            verdicts.append(run_hook(hook, event, payload, root, environment, budget))
        except HookFault as fault:
            errors.append(_line(hook.id, str(fault)))
        except Exception as error:  # noqa: BLE001 - one hook's failure never costs the others their verdicts
            errors.append(_line(hook.id, f"failed in the dispatcher: {type(error).__name__}: {error}"))
    output = combine(verdicts, stop_hook_active=event == "stop" and document.get("stop_hook_active") is True)
    return (json.dumps(output, ensure_ascii=False) + "\n" if output else ""), errors


def run_command(host: str, event: str, *, stdin: IO[bytes] | None = None, stdout: IO[str] | None = None,
                stderr: IO[str] | None = None, environment: Mapping[str, str] | None = None) -> int:
    """``agent-skills hook <host> <event>``: always exits 0."""
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        payload = (stdin or sys.stdin.buffer).read()
        output, errors = dispatch(host, event, payload, os.environ if environment is None else environment)
    except Exception as error:  # noqa: BLE001 - a hook command never fails the session
        output, errors = "", [_line("dispatcher", f"failed: {error}")]
    for stream, text in ((stderr, "".join(f"{line}\n" for line in errors)), (stdout, output)):
        try:
            stream.write(text)
            stream.flush()
        except (OSError, ValueError):
            pass
    return 0
