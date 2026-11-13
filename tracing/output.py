"""Writes one session's folder: session.json and events.jsonl."""
import json
import re
import shutil
from pathlib import Path

from tracing.clock import filetime_to_iso, filetime_to_local_stamp
from tracing.model import SessionInfo
from tracing.split import SessionResult, _t

KNOWN_LIMITS = [
    "Work a game hands to a process outside its own process tree (a COM server started by Windows, a service, "
    "the browser) is not attributed to the session.",
    "Writes made through memory-mapped files can be flushed by the System process and then are not attributed "
    "to the session.",
]

_NON_EVENT_OPS = ("process_start", "process_exit", "mark")


def safe_name(title: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-.")[:60].strip("-.")
    return name or "game"


def session_folder_name(info: SessionInfo) -> str:
    catalog_id = info.game["catalog_id"]
    name = safe_name(catalog_id) if catalog_id else safe_name(info.game["title"])
    return f"{name}-{filetime_to_local_stamp(info.launch_before)}"


def _process_entry(proc, zero: int) -> dict:
    return {"pid": proc.pid, "parent_pid": proc.parent_pid, "image_path": proc.image_path,
            "command_line": proc.command_line,
            "start": filetime_to_iso(proc.start),
            "end": None if proc.end is None else filetime_to_iso(proc.end),
            "start_t": _t(proc.start, zero), "end_t": None if proc.end is None else _t(proc.end, zero),
            "virtualized": proc.virtualized, "is_64bit": proc.is_64bit}


def _stop_entry(stop: dict | None, zero: int) -> dict | None:
    if stop is None:
        return None
    return {"time": filetime_to_iso(stop["time"]), "t": _t(stop["time"], zero), "close_games": stop["close_games"],
            "asked_to_close": list(stop["asked_to_close"]), "ended": list(stop["ended"]),
            "not_closed": [dict(item) for item in stop["not_closed"]]}


def _session_json(result: SessionResult) -> dict:
    info, zero = result.info, result.time_zero
    return {
        "format": 1, "session_id": info.session_id, "game": dict(info.game), "env": dict(info.env),
        "start": filetime_to_iso(zero),
        "end": None if result.end is None else filetime_to_iso(result.end),
        "still_running_at_capture_end": result.still_running,
        "ended_by": result.ended_by, "stop": _stop_entry(result.stop, zero),
        "time_zero_source": result.time_zero_source,
        # the root found in the capture (by exe when the launch gave no pid), else the pid recorded at launch
        "root_pid": result.root.pid if result.root is not None else info.root_pid,
        "processes": [_process_entry(p, zero) for p in result.tree.values()],
        "marks": [{"n": n, "t": _t(m, zero), "time": filetime_to_iso(m)} for n, m in enumerate(info.marks, 1)],
        "event_count": sum(1 for line in result.lines if line["op"] not in _NON_EVENT_OPS),
        "problem": result.problem, "known_limits": list(KNOWN_LIMITS),
    }


def _dumps(value, **kwargs) -> str:
    """Raw UTF-8 text where possible; \\udXXX escapes when a path holds an unpaired surrogate."""
    text = json.dumps(value, ensure_ascii=False, **kwargs)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = json.dumps(value, ensure_ascii=True, **kwargs)
    return text


def write_session(result: SessionResult, traces_dir: Path) -> Path:
    base = session_folder_name(result.info)
    folder = Path(traces_dir) / base
    number = 2
    while folder.exists():
        folder = Path(traces_dir) / f"{base}-{number}"
        number += 1
    folder.mkdir(parents=True)
    try:
        (folder / "session.json").write_text(_dumps(_session_json(result), indent=2), encoding="utf-8")
        with open(folder / "events.jsonl", "w", encoding="utf-8", newline="\n") as out:
            for line in result.lines:
                out.write(_dumps(line) + "\n")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder
