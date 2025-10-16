#!/usr/bin/env python3
"""
Game list control for Game Chooser application
"""

import sys
from pathlib import Path

# Add smart_list and resource_finder submodules to path
smart_list_path = Path(__file__).parent / "smart_list"
if smart_list_path.exists() and str(smart_list_path) not in sys.path:
    sys.path.insert(0, str(smart_list_path))

resource_finder_path = Path(__file__).parent / "resource_finder"
if resource_finder_path.exists() and str(resource_finder_path) not in sys.path:
    sys.path.insert(0, str(resource_finder_path))

import wx
from smart_list import VirtualSmartList, Column


class GameListCtrl:
    """Custom VirtualSmartList wrapper for game display with sorting and keyboard navigation"""

    def __init__(self, parent, library_manager):
        self.library_manager = library_manager
        self.games_displayed = []

        # Create VirtualSmartList
        self.list = VirtualSmartList(
            parent=parent,
            get_virtual_item=self.get_virtual_item,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL
        )

        # Set up sorting
        self.sort_column = self.library_manager.config["SavedState"]["sort_column"]
        self.sort_ascending = self.library_manager.config["SavedState"]["sort_ascending"]

        # Define and set columns
        columns = [
            Column(title="Title", model_field="title", width=200),
            Column(title="Genre", model_field="genre", width=150),
            Column(title="Developer", model_field="developer", width=150),
            Column(title="Year", model_field="year", width=80),
            Column(title="Platform", model_field=lambda g: ", ".join(g.platforms), width=150)
        ]
        self.list.set_columns(columns)

        # Load saved column widths (not supported on DataViewCtrl)
        if not self.is_dataview and self.library_manager.config["SavedState"]["column_widths"]:
            widths = self.library_manager.config["SavedState"]["column_widths"]
            for i, width in enumerate(widths):
                if i < len(columns):
                    # Access underlying control to set column width
                    self.list.control.control.SetColumnWidth(i, width)

        # Bind events - use underlying control for column clicks
        # Context menu, selection, activation need to be bound by MainFrame after init
        self.list.control.control.Bind(wx.EVT_LIST_COL_CLICK, self.on_column_click)
        self.list.control.Bind(wx.EVT_CHAR, self.on_char)

    def get_virtual_item(self, index):
        """Callback for VirtualSmartList to get item by index"""
        if index < len(self.games_displayed):
            return self.games_displayed[index]
        return None

    @property
    def is_dataview(self):
        """Check if using DataViewCtrl (macOS) vs ListView (Windows/Linux)"""
        return hasattr(self.list.control, 'use_dataview') and self.list.control.use_dataview

    @property
    def control(self):
        """Expose underlying wx control for splitter"""
        return self.list.control.control

    def populate(self, games):
        """Populate virtual list with games"""
        self.games_displayed = games
        self.sort_list()
        # Update the virtual list count
        self.list.update_count(len(self.games_displayed))
    
    def sort_list(self):
        """Sort the list by current column and direction"""
        if not self.games_displayed:
            return

        # Sort games in-place
        if self.sort_column == 0:  # Title
            self.games_displayed.sort(key=lambda g: g.title.lower(),
                                     reverse=not self.sort_ascending)
        elif self.sort_column == 1:  # Genre
            self.games_displayed.sort(key=lambda g: g.genre.lower(),
                                     reverse=not self.sort_ascending)
        elif self.sort_column == 2:  # Developer
            self.games_displayed.sort(key=lambda g: g.developer.lower(),
                                     reverse=not self.sort_ascending)
        elif self.sort_column == 3:  # Year
            self.games_displayed.sort(key=lambda g: (g.year == "unknown", g.year),
                                     reverse=not self.sort_ascending)
        elif self.sort_column == 4:  # Platform
            self.games_displayed.sort(key=lambda g: len(g.platforms),
                                     reverse=not self.sort_ascending)

        # Refresh the virtual list display
        if self.games_displayed:
            self.list.refresh()

        # Update column headers with arrows (not supported on DataViewCtrl)
        if not self.is_dataview:
            for col in range(self.GetColumnCount()):
                info = self.list.control.control.GetColumn(col)
                text = info.GetText().rstrip(' ▲▼')
                if col == self.sort_column:
                    text += ' ▲' if self.sort_ascending else ' ▼'
                info.SetText(text)
                self.list.control.control.SetColumn(col, info)
    
    def on_column_click(self, event):
        """Handle column header click for sorting"""
        col = event.GetColumn()
        if col == self.sort_column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = col
            self.sort_ascending = True

        self.sort_list()

        # Save state
        self.library_manager.config["SavedState"]["sort_column"] = self.sort_column
        self.library_manager.config["SavedState"]["sort_ascending"] = self.sort_ascending
        self.library_manager.save_config()

    def on_char(self, event):
        """Handle keyboard shortcuts for sorting"""
        key = event.GetKeyCode()
        if ord('1') <= key <= ord('5'):
            col = key - ord('1')
            if col < self.GetColumnCount():
                if col == self.sort_column:
                    self.sort_ascending = not self.sort_ascending
                else:
                    self.sort_column = col
                    self.sort_ascending = True
                self.sort_list()

                # Save state
                self.library_manager.config["SavedState"]["sort_column"] = self.sort_column
                self.library_manager.config["SavedState"]["sort_ascending"] = self.sort_ascending
                self.library_manager.save_config()
        else:
            event.Skip()

    def get_selected_game(self):
        """Get currently selected game"""
        selected = self.GetFirstSelected()
        if selected >= 0 and selected < len(self.games_displayed):
            return self.games_displayed[selected]
        return None

    def save_column_widths(self):
        """Save current column widths"""
        widths = []
        for i in range(self.GetColumnCount()):
            widths.append(self.GetColumnWidth(i))
        self.library_manager.config["SavedState"]["column_widths"] = widths
        self.library_manager.save_config()

    # Wrapper methods to maintain compatibility with MainFrame
    def GetColumnCount(self):
        """Get number of columns"""
        return self.list.control.control.GetColumnCount()

    def GetColumnWidth(self, col):
        """Get width of column"""
        if self.is_dataview:
            # DataViewCtrl doesn't support GetColumnWidth
            # Return saved width from config or default
            saved_widths = self.library_manager.config["SavedState"]["column_widths"]
            if saved_widths and col < len(saved_widths):
                return saved_widths[col]
            return -1  # Default width
        return self.list.control.control.GetColumnWidth(col)

    def SetColumnWidth(self, col, width):
        """Set width of column"""
        if not self.is_dataview:
            self.list.control.control.SetColumnWidth(col, width)

    def GetFirstSelected(self):
        """Get index of first selected item"""
        return self.list.get_selected_index()

    def Select(self, index):
        """Select item at index"""
        self.list.set_selected_index(index)

    def Focus(self, index):
        """Focus item at index"""
        # smart_list handles focus automatically with selection
        pass

    def SetFocus(self):
        """Set focus to list control"""
        self.list.control.SetFocus()

    def SetLabel(self, label):
        """Set accessibility label"""
        self.list.SetLabel(label)

    def Bind(self, event, handler):
        """Bind event to handler - bind directly to underlying control"""
        # Bind directly to the underlying wx control that's displayed in the splitter
        # This ensures events actually reach the handlers
        self.list.control.control.Bind(event, handler)