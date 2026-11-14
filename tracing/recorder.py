"""The recorder: drives Process Monitor for a whole Game Chooser run, then splits the capture.

Everything that touches the system is injected so the lifecycle is unit-tested without admin rights.
"""
import logging
import ntpath
import os
from typing import Callable

from tracing import closing, messages
from tracing.clock import now_filetime
from tracing.closing import GameCloser, Refused
from tracing.live import LiveProc, LiveTree, process_start_time
from tracing.procmon import EXCLUDED_PROCESSES, ProcmonControl, describe_filter, write_filter_config
from tracing.runfiles import RunDir
from tracing.splitrun import SplitResult

log = logging.getLogger("tracing.recorder")

POLL_SECONDS = 0.5
STOP_GRACE_SECONDS = 10.0
READY_TIMEOUT = 60.0
STOP_TIMEOUT = 300.0
DISK_CHECK_SECONDS = 30.0
MIN_FREE_BYTES = 2 * 1024**3
SNAPSHOT_WARNING_SECONDS = 60.0
CLOSE_WAIT_SECONDS = 15.0  # --trace-stop --close-games: from asking the games to close to ending what is left
END_WAIT_SECONDS = 5.0  # then this long for the ended processes to be gone

# Never asked to close or ended, whatever the session files say: the idle and System processes, and every program
# the capture drops (Process Monitor, Game Chooser, Windows' own services), which a session can't hold.
_PROTECTED_PIDS = frozenset({0, 4})
_PROTECTED_NAMES = frozenset(name.lower() for name in EXCLUDED_PROCESSES)


