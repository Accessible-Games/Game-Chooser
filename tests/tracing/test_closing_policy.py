"""Who the closer will touch: only a process of Game Chooser's user with no more rights than Game Chooser.

The check is made on the opened process, after its start time and before anything is done to it. Here the
processes are fakes; `test_closing.py` runs the same closer on real ones.
"""
import pytest

from tracing import closing
from tracing.closing import Closer, Identity, Refused

MEDIUM, HIGH, SYSTEM, LOW = 0x2000, 0x3000, 0x4000, 0x1000
USER = "S-1-5-21-1-2-3-1001"
APP = Identity(USER, MEDIUM)


class FakeProcesses:
    """ProcessAccess over a dict pid -> (start, Identity or None); records what was done."""

    def __init__(self, procs):
        self.procs = dict(procs)
        self.open_handles = set()
        self.identity_checks = []
        self.posted = []
        self.terminated = []

    def open(self, pid, terminate):
        if pid not in self.procs:
            return None
        self.open_handles.add(pid)
        return pid

    def creation_time(self, handle):
        return self.procs[handle][0]

    def identity(self, handle):
        self.identity_checks.append(handle)
        return self.procs[handle][1]

    def close_windows(self, pid):
        assert pid in self.open_handles  # only while the process is held open
        self.posted.append(pid)
        return True

    def terminate(self, handle):
        self.terminated.append(handle)
        return True

    def close(self, handle):
        self.open_handles.discard(handle)


def closer(identity, limit=APP):
    processes = FakeProcesses({10: (500, identity)})
    return Closer(limit, processes), processes


def test_a_process_of_the_apps_user_with_the_apps_rights_is_asked_and_ended():
    c, p = closer(Identity(USER, MEDIUM))
    assert c.ask_close(10, 500) is True and c.end(10, 500) is True
    assert p.posted == [10] and p.terminated == [10] and not p.open_handles


def test_a_process_with_fewer_rights_is_closed_too():
    c, p = closer(Identity(USER, LOW))
    assert c.ask_close(10, 500) is True and c.end(10, 500) is True


@pytest.mark.parametrize("identity, reason", [
    (Identity("S-1-5-21-1-2-3-1002", MEDIUM), closing.REFUSED_USER),
    (Identity("S-1-5-18", SYSTEM), closing.REFUSED_USER),
    (Identity(USER, HIGH), closing.REFUSED_RIGHTS),
    (Identity(USER, MEDIUM + 1), closing.REFUSED_RIGHTS),
    (None, closing.REFUSED_UNKNOWN),
])
def test_a_foreign_process_is_refused_before_anything_is_done(identity, reason):
    c, p = closer(identity)
    for action in (c.ask_close, c.end):
        with pytest.raises(Refused) as refused:
            action(10, 500)
        assert refused.value.reason == reason
    assert p.posted == [] and p.terminated == [] and not p.open_handles


def test_nothing_is_closed_when_the_apps_own_identity_is_unknown():
    c, p = closer(Identity(USER, MEDIUM), limit=None)
    for action in (c.ask_close, c.end):
        with pytest.raises(Refused) as refused:
            action(10, 500)
        assert refused.value.reason == closing.REFUSED_NO_LIMIT
    assert p.posted == [] and p.terminated == []


def test_another_process_with_the_pid_is_not_refused_just_not_there():
    c, p = closer(Identity("S-1-5-18", SYSTEM))
    assert c.ask_close(10, 501) is False and c.end(10, 501) is False
    assert c.ask_close(11, 500) is False and c.end(11, 500) is False
    assert p.identity_checks == [] and p.posted == [] and p.terminated == [] and not p.open_handles


def test_the_reasons_are_plain_words():
    assert "another user" in closing.REFUSED_USER
    assert "administrator prompt" in closing.REFUSED_RIGHTS
