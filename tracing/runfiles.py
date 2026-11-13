"""A run folder: one recorder run's capture, status and session files under traces/_runs/."""
import json
import logging
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Callable

from tracing.clock import filetime_to_iso, filetime_to_local_stamp, now_filetime
from tracing.model import SessionInfo

log = logging.getLogger(__name__)

_CAPTURE_RE = re.compile(r"^capture(?:-(\d+))?\.pml$", re.IGNORECASE)


REPLACE_TRIES = 10
REPLACE_DELAY = 0.015
READ_TRIES = 5  # status.json can't be read for a moment while the recorder replaces it
READ_RETRY_SECONDS = 0.02


def replace_with_retry(src: Path, dst: Path, *, replace: Callable | None = None,
                       sleep: Callable[[float], None] | None = None) -> None:
    """os.replace, tried again while Windows says the target is held (a reader or antivirus has it open)."""
    replace = replace or os.replace
    sleep = sleep or time.sleep
    for attempt in range(1, REPLACE_TRIES + 1):
        try:
            replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES:
                raise
            sleep(REPLACE_DELAY)


def _temp_for(path: Path) -> Path:
    """A temp file beside `path` that no other writer uses, in this process or another."""
    return path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")


def _atomic_write(path: Path, text: str) -> None:
    temp = _temp_for(path)
    try:
        temp.write_text(text, encoding="utf-8")
        replace_with_retry(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _atomic_create(path: Path, text: str) -> bool:
    """Write `path` whole, only if it does not exist yet; False (and nothing changed) when it does."""
    temp = _temp_for(path)
    try:
        temp.write_text(text, encoding="utf-8")
        if sys.platform == "win32":
            os.rename(temp, path)  # on Windows a rename never replaces a file
        else:
            os.link(temp, path)  # nor does a hard link, anywhere
        return True
    except FileExistsError:
        return False
    finally:
        temp.unlink(missing_ok=True)


def _read_dict(path: Path) -> dict | None:
    """The JSON object in a file; None when it is missing, unreadable or not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class RunDir:
    def __init__(self, path: Path):
        self.path = Path(path)

    @property
    def traces_dir(self) -> Path:
        """The traces folder this run sits in (traces/_runs/<run>, as new_run makes it)."""
        return self.path.parent.parent

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

    @property
    def stop_request_path(self) -> Path:
        """Written by --trace-stop: asks the recorder to end the run."""
        return self.path / "stop-request.json"

    @property
    def stop_taken_path(self) -> Path:
        """The request once the recorder has claimed it (renamed, so it can be claimed or withdrawn, never both)."""
        return self.path / "stop-request.taken.json"

    @property
    def stop_path(self) -> Path:
        """Written by the recorder when it takes a stop request: what the stop did."""
        return self.path / "stop.json"

    def capture_files(self) -> list[Path]:
        found = []
        try:
            items = list(self.path.iterdir())
        except OSError:
            return []
        for item in items:
            match = _CAPTURE_RE.match(item.name)
            if match and item.is_file():
                found.append((int(match.group(1) or 0), item))
        return [item for _, item in sorted(found)]

    def write_status(self, state: str, error: str | None = None, recorder_pid: int | None = None,
                     recorder_start: int | None = None) -> None:
        """`recorder_pid` and `recorder_start` (the recorder process's creation FILETIME: together they name one
        process, since a pid alone can be given to another program later) are kept from the last status when
        no pid is given."""
        if recorder_pid is None:
            previous = self.read_status() or {}
            recorder_pid, recorder_start = previous.get("recorder_pid"), previous.get("recorder_start")
        status = {"state": state, "error": error, "recorder_pid": recorder_pid, "recorder_start": recorder_start,
                  "updated": filetime_to_iso(now_filetime())}
        _atomic_write(self.status_path, json.dumps(status))

    def read_status(self) -> dict | None:
        return _read_dict(self.status_path)

    def write_stop_request(self, request: dict) -> bool:
        """False, and nothing written, when a request is already there (another command's is never replaced)."""
        return _atomic_create(self.stop_request_path, json.dumps(request))

    def read_stop_request(self) -> dict | None:
        return _read_dict(self.stop_request_path)

    def claim_stop_request(self) -> dict | None:
        """The recorder's side: take the request by renaming it, and return it; None when there is none, it can't
        be read, or the command withdrew it first. The rename and the command's removal are each atomic and only
        one of them can succeed, so a request is never both taken and reported withdrawn. Raises OSError when the
        file is held; the caller tries again."""
        request = _read_dict(self.stop_request_path)
        if request is None:
            return None
        try:
            replace_with_retry(self.stop_request_path, self.stop_taken_path)
        except FileNotFoundError:
            return None
        return _read_dict(self.stop_taken_path) or request

    def withdraw_stop_request(self) -> str:
        """The command's side, when the recorder didn't answer: remove the request. "withdrawn" when it was removed
        (the recorder can no longer take it), "taken" when it had already gone (the recorder claimed it), "held"
        when it couldn't be removed (nothing is known yet)."""
        try:
            self.stop_request_path.unlink()
        except FileNotFoundError:
            return "taken"
        except OSError:
            return "held"
        return "withdrawn"

    def write_stop(self, record: dict) -> None:
        _atomic_write(self.stop_path, json.dumps(record))

    def read_stop(self) -> dict | None:
        return _read_dict(self.stop_path)

    def write_session(self, info: SessionInfo) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.sessions_dir / f"{info.session_id}.json", json.dumps(info.to_json()))

    def read_sessions(self) -> list[SessionInfo]:
        return self.read_sessions_and_problems()[0]

    def read_sessions_and_problems(self) -> tuple[list[SessionInfo], list[str]]:
        """The readable sessions in launch order, and one problem per session file that couldn't be read."""
        sessions, problems = [], []
        for item in sorted(self.sessions_dir.glob("*.json")):
            try:
                sessions.append(SessionInfo.from_json(json.loads(item.read_text(encoding="utf-8"))))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log.warning("Skipping unreadable session file %s: %s", item, exc)
                problems.append(f"{item.name}: the session file couldn't be read ({exc}), so that session is "
                                "missing.")
        return sorted(sessions, key=lambda s: s.launch_before), problems

    def read_split(self) -> dict | None:
        return _read_dict(self.split_path)

    def write_split(self, record: dict) -> None:
        _atomic_write(self.split_path, json.dumps(record))

    def is_split(self) -> bool:
        return self.split_path.exists()


def new_run(traces_dir: Path, now_ft: int, pid: int) -> RunDir:
    path = Path(traces_dir) / "_runs" / f"{filetime_to_local_stamp(now_ft)}-{pid}"
    (path / "sessions").mkdir(parents=True, exist_ok=True)
    return RunDir(path)


IsAlive = Callable[[int, int | None], bool]  # (pid, start time or None): that very process is still running


def run_dirs(traces_dir: Path) -> list[RunDir]:
    runs_dir = Path(traces_dir) / "_runs"
    if not runs_dir.is_dir():
        return []
    return [RunDir(item) for item in sorted(runs_dir.iterdir()) if item.is_dir()]


def recorder_alive(status: dict | None, is_alive: IsAlive) -> bool:
    """The recorder the status names, by pid and start time (a pid alone may be another program by now)."""
    pid = status.get("recorder_pid") if status else None
    return pid is not None and is_alive(pid, status.get("recorder_start"))


def _read_status(run: RunDir) -> dict | None:
    """status.json; a file that is there but can't be read is read again, so a recorder caught in the middle of
    replacing it is not mistaken for a run with no status."""
    for _ in range(READ_TRIES):
        status = run.read_status()
        if status is not None or not run.status_path.exists():
            return status
        time.sleep(READ_RETRY_SECONDS)
    return None


def leftover_runs(traces_dir: Path, is_alive: IsAlive) -> list[RunDir]:
    """Runs with a capture that is not split and whose recorder is gone."""
    return [run for run in run_dirs(traces_dir)
            if run.capture_files() and not run.is_split() and not recorder_alive(_read_status(run), is_alive)]
