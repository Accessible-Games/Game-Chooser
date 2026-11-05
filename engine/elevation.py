"""Starting helper programs, with the administrator prompt only for the program that needs it.

Game Chooser itself never runs elevated. Elevated programs go through ShellExecuteEx with the
"runas" verb, so Windows shows its UAC prompt for that one program. Unelevated programs start
inside a job object, so everything they start can be waited for or stopped (see `job`).
"""

from pathlib import Path
from typing import Protocol

from . import messages
from .errors import ElevationDeclined, StepError

_ERROR_CANCELLED = 1223  # The user said no at the UAC prompt.
_SEE_MASK_NOASYNC = 0x100  # Not in pywin32's shellcon; the SDK value (alias of SEE_MASK_FLAG_DDEWAIT).


class RunningProcess(Protocol):
    """A started program. `handle` is a Windows process handle, valid until `wait` returns an exit code."""

    handle: int
    pid: int

    def wait(self, timeout: float | None = None) -> int | None:
        """Exit code, or None if `timeout` seconds pass first. None means wait forever."""

    def close(self) -> None:
        """Release the process handle. Safe to call more than once."""


class Elevator(Protocol):
    def run(self, exe: str, args: str, elevated: bool, cwd: Path | None = None) -> int:
        """Start `exe` with the command-line `args`, wait for it and return its exit code."""

    def start(self, exe: str, args: str, elevated: bool, cwd: Path | None = None) -> RunningProcess:
        """Start `exe` and return at once."""


class _ShellProcess:
    def __init__(self, handle):
        import win32process
        self._handle = handle
        self.handle = int(handle)
        self.pid = win32process.GetProcessId(handle)
        self._code: int | None = None

    def wait(self, timeout: float | None = None) -> int | None:
        import win32event
        import win32process
        if self._code is not None:
            return self._code
        ms = win32event.INFINITE if timeout is None else max(0, int(timeout * 1000))
        if win32event.WaitForSingleObject(self._handle, ms) == win32event.WAIT_TIMEOUT:
            return None
        self._code = win32process.GetExitCodeProcess(self._handle)
        self._close_handle()
        return self._code

    def _close_handle(self) -> None:
        if self._handle is not None:
            self._handle.Close()
            self._handle = None

    def close(self) -> None:
        self._close_handle()


class _JobProcess(_ShellProcess):
    """An unelevated program put in its own job object before it ran a single instruction.

    `job` holds it and every process it starts (None if Windows refused the job); it stays open
    until `close`. The job doesn't end the programs when it is closed.
    """

    def __init__(self, handle, job):
        super().__init__(handle)
        self.job = job

    def close(self) -> None:
        super().close()
        if self.job is not None:
            self.job.Close()
            self.job = None


class WindowsElevator:
    def __init__(self, elevated_verb: str = "runas"):
        # Test seam only: tests pass "open" to drive the real ShellExecuteEx call without a UAC prompt.
        self._elevated_verb = elevated_verb

    def run(self, exe: str, args: str, elevated: bool, cwd: Path | None = None) -> int:
        process = self.start(exe, args, elevated, cwd)
        try:
            return process.wait(None)
        finally:
            process.close()

    def start(self, exe: str, args: str, elevated: bool, cwd: Path | None = None) -> RunningProcess:
        if elevated:
            return self._start_elevated(exe, args, cwd)
        return self._start_in_job(exe, args, cwd)

    def _start_in_job(self, exe: str, args: str, cwd: Path | None) -> RunningProcess:
        """Start suspended, put it in a new job, then let it run: a child it starts at once is in the job too.

        A refusal to start keeps Windows' error (winerror 740 means it needs administrator rights)
        as the StepError's cause.
        """
        import pywintypes
        import win32job
        import win32process
        try:
            process, thread, _, _ = win32process.CreateProcess(
                None, f'"{exe}" {args}'.rstrip(), None, None, False, win32process.CREATE_SUSPENDED, None,
                str(cwd) if cwd else None, win32process.STARTUPINFO())
        except pywintypes.error as e:
            raise StepError(messages.STEP_PROGRAM_NOT_STARTED.format(name=Path(exe).name)) from e
        job = None
        try:
            job = win32job.CreateJobObject(None, "")
            win32job.AssignProcessToJobObject(job, process)
        except pywintypes.error:
            if job is not None:
                job.Close()
            job = None  # It still runs; only its own exit can be waited for.
        finally:
            win32process.ResumeThread(thread)
            thread.Close()
        return _JobProcess(process, job)

    def _start_elevated(self, exe: str, args: str, cwd: Path | None) -> RunningProcess:
        import pythoncom
        import pywintypes
        import win32con
        from win32com.shell import shell, shellcon

        # ShellExecuteEx may hand work to COM shell extensions, so COM must be up on this thread.
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED | pythoncom.COINIT_DISABLE_OLE1DDE)
            com_started = True
        except pythoncom.com_error:
            com_started = False  # Already initialised in another mode; that works too.
        options = {"fMask": shellcon.SEE_MASK_NOCLOSEPROCESS | _SEE_MASK_NOASYNC, "lpVerb": self._elevated_verb,
                   "lpFile": exe, "lpParameters": args, "nShow": win32con.SW_SHOWNORMAL}
        if cwd:
            options["lpDirectory"] = str(cwd)  # pywin32 refuses None for string fields, so omit it instead.
        try:
            result = shell.ShellExecuteEx(**options)
        except pywintypes.error as e:
            if e.winerror == _ERROR_CANCELLED:
                raise ElevationDeclined(str(e)) from e
            raise StepError(messages.STEP_PROGRAM_NOT_STARTED.format(name=Path(exe).name)) from e
        finally:
            if com_started:
                pythoncom.CoUninitialize()
        handle = result.get("hProcess")
        if not handle:
            raise StepError(messages.STEP_PROGRAM_NOT_STARTED.format(name=Path(exe).name))
        return _ShellProcess(handle)
