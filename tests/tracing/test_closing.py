"""Closer over WindowsProcesses on real child processes of the test (plain Python programs; no admin rights, no game).

The children's windows are never seen: one is hidden, the other is a zero-size, off-screen tool window that can't
be activated, so no test moves focus or shows anything.
"""
import os
import subprocess
import sys

import pytest

from tracing import closing
from tracing.closing import Closer, Identity, Refused, WindowsProcesses, process_identity
from tracing.live import process_start_time

pytestmark = [pytest.mark.windows, pytest.mark.skipif(sys.platform != "win32", reason="Windows only")]

SLEEPER = "import time; print('ready', flush=True); time.sleep(120)"

# Makes one top-level window (visible or hidden by argv[1]) and exits with code 7 once that window is closed.
WINDOW = r"""
import ctypes, sys, time
from ctypes import wintypes
user32 = ctypes.windll.user32
user32.CreateWindowExW.restype = ctypes.c_void_p
user32.CreateWindowExW.argtypes = ([wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
                                   + [ctypes.c_int] * 4 + [ctypes.c_void_p] * 4)
user32.IsWindow.argtypes = [ctypes.c_void_p]
WS_POPUP, WS_VISIBLE, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000000, 0x10000000, 0x80, 0x08000000
style = WS_POPUP | (WS_VISIBLE if sys.argv[1] == "visible" else 0)
hwnd = user32.CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, "STATIC", "trace-stop test", style,
                              -32000, -32000, 0, 0, None, None, None, None)
if not hwnd:
    sys.exit(3)
print("ready", flush=True)
msg = wintypes.MSG()
deadline = time.monotonic() + 120
while user32.IsWindow(hwnd) and time.monotonic() < deadline:
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        user32.DispatchMessageW(ctypes.byref(msg))
    time.sleep(0.02)
sys.exit(7 if not user32.IsWindow(hwnd) else 4)
"""


def closer():
    """The closer as the recorder builds it, with this test standing in for Game Chooser."""
    return Closer(process_identity(os.getpid()), WindowsProcesses())


@pytest.fixture
def child():
    started = []

    def start(code, *args):
        # the real interpreter, not the venv's launcher, so the pid is the process that owns the window
        proc = subprocess.Popen([sys._base_executable, "-I", "-S", "-c", code, *args], stdout=subprocess.PIPE,
                                text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        started.append(proc)
        assert proc.stdout.readline().strip() == "ready"
        start_time = process_start_time(proc.pid)
        assert start_time
        return proc, start_time

    yield start
    for proc in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait(10)
        proc.stdout.close()


def test_end_ends_the_process(child):
    proc, start = child(SLEEPER)
    assert closer().end(proc.pid, start) is True
    assert proc.wait(10) == 1


def test_end_refuses_a_process_with_another_start_time(child):
    proc, start = child(SLEEPER)
    assert closer().end(proc.pid, start + 1) is False
    assert proc.poll() is None


def test_end_of_a_process_that_has_gone_is_false(child):
    proc, start = child(SLEEPER)
    proc.kill()
    proc.wait(10)
    assert closer().end(proc.pid, start) is False


def test_ask_close_closes_a_visible_window_and_the_program_ends_by_itself(child):
    proc, start = child(WINDOW, "visible")
    assert closer().ask_close(proc.pid, start) is True
    assert proc.wait(10) == 7


def test_ask_close_refuses_a_process_with_another_start_time(child):
    proc, start = child(WINDOW, "visible")
    assert closer().ask_close(proc.pid, start + 1) is False
    with pytest.raises(subprocess.TimeoutExpired):
        proc.wait(0.5)


def test_ask_close_leaves_hidden_windows_alone(child):
    proc, start = child(WINDOW, "hidden")
    assert closer().ask_close(proc.pid, start) is False
    with pytest.raises(subprocess.TimeoutExpired):
        proc.wait(0.5)


def test_ask_close_of_a_program_without_a_window_is_false(child):
    proc, start = child(SLEEPER)
    assert closer().ask_close(proc.pid, start) is False
    assert proc.poll() is None


def test_process_identity_reads_the_user_and_the_rights_of_a_process(child):
    me = process_identity(os.getpid())
    assert me.user.startswith("S-1-5-") and me.integrity >= 0x1000
    proc, start = child(SLEEPER)
    assert process_identity(proc.pid) == me  # a child runs as its parent does
    proc.kill()
    proc.wait(10)
    assert process_identity(proc.pid) is None


@pytest.mark.parametrize("limit, reason", [
    (lambda me: Identity("S-1-5-21-0-0-0-99999", me.integrity), closing.REFUSED_USER),
    (lambda me: Identity(me.user, me.integrity - 1), closing.REFUSED_RIGHTS),
    (lambda me: None, closing.REFUSED_NO_LIMIT),
])
def test_a_process_outside_the_limit_is_neither_asked_nor_ended(child, limit, reason):
    proc, start = child(WINDOW, "visible")
    refusing = Closer(limit(process_identity(os.getpid())), WindowsProcesses())
    for action in (refusing.ask_close, refusing.end):
        with pytest.raises(Refused) as refused:
            action(proc.pid, start)
        assert refused.value.reason == reason
    with pytest.raises(subprocess.TimeoutExpired):
        proc.wait(0.5)
