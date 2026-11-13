from tracing import messages, recorder
import logging
from tracing.live import LiveProc
from tracing.model import SessionInfo
from tracing.runfiles import RunDir
from tracing.splitrun import SplitResult

GIB = 1024**3


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class FakeProcmon:
    stop_result = True

    def __init__(self, clock, ready=True, dies_at=None, run_dir=None):
        self.clock, self.ready, self.dies_at, self.run_dir = clock, ready, dies_at, run_dir
        self.calls = []
        self.stopped_at = None

    def start(self, backing, config):
        self.calls.append("start")
        assert config.exists()

    def wait_ready(self, timeout):
        self.calls.append(("wait_ready", timeout))
        return self.ready

    def running(self):
        return self.dies_at is None or self.clock.t < self.dies_at

    def stop(self, timeout):
        self.calls.append("stop")
        self.stopped_at = self.clock.t
        if self.run_dir is not None:
            (self.run_dir.path / "capture.pml").write_bytes(b"x")
        return self.stop_result


def session(sid="s1", pid=100, start=1000):
    return SessionInfo(sid, pid, start, 500, 600, {"game_dir": "C:/g"}, {}, ())


def make(tmp_path, *, app_until=0.0, game_until=None, ready=True, dies_at=None, free=None,
         split=None, leftovers=None, capture=True, snapshot=None, with_session=False, sessions=(), clock=None,
         **options):
    """`options` go to the Recorder as they are (closer, now, protected_pids)."""
    clock = clock or Clock()
    run = RunDir(tmp_path / "run")
    (run.path / "sessions").mkdir(parents=True)
    for info in ([session()] if with_session else []) + list(sessions):
        run.write_session(info)
    pm = FakeProcmon(clock, ready, dies_at, run if capture else None)
    states = []
    real = run.write_status

    def spy(state, **kwargs):
        states.append(state)
        real(state, **kwargs)

    run.write_status = spy
    splits = []

    def do_split(r):
        splits.append(r)
        if split:
            return split(r)
        return SplitResult([], [])

    def snap():
        if game_until is not None and clock.t < game_until:
            return [LiveProc(100, 1, 1000, "C:/g/x.exe")]
        return []

    rec = recorder.Recorder(run, lambda: clock.t < app_until, pm, snapshot or snap, do_split,
                            leftovers or (lambda: []), free or (lambda: 10 * GIB), clock, clock.sleep, **options)
    return rec, run, pm, states, splits, clock


def test_clean_run_with_no_games(tmp_path):
    rec, run, pm, states, splits, _ = make(tmp_path)
    assert rec.run() == 0
    assert states == ["starting", "capturing", "stopping", "splitting", "done"]
    assert pm.calls.count("stop") == 1 and len(splits) == 1
    assert run.read_status()["state"] == "done"
    assert run.read_status()["recorder_pid"]


def test_waits_for_games_after_app_exits(tmp_path):
    rec, run, pm, states, splits, _ = make(tmp_path, app_until=5.0, game_until=60.0, with_session=True)
    assert rec.run() == 0
    assert 70.0 <= pm.stopped_at <= 71.0


def test_app_crash_counts_as_exit(tmp_path):
    rec, run, pm, *_ = make(tmp_path, app_until=3.0)
    assert rec.run() == 0
    assert 13.0 <= pm.stopped_at <= 14.0


def test_not_ready_fails_and_stops(tmp_path):
    rec, run, pm, states, *_ = make(tmp_path, ready=False)
    assert rec.run() == 1
    st = run.read_status()
    assert st["state"] == "failed" and st["error"] == messages.PROCMON_NOT_READY
    assert "stop" in pm.calls
    assert "capturing" not in states


def test_procmon_dying_is_reported(tmp_path):
    rec, run, pm, states, splits, _ = make(tmp_path, app_until=1000.0, dies_at=20.0)
    assert rec.run() == 1
    st = run.read_status()
    assert st["state"] == "failed" and st["error"] == messages.PROCMON_STOPPED
    assert len(splits) == 1


