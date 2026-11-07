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


def test_ended_session_not_running():
    t = tree()
    t.add_session("never", 77, None, None, 100)
    t.update([LiveProc(10, 1, 100, None)])
    t.update([])
    assert t.running() == set()
    t.forget_ended()
    t.update([LiveProc(10, 1, 100, None)])
    assert t.running() == set()
    t.add_session("s2", 5, None, None, 0)
    t.forget_ended()
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
