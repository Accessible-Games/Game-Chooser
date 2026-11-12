"""The --trace-stop command: ends the tracing run in progress while traced programs may still be open.

The command runs unelevated and never touches a process. It writes `stop-request.json` into the run folder; the
elevated recorder, which polls that folder, takes the request (`stop.json`), closes the traced games first when
asked to (only processes of the app's user with no more rights than the app: see closing.py), stops Process
Monitor and splits the capture as at a normal end. The command waits for that and prints
what happened.
"""
import json
import os
import time
from pathlib import Path
from typing import Callable

from tracing.clock import now_filetime
from tracing.recorder import CLOSE_WAIT_SECONDS
from tracing.runfiles import RunDir, leftover_runs

ACK_SECONDS = 30.0  # how long a capturing recorder gets to take the request
POLL_SECONDS = 0.5

CLOSE_NOTE = (f"The traced games are asked to close; any still open after {CLOSE_WAIT_SECONDS:g} seconds are "
              "ended. Unsaved game progress is lost. A game started through the administrator prompt is left open.")
NOTHING = "No tracing run is in progress."
_PROGRESS = {"stopping": "The recorder is ending the run.", "splitting": "Splitting the capture."}


def _recorder_alive(run: RunDir, is_alive: Callable[[int], bool]) -> bool:
    status = run.read_status()
    pid = status.get("recorder_pid") if status else None
    return pid is not None and is_alive(pid)


def _ended(run: RunDir) -> bool:
    status = run.read_status()
    return run.is_split() or (status is not None and status.get("state") == "done")


def _runs(traces_dir: Path) -> list[RunDir]:
    runs_dir = Path(traces_dir) / "_runs"
    if not runs_dir.is_dir():
        return []
    return [RunDir(item) for item in sorted(runs_dir.iterdir()) if item.is_dir()]


def _gone(run: RunDir) -> str:
    text = f"{run.path}: the recorder is no longer running, so there is nothing to stop."
    if run.capture_files() and not run.is_split():
        text += " Its capture was not split; run --trace-split to split it."
    return text


def stop_command(traces_dir: Path, run_path: Path | None, close_games: bool, *, is_alive: Callable[[int], bool],
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], int] = now_filetime, out: Callable[[str], None] = print) -> int:
    """End the run in progress under `traces_dir`, or the run folder `run_path` when one is named.

    Returns 0 when the run ended cleanly or there was nothing to stop, 1 on a problem (the recorder gone or not
    answering, a recorder failure, a split problem, a game that could not be closed), 2 when it can't tell which
    run is meant (more than one in progress and none named, or `run_path` is not a run folder)."""
    if run_path is not None:
        run = RunDir(run_path)
        if run.read_status() is None:
            out(f"{run.path}: not a run folder")
            return 2
        if _ended(run):
            out(f"{run.path}: already ended, nothing to do")
            return 0
        if not _recorder_alive(run, is_alive):
            out(_gone(run))
            return 1
        return _stop(run, close_games, is_alive, clock, sleep, now, out)
    live = [run for run in _runs(traces_dir) if not _ended(run) and _recorder_alive(run, is_alive)]
    if len(live) > 1:
        out("More than one tracing run is in progress. Name the one to end: --trace-stop RUN")
        for run in live:
            out(str(run.path))
        return 2
    if live:
        return _stop(live[0], close_games, is_alive, clock, sleep, now, out)
    # No recorder to ask. A capture left by a dead recorder is said, not split here: --trace-split does that.
    dead = leftover_runs(traces_dir, is_alive)
    for run in dead:
        out(_gone(run))
    if not dead:
        out(NOTHING)
    return 1 if dead else 0


