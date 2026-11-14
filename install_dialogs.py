"""Dialogs for installing catalog games. UI thread only; the wording and decisions are in install_choices."""

from pathlib import Path

import wx

from engine import messages
from install_choices import (InstallChoice, build_choice, chosen_optional, default_library_index, extra_label,
                             intro_text, library_choice_label, library_choice_labels, ok_label, selected_library,
                             window_title)
from library_actions import remove_library_checkbox_label, remove_library_text, uninstall_checkbox_label
from library_manager import LibraryError

_WRAP = 450
_text = wx.Control.EscapeMnemonics  # a title or folder with "&" in a label is shown, not taken as an access key


def _add_buttons(dialog, root, ok: str):
    """`ok` (the default button) and Cancel, bottom right, as `dialog.ok_btn` and `dialog.cancel_btn`; Escape is
    Cancel"""
    buttons = wx.BoxSizer(wx.HORIZONTAL)
    dialog.ok_btn = wx.Button(dialog, wx.ID_OK, ok)
    dialog.ok_btn.SetDefault()
    dialog.cancel_btn = wx.Button(dialog, wx.ID_CANCEL, messages.CONFIRM_BUTTON_CANCEL)
    buttons.Add(dialog.ok_btn, 0, wx.RIGHT, 8)
    buttons.Add(dialog.cancel_btn)
    root.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
    dialog.SetEscapeId(wx.ID_CANCEL)
    dialog.SetAffirmativeId(wx.ID_OK)


