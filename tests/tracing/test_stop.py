"""The --trace-stop command: finding the run, asking its recorder to end it, waiting and reporting."""
import json

import pytest

from tests.tracing.test_recorder import Clock, FakeProcmon
from tests.tracing.test_split import CHILD, CHOOSER, ROOT, ev, info
from tracing import messages, recorder, stop
from tracing.live import LiveProc
from tracing.runfiles import RunDir, new_run
from tracing.splitrun import split_run_dir

FT = 133_000_000_000_000_000
NOW = FT + 5
GIB = 1024**3
RECORDER = 700
STARTED = 9000  # when the recorder processes of these tests started
LISTS = {"asked_to_close": [], "ended": [], "not_closed": []}


class Setup:
    def __init__(self, tmp_path):
        self.traces = tmp_path / "traces"
        self.clock = Clock()
        self.alive = {RECORDER}
        self.starts = {}  # the start time of the process that has a pid now, when it isn't STARTED
        self.lines = []
        self.script = []  # what the recorder does at each of the command's sleeps

    def run(self, pid=5, state="capturing", recorder_pid=RECORDER, capture=True):
        run = new_run(self.traces, FT + pid, pid)
        if state is not None:
            run.write_status(state, recorder_pid=recorder_pid, recorder_start=STARTED)
        if capture:
            run.capture_path.write_bytes(b"pml")
        return run

    def sleep(self, seconds):
        self.clock.sleep(seconds)
        if self.script:
            self.script.pop(0)()

    def stop(self, run=None, close_games=False):
        return stop.stop_command(self.traces, run.path if run else None, close_games,
                                 is_alive=self.is_alive, clock=self.clock, sleep=self.sleep,
                                 now=lambda: NOW, out=self.lines.append)

    def is_alive(self, pid, start):
        return pid in self.alive and start in (None, self.starts.get(pid, STARTED))

    @property
    def text(self):
        return "\n".join(self.lines)


@pytest.fixture
def s(tmp_path):
    return Setup(tmp_path)


def finish(run, sessions=None, state="done", error=None, folders=(), problems=(), close_games=False, split=True):
    """What a recorder leaves behind after taking a stop."""
    def act():
        run.write_stop({"requested": NOW, "time": NOW + 1, "close_games": close_games, "sessions": sessions or {}})
        if split:
            run.split_path.write_text(json.dumps({"folders": list(folders), "problems": list(problems),
                                                  "capture_kept": bool(problems), "stop": {}}), encoding="utf-8")
            run.capture_path.unlink(missing_ok=True)
        run.write_status(state, error=error)
    return act


# --- nothing to stop ---

def test_no_runs_folder_is_nothing_to_stop(s):
    assert s.stop() == 0
    assert s.lines == ["No tracing run is in progress."]


def test_finished_runs_are_nothing_to_stop_and_get_no_request(s):
    done = s.run(state="done", capture=False)
    done.split_path.write_text("{}", encoding="utf-8")
    assert s.stop() == 0
    assert s.lines == ["No tracing run is in progress."]
    assert done.read_stop_request() is None


def test_a_run_whose_recorder_never_wrote_a_status_is_not_in_progress(s):
    s.run(state=None, capture=False)  # Game Chooser is still at the administrator prompt
    assert s.stop() == 0
    assert s.lines == ["No tracing run is in progress."]


# --- the recorder already gone ---

def test_recorder_gone_with_an_unsplit_capture_is_a_problem(s):
    run = s.run()
    s.alive.clear()
    assert s.stop() == 1
    assert s.lines == [f"{run.path}: the recorder is no longer running, so there is nothing to stop. "
                       "Its capture was not split; run --trace-split to split it."]
    assert run.read_stop_request() is None


def test_recorder_gone_with_nothing_captured_is_nothing_to_stop(s):
    s.run(state="failed", capture=False)
    s.alive.clear()
    assert s.stop() == 0
    assert s.lines == ["No tracing run is in progress."]


def test_a_dead_run_beside_a_live_one_is_left_to_the_live_recorder(s):
    dead = s.run(pid=4, recorder_pid=600)
    live = s.run(pid=5)
    s.script = [finish(live)]
    assert s.stop() == 0
    assert dead.read_stop_request() is None and live.read_stop_request() is not None


# --- more than one run ---

def test_more_than_one_run_in_progress_stops_none_and_asks_which(s):
    s.alive.add(701)
    first, second = s.run(pid=4), s.run(pid=5, recorder_pid=701)
    assert s.stop() == 2
    assert s.lines == ["More than one tracing run is in progress. Name the one to end: --trace-stop RUN",
                       str(first.path), str(second.path)]
    assert first.read_stop_request() is None and second.read_stop_request() is None


