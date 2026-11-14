#!/usr/bin/env python3
# Game Chooser - A desktop game library manager
# Copyright (C) 2025 Alec Olson
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""Bridges from the install queue's threads to the UI thread.

`WxQueueListener` is called on the queue's notifier thread and only posts to MainFrame's `on_job_*` handlers.
`WxInstallerUI` is called on the engine's worker thread: each method posts a prompt to the UI thread and blocks
on a threading.Event until the UI answers it. Neither touches a window off the UI thread. On quit,
`release_all()` (UI thread) answers every open prompt as cancelled and makes later prompts return at once, so
the worker can never hang on a prompt nobody will answer.
"""

import logging
import threading
from pathlib import Path

import wx

from engine import messages

log = logging.getLogger(__name__)

WAIT_POLL_SECONDS = 0.25  # how often a blocked worker also checks the installer's own finished event
TIMER_MS = 1000  # how often the UI checks whether the installer has finished, to take Done away


def call_after(fn, *args) -> bool:
    """wx.CallAfter from any thread. False (and nothing posted) once the app is gone."""
    try:
        wx.CallAfter(fn, *args)
        return True
    except Exception:
        log.debug("The app closed before %s could run", getattr(fn, "__name__", fn))
        return False


SLOW_CUE_MS = 600  # work still running after this long gets a cue, so the player knows something is happening


def run_in_background(work, done, name: str, slow=None, default=None, post=call_after, later=None):
    """Run `work()` on a daemon thread, then `done(result)` on the UI thread through `post` (nothing runs once
    the app is gone). A failing `work()` is logged and `done(default)` still runs. If the work is still going
    after SLOW_CUE_MS, `slow()` runs on the UI thread (for example a notification). Call on the UI thread.
    `later(ms, fn)` defaults to wx.CallLater."""
    finished = threading.Event()

    def run():
        try:
            result = work()
        except Exception:
            log.exception("Background work %s failed", name)
            result = default
        finished.set()
        post(done, result)

    if slow is not None:
        (later or wx.CallLater)(SLOW_CUE_MS, lambda: None if finished.is_set() else slow())
    thread = threading.Thread(target=run, name=name, daemon=True)
    thread.start()
    return thread


def _call_if_alive(frame, name, *args):
    if frame:  # a destroyed window is falsy; its events are dropped
        getattr(frame, name)(*args)


class JobOutcomes:
    """How jobs ended that the library hasn't been brought up to date with yet. Thread-safe.

    The listener adds each end here (notifier thread) before posting it to the UI thread. Whoever applies it
    first claims it with `take`: MainFrame's normal handler, or `drain` when quitting, for ends whose UI event
    would otherwise never run (a job ending during the quit question or the shutdown wait). So each is applied
    exactly once."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items: dict[int, tuple] = {}  # job id -> (kind, job, result or message), in arrival order

    def add(self, kind: str, job, detail=None):
        with self._lock:
            self._items[job.id] = (kind, job, detail)

    def take(self, job) -> bool:
        """Claim a job's end for applying. False if it was already claimed (or never added)."""
        with self._lock:
            return self._items.pop(job.id, None) is not None

    def drain(self) -> list[tuple]:
        """Claim every end not yet applied: (kind, job, detail), oldest first."""
        with self._lock:
            items = list(self._items.values())
            self._items.clear()
            return items


class WxQueueListener:
    """The install queue's listener: every call is passed to `frame.on_<name>` on the UI thread. Job ends are
    also recorded in `outcomes` first, so quitting can apply ones whose UI event never ran."""

    def __init__(self, frame, post=call_after, outcomes: JobOutcomes | None = None):
        self.frame = frame
        self._post = post
        self.outcomes = outcomes if outcomes is not None else JobOutcomes()

    def _send(self, name, *args):
        self._post(_call_if_alive, self.frame, name, *args)

    def job_started(self, job):
        self._send("on_job_started", job)

    def job_progress(self, job, progress):
        self._send("on_job_progress", job, progress)

    def job_finished(self, job, result):
        self.outcomes.add("finished", job, result)
        self._send("on_job_finished", job, result)

    def job_failed(self, job, message):
        self.outcomes.add("failed", job, message)
        self._send("on_job_failed", job, message)

    def job_cancelled(self, job):
        self.outcomes.add("cancelled", job)
        self._send("on_job_cancelled", job)

    def queue_changed(self, waiting):
        self._send("on_queue_changed", waiting)


class _Gate:
    """One prompt's answer: the first answer wins (Done, the timer, Cancel and quitting can race)."""

    def __init__(self, cancelled):
        self.cancelled = cancelled
        self.value = cancelled
        self._event = threading.Event()
        self._lock = threading.Lock()

    @property
    def answered(self) -> bool:
        return self._event.is_set()

    def answer(self, value) -> bool:
        with self._lock:
            if self._event.is_set():
                return False
            self.value = value
            self._event.set()
            return True

    def cancel(self) -> bool:
        return self.answer(self.cancelled)

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


