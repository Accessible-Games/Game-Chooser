"""The recorder: drives Process Monitor for a whole Game Chooser run, then splits the capture.

Everything that touches the system is injected so the lifecycle is unit-tested without admin rights.
"""
import os
from datetime import datetime
from typing import Callable

from tracing import messages
from tracing.live import LiveProc, LiveTree
from tracing.procmon import ProcmonControl, write_filter_config
from tracing.runfiles import RunDir
from tracing.splitrun import SplitResult

POLL_SECONDS = 0.5
STOP_GRACE_SECONDS = 10.0
READY_TIMEOUT = 60.0
STOP_TIMEOUT = 300.0
DISK_CHECK_SECONDS = 30.0
MIN_FREE_BYTES = 2 * 1024**3


class Recorder:
    def __init__(self, run: RunDir, app_alive: Callable[[], bool], procmon: ProcmonControl,
                 snapshot: Callable[[], list[LiveProc]], split: Callable[[RunDir], SplitResult],
                 leftovers: Callable[[], list[RunDir]], free_bytes: Callable[[], int],
                 clock: Callable[[], float], sleep: Callable[[float], None]):
        self.run_dir = run
        self.app_alive = app_alive
        self.procmon = procmon  # kept for the whole run: the real one kills Process Monitor when collected
        self.snapshot = snapshot
        self.split = split
        self.leftovers = leftovers
        self.free_bytes = free_bytes
        self.clock = clock
        self.sleep = sleep
        self.tree = LiveTree()
        self._added: set[str] = set()
        self._failed = False

    def _log(self, text: str) -> None:
        try:
            with open(self.run_dir.log_path, "a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now().isoformat(timespec='seconds')} {text}\n")
        except OSError:
            pass

    def _status(self, state: str, **kwargs) -> None:
        if self._failed:
            return
        try:
            self.run_dir.write_status(state, **kwargs)
        except OSError as exc:
            self._log(f"could not write status {state}: {exc}")

    def _failure(self, message: str) -> None:
        self._log(f"failed: {message}")
        try:
            self.run_dir.write_status("failed", error=message)
        except OSError as exc:
            self._log(f"could not write failed status: {exc}")
        self._failed = True

    def _update_tree(self) -> None:
        try:
            for info in self.run_dir.read_sessions():
                if info.session_id not in self._added:
                    self._added.add(info.session_id)
                    self.tree.add_session(info.session_id, info.root_pid, info.root_start,
                                          info.game.get("game_dir"), info.launch_before)
            self.tree.update(self.snapshot())
        except Exception as exc:  # keep the last membership; never crash the recorder
            self._log(f"live tree update failed: {exc!r}")

    def run(self) -> int:
        run = self.run_dir
        self._status("starting", recorder_pid=os.getpid())
        try:
            write_filter_config(run.config_path)
            self.procmon.start(run.capture_path, run.config_path)
            ready = self.procmon.wait_ready(READY_TIMEOUT)
        except Exception as exc:
            self._log(f"procmon start failed: {exc!r}")
            ready = False
        if not ready:
            self._failure(messages.PROCMON_NOT_READY)
        else:
            self._status("capturing")
            self._loop()
        self._status("stopping")
        try:
            self.procmon.stop(STOP_TIMEOUT)
        except Exception as exc:
            self._log(f"procmon stop failed: {exc!r}")
        if run.capture_files():
            self._status("splitting")
            try:
                self.split(run)
            except Exception as exc:
                self._failure(messages.SPLIT_FAILED.format(error=exc))
        try:
            for other in self.leftovers():
                try:
                    self.split(other)
                except Exception as exc:
                    self._log(f"leftover split failed for {other.path}: {exc!r}")
        except Exception as exc:
            self._log(f"listing leftovers failed: {exc!r}")
        self._status("done")
        return 1 if self._failed else 0

    def _loop(self) -> None:
        last_disk = self.clock()
        empty_since = None
        while True:
            self._update_tree()
            if not self.procmon.running():
                self._failure(messages.PROCMON_STOPPED)
                return
            now = self.clock()
            if now - last_disk >= DISK_CHECK_SECONDS:
                last_disk = now
                if self.free_bytes() < MIN_FREE_BYTES:
                    self._failure(messages.DISK_LOW)
                    return
            if self.app_alive() or self.tree.running():
                empty_since = None
            elif empty_since is None:
                empty_since = now
            elif now - empty_since >= STOP_GRACE_SECONDS:
                return
            self.sleep(POLL_SECONDS)
