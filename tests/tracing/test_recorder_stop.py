"""The recorder taking a stop request (--trace-stop), with and without closing the traced games."""
import os

from tests.tracing.test_recorder import Clock, FakeProcmon
from tracing import closing, messages, recorder
from tracing.closing import Closer, Identity
from tracing.live import LiveProc
from tracing.model import SessionInfo
from tracing.runfiles import RunDir
from tracing.splitrun import SplitResult

GIB = 1024**3
NOW = 133_500_000_000_000_000
APP_PID = 42
GAME = r"C:\Games\Foo"
NO_LISTS = {"asked_to_close": [], "ended": [], "not_closed": []}
MEDIUM, HIGH, SYSTEM = 0x2000, 0x3000, 0x4000
USER = "S-1-5-21-1-2-3-1001"
APP_IDENTITY = Identity(USER, MEDIUM)


def left(reason, *pids):
    """stop.json's not_closed entries."""
    return [{"pid": pid, "reason": reason} for pid in pids]


class World:
    """The machine's processes, and a closer that acts on them."""

    def __init__(self, clock):
        self.clock = clock
        self.procs: dict[int, LiveProc] = {}
        self.exit_at: dict[int, float] = {}
        self.windowless: set[int] = set()
        self.closes_when_asked: dict[int, float] = {}  # pid -> seconds it takes to close after being asked
        self.unendable: set[int] = set()
        self.asked: list[tuple[int, int]] = []
        self.ended: list[tuple[int, int]] = []
        self.ended_at: list[float] = []
        self.identity: dict[int, Identity | None] = {}  # who a process runs as; the app's user when not given

    def add(self, pid, ppid, start, image, until=None):
        self.procs[pid] = LiveProc(pid, ppid, start, image)
        if until is not None:
            self.exit_at[pid] = until
        return self.procs[pid]

    def snapshot(self):
        return [p for pid, p in self.procs.items()
                if pid not in self.exit_at or self.clock.t < self.exit_at[pid]]

    def ask_close(self, pid, start):
        self.asked.append((pid, start))
        if pid in self.closes_when_asked:
            self.exit_at[pid] = self.clock.t + self.closes_when_asked[pid]
        return pid not in self.windowless

    def end(self, pid, start):
        self.ended.append((pid, start))
        self.ended_at.append(self.clock.t)
        if pid in self.unendable:
            return False
        self.exit_at[pid] = self.clock.t
        return True


class WorldProcesses:
    """What the real closer (closing.Closer) sees of the fake machine."""

    def __init__(self, world):
        self.world = world

    def open(self, pid, terminate):
        return pid if any(p.pid == pid for p in self.world.snapshot()) else None

    def creation_time(self, handle):
        return self.world.procs[handle].start

    def identity(self, handle):
        return self.world.identity.get(handle, APP_IDENTITY)

    def close_windows(self, pid):
        return self.world.ask_close(pid, self.world.procs[pid].start)

    def terminate(self, handle):
        return self.world.end(handle, self.world.procs[handle].start)

    def close(self, handle):
        pass


def session(sid, pid, start, game_dir=GAME):
    return SessionInfo(sid, pid, start, start - 5, start - 1, {"game_dir": game_dir}, {}, ())


def make(tmp_path, *, sessions=(), closer=True, app_until=10_000.0, dies_at=None):
    clock = Clock()
    world = World(clock)
    run = RunDir(tmp_path / "run")
    (run.path / "sessions").mkdir(parents=True)
    for info in sessions:
        run.write_session(info)
    pm = FakeProcmon(clock, True, dies_at, run)
    states = []
    real = run.write_status

    def spy(state, error=None, recorder_pid=None):
        states.append(state)
        real(state, error=error, recorder_pid=recorder_pid)

    run.write_status = spy
    rec = recorder.Recorder(run, lambda: clock.t < app_until, pm, world.snapshot, lambda r: SplitResult([], []),
                            lambda: [], lambda: 10 * GIB, clock, clock.sleep,
                            closer=_closer(world, closer), now=lambda: NOW, protected_pids=(APP_PID,))
    return rec, run, pm, states, world, clock


def _closer(world, kind):
    """True: the world acts as the closer itself. "checked": the real closer, with its identity check, over it."""
    if kind == "checked":
        return Closer(APP_IDENTITY, WorldProcesses(world))
    return world if kind else None


def one_game(world, until=None):
    world.add(100, APP_PID, 1000, GAME + r"\game.exe", until)


