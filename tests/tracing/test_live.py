import os
import sys

import pytest

from tracing.live import LiveProc, LiveTree, process_start_time, windows_snapshot

GAME_DIR = r"C:\Games\Foo"


def tree(root_start=100, game_dir=GAME_DIR):
    t = LiveTree()
    t.add_session("s1", 10, root_start, game_dir, 100)
    return t


def test_root_and_descendants_running():
    t = tree()
    t.update([LiveProc(10, 1, 100, r"C:\Games\Foo\a.exe"), LiveProc(11, 10, 110, None), LiveProc(12, 11, 120, None)])
    assert t.running() == {"s1"}
    t.update([LiveProc(12, 11, 120, None)])
    assert t.running() == {"s1"}
    t.update([])
    assert t.running() == set()


def test_child_of_dead_member_attaches():
    t = tree()
    t.update([LiveProc(10, 1, 100, None), LiveProc(11, 10, 110, None)])
    t.update([LiveProc(11, 10, 110, None), LiveProc(12, 11, 120, None)])
    t.update([LiveProc(12, 11, 120, None)])
    assert t.running() == {"s1"}
    # a child of the dead root, appearing later, still attaches
    t2 = tree()
    t2.update([LiveProc(10, 1, 100, None)])
    t2.update([LiveProc(20, 10, 200, None)])
    assert t2.running() == {"s1"}


def test_pid_reuse_not_counted():
    t = tree()
    t.update([LiveProc(10, 1, 999, None)])
    assert t.running() == set()
    t.update([LiveProc(10, 1, 100, None)])
    assert t.running() == {"s1"}
    t.update([LiveProc(10, 1, 999, None), LiveProc(30, 10, 1000, None)])
    assert t.running() == set()


def test_start_none_ignored():
    t = tree()
    t.update([LiveProc(10, 1, None, None)])
    assert t.running() == set()


def test_orphan_under_game_folder_counts():
    t = tree()
    t.update([LiveProc(10, 1, 100, None)])
    # root exits, stub (pid 50) never seen and gone, game orphaned
    t.update([LiveProc(60, 50, 300, r"c:\games\foo\sub\..\game.exe")])
    assert t.running() == {"s1"}
    out = tree()
    out.update([LiveProc(10, 1, 100, None)])
    out.update([LiveProc(60, 50, 300, r"C:\Games\Other\game.exe")])
    assert out.running() == set()
    early = tree()
    early.update([LiveProc(60, 50, 50, r"C:\Games\Foo\game.exe")])
    assert early.running() == set()


@pytest.mark.parametrize("parent_pid", [10, 70])  # the root's pid, given to another program; a non-member's pid
@pytest.mark.parametrize("folder, running", [(r"C:\Games\Foo", {"s1"}), (r"C:\Games\Other", set())])
def test_child_of_a_reused_pid_counts_only_under_game_folder(parent_pid, folder, running):
    t = tree()
    t.update([LiveProc(10, 1, 100, None)])
    t.update([LiveProc(parent_pid, 1, 500, r"C:\Other\x.exe"), LiveProc(61, parent_pid, 300, folder + r"\game.exe")])
    assert t.running() == running


def test_ended_session_not_running():
    t = tree()
    t.add_session("never", 77, None, None, 100)
    t.update([LiveProc(10, 1, 100, None)])
    t.update([])
    assert t.running() == set()
    t.add_session("s2", 5, None, None, 0)
    t.update([LiveProc(5, 1, 7, None)])
    assert t.running() == {"s2"}


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_snapshot_sees_this_process():
    mine = [p for p in windows_snapshot() if p.pid == os.getpid()]
    assert len(mine) == 1
    p = mine[0]
    assert p.ppid == os.getppid()
    assert p.start
    assert p.image_path and p.image_path.lower().endswith("python.exe")
    assert process_start_time(os.getpid()) == p.start


def test_a_process_list_windows_cannot_read_raises_instead_of_saying_nothing_runs(monkeypatch):
    def unreadable():
        raise OSError("no process list")

    monkeypatch.setattr("engine.installer_watch.process_parents", unreadable)
    with pytest.raises(OSError):
        windows_snapshot()  # an empty list would mark every traced game as exited


# ---- a launch that gave no process ID: the root is found by exe ------------------------------------------

from tracing.attribution import ROOT_SLACK  # noqa: E402

EXE = GAME_DIR + r"\foo.exe"


