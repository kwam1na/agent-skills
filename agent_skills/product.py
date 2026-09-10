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


class CancellationIncompleteError(subprocess.SubprocessError):
    """Cancellation ended without evidence that the launched tree drained."""

    def __init__(
        self,
        *,
        signal_number: int,
        platform: str,
        cleanup: str,
        target_pid: int,
        target_state: str,
        liveness: str,
        surviving_pids: tuple[int, ...],
    ) -> None:
        self.signal_number = signal_number
        self.platform = platform
        self.cleanup = cleanup
        self.target_pid = target_pid
        self.target_state = target_state
        self.liveness = liveness
        self.surviving_pids = surviving_pids
        try:
            signal_name = signal.Signals(signal_number).name
        except ValueError:
            signal_name = str(signal_number)
        survivors = ",".join(str(pid) for pid in surviving_pids) if surviving_pids else "unknown"
        super().__init__(
            "product.cancellation_incomplete: "
            f"signal={signal_name}; platform={platform};cleanup={cleanup};"
            f"targetPid={target_pid};targetState={target_state};"
            f"liveness={liveness};survivingPids={survivors}"
        )


def _observed_child_liveness(child: subprocess.Popen) -> tuple[str, str, tuple[int, ...]]:
    """Return only liveness directly observed through the child handle."""
    try:
        status = child.poll()
    except (OSError, subprocess.SubprocessError):
        return "unknown", "unknown", ()
    if status is None:
        return "running", "confirmed", (child.pid,)
    return "exited", "unknown", ()


def _incomplete_cancellation(
    child: subprocess.Popen,
    *,
    interrupted: int,
    platform: str,
    cleanup: str,
) -> CancellationIncompleteError:
    target_state, liveness, surviving_pids = _observed_child_liveness(child)
    return CancellationIncompleteError(
        signal_number=interrupted,
        platform=platform,
        cleanup=cleanup,
        target_pid=child.pid,
        target_state=target_state,
        liveness=liveness,
        surviving_pids=surviving_pids,
    )


def _wait_for_posix_group(child: subprocess.Popen, timeout: float) -> bool:
    """Poll the isolated process group until it drains or the bound expires."""
    deadline = time.monotonic() + timeout
    while True:
        child.poll()
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def runtime_root(lifecycle: Lifecycle) -> Path:
    status = lifecycle.status().as_dict()
    if status["lifecycle"] != "current" or status["blockers"]:
        raise ValueError("product.lifecycle_unready: reconcile the existing lifecycle first")
    root = lifecycle.root / ".agent-skills" / "generations" / status["active"]["generation"] / "runtime"
    if not (root / "runtime.json").is_file():
        raise ValueError("product.runtime_missing: installed generation has no delivery runtime")
    return root


def reconcile_policy(lifecycle: Lifecycle, *, check: bool, bootstrap: bool = False) -> None:
    runtime = runtime_root(lifecycle)
    policy = lifecycle.root / ".agents" / "policy"
    if not policy.exists() and not bootstrap:
        return
    arguments = ["node", str(runtime / "policy.mjs"), "--product"]
    if bootstrap:
        arguments.append("--bootstrap")
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
    if not windows:
        if hasattr(signal, "SIGHUP") and signal.getsignal(signal.SIGHUP) is not signal.SIG_IGN:
            signals += (signal.SIGHUP,)
        if hasattr(signal, "SIGQUIT"):
            signals += (signal.SIGQUIT,)
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
            try:
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                               capture_output=True, timeout=2, check=True)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                raise _incomplete_cancellation(
                    child,
                    interrupted=interrupted,
                    platform="windows",
                    cleanup="taskkill-failed",
                ) from error
            try:
                child.wait(timeout=2)
            except (OSError, subprocess.SubprocessError) as error:
                raise _incomplete_cancellation(
                    child,
                    interrupted=interrupted,
                    platform="windows",
                    cleanup="child-wait-failed",
                ) from error
            return 128 + interrupted
        # Wait for the whole group, including workers whose parent exited first.
        try:
            drained = _wait_for_posix_group(child, 2)
        except (OSError, subprocess.SubprocessError) as error:
            raise _incomplete_cancellation(
                child,
                interrupted=interrupted,
                platform="posix",
                cleanup="pre-escalation-wait-failed",
            ) from error
        if drained:
            return 128 + interrupted
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            return 128 + interrupted
        except OSError as error:
            raise _incomplete_cancellation(
                child,
                interrupted=interrupted,
                platform="posix",
                cleanup="sigkill-failed",
            ) from error
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired as error:
            raise _incomplete_cancellation(
                child,
                interrupted=interrupted,
                platform="posix",
                cleanup="post-escalation-timeout",
            ) from error
        except (OSError, subprocess.SubprocessError) as error:
            raise _incomplete_cancellation(
                child,
                interrupted=interrupted,
                platform="posix",
                cleanup="post-escalation-wait-failed",
            ) from error
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