def test_low_disk_stops_capture_and_reports(tmp_path):
    holder = {}
    rec, run, pm, states, splits, clock = make(
        tmp_path, app_until=1000.0, free=lambda: 10 * GIB if holder["c"].t < 40 else GIB)
    holder["c"] = clock
    assert rec.run() == 1
    st = run.read_status()
    assert st["state"] == "failed" and st["error"] == messages.DISK_LOW
    assert "stop" in pm.calls and len(splits) == 1
    assert clock.t < 100


def test_split_failure_reports_and_keeps_going(tmp_path):
    other = RunDir(tmp_path / "other")
    calls = []

    def split(r):
        calls.append(r)
        if r is not other:
            raise RuntimeError("boom")

    rec, run, pm, states, splits, _ = make(tmp_path, split=split, leftovers=lambda: [other])
    assert rec.run() == 1
    st = run.read_status()
    assert st["state"] == "failed"
    assert st["error"] == messages.SPLIT_FAILED.format(error="boom")
    assert calls == [run, other]


def test_leftover_runs_are_split_after_this_one(tmp_path):
    other = RunDir(tmp_path / "other")
    rec, run, pm, states, splits, _ = make(tmp_path, leftovers=lambda: [other])
    assert rec.run() == 0
    assert splits == [run, other]


def test_no_capture_files_skips_split(tmp_path):
    rec, run, pm, states, splits, _ = make(tmp_path, capture=False)
    assert rec.run() == 0
    assert splits == [] and "splitting" not in states


def test_snapshot_error_is_logged_and_run_continues(tmp_path):
    def bad():
        raise OSError("nope")

    rec, run, pm, states, splits, _ = make(tmp_path, snapshot=bad, with_session=True)
    assert rec.run() == 0
    assert "live tree update failed" in run.log_path.read_text(encoding="utf-8")


def test_a_process_list_that_cannot_be_read_keeps_the_last_membership(tmp_path):
    clock = Clock()

    def snap():  # the game runs, then the list can't be read for 30 s, then the game is gone
        if clock.t == 0:
            return [LiveProc(100, 1, 1000, "C:/g/x.exe")]
        if clock.t < 30:
            raise OSError("nope")
        return []

    rec, run, pm, *_ = make(tmp_path, snapshot=snap, with_session=True, clock=clock)
    assert rec.run() == 0
    assert 40.0 <= pm.stopped_at <= 41.0


def test_a_process_list_unreadable_for_a_minute_counts_every_game_as_exited(tmp_path):
    clock = Clock()

    def snap():
        if clock.t == 0:
            return [LiveProc(100, 1, 1000, "C:/g/x.exe")]
        raise OSError("nope")

    rec, run, pm, *_ = make(tmp_path, snapshot=snap, with_session=True, clock=clock, dies_at=300.0)
    code = rec.run()
    assert 70.0 <= pm.stopped_at <= 71.0 and code == 0  # 60 s of failed reads, then the 10 s grace


def test_sessions_added_once(tmp_path):
    rec, run, *_ = make(tmp_path, with_session=True)
    added = []
    orig = rec.tree.add_session
    rec.tree.add_session = lambda *a: (added.append(a), orig(*a))[1]
    rec.run()
    assert added == [("s1", 100, 1000, "C:/g", 500, None, 600)]  # exe (none in this session), launch_after


def _raiser():
    raise OSError("disk gone")


def _check_recorder_error(tmp_path, app_alive=None, **kwargs):
    rec, run, pm, states, splits, _ = make(tmp_path, app_until=1000.0, **kwargs)
    rec.app_alive = app_alive or rec.app_alive
    assert rec.run() == 1
    st = run.read_status()
    assert st["state"] == "failed"
    assert st["error"] == messages.RECORDER_ERROR.format(error="disk gone")
    assert "stop" in pm.calls and len(splits) == 1
    assert "disk gone" in run.log_path.read_text(encoding="utf-8")


