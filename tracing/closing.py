"""Closing the traced games for --trace-stop --close-games: ask a process to close, or end it.

The recorder decides which processes (those its live tree attributes to a session). That tree is seeded from
session files any program of the user can write, and the recorder runs elevated, so this module is where the limit
is enforced: `Closer` holds the process open, checks its start time (a pid Windows has given to another program is
never touched), then refuses it unless it runs as Game Chooser's user with no more rights than Game Chooser. So a
stop can close nothing the user's own unelevated programs couldn't close themselves.

ctypes is used inside the functions, so this module imports cleanly anywhere.
"""
from dataclasses import dataclass
from typing import Protocol

PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
WM_CLOSE = 0x0010
END_EXIT_CODE = 1
TOKEN_QUERY = 0x0008
TOKEN_USER_CLASS = 1  # TOKEN_INFORMATION_CLASS: TokenUser
TOKEN_INTEGRITY_CLASS = 25  # TokenIntegrityLevel

# Why a process a stop was asked to close is still running (stop.json's `not_closed`).
REFUSED_USER = "it runs as another user"
REFUSED_RIGHTS = ("it runs with more rights than Game Chooser (a game started through the administrator prompt "
                  "is not closed)")
REFUSED_UNKNOWN = "who it runs as couldn't be read"
REFUSED_NO_LIMIT = "who Game Chooser runs as couldn't be read, so nothing was closed"
NOT_CLOSED_PROTECTED = "a stop never closes it"
NOT_CLOSED_FAILED = "it could not be ended"
NOT_CLOSED_PROCMON = "Process Monitor stopped first, so nothing was ended"


@dataclass(frozen=True)
class Identity:
    """Who a process runs as: its token's user (the SID as text) and integrity level (the level's number:
    0x1000 low, 0x2000 medium, 0x3000 high after the administrator prompt, 0x4000 system)."""
    user: str
    integrity: int


class Refused(Exception):
    """The process is not one a stop may close; `reason` says why in plain words."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def refusal(identity: Identity | None, limit: Identity | None) -> str | None:
    """Why a process running as `identity` may not be closed for an app running as `limit`; None when it may."""
    if limit is None:
        return REFUSED_NO_LIMIT
    if identity is None:
        return REFUSED_UNKNOWN
    if identity.user != limit.user:
        return REFUSED_USER
    if identity.integrity > limit.integrity:
        return REFUSED_RIGHTS
    return None


class GameCloser(Protocol):
    def ask_close(self, pid: int, start: int) -> bool:
        """Ask the process to close, as closing its windows would; True when it had a window to ask.
        Raises Refused for a process a stop may not close."""

    def end(self, pid: int, start: int) -> bool:
        """End the process at once (unsaved work is lost); True when it was ended.
        Raises Refused for a process a stop may not close."""


class ProcessAccess(Protocol):
    """The system calls the closer needs, so its rules are tested without real processes."""

    def open(self, pid: int, terminate: bool):
        """A handle on the process (with the right to end it when `terminate`), or None."""

    def creation_time(self, handle) -> int | None:
        """The creation FILETIME; None when it can't be read or the process has exited."""

    def identity(self, handle) -> Identity | None:
        """Who the process runs as; None when its token can't be read."""

    def close_windows(self, pid: int) -> bool:
        """Ask each visible top-level window of the process to close; True when there was one."""

    def terminate(self, handle) -> bool:
        """End the process."""

    def close(self, handle) -> None:
        """Give the handle back."""


class Closer:
    """Asks or ends one process, only if it is the one meant (pid and start time) and one a stop may close
    (`limit`: who Game Chooser runs as; None refuses everything). Every check is made on the handle the action
    then uses, so nothing can change in between."""

    def __init__(self, limit: Identity | None, processes: ProcessAccess):
        self.limit = limit
        self._processes = processes

    def ask_close(self, pid: int, start: int) -> bool:
        return self._on(pid, start, False, lambda handle: self._processes.close_windows(pid))

    def end(self, pid: int, start: int) -> bool:
        return self._on(pid, start, True, self._processes.terminate)

    def _on(self, pid: int, start: int, terminate: bool, action) -> bool:
        handle = self._processes.open(pid, terminate)
        if not handle:
            return False
        try:
            if self._processes.creation_time(handle) != start:
                return False
            reason = refusal(self._processes.identity(handle), self.limit)
            if reason is not None:
                raise Refused(reason)
            return bool(action(handle))
        finally:
            self._processes.close(handle)


