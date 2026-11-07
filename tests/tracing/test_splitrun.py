import json

import pytest

from tests.tracing.test_split import CHILD, CHOOSER, ROOT, SEVENZ, ev, info
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
    assert marker == {"folders": [folder.name], "problems": []} and run.is_split()
    assert not run.capture_path.exists() and not run.config_path.exists()


def test_split_reports_problems_with_session_id(tmp_path):
    run = make_run(tmp_path, with_session=False)
    run.write_session(info(root_pid=999))
    result = split_run_dir(run, tmp_path, fake_read)
    assert len(result.problems) == 1 and result.problems[0].startswith("s: ")
    assert json.loads(run.split_path.read_text(encoding="utf-8"))["problems"] == result.problems


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
    assert json.loads(run.split_path.read_text(encoding="utf-8")) == {"folders": [], "problems": []}
    assert not run.capture_path.exists()
