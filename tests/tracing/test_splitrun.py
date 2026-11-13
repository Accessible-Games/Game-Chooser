import dataclasses
import json
from pathlib import Path

import pytest

from tests.tracing.test_split import CHILD, CHOOSER, ROOT, SEVENZ, ev, info
import tracing.splitrun as splitrun_module
from tracing.clock import filetime_to_iso
from tracing.runfiles import leftover_runs, new_run
from tracing.splitrun import SplitResult, split_run_dir

FT = 133_000_000_000_000_000


@pytest.fixture(autouse=True)
def plain_stamp(monkeypatch):
    # the shared fixture's times sit near year 1601, which Windows can't convert to local time
    monkeypatch.setattr("tracing.output.filetime_to_local_stamp", lambda ft: "stamp")


def make_run(tmp_path, with_session=True):
    run = new_run(tmp_path, FT, 9)
    run.capture_path.write_bytes(b"pml")
    run.config_path.write_bytes(b"pmc")
    if with_session:
        run.write_session(info())
    return run


def fake_read(files):
    return [CHOOSER, SEVENZ, ROOT, CHILD], iter([ev(ROOT, 12, Length=4)])


def test_split_writes_sessions_marker_and_deletes_capture(tmp_path):
    run = make_run(tmp_path)
    result = split_run_dir(run, tmp_path, fake_read)
    assert isinstance(result, SplitResult) and result.problems == []
    [folder] = result.folders
    assert (folder / "session.json").is_file() and (folder / "events.jsonl").is_file()
    marker = json.loads(run.split_path.read_text(encoding="utf-8"))
    assert marker == {"folders": [folder.name], "problems": [], "capture_kept": False, "stop": None}
    assert run.is_split()
    assert not run.capture_path.exists() and not run.config_path.exists()


def test_split_reports_problems_with_session_id(tmp_path):
    run = make_run(tmp_path, with_session=False)
    run.write_session(info(root_pid=999))
    result = split_run_dir(run, tmp_path, fake_read)
    assert len(result.problems) == 1 and result.problems[0].startswith("s: ")
    marker = json.loads(run.split_path.read_text(encoding="utf-8"))
    assert marker["problems"] == result.problems


def test_a_session_problem_keeps_the_capture(tmp_path):
    run = make_run(tmp_path, with_session=False)
    run.write_session(info(root_pid=999))
    result = split_run_dir(run, tmp_path, fake_read)
    assert result.capture_kept is True and len(result.folders) == 1
    assert json.loads(run.split_path.read_text(encoding="utf-8"))["capture_kept"] is True
    assert run.capture_path.exists() and run.config_path.exists()


def test_an_unreadable_session_file_keeps_the_capture(tmp_path):
    run = make_run(tmp_path)
    (run.sessions_dir / "s002-77.json").write_text("{cut off", encoding="utf-8")
    result = split_run_dir(run, tmp_path, fake_read)
    assert len(result.folders) == 1 and result.capture_kept is True
    [problem] = result.problems
    assert "s002-77.json" in problem
    marker = json.loads(run.split_path.read_text(encoding="utf-8"))
    assert marker["capture_kept"] is True and marker["problems"] == [problem]
    assert run.capture_path.exists() and run.config_path.exists()


def test_only_unreadable_session_files_keep_the_capture_without_reading_it(tmp_path):
    run = make_run(tmp_path, with_session=False)
    (run.sessions_dir / "s001-5.json").write_text("", encoding="utf-8")

    def never(files):
        raise AssertionError("nothing to split")

    result = split_run_dir(run, tmp_path, never)
    assert result.folders == [] and result.capture_kept is True and "s001-5.json" in result.problems[0]
    assert json.loads(run.split_path.read_text(encoding="utf-8"))["capture_kept"] is True
    assert run.capture_path.exists() and run.config_path.exists()


def test_a_held_capture_file_does_not_fail_a_good_split(tmp_path, monkeypatch, caplog):
    run = make_run(tmp_path)
    real_unlink = splitrun_module.Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == "capture.pml":
            raise PermissionError("Process Monitor still has it")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(splitrun_module.Path, "unlink", unlink)
    result = split_run_dir(run, tmp_path, fake_read)
    assert result.problems == [] and result.capture_kept is False
    assert run.capture_path.exists() and not run.config_path.exists()
    assert "capture.pml" in caplog.text


def test_split_failure_keeps_capture(tmp_path):
    run = make_run(tmp_path)

    def broken(files):
        raise ValueError("bad capture")

    with pytest.raises(ValueError):
        split_run_dir(run, tmp_path, broken)
    assert run.capture_path.exists() and run.config_path.exists() and not run.split_path.exists()


