#!/usr/bin/env python3
"""
Game list control for Game Chooser application
"""

import wx
import wx.lib.mixins.listctrl as listmix


class GameListCtrl(wx.ListCtrl, listmix.ListCtrlAutoWidthMixin):
    """Custom Virtual ListCtrl for game display - only renders visible items for better performance"""

    def __init__(self, parent, library_manager):
        super().__init__(parent, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_VIRTUAL)
        listmix.ListCtrlAutoWidthMixin.__init__(self)
        
        self.library_manager = library_manager
        self.games_displayed = []
        
        # Add columns
        self.AppendColumn("Title", width=200)
        self.AppendColumn("Genre", width=150)
        self.AppendColumn("Developer", width=150)
        self.AppendColumn("Year", width=80)
        self.AppendColumn("Platform", width=150)
        
        # Load saved column widths
        if self.library_manager.config["SavedState"]["column_widths"]:
            widths = self.library_manager.config["SavedState"]["column_widths"]
            for i, width in enumerate(widths):
                if i < self.GetColumnCount():
                    self.SetColumnWidth(i, width)
        
        # Set up sorting
        self.sort_column = self.library_manager.config["SavedState"]["sort_column"]
        self.sort_ascending = self.library_manager.config["SavedState"]["sort_ascending"]
        
        # Bind events
        self.Bind(wx.EVT_LIST_COL_CLICK, self.on_column_click)
        self.Bind(wx.EVT_CHAR, self.on_char)

    def OnGetItemText(self, row, col):
        """Virtual list method - return text for given row/column"""
        if row >= len(self.games_displayed):
            return ""

        game = self.games_displayed[row]
        if col == 0:
            return game.title
        elif col == 1:
            return game.genre
        elif col == 2:
            return game.developer
        elif col == 3:
            return game.year
        elif col == 4:
            return ", ".join(game.platforms)
        return ""
    
    def populate(self, games):
        """Populate virtual list with games"""
        self.games_displayed = games
        self.sort_list()
        # Set the virtual list size - this tells wxPython how many items we have
        self.SetItemCount(len(self.games_displayed))
    
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
            self.RefreshItems(0, len(self.games_displayed) - 1)

        # Update column headers with arrows
        for col in range(self.GetColumnCount()):
            info = self.GetColumn(col)
            text = info.GetText().rstrip(' ▲▼')
            if col == self.sort_column:
                text += ' ▲' if self.sort_ascending else ' ▼'
            info.SetText(text)
            self.SetColumn(col, info)
    
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