def test_a_stop_request_ends_the_run_while_a_game_is_still_running(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[session("s1", 100, 1000)])
    one_game(world)
    run.write_stop_request({"requested": NOW - 7, "close_games": False})
    assert rec.run() == 0
    assert pm.stopped_at == 0.0 and pm.calls.count("stop") == 1
    assert states == ["starting", "capturing", "stopping", "splitting", "done"]
    assert run.read_status()["state"] == "done" and run.read_status()["error"] is None
    assert run.read_stop() == {"requested": NOW - 7, "time": NOW, "close_games": False,
                               "sessions": {"s1": NO_LISTS}}


def test_a_stop_request_made_later_is_taken_at_the_next_poll(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[session("s1", 100, 1000)])
    one_game(world)
    sleep = clock.sleep

    def sleep_then_ask(seconds):
        sleep(seconds)
        if clock.t == 20.0:
            run.write_stop_request({"requested": NOW, "close_games": False})

    rec.sleep = sleep_then_ask
    assert rec.run() == 0
    assert pm.stopped_at == 20.0


def test_without_a_request_the_run_goes_on_and_writes_no_stop_record(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[session("s1", 100, 1000)], app_until=5.0)
    one_game(world, until=30.0)
    assert rec.run() == 0
    assert pm.stopped_at >= 40.0 and run.read_stop() is None


def test_an_unreadable_request_is_not_a_stop(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, app_until=5.0)
    run.stop_request_path.write_text("{cut off", encoding="utf-8")
    assert rec.run() == 0
    assert pm.stopped_at >= 15.0 and run.read_stop() is None


def test_stop_lists_every_running_session_and_no_ended_one(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[
        session("s1", 100, 1000, r"C:\Games\A"), session("s2", 200, 2000, r"C:\Games\B"),
        session("s3", 300, 3000, r"C:\Games\C")])
    world.add(APP_PID, 1, 10, r"C:\GameChooser\game-chooser.exe")
    world.add(100, APP_PID, 1000, r"C:\Games\A\a.exe")
    world.add(200, APP_PID, 2000, r"C:\Games\B\b.exe", until=10.0)
    world.add(300, APP_PID, 3000, r"C:\Games\C\c.exe", until=10.0)
    world.add(301, 300, 3001, r"C:\Windows\System32\fondue.exe")  # the prompt a closed game left open
    sleep = clock.sleep

    def sleep_then_ask(seconds):
        sleep(seconds)
        if clock.t == 20.0:
            run.write_stop_request({"requested": NOW, "close_games": False})

    rec.sleep = sleep_then_ask
    assert rec.run() == 0
    assert run.read_stop()["sessions"] == {"s1": NO_LISTS, "s3": NO_LISTS}


def test_stop_with_no_session_running_still_ends_the_run(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[session("s1", 100, 1000)])
    run.write_stop_request({"requested": NOW, "close_games": True})
    assert rec.run() == 0
    assert pm.stopped_at == 0.0 and run.read_stop()["sessions"] == {}
    assert world.asked == [] and world.ended == []


def test_a_plain_stop_touches_no_process(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[session("s1", 100, 1000)])
    one_game(world)
    world.add(101, 100, 1001, GAME + r"\helper.exe")
    run.write_stop_request({"requested": NOW})
    assert rec.run() == 0
    assert world.asked == [] and world.ended == []
    assert run.read_stop()["close_games"] is False


def test_close_games_asks_every_process_then_ends_what_is_left_after_the_wait(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[session("s1", 100, 1000)])
    one_game(world)
    world.add(101, 100, 1001, r"C:\Users\u\AppData\Roaming\Guide\client.exe")
    world.windowless.add(101)
    world.closes_when_asked[100] = 2.0
    run.write_stop_request({"requested": NOW, "close_games": True})
    assert rec.run() == 0
    assert world.asked == [(100, 1000), (101, 1001)]
    assert world.ended == [(101, 1001)]
    assert world.ended_at == [recorder.CLOSE_WAIT_SECONDS]
    assert pm.stopped_at >= recorder.CLOSE_WAIT_SECONDS  # Process Monitor records until the games are gone
    assert run.read_stop() == {"requested": NOW, "time": NOW, "close_games": True, "sessions": {
        "s1": {"asked_to_close": [100], "ended": [101], "not_closed": []}}}
    assert states == ["starting", "capturing", "stopping", "splitting", "done"]


def test_close_games_does_not_wait_once_everything_has_closed(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[session("s1", 100, 1000)])
    one_game(world)
    world.closes_when_asked[100] = 2.0
    run.write_stop_request({"requested": NOW, "close_games": True})
    assert rec.run() == 0
    assert world.ended == [] and pm.stopped_at == 2.0
    assert run.read_stop()["sessions"] == {"s1": {"asked_to_close": [100], "ended": [], "not_closed": []}}


