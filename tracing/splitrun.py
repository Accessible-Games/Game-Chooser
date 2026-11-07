"""Splits a finished run folder into per-session folders, then clears the capture."""
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from tracing.model import ProcInfo, RawEvent
from tracing.output import write_session
from tracing.runfiles import RunDir
from tracing.split import split_run


@dataclass
class SplitResult:
    folders: list[Path]
    problems: list[str]


def _clear_capture(run: RunDir) -> None:
    for path in run.capture_files() + [run.config_path]:
        path.unlink(missing_ok=True)


def split_run_dir(run: RunDir, traces_dir: Path,
                  read_capture: Callable[[list[Path]], tuple[list[ProcInfo], Iterable[RawEvent]]]) -> SplitResult:
    sessions = run.read_sessions()
    if not sessions:
        run.split_path.write_text(json.dumps({"folders": [], "problems": []}), encoding="utf-8")
        _clear_capture(run)
        return SplitResult([], [])
    folders: list[Path] = []
    problems: list[str] = []
    try:
        processes, events = read_capture(run.capture_files())
        for result in split_run(processes, events, sessions):
            folders.append(write_session(result, traces_dir))
            if result.problem:
                problems.append(f"{result.info.session_id}: {result.problem}")
        run.split_path.write_text(json.dumps({"folders": [f.name for f in folders], "problems": problems}),
                                  encoding="utf-8")
    except BaseException:
        # Only the folders this call made go; the capture stays so a retry can run again.
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)
        run.split_path.unlink(missing_ok=True)
        raise
    _clear_capture(run)
    return SplitResult(folders, problems)
