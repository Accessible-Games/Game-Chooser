"""Dialogs for installing catalog games. UI thread only; the wording and decisions are in install_choices."""

import logging
from pathlib import Path

import wx

from engine import messages
from install_choices import (InstallChoice, build_choice, chosen_optional, default_library_index, extra_label,
                             intro_text, library_choice_label, library_choice_labels, ok_label, selected_library,
                             window_title)
from library_manager import LibraryError

log = logging.getLogger(__name__)

__all__ = ["InstallChoice", "InstallConfirmDialog"]

_WRAP = 450


class InstallConfirmDialog(wx.Dialog):
    """Confirm an install (or update, or adding extras), driven by the plan.

    The intro StaticText is the first child, so it comes first in the reading order; its size and "Includes"
    text are read when the dialog opens. After ShowModal() == wx.ID_OK, get_choice() says what was chosen.
    """

    def __init__(self, parent, plan, service):
        super().__init__(parent, title=window_title(plan), style=wx.DEFAULT_DIALOG_STYLE)
        self.plan = plan
        self.service = service
        self._choice = None
        self._entry_titles = {i: e.get("title", "") for i, e in service.entries.items()}
        self._extra_boxes = {}
        self._radio = None
        self._folder = None

        root = wx.BoxSizer(wx.VERTICAL)
        self.intro = wx.StaticText(self, label="")
        root.Add(self.intro, 0, wx.ALL | wx.EXPAND, 10)

        self._build_where(root)
        self._build_extras(root)
        self._build_dependencies(root)

        for warning in plan.warnings:
            text = wx.StaticText(self, label=warning)
            text.Wrap(_WRAP)
            root.Add(text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        self.blocked_text = wx.StaticText(self, label="")
        root.Add(self.blocked_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.ok_btn = wx.Button(self, wx.ID_OK, ok_label(plan))
        self.ok_btn.SetDefault()
        self.cancel_btn = wx.Button(self, wx.ID_CANCEL, messages.CONFIRM_BUTTON_CANCEL)
        buttons.Add(self.ok_btn, 0, wx.RIGHT, 8)
        buttons.Add(self.cancel_btn)
        root.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        self.SetSizer(root)
        self.SetEscapeId(wx.ID_CANCEL)
        self.SetAffirmativeId(wx.ID_OK)

        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        self._update_intro()
        self._update_blocked()
        self.CentreOnParent()

    # ---- building ------------------------------------------------------------------------------------------------

    def _build_where(self, root):
        plan = self.plan
        if len(plan.libraries) >= 2:
            self._radio = wx.RadioBox(self, label=messages.CONFIRM_WHERE,
                                      choices=library_choice_labels(plan.libraries), majorDimension=1,
                                      style=wx.RA_SPECIFY_COLS)
            self._radio.SetSelection(default_library_index(plan))
            self._radio.Bind(wx.EVT_RADIOBOX, lambda e: self._update_blocked())
            root.Add(self._radio, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        elif plan.libraries:
            text = wx.StaticText(self, label=messages.CONFIRM_INSTALLS_INTO.format(
                library=library_choice_label(plan.libraries[0])))
            root.Add(text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        else:
            row = wx.BoxSizer(wx.HORIZONTAL)
            label = wx.StaticText(self, label=messages.CONFIRM_FOLDER_LABEL)
            self._folder = wx.TextCtrl(self, value=str(plan.suggested_folder or ""), size=(320, -1),
                                       name=messages.CONFIRM_FOLDER_NAME)
            browse = wx.Button(self, label=messages.CONFIRM_BROWSE)
            browse.Bind(wx.EVT_BUTTON, self.on_browse)
            self._folder.Bind(wx.EVT_TEXT, lambda e: self._update_blocked())
            row.Add(label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
            row.Add(self._folder, 1, wx.RIGHT, 6)
            row.Add(browse)
            root.Add(row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

    def _build_extras(self, root):
        if not self.plan.extras:
            return
        box = wx.StaticBox(self, label=messages.CONFIRM_EXTRAS_BOX)
        sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
        for extra in self.plan.extras:
            check = wx.CheckBox(box, label=extra_label(extra))
            if extra.required:
                check.SetValue(True)
                check.Disable()
            else:
                check.Bind(wx.EVT_CHECKBOX, lambda e: self._update_intro())
            self._extra_boxes[extra.id] = check
            sizer.Add(check, 0, wx.ALL, 4)
        root.Add(sizer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

    def _build_dependencies(self, root):
        for line in self.plan.dependencies:
            if line.installs:
                check = wx.CheckBox(self, label=line.text)
                check.SetValue(True)
                check.Disable()
                root.Add(check, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
            else:
                text = wx.StaticText(self, label=line.text)
                text.Wrap(_WRAP)
                root.Add(text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

    # ---- state ---------------------------------------------------------------------------------------------------

    def _checked(self) -> dict[str, bool]:
        return {i: box.GetValue() for i, box in self._extra_boxes.items()}

    def _relayout(self):
        sizer = self.GetSizer()
        sizer.SetSizeHints(self)
        self.Fit()
        self.Layout()

    def _update_intro(self):
        chosen = set(chosen_optional(self.plan, self._checked()))
        self.intro.SetLabel(intro_text(self.plan, chosen, self._entry_titles))
        self.intro.Wrap(_WRAP)
        self._relayout()

    def _library_path(self) -> Path | None:
        """The folder the game would go into now, or None when none is chosen yet"""
        if self._radio is not None:
            lib = selected_library(self.plan, self._radio.GetSelection())
            return Path(lib["path"]) if lib else None
        if self.plan.libraries:
            return Path(self.plan.libraries[0]["path"])
        text = self._folder.GetValue().strip()
        return Path(text) if text else None

    def _update_blocked(self):
        path = self._library_path()
        reason = self.service.blocked(self.plan.catalog_id, path) if path is not None else None
        self.blocked_text.SetLabel(reason or "")
        if reason:
            self.blocked_text.Wrap(_WRAP)
        self.ok_btn.Enable(reason is None)
        self._relayout()

    # ---- events --------------------------------------------------------------------------------------------------

    def on_browse(self, event):
        dlg = wx.DirDialog(self, messages.CONFIRM_BROWSE_PROMPT, defaultPath=self._folder.GetValue())
        if dlg.ShowModal() == wx.ID_OK:
            self._folder.SetValue(dlg.GetPath())
        dlg.Destroy()
        self._folder.SetFocus()

    def on_ok(self, event):
        plan = self.plan
        if self._folder is not None:
            text = self._folder.GetValue().strip()
            if not text:
                self._folder.SetFocus()
                return
            path = Path(text)
            if self.service.blocked(plan.catalog_id, path):
                self._update_blocked()
                return
            try:
                library = self.service.ensure_library(path)
            except LibraryError as e:
                wx.MessageBox(str(e), messages.CONFIRM_LIBRARY_ERROR_TITLE, wx.OK | wx.ICON_WARNING, self)
                self._folder.SetFocus()
                return
        elif self._radio is not None:
            library = selected_library(plan, self._radio.GetSelection())
        else:
            library = plan.libraries[0]
        self._choice = build_choice(plan, library, self._checked())
        self.EndModal(wx.ID_OK)

    def get_choice(self) -> InstallChoice | None:
        return self._choice
