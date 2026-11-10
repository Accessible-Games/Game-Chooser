import struct
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from procmon_parser import stream_logs_detail_format as detail_format
from procmon_parser import stream_logs_format
from procmon_parser.consts import FilesystemSetInformationOperation, ProcessOperation, RegistryOperation

from tracing.model import ProcInfo, RawEvent
from tracing.pml import capture_raw_details, install_event_offsets, prefer_ended, read_capture, to_proc, to_raw

# Taken when this file is imported, before any test can have run read_capture.
PARSER_EVENT_OFFSETS = stream_logs_format.EventOffsetsArray


@pytest.fixture(autouse=True)
def parser_event_offsets():
    """Every test starts and ends with procmon-parser's own offsets reader, whatever read_capture installed."""
    stream_logs_format.EventOffsetsArray = PARSER_EVENT_OFFSETS
    yield
    stream_logs_format.EventOffsetsArray = PARSER_EVENT_OFFSETS


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


def _offset_entries(offsets):
    """The capture's event offsets array: five bytes an event, little-endian."""
    return b"".join(offset.to_bytes(5, "little") for offset in offsets)


def test_read_capture_reads_event_offsets_past_4_gib():
    """A capture over 4 GiB: the fifth byte of an entry is bits 32 to 39 of the offset, not flags to drop."""
    wanted = [0x3A8, 0xFFFFFFF0, 0x1_0000_0010, 0x1_3E8A_1234, 0xFF_FFFF_FFFF]
    data = _offset_entries(wanted)
    read_capture([])  # sets procmon-parser up the way every real read does
    offsets = stream_logs_format.EventOffsetsArray(BytesIO(data + b"the process table"), len(data), len(wanted))
    assert list(offsets) == wanted
    assert offsets[2] == 0x1_0000_0010 and list(offsets[3:]) == wanted[3:]


def test_event_offsets_refuses_a_short_array():
    data = _offset_entries([0x3A8, 0x1_0000_0010])
    read_capture([])
    with pytest.raises(ValueError, match="event offsets"):
        stream_logs_format.EventOffsetsArray(BytesIO(data[:-1]), len(data), 2)


def test_install_event_offsets_refuses_a_parser_without_the_reader(monkeypatch):
    """A procmon-parser that reads offsets some other way must stop the read, not bring the 4 GiB failure back."""
    monkeypatch.delattr(stream_logs_format, "EventOffsetsArray")
    with pytest.raises(RuntimeError, match="EventOffsetsArray"):
        install_event_offsets()
    assert not hasattr(stream_logs_format, "EventOffsetsArray")


class SparseFile:
    """A file that holds bytes only at the given offsets, standing in for a capture over 4 GiB."""

    def __init__(self, parts: dict[int, bytes]):
        self.parts = parts
        self.position = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset
        return self.position

    def read(self, size=-1):
        for start, data in self.parts.items():
            if start <= self.position < start + len(data):
                at = self.position - start
                chunk = data[at:] if size < 0 else data[at:at + size]
                self.position += len(chunk)
                return chunk
        return b""


def _strings_table(strings):
    head = 4 + 4 * len(strings)
    offsets, body = [], b""
    for text in strings:
        raw = text.encode("utf-16-le")
        offsets.append(head + len(body))
        body += struct.pack("<I", len(raw)) + raw
    return struct.pack(f"<I{len(strings)}I", len(strings), *offsets) + body


def _capture_past_4_gib():
    """A one-event capture laid out as Process Monitor writes it, with everything after the header past 4 GiB.

    The event starts at 4 GiB + 0x100. Read as 32 bits that is 0x100, inside the header's zeros, which is how the
    real capture failed (process index 0, KeyError).
    """
    events_at = 2**32 + 0x100
    exe = r"C:\Games\g.exe"
    # process index 7, thread 44, class Process, operation Process_Exit, time 1500, 52 bytes of details
    event = stream_logs_format.CommonEventStruct.pack(7, 44, 1, 2, 0, 0, 0, 1500, 0, 0, 0, 52, 0)
    event += struct.pack("<I6Q", 3, 0, 0, 0, 0, 0, 0)  # exit status 3, then times and memory
    offsets = events_at.to_bytes(5, "little")
    # one process: the count, its index, where it starts in the table, then the process itself
    process = struct.pack("<III", 1, 7, 12)
    process += struct.pack("<III4xQI4xQQII", 7, 4321, 100, 0, 1, 1000, 2000, 0, 1)  # index, pid, parent ... 64-bit
    process += struct.pack("<8I", 0, 0, 1, 1, 1, 0, 0, 0)  # strings: name, image path and command line are the exe
    process += struct.pack("<IIQI", 0, 0, 0, 0)  # icons, an unknown pointer, no modules
    strings = _strings_table(["", exe])
    tables = bytes(8)  # no host names, no ports
    offsets_at = events_at + len(event)
    processes_at = offsets_at + len(offsets)
    strings_at = processes_at + len(process)
    tables_at = strings_at + len(strings)
    header = bytearray(stream_logs_format.Header.SIZE)
    header[0:4] = b"PML_"
    struct.pack_into("<II", header, 4, 9, 1)  # version, 64-bit
    struct.pack_into("<I", header, 0x234, 1)  # number of events
    struct.pack_into("<5Q", header, 0x240, events_at, offsets_at, processes_at, strings_at, tables_at)
    struct.pack_into("<QQ", header, 0x398, stream_logs_format.Header.SIZE, tables_at)
    return {0: bytes(header), events_at: event + offsets + process + strings + tables}


def test_read_capture_reads_an_event_stored_past_4_gib(monkeypatch):
    """Through procmon-parser's own reader: the event is found only if the parser uses our offsets."""
    parts = _capture_past_4_gib()
    monkeypatch.setattr("tracing.pml.open", lambda path, mode: SparseFile(parts), raising=False)
    processes, events = read_capture([Path("capture.pml")])
    assert processes == [ProcInfo(pid=4321, parent_pid=100, start=1000, end=2000, image_path=r"C:\Games\g.exe",
                                  command_line=r"C:\Games\g.exe", virtualized=False, is_64bit=True)]
    (event,) = list(events)
    assert (event.pid, event.proc_start, event.operation, event.time) == (4321, 1000, "Process Exit", 1500)
    assert event.details["Exit Status"] == 3