def test_free_bytes_raising_still_stops_and_splits(tmp_path):
    _check_recorder_error(tmp_path, free=_raiser)


def test_app_alive_raising_still_stops_and_splits(tmp_path):
    _check_recorder_error(tmp_path, app_alive=_raiser)


def test_corrupt_session_warning_reaches_recorder_log(tmp_path):
    rec, run, *_ = make(tmp_path)
    (run.path / "sessions" / "bad.json").write_text("{nope", encoding="utf-8")
    before = list(logging.getLogger("tracing").handlers)
    assert rec.run() == 0
    assert "bad.json" in run.log_path.read_text(encoding="utf-8")
    assert logging.getLogger("tracing").handlers == before


def test_active_filter_is_logged_at_info(tmp_path):
    rec, run, *_ = make(tmp_path)
    logging.getLogger("tracing").setLevel(logging.NOTSET)  # the recorder must not rely on the host's level
    assert rec.run() == 0
    assert logging.getLogger("tracing").level == logging.NOTSET
    text = run.log_path.read_text(encoding="utf-8")
    assert "MoUsoCoreWorker.exe" in text
    assert "Desired Access: Read Attributes, Disposition: Open," in text
    assert "INFO" in text and "CreateFile" in text


def test_slow_stop_is_reported_and_split_still_runs(tmp_path):
    rec, run, pm, states, splits, _ = make(tmp_path)
    pm.stop_result = False
    assert rec.run() == 1
    st = run.read_status()
    assert st["state"] == "failed" and st["error"] == messages.PROCMON_STOP_SLOW
    assert len(splits) == 1


def test_several_failures_are_joined_first_first(tmp_path):
    def split(r):
        raise RuntimeError("boom")

    rec, run, pm, states, splits, _ = make(tmp_path, app_until=1000.0, free=lambda: GIB, split=split)
    assert rec.run() == 1
    assert run.read_status()["error"] == (
        messages.DISK_LOW + " " + messages.SPLIT_FAILED.format(error="boom"))


def test_disk_checked_on_first_poll(tmp_path):
    rec, run, pm, states, splits, clock = make(tmp_path, app_until=1000.0, free=lambda: GIB)
    rec.run()
    assert clock.t == 0.0


def test_split_problems_reach_the_recorder_log(tmp_path):
    other = RunDir(tmp_path / "other")

    def split(r):
        if r is other:
            return SplitResult([], ["s002-9: leftover trouble"], True)
        return SplitResult([], ["s001-5: this run's trouble"], True)

    rec, run, *_ = make(tmp_path, split=split, leftovers=lambda: [other])
    assert rec.run() == 0
    text = run.log_path.read_text(encoding="utf-8")
    assert "s001-5: this run's trouble" in text and "s002-9: leftover trouble" in text
    assert str(other.path) in text


def test_snapshot_failure_warning_is_rate_limited(tmp_path):
    def bad():
        raise OSError("nope")

    rec, run, pm, states, splits, clock = make(tmp_path, app_until=130.0, snapshot=bad)
    assert rec.run() == 0
    lines = [x for x in run.log_path.read_text(encoding="utf-8").splitlines() if "live tree update failed" in x]
    assert len(lines) == 3  # the first at 0 s, then at 60 s and 120 s with how many there were since
    assert "nope" in lines[0] and "119 more times" in lines[1] and "119 more times" in lines[2]


def test_status_says_when_the_recorder_process_started(tmp_path):
    import os
    from tracing.live import process_start_time
    rec, run, *_ = make(tmp_path)
    assert rec.run() == 0
    status = run.read_status()
    assert status["recorder_pid"] == os.getpid() and status["recorder_start"] == process_start_time(os.getpid())
