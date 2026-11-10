"""Reading Process Monitor's PML files with procmon-parser into the tracing records.

procmon_parser is imported inside the functions that use it, so this module imports cleanly without it.
"""
import logging
from collections.abc import Iterable, Iterator
from itertools import chain
from pathlib import Path

from tracing.model import ProcInfo, RawEvent


def to_proc(process) -> ProcInfo:
    return ProcInfo(
        pid=process.pid, parent_pid=process.parent_pid, start=process.start_time,
        end=process.end_time or None, image_path=process.image_path, command_line=process.command_line,
        virtualized=bool(process.virtualized), is_64bit=bool(process.is_process_64bit))


def to_raw(event) -> RawEvent:
    from procmon_parser.consts import ProcessOperation
    from procmon_parser.logs import get_error_message
    operation = event.operation.name
    if isinstance(event.operation, ProcessOperation):
        operation = operation.replace("_", " ")  # Procmon's display name: Process_Start is "Process Start"
    return RawEvent(time=event.date_filetime, pid=event.process.pid, proc_start=event.process.start_time,
                    operation=operation, path=event.path, result=get_error_message(event.result),
                    details=event.details or {})


def prefer_ended(existing: ProcInfo, new: ProcInfo) -> ProcInfo:
    """Of two entries for one process (from files Process Monitor rolled over to), the one that saw it end.

    An earlier file lists a process as still running; only the file holding its exit has the end time.
    When both have one, the later end wins; when neither does, the first entry stays.
    """
    if new.end is not None and (existing.end is None or new.end > existing.end):
        return new
    return existing


def read_capture(paths: list[Path]) -> tuple[list[ProcInfo], Iterator[RawEvent]]:
    """Every process across the files (de-duplicated by key, see prefer_ended) and their events, file after file.

    The events are read lazily; each file stays open only while its events are being read.
    """
    from procmon_parser import ProcmonLogsReader
    install_detail_handlers()
    processes: dict[tuple[int, int], ProcInfo] = {}
    for path in paths:
        with open(path, "rb") as f:
            for process in ProcmonLogsReader(f, should_get_stacktrace=False).processes():
                info = to_proc(process)
                existing = processes.get(info.key)
                processes[info.key] = info if existing is None else prefer_ended(existing, info)

    def events_of(path: Path) -> Iterator[RawEvent]:
        with open(path, "rb") as f:
            for event in ProcmonLogsReader(f, should_get_stacktrace=False):
                yield to_raw(event)

    return list(processes.values()), chain.from_iterable(events_of(p) for p in paths)


RENAME_OPERATIONS = ("SetRenameInformationFile", "SetRenameInformationEx", "SetRenameInformationExBypassAccessCheck")
_RENAME_HEADER = 20  # flags (u8 or u32) + padding, u64 RootDirectory, u32 FileNameLength; the name follows
_MAX_NAME_BYTES = 65536
_DISPOSITION_DELETE = 0x1
_warned: set[str] = set()
_log = logging.getLogger(__name__)


def _warn_once(what: str, why: str) -> None:
    if what not in _warned:
        _warned.add(what)
        _log.warning("could not decode %s: %s", what, why)


_NT_PREFIX = "\\??\\"
_NT_UNC_PREFIX = "\\??\\UNC\\"


def win32_path(name: str) -> str:
    """An NT path as a Win32 path: strip the NT prefix, and turn the UNC form into a leading double backslash."""
    if name[:len(_NT_UNC_PREFIX)].upper() == _NT_UNC_PREFIX.upper():
        return "\\\\" + name[len(_NT_UNC_PREFIX):]
    if name.startswith(_NT_PREFIX):
        return name[len(_NT_PREFIX):]
    return name


