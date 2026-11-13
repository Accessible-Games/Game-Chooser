import threading

import pytest

from engine.errors import Cancelled, InstallFailed
from engine.jobs import InstallRequest, JobResult
from engine.queue import InstallQueue

WAIT = 10


def req(cid, action="install"):
    return InstallRequest(action, cid)


class Listener:
    def __init__(self):
        self.events = []
        self.cond = threading.Condition()
        self.threads = set()

    def _add(self, *event):
        with self.cond:
            self.events.append(event)
            self.threads.add(threading.current_thread())
            self.cond.notify_all()

    def job_started(self, job):
        self._add("started", job.request.catalog_id)

    def job_progress(self, job, progress):
        self._add("progress", job.request.catalog_id, progress.phase)

    def job_finished(self, job, result):
        self._add("finished", job.request.catalog_id)

    def job_failed(self, job, message):
        self._add("failed", job.request.catalog_id, message)

    def job_cancelled(self, job):
        self._add("cancelled", job.request.catalog_id)

    def queue_changed(self, waiting):
        self._add("queue", waiting)

    def wait_for(self, event):
        with self.cond:
            assert self.cond.wait_for(lambda: event in self.events, WAIT), self.events

    def kinds(self, kind):
        with self.cond:
            return [e for e in self.events if e[0] == kind]


class P:
    phase = "download"


class FakeEngine:
    """Blocks in run() until `gates[catalog_id]` is set (or the job is cancelled)."""

    def __init__(self, gates, entered, behavior):
        self.gates, self.entered, self.behavior = gates, entered, behavior

    def run(self, request, progress, cancel):
        cid = request.catalog_id
        self.entered.append(cid)
        progress(P())
        gate = self.gates.get(cid)
        if gate is not None:
            while not gate.wait(0.005):
                if cancel():
                    raise Cancelled()
        failure = self.behavior.get(cid)
        if failure:
            raise failure
        return JobResult(request.action, cid, cid, None, [])


class Rig:
    def __init__(self, tmp_path, gates=(), behavior=None):
        self.gates = {c: threading.Event() for c in gates}
        self.entered = []
        self.behavior = behavior or {}
        self.made = 0
        self.listener = Listener()
        self.log = tmp_path / "logs" / "queue.log"
        self.queue = InstallQueue(self.factory, self.listener, self.log)

    def factory(self):
        self.made += 1
        return FakeEngine(self.gates, self.entered, self.behavior)


def test_runs_one_at_a_time_in_order(tmp_path):
    r = Rig(tmp_path, gates=["a", "b"])
    r.queue.submit(req("a"), "A")
    r.queue.submit(req("b"), "B")
    r.listener.wait_for(("started", "a"))
    assert r.queue.busy() and r.queue.current().request.catalog_id == "a"
    assert r.entered == ["a"]
    assert ("started", "b") not in r.listener.events
    r.gates["a"].set()
    r.listener.wait_for(("finished", "a"))
    r.listener.wait_for(("started", "b"))
    r.gates["b"].set()
    r.listener.wait_for(("finished", "b"))
    assert r.entered == ["a", "b"]
    assert ("progress", "a", "download") in r.listener.events
    assert r.queue.shutdown(WAIT)
    assert threading.current_thread() not in r.listener.threads
    assert len(r.listener.threads) == 1


def test_waiting_count_and_queue_changed(tmp_path):
    r = Rig(tmp_path, gates=["a", "b", "c"])
    for c in "abc":
        r.queue.submit(req(c), c.upper())
    r.listener.wait_for(("started", "a"))
    r.listener.wait_for(("queue", 2))
    assert r.queue.waiting_count() == 2
    for c in "abc":
        r.gates[c].set()
    r.listener.wait_for(("finished", "c"))
    r.listener.wait_for(("queue", 0))
    assert r.queue.waiting_count() == 0
    r.queue.shutdown(WAIT)


def test_duplicate_submit_returns_existing_job(tmp_path):
    r = Rig(tmp_path, gates=["entombed"])
    j1 = r.queue.submit(InstallRequest("install", "entombed"), "Entombed")
    j2 = r.queue.submit(InstallRequest("update", "entombed"), "Entombed")
    assert j2 is j1 and r.queue.waiting_count() + int(r.queue.busy()) == 1
    r.listener.wait_for(("started", "entombed"))
    assert r.queue.submit(InstallRequest("repair", "entombed"), "Entombed") is j1
    r.gates["entombed"].set()
    r.listener.wait_for(("finished", "entombed"))
    assert r.entered == ["entombed"]
    r.queue.shutdown(WAIT)


def test_job_for_finds_a_games_queued_or_running_job(tmp_path):
    r = Rig(tmp_path, gates=["a", "b"])
    assert r.queue.job_for("a") is None
    ja, jb = r.queue.submit(req("a"), "A"), r.queue.submit(req("b", "update"), "B")
    r.listener.wait_for(("started", "a"))
    assert r.queue.job_for("a") is ja and r.queue.job_for("b") is jb
    r.queue.cancel(jb.id)
    assert r.queue.job_for("b") is None  # a cancelled waiting job is gone at once
    r.queue.cancel(ja.id)
    assert r.queue.job_for("a") is ja  # a running job counts until it has stopped
    r.listener.wait_for(("cancelled", "a"))
    assert r.queue.job_for("a") is None
    r.queue.shutdown(WAIT)


