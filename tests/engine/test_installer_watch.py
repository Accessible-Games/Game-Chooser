"""The installer watch against a fake Windows process list: following the tree, and never adopting
programs that only share a process id with an installer that has gone."""

import sys
import time

import pytest

from engine import installer_watch
from engine.installer_watch import ProcessGone, _TreeWatch


class FakeSystem:
    """Windows' process list in memory. Like Windows, a process id isn't reused while any handle to the
    process it belonged to is open (`start` refuses that)."""

    def __init__(self):
        self.procs: dict[int, dict] = {}  # pid -> {"parent", "created", "alive"}
        self.open_handles: list["FakeHandle"] = []
        self.killed: list[int] = []
        self.can_hold = True

    def start(self, pid: int, parent: int, created: float, alive: bool = True) -> None:
        old = self.procs.get(pid)
        if old is not None:
            assert not old["alive"], "a running process's id can't be reused"
            assert not self.held(pid), "Windows doesn't reuse an id while a handle to its process is open"
        self.procs[pid] = {"parent": parent, "created": created, "alive": alive, "generation": object()}

    def exit(self, pid: int) -> None:
        self.procs[pid]["alive"] = False

    def held(self, pid: int) -> bool:
        return any(h.pid == pid and not h.closed for h in self.open_handles)

    # -- the interface _TreeWatch uses --
    def processes(self):
        return [(pid, p["parent"]) for pid, p in self.procs.items() if p["alive"]]

    def open(self, pid: int):
        if pid not in self.procs or not self.procs[pid]["alive"]:
            raise ProcessGone()
        handle = FakeHandle(pid, self.procs[pid])
        self.open_handles.append(handle)
        return handle

    def hold(self, process):
        if not self.can_hold:
            return None
        return self.open(process.pid) if self.procs[process.pid]["alive"] else self._handle_of_exited(process.pid)

    def _handle_of_exited(self, pid):
        handle = FakeHandle(pid, self.procs[pid])
        self.open_handles.append(handle)
        return handle

    def created(self, handle):
        return handle.proc["created"]

    def running(self, handle) -> bool:
        return handle.proc["alive"]

    def kill(self, pid: int) -> None:
        self.killed.append(pid)
        if pid in self.procs:
            self.procs[pid]["alive"] = False

    def close(self, handle) -> None:
        handle.closed = True

    def job_running(self, job) -> bool:
        return False

    def kill_job(self, job) -> None:
        pass


class FakeHandle:
    def __init__(self, pid, proc):
        self.pid = pid
        self.proc = proc  # the process this handle was opened on, not whatever has its id later
        self.closed = False


class Root:
    """The started installer, as the elevator returns it: its own handle closes once it has exited."""

    def __init__(self, system: FakeSystem, pid: int):
        self.system = system
        self.pid = pid
        self.handle = system.open(pid)
        self.proc = system.procs[pid]  # this program, not whatever has its id later
        self.closed = False

    def wait(self, timeout=None):
        if self.proc["alive"]:
            return None
        if not self.handle.closed:
            self.handle.closed = True  # as _ShellProcess.wait does
        return 0

    def close(self):
        self.handle.closed = True
        self.closed = True


def started_installer(t0=1000.0):
    system = FakeSystem()
    system.start(100, 4, t0)
    return system, Root(system, 100)


def test_follows_a_child_until_it_ends_and_stops_it_on_terminate():
    system, root = started_installer()
    watch = _TreeWatch(root, system)
    system.start(101, 100, 1001.0)
    assert not watch.poll(0.01)
    system.exit(100)
    assert not watch.poll(0.01)  # the child it started still runs
    watch.terminate()
    assert 101 in system.killed
    assert watch.poll(0.01) and watch.tree_observed()
    watch.close()


def test_the_roots_id_is_held_for_the_watchs_lifetime():
    system, root = started_installer()
    watch = _TreeWatch(root, system)
    system.exit(100)
    assert watch.poll(0.01)
    assert root.handle.closed and system.held(100)  # its own handle went with wait(); the watch's didn't
    watch.close()
    assert not system.held(100)


def test_a_new_program_on_the_roots_id_isnt_adopted_when_the_root_couldnt_be_held():
    system, root = started_installer()
    system.can_hold = False
    watch = _TreeWatch(root, system)
    system.exit(100)
    assert watch.poll(0.01)
    root.handle.closed = True
    system.start(100, 7, 2000.0)  # the player's program now has the installer's old id ...
    system.start(200, 100, 2001.0)  # ... and starts a long-lived program of its own
    assert watch.poll(0.01)  # not the installer's: the tree is still over
    assert not watch.tree_observed()  # but a child it started just before exiting could have been missed
    watch.terminate()
    assert system.killed == []
    watch.close()


def test_a_child_whose_parent_id_was_reused_from_an_older_process_isnt_adopted():
    system, root = started_installer(t0=1000.0)
    system.start(300, 100, 900.0)  # older than the installer: its parent was an earlier holder of id 100
    watch = _TreeWatch(root, system)
    system.exit(100)
    assert watch.poll(0.01)
    watch.terminate()
    assert system.killed == []
    watch.close()


def test_an_unreadable_process_list_means_the_tree_wasnt_observed():
    system, root = started_installer()

    def unreadable():
        raise OSError("no snapshot")
    system.processes = unreadable
    watch = _TreeWatch(root, system)
    system.exit(100)
    assert watch.poll(0.01) and not watch.tree_observed()
    watch.close()


@pytest.mark.windows
def test_real_watch_keeps_the_roots_id_after_it_exits(tmp_path):
    import pywintypes
    import win32api
    import win32event
    from engine.elevation import WindowsElevator
    process = WindowsElevator().start(sys.executable, '-c "pass"', False)
    pid = process.pid
    watch = installer_watch.watch_process(process, False)
    try:
        started = time.monotonic()
        while not watch.poll(0.1):
            assert time.monotonic() - started < 20
        # The elevator's own handle is closed now; the watch's handle keeps the process object (and its id).
        handle = win32api.OpenProcess(0x00100000, False, pid)
        try:
            assert win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_OBJECT_0
        finally:
            handle.Close()
    except pywintypes.error:
        pytest.fail("the installer's process was released while the watch was open: its id could be reused")
    finally:
        watch.close()
