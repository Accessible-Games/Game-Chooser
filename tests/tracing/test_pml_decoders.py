import logging
import struct
from io import BytesIO
from types import SimpleNamespace

import pytest
from procmon_parser import stream_logs_detail_format as detail_format
from procmon_parser.consts import FilesystemSetInformationOperation as Op

from tracing import pml
from tracing.pml import capture_raw_details, install_detail_handlers, win32_path

# Run 4 of the VM probe: SetRenameInformationFile, first 128 bytes of `io`.
REAL_RENAME_IO = bytes.fromhex(
    "01003a005c00570000000000000000004c0000005c003f003f005c0043003a005c00470061006d00650073005c005400720061"
    "006300650054006500730074005c00730061007600650073005c0073006c006f00740031002e00730061007600000000002280"
    "433a5c47616d65735c5472616365546573745c73617665735c73")
TARGET = r"C:\Games\TraceTest\saves\slot1.sav"
# VM probe run 5 (build 2), msedge.exe: SetRenameInformationEx, first 186 bytes of `io` (see ex-bytes.md).
REAL_RENAME_EX_IO = bytes.fromhex(
    "05000000ff7f00000000000000000000a60000005c003f003f005c0043003a005c00550073006500720073005c00670061006d00"
    "6500630068006f006f007300650072005c0041007000700044006100740061005c004c006f00630061006c005c004d0069006300"
    "72006f0073006f00660074005c0045006400670065005c005500730065007200200044006100740061005c00440065006600610075"
    "006c0074005c0050007200650066006500720065006e00630065007300")
REAL_RENAME_EX_TARGET = r"C:\Users\gamechooser\AppData\Local\Microsoft\Edge\User Data\Default\Preferences"
# VM probe run 5 (build 2): SetDispositionInformationEx, `io` as captured (Flags 3 = DELETE | POSIX_SEMANTICS).
REAL_DISPOSITION_EX_IO = bytes.fromhex("03000000a91e")
HANDLERS = detail_format.FilesystemSubOperationHandler


@pytest.fixture(autouse=True)
def fresh_handlers():
    saved = dict(HANDLERS)
    pml._warned.clear()
    install_detail_handlers()
    yield
    HANDLERS.clear()
    HANDLERS.update(saved)


def rename_record(name: str, flags: int = 1, flags_size: int = 1, root: int = 0, length: int | None = None) -> bytes:
    raw = name.encode("utf-16-le")
    head = flags.to_bytes(flags_size, "little").ljust(8, b"\xaa") + struct.pack("<QI", root, len(raw) if length is None else length)
    return head + raw


def call(op: Op, data: bytes, trailer: bytes = b"TRAILER"):
    io = BytesIO(data + trailer)
    event = SimpleNamespace(details={}, category="")
    HANDLERS[op](io, None, event, BytesIO(), None)
    return event, io


@pytest.mark.parametrize("op, data, expected", [
    (Op.SetRenameInformationFile, REAL_RENAME_IO, {"FileName": TARGET, "ReplaceIfExists": "True"}),
    (Op.SetRenameInformationEx, REAL_RENAME_EX_IO, {"FileName": REAL_RENAME_EX_TARGET, "ReplaceIfExists": "True"}),
    (Op.SetDispositionInformationEx, REAL_DISPOSITION_EX_IO, {"Delete": "True"}),
])
def test_details_decoded_from_real_bytes(op, data, expected):
    event, io = call(op, data)
    assert {key: event.details.get(key) for key in expected} == expected
    assert io.tell() == 0


def test_rename_ex_layout_uses_u32_flags():
    nt_name = r"\??\C:\Games\g\new.sav"
    for op in (Op.SetRenameInformationEx, Op.SetRenameInformationExBypassAccessCheck):
        event, _ = call(op, rename_record(nt_name, flags=0x3, flags_size=4))
        assert event.details["FileName"] == r"C:\Games\g\new.sav"
        assert event.details["ReplaceIfExists"] == "True"
    event, _ = call(Op.SetRenameInformationEx, rename_record(nt_name, flags=0x2, flags_size=4))
    assert event.details["ReplaceIfExists"] == "False"


def test_win32_path():
    assert win32_path(r"\??\C:\a\b") == r"C:\a\b"
    assert win32_path(r"\??\UNC\srv\share\f") == "\\\\srv\\share\\f"
    assert win32_path(r"relative\name") == r"relative\name"


def test_disposition_ex_delete_flag():
    for flags, expected in ((b"\x03\x00\x00\x00", "True"), (b"\x00\x00\x00\x00", "False"), (b"\x02\x00\x00\x00", "False")):
        event, _ = call(Op.SetDispositionInformationEx, flags)
        assert event.details["Delete"] == expected


def test_decoders_leave_the_stream_where_procmon_parser_expects():
    for op, data in ((Op.SetRenameInformationFile, REAL_RENAME_IO[:96]),
                     (Op.SetDispositionInformationEx, b"\x01\x00\x00\x00")):
        io = BytesIO(b"PREFIX" + data + b"NEXT")
        io.seek(6)
        HANDLERS[op](io, None, SimpleNamespace(details={}, category=""), BytesIO(), None)
        assert io.tell() == 6
        assert io.read(len(data)) == data and io.read() == b"NEXT"


def test_chained_raw_handler_still_runs():
    HANDLERS.clear()  # the probe's wrapper first, then the decoders
    capture_raw_details(["SetRenameInformationFile"])
    install_detail_handlers()
    event, _ = call(Op.SetRenameInformationFile, REAL_RENAME_IO)
    assert event.details["_raw"]["io"].startswith("01003a00")
    assert event.details["FileName"] == TARGET
    # and the other order: the probe wrapping the decoder
    HANDLERS.clear()
    install_detail_handlers()
    capture_raw_details(["SetRenameInformationFile"])
    event, _ = call(Op.SetRenameInformationFile, REAL_RENAME_IO)
    assert "_raw" in event.details and event.details["FileName"] == TARGET


def test_install_is_idempotent():
    first = {op: HANDLERS[op] for op in (Op.SetRenameInformationFile, Op.SetRenameInformationEx,
                                         Op.SetDispositionInformationEx)}
    install_detail_handlers()
    assert {op: HANDLERS[op] for op in first} == first
    capture_raw_details(["SetRenameInformationFile"])
    wrapped = HANDLERS[Op.SetRenameInformationFile]
    install_detail_handlers()
    assert HANDLERS[Op.SetRenameInformationFile] is wrapped
    event, _ = call(Op.SetRenameInformationFile, REAL_RENAME_IO)
    assert event.details["FileName"] == TARGET


@pytest.mark.parametrize("data", [
    b"", b"\x01" * 10,
    rename_record("abcd", length=500),     # name longer than the buffer
    rename_record("abcd", length=7),       # odd length
    rename_record("abcd", length=1 << 30),
])
def test_garbled_rename_leaves_filename_unset_and_warns_once(data, caplog):
    with caplog.at_level(logging.WARNING, logger="tracing.pml"):
        for _ in range(2):
            event, io = call(Op.SetRenameInformationFile, data, trailer=b"")
            assert "FileName" not in event.details
            assert io.tell() == 0
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_short_disposition_ex_does_not_raise(caplog):
    with caplog.at_level(logging.WARNING, logger="tracing.pml"):
        event, _ = call(Op.SetDispositionInformationEx, b"\x01", trailer=b"")
    assert "Delete" not in event.details

