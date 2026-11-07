from tracing.attribution import ROOT_SLACK, find_root, parent_of, session_tree
from tracing.clock import TICKS_PER_SECOND
from tracing.model import ProcInfo, SessionInfo

S = TICKS_PER_SECOND


def p(pid, ppid, start, end=None, image="x.exe"):
    return ProcInfo(pid, ppid, start, end, image, image, False, True)


def session(root_pid=100, root_start=None, before=10 * S, after=11 * S, marks=()):
    return SessionInfo("s1", root_pid, root_start, before, after,
                       {"title": "T", "catalog_id": "c", "library_id": "l", "launch_path": "a/b.exe",
                        "exe": "g/b.exe", "game_dir": "g"},
                       {"windows": "10", "local_appdata": "L", "user_profile": "U", "app_exe": "a.exe",
                        "app_exe_mtime": 1, "compiled": False}, marks)


def test_tree_follows_children_and_grandchildren():
    root = p(100, 1, 10 * S)
    procs = [root, p(200, 100, 11 * S), p(300, 200, 12 * S), p(400, 1, 11 * S)]
    assert set(session_tree(procs, root)) == {(100, 10 * S), (200, 11 * S), (300, 12 * S)}


def test_pid_reuse_picks_the_live_parent():
    root = p(100, 1, 9 * S)
    old = p(200, 1, 0, 5 * S)
    new = p(200, 100, 10 * S)
    late = p(300, 200, 12 * S)
    early = p(301, 200, 3 * S)
    tree = session_tree([root, old, new, late, early], root)
    assert late.key in tree and new.key in tree
    assert early.key not in tree and old.key not in tree


def test_child_of_a_dead_parent_still_attaches():
    root = p(100, 1, 0, 2 * S)
    child = p(200, 100, 1 * S)
    assert child.key in session_tree([root, child], root)


def test_find_root_prefers_exact_start_then_window():
    exact = p(100, 1, 5 * S)
    closer = p(100, 1, 10 * S)
    assert find_root([closer, exact], session(root_start=5 * S)) is exact
    early = p(100, 1, 0)
    inside = p(100, 1, 10 * S + S // 2)
    assert find_root([early, inside], session()) is inside
    assert ROOT_SLACK == 2 * S


def test_find_root_falls_back_to_the_window_when_the_start_is_off():
    inside = p(100, 1, 10 * S + S // 2)
    assert find_root([inside], session(root_start=10 * S + S // 2 + 3)) is inside
    assert find_root([p(100, 1, 0)], session(root_start=3)) is None  # off and outside the window


def test_find_root_window_prefers_the_start_closest_to_root_start():
    near_launch = p(100, 1, 10 * S)
    near_start = p(100, 1, 11 * S + S // 2)
    procs = [near_launch, near_start]
    assert find_root(procs, session(root_start=11 * S + S // 2 + 5)) is near_start
    assert find_root(procs, session()) is near_launch


def test_find_root_none_when_absent():
    assert find_root([p(5, 1, 10 * S)], session()) is None


def test_session_info_json_round_trip():
    s = session(marks=(1, 2, 3))
    back = SessionInfo.from_json(s.to_json())
    assert back == s and isinstance(back.marks, tuple)
