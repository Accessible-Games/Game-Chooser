"""Reading Process Monitor's PML files with procmon-parser into the tracing records.

procmon_parser is imported inside the functions that use it, so this module imports cleanly without it.
"""
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


def install_detail_handlers() -> None:
    """Decoders for the details procmon-parser leaves out (rename targets, SetDispositionInformationEx).

    A placeholder until the probe's findings show the byte layouts (Task 8). It must be idempotent and must chain any
    handler already registered for an operation (such as the probe's `_raw` wrappers) rather than replace it.
    """


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
    return handler


def _peek(stream, size: int) -> bytes:
    position = stream.tell()
    data = stream.read(size)
    stream.seek(position)
    return data
