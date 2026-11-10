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
APP_PROCESSES = ("Procmon.exe", "Procmon64.exe", "System", "game-chooser.exe", "7z.exe", "innoextract.exe")

# Windows background activity and tools running beside a trace (Windows Update, Defender, search indexing, ...).
# Not explorer.exe, conhost.exe, cmd.exe, powershell.exe, python.exe or rundll32.exe: games or their launchers run them.
SYSTEM_PROCESSES = (
    "svchost.exe", "MsMpEng.exe", "MpDefenderCoreService.exe", "NisSrv.exe", "MoUsoCoreWorker.exe", "TiWorker.exe",
    "TrustedInstaller.exe", "SearchIndexer.exe", "SearchProtocolHost.exe", "SearchFilterHost.exe",
    "CompatTelRunner.exe", "wuauclt.exe", "UsoClient.exe", "sppsvc.exe", "lsass.exe", "services.exe", "csrss.exe",
    "smss.exe", "wininit.exe", "winlogon.exe", "Registry", "MemCompression", "SgrmBroker.exe",
    "SecurityHealthService.exe", "WmiPrvSE.exe", "dllhost.exe", "taskhostw.exe", "claude.exe", "nvda.exe",
)
EXCLUDED_PROCESSES = APP_PROCESSES + SYSTEM_PROCESSES

# Procmon's display strings for the Desired Access of a CreateFile that opens an existing file for reading only.
# None holds Write, Append, Delete, Generic All or Maximum Allowed, so no write kind can come through one.
READ_ONLY_ACCESS = (
    "Read Attributes", "Generic Read", "Read Control", "Read Data/List Directory, Synchronize",
    "Read Data/List Directory, Execute/Traverse, Synchronize", "Execute/Traverse, Synchronize",
    "Read Data/List Directory, Execute/Traverse, Read Attributes, Synchronize", "Synchronize",
    "Read Attributes, Synchronize", "Generic Read/Execute", "Generic Execute", "Read EA",
    "Read Attributes, Read Control, Synchronize", "Read Control, Synchronize",
    "Read Data/List Directory, Read Attributes, Synchronize", "Read Data/List Directory",
)


def _detail_excludes() -> list[tuple[str, str]]:
    """(relation name, value) of each Detail rule that drops an event: read-only opens, opened-existing keys and
    a disposition set to not-delete. classify keeps none of them."""
    rules = [("BEGINS_WITH", f"Desired Access: {acc}, Disposition: Open,") for acc in READ_ONLY_ACCESS]
    rules.append(("CONTAINS", "Disposition: REG_OPENED_EXISTING_KEY"))
    rules.append(("IS", "Delete: False"))
    return rules


def describe_filter(operations: Sequence[str] = CAPTURE_OPERATIONS) -> list[str]:
    """The active filter in readable form, one line per category, for the recorder log."""
    return [
        "capture filter, included operations: " + ", ".join(operations),
        "capture filter, excluded processes: " + ", ".join(EXCLUDED_PROCESSES),
        "capture filter, excluded Detail rules: " + "; ".join(f"{rel} {value!r}" for rel, value in _detail_excludes()),
    ]


def filter_config(operations: Sequence[str] = CAPTURE_OPERATIONS, destructive: bool = True) -> dict:
    """Include only `operations`; drop the events of EXCLUDED_PROCESSES, read-only opens, opened-existing registry
    keys and "Delete: False" dispositions.

    With the destructive filter, events the filter drops are never written to the backing file.
    """
    from procmon_parser import Column, Rule, RuleAction, RuleRelation
    rules = [Rule(Column.OPERATION, RuleRelation.IS, op, RuleAction.INCLUDE) for op in operations]
    rules += [Rule(Column.PROCESS_NAME, RuleRelation.IS, name, RuleAction.EXCLUDE) for name in EXCLUDED_PROCESSES]
    rules += [Rule(Column.DETAIL, getattr(RuleRelation, rel), value, RuleAction.EXCLUDE)
              for rel, value in _detail_excludes()]
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
