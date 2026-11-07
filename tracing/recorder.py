"""The recorder: drives Process Monitor for a whole Game Chooser run, then splits the capture.

Everything that touches the system is injected so the lifecycle is unit-tested without admin rights.
"""
import logging
import os
from typing import Callable

from tracing import messages
from tracing.live import LiveProc, LiveTree
from tracing.procmon import ProcmonControl, write_filter_config
from tracing.runfiles import RunDir
from tracing.splitrun import SplitResult

log = logging.getLogger("tracing.recorder")

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
        self._errors: list[str] = []

    def _status(self, state: str, **kwargs) -> None:
        if self._failed:
            return
        try:
            self.run_dir.write_status(state, **kwargs)
        except OSError as exc:
            log.warning("could not write status %s: %s", state, exc)

    def _failure(self, message: str) -> None:
        log.error("failed: %s", message)
        self._errors.append(message)
        self._failed = True
        try:
            self.run_dir.write_status("failed", error=" ".join(self._errors))
        except OSError as exc:
            log.warning("could not write failed status: %s", exc)

    def _update_tree(self) -> None:
        try:
            for info in self.run_dir.read_sessions():
                if info.session_id not in self._added:
                    self._added.add(info.session_id)
                    self.tree.add_session(info.session_id, info.root_pid, info.root_start,
                                          info.game.get("game_dir"), info.launch_before)
            self.tree.update(self.snapshot())
        except Exception as exc:  # keep the last membership; never crash the recorder
            log.warning("live tree update failed: %r", exc)

    def run(self) -> int:
        logger = logging.getLogger("tracing")
        handler = None
        try:
            handler = logging.FileHandler(self.run_dir.log_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        except OSError:
            handler = None
        try:
            return self._run()
        finally:
            if handler is not None:
                logger.removeHandler(handler)
                handler.close()

    def _run(self) -> int:
        run = self.run_dir
        self._status("starting", recorder_pid=os.getpid())
        try:
            write_filter_config(run.config_path)
            self.procmon.start(run.capture_path, run.config_path)
            if not self.procmon.wait_ready(READY_TIMEOUT):
                self._failure(messages.PROCMON_NOT_READY)
            else:
                self._status("capturing")
                self._loop()
        except Exception as exc:
            log.exception("recorder error")
            self._failure(messages.RECORDER_ERROR.format(error=exc))
        self._status("stopping")
        try:
            if not self.procmon.stop(STOP_TIMEOUT):
                self._failure(messages.PROCMON_STOP_SLOW)
        except Exception as exc:
            log.exception("procmon stop failed")
            self._failure(messages.RECORDER_ERROR.format(error=exc))
        try:
            if run.capture_files():
                self._status("splitting")
                try:
                    self.split(run)
                except Exception as exc:
                    log.exception("split failed")
                    self._failure(messages.SPLIT_FAILED.format(error=exc))
            for other in self.leftovers():
                try:
                    self.split(other)
                except Exception:
                    log.exception("leftover split failed for %s", other.path)
        except Exception:
            log.exception("finishing failed")
        self._status("done")
        return 1 if self._failed else 0

    def _loop(self) -> None:
        last_disk = None
        empty_since = None
        while True:
            self._update_tree()
            if not self.procmon.running():
                self._failure(messages.PROCMON_STOPPED)
                return
            now = self.clock()
            if last_disk is None or now - last_disk >= DISK_CHECK_SECONDS:
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
