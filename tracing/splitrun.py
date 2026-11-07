"""Splits a finished run folder into per-session folders, then clears the capture.

The capture (and filter.pmc) is kept when any session couldn't be recovered: a session with a problem or a session
file that couldn't be read. split.json says so (`capture_kept`), and `--trace-split RUN` can split it again.
"""
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from tracing.model import ProcInfo, RawEvent
from tracing.output import write_session
from tracing.runfiles import RunDir
from tracing.split import split_run

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


def _write_marker(run: RunDir, folders: list[Path], problems: list[str], capture_kept: bool) -> None:
    run.split_path.write_text(json.dumps({"folders": [f.name for f in folders], "problems": problems,
                                          "capture_kept": capture_kept}), encoding="utf-8")


def split_run_dir(run: RunDir, traces_dir: Path,
                  read_capture: Callable[[list[Path]], tuple[list[ProcInfo], Iterable[RawEvent]]]) -> SplitResult:
    sessions, problems = run.read_sessions_and_problems()
    folders: list[Path] = []
    if not sessions:
        keep = bool(problems)  # only unreadable session files: nothing to split, but keep the capture for them
        _write_marker(run, folders, problems, keep)
        if not keep:
            _clear_capture(run)
        return SplitResult(folders, problems, keep)
    try:
        processes, events = read_capture(run.capture_files())
        for result in split_run(processes, events, sessions):
            folders.append(write_session(result, traces_dir))
            if result.problem:
                problems.append(f"{result.info.session_id}: {result.problem}")
        keep = bool(problems)
        _write_marker(run, folders, problems, keep)
    except BaseException:
        # Only the folders this call made go; the capture stays so a retry can run again.
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)
        run.split_path.unlink(missing_ok=True)
        raise
    if not keep:
        _clear_capture(run)
    return SplitResult(folders, problems, keep)
