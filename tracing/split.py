"""Splits one system-wide capture into per-session lines, attributing events by process tree."""
import ntpath
from dataclasses import dataclass
from typing import Iterable

from tracing.attribution import find_root, session_tree
from tracing.clock import TICKS_PER_SECOND
from tracing.kinds import classify
from tracing.model import ProcInfo, RawEvent, SessionInfo
from tracing.virtualstore import virtual_store_path

MAX_VALUE_CHARS = 256
PROBLEM_NO_ROOT = "The game's first process isn't in the capture, so no events could be attributed to this session."

_ORDER = {"process_start": 0, "file_write": 1, "mark": 2, "process_exit": 3}


@dataclass
class SessionResult:
    info: SessionInfo
    root: ProcInfo | None
    time_zero: int
    time_zero_source: str
    tree: dict[tuple[int, int], ProcInfo]
    lines: list[dict]
    end: int | None
    still_running: bool
    problem: str | None


def _name(path: str) -> str:
    return ntpath.basename(path)


def _t(time: int, zero: int) -> float:
    return round((time - zero) / TICKS_PER_SECOND, 6)


def _value_text(data) -> str:
    if isinstance(data, (bytes, bytearray)):
        text = bytes(data).hex()
    elif isinstance(data, (list, tuple)):
        text = "\n".join(str(x) for x in data)
    else:
        text = "" if data is None else str(data)
    return text[:MAX_VALUE_CHARS]


def _length(details: dict) -> int:
    try:
        return int(details.get("Length", 0))
    except (TypeError, ValueError):
        return 0


def _write_line(kind: str, event: RawEvent, proc: ProcInfo, zero: int, info: SessionInfo) -> dict:
    local_appdata = info.env["local_appdata"]
    line = {"t": _t(event.time, zero), "op": kind, "process": _name(proc.image_path), "pid": event.pid,
            "path": event.path, "result": event.result}
    if kind == "rename":
        line["new_path"] = event.details.get("FileName")
    if kind == "reg_set_value":
        line["value_type"] = event.details.get("Type")
        line["value_data"] = _value_text(event.details.get("Data"))
    mapped = virtual_store_path(event.path, proc.virtualized, local_appdata)
    if mapped is not None:
        line["virtual_store_path"] = mapped
    if kind == "rename" and line["new_path"]:
        new_mapped = virtual_store_path(line["new_path"], proc.virtualized, local_appdata)
        if new_mapped is not None:
            line["new_virtual_store_path"] = new_mapped
    if kind == "file_write":
        line["bytes"] = _length(event.details)
        line["count"] = 1
    return line


def _merge_writes(lines: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for line in lines:
        prev = merged[-1] if merged else None
        if (line["op"] == "file_write" and prev is not None and prev["op"] == "file_write"
                and (prev["pid"], prev["path"], prev["result"]) == (line["pid"], line["path"], line["result"])):
            prev["count"] += line["count"]
            prev["bytes"] += line["bytes"]
            prev["t_end"] = line["t"]
        else:
            merged.append(line)
    return merged


def _process_lines(tree: dict[tuple[int, int], ProcInfo], zero: int, end: int | None) -> list[dict]:
    lines = []
    for proc in tree.values():
        lines.append({"t": _t(proc.start, zero), "op": "process_start", "pid": proc.pid,
                      "parent_pid": proc.parent_pid, "process": _name(proc.image_path),
                      "image_path": proc.image_path, "command_line": proc.command_line})
        if end is not None and proc.end is not None:
            lines.append({"t": _t(proc.end, zero), "op": "process_exit", "pid": proc.pid,
                          "process": _name(proc.image_path)})
    return lines


def split_run(processes: list[ProcInfo], events: Iterable[RawEvent],
              sessions: list[SessionInfo]) -> list[SessionResult]:
    results: list[SessionResult] = []
    owner: dict[tuple[int, int], int] = {}
    for index, info in enumerate(sessions):
        root = find_root(processes, info)
        if root is None:
            results.append(SessionResult(info, None, info.launch_before, "launch_call", {}, [], None, False,
                                         PROBLEM_NO_ROOT))
            continue
        tree = session_tree(processes, root)
        for key in tree:
            owner.setdefault(key, index)
        ends = [q.end for q in tree.values()]
        running = any(e is None for e in ends)
        end = None if running else max(ends)
        results.append(SessionResult(info, root, root.start, "process_start", tree, [], end, running, None))

    for event in events:
        index = owner.get(event.proc_key)
        if index is None:
            continue
        kind = classify(event.operation, event.result, event.details)
        if kind is None:
            continue
        result = results[index]
        proc = result.tree[event.proc_key]
        result.lines.append(_write_line(kind, event, proc, result.time_zero, result.info))

    for result in results:
        if result.root is None:
            continue
        zero = result.time_zero
        lines = result.lines + _process_lines(result.tree, zero, result.end)
        lines += [{"t": _t(m, zero), "op": "mark", "n": n} for n, m in enumerate(result.info.marks, 1)]
        lines.sort(key=lambda line: (line["t"], _ORDER.get(line["op"], 1)))
        result.lines = _merge_writes(lines)
    return results
