"""Splits a finished run folder into per-session folders, then clears the capture.

The capture (and filter.pmc) is kept when any session couldn't be recovered: a session with a problem or a session
file that couldn't be read. split.json says so (`capture_kept`), and `--trace-split RUN` can split it again.
"""
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from tracing.clock import filetime_to_iso
from tracing.model import ProcInfo, RawEvent
from tracing.output import write_session
from tracing.runfiles import RunDir
from tracing.split import normalize_stop, split_run

log = logging.getLogger(__name__)


@dataclass
class SplitResult:
    folders: list[Path]
    problems: list[str]
    capture_kept: bool = False


def _clear_capture(run: RunDir) -> None:
    """Best effort: a file Process Monitor (or anything else) still holds stays, and is logged."""
    for path in run.capture_files() + [run.config_path]:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Could not remove %s after the split: %s", path, exc)


def _stop_summary(stop: dict | None) -> dict | None:
    """split.json's `stop`: None for a run that ended by itself, else when it was stopped and which sessions were
    still running."""
    if stop is None:
        return None
    return {"requested": None if stop["requested"] is None else filetime_to_iso(stop["requested"]),
            "time": filetime_to_iso(stop["time"]), "close_games": stop["close_games"],
            "sessions": sorted(stop["sessions"])}


def _write_marker(run: RunDir, folders: list[Path], problems: list[str], capture_kept: bool,
                  stop: dict | None) -> None:
    run.write_split({"folders": [f.name for f in folders], "problems": problems, "capture_kept": capture_kept,
                     "stop": _stop_summary(stop)})


def split_run_dir(run: RunDir, traces_dir: Path,
                  read_capture: Callable[[list[Path]], tuple[list[ProcInfo], Iterable[RawEvent]]]) -> SplitResult:
    sessions, problems = run.read_sessions_and_problems()
    stop = normalize_stop(run.read_stop())  # what the recorder wrote when --trace-stop ended the run, if it did
    folders: list[Path] = []
    if not sessions:
        keep = bool(problems)  # only unreadable session files: nothing to split, but keep the capture for them
        _write_marker(run, folders, problems, keep, stop)
        if not keep:
            _clear_capture(run)
        return SplitResult(folders, problems, keep)
    try:
        processes, events = read_capture(run.capture_files())
        for result in split_run(processes, events, sessions, stop):
            folders.append(write_session(result, traces_dir))
            if result.problem:
                problems.append(f"{result.info.session_id}: {result.problem}")
        keep = bool(problems)
        _write_marker(run, folders, problems, keep, stop)
    except BaseException:
        # Only the folders this call made go; the capture stays so a retry can run again.
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)
        run.split_path.unlink(missing_ok=True)
        raise
    if not keep:
        _clear_capture(run)
    return SplitResult(folders, problems, keep)