def test_a_named_run_is_the_only_one_stopped(s):
    s.alive.add(701)
    first, second = s.run(pid=4), s.run(pid=5, recorder_pid=701)
    s.script = [finish(second)]
    assert s.stop(second) == 0
    assert first.read_stop_request() is None and second.read_stop_request() is not None


def test_a_named_folder_that_is_not_a_run_is_a_bad_command(s, tmp_path):
    assert s.stop(RunDir(tmp_path / "nowhere")) == 2
    assert s.lines == [f"{tmp_path / 'nowhere'}: not a run folder"]


def test_a_named_run_that_already_ended_does_nothing(s):
    run = s.run(state="done", capture=False)
    run.split_path.write_text("{}", encoding="utf-8")
    assert s.stop(run) == 0
    assert s.lines == [f"{run.path}: already ended, nothing to do"]
    assert run.read_stop_request() is None


def test_a_named_run_whose_recorder_is_gone(s):
    run = s.run(capture=False)
    s.alive.clear()
    assert s.stop(run) == 1
    assert s.lines == [f"{run.path}: the recorder is no longer running, so there is nothing to stop."]
    assert run.read_stop_request() is None


# --- stopping ---

def test_stop_writes_the_request_waits_and_reports_the_session_folders(s):
    run = s.run()
    s.script = [lambda: run.write_status("stopping"), lambda: run.write_status("splitting"),
                finish(run, {"s001-9": LISTS}, folders=["foo-20261111-101010"])]
    assert s.stop() == 0
    request = run.read_stop_request()
    assert request["requested"] == NOW and request["close_games"] is False
    assert s.lines == [f"Asked the recorder to end the run {run.path}.",
                       "The recorder is ending the run.",
                       "Splitting the capture.",
                       "Still running when the run was stopped: s001-9",
                       f"Wrote {s.traces / 'foo-20261111-101010'}",
                       "The run has ended."]


def test_stop_with_no_game_running_says_so(s):
    run = s.run()
    s.script = [finish(run)]
    assert s.stop() == 0
    assert "No traced game was running when the run was stopped." in s.lines and "The run has ended." in s.lines


def test_close_games_goes_into_the_request_with_its_warning(s):
    run = s.run()
    lists = {"asked_to_close": [100, 101], "ended": [101], "not_closed": []}
    s.script = [finish(run, {"s001-9": lists, "s002-8": {**LISTS, "asked_to_close": [100]}}, close_games=True)]
    assert s.stop(close_games=True) == 0
    assert run.read_stop_request()["close_games"] is True
    assert s.lines[1] == stop.CLOSE_NOTE and "15 seconds" in stop.CLOSE_NOTE
    assert "unsaved game progress is lost" in stop.CLOSE_NOTE.lower()
    assert "Asked 2 processes to close; ended 1 that stayed open." in s.lines


def test_a_process_that_could_not_be_closed_is_a_problem(s):
    run = s.run()
    left = [{"pid": 100, "reason": "could not be ended"}, {"pid": 103, "reason": "runs as another user"}]
    s.script = [finish(run, {"s001-9": {**LISTS, "not_closed": left}}, close_games=True)]
    assert s.stop(close_games=True) == 1
    assert ("Problem: s001-9: still running after the stop: 100 (could not be ended), "
            "103 (runs as another user)") in s.lines
    assert "The run has ended." not in s.lines


def test_a_plain_stop_leaving_games_running_is_no_problem(s):
    run = s.run()
    s.script = [finish(run, {"s001-9": LISTS})]
    assert s.stop() == 0
    assert not any(line.startswith("Problem") for line in s.lines)


def test_no_answer_withdraws_the_request_and_is_a_problem(s):
    run = s.run()
    assert s.stop(close_games=True) == 1
    assert s.clock.t == stop.ACK_SECONDS
    assert s.lines[-1] == ("Problem: the recorder didn't answer within 30 seconds, so the request was withdrawn "
                           "and the run goes on.")
    assert run.read_stop_request() is None


def test_the_wait_for_an_answer_starts_once_process_monitor_is_up(s):
    run = s.run(state="starting")
    s.script = [lambda: None] * 100 + [lambda: run.write_status("capturing")] + [lambda: None] * 10 + [finish(run)]
    assert s.stop() == 0
    assert s.clock.t > stop.ACK_SECONDS


def test_a_run_that_is_already_ending_is_waited_for_and_its_request_kept(s):
    run = s.run()
    run.write_stop_request({"requested": 1, "close_games": False})
    run.write_status("stopping")
    s.script = [finish(run)]
    assert s.stop(close_games=True) == 0
    assert run.read_stop_request() == {"requested": 1, "close_games": False}
    assert s.lines[0] == f"The run {run.path} is already ending; waiting for it."
    assert stop.CLOSE_NOTE not in s.lines


