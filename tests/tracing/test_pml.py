from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from procmon_parser import stream_logs_detail_format as detail_format
from procmon_parser.consts import FilesystemSetInformationOperation, ProcessOperation, RegistryOperation

from tracing.model import ProcInfo, RawEvent
from tracing.pml import capture_raw_details, prefer_ended, read_capture, to_proc, to_raw


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


def _info(end, start=1000):
    return ProcInfo(pid=10, parent_pid=4, start=start, end=end, image_path="g.exe", command_line="g.exe",
                    virtualized=False, is_64bit=True)


def test_prefer_ended_keeps_the_entry_with_the_latest_end():
    running, ended, later = _info(None), _info(2000), _info(3000)
    assert prefer_ended(running, ended) is ended
    assert prefer_ended(ended, running) is ended
    assert prefer_ended(ended, later) is later and prefer_ended(later, ended) is later
    assert prefer_ended(running, _info(None)) is running


def test_read_capture_merges_a_process_across_rolled_over_files(tmp_path, monkeypatch):
    import procmon_parser
    per_file = {
        "capture.pml": [_process(end_time=0), _process(pid=20, end_time=0)],
        "capture-1.pml": [_process(end_time=5000), _process(pid=20, end_time=0)],
        "capture-2.pml": [_process(end_time=0)],
    }

    class FakeReader:
        def __init__(self, f, should_get_stacktrace):
            self.name = Path(f.name).name

        def processes(self):
            return per_file[self.name]

        def __iter__(self):
            return iter([])

    monkeypatch.setattr(procmon_parser, "ProcmonLogsReader", FakeReader)
    files = []
    for name in per_file:
        (tmp_path / name).write_bytes(b"x")
        files.append(tmp_path / name)
    processes, events = read_capture(files)
    ends = {p.pid: p.end for p in processes}
    assert ends == {10: 5000, 20: None} and list(events) == []


@pytest.fixture
def restore_event_offsets():
    from procmon_parser import stream_logs_format
    saved = stream_logs_format.EventOffsetsArray
    yield stream_logs_format
    stream_logs_format.EventOffsetsArray = saved


def _offset_entries(offsets):
    """The capture's event offsets array: five bytes an event, little-endian."""
    return b"".join(offset.to_bytes(5, "little") for offset in offsets)


def test_read_capture_reads_event_offsets_past_4_gib(restore_event_offsets):
    """A capture over 4 GiB: the fifth byte of an entry is bits 32 to 39 of the offset, not flags to drop."""
    wanted = [0x3A8, 0xFFFFFFF0, 0x1_0000_0010, 0x1_3E8A_1234, 0xFF_FFFF_FFFF]
    data = _offset_entries(wanted)
    read_capture([])  # sets procmon-parser up the way every real read does
    offsets = restore_event_offsets.EventOffsetsArray(BytesIO(data + b"the process table"), len(data), len(wanted))
    assert list(offsets) == wanted
    assert offsets[2] == 0x1_0000_0010 and list(offsets[3:]) == wanted[3:]


def test_event_offsets_refuses_a_short_array(restore_event_offsets):
    data = _offset_entries([0x3A8, 0x1_0000_0010])
    read_capture([])
    with pytest.raises(ValueError, match="event offsets"):
        restore_event_offsets.EventOffsetsArray(BytesIO(data[:-1]), len(data), 2)
