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

"""The games view both tabs are built from: Search, the games list with the filter tree to its right, the action
button with the install progress, and the description.

UI thread only. The view shows the rows it is given (`catalog_view.CatalogRow`: `build_rows` on the Catalog tab,
`library_rows` on the Library tab). Its rules are in .claude/rules/ui-main-window.md.
"""

import random

import wx

from smart_list import VirtualSmartList

from catalog_view import (TypeAhead, action_label, action_verb, description_text, filter_rows, pick_index,
                          tree_categories, tree_values, type_ahead_index)
from description_field import DescriptionField
from engine import messages
from progress_area import ProgressArea

# The keys that end a text being typed in the list: Escape, Backspace, Enter and the keys that move through the
# list (with Num Lock off the number pad sends its own codes for those)
ENDS_TYPED_TEXT = frozenset(getattr(wx, f"WXK_{prefix}{name}") for prefix in ("", "NUMPAD_")
                            for name in ("UP", "DOWN", "HOME", "END", "PAGEUP", "PAGEDOWN")
                            ) | {wx.WXK_ESCAPE, wx.WXK_BACK, wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER}


class GamesPanel(wx.Panel):
    """Tab order: Search, games list, filter tree, action button, install progress, description.

    Each tab's settings:
    - `name`: the list's accessible name.
    - `columns`: the list's smart_list columns.
    - `categories`: (criteria key, tree category label) in tree order.
    - `state_prefix`: what this tab's SavedState keys start with.
    - `idle_label`: the action button's text while no game is selected.
    - `on_action()`: Enter, Space (unless it belongs to a text being typed in the list), double-click or the
      button.
    - `context_menu(row)`: the wx.Menu for the selected row (None for no row); None shows no menu. The view
      shows the menu and destroys it.
    - `sort_keys`: one sort key per column (the player sorts by a header click or the column's digit key; an
      arrow in the header shows the sort, which is saved).
    - `tree_keys`: the categories the tree shows now (`set_tree_keys` changes them); None for all of them.
    - `row_filter(row)`: False for a row the list leaves out while the tree still shows its values.
    - `on_key(event)`: the tab's own keys in the list; it returns True for a key it took.
    - `on_shown()`: called after the action label and the description were set."""

    def __init__(self, parent, frame, *, name, columns, sort_keys, categories, state_prefix, idle_label,
                 on_action, context_menu, tree_keys=None, row_filter=None, on_key=None, on_shown=None):
        super().__init__(parent)
        self.frame = frame
        self.categories = categories
        self.state_prefix = state_prefix
        self.idle_label = idle_label
        self.on_action = on_action
        self.context_menu = context_menu
        self.sort_keys = sort_keys
        self.tree_keys = list(tree_keys) if tree_keys is not None else [key for key, _ in categories]
        self.row_filter = row_filter
        self.on_key = on_key
        self.on_shown = on_shown
        self._all_rows = []
        self.displayed = []  # the rows in the list now (filtered and sorted)
        self._tree_nodes = None  # what the tree shows: (key, label, values) per category; None until first built
        self._populating = False
        self.restoring_tree = False
        self.type_ahead = TypeAhead()  # the text being typed into the list to jump to a title
        # Read here, not by the main window later: the list is filled before the rest of the saved state loads
        self.sort_column, self.sort_ascending = 0, True
        column = self._read("sort_column", int, -1)
        if 0 <= column < len(sort_keys):
            self.sort_column = column
            self.sort_ascending = self._read("sort_ascending", bool, True)

        sizer = wx.BoxSizer(wx.VERTICAL)

        search_sizer = wx.BoxSizer(wx.HORIZONTAL)
        search_label = wx.StaticText(self, label="Search:")
        self.search_combo = wx.ComboBox(self, style=wx.CB_DROPDOWN)
        self.search_combo.SetValue(self._read("last_search", str, ""))
        search_sizer.Add(search_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        search_sizer.Add(self.search_combo, 1, wx.EXPAND)
        sizer.Add(search_sizer, 0, wx.EXPAND | wx.ALL, 5)

        self.splitter = splitter = wx.SplitterWindow(self)
        self.list = VirtualSmartList(parent=splitter, get_virtual_item=self._get_virtual_item,
                                     style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.list.set_columns(columns)
        self.list.SetLabel(name)
        self.list_ctrl.SetName(name)
        widths = self._read("column_widths", list, item=int)
        if widths and not self.list.control.use_dataview:  # a DataViewCtrl (macOS) keeps its own widths
            for column, width in enumerate(widths[:len(columns)]):
                self.list_ctrl.SetColumnWidth(column, width)

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

        # The action button and, beside it while an install job runs or waits, the install progress (created
        # next, so it follows the button in tab order)
        self.action_sizer_slot = wx.BoxSizer(wx.HORIZONTAL)
        self.action_btn = wx.Button(self, label=idle_label)
        self.action_btn.Bind(wx.EVT_BUTTON, lambda event: self.on_action())
        self.action_sizer_slot.Add(self.action_btn, 0, wx.ALIGN_CENTER)
        self.progress_area = ProgressArea(self)
        self.progress_area.Hide()
        self.action_sizer_slot.Add(self.progress_area, 0, wx.ALIGN_CENTER | wx.LEFT, 10)
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
        self.list_ctrl.Bind(wx.EVT_LIST_COL_CLICK, lambda event: self.sort_by(event.GetColumn()))
        self.list_ctrl.Bind(wx.EVT_CHAR, self.on_char)
        self._show_sort()

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

    def _read(self, name, kind, default=None, item=None):
        return self.frame.library_manager.saved(self._key(name), kind, default, item)

    def save_config(self):
        """Write the config. A save that fails is logged and never stops what the player was doing: the change
        stays in memory and is written with the next save that works."""
        manager = self.frame.library_manager
        manager.save_or_keep(manager.save_config, "the config")

    def _get_virtual_item(self, index):
        return self.displayed[index] if 0 <= index < len(self.displayed) else None

    def selected_row(self):
        index = self.list.get_selected_index()
        return self.displayed[index] if 0 <= index < len(self.displayed) else None

    # ---- rows, tree and list -----------------------------------------------------------------------------------

    def set_rows(self, rows):
        """Every change to the games comes through here: show these rows, bring the tree up to date (its
        selections kept) and refill the list. The selected game stays selected; when it is no longer listed
        (deleted, uninstalled, gone from the catalog) the selection keeps its row number."""
        self._all_rows = rows
        self._sync_tree()
        self._fill(by_player=False)

    def refill(self):
        """What the rows show or sort by changed while the games stayed the same (the Library's Status column,
        worked out on a worker thread): fill the list again, as quietly as `set_rows` does."""
        self._fill(by_player=False)

    def set_tree_keys(self, keys):
        """Show only these categories in the tree. A selection under a category that goes no longer filters."""
        self.tree_keys = list(keys)
        self._sync_tree()
        self.on_tree_selection()

    def _sync_tree(self):
        """Rebuild the tree only when what it shows changed, keeping the player's selections, the open categories
        and the tree's focused item"""
        values = tree_categories(self._all_rows)
        nodes = [(key, label, tree_values(key, values[key])) for key, label in self.categories
                 if key in self.tree_keys and values.get(key)]
        if nodes == self._tree_nodes:
            return
        # Until the tree has values, the saved selections wait (the catalog offline with no saved copy)
        paths = self._selected_paths() if self._tree_nodes else self._read("tree_selections", list, [], str)
        tree = self.tree_ctrl
        opened = [path for path, item in self._tree_items().items() if path and tree.IsExpanded(item)]
        focused = self._tree_path(tree.GetFocusedItem())
        self._tree_nodes = nodes
        self._build_tree(nodes)
        self._restore_tree(paths, opened, focused)

    def _tree_path(self, item) -> str | None:
        """"Category/Value" for a value's item, "Category" for a category's, "" for the root; None for no item"""
        tree = self.tree_ctrl
        root = tree.GetRootItem()
        if not item or not item.IsOk():
            return None
        if item == root:
            return ""
        parent = tree.GetItemParent(item)
        text = tree.GetItemText(item)
        return text if parent == root else f"{tree.GetItemText(parent)}/{text}"

    def _tree_items(self) -> dict:
        """The root's, every category's and every value's item, by its path"""
        tree = self.tree_ctrl
        items = {}
        root = tree.GetRootItem()
        if not root or not root.IsOk():
            return items
        items[""] = root
        category, cookie = tree.GetFirstChild(root)
        while category.IsOk():
            items[self._tree_path(category)] = category
            child, child_cookie = tree.GetFirstChild(category)
            while child.IsOk():
                items[self._tree_path(child)] = child
                child, child_cookie = tree.GetNextChild(category, child_cookie)
            category, cookie = tree.GetNextChild(root, cookie)
        return items

    def _build_tree(self, nodes):
        tree = self.tree_ctrl
        self.restoring_tree = True
        try:
            tree.DeleteAllItems()
            root = tree.AddRoot("Filters")
            for key, label, values in nodes:
                node = tree.AppendItem(root, label)
                tree.SetItemData(node, key)
                for value in values:
                    child = tree.AppendItem(node, value)
                    tree.SetItemData(child, value)
            tree.Expand(root)  # the categories start closed
        finally:
            self.restoring_tree = False

    def _selected_values(self):
        """(category item, value item) for each selected value; the root and the categories select nothing"""
        tree = self.tree_ctrl
        root = tree.GetRootItem()
        for item in tree.GetSelections():
            parent = tree.GetItemParent(item)
            if parent and parent.IsOk() and parent != root:
                yield parent, item

    def _selected_paths(self) -> list[str]:
        return [self._tree_path(item) for _, item in self._selected_values()]

    def _restore_tree(self, selected, opened, focused):
        """In the tree just built: select the values that were selected, open the categories that were open and
        put the tree's focus back on its item: the root, a category, or a value (its category when the value is
        gone)"""
        tree = self.tree_ctrl
        items = self._tree_items()
        self.restoring_tree = True
        try:
            for path in opened:
                if path in items:
                    tree.Expand(items[path])
            for path in selected:
                if "/" in path and path in items:  # a category selects nothing
                    tree.SelectItem(items[path], True)
            item = None if focused is None else items.get(focused) or items.get(focused.split("/")[0])
            if item:
                tree.SetFocusedItem(item)
        finally:
            self.restoring_tree = False

    def _criteria(self) -> dict[str, set[str]]:
        tree = self.tree_ctrl
        criteria: dict[str, set[str]] = {}
        for category, item in self._selected_values():
            criteria.setdefault(tree.GetItemData(category), set()).add(tree.GetItemData(item))
        return criteria

    def on_tree_selection(self, event=None):
        """The player changed the tree's selection: filter first, then save it"""
        if self.restoring_tree:
            return
        self.apply_filters()
        self._saved()[self._key("tree_selections")] = self._selected_paths()
        self.save_config()

    def on_tree_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.restoring_tree = True  # UnselectAll fires a change event only when something was selected,
            try:                        # so its event is ignored and the filters are applied here either way
                self.tree_ctrl.UnselectAll()  # clears every filter
            finally:
                self.restoring_tree = False
            self.on_tree_selection()
            return
        event.Skip()

    def apply_filters(self):
        """The player changed a filter or the sort order (the search, the tree, what `row_filter` leaves out):
        refill the list and bring the selected row into view. The selected game stays selected; when the new
        filter leaves it out, the selection goes to the first row."""
        self._fill(by_player=True)

    def _fill(self, by_player: bool):
        """Filter and sort the rows into the list and select the game that was selected (`pick_index` finds it; at
        startup, the one remembered from the last run). The action label and the description are set here, never
        left to a selection event: wx fires none when the selected row number stays the same and another game
        moves into it.

        `by_player`: the player changed a filter or the sort order. Then a selected game that is no longer listed
        gives way to the first row, and the selected row is brought into view. Otherwise (the games changed, or a
        background result arrived) the selection keeps its row number, and the list scrolls to the selected row
        only when that row was in view before: a refresh never scrolls away from where the player is looking."""
        rows = filter_rows(self._all_rows, self.search_combo.GetValue(), self._criteria())
        if self.row_filter is not None:
            rows = [row for row in rows if self.row_filter(row)]
        rows.sort(key=lambda r: (r.title.lower(), r.id))  # title order, also among rows that tie below
        rows.sort(key=self.sort_keys[self.sort_column], reverse=not self.sort_ascending)
        old, old_index = self.selected_row(), self.list.get_selected_index()
        wanted = old.id if old is not None else self._read("last_selected", str)
        scroll = by_player or self._in_view(old_index)  # asked before the list changes
        self._populating = True  # the selection chosen here is not the player's, so it isn't saved
        try:
            self.displayed = rows
            self.list.update_count(len(rows))
            index = pick_index(rows, wanted, 0 if by_player else old_index, old.game if old is not None else None)
            if index >= 0:
                self._select(index, scroll)
            if rows and not self.list.control.use_dataview:  # a DataViewCtrl (macOS) redraws on the new count
                self.list.refresh()
        finally:
            self._populating = False
        if not by_player and old is not None and index >= 0 and rows[index].id != wanted:
            # The selected row has another id now: the game got one (a new launch path), or it is gone and
            # another game is in its row. Either way that id is what the next start selects.
            self._saved()[self._key("last_selected")] = rows[index].id
        self._show_selection()

    def _in_view(self, index) -> bool:
        """Whether the list shows this row now. With no row (nothing was selected: the first fill, or a list that
        was empty) the answer is yes, so the remembered row is brought into view."""
        if index < 0 or self.list.control.use_dataview:
            return True
        top = self.list_ctrl.GetTopItem()
        return top <= index < top + self.list_ctrl.GetCountPerPage()

    def _select(self, index, scroll=True):
        """Select a row and make it the list's focused item too, so the arrow keys start from it; `scroll` also
        brings it into view. (ListCtrl.Focus would do both, so it can't be used for a refresh that must not
        scroll.)"""
        self.list.control.Select(index, True)
        if not self.list.control.use_dataview:
            self.list_ctrl.SetItemState(index, wx.LIST_STATE_FOCUSED, wx.LIST_STATE_FOCUSED)
            if scroll:
                self.list_ctrl.EnsureVisible(index)

    def _show_selection(self):
        row = self.selected_row()
        if row is None and self.action_btn.HasFocus():
            self.focus_list()  # never leave focus on a control that is about to be disabled (not under a dialog)
        self.action_btn.SetLabelText(action_label(row) if row else self.idle_label)
        self.action_btn.Enable(row is not None)
        self.description.set_text(description_text(row.entry) if row else "")
        self.Layout()  # the whole view: the slot alone keeps the width the last label gave it
        if self.on_shown is not None:
            self.on_shown()

    # ---- sorting ------------------------------------------------------------------------------------------------

    def sort_by(self, column):
        """A header click or a digit key: sort by that column; the same column again, the other way round.
        The selected game stays selected, and a text being typed ends."""
        self.type_ahead.clear()
        if column == self.sort_column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column, self.sort_ascending = column, True
        self._show_sort()
        self.apply_filters()
        self._saved()[self._key("sort_column")] = self.sort_column
        self._saved()[self._key("sort_ascending")] = self.sort_ascending
        self.save_config()

    def on_char(self, event):
        """A character typed in the list is type-ahead (the virtual list does none by itself), or a digit that
        sorts by its column. A digit only extends a text that is being typed ("area 51"), so a title that
        starts with a digit can't be reached by typing; nor does a Space start one (`TypeAhead` has its limits).
        A key with Ctrl alone or Alt alone is never text and sorts nothing. With both it is AltGr and is text:
        wx hands over a letter outside the system code page that way. Not on the macOS DataView control: there
        every key but a sorting digit is left to the control."""
        code = event.GetUnicodeKey()
        char = chr(code) if code != wx.WXK_NONE else ""
        ctrl, alt = event.ControlDown(), event.AltDown()
        text = char != "" and char.isprintable() and ctrl == alt and not self.list.control.use_dataview
        column = event.GetKeyCode() - ord("1")
        if text and (self.type_ahead.typing() or char not in "0123456789"):
            self._type(char)
        elif 0 <= column < len(self.sort_keys) and not ctrl and not alt:  # Shift may be what gives the digit
            self.sort_by(column)
        elif not text:  # a character is never passed on: the control might search for it on its own
            event.Skip()

    def _type(self, char):
        """Add a character to the text being typed and jump to the title that text leads to. The jump is the
        player's own move: the selection event does the rest, as after an arrow key (label, description,
        remembered game). A text no title starts with moves nothing. Nor does a Space added here: it only
        extends the text, and the next character does the jump ("a h" finds "A Hero's Call")."""
        text = self.type_ahead.add(char)
        if char == " ":
            return
        index = type_ahead_index([row.title for row in self.displayed], text, self.list.get_selected_index())
        if index >= 0:
            self._select(index)

    def _show_sort(self):
        """An arrow in the sorted column's header (a DataViewCtrl, on macOS, has none)"""
        if self.list.control.use_dataview:
            return
        for column in range(self.list_ctrl.GetColumnCount()):
            info = self.list_ctrl.GetColumn(column)
            text = info.GetText().rstrip(" ▲▼")
            if column == self.sort_column:
                text += " ▲" if self.sort_ascending else " ▼"
            info.SetText(text)
            self.list_ctrl.SetColumn(column, info)

    # ---- the install progress, selection and focus ---------------------------------------------------------------

    def set_busy(self, busy: bool):
        """Show the install progress beside the action button while a job runs or waits. Focus never moves into
        the progress area; if it was in there when the area hides, it goes to the list."""
        if self.progress_area.IsShown() == busy:
            return
        lost_focus = not busy and self.progress_area.has_focus_inside()
        self.progress_area.Show(busy)
        self.action_sizer_slot.Layout()
        self.Layout()
        if lost_focus:
            self.focus_list()  # which refuses while a dialog is open

    def on_selected(self, event):
        if self.frame.dialog_active:  # spurious events while a modal dialog is open
            return
        row = self.selected_row()
        self._show_selection()
        if row is not None and not self._populating:
            self._saved()[self._key("last_selected")] = row.id
            self.save_config()

    def select_row(self, row):
        for index, shown in enumerate(self.displayed):
            if shown.id == row.id:
                self._select(index)
                self._show_selection()
                return

    def random_game(self, candidates, none_left, act, caption="Random Game"):
        """Ctrl+R and Ctrl+Shift+R: pick one of `candidates` (rows of the list as shown) at random and ask. On Yes
        its row is selected and `act(row)` runs. `none_left` is the (text, caption) to show when there is no
        candidate."""
        if not candidates:
            self.frame._tell(*none_left, wx.ICON_INFORMATION)
            return
        row = random.choice(candidates)
        # The row is held across the prompt and the action, so no refresh may replace it meanwhile
        with self.frame.modal_dialog():
            answer = wx.MessageBox(messages.CATALOG_RANDOM_PROMPT.format(title=row.title, verb=action_verb(row)),
                                   caption, wx.YES_NO | wx.ICON_QUESTION)
            if answer == wx.YES:
                self.select_row(row)
                act(row)

    def focus_list(self):
        """Put keyboard focus in the games list (screen reader pattern: select, focused item, SetFocus).
        Not while the app is starting up or a dialog is open."""
        if not self or self.frame.initializing or self.frame.dialog_active:
            return
        index = self.list.get_selected_index()
        if index < 0 and self.displayed:
            index = 0
        if index >= 0:
            self._select(index)
        self.list.control.SetFocus()

    def on_list_key(self, event):
        key = event.GetKeyCode()
        plain = not event.HasAnyModifiers()
        # Any Space but Alt+Space (the window menu) is a plain one: Ctrl+Space reaching the list control clears
        # the selection, and the button still names the game
        space = key == wx.WXK_SPACE and event.GetModifiers() != wx.MOD_ALT
        if key in ENDS_TYPED_TEXT:  # the key itself goes on to the list as before (Enter acts below)
            self.type_ahead.clear()
        # A held Space or Enter acts once on the list (a dialog its first press opens still gets the repeats)
        if event.IsAutoRepeat() and (space or key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)):
            return
        if space and self.type_ahead.takes_space():
            # Part of the text being typed ("temple of"), for 3 seconds after its last character: a player
            # waits for the screen reader to finish the row. Taken here, never skipped on to on_char: wx turns
            # a Space key-down that reaches the list control into an activation.
            self._type(" ")
        elif space or (key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and plain):
            self.on_action()
        elif key == wx.WXK_WINDOWS_MENU or (key == wx.WXK_F10 and event.ShiftDown()):
            self.on_context(event)
        elif self.on_key is None or not self.on_key(event):
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

    def restore_sash(self):
        """Put the splitter where it was saved, else in the middle. The main window calls this once it has its
        size: a position set before that does not hold, and one left to wx stays at the minimum pane size and
        does not follow a resize."""
        self.splitter.SetSashPosition(self._read("splitter_position", int) or self.splitter.GetSize()[0] // 2)

    def save_state(self):
        """On close, into SavedState (the caller writes the config): the splitter position, the column widths, the
        search text and the tree filters. The last two are not saved while there are no rows (the catalog offline
        with no saved copy), so the player's last filters survive until the games load."""
        state = self._saved()
        state[self._key("splitter_position")] = self.splitter.GetSashPosition()
        if not self.list.control.use_dataview:
            state[self._key("column_widths")] = [self.list_ctrl.GetColumnWidth(column)
                                                 for column in range(self.list_ctrl.GetColumnCount())]
        if not self._all_rows:
            return
        state[self._key("last_search")] = self.search_combo.GetValue()
        state[self._key("tree_selections")] = self._selected_paths()