def test_the_recorder_dying_during_the_stop_is_a_problem(s):
    run = s.run()

    def die():
        run.write_stop({"requested": NOW, "time": NOW, "close_games": False, "sessions": {}})
        run.write_status("stopping")
        s.alive.clear()

    s.script = [die]
    assert s.stop() == 1
    assert ("Problem: the recorder closed before the run had ended. The capture was not split; "
            f"run --trace-split {run.path}") in s.lines
    assert "The run has ended." not in s.lines


def test_a_recorder_failure_during_the_stop_is_reported(s):
    run = s.run()

    def fail():
        finish(run, state="failed", error=messages.PROCMON_STOP_SLOW, folders=["foo-1"])()
        s.alive.clear()  # a failed run never says done: its recorder exiting is the end

    s.script = [fail]
    assert s.stop() == 1
    assert f"Problem: {messages.PROCMON_STOP_SLOW}" in s.lines and f"Wrote {s.traces / 'foo-1'}" in s.lines
    assert "The run has ended." not in s.lines


def test_split_problems_are_reported(s):
    run = s.run()
    s.script = [finish(run, folders=["foo-1"], problems=["s001-9: no root"])]
    assert s.stop() == 1
    assert "Problem: s001-9: no root" in s.lines


def test_a_run_that_ended_by_itself_before_the_request_was_taken(s):
    run = s.run()

    def ended_alone():
        run.split_path.write_text(json.dumps({"folders": [], "problems": [], "capture_kept": False, "stop": None}),
                                  encoding="utf-8")
        run.write_status("done")

    s.script = [ended_alone]
    assert s.stop() == 0
    assert "The run ended by itself before the stop was taken." in s.lines


def test_a_request_that_cannot_be_written_is_a_problem(s, monkeypatch):
    run = s.run()

    def denied(self, request):
        raise PermissionError("denied")

    monkeypatch.setattr(RunDir, "write_stop_request", denied)
    assert s.stop() == 1
    assert s.lines == [f"Problem: the stop request couldn't be written to {run.path} (denied)."]


# --- the command and a real recorder, through the run folder's files ---

def recorder_for(run, traces, clock, snapshot, closer=None):
    def fake_read(files):
        return [CHOOSER, ROOT, CHILD], iter([ev(ROOT, 12, Length=4)])

    return recorder.Recorder(run, lambda: True, FakeProcmon(clock, True, None, run), snapshot,
                             lambda r: split_run_dir(r, traces, fake_read), lambda: [], lambda: 10 * GIB, clock,
                             clock.sleep, closer=closer, now=lambda: NOW + 1)


@pytest.fixture
def plain_stamp(monkeypatch):
    # the shared fixture's times sit near year 1601, which Windows can't convert to local time
    monkeypatch.setattr("tracing.output.filetime_to_local_stamp", lambda ft: "stamp")


def test_command_and_recorder_end_a_run_with_a_game_still_open(s, plain_stamp):
    run = s.run(state=None, capture=False)
    run.write_session(info())
    run.write_status("capturing", recorder_pid=RECORDER, recorder_start=STARTED)
    rec = recorder_for(run, s.traces, Clock(), lambda: [LiveProc(100, 50, ROOT.start, r"C:\g\a.exe")])
    s.script = [rec.run]
    assert s.stop() == 0
    assert s.lines[-3:] == ["Still running when the run was stopped: s", f"Wrote {s.traces / 'c-stamp'}",
                            "The run has ended."]
    session = json.loads((s.traces / "c-stamp" / "session.json").read_text(encoding="utf-8"))
    assert session["ended_by"] == "stop" and session["still_running_at_capture_end"] is True
    assert session["stop"]["close_games"] is False and session["end"] is None
    marker = json.loads(run.split_path.read_text(encoding="utf-8"))
    assert marker["stop"]["sessions"] == ["s"] and marker["problems"] == [] and marker["capture_kept"] is False
    assert run.read_status()["state"] == "done"


def test_command_and_recorder_with_no_game_open(s, plain_stamp):
    run = s.run(state=None, capture=False)
    run.write_session(info())
    run.write_status("capturing", recorder_pid=RECORDER, recorder_start=STARTED)
    rec = recorder_for(run, s.traces, Clock(), lambda: [])
    s.script = [rec.run]
    assert s.stop() == 0
    assert "No traced game was running when the run was stopped." in s.lines
    session = json.loads((s.traces / "c-stamp" / "session.json").read_text(encoding="utf-8"))
    # the recorder saw no process, but in the capture CHILD never exits: cut off by the stop all the same
    assert session["ended_by"] == "stop" and session["stop"]["asked_to_close"] == []
    assert json.loads(run.split_path.read_text(encoding="utf-8"))["stop"]["sessions"] == []