def test_resubmit_from_finished_callback_runs_a_new_job(tmp_path):
    r = Rig(tmp_path)
    resubmitted = []
    original = r.listener.job_finished

    def finished(job, result):
        original(job, result)
        if not resubmitted:
            resubmitted.append(r.queue.submit(req("a"), "A"))

    r.listener.job_finished = finished
    j1 = r.queue.submit(req("a"), "A")
    with r.listener.cond:
        assert r.listener.cond.wait_for(lambda: len(r.listener.kinds("finished")) == 2, WAIT)
    assert resubmitted[0] is not j1 and resubmitted[0].id != j1.id
    assert r.queue.shutdown(WAIT)
    assert r.entered == ["a", "a"]


def test_resubmit_while_running_job_is_cancelling_queues_a_new_job(tmp_path):
    r = Rig(tmp_path, gates=["a"])
    j1 = r.queue.submit(req("a"), "A")
    r.listener.wait_for(("started", "a"))
    r.queue.cancel(j1.id)
    j2 = r.queue.submit(req("a"), "A")
    assert j2 is not j1
    r.listener.wait_for(("cancelled", "a"))
    r.gates["a"].set()  # the second run waits on the same gate
    r.listener.wait_for(("finished", "a"))
    assert j1.state == "cancelled" and j2.state == "done"
    r.queue.shutdown(WAIT)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_base_exception_still_frees_the_worker_slot(tmp_path):
    r = Rig(tmp_path, behavior={"a": SystemExit()})
    j = r.queue.submit(req("a"), "A")
    r.listener.wait_for(("started", "a"))
    r.queue.shutdown(WAIT)
    assert j.state == "failed" and not r.queue.busy()


def test_cancel_queued_and_running(tmp_path):
    r = Rig(tmp_path, gates=["a", "b", "c"])
    ja = r.queue.submit(req("a"), "A")
    jb = r.queue.submit(req("b"), "B")
    jc = r.queue.submit(req("c"), "C")
    r.listener.wait_for(("started", "a"))
    r.queue.cancel(jb.id)
    r.listener.wait_for(("cancelled", "b"))
    assert jb.state == "cancelled" and r.queue.waiting_count() == 1
    r.queue.cancel(ja.id)
    assert ja.cancel_event.is_set()
    r.listener.wait_for(("cancelled", "a"))
    assert ja.state == "cancelled"
    r.listener.wait_for(("started", "c"))
    r.gates["c"].set()
    r.listener.wait_for(("finished", "c"))
    assert jc.state == "done"
    assert "b" not in r.entered
    r.queue.cancel(999)  # an unknown id is a no-op
    r.queue.shutdown(WAIT)


def test_cancel_all(tmp_path):
    r = Rig(tmp_path, gates=["a", "b"])
    r.queue.submit(req("a"), "A")
    r.queue.submit(req("b"), "B")
    r.listener.wait_for(("started", "a"))
    r.queue.cancel_all()
    r.listener.wait_for(("cancelled", "a"))
    r.listener.wait_for(("cancelled", "b"))
    assert r.entered == ["a"]
    assert r.queue.shutdown(WAIT)


def test_unexpected_error_is_reported_and_logged(tmp_path):
    r = Rig(tmp_path, behavior={"entombed": KeyError("boom")})
    j = r.queue.submit(req("entombed"), "Entombed")
    r.queue.submit(req("next"), "Next")
    r.listener.wait_for(("finished", "next"))
    ((_, _, message),) = r.listener.kinds("failed")
    assert message.startswith("Something went wrong installing Entombed")
    assert j.state == "failed"
    text = r.log.read_text(encoding="utf-8")
    assert "Traceback" in text and "KeyError" in text and "entombed" in text
    r.queue.shutdown(WAIT)


def test_install_failed_message_passed_through(tmp_path):
    r = Rig(tmp_path, behavior={"a": InstallFailed("Plain words.")})
    j = r.queue.submit(req("a"), "A")
    r.listener.wait_for(("failed", "a", "Plain words."))
    assert j.state == "failed"
    r.queue.shutdown(WAIT)


def test_fresh_engine_per_job(tmp_path):
    r = Rig(tmp_path)
    r.queue.submit(req("a"), "A")
    r.queue.submit(req("b"), "B")
    r.listener.wait_for(("finished", "b"))
    assert r.made == 2
    r.queue.shutdown(WAIT)


def test_shutdown_cancels_and_joins(tmp_path):
    r = Rig(tmp_path, gates=["a", "b"])
    ja = r.queue.submit(req("a"), "A")
    jb = r.queue.submit(req("b"), "B")
    r.listener.wait_for(("started", "a"))
    assert r.queue.shutdown(WAIT) is True
    assert ja.state == "cancelled" and jb.state == "cancelled"
    assert ("cancelled", "a") in r.listener.events and ("cancelled", "b") in r.listener.events
    assert not r.queue.busy() and r.queue.waiting_count() == 0
    assert r.entered == ["a"]


def test_shutdown_reports_false_if_job_ignores_cancel(tmp_path):
    release = threading.Event()
    entered = threading.Event()

    class Stubborn:
        def run(self, request, progress, cancel):
            entered.set()
            release.wait(WAIT)
            return JobResult("install", "a", "A", None, [])

    q = InstallQueue(Stubborn, Listener(), tmp_path / "q.log")
    q.submit(req("a"), "A")
    assert entered.wait(WAIT)
    assert q.shutdown(0.05) is False
    release.set()
    assert q.shutdown(WAIT) is True


def test_listener_error_does_not_kill_worker(tmp_path):
    r = Rig(tmp_path)
    original = r.listener.job_finished

    def boom(job, result):
        original(job, result)
        raise RuntimeError("listener broke")

    r.listener.job_finished = boom
    r.queue.submit(req("a"), "A")
    r.queue.submit(req("b"), "B")
    r.listener.wait_for(("finished", "b"))
    assert r.queue.shutdown(WAIT)
    assert "listener broke" in r.log.read_text(encoding="utf-8")
