#!/usr/bin/env python3
"""
Game list control for Game Chooser application
"""

import wx
import wx.lib.mixins.listctrl as listmix


class GameListCtrl(wx.ListCtrl, listmix.ListCtrlAutoWidthMixin):
    """Custom ListCtrl for game display"""
    
    def __init__(self, parent, library_manager):
        super().__init__(parent, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
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
    
    def populate(self, games):
        """Populate list with games"""
        self.DeleteAllItems()
        self.games_displayed = games
        
        for game in games:
            index = self.InsertItem(self.GetItemCount(), game.title)
            self.SetItem(index, 1, game.genre)
            self.SetItem(index, 2, game.developer)
            self.SetItem(index, 3, game.year)
            self.SetItem(index, 4, ", ".join(game.platforms))
        
        self.sort_list()
    
    def sort_list(self):
        """Sort the list by current column and direction"""
        if not self.games_displayed:
            return
        
        # Sort games
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
        
        # Repopulate
        self.DeleteAllItems()
        for game in self.games_displayed:
            index = self.InsertItem(self.GetItemCount(), game.title)
            self.SetItem(index, 1, game.genre)
            self.SetItem(index, 2, game.developer)
            self.SetItem(index, 3, game.year)
            self.SetItem(index, 4, ", ".join(game.platforms))
        
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