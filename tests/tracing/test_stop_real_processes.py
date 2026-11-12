"""--trace-stop --close-games end to end on real processes: the command, a recorder (with a fake Process Monitor),
the real process snapshot and the real closer. No admin rights and no game: the "game" is a Python program of this
test with an off-screen window, which starts a helper that has none.
"""
import os
import subprocess
import sys
import threading
import time

import pytest

import tracing.cli as cli
from tests.tracing.test_closing import SLEEPER, WINDOW
from tests.tracing.test_recorder import Clock, FakeProcmon
from tracing import recorder, stop
from tracing.clock import now_filetime
from tracing.closing import WindowsCloser, process_identity
from tracing.live import process_start_time, windows_snapshot
from tracing.model import SessionInfo
from tracing.runfiles import new_run
from tracing.splitrun import SplitResult

pytestmark = [pytest.mark.windows, pytest.mark.skipif(sys.platform != "win32", reason="Windows only")]

# The game: starts a helper without a window, tells its pid, then lives until its own window is closed.
GAME = ("import subprocess, sys\n"
        f"helper = subprocess.Popen([sys.executable, '-I', '-S', '-c', {SLEEPER!r}], stdout=subprocess.DEVNULL,\n"
        "                          creationflags=subprocess.CREATE_NO_WINDOW)\n"
        "print(helper.pid, flush=True)\n" + WINDOW)


def test_close_games_closes_the_game_tree_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder, "CLOSE_WAIT_SECONDS", 1.5)
    monkeypatch.setattr(recorder, "POLL_SECONDS", 0.1)
    monkeypatch.setattr(stop, "POLL_SECONDS", 0.1)
    python = [sys._base_executable, "-I", "-S", "-c"]
    flags = subprocess.CREATE_NO_WINDOW
    bystander = subprocess.Popen(python + [SLEEPER], stdout=subprocess.PIPE, text=True, creationflags=flags)
    before = now_filetime()
    game = subprocess.Popen(python + [GAME, "visible"], stdout=subprocess.PIPE, text=True, creationflags=flags)
    helper_pid = None
    try:
        helper_pid = int(game.stdout.readline())
        assert game.stdout.readline().strip() == "ready" and bystander.stdout.readline().strip() == "ready"
        run = new_run(tmp_path, before, 5)
        run.write_session(SessionInfo("s001", game.pid, process_start_time(game.pid), before, now_filetime(),
                                      {"game_dir": str(tmp_path / "game"), "exe": sys._base_executable}, {}, ()))
        rec = recorder.Recorder(run, lambda: True, FakeProcmon(Clock(), True, None, run), windows_snapshot,
                                lambda r: SplitResult([], []), lambda: [], lambda: 10 * 1024**3, time.monotonic,
                                time.sleep, closer=WindowsCloser(process_identity(os.getpid())), protected_pids=())
        thread = threading.Thread(target=rec.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        while (run.read_status() or {}).get("state") != "capturing" and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.5)  # a few polls, so the live tree holds the game and its helper
        lines = []
        code = stop.stop_command(tmp_path, None, True, is_alive=cli._is_alive, out=lines.append)
        thread.join(20)
        assert code == 0, lines
        assert game.wait(10) == 7  # it closed by itself when asked
        assert not cli._is_alive(helper_pid)  # no window to ask: ended after the wait
        assert bystander.poll() is None  # never in a session: untouched
        [(session_id, lists)] = run.read_stop()["sessions"].items()
        assert session_id == "s001" and lists["asked_to_close"] == [game.pid] and lists["not_closed"] == []
        # the helper, and with it the console host Windows gave it (a child process of the helper)
        assert helper_pid in lists["ended"] and game.pid not in lists["ended"]
        assert not {bystander.pid, os.getpid()} & set(lists["ended"])
        assert any(line.startswith("Asked 1 processes to close; ended ") for line in lines)
    finally:
        for proc in (game, bystander):
            if proc.poll() is None:
                proc.kill()
            proc.wait(10)
            proc.stdout.close()
        if helper_pid is not None and cli._is_alive(helper_pid):
            subprocess.run(["taskkill", "/F", "/PID", str(helper_pid)], capture_output=True)