class Recorder:
    def __init__(self, run: RunDir, app_alive: Callable[[], bool], procmon: ProcmonControl,
                 snapshot: Callable[[], list[LiveProc]], split: Callable[[RunDir], SplitResult],
                 leftovers: Callable[[], list[RunDir]], free_bytes: Callable[[], int],
                 clock: Callable[[], float], sleep: Callable[[float], None], closer: GameCloser,
                 now: Callable[[], int] = now_filetime, protected_pids=()):
        self.run_dir = run
        self.app_alive = app_alive
        self.procmon = procmon  # kept for the whole run: the real one kills Process Monitor when collected
        self.snapshot = snapshot
        self.split = split
        self.leftovers = leftovers
        self.free_bytes = free_bytes
        self.clock = clock
        self.sleep = sleep
        self.closer = closer
        self.now = now
        self._protected_pids = _PROTECTED_PIDS | {os.getpid()} | set(protected_pids)
        self._accept_sessions = True
        self._stop_taken = False
        self._refused: dict[tuple[int, int], str] = {}  # processes the closer would not touch, and why
        self.tree = LiveTree()
        self._added: set[str] = set()
        self._failed = False
        self._errors: list[str] = []
        self._snapshot_warned_at: float | None = None
        self._snapshot_unlogged = 0
        self._snapshot_failing_since: float | None = None

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
        # Either step failing keeps the last membership (for SNAPSHOT_WARNING_SECONDS at most for the process list);
        # neither may crash the recorder.
        if self._accept_sessions:
            try:
                for info in self.run_dir.read_sessions_and_problems()[0]:
                    if info.session_id not in self._added:
                        self._added.add(info.session_id)
                        self.tree.add_session(info.session_id, info.root_pid, info.root_start,
                                              info.game.get("game_dir"), info.launch_before,
                                              info.game.get("exe"), info.launch_after)
            except Exception as exc:
                log.warning("live tree update failed reading the session files: %r", exc)
        try:
            self.tree.update(self.snapshot())
            self._snapshot_failing_since = None
        except Exception as exc:
            self._snapshot_failed(exc)

    def _snapshot_failed(self, exc: Exception) -> None:
        """Log the first failure, then at most once per SNAPSHOT_WARNING_SECONDS with how many weren't logged."""
        now = self.clock()
        if self._snapshot_failing_since is None:
            self._snapshot_failing_since = now
        elif now - self._snapshot_failing_since >= SNAPSHOT_WARNING_SECONDS:
            self.tree.update([])  # stale membership must not keep the run going for ever
        if self._snapshot_warned_at is None:
            log.warning("live tree update failed: %r", exc)
        elif now - self._snapshot_warned_at >= SNAPSHOT_WARNING_SECONDS:
            log.warning("live tree update failed: %r (and %d more times since the last warning)", exc,
                        self._snapshot_unlogged)
        else:
            self._snapshot_unlogged += 1
            return
        self._snapshot_warned_at = now
        self._snapshot_unlogged = 0

    def _split(self, run: RunDir) -> None:
        result = self.split(run)
        for problem in getattr(result, "problems", None) or []:
            log.warning("split problem in %s: %s", run.path, problem)
        if getattr(result, "capture_kept", False):
            log.warning("the capture in %s was kept so it can be split again with --trace-split", run.path)

    def run(self) -> int:
        logger = logging.getLogger("tracing")
        handler = None
        old_level = logger.level
        if logger.getEffectiveLevel() > logging.INFO:
            logger.setLevel(logging.INFO)
        try:
            handler = logging.FileHandler(self.run_dir.log_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        except OSError:
            handler = None
        try:
            return self._run()
        finally:
            logger.setLevel(old_level)
            if handler is not None:
                logger.removeHandler(handler)
                handler.close()

    def _run(self) -> int:
        run = self.run_dir
        self._status("starting", recorder_pid=os.getpid(), recorder_start=process_start_time(os.getpid()))
        try:
            write_filter_config(run.config_path)
            for line in describe_filter():
                log.info("%s", line)
            self.procmon.start(run.capture_path, run.config_path)
            if not self.procmon.wait_ready(READY_TIMEOUT):
                log.warning("Process Monitor not ready: %s", getattr(self.procmon, "not_ready_reason", None))
                self._failure(messages.PROCMON_NOT_READY)
            else:
                self._status("capturing")
                self._loop()
        except Exception as exc:
            log.exception("recorder error")
            self._failure(messages.RECORDER_ERROR.format(error=exc))
        if not self._stop_taken:  # a stop request wrote it when it was taken
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
                    self._split(run)
                except Exception as exc:
                    log.exception("split failed")
                    self._failure(messages.SPLIT_FAILED.format(error=exc))
            for other in self.leftovers():
                try:
                    self._split(other)
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
            request = self._claim_stop()
            if request is not None:
                self._take_stop(request)
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

    # --- a stop request (--trace-stop) ---

    def _claim_stop(self) -> dict | None:
        """The stop request, once this recorder has claimed it (from then on the command waits for the result
        instead of withdrawing it). A claim that fails is tried again at the next poll."""
        try:
            return self.run_dir.claim_stop_request()
        except OSError as exc:
            log.warning("could not take the stop request: %s", exc)
            return None

    def _take_stop(self, request: dict) -> None:
        """End the run now, while traced programs may still be open. From here on no new session joins the run.

        stop.json records it for the split and for the app: the sessions that were running, and, when the request
        asked for the games to be closed, what was done to each of their processes (written after each act; the
        full `not_closed` lists come at the end)."""
        self._stop_taken = True
        self._accept_sessions = False
        close = request.get("close_games") is True
        running = self.tree.running_processes()
        record = {"requested": request.get("requested"), "time": self.now(), "close_games": close,
                  "sessions": {sid: {"asked_to_close": [], "ended": [], "not_closed": []} for sid in running}}
        log.info("stop requested (close games: %s); sessions still running: %s", close,
                 ", ".join(running) or "none")
        self._write_stop(record)
        self._status("stopping")
        if not close:
            return
        why = closing.NOT_CLOSED_FAILED
        try:
            if not self._close_games(record):
                why = closing.NOT_CLOSED_PROCMON
        finally:
            for sid, procs in self.tree.running_processes().items():
                if sid in record["sessions"]:
                    left = [{"pid": p.pid, "reason": self._not_closed_reason(p, why)}
                            for p in sorted(procs, key=lambda p: p.pid)]
                    record["sessions"][sid]["not_closed"] = left
                    for p, entry in zip(sorted(procs, key=lambda p: p.pid), left):
                        log.warning("still running after the stop, session %s: %d (%s): %s", sid, p.pid,
                                    p.image_path, entry["reason"])
            self._write_stop(record)

    def _protected(self, proc: LiveProc) -> bool:
        """Never a target, whatever the session files say: this recorder, Game Chooser, pids 0 and 4, a program
        the capture drops, or one whose path couldn't be read."""
        if proc.pid in self._protected_pids or not proc.image_path:
            return True
        return ntpath.basename(proc.image_path).lower() in _PROTECTED_NAMES

    def _not_closed_reason(self, proc: LiveProc, default: str) -> str:
        if self._protected(proc):
            return closing.NOT_CLOSED_PROTECTED
        return self._refused.get((proc.pid, proc.start), default)

    def _write_stop(self, record: dict) -> None:
        try:
            self.run_dir.write_stop(record)
        except OSError as exc:
            log.warning("could not write stop.json: %s", exc)

    def _close_targets(self, record: dict) -> dict[tuple[int, int], tuple[LiveProc, list[str]]]:
        """The running processes of the sessions the stop found running, each with the sessions holding it.

        Only what the live tree attributes to one of those sessions, as (pid, start) so a reused pid is never
        taken; never a protected process (`_protected`) or one the closer has refused. The tree comes from
        session files any program of the user can write, so this is not the safeguard: the closer's check of who
        each process runs as is (closing.Closer)."""
        targets: dict[tuple[int, int], tuple[LiveProc, list[str]]] = {}
        for sid, procs in self.tree.running_processes().items():
            if sid not in record["sessions"]:
                continue
            for proc in procs:
                if self._protected(proc) or (proc.pid, proc.start) in self._refused:
                    continue
                targets.setdefault((proc.pid, proc.start), (proc, []))[1].append(sid)
        return targets

    def _close_games(self, record: dict) -> bool:
        """Ask every traced process to close, wait CLOSE_WAIT_SECONDS for them to go, then end what is left.
        A process the closer refuses is left alone from then on and not waited for. False when Process Monitor
        died during the wait, and so nothing was ended."""
        self._act(self.closer.ask_close, "asked_to_close", self._close_targets(record), record)
        targets = self._close_targets(record)  # without the refused ones
        deadline = self.clock() + CLOSE_WAIT_SECONDS
        while targets and self.clock() < deadline:
            self.sleep(POLL_SECONDS)
            self._update_tree()
            if not self.procmon.running():
                self._failure(messages.PROCMON_STOPPED)  # nothing records any more: end nothing
                return False
            targets = self._close_targets(record)
        self._act(self.closer.end, "ended", targets, record)
        targets = self._close_targets(record)
        deadline = self.clock() + END_WAIT_SECONDS
        while targets and self.clock() < deadline:
            self.sleep(POLL_SECONDS)
            self._update_tree()
            targets = self._close_targets(record)
        return True

    def _act(self, action: Callable[[int, int], bool], field: str, targets: dict, record: dict) -> None:
        """Do `action` to each target and note it in the record. stop.json is written after every single one, so
        the file says what has been done even if this recorder dies in the middle."""
        for (pid, start), (proc, sids) in targets.items():
            try:
                done = action(pid, start)
            except Refused as refused:
                self._refused[(pid, start)] = refused.reason
                log.warning("%s refused for %d (%s): %s", field, pid, proc.image_path, refused.reason)
                for sid in sids:
                    record["sessions"][sid]["not_closed"].append({"pid": pid, "reason": refused.reason})
                self._write_stop(record)
                continue
            except Exception as exc:
                log.warning("%s failed for %d (%s): %r", field, pid, proc.image_path, exc)
                done = False
            log.info("%s %d (%s): %s", field, pid, proc.image_path, "yes" if done else "no")
            if done:
                for sid in sids:
                    record["sessions"][sid][field].append(pid)
                self._write_stop(record)
