"""Optional command adapter for the shared observation contract, not a scheduler.

Native hosts may perform these same commands directly. This adapter never derives
review decisions, dispatches a lens, deletes output, retries, or changes authority.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile

MAX_RESPONSE_BYTES = 16 * 1024 * 1024


def invoke(argv: list[str], cwd: str) -> tuple[int, object]:
    # Bound retained output without loading an arbitrary runtime response into RAM.
    # No shell, raw environment capture, or transcript inspection.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        result = subprocess.run(argv, cwd=cwd, stdout=output, stderr=error, timeout=30, check=False)
        if output.tell() > MAX_RESPONSE_BYTES:
            return 1, {}
        output.seek(0)
        try:
            return result.returncode, json.load(output)
        except (ValueError, UnicodeError):
            # Emit/capture may have a human-readable successful response.
            return result.returncode, {}


def report(request: dict) -> dict:
    def outcome(status: str, reason: str) -> dict:
        return {"spec": "workflow-observation-result/1", "status": status, "reason": reason}

    try:
        runtime = request["runtime"]
        cwd = request["cwd"]
        run_id = request["runId"]
        operation = request["operation"]
        if (not isinstance(runtime, list) or not runtime or
                any(not isinstance(part, str) or not part for part in runtime) or
                not isinstance(cwd, str) or not isinstance(run_id, str) or
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id) or
                operation not in ("emit", "capture")):
            return outcome("unavailable", "Supply a trusted runtime argv, cwd, existing run ID and supported operation.")
        code, capabilities = invoke([*runtime, "runs", "capabilities", "--json"], cwd)
        if (code != 0 or not isinstance(capabilities, dict) or capabilities.get("spec") != "run-capabilities/1" or
                "run-event/2" not in capabilities.get("writerVersions", []) or capabilities.get("artifactCapture") is not True):
            return outcome("unsupported", "Install the coordinated runtime with v2 events and artifact capture; no observation was written.")
        code, journal = invoke([*runtime, "runs", "show", run_id, "--json"], cwd)
        if code != 0:
            return outcome("unavailable", "The selected run could not be read; retain output and report the limitation.")
        if not isinstance(journal, dict) or journal.get("spec") != "delivery-run-export/2" or journal.get("runId") != run_id:
            return outcome("unsupported", "The selected run is not a supported v2 run; retain it and start an explicitly linked successor when authorized.")
        if operation == "capture":
            argv = [*runtime, "runs", "capture", run_id, "--json", json.dumps(request["request"], ensure_ascii=False)]
        else:
            event_id, kind = request["eventId"], request["kind"]
            if not isinstance(event_id, str) or not event_id or not isinstance(kind, str) or not kind:
                return outcome("unavailable", "Supply stable event identity and the observed event kind.")
            argv = [*runtime, "emit", kind, "--run", run_id, "--event-id", event_id,
                    "--json", json.dumps(request["payload"], ensure_ascii=False)]
        code, _ = invoke(argv, cwd)
        if code != 0:
            return outcome("unavailable", "Runtime refused or failed the observation; preserve selected output and report capture as unavailable, not accepted evidence.")
        return outcome("reported", "Runtime accepted the observation; this is not evidence acceptance or a permission grant.")
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return outcome("unavailable", "Observation unavailable; retain selected output and continue under the existing delivery authority.")


def main() -> None:
    raw = sys.stdin.buffer.read(1024 * 1024 + 1)
    try:
        request = json.loads(raw) if len(raw) <= 1024 * 1024 else None
    except (ValueError, UnicodeError):
        request = None
    result = report(request) if isinstance(request, dict) else {
        "spec": "workflow-observation-result/1", "status": "unavailable", "reason": "Expected a bounded JSON request object."}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
