"""The queue and installer bridges, without a running wx app: posting is replaced by a recorder."""

import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("wx")

import wx_bridge  # noqa: E402
from wx_bridge import WxInstallerUI, WxQueueListener  # noqa: E402


class Recorder:
    """Stands in for wx.CallAfter: records each post instead of running it on a UI thread."""

    def __init__(self, ok=True):
        self.posts = []
        self.ok = ok
        self.posted = threading.Event()

    def __call__(self, fn, *args):
        self.posts.append((fn, args))
        self.posted.set()
        return self.ok


class FakeFrame:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("on_"):
            return lambda *args: self.calls.append((name, args))
        raise AttributeError(name)


def in_thread(fn, *args):
    out = {}
    thread = threading.Thread(target=lambda: out.setdefault("value", fn(*args)), daemon=True)
    thread.start()
    return thread, out


def test_listener_posts_every_queue_event_to_the_frames_handler():
    post, frame = Recorder(), FakeFrame()
    listener = WxQueueListener(frame, post=post)
    listener.job_started("J")
    listener.job_progress("J", "P")
    listener.job_finished("J", "R")
    listener.job_failed("J", "M")
    listener.job_cancelled("J")
    listener.queue_changed(2)
    for fn, args in post.posts:  # what the UI thread would run
        fn(*args)
    assert frame.calls == [("on_job_started", ("J",)), ("on_job_progress", ("J", "P")),
                           ("on_job_finished", ("J", "R")), ("on_job_failed", ("J", "M")),
                           ("on_job_cancelled", ("J",)), ("on_queue_changed", (2,))]


def test_listener_drops_events_for_a_closed_window():
    post = Recorder()
    frame = FakeFrame()
    frame.__class__ = type("Dead", (FakeFrame,), {"__bool__": lambda self: False})
    WxQueueListener(frame, post=post).job_started("J")
    fn, args = post.posts[0]
    fn(*args)
    assert frame.calls == []


def test_prompts_return_the_ui_answer():
    post = Recorder()
    ui = WxInstallerUI(FakeFrame(), post=post)
    thread, out = in_thread(ui.locate_folder, "Entombed")
    assert post.posted.wait(2)
    fn, (gate, title) = post.posts[0]
    assert fn == ui._show_locate and title == "Entombed"
    gate.answer(Path("C:/Games/Entombed"))
    thread.join(2)
    assert out["value"] == Path("C:/Games/Entombed")


def test_wait_for_installer_returns_true_once_the_installer_finishes_without_the_ui():
    post = Recorder()
    ui = WxInstallerUI(FakeFrame(), post=post)
    finished = threading.Event()
    thread, out = in_thread(ui.wait_for_installer, "Entombed", finished)
    assert post.posted.wait(2)
    finished.set()  # nobody answers on the UI side
    thread.join(2)
    assert not thread.is_alive() and out["value"] is True


def test_release_all_unblocks_every_prompt_as_cancelled_and_later_ones_return_at_once():
    post = Recorder()
    ui = WxInstallerUI(FakeFrame(), post=post)
    waits = [in_thread(ui.wait_for_installer, "A", threading.Event()), in_thread(ui.locate_folder, "B"),
             in_thread(ui.announce_target, "C", Path("C:/Games/C"))]
    deadline = time.monotonic() + 2
    while len(post.posts) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(post.posts) == 3
    ui.release_all()
    for thread, _ in waits:
        thread.join(2)
        assert not thread.is_alive()
    assert waits[0][1]["value"] is False and waits[1][1]["value"] is None and waits[2][1]["value"] is None
    before = len(post.posts)
    started = time.monotonic()
    assert ui.wait_for_installer("D", threading.Event()) is False
    assert ui.locate_folder("D") is None
    assert ui.announce_target("D", Path("C:/D")) is None
    assert time.monotonic() - started < 0.5 and len(post.posts) == before  # nothing posted after quitting


def test_a_prompt_that_cannot_be_posted_is_cancelled():
    ui = WxInstallerUI(FakeFrame(), post=Recorder(ok=False))
    assert ui.wait_for_installer("A", threading.Event()) is False
    assert ui.locate_folder("A") is None


def test_the_first_answer_wins():
    gate = wx_bridge._Gate(False)
    assert gate.answer(True)
    assert not gate.cancel()
    assert gate.value is True
