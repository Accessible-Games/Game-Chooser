import enum
import subprocess
import sys

import pytest

import procmon_parser
from procmon_parser import RuleAction, consts

from tracing.kinds import classify
from tracing.procmon import CAPTURE_OPERATIONS, EXCLUDED_PROCESSES, write_filter_config


def test_filter_round_trips(tmp_path):
    path = tmp_path / "filter.pmc"
    write_filter_config(path)
    with open(path, "rb") as f:
        config = procmon_parser.load_configuration(f)
    rules = config["FilterRules"]
    include = [r.value for r in rules if r.action == RuleAction.INCLUDE]
    exclude = [r.value for r in rules if r.action == RuleAction.EXCLUDE]
    assert tuple(include) == CAPTURE_OPERATIONS
    assert tuple(exclude) == EXCLUDED_PROCESSES
    assert config["DestructiveFilter"] == 1


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
