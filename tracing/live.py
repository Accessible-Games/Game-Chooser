"""Live process tree: which traced sessions still have a running process.

Used by the recorder (stop only when every session's processes have exited) and by the mark key (which sessions
are running). Processes are identified by (pid, start) so a reused pid is never mistaken for a member.

A session whose launch gave no process ID (root_pid 0) finds its root by executable: the first snapshot holding a
process whose image is the game's exe and whose start lies from ROOT_SLACK before `started_at` to ROOT_SLACK after
`launch_after` (`started_at` when not given), and that no other session holds, picks one (the earliest that started
at or after `started_at`; failing that, the closest to it), and from then on that process is the root, as if its
pid had been known.

A session whose launch gave a process ID but no start time (it couldn't be read) takes the process with that pid
only if it started in the same window, as `attribution.find_root` does, and from then on only that process: a
later program that Windows gives the pid to is never the root.
"""
import os
import sys
from dataclasses import dataclass

from tracing.attribution import ROOT_SLACK, UNKNOWN_PID, exe_key, find_root_by_exe
from tracing.closing import WindowsProcesses


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
        self.game_dir = exe_key(game_dir)
        self.started_at = started_at
        self.members: set[tuple[int, int]] = set()
        self.live: list[LiveProc] = []  # the members in the last snapshot
        self.alive = False
        self.seen = False

    def _inside_game_dir(self, image_path):
        if not self.game_dir or not image_path:
            return False
        try:
            return os.path.commonpath([self.game_dir, exe_key(image_path)]) == self.game_dir
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
        elif self.root_start is None:
            low, high = self.started_at - ROOT_SLACK, self.launch_after + ROOT_SLACK
            for p in procs:
                if p.pid == self.root_pid and low <= p.start <= high:
                    self.root_start = p.start
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
        self.live = [p for p in procs if (p.pid, p.start) in self.members]
        self.alive = bool(self.live)
        if self.members:
            self.seen = True

    def _qualifies(self, p, member_pids, alive_start):
        if self.root_pid != UNKNOWN_PID and p.pid == self.root_pid:
            return self.root_start is not None and p.start == self.root_start
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

    def running_processes(self):
        """Each running session's members that were in the last snapshot, in snapshot order."""
        return {sid: list(s.live) for sid, s in self._sessions.items() if s.live}

    def forget_ended(self):
        for sid in [sid for sid, s in self._sessions.items() if s.seen and not s.alive]:
            del self._sessions[sid]


def windows_snapshot() -> list[LiveProc]:
    """Every running process. Raises OSError when Windows' process list can't be read: an empty list would say
    that every traced game has exited."""
    from engine.installer_watch import process_parents
    return [LiveProc(pid, ppid, *_query(pid)) for pid, ppid in process_parents()]


def _query(pid: int):
    """(creation FILETIME, image path) of a process; None for what can't be read."""
    import ctypes
    from ctypes import wintypes

    processes = WindowsProcesses()
    handle = processes.open(pid, False)
    if not handle:
        return None, None
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        image = buf.value if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else None
        return processes.start_time(handle), image
    finally:
        processes.close(handle)


def process_start_time(pid: int) -> int | None:
    if sys.platform != "win32":
        return None
    return _query(pid)[0]