def test_root_found_by_exe_when_the_pid_is_unknown():
    t = LiveTree()
    t.add_session("s1-0", 0, None, None, 1000, exe=EXE, launch_after=1100)  # no folder rule: only the root rule
    t.update([LiveProc(0, 0, 900, None),  # the idle process is never the root
              LiveProc(30, 4, 1050, r"c:/games/foo/FOO.EXE"), LiveProc(31, 30, 1060, None)])
    assert t.running() == {"s1-0"}
    t.update([LiveProc(31, 30, 1060, None)])
    assert t.running() == {"s1-0"}  # the child keeps it running
    t.update([LiveProc(40, 4, 1070, EXE)])  # once found, the root is fixed: a second copy isn't it
    assert t.running() == set()


def test_same_exe_outside_the_window_is_not_the_root():
    t = LiveTree()
    t.add_session("s1-0", 0, None, None, 10 * ROOT_SLACK, exe=EXE, launch_after=11 * ROOT_SLACK)
    t.update([LiveProc(50, 4, 9 * ROOT_SLACK - 1, EXE), LiveProc(51, 4, 12 * ROOT_SLACK + 1, EXE)])
    assert t.running() == set()
    t.update([LiveProc(52, 4, 12 * ROOT_SLACK, EXE)])  # at the end of the window: a slow administrator prompt
    assert t.running() == {"s1-0"}


def test_a_session_found_by_exe_never_takes_another_sessions_root():
    s = 10_000_000  # one second in FILETIME ticks
    t = LiveTree()
    # B launched first through a slow administrator prompt (no pid); A launched at 12 s with its pid known
    t.add_session("b", 0, None, None, 11 * s, exe=EXE, launch_after=15 * s)
    t.add_session("a", 500, 12 * s, None, 12 * s)
    t.update([LiveProc(500, 4, 12 * s, EXE)])  # only A's root so far: B must not take it
    assert t.running() == {"a"}
    t.update([LiveProc(500, 4, 12 * s, EXE), LiveProc(600, 4, 15 * s, EXE)])
    assert t.running() == {"a", "b"}
    t.update([LiveProc(600, 4, 15 * s, EXE)])
    assert t.running() == {"b"}
    t.update([LiveProc(500, 4, 12 * s, EXE)])
    assert t.running() == {"a"}


def test_running_processes_lists_the_live_members_of_each_session():
    t = tree()
    t.add_session("s2", 50, 500, None, 500)
    root = LiveProc(10, 1, 100, r"C:\Games\Foo\a.exe")
    child = LiveProc(11, 10, 110, r"C:\Windows\helper.exe")
    other = LiveProc(99, 1, 50, r"C:\Windows\notepad.exe")
    t.update([root, child, other])
    assert t.running_processes() == {"s1": [root, child]}
    # the root has gone: the child it left behind is still the session's
    t.update([child, other, LiveProc(50, 1, 500, None)])
    assert t.running_processes() == {"s1": [child], "s2": [LiveProc(50, 1, 500, None)]}
    t.update([other])
    assert t.running_processes() == {}


S = 10_000_000  # ticks per second


def startless(before=100 * S, after=101 * S):
    t = LiveTree()
    t.add_session("s1", 10, None, GAME_DIR, before, None, after)  # the launch gave a pid but no start time
    return t


def test_a_root_with_no_start_time_is_not_a_later_process_with_that_pid():
    t = startless()
    t.update([LiveProc(10, 1, 200 * S, r"C:\Other\x.exe"), LiveProc(11, 10, 201 * S, None)])
    assert t.running() == set() and t.running_processes() == {}


def test_a_root_with_no_start_time_is_not_a_process_from_before_the_launch():
    t = startless()
    t.update([LiveProc(10, 1, 50 * S, r"C:\Other\x.exe")])
    assert t.running() == set()


def test_a_root_with_no_start_time_is_found_within_the_launch_window_and_then_kept():
    for start in (98 * S, 100 * S + 5, 103 * S):  # 2 s before the call to 2 s after it returned (in_launch_window)
        t = startless()
        root = LiveProc(10, 1, start, r"C:\Games\Foo\a.exe")
        t.update([root, LiveProc(11, 10, 110 * S, None)])
        assert t.running() == {"s1"} and len(t.running_processes()["s1"]) == 2
        # once found, that process is the root: the pid given to another program later is not
        t.update([LiveProc(10, 1, 400 * S, r"C:\Other\x.exe")])
        assert t.running() == set()
