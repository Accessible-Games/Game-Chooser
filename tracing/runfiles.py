"""A run folder: one recorder run's capture, status and session files under traces/_runs/."""
import json
import logging
import os
import re
from pathlib import Path
from typing import Callable

from tracing.clock import filetime_to_iso, filetime_to_local_stamp, now_filetime
from tracing.model import SessionInfo

log = logging.getLogger(__name__)

STATES = ("starting", "capturing", "failed", "stopping", "splitting", "done")

_CAPTURE_RE = re.compile(r"^capture(?:-(\d+))?\.pml$")


def _atomic_write(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


class RunDir:
    def __init__(self, path: Path):
        self.path = Path(path)

    @property
    def status_path(self) -> Path:
        return self.path / "status.json"

    @property
    def sessions_dir(self) -> Path:
        return self.path / "sessions"

    @property
    def config_path(self) -> Path:
        return self.path / "filter.pmc"

    @property
    def capture_path(self) -> Path:
        return self.path / "capture.pml"

    @property
    def log_path(self) -> Path:
        return self.path / "recorder.log"

    @property
    def split_path(self) -> Path:
        return self.path / "split.json"

    def capture_files(self) -> list[Path]:
        found = []
        for item in self.path.glob("capture*.pml"):
            match = _CAPTURE_RE.match(item.name)
            if match:
                found.append((int(match.group(1) or 0), item))
        return [item for _, item in sorted(found)]

    def write_status(self, state: str, error: str | None = None, recorder_pid: int | None = None) -> None:
        if recorder_pid is None:
            previous = self.read_status()
            recorder_pid = previous.get("recorder_pid") if previous else None
        status = {"state": state, "error": error, "recorder_pid": recorder_pid,
                  "updated": filetime_to_iso(now_filetime())}
        _atomic_write(self.status_path, json.dumps(status))

    def read_status(self) -> dict | None:
        try:
            data = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def write_session(self, info: SessionInfo) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.sessions_dir / f"{info.session_id}.json", json.dumps(info.to_json()))

    def read_sessions(self) -> list[SessionInfo]:
        sessions = []
        for item in sorted(self.sessions_dir.glob("*.json")):
            try:
                sessions.append(SessionInfo.from_json(json.loads(item.read_text(encoding="utf-8"))))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log.warning("Skipping unreadable session file %s: %s", item, exc)
        return sorted(sessions, key=lambda s: s.launch_before)

    def is_split(self) -> bool:
        return self.split_path.exists()


def new_run(traces_dir: Path, now_ft: int, pid: int) -> RunDir:
    path = Path(traces_dir) / "_runs" / f"{filetime_to_local_stamp(now_ft)}-{pid}"
    (path / "sessions").mkdir(parents=True, exist_ok=True)
    return RunDir(path)


def leftover_runs(traces_dir: Path, is_alive: Callable[[int], bool]) -> list[RunDir]:
    runs_dir = Path(traces_dir) / "_runs"
    if not runs_dir.is_dir():
        return []
    leftovers = []
    for item in sorted(runs_dir.iterdir()):
        if not item.is_dir():
            continue
        run = RunDir(item)
        if not run.capture_files() or run.is_split():
            continue
        status = run.read_status()
        pid = status.get("recorder_pid") if status else None
        if pid is None or not is_alive(pid):
            leftovers.append(run)
    return leftovers