def test_no_sessions_just_finishes(tmp_path):
    run = make_run(tmp_path, with_session=False)

    def never(files):
        raise AssertionError("must not read the capture")

    result = split_run_dir(run, tmp_path, never)
    assert result.folders == [] and result.problems == []
    assert json.loads(run.split_path.read_text(encoding="utf-8")) == {
        "folders": [], "problems": [], "capture_kept": False, "stop": None}
    assert not run.capture_path.exists() and not run.config_path.exists()


def test_a_split_json_cut_off_while_written_leaves_the_run_unsplit(tmp_path, monkeypatch):
    run = make_run(tmp_path, with_session=False)
    write_text = Path.write_text

    def killed_halfway(path, text, *args, **kwargs):  # the recorder dies with split.json half written
        if path.name.startswith("split.json"):
            write_text(path, text[:len(text) // 2], *args, **kwargs)
            raise KeyboardInterrupt
        return write_text(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", killed_halfway)
    with pytest.raises(KeyboardInterrupt):
        split_run_dir(run, tmp_path, fake_read)
    monkeypatch.undo()
    assert not run.is_split() and not list(run.path.glob("split.json.*.tmp"))
    assert [r.path for r in leftover_runs(tmp_path, lambda pid, start: False)] == [run.path]


def test_failed_write_removes_this_calls_folders_and_retry_has_no_suffix(tmp_path, monkeypatch):
    run = make_run(tmp_path)
    first = info()
    second = dataclasses.replace(first, session_id="t", game={**first.game, "catalog_id": "other"})
    run.write_session(second)
    real = splitrun_module.write_session
    calls = []

    def flaky(result, traces_dir):
        calls.append(result.info.session_id)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(result, traces_dir)

    monkeypatch.setattr(splitrun_module, "write_session", flaky)
    with pytest.raises(OSError):
        split_run_dir(run, tmp_path, fake_read)
    assert [p.name for p in tmp_path.iterdir()] == ["_runs"]
    assert run.capture_path.exists() and run.config_path.exists() and not run.split_path.exists()
    monkeypatch.setattr(splitrun_module, "write_session", real)
    result = split_run_dir(run, tmp_path, fake_read)
    assert sorted(f.name for f in result.folders) == ["c-stamp", "other-stamp"]


def test_split_json_has_no_stop_for_a_run_that_ended_by_itself(tmp_path):
    run = make_run(tmp_path)
    result = split_run_dir(run, tmp_path, fake_read)
    assert json.loads(run.split_path.read_text(encoding="utf-8"))["stop"] is None
    data = json.loads((result.folders[0] / "session.json").read_text(encoding="utf-8"))
    assert data["ended_by"] == "capture_end" and data["stop"] is None  # CHILD has no exit in the fixture


def test_a_stopped_run_marks_its_sessions_and_split_json(tmp_path):
    run = make_run(tmp_path)
    lists = {"asked_to_close": [100], "ended": [200], "not_closed": []}
    run.write_stop({"requested": FT - 10_000_000, "time": FT, "close_games": True,
                    "sessions": {"s": lists, "gone": {}}})
    result = split_run_dir(run, tmp_path, fake_read)
    assert result.problems == [] and result.capture_kept is False  # a stop is not a problem
    marker = json.loads(run.split_path.read_text(encoding="utf-8"))
    assert marker["stop"] == {"requested": filetime_to_iso(FT - 10_000_000), "time": filetime_to_iso(FT),
                              "close_games": True, "sessions": ["gone", "s"]}
    data = json.loads((result.folders[0] / "session.json").read_text(encoding="utf-8"))
    assert data["ended_by"] == "stop"
    assert data["stop"]["time"] == filetime_to_iso(FT) and data["stop"]["close_games"] is True
    assert data["stop"]["asked_to_close"] == [100] and data["stop"]["ended"] == [200]


def test_a_stopped_run_with_no_sessions_still_says_stop(tmp_path):
    run = make_run(tmp_path, with_session=False)
    run.write_stop({"requested": None, "time": FT, "close_games": False, "sessions": {}})
    split_run_dir(run, tmp_path, fake_read)
    assert json.loads(run.split_path.read_text(encoding="utf-8")) == {
        "folders": [], "problems": [], "capture_kept": False,
        "stop": {"requested": None, "time": filetime_to_iso(FT), "close_games": False, "sessions": []}}


def test_a_request_the_recorder_never_took_is_not_a_stop(tmp_path):
    run = make_run(tmp_path)
    run.write_stop_request({"requested": FT, "close_games": True})
    result = split_run_dir(run, tmp_path, fake_read)
    assert json.loads(run.split_path.read_text(encoding="utf-8"))["stop"] is None
    data = json.loads((result.folders[0] / "session.json").read_text(encoding="utf-8"))
    assert data["ended_by"] == "capture_end"
