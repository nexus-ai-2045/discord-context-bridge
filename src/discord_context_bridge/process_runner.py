from __future__ import annotations

import os
import signal
from dataclasses import dataclass
from os import PathLike
import subprocess
import threading
import time
from typing import Mapping, Sequence


OUTPUT_LIMIT = 64 * 1024
PROCESS_CLEANUP_TIMEOUT = 5.0
_CHILD_ENV_ALLOWLIST = (
    "PATH",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
)


@dataclass(frozen=True)
class ProcessResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    stdout_bytes: int
    stderr_bytes: int
    failure_stage: str | None = None
    output_truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def elapsed(self) -> float:
        """Backward-compatible alias for callers that used the shorter name."""
        return self.elapsed_seconds


def minimal_child_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return only non-secret environment variables normally needed to launch tools."""
    parent = os.environ if source is None else source
    by_upper = {key.upper(): value for key, value in parent.items()}
    return {
        key: by_upper[key.upper()]
        for key in _CHILD_ENV_ALLOWLIST
        if key.upper() in by_upper
    }


def _read_bounded_stream(stream, limit_reached: threading.Event) -> tuple[bytes, int]:
    """Drain a pipe without retaining more than OUTPUT_LIMIT bytes."""
    retained = bytearray()
    size = 0
    while True:
        chunk = stream.read(8192)
        if not chunk:
            break
        size += len(chunk)
        remaining = OUTPUT_LIMIT - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])
        if size > OUTPUT_LIMIT:
            limit_reached.set()
    return bytes(retained), size


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=PROCESS_CLEANUP_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    try:
        process.wait(timeout=PROCESS_CLEANUP_TIMEOUT)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=PROCESS_CLEANUP_TIMEOUT)
    except subprocess.TimeoutExpired:
        # The runner must remain bounded even if the OS refuses process cleanup.
        pass


def run_process(
    argv: Sequence[str],
    *,
    cwd: str | PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> ProcessResult:
    """Run an argv-only child and return UTF-8 text truncated to 64 KiB per stream.

    Reader threads drain both pipes while retaining at most 64 KiB per stream;
    exceeding that quota stops the process tree. ``stdout_bytes`` and
    ``stderr_bytes`` retain the observed sizes.
    Supplying ``env`` uses replacement semantics; callers must pass every
    variable the child needs, normally starting from :func:`minimal_child_env`.
    """
    if isinstance(argv, (str, bytes)):
        raise TypeError("argv must be a sequence of strings, not a shell command")

    command = list(argv)
    child_env = minimal_child_env() if env is None else dict(env)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    group_options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    started = time.perf_counter()
    failure_stage: str | None = None
    returncode = 0

    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        **group_options,
    )
    assert process.stdout is not None and process.stderr is not None
    limit_reached = threading.Event()
    results: dict[str, tuple[bytes, int]] = {}

    def read_stream(name: str, stream) -> None:
        results[name] = _read_bounded_stream(stream, limit_reached)

    readers = [
        threading.Thread(target=read_stream, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=read_stream, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = None if timeout is None else started + timeout
    while process.poll() is None:
        if limit_reached.is_set():
            _terminate_process_tree(process)
            returncode = 125
            failure_stage = "output_limit"
            break
        if deadline is not None and time.perf_counter() >= deadline:
            _terminate_process_tree(process)
            returncode = 124
            failure_stage = "timeout"
            break
        time.sleep(0.01)
    else:
        returncode = process.returncode

    for reader in readers:
        reader.join(timeout=PROCESS_CLEANUP_TIMEOUT)
    # A short-lived child can exit between writing past the quota and the main
    # loop observing the event. The completed output is still a quota failure.
    if limit_reached.is_set() and failure_stage is None:
        returncode = 125
        failure_stage = "output_limit"
    process.stdout.close()
    process.stderr.close()
    stdout_raw, stdout_bytes = results.get("stdout", (b"", 0))
    stderr_raw, stderr_bytes = results.get("stderr", (b"", 0))
    stdout = stdout_raw.decode("utf-8", errors="replace")
    stderr = stderr_raw.decode("utf-8", errors="replace")
    stdout_truncated = stdout_bytes > OUTPUT_LIMIT
    stderr_truncated = stderr_bytes > OUTPUT_LIMIT

    return ProcessResult(
        args=tuple(command),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        elapsed_seconds=time.perf_counter() - started,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        failure_stage=failure_stage,
        output_truncated=stdout_truncated or stderr_truncated,
    )
