"""Splits a finished run folder into per-session folders, then clears the capture."""
import json
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


def _finish(run: RunDir, folders: list[Path], problems: list[str]) -> SplitResult:
    run.split_path.write_text(json.dumps({"folders": [f.name for f in folders], "problems": problems}),
                              encoding="utf-8")
    for path in run.capture_files() + [run.config_path]:
        path.unlink(missing_ok=True)
    return SplitResult(folders, problems)


def split_run_dir(run: RunDir, traces_dir: Path,
                  read_capture: Callable[[list[Path]], tuple[list[ProcInfo], Iterable[RawEvent]]]) -> SplitResult:
    sessions = run.read_sessions()
    if not sessions:
        return _finish(run, [], [])
    processes, events = read_capture(run.capture_files())
    results = split_run(processes, events, sessions)
    folders, problems = [], []
    for result in results:
        folders.append(write_session(result, traces_dir))
        if result.problem:
            problems.append(f"{result.info.session_id}: {result.problem}")
    return _finish(run, folders, problems)