def _rename_handler(operation_name: str, extended: bool, previous):
    def handler(io, metadata, event, details_io, extra_detail_io):
        try:
            position = io.tell()
            try:
                head = io.read(_RENAME_HEADER)
                length = int.from_bytes(head[16:20], "little") if len(head) == _RENAME_HEADER else -1
                name_bytes = io.read(length) if 0 <= length <= _MAX_NAME_BYTES else b""
            finally:
                io.seek(position)
            if len(head) < _RENAME_HEADER or length < 0 or length > _MAX_NAME_BYTES or length % 2                     or len(name_bytes) != length:
                _warn_once(operation_name, "the rename record is short or garbled")
            else:
                flags = int.from_bytes(head[0:4], "little") if extended else head[0]
                event.details["ReplaceIfExists"] = "True" if flags & 0x1 else "False"
                event.details["FileName"] = win32_path(name_bytes.decode("utf-16-le"))
        except Exception as error:  # a decoder never breaks the parse
            _warn_once(operation_name, repr(error))
        if previous is not None:
            previous(io, metadata, event, details_io, extra_detail_io)
    handler.gc_decoder = True
    handler.previous = previous
    return handler


def _disposition_ex_handler(previous):
    def handler(io, metadata, event, details_io, extra_detail_io):
        try:
            data = _peek(io, 4)
            if len(data) < 4:
                _warn_once("SetDispositionInformationEx", "the record is short")
            else:
                is_delete = bool(int.from_bytes(data, "little") & _DISPOSITION_DELETE)
                event.details["Delete"] = "True" if is_delete else "False"
                if is_delete:
                    event.category = "Write"
        except Exception as error:
            _warn_once("SetDispositionInformationEx", repr(error))
        if previous is not None:
            previous(io, metadata, event, details_io, extra_detail_io)
    handler.gc_decoder = True
    handler.previous = previous
    return handler


def _already_installed(handler) -> bool:
    """True if this handler, or any handler it chains to, is one of ours."""
    while handler is not None:
        if getattr(handler, "gc_decoder", False):
            return True
        handler = getattr(handler, "previous", None)
    return False


def install_detail_handlers() -> None:
    """Decoders for the details procmon-parser leaves out: rename targets and SetDispositionInformationEx.

    Idempotent. A handler already registered for an operation (such as the probe's `_raw` wrapper) is chained and
    still runs. Each decoder peeks at the stream and puts it back, so the parse carries on as procmon-parser expects.
    """
    from procmon_parser.consts import FilesystemSetInformationOperation
    from procmon_parser.stream_logs_detail_format import FilesystemSubOperationHandler
    for name in RENAME_OPERATIONS:
        operation = FilesystemSetInformationOperation[name]
        previous = FilesystemSubOperationHandler.get(operation)
        if not _already_installed(previous):
            FilesystemSubOperationHandler[operation] = _rename_handler(name, name != RENAME_OPERATIONS[0], previous)
    operation = FilesystemSetInformationOperation.SetDispositionInformationEx
    previous = FilesystemSubOperationHandler.get(operation)
    if not _already_installed(previous):
        FilesystemSubOperationHandler[operation] = _disposition_ex_handler(previous)


def capture_raw_details(operations: Iterable[str]) -> None:
    """For the probe only: keep the raw detail bytes of each named set-information operation in details["_raw"].

    The streams are peeked and put back where they were, so parsing carries on unchanged; an existing
    decoder for the operation still runs afterwards.
    """
    from procmon_parser.consts import FilesystemSetInformationOperation
    from procmon_parser.stream_logs_detail_format import FilesystemSubOperationHandler
    for name in operations:
        operation = FilesystemSetInformationOperation[name]
        FilesystemSubOperationHandler[operation] = _raw_handler(FilesystemSubOperationHandler.get(operation))


def _raw_handler(previous):
    def handler(io, metadata, event, details_io, extra_detail_io):
        event.details["_raw"] = {
            "io": _peek(io, 128).hex(),
            "details_io": details_io.getvalue().hex(),
            "extra": _peek(extra_detail_io, 1024).hex() if extra_detail_io is not None else None,
        }
        if previous is not None:
            previous(io, metadata, event, details_io, extra_detail_io)
    handler.previous = previous
    return handler


def _peek(stream, size: int) -> bytes:
    position = stream.tell()
    data = stream.read(size)
    stream.seek(position)
    return data
