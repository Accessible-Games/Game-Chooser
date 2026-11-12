import pytest

import tracing.runfiles as runfiles
from tracing.clock import TICKS_PER_SECOND
from tracing.model import SessionInfo
from tracing.runfiles import RunDir, STATES, leftover_runs, new_run, replace_with_retry

S = TICKS_PER_SECOND
FT = 133_000_000_000_000_000


def session(sid, before):
    return SessionInfo(sid, 1, None, before, before + 1, {"title": "T", "catalog_id": "c"}, {}, ())


def test_status_round_trip_and_atomic_keep_pid(tmp_path):
    run = RunDir(tmp_path)
    assert run.read_status() is None
    run.write_status("starting", recorder_pid=7)
    run.write_status("capturing")
    status = run.read_status()
    assert status["recorder_pid"] == 7 and status["state"] == "capturing" and status["error"] is None
    assert "updated" in status and "capturing" in STATES
    assert [p.name for p in tmp_path.iterdir()] == ["status.json"]


def test_status_unreadable_is_none(tmp_path):
    (tmp_path / "status.json").write_text("{not json", encoding="utf-8")
    assert RunDir(tmp_path).read_status() is None


def test_sessions_round_trip_sorted(tmp_path):
    run = new_run(tmp_path, FT, 5)
    run.write_session(session("b", 200))
    run.write_session(session("a", 100))
    (run.sessions_dir / "bad.json").write_text("{oops", encoding="utf-8")
    assert [s.session_id for s in run.read_sessions()] == ["a", "b"]
    sessions, problems = run.read_sessions_and_problems()
    assert [s.session_id for s in sessions] == ["a", "b"]
    assert len(problems) == 1 and "bad.json" in problems[0]
    assert RunDir(tmp_path / "nowhere").read_sessions_and_problems() == ([], [])


def test_new_run_location(tmp_path):
    run = new_run(tmp_path, FT, 5)
    assert run.path.parent == tmp_path / "_runs" and run.path.name.endswith("-5")
    assert run.sessions_dir.is_dir()
    assert run.config_path.name == "filter.pmc" and run.capture_path.name == "capture.pml"
    assert run.log_path.name == "recorder.log" and not run.is_split()


def test_capture_files_order(tmp_path):
    run = RunDir(tmp_path)
    for name in ("capture-2.pml", "capture.pml", "capture-1.pml", "capture-10.pml"):
        (tmp_path / name).write_bytes(b"x")
    assert [p.name for p in run.capture_files()] == ["capture.pml", "capture-1.pml", "capture-2.pml",
                                                     "capture-10.pml"]


def test_leftover_runs(tmp_path):
    dead = new_run(tmp_path, FT, 1)
    live = new_run(tmp_path, FT + S, 2)
    done = new_run(tmp_path, FT + 2 * S, 3)
    empty = new_run(tmp_path, FT + 3 * S, 4)
    nostatus = new_run(tmp_path, FT + 4 * S, 6)
    for run, pid in ((dead, 11), (live, 22), (done, 33), (empty, 44)):
        run.write_status("capturing", recorder_pid=pid)
    for run in (dead, live, done, nostatus):
        run.capture_path.write_bytes(b"x")
    (done.path / "split.json").write_text("{}", encoding="utf-8")
    found = leftover_runs(tmp_path, lambda pid: pid == 22)
    assert sorted(r.path for r in found) == sorted([dead.path, nostatus.path])
    assert leftover_runs(tmp_path / "missing", lambda pid: False) == []


class FlakyReplace:
    def __init__(self, failures):
        self.failures, self.calls = failures, 0

    def __call__(self, src, dst):
        self.calls += 1
        if self.calls <= self.failures:
            raise PermissionError("held by a reader")
        runfiles.os.replace(src, dst)


def test_replace_retries_a_held_file(tmp_path):
    src, dst = tmp_path / "a.tmp", tmp_path / "a"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    replace, sleeps = FlakyReplace(2), []
    replace_with_retry(src, dst, replace=replace, sleep=sleeps.append)
    assert dst.read_text(encoding="utf-8") == "new" and not src.exists()
    assert replace.calls == 3 and sleeps == [0.015, 0.015]


def test_replace_gives_up_after_ten_tries(tmp_path):
    src, dst = tmp_path / "a.tmp", tmp_path / "a"
    src.write_text("new", encoding="utf-8")
    replace, sleeps = FlakyReplace(100), []
    with pytest.raises(PermissionError):
        replace_with_retry(src, dst, replace=replace, sleep=sleeps.append)
    assert replace.calls == 10 and len(sleeps) == 9


