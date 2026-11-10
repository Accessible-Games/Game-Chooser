"""Live process tree: which traced sessions still have a running process.

Used by the recorder (stop only when every session's processes have exited) and by the mark key (which sessions
are running). Processes are identified by (pid, start) so a reused pid is never mistaken for a member.

A session whose launch gave no process ID (root_pid 0) finds its root by executable: the first snapshot holding a
process whose image is the game's exe and whose start lies from ROOT_SLACK before `started_at` to ROOT_SLACK after
`launch_after` (`started_at` when not given), and that no other session holds, picks one (the earliest that started
at or after `started_at`; failing that, the closest to it), and from then on that process is the root, as if its
pid had been known.
"""
import os
import sys
from dataclasses import dataclass

from tracing.attribution import UNKNOWN_PID, find_root_by_exe


@dataclass(frozen=True)
class LiveProc:
    pid: int
    ppid: int
    start: int | None
    image_path: str | None


class _Session:
    def __init__(self, root_pid, root_start, game_dir, started_at, exe=None, launch_after=None):
        self.root_pid = root_pid
        self.root_start = root_start
        self.exe = exe
        self.launch_after = started_at if launch_after is None else launch_after
        self.game_dir = os.path.normcase(os.path.normpath(game_dir)) if game_dir else None
        self.started_at = started_at
        self.members: set[tuple[int, int]] = set()
        self.alive = False
        self.seen = False

    def _inside_game_dir(self, image_path):
        if not self.game_dir or not image_path:
            return False
        image = os.path.normcase(os.path.normpath(image_path))
        try:
            return os.path.commonpath([self.game_dir, image]) == self.game_dir
        except ValueError:
            return False

    def owned(self):
        """The (pid, start) keys this session holds: its members, and its root once known."""
        keys = set(self.members)
        if self.root_pid != UNKNOWN_PID and self.root_start is not None:
            keys.add((self.root_pid, self.root_start))
        return keys

    def update(self, procs, alive_start, exclude=frozenset()):
        if self.root_pid == UNKNOWN_PID:
            root = find_root_by_exe(procs, self.exe, self.started_at, self.launch_after, exclude)
            if root is not None:
                self.root_pid, self.root_start = root.pid, root.start
        member_pids = {}
        for pid, start in self.members:
            member_pids.setdefault(pid, []).append(start)
        changed = True
        while changed:
            changed = False
            for p in procs:
                key = (p.pid, p.start)
                if key in self.members:
                    continue
                if self._qualifies(p, member_pids, alive_start):
                    self.members.add(key)
                    member_pids.setdefault(p.pid, []).append(p.start)
                    changed = True
        self.alive = any(alive_start.get(pid) == start for pid, start in self.members)
        if self.members:
            self.seen = True

    def _qualifies(self, p, member_pids, alive_start):
        if self.root_pid != UNKNOWN_PID and p.pid == self.root_pid:
            return self.root_start is None or p.start == self.root_start
        parents = member_pids.get(p.ppid, [])
        current = alive_start.get(p.ppid)
        for start in parents:
            # a live process with this pid but another start is a reused pid, not our member
            if start <= p.start and (current is None or current == start):
                return True
        # the parent is gone when its pid is free, or a known member's pid was taken by another process
        # (a live process that started after the child cannot be its real parent either)
        gone = current is None or current > p.start or (bool(parents) and current not in parents)
        if gone:
            return p.start >= self.started_at and self._inside_game_dir(p.image_path)
        return False


class LiveTree:
    def __init__(self):
        self._sessions: dict[str, _Session] = {}

    def add_session(self, session_id, root_pid, root_start, game_dir, started_at, exe=None, launch_after=None):
        """root_pid 0 means the launch gave no process ID: the root is then found by `exe` (see the module)."""
        self._sessions[session_id] = _Session(root_pid, root_start, game_dir, started_at, exe, launch_after)

    def update(self, snapshot):
        procs = [p for p in snapshot if p.start is not None]
        alive_start = {p.pid: p.start for p in procs}
        # Sessions whose root is known first, so a session still looking for its root by exe never takes a
        # process another session holds.
        sessions = sorted(self._sessions.values(), key=lambda s: s.root_pid == UNKNOWN_PID)
        for session in sessions:
            exclude = set()
            if session.root_pid == UNKNOWN_PID:
                for other in self._sessions.values():
                    if other is not session:
                        exclude |= other.owned()
            session.update(procs, alive_start, exclude)

    def running(self):
        return {sid for sid, s in self._sessions.items() if s.alive}

    def forget_ended(self):
        for sid in [sid for sid, s in self._sessions.items() if s.seen and not s.alive]:
            del self._sessions[sid]


def windows_snapshot() -> list[LiveProc]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    found = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            found.append((entry.th32ProcessID, entry.th32ParentProcessID))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    result = []
    for pid, ppid in found:
        start, image = _query(pid)
        result.append(LiveProc(pid, ppid, start, image))
    return result


def _query(pid: int):
    """(creation FILETIME, image path) of a process; None for what can't be read."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None, None
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        start = None
        if kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                    ctypes.byref(kernel), ctypes.byref(user)):
            start = (created.dwHighDateTime << 32) | created.dwLowDateTime or None
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        image = buf.value if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else None
        return start, image
    finally:
        kernel32.CloseHandle(handle)


def process_start_time(pid: int) -> int | None:
    if sys.platform != "win32":
        return None
    return _query(pid)[0]
