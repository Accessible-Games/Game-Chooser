import enum
import subprocess
import sys

import pytest

import procmon_parser
from procmon_parser import Column, RuleAction, RuleRelation, consts

from tracing.kinds import classify
from tracing.procmon import (CAPTURE_OPERATIONS, EXCLUDED_PROCESSES, READ_ONLY_ACCESS, describe_filter,
                             filter_config, write_filter_config)


def test_filter_round_trips(tmp_path):
    path = tmp_path / "filter.pmc"
    write_filter_config(path)
    with open(path, "rb") as f:
        config = procmon_parser.load_configuration(f)
    rules = config["FilterRules"]
    include = [r.value for r in rules if r.action == RuleAction.INCLUDE]
    exclude = [r.value for r in rules if r.action == RuleAction.EXCLUDE and r.column == Column.PROCESS_NAME]
    assert tuple(include) == CAPTURE_OPERATIONS
    assert tuple(exclude) == EXCLUDED_PROCESSES
    assert config["DestructiveFilter"] == 1


def _detail_excludes(rules):
    return [(r.relation, r.value) for r in rules if r.action == RuleAction.EXCLUDE and r.column == Column.DETAIL]


def test_detail_excludes_round_trip(tmp_path):
    path = tmp_path / "filter.pmc"
    write_filter_config(path)
    with open(path, "rb") as f:
        config = procmon_parser.load_configuration(f)
    got = _detail_excludes(config["FilterRules"])
    for acc in READ_ONLY_ACCESS:
        assert (RuleRelation.BEGINS_WITH, f"Desired Access: {acc}, Disposition: Open,") in got
    assert (RuleRelation.CONTAINS, "Disposition: REG_OPENED_EXISTING_KEY") in got
    assert (RuleRelation.IS, "Delete: False") in got
    assert len(got) == len(READ_ONLY_ACCESS) + 2


def test_system_processes_excluded_and_launchers_kept():
    for name in ("svchost.exe", "MoUsoCoreWorker.exe", "MsMpEng.exe", "claude.exe", "nvda.exe", "Registry",
                 "game-chooser.exe", "7z.exe"):
        assert name in EXCLUDED_PROCESSES
    for name in ("explorer.exe", "conhost.exe", "cmd.exe", "powershell.exe", "python.exe", "rundll32.exe"):
        assert name not in EXCLUDED_PROCESSES


def _dropped(detail):
    """Whether Process Monitor would drop an event with this Detail (case-insensitive, as it matches)."""
    d = detail.lower()
    for relation, value in _detail_excludes(filter_config()["FilterRules"]):
        v = value.lower()
        if (relation == RuleRelation.BEGINS_WITH and d.startswith(v)) or                 (relation == RuleRelation.CONTAINS and v in d) or (relation == RuleRelation.IS and d == v):
            return True
    return False


def test_read_only_access_strings_hold_no_write_right():
    for acc in READ_ONLY_ACCESS:
        assert not any(bad in acc for bad in ("Write", "Append", "Delete", "All", "Maximum")), acc


def test_nothing_a_write_kind_needs_is_dropped():
    for acc in READ_ONLY_ACCESS:
        for opened in ("Opened", None):
            details = {"Desired Access": acc, "Disposition": "Open", "Options": "Non-Directory File"}
            if opened:
                details["OpenResult"] = opened
            for result in ("SUCCESS", "NAME NOT FOUND"):
                assert classify("CreateFile", result, details) is None, (acc, result)
    assert classify("RegCreateKey", "SUCCESS", {"Disposition": "REG_OPENED_EXISTING_KEY"}) is None
    assert classify("SetDispositionInformationFile", "SUCCESS", {"Delete": "False"}) is None


def test_write_opens_are_kept_by_the_filter():
    kept = (
        "Desired Access: Delete, Disposition: Open, Options: Non-Directory File, Delete On Close, Attributes: n/a, "
        "ShareMode: Delete, AllocationSize: n/a, OpenResult: Opened",
        "Desired Access: Generic Write, Read Attributes, Disposition: OverwriteIf, Options: Non-Directory File, "
        "Attributes: N, ShareMode: None, AllocationSize: 0, OpenResult: Overwritten",
        "Desired Access: Generic Read/Write, Disposition: Open, Options: Non-Directory File, Attributes: N, "
        "ShareMode: Read, AllocationSize: n/a, OpenResult: Opened",
        "Delete: True",
        "Desired Access: Read Attributes, Disposition: Create, Options: Non-Directory File, OpenResult: Created",
    )
    for detail in kept:
        assert not _dropped(detail), detail
    assert _dropped("Desired Access: Read Attributes, Disposition: Open, Options: Non-Directory File")
    assert _dropped("Delete: False")


