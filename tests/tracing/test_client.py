import json

from engine.errors import ElevationDeclined
from tracing import messages
from tracing.client import TraceClient
from tracing.live import LiveProc


NOW = 133_500_000_000_000_000


class FakeProc:
    pid = 1
    handle = 0

    def __init__(self):
        self.code = None

    def wait(self, timeout=None):
        return self.code


def make(tmp_path, snapshot=None, start_recorder=None, procmon_exists=True):
    procmon = tmp_path / "Procmon64.exe"
    if procmon_exists:
        procmon.write_text("x")
    calls = []
    proc = FakeProc()

    def starter(argv):
        calls.append(argv)
        return proc

    client = TraceClient(tmp_path / "traces", procmon, start_recorder or starter, {"windows": "11"},
                         clock=lambda: NOW, snapshot=snapshot or (lambda: []),
                         start_time_of=lambda pid: 77, app_pid=42)
    return client, proc, calls


def status(client, state, error=None):
    client._run.write_status(state, error=error)


def test_missing_procmon_is_off_with_path(tmp_path):
    client, _, calls = make(tmp_path, procmon_exists=False)
    client.start()
    assert client.state.phase == "off"
    assert client.state.reason == messages.PROCMON_MISSING.format(path=tmp_path / "Procmon64.exe")
    assert calls == []


def test_declined_prompt_is_off(tmp_path):
    def decline(argv):
        raise ElevationDeclined("no")
    client, _, _ = make(tmp_path, start_recorder=decline)
    client.start()
    assert client.state.phase == "off" and client.state.reason == messages.DECLINED


def test_other_start_error(tmp_path):
    def boom(argv):
        raise OSError("bad")
    client, _, _ = make(tmp_path, start_recorder=boom)
    client.start()
    assert client.state.reason == messages.START_FAILED.format(error=OSError("bad"))


def test_starting_then_on_then_failed(tmp_path):
    client, _, _ = make(tmp_path)
    assert client.state.phase == "starting"
    client.start()
    assert client.poll().phase == "starting"
    status(client, "starting")
    assert client.poll().phase == "starting"
    status(client, "capturing")
    assert client.poll().phase == "on"
    status(client, "failed", "Disk full.")
    state = client.poll()
    assert state.phase == "off" and state.reason == messages.RECORDER_FAILED.format(error="Disk full.")
    status(client, "capturing")
    assert client.poll().phase == "off"


def test_recorder_gone_is_off(tmp_path):
    client, proc, _ = make(tmp_path)
    client.start()
    status(client, "capturing")
    assert client.poll().phase == "on"
    proc.code = 1
    state = client.poll()
    assert state.phase == "off" and state.reason == messages.RECORDER_GONE


def test_snapshot_error_does_not_crash_poll(tmp_path):
    def boom():
        raise OSError("snap")
    client, _, _ = make(tmp_path, snapshot=boom)
    client.start()
    status(client, "capturing")
    assert client.poll().phase == "on"


def test_launch_records_session_only_when_on(tmp_path):
    client, _, _ = make(tmp_path)
    client.start()
    assert client.game_launched(10, 1, 2, {"title": "T"}) is None
    assert list(client._run.sessions_dir.glob("*.json")) == []
    status(client, "capturing")
    client.poll()
    sid = client.game_launched(10, 1, 2, {"title": "T", "game_dir": "C:/g"})
    assert sid == "s001-10"
    data = json.loads((client._run.sessions_dir / f"{sid}.json").read_text())
    assert (data["root_pid"], data["root_start"], data["launch_before"], data["launch_after"]) == (10, 77, 1, 2)
    assert data["game"]["title"] == "T" and data["env"] == {"windows": "11"}


def test_mark_goes_to_running_sessions_only(tmp_path):
    procs = [LiveProc(10, 1, 77, None), LiveProc(11, 1, 77, None)]
    client, _, _ = make(tmp_path, snapshot=lambda: list(procs))
    client.start()
    status(client, "capturing")
    client.poll()
    a = client.game_launched(10, 1, 2, {"title": "A"})
    b = client.game_launched(11, 1, 2, {"title": "B"})
    client.poll()
    procs.pop(1)
    assert client.mark() == [a]
    read = {s.session_id: s for s in client._run.read_sessions()}
    assert read[a].marks == (NOW,) and read[b].marks == ()


def test_mark_with_no_games_returns_empty(tmp_path):
    client, _, _ = make(tmp_path)
    client.start()
    status(client, "capturing")
    assert client.mark() == []


def test_recorder_arguments(tmp_path):
    client, _, calls = make(tmp_path)
    client.start()
    assert calls == [["--trace-recorder", str(client._run.path), "--app-pid", "42",
                      "--procmon", str(tmp_path / "Procmon64.exe")]]


