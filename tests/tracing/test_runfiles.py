from tracing.clock import TICKS_PER_SECOND
from tracing.model import SessionInfo
from tracing.runfiles import RunDir, STATES, leftover_runs, new_run

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