def test_describe_filter_names_everything_dropped():
    lines = describe_filter()
    text = " ".join(lines)
    assert "MoUsoCoreWorker.exe" in text and "CreateFile" in text
    assert f"Desired Access: {READ_ONLY_ACCESS[0]}, Disposition: Open," in text
    assert "REG_OPENED_EXISTING_KEY" in text and "Delete: False" in text


def _every_operation_name():
    names = set()
    for value in vars(consts).values():
        if isinstance(value, type) and issubclass(value, consts.Operation) and issubclass(value, enum.Enum):
            for member in value:
                names.add(getattr(member, "csv_name", member.name))
    return names


_DETAILS = ({}, {"OpenResult": "Created"}, {"OpenResult": "Overwritten"}, {"Delete": "True"},
            {"Disposition": "REG_CREATED_NEW_KEY"}, {"Disposition": "Create"},
            {"Options": "Delete On Close"})
_RESULTS = ("SUCCESS", "ACCESS DENIED")


def test_capture_operations_cover_every_write_kind():
    names = _every_operation_name()
    assert "CreateFile" in names and "Process Start" in names  # the enumeration found the real operations
    writing = {op for op in names for details in _DETAILS for result in _RESULTS
               if classify(op, result, details) is not None}
    assert {"CreateFile", "WriteFile", "SetRenameInformationEx", "SetDispositionInformationEx",
            "RegSetValue", "RegCreateKey"} <= writing
    assert writing <= set(CAPTURE_OPERATIONS)


@pytest.mark.windows
def test_job_ends_the_process_when_closed():
    from tracing.procmon import _kill_on_close_job
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        job = _kill_on_close_job(process.pid)
        assert process.poll() is None
        job.Close()
        assert process.wait(10) is not None
    finally:
        if process.poll() is None:
            process.kill()


def test_helper_runs_accept_the_eula_quietly(tmp_path, monkeypatch):
    import tracing.procmon as procmon_module
    from tracing.procmon import WindowsProcmon
    calls = []

    def fake_run(argv, timeout=None):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(procmon_module.subprocess, "run", fake_run)
    exe = tmp_path / "Procmon64.exe"
    control = WindowsProcmon(exe)
    control._backing = tmp_path / "capture.pml"
    control._backing.write_bytes(b"")
    assert control.wait_ready(5)
    assert control.stop(5)
    assert calls == [[str(exe), "/AcceptEula", "/Quiet", "/WaitForIdle"],
                     [str(exe), "/AcceptEula", "/Quiet", "/Terminate"]]


class _FakeProcess:
    def __init__(self, exit_code=None):
        self.exit_code = exit_code

    def poll(self):
        return self.exit_code


def _ready_control(tmp_path, monkeypatch, codes, backing_after=0, process=None, step=0.5):
    """A WindowsProcmon whose helper returns `codes` in turn (the last repeats); the fake clock moves on each sleep."""
    import tracing.procmon as procmon_module
    from tracing.procmon import WindowsProcmon
    now = [0.0]
    calls = []

    def fake_run(argv, timeout=None):
        calls.append(timeout)
        if len(calls) >= backing_after:
            backing.write_bytes(b"")
        return subprocess.CompletedProcess(argv, codes[min(len(calls), len(codes)) - 1])

    def fake_sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr(procmon_module.subprocess, "run", fake_run)
    backing = tmp_path / "capture.pml"
    control = WindowsProcmon(tmp_path / "Procmon64.exe", clock=lambda: now[0], sleep=fake_sleep)
    control._backing = backing
    control._process = process
    return control, calls


def test_wait_ready_retries_a_failing_helper_until_it_succeeds(tmp_path, monkeypatch):
    control, calls = _ready_control(tmp_path, monkeypatch, [3, 3, 0])
    assert control.wait_ready(60)
    assert len(calls) == 3
    assert control.not_ready_reason is None


def test_wait_ready_waits_for_the_capture_file(tmp_path, monkeypatch):
    control, calls = _ready_control(tmp_path, monkeypatch, [0], backing_after=3)
    # Helper succeeds each time but the file only exists from the third call on; the check precedes creation.
    assert control.wait_ready(60)
    assert len(calls) == 3


def test_wait_ready_stops_at_once_when_the_capture_exits(tmp_path, monkeypatch):
    control, calls = _ready_control(tmp_path, monkeypatch, [3], process=_FakeProcess(1))
    assert not control.wait_ready(60)
    assert calls == []
    assert "exited (code 1)" in control.not_ready_reason


def test_wait_ready_gives_up_at_the_deadline_and_says_why(tmp_path, monkeypatch):
    control, calls = _ready_control(tmp_path, monkeypatch, [3])
    assert not control.wait_ready(5)
    assert len(calls) == 10
    assert "no answer within 5 s" in control.not_ready_reason
    assert "exited with code 3" in control.not_ready_reason