def test_recorder_leaving_capturing_is_off(tmp_path):
    for later in ("stopping", "splitting", "done", "bogus"):
        (tmp_path / later).mkdir()
        client, proc, _ = make(tmp_path / later)
        client.start()
        status(client, "capturing")
        assert client.poll().phase == "on"
        status(client, later)
        if later == "done":
            proc.code = 0
        state = client.poll()
        assert state.phase == "off" and state.reason == messages.RECORDER_GONE


def test_launch_without_a_pid_is_recorded_with_root_pid_0(tmp_path):
    asked = []
    procs = [LiveProc(30, 4, 5, r"C:\g\a.exe")]
    client = TraceClient(tmp_path / "traces", tmp_path / "Procmon64.exe", lambda argv: FakeProc(),
                         {"windows": "11"}, clock=lambda: NOW, snapshot=lambda: list(procs),
                         start_time_of=lambda pid: asked.append(pid) or 77, app_pid=42)
    (tmp_path / "Procmon64.exe").write_text("x")
    client.start()
    status(client, "capturing")
    client.poll()
    sid = client.game_launched(None, 1, 2, {"title": "T", "game_dir": "C:/g", "exe": "C:/g/a.exe"})
    assert sid == "s001-0" and asked == []
    data = json.loads((client._run.sessions_dir / f"{sid}.json").read_text())
    assert (data["root_pid"], data["root_start"], data["launch_before"], data["launch_after"]) == (0, None, 1, 2)
    client.poll()
    assert client.mark() == [sid]  # the live tree found the root by exe


STOP = {"requested": NOW, "time": NOW, "close_games": False, "sessions": {}}


def test_a_stop_turns_tracing_off_without_an_announcement(tmp_path):
    for later in ("stopping", "splitting", "done"):
        (tmp_path / later).mkdir()
        client, proc, _ = make(tmp_path / later)
        client.start()
        status(client, "capturing")
        assert client.poll().phase == "on" and client.poll().announce is True
        client._run.write_stop(STOP)
        status(client, later)
        state = client.poll()
        assert (state.phase, state.reason, state.announce) == ("off", messages.STOPPED, False)
        assert client.game_launched(10, 1, 2, {"title": "T"}) is None  # nothing launched now joins the run


def test_a_recorder_that_closed_unexpectedly_is_still_announced(tmp_path):
    client, proc, _ = make(tmp_path)
    client.start()
    status(client, "capturing")
    client.poll()
    client._run.write_stop_request({"requested": NOW, "close_games": False})  # asked for, never taken
    proc.code = 1
    state = client.poll()
    assert (state.phase, state.reason, state.announce) == ("off", messages.RECORDER_GONE, True)


def test_a_stopped_recorder_seen_only_by_its_exit_is_a_stop(tmp_path):
    client, proc, _ = make(tmp_path)
    client.start()
    status(client, "capturing")
    client.poll()
    client._run.write_stop(STOP)
    proc.code = 0
    state = client.poll()
    assert (state.phase, state.reason, state.announce) == ("off", messages.STOPPED, False)


def test_a_failure_during_a_stop_is_announced(tmp_path):
    client, proc, _ = make(tmp_path)
    client.start()
    status(client, "capturing")
    client.poll()
    client._run.write_stop(STOP)
    status(client, "failed", messages.PROCMON_STOP_SLOW)
    state = client.poll()
    assert state.reason == messages.RECORDER_FAILED.format(error=messages.PROCMON_STOP_SLOW)
    assert state.phase == "off" and state.announce is True


def test_no_mark_is_made_once_tracing_is_off(tmp_path):
    procs = [LiveProc(10, 1, 77, None)]
    client, _, _ = make(tmp_path, snapshot=lambda: list(procs))
    client.start()
    status(client, "capturing")
    client.poll()
    sid = client.game_launched(10, 1, 2, {"title": "A"})
    assert client.mark() == [sid]
    client._run.write_stop(STOP)
    status(client, "done")
    assert client.mark() == []  # the game is still open, but the run has ended: nothing would hold the mark
    assert client._run.read_sessions()[0].marks == (NOW,)


def test_a_launch_that_returns_after_a_stop_was_taken_is_not_recorded(tmp_path):
    # the launch was let through as traced, then took a while (an administrator prompt); meanwhile the run was stopped
    client, _, _ = make(tmp_path)
    client.start()
    status(client, "capturing")
    assert client.poll().phase == "on"
    client._run.write_stop(STOP)
    status(client, "stopping")
    assert client.game_launched(10, 1, 2, {"title": "T", "game_dir": "C:/g"}) is None
    assert list(client._run.sessions_dir.glob("*.json")) == []
    assert client.state.phase == "off" and client.state.announce is False
