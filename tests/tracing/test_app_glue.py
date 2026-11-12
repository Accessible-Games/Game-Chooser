"""The glue's state handling, without a running wx app (skipped when wxPython is not installed)."""
import contextlib

import pytest

wx = pytest.importorskip("wx")

from tracing import app_glue, messages  # noqa: E402
from tracing.client import TraceState  # noqa: E402


class Frame:
    def __init__(self):
        self.title_note = messages.TITLE_ON
        self.titles = []

    def update_title(self):
        self.titles.append(self.title_note)

    @contextlib.contextmanager
    def modal_dialog(self):
        yield


class Timer:
    stopped = False

    def Stop(self):
        self.stopped = True


class Client:
    def __init__(self, state):
        self.state = state

    def poll(self):
        return self.state


class Game:
    title = "Foo"


def make(state, phase="on"):
    glue = app_glue.TracingGlue.__new__(app_glue.TracingGlue)
    glue._frame, glue._timer, glue._client = Frame(), Timer(), Client(state)
    glue._phase, glue._told_off = phase, False
    glue.told = []
    glue._tell = glue.told.append
    return glue


STOPPED = TraceState("off", messages.STOPPED, announce=False)
GONE = TraceState("off", messages.RECORDER_GONE)


def test_a_stop_changes_the_title_and_opens_no_dialog():
    glue = make(STOPPED)
    glue._on_timer(None)
    assert glue._frame.titles == [messages.TITLE_OFF] and glue._timer.stopped
    assert glue.told == []


def test_an_unexpected_off_is_still_told_once():
    glue = make(GONE)
    glue._on_timer(None)
    glue._apply_state(GONE)
    assert glue._frame.titles == [messages.TITLE_OFF] and glue.told == [messages.RECORDER_GONE]


def test_the_first_launch_after_a_stop_says_why_tracing_is_off(monkeypatch):
    asked = []
    monkeypatch.setattr(app_glue.wx, "MessageBox", lambda text, *a, **k: asked.append(text) or wx.YES)
    glue = make(STOPPED)
    glue._on_timer(None)
    assert glue.before_launch(Game()) == ("untraced", None)
    assert glue.before_launch(Game()) == ("untraced", None)
    off = messages.LAUNCH_OFF.format(title="Foo")
    assert asked == [messages.STOPPED + " " + off, off]
    assert glue.told == []
