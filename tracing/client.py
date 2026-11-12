"""Game Chooser's side of tracing: starts the elevated recorder, watches it, records launches and marks."""
import dataclasses
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from engine.errors import ElevationDeclined
from tracing import messages
from tracing.attribution import UNKNOWN_PID
from tracing.clock import now_filetime
from tracing.live import LiveTree, process_start_time, windows_snapshot
from tracing.model import SessionInfo
from tracing.runfiles import RunDir, new_run

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TraceState:
    phase: str  # "starting", "on" or "off"
    reason: str | None = None
    # False for an off the admin asked for (--trace-stop): the title follows, but no dialog is opened for it; the
    # reason is said when a game is next launched.
    announce: bool = True


class TraceClient:
    def __init__(self, traces_dir: Path, procmon: Path, start_recorder: Callable, env: dict,
                 clock=now_filetime, snapshot=windows_snapshot, start_time_of=process_start_time,
                 app_pid: int = os.getpid()):
        self._traces_dir = Path(traces_dir)
        self._procmon = Path(procmon)
        self._start_recorder = start_recorder
        self._env = dict(env)
        self._clock = clock
        self._snapshot = snapshot
        self._start_time_of = start_time_of
        self._app_pid = app_pid
        self._lock = threading.Lock()
        self._state = TraceState("starting")
        self._process = None
        self._run: RunDir | None = None
        self._tree = LiveTree()
        self._sessions: dict[str, SessionInfo] = {}
        self._count = 0

    @property
    def state(self) -> TraceState:
        with self._lock:
            return self._state

    def _set_off(self, reason: str, announce: bool = True) -> None:
        with self._lock:
            if self._state.phase != "off":
                self._state = TraceState("off", reason, announce)

    def _set_gone(self, run: RunDir | None) -> None:
        """The recorder has left capturing or exited: a stop it took (stop.json) is quiet, anything else is not."""
        if run is not None and run.read_stop() is not None:
            self._set_off(messages.STOPPED, announce=False)
        else:
            self._set_off(messages.RECORDER_GONE)

    def start(self) -> None:
        if not self._procmon.exists():
            self._set_off(messages.PROCMON_MISSING.format(path=self._procmon))
            return
        try:
            run = new_run(self._traces_dir, self._clock(), self._app_pid)
            with self._lock:
                self._run = run
            process = self._start_recorder(["--trace-recorder", str(run.path), "--app-pid", str(self._app_pid),
                                            "--procmon", str(self._procmon)])
        except ElevationDeclined:
            self._set_off(messages.DECLINED)
            return
        except Exception as exc:
            log.warning("recorder start failed: %s", exc)
            self._set_off(messages.START_FAILED.format(error=exc))
            return
        with self._lock:
            self._process = process

    def poll(self) -> TraceState:
        with self._lock:
            if self._state.phase == "off":
                return self._state
            run, process = self._run, self._process
        try:
            self._tree.update(self._snapshot())
        except Exception as exc:
            log.warning("process snapshot failed: %s", exc)
        status = run.read_status() if run else None
        recorder_state = status.get("state") if status else None
        if recorder_state == "failed":
            self._set_off(messages.RECORDER_FAILED.format(error=status.get("error")))
        elif recorder_state not in (None, "starting", "capturing"):
            self._set_gone(run)
        elif process is not None and process.wait(0) is not None:
            self._set_gone(run)
        elif recorder_state == "capturing":
            with self._lock:
                if self._state.phase == "starting":
                    self._state = TraceState("on")
        return self.state

    def game_launched(self, pid: int | None, before: int, after: int, game: dict) -> str | None:
        """Record a launch. A launch that gave no process ID (pid None) is recorded with root_pid 0 and no start
        time; its root is then found by the game's exe (`attribution.find_root_by_exe`)."""
        root_pid = pid or UNKNOWN_PID
        # Ask the recorder again: the launch may have taken a while (an administrator prompt), and a run stopped
        # in the meantime must not get a session it never captured.
        self.poll()
        with self._lock:
            if self._state.phase != "on" or self._run is None:
                return None
            self._count += 1
            session_id = f"s{self._count:03d}-{root_pid}"
            run = self._run
        root_start = self._start_time_of(root_pid) if root_pid != UNKNOWN_PID else None
        info = SessionInfo(session_id, root_pid, root_start, before, after, dict(game), dict(self._env), ())
        self._sessions[session_id] = info
        run.write_session(info)
        self._tree.add_session(session_id, root_pid, root_start, game.get("game_dir"), before, game.get("exe"),
                               after)
        return session_id

    def mark(self) -> list[str]:
        now = self._clock()
        if self.poll().phase != "on":
            return []  # the run has ended (or never began): a game still open is no longer being traced
        with self._lock:
            run = self._run
        marked = []
        for session_id in sorted(self._tree.running()):
            info = self._sessions.get(session_id)
            if info is None or run is None:
                continue
            info = dataclasses.replace(info, marks=info.marks + (now,))
            self._sessions[session_id] = info
            run.write_session(info)
            marked.append(session_id)
        return marked
