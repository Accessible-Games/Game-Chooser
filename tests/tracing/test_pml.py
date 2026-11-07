from io import BytesIO
from types import SimpleNamespace

import pytest
from procmon_parser import stream_logs_detail_format as detail_format
from procmon_parser.consts import FilesystemSetInformationOperation, ProcessOperation, RegistryOperation

from tracing.model import ProcInfo, RawEvent
from tracing.pml import capture_raw_details, to_proc, to_raw


def _process(**kw):
    values = dict(pid=10, parent_pid=4, start_time=1000, end_time=2000, image_path=r"C:\Games\g.exe",
                  command_line='"C:\\Games\\g.exe"', virtualized=0, is_process_64bit=True)
    values.update(kw)
    return SimpleNamespace(**values)


def _event(operation, result=0, details=None, path=r"HKCU\Software\Game"):
    return SimpleNamespace(process=_process(), operation=operation, result=result, details=details,
                           path=path, date_filetime=1500)


def test_to_raw_names_and_results():
    raw = to_raw(_event(RegistryOperation.RegSetValue, details={"Type": "REG_SZ"}))
    assert raw == RawEvent(time=1500, pid=10, proc_start=1000, operation="RegSetValue",
                           path=r"HKCU\Software\Game", result="SUCCESS", details={"Type": "REG_SZ"})
    started = to_raw(_event(ProcessOperation.Process_Start, details=None, path=""))
    assert started.operation == "Process Start"
    assert started.details == {}
    assert to_raw(_event(ProcessOperation.Process_Exit)).operation == "Process Exit"
    assert to_raw(_event(RegistryOperation.RegSetValue, result=0xC0000022)).result != "SUCCESS"


def test_to_proc_zero_end_is_none():
    proc = to_proc(_process(end_time=0))
    assert proc == ProcInfo(pid=10, parent_pid=4, start=1000, end=None, image_path=r"C:\Games\g.exe",
                            command_line='"C:\\Games\\g.exe"', virtualized=False, is_64bit=True)
    assert to_proc(_process(end_time=2000, virtualized=1)).end == 2000
    assert to_proc(_process(virtualized=1)).virtualized is True


@pytest.fixture
def restore_handlers():
    saved = dict(detail_format.FilesystemSubOperationHandler)
    yield
    detail_format.FilesystemSubOperationHandler.clear()
    detail_format.FilesystemSubOperationHandler.update(saved)


def test_raw_capture_handler_peeks_without_consuming(restore_handlers):
    capture_raw_details(["SetRenameInformationEx", "SetDispositionInformationEx"])
    op = FilesystemSetInformationOperation.SetRenameInformationEx
    handler = detail_format.FilesystemSubOperationHandler[op]
    io = BytesIO(bytes(range(200)))
    io.seek(7)
    details_io = BytesIO(b"\x01\x02\x03")
    details_io.seek(1)
    extra = BytesIO(b"\xaa" * 2000)
    extra.seek(3)
    event = SimpleNamespace(operation=op, details={})
    handler(io, None, event, details_io, extra)
    assert (io.tell(), details_io.tell(), extra.tell()) == (7, 1, 3)
    raw = event.details["_raw"]
    assert raw["io"] == bytes(range(7, 135)).hex()
    assert raw["details_io"] == "010203"
    assert raw["extra"] == ("aa" * 1024)

    event = SimpleNamespace(operation=FilesystemSetInformationOperation.SetDispositionInformationEx, details={})
    detail_format.FilesystemSubOperationHandler[event.operation](BytesIO(b"\x01"), None, event, BytesIO(), None)
    assert event.details["_raw"] == {"io": "01", "details_io": "", "extra": None}


def test_raw_capture_keeps_an_existing_handler(restore_handlers):
    capture_raw_details(["SetDispositionInformationFile"])
    op = FilesystemSetInformationOperation.SetDispositionInformationFile
    event = SimpleNamespace(operation=op, details={}, category="")
    io = BytesIO(b"\x01\x00\x00\x00rest")
    detail_format.FilesystemSubOperationHandler[op](io, None, event, BytesIO(), None)
    assert event.details["Delete"] == "True"  # procmon-parser's own decoder still ran
    assert event.details["_raw"]["io"] == b"\x01\x00\x00\x00rest".hex()
    assert io.tell() == 4  # where procmon-parser's decoder leaves it
