"""Driving Process Monitor headless: its filter configuration and starting, readiness and stopping a capture.

procmon_parser, win32api and win32job are imported inside the functions that use them, so this module imports
cleanly without them.
"""
import logging
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

READY_RETRY_SECONDS = 0.5

# Procmon's display names of the operations a capture keeps.
CAPTURE_OPERATIONS = (
    "CreateFile", "WriteFile", "SetRenameInformationFile", "SetRenameInformationEx",
    "SetRenameInformationExBypassAccessCheck", "SetDispositionInformationFile", "SetDispositionInformationEx",
    "RegCreateKey", "RegSetValue", "RegDeleteValue", "RegDeleteKey",
    # Kept so every process appears in the PML's process table, even one that writes nothing (a launcher).
    "Process Create", "Process Start", "Process Exit",
)

# Game Chooser itself (the compiled exe; also the elevated recorder) and the bundled extraction tools: their writes
# are never a game's, and installs inside a traced run would fill the capture.
EXCLUDED_PROCESSES = ("Procmon.exe", "Procmon64.exe", "System", "game-chooser.exe", "7z.exe", "innoextract.exe")


def filter_config(operations: Sequence[str] = CAPTURE_OPERATIONS, destructive: bool = True) -> dict:
    """Include only `operations`, never the events of EXCLUDED_PROCESSES (Process Monitor, System, Game Chooser and
    its extraction tools).

    With the destructive filter, events the filter drops are never written to the backing file.
    """
    from procmon_parser import Column, Rule, RuleAction, RuleRelation
    rules = [Rule(Column.OPERATION, RuleRelation.IS, op, RuleAction.INCLUDE) for op in operations]
    rules += [Rule(Column.PROCESS_NAME, RuleRelation.IS, name, RuleAction.EXCLUDE) for name in EXCLUDED_PROCESSES]
    return {"FilterRules": rules, "DestructiveFilter": 1 if destructive else 0}


def write_filter_config(path: Path, operations: Sequence[str] = CAPTURE_OPERATIONS,
                        destructive: bool = True) -> None:
    from procmon_parser import dump_configuration
    with open(path, "wb") as f:
        dump_configuration(filter_config(operations, destructive), f)


class ProcmonControl(Protocol):
    def start(self, backing: Path, config: Path) -> None:
        """Start capturing into `backing` with the filter configuration `config`."""

    def wait_ready(self, timeout: float) -> bool:
        """True once the capture is running and writing `backing`."""

    def running(self) -> bool:
        """True while the capture process is alive."""

    def stop(self, timeout: float) -> bool:
        """Stop the capture; True if the capture process exited within `timeout` seconds."""


class WindowsProcmon:
    """Process Monitor run from `exe` (Procmon64.exe). Needs administrator rights."""

    def __init__(self, exe: Path, clock=time.monotonic, sleep=time.sleep):
        self.exe = Path(exe)
        self._clock = clock
        self._sleep = sleep
        self.not_ready_reason: str | None = None  # Plain words for the last failed readiness check.
        self._process: subprocess.Popen | None = None
        self._job = None  # Holds Process Monitor: closing it (also when this program dies) ends the capture.
        self._backing: Path | None = None

    def start(self, backing: Path, config: Path) -> None:
        self._backing = Path(backing)
        self._process = subprocess.Popen([str(self.exe), "/AcceptEula", "/Quiet", "/Minimized",
                                          "/BackingFile", str(backing), "/LoadConfig", str(config)])
        try:
            self._job = _kill_on_close_job(self._process.pid)
        except Exception:
            self._process.kill()
            raise

    def wait_ready(self, timeout: float) -> bool:
        """Wait up to `timeout` seconds for the capture instance to be up and writing its backing file.

        The /WaitForIdle helper fails when it runs before the capture instance has started, so it is retried until
        the deadline, unless the capture process itself has exited.
        """
        deadline = self._clock() + timeout
        attempt = 0
        while True:
            attempt += 1
            if self._process is not None and self._process.poll() is not None:
                return self._not_ready(f"Process Monitor exited (code {self._process.poll()})")
            remaining = deadline - self._clock()
            if remaining <= 0:
                return self._not_ready(self._timeout_reason(timeout))
            try:
                result = subprocess.run([str(self.exe), "/AcceptEula", "/Quiet", "/WaitForIdle"],
                                        timeout=remaining)
            except subprocess.TimeoutExpired:
                return self._not_ready(self._timeout_reason(timeout))
            if result.returncode != 0:
                reason = f"the readiness check exited with code {result.returncode} (attempt {attempt})"
            elif self._backing is None or not self._backing.exists():
                reason = f"the capture file wasn't created yet (attempt {attempt})"
            else:
                log.info("Process Monitor ready (attempt %d)", attempt)
                self.not_ready_reason = None
                return True
            self.not_ready_reason = reason
            log.info("Process Monitor not ready: %s", reason)
            self._sleep(READY_RETRY_SECONDS)

    def _timeout_reason(self, timeout: float) -> str:
        last = f"; last check: {self.not_ready_reason}" if self.not_ready_reason else ""
        return f"no answer within {timeout:g} s{last}"

    def _not_ready(self, reason: str) -> bool:
        self.not_ready_reason = reason
        log.warning("Process Monitor not ready: %s", reason)
        return False

    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        try:
            subprocess.run([str(self.exe), "/AcceptEula", "/Quiet", "/Terminate"], timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
        if self._process is None:
            return True
        try:
            self._process.wait(max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return False
        self._close_job()
        return True

    def _close_job(self) -> None:
        if self._job is not None:
            self._job.Close()
            self._job = None


def _kill_on_close_job(pid: int):
    """A job holding process `pid` that ends it when the job's last handle closes."""
    import win32api
    import win32con
    import win32job
    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid)
    try:
        win32job.AssignProcessToJobObject(job, handle)
    except Exception:
        job.Close()
        raise
    finally:
        handle.Close()
    return job