def test_a_process_that_exits_by_itself_during_the_wait_is_not_ended(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[session("s1", 100, 1000)])
    one_game(world)
    world.add(101, 100, 1001, GAME + r"\saver.exe", until=6.0)
    world.windowless.add(101)
    run.write_stop_request({"requested": NOW, "close_games": True})
    assert rec.run() == 0
    assert world.ended == [(100, 1000)]
    assert run.read_stop()["sessions"]["s1"] == {"asked_to_close": [100], "ended": [100], "not_closed": []}


def test_close_games_never_targets_anything_outside_a_running_session(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[
        session("s1", 100, 1000), session("over", 500, 5000, r"C:\Games\Over"),
        session("app", APP_PID, 10), session("me", os.getpid(), 20)])
    world.add(4, 0, 1, "System")
    world.add(APP_PID, 900, 10, r"C:\Python\python.exe")  # Game Chooser from source
    world.add(os.getpid(), 900, 20, r"C:\GameChooser\game-chooser.exe")  # this recorder
    world.add(900, 4, 5, r"C:\Windows\explorer.exe")
    world.add(901, 900, 6, r"C:\Windows\System32\notepad.exe")
    world.add(60, 4, 7, r"C:\GameChooser\procmon\Procmon64.exe")
    world.add(100, 900, 1000, GAME + r"\game.exe")
    world.add(101, 100, 1001, r"C:\Windows\System32\cmd.exe")
    world.add(102, 101, 1002, r"C:\Games\Foo\child.exe")
    world.add(103, 100, 1003, r"C:\Windows\System32\dllhost.exe")  # a name the capture drops
    world.add(104, 100, 1004, None)  # a program whose path could not be read
    world.add(500, 900, 5000, r"C:\Games\Over\over.exe", until=1.0)  # that session ended before the stop
    world.add(501, 900, 5001, r"C:\Games\Over\later.exe")  # never linked to it
    sleep = clock.sleep

    def sleep_then_ask(seconds):
        sleep(seconds)
        if clock.t == 5.0:
            run.write_stop_request({"requested": NOW, "close_games": True})

    rec.sleep = sleep_then_ask
    assert rec.run() == 0
    game_tree = [(100, 1000), (101, 1001), (102, 1002)]
    assert world.asked == game_tree and world.ended == game_tree
    stop = run.read_stop()
    assert sorted(stop["sessions"]) == ["app", "me", "s1"]
    assert stop["sessions"]["s1"] == {"asked_to_close": [100, 101, 102], "ended": [100, 101, 102],
                                      "not_closed": left(closing.NOT_CLOSED_PROTECTED, 103, 104)}
    assert stop["sessions"]["app"] == {"asked_to_close": [], "ended": [],
                                       "not_closed": left(closing.NOT_CLOSED_PROTECTED, APP_PID)}
    assert stop["sessions"]["me"] == {"asked_to_close": [], "ended": [],
                                      "not_closed": left(closing.NOT_CLOSED_PROTECTED, os.getpid())}


def test_a_game_launched_after_the_stop_was_taken_is_left_alone(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[session("s1", 100, 1000)])
    one_game(world)
    world.windowless.add(100)
    sleep = clock.sleep

    def sleep_then_launch(seconds):
        sleep(seconds)
        if clock.t == 3.0:
            world.add(200, APP_PID, 2000, r"C:\Games\Late\late.exe")
            run.write_session(session("s2", 200, 2000, r"C:\Games\Late"))

    rec.sleep = sleep_then_launch
    run.write_stop_request({"requested": NOW, "close_games": True})
    assert rec.run() == 0
    assert world.asked == [(100, 1000)] and world.ended == [(100, 1000)]
    assert sorted(run.read_stop()["sessions"]) == ["s1"]


def test_a_process_that_cannot_be_ended_is_recorded_and_the_run_still_ends(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[session("s1", 100, 1000)])
    one_game(world)
    world.unendable.add(100)
    run.write_stop_request({"requested": NOW, "close_games": True})
    assert rec.run() == 0
    assert world.ended == [(100, 1000)]
    assert run.read_stop()["sessions"]["s1"] == {"asked_to_close": [100], "ended": [],
                                                 "not_closed": left(closing.NOT_CLOSED_FAILED, 100)}
    assert pm.stopped_at == recorder.CLOSE_WAIT_SECONDS + recorder.END_WAIT_SECONDS
    assert "100" in run.log_path.read_text(encoding="utf-8")


