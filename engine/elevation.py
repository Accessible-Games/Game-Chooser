"""Starting helper programs, with the administrator prompt only for the program that needs it.

Game Chooser itself never runs elevated. Elevated programs go through ShellExecuteEx with the
"runas" verb, so Windows shows its UAC prompt for that one program.
"""

import subprocess
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


class _SubprocessProcess:
    def __init__(self, popen: subprocess.Popen):
        self._popen = popen
        self.pid = popen.pid
        self.handle = int(getattr(popen, "_handle", 0))

    def wait(self, timeout: float | None = None) -> int | None:
        try:
            return self._popen.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def close(self) -> None:
        pass  # subprocess owns its handle and closes it when the Popen is collected.


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
        self.close()
        return self._code

    def close(self) -> None:
        if self._handle is not None:
            self._handle.Close()
            self._handle = None


class WindowsElevator:
    def run(self, exe: str, args: str, elevated: bool, cwd: Path | None = None) -> int:
        process = self.start(exe, args, elevated, cwd)
        try:
            return process.wait(None)
        finally:
            process.close()

    def start(self, exe: str, args: str, elevated: bool, cwd: Path | None = None) -> RunningProcess:
        if elevated:
            return self._start_elevated(exe, args, cwd)
        try:
            popen = subprocess.Popen(f'"{exe}" {args}'.rstrip(), cwd=str(cwd) if cwd else None)
        except OSError as e:
            raise StepError(messages.STEP_PROGRAM_NOT_STARTED.format(name=Path(exe).name)) from e
        return _SubprocessProcess(popen)

    @staticmethod
    def _start_elevated(exe: str, args: str, cwd: Path | None) -> RunningProcess:
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
        try:
            result = shell.ShellExecuteEx(
                fMask=shellcon.SEE_MASK_NOCLOSEPROCESS | _SEE_MASK_NOASYNC,
                lpVerb="runas", lpFile=exe, lpParameters=args,
                lpDirectory=str(cwd) if cwd else None, nShow=win32con.SW_SHOWNORMAL)
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
