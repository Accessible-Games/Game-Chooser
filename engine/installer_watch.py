"""Watching a started installer until it and every program it started have exited.

Installers often start a child and exit at once (NSIS uninstallers copy themselves to %TEMP%;
InstallShield and Ghost Installer bootstrappers hand off), so the installer's own exit isn't the end.
The watch follows the whole process tree:

- An unelevated program started by WindowsElevator is in its own job object from its first
  instruction (`process.job`); the job counts every process in it.
- Every program, elevated or not, is also followed through Windows' process list: any process whose
  parent is a watched process (and that started after it) is watched too, with a SYNCHRONIZE handle,
  which Windows allows across integrity levels. That catches an installer relaunching itself
  elevated, which leaves the job.

`tree_observed()` says whether the whole tree could be followed. When it can't (the process list
couldn't be read, or a child couldn't be opened), callers must not assume nothing is still running.
"""

import ctypes
import sys
import time
from contextlib import suppress
from ctypes import wintypes
from typing import Protocol

from .elevation import RunningProcess


class ProcessWatch(Protocol):
    def poll(self, timeout: float) -> bool:
        """True once the installer and every program it started have exited; else False after `timeout`."""

    def exit_code(self) -> int | None:
        """The installer's own exit code, None while it runs."""

    def tree_observed(self) -> bool:
        """True if every program the installer started could be followed (meaningful once `poll` is True)."""

    def terminate(self) -> None:
        """Stop the installer and what it started, as far as Windows allows."""

    def close(self) -> None:
        """Release the handles. Safe to call more than once."""


_TH32CS_SNAPPROCESS = 0x2
_SYNCHRONIZE = 0x00100000
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_INVALID_PARAMETER = 87  # OpenProcess on a process that has already gone


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]


def process_parents() -> list[tuple[int, int]]:
    """(pid, parent pid) for every running process. Raises OSError if the list can't be read."""
    if sys.platform != "win32":
        raise OSError("No process list on this platform")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        pairs = []
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            pairs.append((entry.th32ProcessID, entry.th32ParentProcessID))
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return pairs
    finally:
        kernel32.CloseHandle(snapshot)


class ProcessGone(Exception):
    """The process has already exited and gone."""


class _WindowsProcesses:
    """What the watch needs from Windows. Tests pass a fake with the same methods."""

    def processes(self) -> list[tuple[int, int]]:
        return process_parents()

    def open(self, pid: int):
        """A SYNCHRONIZE handle to a running process. Raises ProcessGone, or OSError if it can't be opened."""
        import pywintypes
        import win32api
        try:
            return win32api.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        except pywintypes.error as e:
            if e.winerror == _ERROR_INVALID_PARAMETER:
                raise ProcessGone() from e
            raise OSError(e.winerror, e.strerror) from e

    def hold(self, process: RunningProcess):
        """A handle of our own to the started program, kept while its own may be closed; None if refused."""
        import win32api
        import win32con
        try:
            current = win32api.GetCurrentProcess()
            return win32api.DuplicateHandle(current, process.handle, current, 0, False,
                                            win32con.DUPLICATE_SAME_ACCESS)
        except Exception:
            return None

    def created(self, handle) -> float | None:
        try:
            import win32process
            return win32process.GetProcessTimes(handle)["CreationTime"].timestamp()
        except Exception:
            return None

    def running(self, handle) -> bool:
        import win32event
        return win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_TIMEOUT

    def kill(self, pid: int) -> None:
        import win32api
        try:
            handle = win32api.OpenProcess(_PROCESS_TERMINATE, False, pid)
        except Exception:
            return  # gone, or elevated: usually refused
        try:
            with suppress(Exception):
                win32api.TerminateProcess(handle, 1)
        finally:
            handle.Close()

    def close(self, handle) -> None:
        with suppress(Exception):
            handle.Close()

    def job_running(self, job) -> bool:
        import win32job
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectBasicAccountingInformation)
        return info["ActiveProcesses"] > 0

    def kill_job(self, job) -> None:
        import win32job
        with suppress(Exception):
            win32job.TerminateJobObject(job, 1)


class _TreeWatch:
    """Windows reuses a process id once every handle to its process is closed. The watch holds a handle
    to each process it follows, the started program included (its own handle is closed by `wait`), so
    a parent id in the process list always means the process the watch knows. If the started program
    can't be held, it stops counting as a parent once it has exited."""

    def __init__(self, process: RunningProcess, system=None):
        self._system = system if system is not None else _WindowsProcesses()
        self._process = process
        self._job = getattr(process, "job", None)  # owned by the process; closed by process.close()
        self._root = self._system.hold(process)
        self._children: dict[int, object] = {}  # pid -> SYNCHRONIZE handle
        created = self._system.created(self._root if self._root is not None else process.handle)
        self._known: dict[int, float] = {process.pid: created or 0.0}
        self._followed = True
        self._sweep()

    def _parents(self) -> set[int]:
        """Watched ids that still name the watched process (see the class notes)."""
        parents = set(self._children)
        if self._root is not None or self._process.wait(0) is None:
            parents.add(self._process.pid)
        else:
            # A child it started just before exiting can't be told from a stranger's on its reused id,
            # so the tree can't be said to have been followed.
            self._followed = False
        return parents

    def _sweep(self) -> None:
        try:
            pairs = self._system.processes()
        except OSError:
            self._followed = False
            return
        parents = self._parents()
        grew = True
        while grew:
            grew = False
            for pid, parent in pairs:
                if pid in self._known or parent not in parents:
                    continue
                try:
                    handle = self._system.open(pid)
                except ProcessGone:
                    continue
                except OSError:
                    self._followed = False  # running, but can't be watched
                    continue
                created = self._system.created(handle)
                if created is not None and created < self._known[parent]:
                    self._system.close(handle)  # an older process whose parent id was reused: not ours
                    continue
                self._known[pid] = created or 0.0
                self._children[pid] = handle
                parents.add(pid)
                grew = True

    def _children_running(self) -> bool:
        return any(self._system.running(h) for h in self._children.values())

    def _job_running(self) -> bool:
        return self._job is not None and self._system.job_running(self._job)

    def poll(self, timeout: float) -> bool:
        main_done = self._process.wait(timeout) is not None
        self._sweep()
        if not main_done:
            return False
        if self._job_running() or self._children_running():
            time.sleep(timeout)
            return False
        self._sweep()  # a child started just before its parent exited
        return not self._children_running()

    def exit_code(self) -> int | None:
        return self._process.wait(0)

    def tree_observed(self) -> bool:
        return self._followed

    def terminate(self) -> None:
        if self._job is not None:
            self._system.kill_job(self._job)
        # Only processes still running whose handle is held: Windows never reuses a pid while a handle
        # to that process is open, so no other program can be hit.
        running = [pid for pid, h in self._children.items() if self._system.running(h)]
        if self._process.wait(0) is None:
            running.append(self._process.pid)
        for pid in running:
            self._system.kill(pid)

    def close(self) -> None:
        for handle in self._children.values():
            self._system.close(handle)
        self._children = {}
        if self._root is not None:
            self._system.close(self._root)
            self._root = None
        self._process.close()


def watch_process(process: RunningProcess, elevated: bool) -> ProcessWatch:
    """The default watch: its job (when WindowsElevator started it unelevated) plus its descendants."""
    return _TreeWatch(process)