def test_the_same_process_in_two_sessions_is_asked_and_ended_once(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[
        session("s1", 100, 1000), session("s2", 100, 1000)])
    one_game(world)
    run.write_stop_request({"requested": NOW, "close_games": True})
    assert rec.run() == 0
    assert world.asked == [(100, 1000)] and world.ended == [(100, 1000)]
    lists = {"asked_to_close": [100], "ended": [100], "not_closed": []}
    assert run.read_stop()["sessions"] == {"s1": lists, "s2": lists}


def test_process_monitor_dying_during_the_wait_ends_nothing(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[session("s1", 100, 1000)], dies_at=4.0)
    one_game(world)
    run.write_stop_request({"requested": NOW, "close_games": True})
    assert rec.run() == 1
    assert world.asked == [(100, 1000)] and world.ended == []
    assert run.read_status()["error"] == messages.PROCMON_STOPPED
    assert run.read_stop()["sessions"]["s1"] == {"asked_to_close": [100], "ended": [],
                                                 "not_closed": left(closing.NOT_CLOSED_PROCMON, 100)}


def test_a_closer_that_raises_does_not_stop_the_stop(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[session("s1", 100, 1000)])
    one_game(world)

    def boom(pid, start):
        raise OSError("access denied")

    world.ask_close = boom
    run.write_stop_request({"requested": NOW, "close_games": True})
    assert rec.run() == 0
    assert world.ended == [(100, 1000)]
    assert run.read_stop()["sessions"]["s1"] == {"asked_to_close": [], "ended": [100], "not_closed": []}


def test_close_games_without_a_closer_closes_nothing_and_says_so(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[session("s1", 100, 1000)], closer=False)
    one_game(world)
    run.write_stop_request({"requested": NOW, "close_games": True})
    assert rec.run() == 0
    assert pm.stopped_at == 0.0
    assert run.read_stop()["sessions"]["s1"] == {"asked_to_close": [], "ended": [],
                                                 "not_closed": left(closing.NOT_CLOSED_NO_CLOSER, 100)}


# --- the session files are written unelevated: they must not be able to point the elevated recorder at anything
# --- the app's user could not close without it

def forged(sid, root_pid, root_start, before, after, **game):
    return SessionInfo(sid, root_pid, root_start, before, after, game, {}, ())


def test_session_files_cannot_point_close_games_at_a_foreign_process(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, closer="checked", sessions=[
        session("s1", 100, 1000),
        # no start time: whatever process has pid 700
        forged("null-start", 700, None, 695, 699, game_dir=r"C:\x"),
        # no pid: the root is picked by a program path and a time window the file sets
        forged("by-exe", 0, None, 705, 715, game_dir=r"C:\y", exe=r"C:\Windows\regedit.exe"),
        # a game folder that holds the whole disk, since the beginning of time: every orphan on the machine
        forged("orphans", 999_999, 1, 0, 0, game_dir="C:\\"),
    ])
    world.add(APP_PID, 900, 10, r"C:\GameChooser\game-chooser.exe")
    world.add(900, 4, 5, r"C:\Windows\explorer.exe")
    world.add(4, 0, 1, "System")
    one_game(world)
    world.add(700, 600, 700, r"C:\Program Files\Backup\agent.exe")  # a service
    world.identity[700] = Identity("S-1-5-18", SYSTEM)
    world.add(710, 900, 710, r"C:\Windows\regedit.exe")  # the user's own, started as administrator
    world.identity[710] = Identity(USER, HIGH)
    world.add(720, 602, 720, r"C:\Users\other\app.exe")  # another user's
    world.identity[720] = Identity("S-1-5-21-1-2-3-1002", MEDIUM)
    world.add(730, 603, 730, r"C:\Protected\guard.exe")  # one that won't say who it runs as
    world.identity[730] = None
    run.write_stop_request({"requested": NOW, "close_games": True})
    assert rec.run() == 0
    assert world.asked == [(100, 1000)] and world.ended == [(100, 1000)]  # the real game, and nothing else
    sessions = run.read_stop()["sessions"]
    assert sessions["s1"] == {"asked_to_close": [100], "ended": [100], "not_closed": []}
    assert sessions["null-start"] == {"asked_to_close": [], "ended": [],
                                      "not_closed": left(closing.REFUSED_USER, 700)}
    assert sessions["by-exe"] == {"asked_to_close": [], "ended": [], "not_closed": left(closing.REFUSED_RIGHTS, 710)}
    assert sessions["orphans"]["asked_to_close"] == [] and sessions["orphans"]["ended"] == []
    assert {entry["pid"]: entry["reason"] for entry in sessions["orphans"]["not_closed"]} == {
        700: closing.REFUSED_USER, 720: closing.REFUSED_USER,
        730: closing.REFUSED_UNKNOWN}
    # one poll to see the ended game gone; the refused ones are not waited for (that would be 5 s more)
    assert pm.stopped_at == recorder.CLOSE_WAIT_SECONDS + recorder.POLL_SECONDS