def test_status_write_retries_through_the_helper(tmp_path, monkeypatch):
    real, calls = runfiles.os.replace, []

    def flaky(a, b):
        calls.append(a)
        if len(calls) <= 2:
            raise PermissionError("held by a reader")
        real(a, b)

    monkeypatch.setattr(runfiles.time, "sleep", lambda s: None)
    monkeypatch.setattr(runfiles.os, "replace", flaky)
    RunDir(tmp_path).write_status("capturing")
    assert len(calls) == 3
    assert RunDir(tmp_path).read_status()["state"] == "capturing"
    assert [p.name for p in tmp_path.iterdir()] == ["status.json"]


def test_status_write_that_keeps_failing_raises_and_leaves_no_temp(tmp_path, monkeypatch):
    monkeypatch.setattr(runfiles.time, "sleep", lambda s: None)

    def held(a, b):
        raise PermissionError("held")

    monkeypatch.setattr(runfiles.os, "replace", held)
    with pytest.raises(PermissionError):
        RunDir(tmp_path).write_status("capturing")
    assert list(tmp_path.iterdir()) == []


def test_capture_files_ignore_case(tmp_path):
    for name in ("Capture.PML", "CAPTURE-1.pml", "capture-2.Pml", "capture-x.pml", "notes.pml"):
        (tmp_path / name).write_bytes(b"x")
    assert [p.name for p in RunDir(tmp_path).capture_files()] == ["Capture.PML", "CAPTURE-1.pml", "capture-2.Pml"]


def test_stop_request_round_trip(tmp_path):
    run = new_run(tmp_path, FT, 5)
    assert run.read_stop_request() is None
    run.write_stop_request({"requested": FT, "close_games": True})
    assert run.stop_request_path.name == "stop-request.json"
    assert run.read_stop_request() == {"requested": FT, "close_games": True}
    assert not list(run.path.glob("*.tmp"))


def test_stop_record_round_trip_and_unreadable_is_none(tmp_path):
    run = new_run(tmp_path, FT, 5)
    assert run.read_stop() is None
    run.write_stop({"time": FT, "close_games": False, "sessions": {}})
    assert run.stop_path.name == "stop.json"
    assert run.read_stop() == {"time": FT, "close_games": False, "sessions": {}}
    run.stop_path.write_text("[1]", encoding="utf-8")
    assert run.read_stop() is None
    run.stop_request_path.write_text("{cut", encoding="utf-8")
    assert run.read_stop_request() is None


REQUEST = {"requested": FT, "close_games": True}


def test_the_recorder_claims_a_request_and_the_command_then_sees_it_taken(tmp_path):
    run = new_run(tmp_path, FT, 5)
    run.write_stop_request(REQUEST)
    assert run.claim_stop_request() == REQUEST
    assert not run.stop_request_path.exists() and run.stop_taken_path.name == "stop-request.taken.json"
    assert run.read_stop_request() is None and run.stop_taken_path.exists()
    assert run.withdraw_stop_request() == "taken"
    assert run.claim_stop_request() is None  # claimed once only


def test_a_request_the_command_withdrew_first_can_no_longer_be_claimed(tmp_path):
    run = new_run(tmp_path, FT, 5)
    run.write_stop_request(REQUEST)
    assert run.withdraw_stop_request() == "withdrawn"
    assert run.claim_stop_request() is None
    assert not run.stop_taken_path.exists() and not run.stop_request_path.exists()


def test_a_request_withdrawn_between_the_recorders_look_and_its_claim_is_not_claimed(tmp_path, monkeypatch):
    run = new_run(tmp_path, FT, 5)
    run.write_stop_request(REQUEST)
    real = runfiles.replace_with_retry

    def withdrawn_just_before(src, dst, **kwargs):
        assert run.withdraw_stop_request() == "withdrawn"
        real(src, dst, **kwargs)

    monkeypatch.setattr(runfiles, "replace_with_retry", withdrawn_just_before)
    assert run.claim_stop_request() is None
    assert not run.stop_taken_path.exists()


def test_an_unreadable_request_is_not_claimed_and_can_be_withdrawn(tmp_path):
    run = new_run(tmp_path, FT, 5)
    run.stop_request_path.write_text("{cut off", encoding="utf-8")
    assert run.claim_stop_request() is None
    assert run.stop_request_path.exists() and not run.stop_taken_path.exists()
    assert run.withdraw_stop_request() == "withdrawn"


def test_a_request_that_cannot_be_removed_is_held_not_withdrawn(tmp_path, monkeypatch):
    run = new_run(tmp_path, FT, 5)
    run.write_stop_request(REQUEST)

    def denied(self, missing_ok=False):
        raise PermissionError("in use")

    monkeypatch.setattr(runfiles.Path, "unlink", denied)
    assert run.withdraw_stop_request() == "held"
