"""Driving Process Monitor headless: its filter configuration and starting, readiness and stopping a capture.

procmon_parser, win32api and win32job are imported inside the functions that use them, so this module imports
cleanly without them.
"""
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

# Procmon's display names of the operations a capture keeps.
CAPTURE_OPERATIONS = (
    "CreateFile", "WriteFile", "SetRenameInformationFile", "SetRenameInformationEx",
    "SetRenameInformationExBypassAccessCheck", "SetDispositionInformationFile", "SetDispositionInformationEx",
    "RegCreateKey", "RegSetValue", "RegDeleteValue", "RegDeleteKey",
    # Kept so every process appears in the PML's process table, even one that writes nothing (a launcher).
    "Process Create", "Process Start", "Process Exit",
)

EXCLUDED_PROCESSES = ("Procmon.exe", "Procmon64.exe", "System")


def filter_config(operations: Sequence[str] = CAPTURE_OPERATIONS, destructive: bool = True) -> dict:
    """Include only `operations`, never Process Monitor's own or System's events.

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

    def __init__(self, exe: Path):
        self.exe = Path(exe)
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
        try:
            result = subprocess.run([str(self.exe), "/AcceptEula", "/Quiet", "/WaitForIdle"], timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0 and self._backing is not None and self._backing.exists()

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