def _stop(run: RunDir, close_games: bool, is_alive, clock, sleep, now, out) -> int:
    status = run.read_status() or {}
    capturing = status.get("state") in ("starting", "capturing")
    asked = run.read_stop_request() is not None or run.stop_taken_path.exists() or run.read_stop() is not None
    if capturing and not asked:
        try:
            run.write_stop_request({"requested": now(), "close_games": close_games, "requester_pid": os.getpid()})
        except OSError as error:
            out(f"Problem: the stop request couldn't be written to {run.path} ({error}).")
            return 1
        out(f"Asked the recorder to end the run {run.path}.")
        if close_games:
            out(CLOSE_NOTE)
    else:
        out(f"The run {run.path} is already ending; waiting for it.")
    return _wait(run, is_alive, clock, sleep, out)


def _wait(run: RunDir, is_alive, clock, sleep, out) -> int:
    """Wait for the recorder to take the request and finish; it works as long as it lives, with no time limit
    (Process Monitor's stop and the split of a large capture take minutes)."""
    deadline = clock() + ACK_SECONDS
    taken = False
    shown = None
    while True:
        status = run.read_status() or {}
        state = status.get("state")
        if state in _PROGRESS and state != shown:
            shown = state
            out(_PROGRESS[state])
        if state == "done":
            break
        if not _recorder_alive(run, is_alive):
            break
        if not taken:
            if state not in ("starting", "capturing") or run.read_stop() is not None:
                taken = True
            elif state == "starting":
                deadline = clock() + ACK_SECONDS  # the request is seen once Process Monitor is capturing
            elif clock() >= deadline:
                # Only a request this command removed itself is withdrawn. One that has gone was claimed by the
                # recorder (it renames it), so the stop is under way; one that can't be removed may still be
                # claimed, so it is tried again at the next poll.
                answer = run.withdraw_stop_request()
                if answer == "withdrawn":
                    out(f"Problem: the recorder didn't answer within {ACK_SECONDS:g} seconds, so the request was "
                        "withdrawn and the run goes on.")
                    return 1
                taken = answer == "taken"
        sleep(POLL_SECONDS)
    return _report(run, out)


def _read_split(run: RunDir) -> dict | None:
    try:
        data = json.loads(run.split_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _report(run: RunDir, out) -> int:
    status = run.read_status() or {}
    state = status.get("state")
    stop = run.read_stop()
    split = _read_split(run)
    traces_dir = run.path.parent.parent
    problem = False
    if state == "failed":
        out(f"Problem: {status.get('error')}")
        problem = True
    elif state != "done":
        text = "Problem: the recorder closed before the run had ended."
        if split is None and run.capture_files():
            text += f" The capture was not split; run --trace-split {run.path}"
        out(text)
        problem = True
    if stop is None:
        if not problem:
            out("The run ended by itself before the stop was taken.")
    else:
        problem = _report_stop(stop, out) or problem
    if split is not None:
        for folder in split.get("folders") or []:
            out(f"Wrote {traces_dir / folder}")
        for text in split.get("problems") or []:
            out(f"Problem: {text}")
            problem = True
    if not problem:
        out("The run has ended.")
    return 1 if problem else 0


def _report_stop(stop: dict, out) -> bool:
    """Say which sessions the stop found running and what closing them did; True when a game could not be closed."""
    sessions = stop.get("sessions")
    sessions = sessions if isinstance(sessions, dict) else {}
    if sessions:
        out("Still running when the run was stopped: " + ", ".join(sorted(sessions)))
    else:
        out("No traced game was running when the run was stopped.")
    if stop.get("close_games") is not True or not sessions:
        return False
    asked, ended, problem = set(), set(), False
    for session_id in sorted(sessions):
        entry = sessions[session_id] if isinstance(sessions[session_id], dict) else {}
        asked.update(entry.get("asked_to_close") or [])
        ended.update(entry.get("ended") or [])
        left = [item for item in entry.get("not_closed") or [] if isinstance(item, dict)]
        if left:
            out(f"Problem: {session_id}: still running after the stop: "
                + ", ".join(f"{item.get('pid')} ({item.get('reason')})" for item in left))
            problem = True
    out(f"Asked {len(asked)} processes to close; ended {len(ended)} that stayed open.")
    return problem
