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

"""The Catalog tab: browse the curated catalog, filter it, and install, add or launch a game.

UI thread only. The panel never fetches anything itself: MainFrame fetches the index on a worker thread and
hands the result to `set_index_state`. Links open in the browser only when the player chooses them.
"""

import logging
import random
import webbrowser

import wx

import game_list  # noqa: F401  (puts the smart_list submodule on sys.path)
from smart_list import Column, VirtualSmartList

from catalog_view import (FLAG_LABELS, action_kind, action_label, action_verb, build_rows, description_text,
                          filter_rows, link_items, tree_categories)
from description_field import DescriptionField
from engine import messages
from engine.entries import current_platform, game_fields, platform_sources
from install_dialogs import InstallConfirmDialog
from install_service import InstallService
from progress_area import ProgressArea

log = logging.getLogger(__name__)

# (criteria key, tree category label), in tree order
TREE_CATEGORIES = [
    ("platform", "Platform"),
    ("genre", "Genre"),
    ("developer", "Developer"),
    ("year", "Release Year"),
    ("flags", "Flags"),
    ("installed", "Installed"),
]


class CatalogPanel(wx.Panel):
    """Tab order: status line, Retry, Search, games list, filter tree, action button, description."""

    def __init__(self, parent, frame, service: InstallService):
        super().__init__(parent)
        self.frame = frame
        self.service = service
        self.state = service.index_state
        self._checking = False
        self._newer_shown = False  # the "newer version needed" message shows once per session
        self._all_rows = []
        self._displayed = []
        self._categories = None  # the tree's categories; None until the tree is first built
        self._tree_has_values = False  # until it is, the saved tree selections wait (offline start, no cache)
        self._selected_id = self._saved()["catalog_last_selected"]
        self._populating = False
        self.restoring_tree = False

        sizer = wx.BoxSizer(wx.VERTICAL)

        # Status line (hidden unless there is a message). The hidden label before it is what a screen reader reads.
        self._status_label = wx.StaticText(self, label="Catalog status")
        self._status_label.Hide()
        self.status = wx.TextCtrl(self, style=wx.TE_READONLY)
        self.status.SetName("Catalog status")
        sizer.Add(self.status, 0, wx.EXPAND | wx.ALL, 5)
        self.retry_btn = wx.Button(self, label="Retry")
        self.retry_btn.Bind(wx.EVT_BUTTON, self.on_retry)
        sizer.Add(self.retry_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        search_sizer = wx.BoxSizer(wx.HORIZONTAL)
        search_label = wx.StaticText(self, label="Search:")
        self.search_combo = wx.ComboBox(self, style=wx.CB_DROPDOWN)
        self.search_combo.SetValue(self._saved()["catalog_last_search"] or "")
        search_sizer.Add(search_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        search_sizer.Add(self.search_combo, 1, wx.EXPAND)
        sizer.Add(search_sizer, 0, wx.EXPAND | wx.ALL, 5)

        splitter = wx.SplitterWindow(self)
        self.list = VirtualSmartList(parent=splitter, get_virtual_item=self._get_virtual_item,
                                     style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.list.set_columns([
            Column(title="Title", model_field=lambda r: r.title, width=200),
            Column(title="Developer", model_field=lambda r: r.developer, width=180),
            Column(title="Flags", model_field=lambda r: r.flags_text, width=220),
        ])
        self.list.SetLabel("Catalog games")
        self.list_ctrl.SetName("Catalog games")

        tree_panel = wx.Panel(splitter)
        tree_sizer = wx.BoxSizer(wx.VERTICAL)
        tree_label = wx.StaticText(tree_panel, label="Filter games by:")
        tree_label.Hide()
        tree_sizer.Add(tree_label, 0, wx.ALL, 0)
        self.tree_ctrl = wx.TreeCtrl(tree_panel, style=wx.TR_DEFAULT_STYLE | wx.TR_MULTIPLE)
        tree_sizer.Add(self.tree_ctrl, 1, wx.EXPAND)
        tree_panel.SetSizer(tree_sizer)

        splitter.SplitVertically(self.list_ctrl, tree_panel)
        splitter.SetSashGravity(0.5)
        splitter.SetMinimumPaneSize(100)
        sizer.Add(splitter, 1, wx.EXPAND)

        # The action button, or while an install job runs or waits, the install progress in its place
        # (created next, so it takes the button's place in tab order too)
        self.action_sizer_slot = wx.BoxSizer(wx.HORIZONTAL)
        self.action_btn = wx.Button(self, label=action_label(None))
        self.action_btn.Bind(wx.EVT_BUTTON, lambda event: self.on_action())
        self.action_sizer_slot.Add(self.action_btn, 0, wx.ALIGN_CENTER)
        self.progress_area = ProgressArea(self)
        self.progress_area.Hide()
        self.action_sizer_slot.Add(self.progress_area, 0, wx.ALIGN_CENTER)
        sizer.Add(self.action_sizer_slot, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        self.description = DescriptionField(self, "Description")
        self.description.SetMinSize((-1, 120))
        sizer.Add(self.description.label_ctrl, 0, wx.LEFT | wx.RIGHT, 5)
        sizer.Add(self.description, 0, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(sizer)

        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_selected)
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda event: self.on_action())
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self.on_context)
        self.list_ctrl.Bind(wx.EVT_KEY_DOWN, self.on_list_key)
        self.tree_ctrl.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_tree_selection)
        self.tree_ctrl.Bind(wx.EVT_KEY_DOWN, self.on_tree_key)
        self.search_combo.Bind(wx.EVT_TEXT, lambda event: self.apply_filters())
        self.search_combo.Bind(wx.EVT_COMBOBOX, lambda event: self.apply_filters())

        self._update_status()
        self.refresh_rows()

    # ---- small helpers ----------------------------------------------------------------------------------------

    @property
    def list_ctrl(self):
        """The wx control inside the smart list"""
        return self.list.control.control

    def _saved(self) -> dict:
        return self.frame.library_manager.config["SavedState"]

    def _save_config(self):
        self.frame.library_manager.save_config()

    def _get_virtual_item(self, index):
        return self._displayed[index] if 0 <= index < len(self._displayed) else None

    def selected_row(self):
        index = self.list.get_selected_index()
        return self._displayed[index] if 0 <= index < len(self._displayed) else None

    # ---- the index and the status line -------------------------------------------------------------------------

    def set_index_state(self, state):
        """Show a fetched (or saved) index: update the status line and the rows"""
        self.state = state
        self._checking = False
        self._update_status()
        self.refresh_rows()
        if (self.retry_btn.IsShown() and self.frame.active_tab() == "catalog" and not self.frame.initializing
                and not self.frame.dialog_active):
            self.status.SetFocus()  # no catalog at all: a screen reader reads why, and Retry is next

    def set_checking(self, checking: bool):
        """Show or clear "Checking for catalog updates" while MainFrame fetches the index"""
        self._checking = checking
        self._update_status()

    def check_for_updates(self):
        """F5 and Retry: fetch the index again (MainFrame runs the fetch on a worker thread)"""
        self.frame.start_index_fetch()

    def on_retry(self, event):
        self.check_for_updates()
        if self.status.IsShown():  # the Retry button is about to hide; a screen reader follows to the status
            self.status.SetFocus()

    def _status_message(self) -> tuple[str, bool]:
        """(status line text, whether Retry shows)"""
        if self._checking:
            return messages.CATALOG_CHECKING, False
        state = self.state
        if state.newer_app_needed and not self._newer_shown:
            self._newer_shown = True
            return messages.CATALOG_NEWER_APP_NEEDED, bool(state.error and not state.entries)
        if state.error and not state.entries:
            return messages.CATALOG_LOAD_FAILED, True
        return "", False

    def _update_status(self):
        text, retry = self._status_message()
        lost_focus = ((self.status.IsShown() and not text and self.status.HasFocus())
                      or (self.retry_btn.IsShown() and not retry and self.retry_btn.HasFocus()))
        if self.status.GetValue() != text:
            self.status.ChangeValue(text)
        self.status.Show(bool(text))
        self.retry_btn.Show(retry)
        self.Layout()
        if lost_focus and not self.frame.dialog_active:  # never leave focus on a control that just hid
            self.focus_list()

    # ---- rows, tree and list -----------------------------------------------------------------------------------

    def refresh_rows(self):
        """Rebuild the rows from the service (installed state may have changed) and re-filter, keeping the
        selected game selected"""
        self._all_rows = build_rows(self.service.visible_games(), self.service.installed_ids())
        self._sync_tree()
        self.apply_filters()

    def _sync_tree(self):
        categories = tree_categories(self._all_rows)
        if categories == self._categories:
            return
        paths = self._selected_paths() if self._tree_has_values else self._saved()["catalog_tree_selections"]
        self._categories = categories
        self._build_tree(categories)
        self._restore_tree(paths)
        self._tree_has_values = any(categories.values())

    def _build_tree(self, categories):
        tree = self.tree_ctrl
        self.restoring_tree = True
        try:
            tree.DeleteAllItems()
            root = tree.AddRoot("Filters")
            for key, label in TREE_CATEGORIES:
                values = categories.get(key)
                if not values:
                    continue
                node = tree.AppendItem(root, label)
                tree.SetItemData(node, key)
                for value in sorted(values, key=lambda v: FLAG_LABELS.get(v, v).lower() if key == "flags"
                                    else v.lower()):
                    child = tree.AppendItem(node, FLAG_LABELS.get(value, value) if key == "flags" else value)
                    tree.SetItemData(child, value)
            tree.Expand(root)  # the categories stay collapsed
        finally:
            self.restoring_tree = False

    def _selected_paths(self) -> list[str]:
        tree = self.tree_ctrl
        paths = []
        root = tree.GetRootItem()
        for item in tree.GetSelections():
            parent = tree.GetItemParent(item)
            if not parent or not parent.IsOk() or parent == root:
                continue
            paths.append(f"{tree.GetItemText(parent)}/{tree.GetItemText(item)}")
        return paths

    def _restore_tree(self, paths):
        tree = self.tree_ctrl
        root = tree.GetRootItem()
        if not paths or not root or not root.IsOk():
            return
        self.restoring_tree = True
        try:
            tree.UnselectAll()
            category, cookie = tree.GetFirstChild(root)
            while category.IsOk():
                label = tree.GetItemText(category)
                child, child_cookie = tree.GetFirstChild(category)
                while child.IsOk():
                    if f"{label}/{tree.GetItemText(child)}" in paths:
                        tree.SelectItem(child, True)
                    child, child_cookie = tree.GetNextChild(category, child_cookie)
                category, cookie = tree.GetNextChild(root, cookie)
        finally:
            self.restoring_tree = False

    def _criteria(self) -> dict[str, set[str]]:
        tree = self.tree_ctrl
        root = tree.GetRootItem()
        criteria: dict[str, set[str]] = {}
        for item in tree.GetSelections():
            parent = tree.GetItemParent(item)
            if not parent or not parent.IsOk() or parent == root:
                continue  # the root and category nodes select nothing
            criteria.setdefault(tree.GetItemData(parent), set()).add(tree.GetItemData(item))
        return criteria

    def on_tree_selection(self, event):
        if self.restoring_tree:
            return
        self._saved()["catalog_tree_selections"] = self._selected_paths()
        self._save_config()
        self.apply_filters()

    def on_tree_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.tree_ctrl.UnselectAll()  # clears every filter
            return
        event.Skip()

    def apply_filters(self):
        """Filter and sort the rows into the list, keeping the same game selected where it is still shown"""
        rows = filter_rows(self._all_rows, self.search_combo.GetValue(), self._criteria())
        rows.sort(key=lambda r: (r.title.lower(), r.id))
        wanted = self._selected_id
        if self.selected_row() is not None:
            wanted = self.selected_row().id
        self._populating = True  # the selection chosen here is not the player's, so it isn't saved
        try:
            self._displayed = rows
            self.list.update_count(len(rows))
            index = next((i for i, r in enumerate(rows) if r.id == wanted), 0 if rows else -1)
            if index >= 0:
                self.list.control.Select(index, True)
                if not self.list.control.use_dataview:
                    self.list_ctrl.EnsureVisible(index)
            if rows:
                self.list.refresh()
        finally:
            self._populating = False
        self._show_selection()

    def _show_selection(self):
        row = self.selected_row()
        self.action_btn.SetLabel(action_label(row))
        self.action_btn.Enable(row is not None)
        self.description.set_text(description_text(row.entry) if row else "")
        self.action_sizer_slot.Layout()

    def set_busy(self, busy: bool):
        """While an install job runs or waits, the progress area takes the action button's place.
        Focus never moves into the progress area; if it was on the control that hides, it goes to the list."""
        if self.progress_area.IsShown() == busy:
            return
        lost_focus = self.action_btn.HasFocus() if busy else self.progress_area.has_focus_inside()
        self.action_btn.Show(not busy)
        self.progress_area.Show(busy)
        self.action_sizer_slot.Layout()
        self.Layout()
        if lost_focus and not self.frame.dialog_active:
            self.focus_list()

    def on_selected(self, event):
        if self.frame.dialog_active:  # spurious events while a modal dialog is open
            return
        row = self.selected_row()
        self._show_selection()
        if row is not None and not self._populating:
            self._selected_id = row.id
            self._saved()["catalog_last_selected"] = row.id
            self._save_config()

    def _select_row(self, row):
        for index, shown in enumerate(self._displayed):
            if shown.id == row.id:
                self.list.control.Select(index, True)
                if not self.list.control.use_dataview:
                    self.list_ctrl.EnsureVisible(index)
                self._show_selection()
                return

    def focus_list(self):
        """Put keyboard focus in the games list (screen reader pattern: Select, Focus, SetFocus).
        Not while the app is starting up (a scan dialog may own focus) or a dialog is open."""
        if not self or self.frame.initializing or self.frame.dialog_active:
            return
        index = self.list.get_selected_index()
        if index < 0 and self._displayed:
            index = 0
        if index >= 0:
            self.list.control.Select(index, True)
            if not self.list.control.use_dataview:
                self.list_ctrl.Focus(index)
        self.list.control.SetFocus()

    # ---- actions -------------------------------------------------------------------------------------------------

    def on_action(self):
        """Enter, Space, double-click or the button: launch an installed game; otherwise install or add it"""
        row = self.selected_row()
        if row is None:
            return
        if row.installed:
            self._launch(row)
        else:
            self._confirm_install(row)

    def _launch(self, row):
        game = self.service.manager.catalog_game(row.id)
        if game is None:
            log.warning("%s is installed but has no Library entry", row.id)
            with self.frame.modal_dialog():
                wx.MessageBox(messages.CATALOG_NOT_IN_LIBRARY.format(title=row.title), "Catalog",
                              wx.OK | wx.ICON_INFORMATION)
            return
        self.frame.launch_game(game)

    def _confirm_install(self, row):
        """Install: confirm where and what first. A web game just gets added to the Library."""
        if action_kind(row) == "add":
            self._add_web_game(row)
            return
        job = self.service.queue.job_for(row.id)
        if job is not None:  # already queued or running: say so, and change nothing (not even last_library)
            text = messages.JOB_ALREADY_RUNNING if job.state == "running" else messages.JOB_ALREADY_WAITING
            self._tell(text.format(title=row.title), wx.ICON_INFORMATION)
            self._focus_row(row)
            return
        try:
            plan = self.service.plan(row.id)
        except Exception:
            log.exception("Couldn't plan installing %s", row.id)
            self._tell(messages.INSTALL_NOT_STARTED.format(title=row.title), wx.ICON_ERROR)
            self._focus_row(row)
            return
        choice = None
        with self.frame.modal_dialog():
            dlg = InstallConfirmDialog(self.frame, plan, self.service)
            try:
                if dlg.ShowModal() == wx.ID_OK:
                    choice = dlg.get_choice()
            finally:
                dlg.Destroy()
        if choice is not None:
            try:
                self.service.submit(plan.action, row.id, choice.library_id, choice.library_path, choice.extras)
            except Exception:
                log.exception("Couldn't queue installing %s", row.id)
                self._tell(messages.INSTALL_NOT_STARTED.format(title=row.title), wx.ICON_ERROR)
            self.frame.refresh_game_list()
            self.refresh_rows()
            self.frame.refresh_statuses()
        self._focus_row(row)

    def _tell(self, text, icon):
        with self.frame.modal_dialog():
            wx.MessageBox(text, "Catalog", wx.OK | icon, self.frame)

    def _add_web_game(self, row):
        sources = platform_sources(row.entry, current_platform())
        fields = game_fields(row.entry, current_platform())
        fields.update(platforms=["Web"], launch_path=sources[0].get("url", ""), library_id="", catalog_id=row.id)
        self.service.manager.add_catalog_game(fields)
        self.frame.refresh_game_list()
        self.refresh_rows()
        self.frame.refresh_statuses()
        self.frame.notify(messages.ADDED.format(title=fields["title"] or row.title))
        self._focus_row(row)

    def _focus_row(self, row):
        """Back to the row in the list, by the screen reader pattern (Select, Focus, SetFocus)"""
        if not self:
            return
        self._select_row(row)
        self.focus_list()

    def on_random_game(self):
        """Ctrl+R: pick a random game from the list as shown (filters applied) and offer to act on it"""
        if not self._displayed:
            with self.frame.modal_dialog():
                wx.MessageBox(messages.CATALOG_NO_GAMES, "No Games", wx.OK | wx.ICON_INFORMATION)
            return
        row = random.choice(self._displayed)
        # The row is held across the prompt, so no index refresh may replace it meanwhile
        with self.frame.modal_dialog():
            response = wx.MessageBox(
                messages.CATALOG_RANDOM_PROMPT.format(title=row.title, verb=action_verb(row)),
                "Random Game", wx.YES_NO | wx.ICON_QUESTION)
            if response == wx.YES:
                self._select_row(row)
                self.on_action()

    def on_list_key(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER, wx.WXK_SPACE) and not event.HasAnyModifiers():
            self.on_action()
        elif key == wx.WXK_WINDOWS_MENU or (key == wx.WXK_F10 and event.ShiftDown()):
            self.on_context(event)
        else:
            event.Skip()

    def on_context(self, event):
        """The action, then a link item for each link the game has (right-click, Shift+F10, Applications key)"""
        row = self.selected_row()
        if row is None:
            return
        menu = wx.Menu()
        action = menu.Append(wx.ID_ANY, action_label(row))
        menu.Bind(wx.EVT_MENU, lambda e: self.on_action(), action)
        for label, url in link_items(row.entry):
            item = menu.Append(wx.ID_ANY, label)
            menu.Bind(wx.EVT_MENU, lambda e, link=url: webbrowser.open(link), item)
        if event.GetEventType() == wx.wxEVT_LIST_ITEM_RIGHT_CLICK:
            self.list_ctrl.PopupMenu(menu)
        else:  # keyboard: next to the selected row
            index = self.list.get_selected_index()
            rect = self.list_ctrl.GetItemRect(index) if index >= 0 and not self.list.control.use_dataview else None
            if rect is not None:
                self.list_ctrl.PopupMenu(menu, wx.Point(rect.x + 10, rect.y + rect.height // 2))
            else:
                self.list_ctrl.PopupMenu(menu)
        menu.Destroy()

    # ---- saving ----------------------------------------------------------------------------------------------------

    def save_state(self):
        """Search text and tree filters (on close). Nothing is saved while the catalog is empty (offline with no
        saved copy), so the player's last filters survive until the games load."""
        if not self._all_rows:
            return
        state = self._saved()
        state["catalog_last_search"] = self.search_combo.GetValue()
        state["catalog_tree_selections"] = self._selected_paths()