# --- the request is withdrawn only when the recorder can no longer take it ---

def test_a_request_the_recorder_claimed_at_the_last_moment_is_waited_for_not_withdrawn(s):
    run = s.run()
    polls = int(stop.ACK_SECONDS / stop.POLL_SECONDS)

    def claim():  # the recorder has taken the request but not yet written stop.json
        assert run.claim_stop_request() is not None

    s.script = [lambda: None] * (polls - 1) + [claim, lambda: None, finish(run, {"s001-9": LISTS})]
    assert s.stop(close_games=True) == 0
    assert not any("withdrawn" in line for line in s.lines) and "The run has ended." in s.lines


def test_a_request_that_cannot_be_removed_is_not_reported_as_withdrawn(s, monkeypatch):
    s.run()
    answers = ["held", "held", "withdrawn"]
    asked = []
    monkeypatch.setattr(RunDir, "withdraw_stop_request", lambda self: asked.append(1) or answers.pop(0))
    assert s.stop() == 1
    assert len(asked) == 3 and s.clock.t == stop.ACK_SECONDS + 2 * stop.POLL_SECONDS
    assert [line for line in s.lines if "withdrawn" in line] == [
        "Problem: the recorder didn't answer within 30 seconds, so the request was withdrawn and the run goes on."]


def test_a_held_request_the_recorder_then_takes_ends_the_run(s, monkeypatch):
    run = s.run()
    monkeypatch.setattr(RunDir, "withdraw_stop_request", lambda self: "held")
    polls = int(stop.ACK_SECONDS / stop.POLL_SECONDS)
    s.script = [lambda: None] * (polls + 3) + [finish(run)]
    assert s.stop() == 0
    assert not any("withdrawn" in line for line in s.lines)


def test_no_second_request_is_written_while_a_claimed_one_is_being_taken(s):
    run = s.run()
    run.write_stop_request({"requested": 1, "close_games": False})
    assert run.claim_stop_request() is not None  # claimed; stop.json and the status change are still to come
    s.script = [finish(run)]
    assert s.stop(close_games=True) == 0
    assert run.read_stop_request() is None and s.lines[0] == f"The run {run.path} is already ending; waiting for it."


# --- the recorder is known by pid and start time; a status that can't be read for a moment is not its death ---

def test_a_dead_run_whose_recorder_pid_went_to_another_program_is_not_in_progress(s):
    run = s.run()
    s.starts[RECORDER] = STARTED + 500  # the pid is alive, but it is another process now
    assert s.stop() == 1
    assert s.lines == [f"{run.path}: the recorder is no longer running, so there is nothing to stop. "
                       "Its capture was not split; run --trace-split to split it."]
    assert run.read_stop_request() is None


def test_a_status_from_before_start_times_were_written_is_checked_by_pid(s):
    run = s.run(state=None)
    run.write_status("capturing", recorder_pid=RECORDER)
    s.script = [finish(run)]
    assert s.stop() == 0


def test_a_status_that_cannot_be_read_for_a_moment_does_not_end_the_wait(s):
    run = s.run()
    text = {}

    def unreadable():  # the command reads while the recorder is replacing the file
        text["status"] = run.status_path.read_text(encoding="utf-8")
        run.status_path.write_text("{", encoding="utf-8")

    def readable():
        run.status_path.write_text(text["status"], encoding="utf-8")

    s.script = [unreadable, readable, finish(run)]
    assert s.stop() == 0
    assert "The run has ended." in s.lines and not any(line.startswith("Problem") for line in s.lines)


def test_a_status_that_cannot_be_read_at_first_is_read_again(s, monkeypatch):
    run = s.run()
    real = RunDir.read_status
    reads = []

    def flaky(self):
        reads.append(1)
        return None if len(reads) == 1 else real(self)

    monkeypatch.setattr(RunDir, "read_status", flaky)
    monkeypatch.setattr(stop, "READ_RETRY_SECONDS", 0)
    s.script = [finish(run)]
    assert s.stop() == 0 and run.read_stop_request() is not None


def test_a_request_another_command_wrote_first_is_kept_and_waited_for(s, monkeypatch):
    run = s.run()
    real = RunDir.write_stop_request

    def beaten(self, request):  # another --trace-stop got in between this one's look and its write
        assert real(self, {"requested": 1, "close_games": False}) is True
        return real(self, request)

    monkeypatch.setattr(RunDir, "write_stop_request", beaten)
    s.script = [finish(run)]
    assert s.stop(close_games=True) == 0
    assert run.read_stop_request() == {"requested": 1, "close_games": False}
    assert s.lines[0] == f"The run {run.path} is already ending; waiting for it." and stop.CLOSE_NOTE not in s.lines