class InstallConfirmDialog(wx.Dialog):
    """Confirm an install (or update, or adding extras), driven by the plan.

    The intro StaticText is the first child, so it comes first in the reading order; its size and "Includes"
    text are read when the dialog opens. After ShowModal() == wx.ID_OK, `choice` says what was chosen.

    `fixed_library` (Update and Extras) is the game's own library: it is shown, not offered as a choice. Why the
    game can't be installed where chosen is shown in a read-only "Install problem" field right after the choice,
    so Tab reaches it while Install is unavailable.
    """

    def __init__(self, parent, plan, service, fixed_library: dict | None = None):
        super().__init__(parent, title=window_title(plan), style=wx.DEFAULT_DIALOG_STYLE)
        self.plan = plan
        self.service = service
        self.fixed_library = fixed_library
        self.choice: InstallChoice | None = None
        self._entry_titles = {i: e.get("title", "") for i, e in service.entries.items()}
        self._extra_boxes = {}
        self._radio = None
        self._folder = None

        root = wx.BoxSizer(wx.VERTICAL)
        self.intro = wx.StaticText(self, label="")
        root.Add(self.intro, 0, wx.ALL | wx.EXPAND, 10)

        self._build_where(root)
        self._build_blocked(root)
        self._build_extras(root)
        self._build_dependencies(root)

        for warning in plan.warnings:
            text = wx.StaticText(self, label=_text(warning))
            text.Wrap(_WRAP)
            root.Add(text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

        _add_buttons(self, root, ok_label(plan))
        self.SetSizer(root)

        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        self._update_intro()
        self._update_blocked()
        self.CentreOnParent()

    # ---- building ------------------------------------------------------------------------------------------------

    def _build_where(self, root):
        plan = self.plan
        if self.fixed_library is not None:
            text = wx.StaticText(self, label=_text(messages.CONFIRM_INSTALLS_INTO.format(
                library=library_choice_label(self.fixed_library))))
            root.Add(text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        elif len(plan.libraries) >= 2:
            self._radio = wx.RadioBox(self, label=messages.CONFIRM_WHERE,
                                      choices=[_text(c) for c in library_choice_labels(plan.libraries)],
                                      majorDimension=1, style=wx.RA_SPECIFY_COLS)
            self._radio.SetSelection(default_library_index(plan))
            self._radio.Bind(wx.EVT_RADIOBOX, lambda e: self._update_blocked())
            root.Add(self._radio, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        elif plan.libraries:
            text = wx.StaticText(self, label=_text(messages.CONFIRM_INSTALLS_INTO.format(
                library=library_choice_label(plan.libraries[0]))))
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

    def _build_blocked(self, root):
        """The install problem, right after the library choice in Tab order. Its hidden label is its previous
        sibling (how Windows names an edit field), and it shows only while there is a problem."""
        self.blocked_label = wx.StaticText(self, label=messages.INSTALL_PROBLEM_TITLE)
        self.blocked_label.Hide()
        self.blocked_text = wx.TextCtrl(self, value="", size=(_WRAP, 60),
                                        style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_NO_VSCROLL,
                                        name=messages.INSTALL_PROBLEM_TITLE)
        self.blocked_text.Hide()
        root.Add(self.blocked_label, 0, wx.LEFT | wx.RIGHT, 10)
        root.Add(self.blocked_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

    def _build_extras(self, root):
        if not self.plan.extras:
            return
        box = wx.StaticBox(self, label=messages.CONFIRM_EXTRAS_BOX)
        sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
        for extra in self.plan.extras:
            check = wx.CheckBox(box, label=_text(extra_label(extra)))
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
                check = wx.CheckBox(self, label=_text(line.text))
                check.SetValue(True)
                check.Disable()
                root.Add(check, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
            else:
                text = wx.StaticText(self, label=_text(line.text))
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
        self.intro.SetLabelText(intro_text(self.plan, chosen, self._entry_titles))
        self.intro.Wrap(_WRAP)
        self._relayout()

    def _library(self) -> dict | None:
        """The library chosen now: the fixed one, the one picked, or the only one; None for a typed folder"""
        if self.fixed_library is not None:
            return self.fixed_library
        if self._radio is not None:
            return selected_library(self.plan, self._radio.GetSelection())
        return self.plan.libraries[0] if self.plan.libraries else None

    def _library_path(self) -> Path | None:
        """The folder the game would go into now, or None when none is chosen yet"""
        lib = self._library()
        text = self._folder.GetValue().strip() if self._folder is not None else ""
        return Path(lib["path"]) if lib else Path(text) if text else None

    def _blocked_reason(self) -> str | None:
        if self.fixed_library is not None:  # the game's own folder: only a library being removed stops it
            removing = self.fixed_library.get("id") in self.service.pending_removal
            return messages.LIBRARY_BEING_REMOVED if removing else None
        path = self._library_path()
        return self.service.blocked(self.plan.catalog_id, path) if path is not None else None

    def _update_blocked(self):
        reason = self._blocked_reason()
        self.blocked_text.ChangeValue(reason or "")
        self.blocked_text.Show(bool(reason))
        if self._radio is not None:  # also the chosen radio button's tooltip
            self._radio.SetItemToolTip(self._radio.GetSelection(), reason or "")
        self.ok_btn.Enable(reason is None)
        self._relayout()

    # ---- events --------------------------------------------------------------------------------------------------

    def on_browse(self, event):
        with wx.DirDialog(self, messages.CONFIRM_BROWSE_PROMPT, defaultPath=self._folder.GetValue()) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self._folder.SetValue(dlg.GetPath())
        self._folder.SetFocus()

    def on_ok(self, event):
        if self._blocked_reason():  # checked again for every kind of choice: things may have changed
            self._update_blocked()
            return
        library = self._library()
        if self._folder is not None:
            path = self._library_path()
            if path is None:
                self._folder.SetFocus()
                return
            try:
                library = self.service.ensure_library(path)
            except LibraryError as e:
                wx.MessageBox(str(e), messages.CONFIRM_LIBRARY_ERROR_TITLE, wx.OK | wx.ICON_WARNING, self)
                self._folder.SetFocus()
                return
        self.choice = build_choice(self.plan, library, self._checked())
        self.EndModal(wx.ID_OK)


class _UninstallConfirm(wx.Dialog):
    """A text, the "Also delete files ... created" checkbox (unticked, so saves and settings stay) and two buttons.
    The text is the first child, so it is read when the dialog opens; focus starts on the checkbox."""

    def __init__(self, parent, title: str, text: str, checkbox_label: str, ok: str):
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE)
        root = wx.BoxSizer(wx.VERTICAL)
        self.text = wx.StaticText(self, label=_text(text))
        self.text.Wrap(_WRAP)
        root.Add(self.text, 0, wx.ALL | wx.EXPAND, 10)
        self.delete_box = wx.CheckBox(self, label=_text(checkbox_label))
        root.Add(self.delete_box, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        _add_buttons(self, root, ok)
        self.SetSizerAndFit(root)
        self.delete_box.SetFocus()
        self.CentreOnParent()

    def delete_player_data(self) -> bool:
        return self.delete_box.GetValue()


class UninstallDialog(_UninstallConfirm):
    """"Uninstall <title>": Uninstall or Cancel. Player files go (to the Recycle Bin) only when the box is ticked."""

    def __init__(self, parent, title: str, player_files: int):
        super().__init__(parent, messages.UNINSTALL_TITLE.format(title=title),
                         messages.UNINSTALL_INTRO.format(title=title),
                         uninstall_checkbox_label(title, player_files), messages.UNINSTALL_BUTTON)


class RemoveLibraryDialog(_UninstallConfirm):
    """"Remove <label>", for a library holding catalog installs: `games` are uninstalled first; `kept` (installs
    Game Chooser can't uninstall) stay in the list as games added by hand. `player_files` counts `games` only."""

    def __init__(self, parent, label: str, games: list[str], player_files: int, kept=()):
        super().__init__(parent, messages.REMOVE_LIBRARY_TITLE.format(label=label),
                         remove_library_text(label, games, kept), remove_library_checkbox_label(player_files),
                         messages.REMOVE_LIBRARY_BUTTON)