class WindowsCloser(Closer):
    def __init__(self, limit: Identity | None):
        super().__init__(limit, WindowsProcesses())


def process_identity(pid: int) -> Identity | None:
    """Who the running process `pid` runs as; None when it has gone or can't be read."""
    processes = WindowsProcesses()
    handle = processes.open(pid, False)
    if not handle:
        return None
    try:
        return processes.identity(handle) if processes.creation_time(handle) is not None else None
    finally:
        processes.close(handle)


class WindowsProcesses:
    def open(self, pid: int, terminate: bool):
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        access = PROCESS_QUERY_LIMITED_INFORMATION | (PROCESS_TERMINATE if terminate else 0)
        return kernel32.OpenProcess(access, False, pid)

    def close(self, handle) -> None:
        import ctypes
        ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.CloseHandle(handle)

    def creation_time(self, handle) -> int | None:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return None
        return self.start_time(handle)

    def start_time(self, handle) -> int | None:
        """The creation FILETIME, of a process that has exited too; None when it can't be read."""
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel),
                                        ctypes.byref(user)):
            return None
        return (created.dwHighDateTime << 32) | created.dwLowDateTime or None

    def identity(self, handle) -> Identity | None:
        """The user SID and integrity level of the process's token (OpenProcessToken with TOKEN_QUERY)."""
        import ctypes
        from ctypes import wintypes
        advapi32 = ctypes.windll.advapi32
        advapi32.OpenProcessToken.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        token = ctypes.c_void_p()
        if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            user = _token_sid(token, TOKEN_USER_CLASS)
            label = _token_sid(token, TOKEN_INTEGRITY_CLASS)
            if user is None or label is None:
                return None
            advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
            advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
            advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
            advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
            advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
            text = ctypes.c_void_p()
            if not advapi32.ConvertSidToStringSidW(_sid_pointer(user), ctypes.byref(text)):
                return None
            try:
                sid_text = ctypes.wstring_at(text.value)
            finally:
                ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
                ctypes.windll.kernel32.LocalFree(text)
            label_sid = _sid_pointer(label)
            count = advapi32.GetSidSubAuthorityCount(label_sid).contents.value
            if not sid_text or count < 1:
                return None
            return Identity(sid_text, advapi32.GetSidSubAuthority(label_sid, count - 1).contents.value)
        finally:
            self.close(token)

    def close_windows(self, pid: int) -> bool:
        """Post WM_CLOSE to each visible top-level window of the process, as the window's close button does. The
        program may save, ask a question or ignore it. Hidden windows are left alone: programs keep internal ones
        that were never meant to be closed."""
        import ctypes
        user32 = ctypes.windll.user32
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        posted = False
        for hwnd in _visible_windows(pid):
            posted = bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)) or posted
        return posted

    def terminate(self, handle) -> bool:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        return bool(kernel32.TerminateProcess(handle, END_EXIT_CODE))


def _token_sid(token, info_class: int):
    """The buffer GetTokenInformation fills for TokenUser or TokenIntegrityLevel (each starts with a pointer to a
    SID kept inside the buffer), or None."""
    import ctypes
    from ctypes import wintypes
    advapi32 = ctypes.windll.advapi32
    advapi32.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    size = wintypes.DWORD()
    advapi32.GetTokenInformation(token, info_class, None, 0, ctypes.byref(size))
    if not size.value:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if not advapi32.GetTokenInformation(token, info_class, buffer, size, ctypes.byref(size)):
        return None
    return buffer


def _sid_pointer(buffer):
    """The SID pointer at the start of a TOKEN_USER or TOKEN_MANDATORY_LABEL buffer."""
    import ctypes
    return ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value


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
