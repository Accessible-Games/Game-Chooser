"""Which processes belong to a play session: the launched root and every descendant."""
import os

from tracing.clock import TICKS_PER_SECOND
from tracing.model import ProcInfo, SessionInfo

ROOT_SLACK = 2 * TICKS_PER_SECOND
UNKNOWN_PID = 0  # root_pid when the launch handed back no process ID


def exe_key(path: str | None) -> str | None:
    """A path as tracing compares it (the root's exe, the game folder): normalized, case folded on Windows."""
    return os.path.normcase(os.path.normpath(path)) if path else None


def find_root_by_exe(processes, exe: str | None, before: int, after: int, exclude=frozenset()):
    """The process whose image is `exe` that started within ROOT_SLACK of the launch call (from `before - ROOT_SLACK`
    to `after + ROOT_SLACK`) and whose (pid, start) is not in `exclude` (processes another session owns).

    One that started at or after the call (`before`) wins, the earliest of those: the real root, started through
    the administrator prompt, starts after the call. Only when there is none, the closest to `before`. Works on
    ProcInfo and live processes alike."""
    want = exe_key(exe)
    if want is None:
        return None
    low, high = before - ROOT_SLACK, after + ROOT_SLACK
    candidates = [q for q in processes if q.pid != UNKNOWN_PID and q.start is not None
                  and low <= q.start <= high and (q.pid, q.start) not in exclude
                  and exe_key(q.image_path) == want]
    if not candidates:
        return None
    after_call = [q for q in candidates if q.start >= before]
    return min(after_call or candidates, key=lambda q: abs(q.start - before))


def find_root(processes: list[ProcInfo], session: SessionInfo, exclude=frozenset()) -> ProcInfo | None:
    """The launched process: the pid with exactly the start time read at launch; failing that, the process with
    that pid that started within ROOT_SLACK of the launch call, closest to that start time (or to the call).

    When the launch gave no process ID (root_pid is UNKNOWN_PID), the root is found by executable instead
    (find_root_by_exe with the session's game exe and launch window), never among the `exclude` keys."""
    if session.root_pid == UNKNOWN_PID:
        return find_root_by_exe(processes, session.game.get("exe"), session.launch_before, session.launch_after,
                                exclude)
    candidates = [q for q in processes if q.pid == session.root_pid]
    if session.root_start is not None:
        for q in candidates:
            if q.start == session.root_start:
                return q
    low = session.launch_before - ROOT_SLACK
    high = session.launch_after + ROOT_SLACK
    candidates = [q for q in candidates if low <= q.start <= high]
    if not candidates:
        return None
    target = session.root_start if session.root_start is not None else session.launch_before
    return min(candidates, key=lambda q: abs(q.start - target))


def parent_of(proc: ProcInfo, by_pid: dict[int, list[ProcInfo]]) -> ProcInfo | None:
    """The parent that was alive when proc started; the latest start wins when a pid was reused."""
    best = None
    for q in by_pid.get(proc.parent_pid, ()):
        if q.start <= proc.start and (q.end is None or q.end >= proc.start):
            if best is None or q.start > best.start:
                best = q
    return best


def session_tree(processes: list[ProcInfo], root: ProcInfo) -> dict[tuple[int, int], ProcInfo]:
    by_pid: dict[int, list[ProcInfo]] = {}
    for q in processes:
        by_pid.setdefault(q.pid, []).append(q)
    member: dict[tuple[int, int], bool] = {root.key: True}

    def belongs(q: ProcInfo) -> bool:
        chain = []
        cur: ProcInfo | None = q
        result = False
        while cur is not None:
            if cur.key in member:
                result = member[cur.key]
                break
            chain.append(cur)
            cur = parent_of(cur, by_pid)
            if cur is not None and cur.key in (c.key for c in chain):
                break  # a cycle can't reach the root
        for c in chain:
            member[c.key] = result
        return result

    return {q.key: q for q in processes if belongs(q)}
