"""Closing the traced games for --trace-stop --close-games: ask a process to close, or end it.

The recorder decides which processes (only those its live tree attributes to a session); this module only acts on
one process, named by pid and creation time. Both actions hold the process open while they check that time, so a
pid Windows has given to another program is never touched.

ctypes is used inside the functions, so this module imports cleanly anywhere.
"""
from typing import Protocol

PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
WM_CLOSE = 0x0010
END_EXIT_CODE = 1


class GameCloser(Protocol):
    def ask_close(self, pid: int, start: int) -> bool:
        """Ask the process to close, as closing its windows would; True when it had a window to ask."""

    def end(self, pid: int, start: int) -> bool:
        """End the process at once (unsaved work is lost); True when it was ended."""


class WindowsCloser:
    def ask_close(self, pid: int, start: int) -> bool:
        """Post WM_CLOSE to each visible top-level window of the process, as the window's close button does. The
        program may save, ask a question or ignore it. Hidden windows are left alone: programs keep internal ones
        that were never meant to be closed. False when the process has no visible window (or is another one)."""
        import ctypes
        handle = _open(PROCESS_QUERY_LIMITED_INFORMATION, pid)
        if not handle:
            return False
        try:
            if _creation_time(handle) != start:
                return False
            user32 = ctypes.windll.user32
            user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            posted = False
            for hwnd in _visible_windows(pid):
                posted = bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)) or posted
            return posted
        finally:
            _close(handle)

    def end(self, pid: int, start: int) -> bool:
        """TerminateProcess, only on the process with this pid that was created at `start`."""
        import ctypes
        handle = _open(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, pid)
        if not handle:
            return False
        try:
            if _creation_time(handle) != start:
                return False
            kernel32 = ctypes.windll.kernel32
            kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            return bool(kernel32.TerminateProcess(handle, END_EXIT_CODE))
        finally:
            _close(handle)


def _open(access: int, pid: int):
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    return kernel32.OpenProcess(access, False, pid)


def _close(handle) -> None:
    import ctypes
    ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.CloseHandle(handle)


def _creation_time(handle) -> int | None:
    """The process's creation FILETIME, or None when it can't be read or the process has already exited."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    kernel32.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    code = wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
        return None
    created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
    if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel),
                                    ctypes.byref(user)):
        return None
    return (created.dwHighDateTime << 32) | created.dwLowDateTime or None


def _visible_windows(pid: int) -> list[int]:
    """The visible top-level windows that belong to process `pid`."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows.argtypes = [enum_proc, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    found: list[int] = []

    def each(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(enum_proc(each), None)
    return found
