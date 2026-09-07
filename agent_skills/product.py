"""Ordinary product commands over the existing repository lifecycle."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from .lifecycle import Lifecycle
from .locking import RepositoryLock


def runtime_root(lifecycle: Lifecycle) -> Path:
    status = lifecycle.status().as_dict()
    if status["lifecycle"] != "current" or status["blockers"]:
        raise ValueError("product.lifecycle_unready: reconcile the existing lifecycle first")
    root = lifecycle.root / ".agent-skills" / "generations" / status["active"]["generation"] / "runtime"
    if not (root / "runtime.json").is_file():
        raise ValueError("product.runtime_missing: installed generation has no delivery runtime")
    return root


def reconcile_policy(lifecycle: Lifecycle, *, check: bool) -> None:
    runtime = runtime_root(lifecycle)
    policy = lifecycle.root / ".agents" / "policy"
    if not policy.exists():
        return
    arguments = ["node", str(runtime / "policy.mjs"), "--product"]
    if check:
        arguments.append("--check")
    result = subprocess.run(arguments, cwd=lifecycle.root, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError("product.policy_unready: " + (result.stderr or result.stdout)[-4000:])


def product_status(lifecycle: Lifecycle) -> dict:
    status = lifecycle.status().as_dict()
    status["productReady"] = False
    try:
        runtime = runtime_root(lifecycle)
        reconcile_policy(lifecycle, check=True)
        status["runtime"] = json.loads((runtime / "runtime.json").read_text())
        status["productReady"] = True
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        status["blockers"] = [*status["blockers"], str(error).split(":", 1)[0]]
        status["reconciliation"] = str(error)
    return status


def _run_command(arguments: list[str], root: Path) -> int:
    """Keep native process-group cancellation inside the installed launcher."""
    interrupted = 0
    child = None
    windows = os.name == "nt"

    def interrupt(signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = interrupted or signum
        if child is not None and not windows:
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass

    signals = (signal.SIGINT, signal.SIGTERM)
    if not windows and hasattr(signal, "SIGHUP"):
        signals += (signal.SIGHUP,)
    previous = {sig: signal.signal(sig, interrupt) for sig in signals}
    try:
        if windows:
            # Isolate console Ctrl+C from descendants so the launcher can stop
            # the whole tree before the direct child exits.
            child = subprocess.Popen(arguments, cwd=root, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            child = subprocess.Popen(arguments, cwd=root, start_new_session=True)
        if interrupted:
            interrupt(interrupted, None)
        while not interrupted:
            try:
                code = child.wait(timeout=0.1)
                if not interrupted:
                    return code
            except subprocess.TimeoutExpired:
                pass
        if windows:
            # Windows has no POSIX group signals. Its native tree termination
            # command stops descendants together, without a new supervisor.
            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                           capture_output=True, timeout=2, check=True)
            child.wait(timeout=2)
            return 128 + interrupted
        # Wait for the whole group, including workers whose parent exited first.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            child.poll()
            try:
                os.killpg(child.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.wait(timeout=2)
        return 128 + interrupted
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run_runtime(lifecycle: Lifecycle, arguments: list[str]) -> int:
    # Share the existing maintenance lock: commands can run together, but a
    # generation cannot change between readiness and completion.
    with RepositoryLock(lifecycle.lock_path, shared=True):
        runtime = runtime_root(lifecycle)
        reconcile_policy(lifecycle, check=True)
        return _run_command([
            "node", "--experimental-strip-types", "--import", str(runtime / "bootstrap.mjs"),
            str(runtime / "cli.mjs"), *arguments,
        ], lifecycle.root)
