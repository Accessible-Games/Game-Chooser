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

"""The install progress area: a gauge, a status line, Done (while an installer runs) and Cancel.

UI thread only. It shows the queue's current job, never the selected game: MainFrame drives every copy of it
(one beside each tab's action button) with the same calls. It never moves focus into itself.
"""

from typing import Callable

import wx

from engine import messages
from engine.messages import Progress

GAUGE_RANGE = 1000


class _FocusableGauge(wx.Gauge):
    """A gauge Tab stops on, so a screen reader can read the progress on demand."""

    def AcceptsFocus(self):
        return True

    def AcceptsFocusFromKeyboard(self):
        return True


class ProgressArea(wx.Panel):
    """Tab order: gauge ("Install progress"), status ("Install status"), Done (hidden), Cancel."""

    def __init__(self, parent):
        super().__init__(parent)
        self.on_cancel: Callable[[], None] = lambda: None
        self._on_done: Callable[[], None] | None = None
        self._title = ""
        self._label = ""
        self._waiting = 0

        sizer = wx.BoxSizer(wx.HORIZONTAL)
        # Each hidden label is the control's previous sibling, which is where a screen reader finds its name
        gauge_label = wx.StaticText(self, label=messages.PROGRESS_GAUGE_NAME)
        gauge_label.Hide()
        self.gauge = _FocusableGauge(self, range=GAUGE_RANGE, size=(200, -1), style=wx.GA_HORIZONTAL)
        self.gauge.SetName(messages.PROGRESS_GAUGE_NAME)
        self.gauge.SetLabel(messages.PROGRESS_GAUGE_NAME)
        status_label = wx.StaticText(self, label=messages.PROGRESS_STATUS_NAME)
        status_label.Hide()
        self.status = wx.TextCtrl(self, style=wx.TE_READONLY, size=(380, -1))
        self.status.SetName(messages.PROGRESS_STATUS_NAME)
        self.done_btn = wx.Button(self, label=messages.PROGRESS_DONE)
        self.done_btn.Hide()
        self.cancel_btn = wx.Button(self, label=messages.PROGRESS_CANCEL)

        sizer.Add(self.gauge, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        sizer.Add(self.status, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        sizer.Add(self.done_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        sizer.Add(self.cancel_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        self.SetSizer(sizer)

        self.done_btn.Bind(wx.EVT_BUTTON, self._done_pressed)
        self.cancel_btn.Bind(wx.EVT_BUTTON, lambda event: self.on_cancel())

    # ---- the job ---------------------------------------------------------------------------------------------

    def show_job(self, title: str):
        """A new job is running: empty gauge, "Preparing <title>", no Done."""
        self._title = title
        self.gauge.SetValue(0)
        self.hide_done_button()
        self.set_label(messages.JOB_PREPARING.format(title=title))

    def update(self, progress: Progress, waiting: int):
        """A progress report: the gauge (it pulses when the total is unknown) and the phase."""
        if progress.total:
            done = max(0, min(progress.done or 0, progress.total))
            self.gauge.SetValue(done * GAUGE_RANGE // progress.total)
        else:
            self.gauge.Pulse()
        self._waiting = waiting
        self.set_label(messages.phase_label(progress, self._title))

    def set_label(self, label: str):
        """Show a phase in the status line (the waiting count stays)."""
        self._label = label
        self._render()

    def set_waiting(self, waiting: int):
        self._waiting = waiting
        self._render()

    def _render(self):
        text = messages.with_waiting(self._label, self._waiting)
        if self.status.GetValue() != text:
            self.status.ChangeValue(text)  # no EVT_TEXT, and an unchanged line keeps its caret

    # ---- Done ------------------------------------------------------------------------------------------------

    def show_done_button(self, on_done: Callable[[], None]):
        self._on_done = on_done
        if not self.done_btn.IsShown():
            self.done_btn.Show()
            self.Layout()

    def hide_done_button(self):
        self._on_done = None
        if self.done_btn.IsShown():
            moved = self.done_btn.HasFocus()
            self.done_btn.Hide()
            self.Layout()
            if moved:  # focus was already here; keep it in the area rather than lose it
                self.cancel_btn.SetFocus()

    def _done_pressed(self, event):
        on_done = self._on_done
        if on_done is not None:
            on_done()

    def has_focus_inside(self) -> bool:
        focus = wx.Window.FindFocus()
        while focus is not None:
            if focus is self:
                return True
            focus = focus.GetParent()
        return False