def test_a_game_started_through_the_administrator_prompt_is_not_closed_and_says_why(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, closer="checked", sessions=[
        session("s1", 100, 1000), session("s2", 200, 2000, r"C:\Games\Admin")])
    world.add(APP_PID, 1, 10, r"C:\GameChooser\game-chooser.exe")
    one_game(world)
    world.closes_when_asked[100] = 1.0
    world.add(200, APP_PID, 2000, r"C:\Games\Admin\game.exe")
    world.identity[200] = Identity(USER, HIGH)
    run.write_stop_request({"requested": NOW, "close_games": True})
    assert rec.run() == 0
    assert world.asked == [(100, 1000)] and world.ended == []
    assert pm.stopped_at == 1.0  # the refused game is not waited for
    assert run.read_stop()["sessions"]["s2"] == {"asked_to_close": [], "ended": [],
                                                 "not_closed": left(closing.REFUSED_RIGHTS, 200)}
    assert "administrator prompt" in run.log_path.read_text(encoding="utf-8")


def test_the_recorder_claims_the_request_it_takes(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path)
    run.write_stop_request({"requested": NOW, "close_games": False})
    assert rec.run() == 0
    assert not run.stop_request_path.exists() and run.stop_taken_path.exists()
    assert run.withdraw_stop_request() == "taken"


def test_a_claim_that_fails_is_tried_again_at_the_next_poll(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path)
    run.write_stop_request({"requested": NOW, "close_games": False})
    real = run.claim_stop_request
    calls = []

    def flaky():
        calls.append(clock.t)
        if len(calls) < 3:
            raise PermissionError("held")
        return real()

    run.claim_stop_request = flaky
    assert rec.run() == 0
    assert pm.stopped_at == 1.0 and run.read_stop() is not None
    assert "stop request" in run.log_path.read_text(encoding="utf-8")


class Died(BaseException):
    """The recorder process ending in the middle of its work (nothing after it runs in a real death)."""


def test_stop_json_is_true_after_each_act_should_the_recorder_die_partway(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, sessions=[session("s1", 100, 1000)])
    one_game(world)
    world.add(101, 100, 1001, GAME + r"\helper.exe")
    world.add(102, 100, 1002, GAME + r"\other.exe")
    on_disk = {}
    ask, end = world.ask_close, world.end

    def ask_and_look(pid, start):
        on_disk[("ask", pid)] = run.read_stop()["sessions"]["s1"]
        return ask(pid, start)

    def end_then_die(pid, start):
        on_disk[("end", pid)] = run.read_stop()["sessions"]["s1"]
        if pid == 102:
            raise Died()
        return end(pid, start)

    world.ask_close, world.end = ask_and_look, end_then_die
    run.write_stop_request({"requested": NOW, "close_games": True})
    try:
        rec.run()
    except Died:
        pass
    # what the file said at the moment each act began: everything done before it
    assert on_disk[("ask", 100)]["asked_to_close"] == []
    assert on_disk[("ask", 101)]["asked_to_close"] == [100]
    assert on_disk[("ask", 102)]["asked_to_close"] == [100, 101]
    assert on_disk[("end", 100)] == {"asked_to_close": [100, 101, 102], "ended": [], "not_closed": []}
    assert on_disk[("end", 101)]["ended"] == [100]
    assert on_disk[("end", 102)]["ended"] == [100, 101]  # the death comes here: the file already says so


def test_a_refusal_is_on_disk_before_the_next_act(tmp_path):
    rec, run, pm, states, world, clock = make(tmp_path, closer="checked", sessions=[session("s1", 100, 1000)])
    world.add(APP_PID, 1, 10, r"C:\GameChooser\game-chooser.exe")
    one_game(world)
    world.identity[100] = Identity(USER, HIGH)
    world.add(101, 100, 1001, GAME + r"\helper.exe")
    seen = []
    ask = world.ask_close
    world.ask_close = lambda pid, start: seen.append(run.read_stop()["sessions"]["s1"]["not_closed"]) or ask(pid, start)
    run.write_stop_request({"requested": NOW, "close_games": True})
    assert rec.run() == 0
    assert seen == [left(closing.REFUSED_RIGHTS, 100)]  # seen while 101 was being asked
