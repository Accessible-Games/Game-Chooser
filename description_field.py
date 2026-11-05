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

"""A read-only game description that a screen reader reads line by line; Enter on a link line opens it."""

import webbrowser

import wx

from catalog_view import link_at_line


class DescriptionField(wx.TextCtrl):
    """Multi-line, read-only text with a visible label just before it (so screen readers announce it).

    Tab leaves the field (no TE_PROCESS_TAB). The caller adds `label_ctrl` and the field to its sizer.
    """

    def __init__(self, parent, label: str):
        # Created first so it is the field's previous sibling, which is how Windows finds an edit's label
        label_ctrl = wx.StaticText(parent, label=label)
        super().__init__(parent, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        self.label_ctrl = label_ctrl
        self.SetName(label)
        self.Bind(wx.EVT_KEY_DOWN, self.on_key_down)

    def set_text(self, text: str):
        """Show new text, with the caret at the start. The same text again leaves the caret where it is."""
        if text == self.GetValue():
            return
        self.ChangeValue(text)
        self.SetInsertionPoint(0)

    def current_line(self) -> str:
        """The whole line the caret is on. A long line that wraps on screen still counts as one line."""
        text = self.GetValue()
        pos = min(self.GetInsertionPoint(), len(text))
        start = text.rfind("\n", 0, pos) + 1
        end = text.find("\n", pos)
        return text[start:] if end == -1 else text[start:end]

    def on_key_down(self, event):
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not event.HasAnyModifiers():
            url = link_at_line(self.current_line())
            if url:
                webbrowser.open(url)
                return
        event.Skip()
