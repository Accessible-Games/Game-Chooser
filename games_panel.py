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

"""The games view a tab is built from: Search, the games list with the filter tree to its right, the action
button (the install progress takes its place while a job runs or waits) and the description.

UI thread only. The view shows the rows it is given (`catalog_view.CatalogRow`); what a row's action does and what
its context menu holds are the tab's.
"""

import wx

from smart_list import VirtualSmartList

from catalog_view import action_label, description_text, filter_rows, tree_categories, tree_values
from description_field import DescriptionField
from progress_area import ProgressArea


class GamesPanel(wx.Panel):
    """Tab order: Search, games list, filter tree, action button, description.

    `columns`: the list's smart_list columns. `categories`: (criteria key, tree category label) in tree order.
    `state_prefix`: what this tab's SavedState keys start with. `on_action()`: Enter, Space, double-click or the
    button. `context_menu(row)`: the wx.Menu for the selected row (None for no menu); the view shows and
    destroys it."""

    def __init__(self, parent, frame, *, name, columns, categories, state_prefix, on_action, context_menu):
        super().__init__(parent)
        self.frame = frame
        self.categories = categories
        self.state_prefix = state_prefix
        self.on_action = on_action
        self.context_menu = context_menu
        self._all_rows = []
        self.displayed = []  # the rows in the list now (filtered and sorted)
        self._categories = None  # the tree's categories; None until the tree is first built
        self._tree_has_values = False  # until it is, the saved tree selections wait (offline start, no cache)
        self._selected_id = self._saved()[self._key("last_selected")]
        self._populating = False
        self.restoring_tree = False

        sizer = wx.BoxSizer(wx.VERTICAL)

        search_sizer = wx.BoxSizer(wx.HORIZONTAL)
        search_label = wx.StaticText(self, label="Search:")
        self.search_combo = wx.ComboBox(self, style=wx.CB_DROPDOWN)
        self.search_combo.SetValue(self._saved()[self._key("last_search")] or "")
        search_sizer.Add(search_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        search_sizer.Add(self.search_combo, 1, wx.EXPAND)
        sizer.Add(search_sizer, 0, wx.EXPAND | wx.ALL, 5)

        splitter = wx.SplitterWindow(self)
        self.list = VirtualSmartList(parent=splitter, get_virtual_item=self._get_virtual_item,
                                     style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.list.set_columns(columns)
        self.list.SetLabel(name)
        self.list_ctrl.SetName(name)

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

    # ---- small helpers ----------------------------------------------------------------------------------------

    @property
    def list_ctrl(self):
        """The wx control inside the smart list"""
        return self.list.control.control

    def _saved(self) -> dict:
        return self.frame.library_manager.config["SavedState"]

    def _key(self, name: str) -> str:
        """This tab's SavedState key"""
        return self.state_prefix + name

    def _save_config(self):
        self.frame.library_manager.save_config()

    def _get_virtual_item(self, index):
        return self.displayed[index] if 0 <= index < len(self.displayed) else None

    def selected_row(self):
        index = self.list.get_selected_index()
        return self.displayed[index] if 0 <= index < len(self.displayed) else None

    # ---- rows, tree and list -----------------------------------------------------------------------------------

    def set_rows(self, rows):
        """Show these rows (the games changed, or what is installed) and re-filter, keeping the selected game
        selected"""
        self._all_rows = rows
        self._sync_tree()
        self.apply_filters()

    def _sync_tree(self):
        categories = tree_categories(self._all_rows)
        if categories == self._categories:
            return
        paths = self._selected_paths() if self._tree_has_values else self._saved()[self._key("tree_selections")]
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
            for key, label in self.categories:
                values = categories.get(key)
                if not values:
                    continue
                node = tree.AppendItem(root, label)
                tree.SetItemData(node, key)
                for value in tree_values(key, values):
                    child = tree.AppendItem(node, value)
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
        self._saved()[self._key("tree_selections")] = self._selected_paths()
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
            self.displayed = rows
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
            self._saved()[self._key("last_selected")] = row.id
            self._save_config()

    def select_row(self, row):
        for index, shown in enumerate(self.displayed):
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
        if index < 0 and self.displayed:
            index = 0
        if index >= 0:
            self.list.control.Select(index, True)
            if not self.list.control.use_dataview:
                self.list_ctrl.Focus(index)
        self.list.control.SetFocus()

    def on_list_key(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER, wx.WXK_SPACE) and not event.HasAnyModifiers():
            self.on_action()
        elif key == wx.WXK_WINDOWS_MENU or (key == wx.WXK_F10 and event.ShiftDown()):
            self.on_context(event)
        else:
            event.Skip()

    def on_context(self, event):
        """The selected row's context menu (right-click, Shift+F10, Applications key)"""
        menu = self.context_menu(self.selected_row())
        if menu is None:
            return
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
        """Search text and tree filters (on close). Nothing is saved while there are no rows (the catalog offline
        with no saved copy), so the player's last filters survive until the games load."""
        if not self._all_rows:
            return
        state = self._saved()
        state[self._key("last_search")] = self.search_combo.GetValue()
        state[self._key("tree_selections")] = self._selected_paths()
