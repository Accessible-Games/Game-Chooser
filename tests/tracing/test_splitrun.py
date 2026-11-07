import dataclasses
import json

import pytest

from tests.tracing.test_split import CHILD, CHOOSER, ROOT, SEVENZ, ev, info
import tracing.splitrun as splitrun_module
from tracing.runfiles import new_run
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
    assert marker == {"folders": [folder.name], "problems": [], "capture_kept": False} and run.is_split()
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
        "folders": [], "problems": [], "capture_kept": False}
    assert not run.capture_path.exists() and not run.config_path.exists()


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
