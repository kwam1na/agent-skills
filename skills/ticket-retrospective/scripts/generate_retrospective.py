#!/usr/bin/env python3
"""Generate post-delivery ticket retrospective reports."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import traceback
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SECRET_PATTERNS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|token|secret|password|passwd|access[_-]?token)\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S+"),
]

PATH_PATTERNS = [
    re.compile(r"/Users/[^/\s]+/"),
    re.compile(r"/home/[^/\s]+/"),
    re.compile(r"C:\\Users\\[^\\\s]+\\"),
]

ERROR_PATTERN = re.compile(
    r"(?i)\b(error|failed|failure|traceback|exception|exit(ed)? with code [1-9])\b"
)
EXIT_CODE_PATTERN = re.compile(r"Process exited with code\s+(-?\d+)")
PASS_CLAIM_PATTERN = re.compile(
    r"(?i)\b(all green|passes|passed|successful|ready for review|checks are green)\b"
)
TEST_COMMAND_PATTERN = re.compile(
    r"(?i)\b(pytest|vitest|jest|bun run test|npm test|pnpm test|cargo test|go test)\b"
)
VALIDATION_COMMAND_PATTERN = re.compile(
    r"(?i)\b(test|typecheck|lint|build|check|verify|validate|tsc|pytest|vitest|jest|pr:[a-z0-9_-]+)\b"
)
BRANCH_PATTERN = re.compile(r"\bcodex/[A-Za-z0-9._-]+\b")
THREAD_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{27}$", re.IGNORECASE)


@dataclass
class ParsedCall:
    timestamp: datetime
    name: str
    call_id: str | None
    arguments: dict[str, Any]
    raw_arguments: str
    output: str | None = None
    output_ts: datetime | None = None


def parse_ts(value: str | int | float | None) -> datetime:
    if value is None:
        raise ValueError("timestamp is required")

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)

    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"

    if raw.isdigit():
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)

    return datetime.fromisoformat(raw).astimezone(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def redact_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("<redacted-secret>", redacted)
    for pattern in PATH_PATTERNS:
        redacted = pattern.sub(lambda m: _redact_user_path(m.group(0)), redacted)
    return redacted


def _redact_user_path(path_prefix: str) -> str:
    if path_prefix.startswith("/Users/"):
        return "/Users/<redacted>/"
    if path_prefix.startswith("/home/"):
        return "/home/<redacted>/"
    if path_prefix.startswith("C:\\Users\\"):
        return "C:\\Users\\<redacted>\\"
    return path_prefix


def normalize_command(cmd: str) -> str:
    return re.sub(r"\s+", " ", cmd.strip())


def is_validation_command(cmd: str) -> bool:
    return bool(VALIDATION_COMMAND_PATTERN.search(normalize_command(cmd)))


def extract_message_text(event: dict[str, Any]) -> str | None:
    payload = event.get("payload") or {}
    if event.get("type") != "response_item":
        return None
    if payload.get("type") != "message":
        return None

    parts = payload.get("content") or []
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())
    return "\n".join(chunks).strip() or None


def is_tool_failure(output: str) -> bool:
    text = output or ""
    if not text.strip():
        return False

    exit_match = EXIT_CODE_PATTERN.search(text)
    if exit_match:
        try:
            return int(exit_match.group(1)) != 0
        except ValueError:
            pass

    lowered = text.lower()
    if "aborted by user" in lowered:
        return True
    if re.search(r"(?im)^\s*(error|traceback|exception)\b", text):
        return True
    return False


def find_session_files(codex_home: Path, thread_id: str) -> list[Path]:
    candidates: list[Path] = []
    for folder_name in ("sessions", "archived_sessions"):
        root = codex_home / folder_name
        if not root.exists():
            continue
        candidates.extend(root.rglob(f"*{thread_id}*.jsonl"))

    unique = sorted({path.resolve() for path in candidates})
    return [Path(path) for path in unique if path.is_file()]


def load_session_events(
    codex_home: Path,
    thread_id: str,
    start_ts: datetime,
    handoff_ts: datetime,
) -> tuple[list[dict[str, Any]], list[Path]]:
    files = find_session_files(codex_home, thread_id)
    events: list[dict[str, Any]] = []

    for file_path in files:
        try:
            with file_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    timestamp = event.get("timestamp")
                    if not timestamp:
                        continue
                    try:
                        event_ts = parse_ts(timestamp)
                    except ValueError:
                        continue

                    if event_ts < start_ts or event_ts > handoff_ts:
                        continue

                    event["_parsed_ts"] = event_ts
                    events.append(event)
        except OSError:
            continue

    events.sort(key=lambda item: item.get("_parsed_ts", datetime.min.replace(tzinfo=timezone.utc)))
    return events, files


def load_sqlite_logs(
    codex_home: Path,
    thread_id: str,
    start_ts: datetime,
    handoff_ts: datetime,
) -> list[dict[str, Any]]:
    db_path = codex_home / "logs_2.sqlite"
    if not db_path.exists():
        return []

    start_epoch = int(start_ts.timestamp())
    end_epoch = int(handoff_ts.timestamp())

    rows: list[dict[str, Any]] = []
    query = """
        SELECT ts, level, target, thread_id, feedback_log_body
        FROM logs
        WHERE thread_id = ?
          AND ts >= ?
          AND ts <= ?
        ORDER BY ts ASC, ts_nanos ASC, id ASC
    """
    try:
        with sqlite3.connect(str(db_path)) as conn:
            cursor = conn.execute(query, (thread_id, start_epoch, end_epoch))
            for ts, level, target, row_thread_id, body in cursor.fetchall():
                rows.append(
                    {
                        "ts": datetime.fromtimestamp(ts, tz=timezone.utc),
                        "level": str(level),
                        "target": str(target),
                        "thread_id": str(row_thread_id),
                        "body": str(body or ""),
                    }
                )
    except sqlite3.Error:
        return []

    return rows


def extract_calls(events: list[dict[str, Any]]) -> list[ParsedCall]:
    calls_by_id: dict[str, ParsedCall] = {}
    ordered_calls: list[ParsedCall] = []

    for event in events:
        payload = event.get("payload") or {}
        event_type = event.get("type")
        ts = event.get("_parsed_ts")
        if not isinstance(ts, datetime):
            continue

        if event_type == "response_item" and payload.get("type") == "function_call":
            raw_args = payload.get("arguments") or "{}"
            parsed_args: dict[str, Any] = {}
            if isinstance(raw_args, str):
                try:
                    maybe_obj = json.loads(raw_args)
                    if isinstance(maybe_obj, dict):
                        parsed_args = maybe_obj
                except json.JSONDecodeError:
                    parsed_args = {}

            call = ParsedCall(
                timestamp=ts,
                name=str(payload.get("name") or ""),
                call_id=str(payload.get("call_id")) if payload.get("call_id") else None,
                arguments=parsed_args,
                raw_arguments=str(raw_args),
            )
            ordered_calls.append(call)
            if call.call_id:
                calls_by_id[call.call_id] = call

        if event_type == "response_item" and payload.get("type") == "function_call_output":
            call_id = payload.get("call_id")
            if not call_id or call_id not in calls_by_id:
                continue
            call = calls_by_id[call_id]
            output = payload.get("output")
            call.output = str(output) if output is not None else ""
            call.output_ts = ts

    return ordered_calls


def command_category(command: str) -> str:
    cmd = command.strip().lower()
    if not cmd:
        return "other"
    if VALIDATION_COMMAND_PATTERN.search(cmd):
        return "validation"
    if cmd.startswith("git "):
        return "git"
    if cmd.startswith(("rg ", "sed ", "cat ", "ls ", "find ", "sqlite3 ", "head ", "tail ")):
        return "exploration"
    if cmd.startswith(("apply_patch", "mv ", "cp ", "touch ")):
        return "edit"
    return "other"


def compute_metrics(
    calls: list[ParsedCall],
    events: list[dict[str, Any]],
    start_ts: datetime,
    handoff_ts: datetime,
) -> dict[str, Any]:
    commands: list[tuple[datetime, str]] = []
    for call in calls:
        if call.name == "exec_command":
            cmd = str(call.arguments.get("cmd") or "").strip()
            if cmd:
                commands.append((call.timestamp, normalize_command(cmd)))

    command_counter = Counter(command for _, command in commands)
    retry_count = sum(max(0, count - 1) for count in command_counter.values())
    test_commands = [command for _, command in commands if TEST_COMMAND_PATTERN.search(command)]
    test_rerun_count = max(0, len(test_commands) - len(set(test_commands)))

    tool_error_count = 0
    for call in calls:
        output = call.output or ""
        if is_tool_failure(output):
            tool_error_count += 1

    categories = [command_category(command) for _, command in commands]
    pivot_count = 0
    for prev, curr in zip(categories, categories[1:]):
        if prev != curr:
            pivot_count += 1

    first_tool_ts = calls[0].timestamp if calls else start_ts
    validation_ts = next(
        (
            ts
            for ts, command in commands
            if VALIDATION_COMMAND_PATTERN.search(command)
        ),
        None,
    )

    aborted_turn_count = sum(
        1
        for event in events
        if event.get("type") == "event_msg"
        and isinstance(event.get("payload"), dict)
        and event.get("payload", {}).get("type") == "turn_aborted"
    )

    setup_seconds = max(0, int((first_tool_ts - start_ts).total_seconds()))
    if validation_ts:
        implementation_seconds = max(0, int((validation_ts - first_tool_ts).total_seconds()))
        validation_seconds = max(0, int((handoff_ts - validation_ts).total_seconds()))
    else:
        implementation_seconds = max(0, int((handoff_ts - first_tool_ts).total_seconds()))
        validation_seconds = 0

    return {
        "retry_count": retry_count,
        "tool_error_count": tool_error_count,
        "test_rerun_count": test_rerun_count,
        "pivot_count": pivot_count,
        "aborted_turn_count": aborted_turn_count,
        "event_count": len(events),
        "tool_call_count": len(calls),
        "command_count": len(commands),
        "elapsed_total_seconds": max(0, int((handoff_ts - start_ts).total_seconds())),
        "elapsed_setup_seconds": setup_seconds,
        "elapsed_implementation_seconds": implementation_seconds,
        "elapsed_validation_seconds": validation_seconds,
    }


def detect_inconsistencies(
    calls: list[ParsedCall],
    events: list[dict[str, Any]],
    delivery_gate: dict[str, bool],
    branch: str,
) -> list[str]:
    inconsistencies: list[str] = []
    messages: list[tuple[datetime, str]] = []
    for event in events:
        ts = event.get("_parsed_ts")
        if not isinstance(ts, datetime):
            continue
        text = extract_message_text(event)
        if text:
            messages.append((ts, text))

    failing_outputs: list[datetime] = []
    for call in calls:
        output = call.output or ""
        if is_tool_failure(output):
            failing_outputs.append(call.output_ts or call.timestamp)

    for message_ts, text in messages:
        if PASS_CLAIM_PATTERN.search(text) and any(ts > message_ts for ts in failing_outputs):
            inconsistencies.append(
                "Claim/evidence mismatch: success language appeared before a later failing tool output."
            )
            break

    observed_validation = any(
        call.name == "exec_command" and is_validation_command(str(call.arguments.get("cmd") or ""))
        for call in calls
    )
    if delivery_gate.get("required_checks_green", False) and not observed_validation:
        inconsistencies.append(
            "Partial gate evidence: required checks were marked green but no explicit validation command was observed."
        )

    observed_branches: set[str] = set()
    for call in calls:
        if call.name != "exec_command":
            continue
        cmd = str(call.arguments.get("cmd") or "")
        for match in BRANCH_PATTERN.findall(cmd):
            observed_branches.add(match)
    if observed_branches and branch not in observed_branches:
        inconsistencies.append(
            f"State drift risk: observed branch names {sorted(observed_branches)} did not include expected branch `{branch}`."
        )

    if not delivery_gate.get("pr_non_draft", True):
        inconsistencies.append("Delivery gate failed: PR remained draft at handoff.")
    if not delivery_gate.get("required_checks_green", True):
        inconsistencies.append("Delivery gate failed: required checks were not green at handoff.")
    if not delivery_gate.get("linear_in_review", True):
        inconsistencies.append("Delivery gate failed: Linear issue was not in `In Review` at handoff.")

    return inconsistencies


def derive_struggles_resolutions(calls: list[ParsedCall], metrics: dict[str, Any]) -> tuple[list[str], list[str]]:
    struggles: list[str] = []
    resolutions: list[str] = []

    if metrics["retry_count"] > 0:
        struggles.append(
            f"Repeated commands occurred {metrics['retry_count']} time(s), indicating retry loops."
        )
    if metrics["tool_error_count"] > 0:
        struggles.append(
            f"Tool outputs contained {metrics['tool_error_count']} error-like event(s)."
        )
    if metrics["aborted_turn_count"] > 0:
        struggles.append(
            f"Run was interrupted {metrics['aborted_turn_count']} time(s) before completion."
        )
    if metrics["pivot_count"] > 5:
        struggles.append(
            f"High context churn detected with {metrics['pivot_count']} command-category pivots."
        )

    command_outcomes: dict[str, list[bool]] = {}
    for call in calls:
        if call.name != "exec_command":
            continue
        cmd = normalize_command(str(call.arguments.get("cmd") or ""))
        if not cmd:
            continue
        output = call.output or ""
        failed = is_tool_failure(output)
        command_outcomes.setdefault(cmd, []).append(failed)

    for command, outcomes in command_outcomes.items():
        if any(outcomes) and not outcomes[-1]:
            resolutions.append(
                f"Recovered command after failure by rerunning successfully: `{command}`."
            )

    if metrics["tool_error_count"] > 0 and metrics["retry_count"] > 0 and not resolutions:
        resolutions.append("Recovered from transient failures via iterative retries and narrowed command scope.")
    if not struggles:
        struggles.append("No material execution struggles detected from available logs.")
    if not resolutions:
        resolutions.append("No explicit recovery sequence was detected from available logs.")

    return struggles, resolutions


def derive_heuristics(metrics: dict[str, Any], inconsistencies: list[str]) -> list[str]:
    heuristics: list[str] = []

    if metrics["retry_count"] > 0:
        heuristics.append("When a command fails, rerun a narrowed version before broad full-suite checks.")
    if metrics["tool_error_count"] > 0:
        heuristics.append("Capture the first failing output excerpt early to avoid context drift during debugging.")
    if metrics["aborted_turn_count"] > 0:
        heuristics.append("After interruption, immediately re-check branch, git status, and active ticket context.")
    if any("validation command" in line for line in inconsistencies):
        heuristics.append("Enforce explicit local validation command evidence before handoff messaging.")
    if not heuristics:
        heuristics.append("Maintain current execution flow; no new heuristics were strongly indicated.")

    return heuristics


def derive_skill_deltas(metrics: dict[str, Any], inconsistencies: list[str]) -> list[str]:
    deltas: list[str] = []

    if any("validation command" in item for item in inconsistencies):
        deltas.append(
            "Add a hard checklist item requiring explicit validation command evidence in final handoff notes."
        )
    if metrics["retry_count"] >= 3:
        deltas.append("Add retry budget guidance: escalate strategy after three repeated command failures.")
    if metrics["pivot_count"] >= 6:
        deltas.append("Add branch-scope guardrail to reduce exploration/validation thrash.")
    if metrics["aborted_turn_count"] > 0:
        deltas.append("Add interruption recovery mini-checklist before resuming implementation.")
    if not deltas:
        deltas.append("No critical skill delta identified; retain existing workflow and monitor future runs.")

    return deltas


def build_evidence_excerpt(
    calls: list[ParsedCall],
    sqlite_rows: list[dict[str, Any]],
    limit: int = 12,
) -> list[str]:
    excerpts: list[str] = []
    for call in calls:
        if len(excerpts) >= limit:
            break
        if call.name != "exec_command":
            continue

        cmd = normalize_command(str(call.arguments.get("cmd") or ""))
        output = call.output or ""
        if not cmd:
            continue

        if is_tool_failure(output):
            snippet = output[:220].replace("\n", " ").strip()
            excerpts.append(f"Command failed: `{cmd}` | output: {snippet}")
        elif TEST_COMMAND_PATTERN.search(cmd):
            snippets = output[:140].replace("\n", " ").strip()
            excerpts.append(f"Validation command: `{cmd}` | output: {snippets}")

    for row in sqlite_rows:
        if len(excerpts) >= limit:
            break
        body = str(row.get("body") or "")
        if not body:
            continue
        if ERROR_PATTERN.search(body):
            target = row.get("target", "log")
            excerpts.append(
                f"Runtime log [{target}]: {body[:220].replace(chr(10), ' ').strip()}"
            )

    if not excerpts:
        excerpts.append("No significant redacted evidence excerpts were available from the selected time window.")

    return [redact_text(item) for item in excerpts]


def summarize_timeline(
    start_ts: datetime,
    handoff_ts: datetime,
    metrics: dict[str, Any],
    session_files: list[Path],
    sqlite_rows: list[dict[str, Any]],
) -> str:
    session_desc = ", ".join(path.name for path in session_files[:3]) or "none"
    if len(session_files) > 3:
        session_desc = f"{session_desc} (+{len(session_files) - 3} more)"

    summary_lines = [
        f"Window: `{iso_z(start_ts)}` to `{iso_z(handoff_ts)}`.",
        f"Session files inspected: {len(session_files)} ({session_desc}).",
        f"Session events: {metrics['event_count']}. Tool calls: {metrics['tool_call_count']}. Commands: {metrics['command_count']}.",
        f"SQLite log rows inspected: {len(sqlite_rows)}.",
        "Phase timing (seconds): "
        f"setup={metrics['elapsed_setup_seconds']}, "
        f"implementation={metrics['elapsed_implementation_seconds']}, "
        f"validation={metrics['elapsed_validation_seconds']}.",
    ]
    return "\n".join(summary_lines)


def _yaml_quote(value: Any) -> str:
    if value is None:
        return '""'
    return json.dumps(str(value), ensure_ascii=False)


def _render_list(items: list[str]) -> str:
    if not items:
        return "- none"
    return "\n".join(f"- {item}" for item in items)


def build_report(
    *,
    metadata: dict[str, Any],
    delivery_gate: dict[str, bool],
    timeline_summary: str,
    inconsistencies: list[str],
    struggles: list[str],
    resolutions: list[str],
    heuristics: list[str],
    proposed_skill_deltas: list[str],
    light_metrics: dict[str, Any],
    evidence: list[str],
) -> str:
    frontmatter_lines = [
        "---",
        f"ticket_id: {_yaml_quote(metadata.get('ticket_id'))}",
        f"thread_id: {_yaml_quote(metadata.get('thread_id'))}",
        f"created_at: {_yaml_quote(metadata.get('created_at'))}",
        f"skill_version: {_yaml_quote(metadata.get('skill_version'))}",
        f"repo: {_yaml_quote(metadata.get('repo'))}",
        f"branch: {_yaml_quote(metadata.get('branch'))}",
        f"pr_url: {_yaml_quote(metadata.get('pr_url'))}",
        f"status: {_yaml_quote(metadata.get('status'))}",
        "---",
        "",
    ]

    metadata_lines = [
        f"ticket_id: `{metadata.get('ticket_id', '')}`",
        f"linear_issue_id: `{metadata.get('linear_issue_id', '')}`",
        f"thread_id: `{metadata.get('thread_id', '')}`",
        f"created_at: `{metadata.get('created_at', '')}`",
        f"repo: `{metadata.get('repo', '')}`",
        f"branch: `{metadata.get('branch', '')}`",
        f"pr_url: `{metadata.get('pr_url', '')}`",
        f"commit_sha: `{metadata.get('commit_sha', '')}`",
        f"validation_summary: `{metadata.get('validation_summary', '')}`",
    ]

    delivery_lines = [
        f"pr_non_draft: `{delivery_gate.get('pr_non_draft', False)}`",
        f"required_checks_green: `{delivery_gate.get('required_checks_green', False)}`",
        f"linear_in_review: `{delivery_gate.get('linear_in_review', False)}`",
    ]

    metric_lines = [f"{key}: `{value}`" for key, value in sorted(light_metrics.items())]

    sections = [
        "## Metadata",
        _render_list(metadata_lines),
        "",
        "## Delivery Gate Check",
        _render_list(delivery_lines),
        "",
        "## Timeline Summary",
        timeline_summary,
        "",
        "## Inconsistencies Found",
        _render_list(inconsistencies),
        "",
        "## Struggles and Resolutions",
        "### Struggles",
        _render_list(struggles),
        "",
        "### Resolutions",
        _render_list(resolutions),
        "",
        "## Reusable Heuristics",
        _render_list(heuristics),
        "",
        "## Proposed Skill Deltas",
        _render_list(proposed_skill_deltas),
        "",
        "## Light Metrics",
        _render_list(metric_lines),
        "",
        "## Redacted Evidence Appendix",
        _render_list(evidence),
        "",
    ]

    report = "\n".join(frontmatter_lines + sections)
    return redact_text(report)


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def upsert_index(index_path: Path, record: dict[str, Any]) -> None:
    ensure_directory(index_path.parent)
    existing: list[dict[str, Any]] = []
    if index_path.exists():
        for line in index_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                existing.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    run_key = str(record.get("run_key", ""))
    filtered = [item for item in existing if str(item.get("run_key", "")) != run_key]
    filtered.append(record)
    index_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in filtered) + "\n",
        encoding="utf-8",
    )


def write_failure_artifacts(
    *,
    out_dir: Path,
    payload: dict[str, Any],
    error_message: str,
) -> tuple[Path, Path]:
    alerts_dir = out_dir / "alerts"
    ensure_directory(alerts_dir)
    ensure_directory(out_dir)

    timestamp = payload.get("handoff_ts") or iso_z(datetime.now(tz=timezone.utc))
    safe_timestamp = str(timestamp).replace(":", "-")
    safe_ticket = str(payload.get("ticket_id", "unknown")).replace("/", "_")
    safe_thread = str(payload.get("thread_id", "unknown")).replace("/", "_")

    alert_path = alerts_dir / f"{safe_timestamp}-{safe_ticket}-{safe_thread}.json"
    alert_record = {
        "type": "retrospective_generation_failure",
        "error": redact_text(error_message),
        "payload": payload,
        "created_at": iso_z(datetime.now(tz=timezone.utc)),
    }
    alert_path.write_text(json.dumps(alert_record, ensure_ascii=False, indent=2), encoding="utf-8")

    queue_path = out_dir / "queue.jsonl"
    queue_record = {
        "ticket_id": payload.get("ticket_id"),
        "thread_id": payload.get("thread_id"),
        "handoff_ts": payload.get("handoff_ts"),
        "error": redact_text(error_message),
        "queued_at": iso_z(datetime.now(tz=timezone.utc)),
        "payload": payload,
    }
    with queue_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(queue_record, ensure_ascii=False) + "\n")

    return alert_path, queue_path


def parse_ci_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value}")


def default_codex_home() -> Path:
    return Path((Path.home() / ".codex") if "CODEX_HOME" not in os_environ() else os_environ()["CODEX_HOME"])


def os_environ() -> dict[str, str]:
    # Wrapper enables easier patching and test isolation if needed.
    import os

    return os.environ


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a strict redacted retrospective markdown report for a delivered ticket run."
    )
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--pr-url", required=True)
    parser.add_argument("--linear-issue-id", required=True)
    parser.add_argument("--start-ts", required=True)
    parser.add_argument("--handoff-ts", required=True)
    parser.add_argument("--commit-sha")
    parser.add_argument("--validation-summary")
    parser.add_argument("--ci-check-ids")
    parser.add_argument("--skill-version", default="ticket-retrospective/v1")
    parser.add_argument("--codex-home", default=str(default_codex_home()))
    parser.add_argument("--output-dir")
    parser.add_argument("--pr-non-draft", default="true", type=parse_bool)
    parser.add_argument("--required-checks-green", default="true", type=parse_bool)
    parser.add_argument("--linear-in-review", default="true", type=parse_bool)
    parser.add_argument("--non-blocking", action="store_true", default=True)
    parser.add_argument("--blocking", action="store_true", default=False)
    return parser


def _validate_thread_id(thread_id: str) -> None:
    # Preserve flexibility for non-UUID ids in future environments.
    if len(thread_id) < 8:
        raise ValueError("thread_id must be at least 8 characters")
    if not THREAD_ID_PATTERN.match(thread_id):
        # Do not fail hard on format mismatch; this is informational validation.
        return


def run(args: argparse.Namespace) -> dict[str, Any]:
    _validate_thread_id(args.thread_id)
    start_ts = parse_ts(args.start_ts)
    handoff_ts = parse_ts(args.handoff_ts)
    if handoff_ts < start_ts:
        raise ValueError("handoff_ts must be greater than or equal to start_ts")

    codex_home = Path(args.codex_home).expanduser().resolve()
    out_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else (codex_home / "ticket-retrospectives").resolve()
    )

    delivery_gate = {
        "pr_non_draft": bool(args.pr_non_draft),
        "required_checks_green": bool(args.required_checks_green),
        "linear_in_review": bool(args.linear_in_review),
    }

    events, session_files = load_session_events(
        codex_home=codex_home,
        thread_id=args.thread_id,
        start_ts=start_ts,
        handoff_ts=handoff_ts,
    )
    sqlite_rows = load_sqlite_logs(
        codex_home=codex_home,
        thread_id=args.thread_id,
        start_ts=start_ts,
        handoff_ts=handoff_ts,
    )

    calls = extract_calls(events)
    metrics = compute_metrics(calls, events, start_ts, handoff_ts)
    inconsistencies = detect_inconsistencies(
        calls=calls,
        events=events,
        delivery_gate=delivery_gate,
        branch=args.branch,
    )
    struggles, resolutions = derive_struggles_resolutions(calls, metrics)
    heuristics = derive_heuristics(metrics, inconsistencies)
    deltas = derive_skill_deltas(metrics, inconsistencies)
    evidence = build_evidence_excerpt(calls, sqlite_rows)
    timeline_summary = summarize_timeline(start_ts, handoff_ts, metrics, session_files, sqlite_rows)

    created_at = iso_z(datetime.now(tz=timezone.utc))
    handoff_date = handoff_ts.astimezone(timezone.utc)
    report_dir = out_dir / handoff_date.strftime("%Y") / handoff_date.strftime("%m") / args.ticket_id
    ensure_directory(report_dir)
    file_name = f"{handoff_date.strftime('%Y-%m-%dT%H-%M-%SZ')}-{args.thread_id}.md"
    report_path = report_dir / file_name

    status = "ok" if not inconsistencies else "warning"
    metadata = {
        "ticket_id": args.ticket_id,
        "linear_issue_id": args.linear_issue_id,
        "thread_id": args.thread_id,
        "created_at": created_at,
        "skill_version": args.skill_version,
        "repo": args.repo_path,
        "branch": args.branch,
        "pr_url": args.pr_url,
        "status": status,
        "commit_sha": args.commit_sha or "",
        "validation_summary": args.validation_summary or "",
        "ci_check_ids": parse_ci_ids(args.ci_check_ids),
    }

    report = build_report(
        metadata=metadata,
        delivery_gate=delivery_gate,
        timeline_summary=timeline_summary,
        inconsistencies=inconsistencies or ["No inconsistencies detected from available evidence."],
        struggles=struggles,
        resolutions=resolutions,
        heuristics=heuristics,
        proposed_skill_deltas=deltas,
        light_metrics=metrics,
        evidence=evidence,
    )
    report_path.write_text(report, encoding="utf-8")

    run_key = f"{args.ticket_id}:{args.thread_id}:{iso_z(handoff_ts)}"
    index_record = {
        "run_key": run_key,
        "ticket_id": args.ticket_id,
        "thread_id": args.thread_id,
        "linear_issue_id": args.linear_issue_id,
        "created_at": created_at,
        "handoff_ts": iso_z(handoff_ts),
        "status": status,
        "report_path": str(report_path),
        "repo": args.repo_path,
        "branch": args.branch,
        "pr_url": args.pr_url,
    }
    upsert_index(out_dir / "index.jsonl", index_record)

    return {
        "status": status,
        "report_path": str(report_path),
        "index_path": str(out_dir / "index.jsonl"),
        "run_key": run_key,
    }


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    non_blocking = bool(args.non_blocking and not args.blocking)

    payload = {
        "ticket_id": args.ticket_id,
        "thread_id": args.thread_id,
        "repo_path": args.repo_path,
        "branch": args.branch,
        "pr_url": args.pr_url,
        "linear_issue_id": args.linear_issue_id,
        "start_ts": args.start_ts,
        "handoff_ts": args.handoff_ts,
        "commit_sha": args.commit_sha,
        "validation_summary": args.validation_summary,
        "ci_check_ids": parse_ci_ids(args.ci_check_ids),
    }

    codex_home = Path(args.codex_home).expanduser().resolve()
    out_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else (codex_home / "ticket-retrospectives").resolve()
    )

    try:
        result = run(args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # pragma: no cover - behavior tested via artifact checks
        error_message = f"{type(exc).__name__}: {exc}"
        stack = traceback.format_exc(limit=8)
        alert_path, queue_path = write_failure_artifacts(
            out_dir=out_dir,
            payload=payload,
            error_message=f"{error_message}\n{stack}",
        )
        result = {
            "status": "error",
            "error": redact_text(error_message),
            "alert_path": str(alert_path),
            "queue_path": str(queue_path),
            "non_blocking": non_blocking,
        }
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 0 if non_blocking else 1


if __name__ == "__main__":
    raise SystemExit(main())