class WxInstallerUI:
    """The engine's InstallerUI. The three protocol methods run on the worker thread; the rest on the UI thread."""

    def __init__(self, frame, post=call_after):
        self.frame = frame
        self._post = post
        self._lock = threading.Lock()
        self._pending: set[_Gate] = set()
        self._closing = False
        self._wait: tuple[_Gate, threading.Event] | None = None  # the shown "Waiting for the installer" (UI)
        self._timer = None

    # ---- the InstallerUI protocol (worker thread) ------------------------------------------------------------

    def announce_target(self, title: str, target: Path) -> None:
        """Copy the folder to the clipboard and say where to install; returns once the player has read it.
        After quitting it returns at once (the job is already cancelled; the engine stops at its next check)."""
        self._ask(None, self._show_target, title, Path(target))

    def wait_for_installer(self, title: str, finished: threading.Event) -> bool:
        """True on Done or once the installer has finished; False on Cancel or quitting."""
        return bool(self._ask(False, self._show_wait, title, finished, finished=finished))

    def locate_folder(self, title: str) -> Path | None:
        return self._ask(None, self._show_locate, title)

    def _ask(self, cancelled, show, *args, finished: threading.Event | None = None):
        gate = _Gate(cancelled)
        with self._lock:
            if self._closing:
                return cancelled
            self._pending.add(gate)
        try:
            if not self._post(show, gate, *args):
                gate.cancel()  # no UI thread to answer
            while not gate.wait(WAIT_POLL_SECONDS):
                if finished is not None and finished.is_set():
                    gate.answer(True)
            return gate.value
        finally:
            with self._lock:
                self._pending.discard(gate)

    # ---- UI thread -------------------------------------------------------------------------------------------

    def release_all(self):
        """Quitting: answer every open prompt as cancelled; later prompts return cancelled at once."""
        with self._lock:
            self._closing = True
            pending = list(self._pending)
        for gate in pending:
            gate.cancel()
        self._end_wait()

    def cancel_wait(self):
        """The player's Cancel: the installer wait (if one is shown) answers False."""
        wait = self._wait
        if wait is None:
            return
        wait[0].cancel()
        self._end_wait()

    def waiting(self) -> bool:
        """Whether "Waiting for the installer" with Done is showing for a wait that is still open."""
        return self._wait is not None and not self._wait[0].answered

    def _stale(self, gate) -> bool:
        if self._closing or gate.answered or not self.frame:
            gate.cancel()
            return True
        return False

    def _show_target(self, gate, title, target):
        if self._stale(gate):
            return
        copied = False
        try:
            if wx.TheClipboard.Open():
                try:
                    copied = wx.TheClipboard.SetData(wx.TextDataObject(str(target)))
                    wx.TheClipboard.Flush()  # the path stays on the clipboard after Game Chooser closes
                finally:
                    wx.TheClipboard.Close()
        except Exception:
            log.warning("Couldn't put the install folder on the clipboard", exc_info=True)
        if not copied:
            log.warning("The clipboard couldn't be opened; the install folder isn't on it")
        text = messages.INSTALLER_TARGET if copied else messages.INSTALLER_TARGET_NO_CLIPBOARD
        try:
            with self.frame.modal_dialog():
                wx.MessageBox(text.format(path=target),
                              messages.INSTALLER_TARGET_TITLE.format(title=title),
                              wx.OK | wx.ICON_INFORMATION, self.frame)
        finally:
            gate.answer(None)

    def _show_wait(self, gate, title, finished):
        if self._stale(gate):
            return
        if finished.is_set():
            gate.answer(True)
            return
        self._end_wait()  # never two at once
        self._wait = (gate, finished)
        self.frame.show_installer_wait(self._done)
        if self._timer is None:
            self._timer = wx.Timer()
            self._timer.Bind(wx.EVT_TIMER, self.check_wait)
        self._timer.Start(TIMER_MS)

    def _done(self):
        wait = self._wait
        if wait is not None:
            wait[0].answer(True)
        self._end_wait()

    def check_wait(self, event=None):
        """Take Done away once the wait is over (the installer finished, or the worker stopped waiting).
        The 1 s timer calls this; MainFrame also does on every queue event, so Done never outlives its job."""
        wait = self._wait
        if wait is None:
            self._end_wait()
            return
        gate, finished = wait
        if finished.is_set():
            gate.answer(True)
        if gate.answered:
            self._end_wait()

    def _end_wait(self):
        if self._timer is not None:
            self._timer.Stop()
        if self._wait is None:
            return
        self._wait = None
        if self.frame:
            self.frame.hide_installer_wait()

    def _show_locate(self, gate, title):
        if self._stale(gate):
            return
        path = None
        try:
            with wx.DirDialog(self.frame, messages.LOCATE_FOLDER.format(title=title),
                              style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST) as dlg, self.frame.modal_dialog():
                if dlg.ShowModal() == wx.ID_OK and dlg.GetPath():
                    path = Path(dlg.GetPath())
        finally:
            gate.answer(path)
