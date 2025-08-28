#!/usr/bin/env python3
"""
Main window for Game Chooser application
"""

import wx
import os
import subprocess
import platform
import webbrowser
from pathlib import Path

from models import Game
from library_manager import GameLibraryManager
from game_list import GameListCtrl
from dialogs import EditGameDialog, EditManualGameDialog, PreferencesDialog


class MainFrame(wx.Frame):
    """Main application window"""
    
    def __init__(self):
        super().__init__(None, title="Game Chooser (0)")
        
        self.library_manager = GameLibraryManager()
        self.filtered_games = []
        self.search_timer = None
        
        # Set up UI
        self.init_ui()
        
        # Load saved state
        self.load_saved_state()
        
        # Initial library check
        self.check_libraries()
        
        # Update title
        self.update_title()
        
        # Bind close event
        self.Bind(wx.EVT_CLOSE, self.on_close)
    
    def init_ui(self):
        """Initialize the user interface"""
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Menu bar
        self.create_menu_bar()
        
        # Search combo box
        search_sizer = wx.BoxSizer(wx.HORIZONTAL)
        search_label = wx.StaticText(panel, label="Search:")
        self.search_combo = wx.ComboBox(panel, style=wx.CB_DROPDOWN)
        self.search_combo.Bind(wx.EVT_TEXT, self.on_search_text)
        self.search_combo.Bind(wx.EVT_COMBOBOX, self.on_search_select)
        
        search_sizer.Add(search_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        search_sizer.Add(self.search_combo, 1, wx.EXPAND)
        
        main_sizer.Add(search_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # Splitter for list and tree
        splitter = wx.SplitterWindow(panel)
        
        # Game list
        self.game_list = GameListCtrl(splitter, self.library_manager)
        self.game_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_game_selected)
        self.game_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_game_activated)
        self.game_list.Bind(wx.EVT_CONTEXT_MENU, self.on_list_context)
        self.game_list.Bind(wx.EVT_KEY_DOWN, self.on_list_key)
        
        # Tree control
        tree_panel = wx.Panel(splitter)
        tree_sizer = wx.BoxSizer(wx.VERTICAL)
        
        self.tree_ctrl = wx.TreeCtrl(tree_panel, 
                                    style=wx.TR_DEFAULT_STYLE | wx.TR_MULTIPLE)
        self.tree_ctrl.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_tree_selection)
        self.tree_ctrl.Bind(wx.EVT_KEY_DOWN, self.on_tree_key)
        
        # Filter tree button
        self.filter_btn = wx.Button(tree_panel, label="Filter Tree")
        self.filter_btn.Bind(wx.EVT_BUTTON, self.on_filter_tree)
        
        tree_sizer.Add(self.tree_ctrl, 1, wx.EXPAND)
        tree_sizer.Add(self.filter_btn, 0, wx.EXPAND | wx.TOP, 5)
        tree_panel.SetSizer(tree_sizer)
        
        # Set up splitter
        splitter.SplitVertically(self.game_list, tree_panel)
        splitter.SetSashGravity(0.5)
        
        main_sizer.Add(splitter, 1, wx.EXPAND)
        
        # Launch button
        self.launch_btn = wx.Button(panel, label="Launch")
        self.launch_btn.Bind(wx.EVT_BUTTON, self.on_launch)
        main_sizer.Add(self.launch_btn, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        panel.SetSizer(main_sizer)
        
        # Store splitter reference
        self.splitter = splitter
        
        # Set up keyboard shortcuts
        self.setup_accelerators()
        
        # Build initial tree
        self.build_tree()
        
        # Populate initial game list
        self.refresh_game_list()
    
    def create_menu_bar(self):
        """Create the menu bar"""
        menu_bar = wx.MenuBar()
        
        # File menu
        file_menu = wx.Menu()
        exit_item = file_menu.Append(wx.ID_EXIT, "E&xit\tAlt+F4")
        self.Bind(wx.EVT_MENU, self.on_exit, exit_item)
        menu_bar.Append(file_menu, "&File")
        
        # Edit menu
        edit_menu = wx.Menu()
        pref_item = edit_menu.Append(wx.ID_PREFERENCES, "&Preferences\tCtrl+,")
        self.Bind(wx.EVT_MENU, self.on_preferences, pref_item)
        menu_bar.Append(edit_menu, "&Edit")
        
        self.SetMenuBar(menu_bar)
    
    def setup_accelerators(self):
        """Set up keyboard accelerators"""
        accel_entries = []
        
        # Ctrl+F for search
        search_id = wx.NewIdRef()
        accel_entries.append((wx.ACCEL_CTRL, ord('F'), search_id))
        self.Bind(wx.EVT_MENU, lambda e: self.search_combo.SetFocus(), id=search_id)
        
        # Ctrl+N for new web game
        new_game_id = wx.NewIdRef()
        accel_entries.append((wx.ACCEL_CTRL, ord('N'), new_game_id))
        self.Bind(wx.EVT_MENU, self.on_add_web_game, id=new_game_id)
        
        # F5 for refresh
        refresh_id = wx.NewIdRef()
        accel_entries.append((wx.ACCEL_NORMAL, wx.WXK_F5, refresh_id))
        self.Bind(wx.EVT_MENU, self.on_refresh, id=refresh_id)
        
        accel_table = wx.AcceleratorTable(accel_entries)
        self.SetAcceleratorTable(accel_table)
    
    def check_libraries(self):
        """Check for game libraries on startup"""
        if not self.library_manager.config["libraries"]:
            wx.MessageBox("No game libraries defined. Please select a game library folder.", 
                        "Setup Required", wx.OK | wx.ICON_INFORMATION)
            
            dlg = wx.DirDialog(self, "Choose Game Library Directory")
            if dlg.ShowModal() == wx.ID_OK:
                path = dlg.GetPath()
                name = os.path.basename(path)
                self.library_manager.config["libraries"].append({
                    "name": name,
                    "path": path
                })
                self.library_manager.save_config()
            else:
                # User cancelled, exit
                self.Close()
                return
            dlg.Destroy()
        
        # Validate and scan
        try:
            exc_count = self.library_manager.validate_and_scan_all_with_dialog(self)
            
            if len(self.library_manager.games) == 0:
                if wx.MessageBox("No games found in currently added libraries. Open preferences?",
                                "No Games Found", 
                                wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
                    self.on_preferences(None)
            elif exc_count > 0:
                if wx.MessageBox(f"Added {exc_count} executables to exceptions. Open preferences?",
                                "Exceptions Added", 
                                wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
                    self.on_preferences(None)
        
        except PermissionError as e:
            if wx.MessageBox(f"{e}\nRemove this library?", 
                            "Permission Denied", 
                            wx.YES_NO | wx.ICON_ERROR) == wx.YES:
                # Remove the problematic library
                # (Would need to identify which one caused the error)
                pass
    
    def build_tree(self, filters=None):
        """Build the tree control hierarchy"""
        self.tree_ctrl.DeleteAllItems()
        
        if filters is None:
            filters = ["platform", "genre", "developer", "year"]
        
        # Build tree structure from games
        tree_data = {}
        
        for game in self.library_manager.games:
            for platform in game.platforms:
                if "platform" not in filters:
                    continue
                    
                if platform not in tree_data:
                    tree_data[platform] = {}
                
                genre = game.genre or "Unknown"
                if "genre" in filters:
                    if genre not in tree_data[platform]:
                        tree_data[platform][genre] = {}
                    
                    developer = game.developer or "Unknown"
                    if "developer" in filters:
                        if developer not in tree_data[platform][genre]:
                            tree_data[platform][genre][developer] = set()
                        
                        if "year" in filters:
                            tree_data[platform][genre][developer].add(game.year)
        
        # Build tree control
        root = self.tree_ctrl.AddRoot("Games")
        
        for platform in sorted(tree_data.keys()):
            plat_node = self.tree_ctrl.AppendItem(root, platform)
            
            if "genre" in filters:
                for genre in sorted(tree_data[platform].keys()):
                    genre_node = self.tree_ctrl.AppendItem(plat_node, genre)
                    
                    if "developer" in filters:
                        for developer in sorted(tree_data[platform][genre].keys()):
                            dev_node = self.tree_ctrl.AppendItem(genre_node, developer)
                            
                            if "year" in filters:
                                for year in sorted(tree_data[platform][genre][developer]):
                                    self.tree_ctrl.AppendItem(dev_node, year)
        
        self.tree_ctrl.ExpandAll()
    
    def on_tree_selection(self, event):
        """Handle tree selection changes"""
        self.apply_filters()
    
    def on_tree_key(self, event):
        """Handle tree keyboard events"""
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            # Clear selection
            self.tree_ctrl.UnselectAll()
            self.apply_filters()
        elif event.GetKeyCode() == wx.WXK_DELETE:
            # Delete selected game if applicable
            # (Tree doesn't have games anymore, so this might not apply)
            pass
        else:
            event.Skip()
    
    def get_tree_selection_criteria(self):
        """Get filtering criteria from tree selection"""
        selections = self.tree_ctrl.GetSelections()
        if not selections:
            return None
        
        criteria = {
            "platforms": set(),
            "genres": set(),
            "developers": set(),
            "years": set()
        }
        
        for item in selections:
            path = []
            current = item
            while current and current != self.tree_ctrl.GetRootItem():
                path.insert(0, self.tree_ctrl.GetItemText(current))
                current = self.tree_ctrl.GetItemParent(current)
            
            if len(path) >= 1:
                criteria["platforms"].add(path[0])
            if len(path) >= 2:
                criteria["genres"].add(path[1])
            if len(path) >= 3:
                criteria["developers"].add(path[2])
            if len(path) >= 4:
                criteria["years"].add(path[3])
        
        return criteria
    
    def apply_filters(self):
        """Apply search and tree filters"""
        search_term = self.search_combo.GetValue().lower().strip()
        tree_criteria = self.get_tree_selection_criteria()
        
        filtered = []
        
        for game in self.library_manager.games:
            # Apply tree filter first
            if tree_criteria:
                platform_match = not tree_criteria["platforms"] or \
                                any(p in tree_criteria["platforms"] for p in game.platforms)
                genre_match = not tree_criteria["genres"] or \
                             game.genre in tree_criteria["genres"] or \
                             (game.genre == "" and "Unknown" in tree_criteria["genres"])
                dev_match = not tree_criteria["developers"] or \
                           game.developer in tree_criteria["developers"] or \
                           (game.developer == "" and "Unknown" in tree_criteria["developers"])
                year_match = not tree_criteria["years"] or \
                            game.year in tree_criteria["years"]
                
                if not (platform_match and genre_match and dev_match and year_match):
                    continue
            
            # Apply search filter
            if search_term:
                if not any(search_term in field.lower() for field in 
                          [game.title, game.genre, game.developer, game.year] + game.platforms):
                    continue
            
            filtered.append(game)
        
        self.filtered_games = filtered
        self.game_list.populate(filtered)
        
        # Select first item if any
        if filtered:
            self.game_list.Select(0)
    
    def refresh_game_list(self):
        """Refresh the game list display"""
        self.apply_filters()
        self.update_title()
    
    def on_search_text(self, event):
        """Handle search text changes with delay"""
        # Cancel previous timer
        if self.search_timer:
            self.search_timer.Stop()
        
        # Start new timer for 0.5 second delay
        self.search_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_search_timer)
        self.search_timer.Start(500, wx.TIMER_ONE_SHOT)
    
    def on_search_timer(self, event):
        """Execute search after delay"""
        search_term = self.search_combo.GetValue().lower().strip()
        
        # Find matching values for combo box
        matches = set()
        for game in self.library_manager.games:
            if search_term in game.title.lower():
                matches.add(game.title)
            if search_term in game.genre.lower():
                matches.add(game.genre)
            if search_term in game.developer.lower():
                matches.add(game.developer)
            if search_term in game.year.lower():
                matches.add(game.year)
            for platform in game.platforms:
                if search_term in platform.lower():
                    matches.add(platform)
        
        # Update combo box choices
        self.search_combo.SetItems(sorted(matches))
        
        # Apply filters
        self.apply_filters()
    
    def on_search_select(self, event):
        """Handle combo box selection"""
        self.apply_filters()
    
    def on_game_selected(self, event):
        """Handle game selection in list"""
        game = self.game_list.get_selected_game()
        if game:
            self.launch_btn.SetLabel(f"Launch {game.title}")
            
            # Save selected game
            self.library_manager.config["SavedState"]["last_selected"] = game.title
            self.library_manager.save_config()
    
    def on_game_activated(self, event):
        """Handle double-click on game"""
        self.on_launch(None)
    
    def on_list_context(self, event):
        """Show context menu for list"""
        menu = wx.Menu()
        
        game = self.game_list.get_selected_game()
        if game:
            launch_item = menu.Append(wx.ID_ANY, "Launch")
            self.Bind(wx.EVT_MENU, self.on_launch, launch_item)
            
            edit_item = menu.Append(wx.ID_ANY, "Edit")
            self.Bind(wx.EVT_MENU, self.on_edit_game, edit_item)
            
            delete_item = menu.Append(wx.ID_ANY, "Delete")
            self.Bind(wx.EVT_MENU, self.on_delete_game, delete_item)
            
            menu.AppendSeparator()
        
        add_web_item = menu.Append(wx.ID_ANY, "Add Web Game")
        self.Bind(wx.EVT_MENU, self.on_add_web_game, add_web_item)
        
        add_manual_item = menu.Append(wx.ID_ANY, "Add Manual Game")
        self.Bind(wx.EVT_MENU, self.on_add_manual_game, add_manual_item)
        
        self.PopupMenu(menu)
        menu.Destroy()
    
    def on_list_key(self, event):
        """Handle list keyboard events"""
        key = event.GetKeyCode()
        
        if key == ord('E'):
            self.on_edit_game(None)
        elif key == wx.WXK_DELETE:
            self.on_delete_game(None)
        elif key in [wx.WXK_RETURN, wx.WXK_SPACE]:
            self.on_launch(None)
        else:
            event.Skip()
    
    def on_launch(self, event):
        """Launch selected game"""
        game = self.game_list.get_selected_game()
        if not game:
            return
        
        if game.launch_path.startswith("http"):
            # Web game
            webbrowser.open(game.launch_path)
        else:
            # Regular game
            full_path = self.library_manager.get_full_path(game)
            if not full_path or not Path(full_path).exists():
                wx.MessageBox("Game executable not found. Please locate the game.",
                            "File Not Found", wx.OK | wx.ICON_ERROR)
                
                # File dialog to relocate
                dlg = wx.FileDialog(self, "Locate Game Executable")
                if dlg.ShowModal() == wx.ID_OK:
                    new_path = dlg.GetPath()
                    
                    # Validate path is in a library
                    valid = False
                    for lib in self.library_manager.config["libraries"]:
                        if new_path.startswith(lib["path"]):
                            # Update game path
                            rel_path = Path(new_path).relative_to(Path(lib["path"]).parent)
                            game.launch_path = str(rel_path).replace(os.sep, '\\')
                            self.library_manager.save_games()
                            valid = True
                            break
                    
                    if not valid:
                        wx.MessageBox("Selected file is not in a configured game library.",
                                    "Invalid Location", wx.OK | wx.ICON_ERROR)
                        return
                dlg.Destroy()
                return
            
            # Launch the game
            try:
                system = platform.system()
                if system == "Darwin" and full_path.endswith(".app"):
                    # macOS .app bundle
                    subprocess.Popen(["open", "-a", full_path])
                else:
                    # Regular executable
                    subprocess.Popen([full_path])
                
                # Minimize window
                self.Iconize(True)
            
            except Exception as e:
                wx.MessageBox(f"Failed to launch game: {e}",
                            "Launch Error", wx.OK | wx.ICON_ERROR)
    
    def on_edit_game(self, event):
        """Edit selected game"""
        game = self.game_list.get_selected_game()
        if not game:
            return
        
        if game.launch_path.startswith("http"):
            # Web game
            dlg = EditGameDialog(self, game, self.library_manager, is_web=True)
        elif game.library_name == "manual":
            # Manual game - use manual edit dialog
            dlg = EditManualGameDialog(self, game, self.library_manager)
            dlg.SetTitle("Edit Manual Game")
            # Pre-populate the path field with the full path
            dlg.path_ctrl.SetValue(game.launch_path)
        else:
            # Regular auto-discovered game
            dlg = EditGameDialog(self, game, self.library_manager, is_web=False)
        
        if dlg.ShowModal() == wx.ID_OK:
            self.library_manager.save_games()
            self.refresh_game_list()
        dlg.Destroy()
    
    def on_delete_game(self, event):
        """Delete selected game"""
        game = self.game_list.get_selected_game()
        if not game:
            return
        
        if wx.MessageBox(f"Delete '{game.title}' from library?",
                        "Confirm Delete",
                        wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
            self.library_manager.games.remove(game)
            self.library_manager.save_games()
            self.refresh_game_list()
    
    def on_add_web_game(self, event):
        """Add a new web game"""
        game = Game(title="", platforms=["Web"], launch_path="https://")
        dlg = EditGameDialog(self, game, self.library_manager, is_web=True)
        
        if dlg.ShowModal() == wx.ID_OK:
            self.library_manager.games.append(game)
            self.library_manager.save_games()
            self.refresh_game_list()
        dlg.Destroy()
    
    def on_add_manual_game(self, event):
        """Add a new manual game"""
        game = Game(title="", platforms=[], launch_path="")
        dlg = EditManualGameDialog(self, game, self.library_manager)
        
        if dlg.ShowModal() == wx.ID_OK:
            self.library_manager.games.append(game)
            self.library_manager.save_games()
            self.refresh_game_list()
        dlg.Destroy()
    
    def on_filter_tree(self, event):
        """Show filter tree dialog"""
        dlg = wx.Dialog(self, title="Filter Tree Levels", size=(300, 200))
        
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Checkboxes for each level
        levels = ["Platform", "Genre", "Developer", "Year"]
        checkboxes = []
        
        for level in levels:
            cb = wx.CheckBox(panel, label=level)
            cb.SetValue(True)  # All checked by default
            checkboxes.append(cb)
            sizer.Add(cb, 0, wx.ALL, 5)
        
        # Buttons
        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(panel, wx.ID_OK)
        cancel_btn = wx.Button(panel, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.TOP, 10)
        panel.SetSizer(sizer)
        
        if dlg.ShowModal() == wx.ID_OK:
            # Rebuild tree with selected levels
            filters = []
            if checkboxes[0].GetValue():
                filters.append("platform")
            if checkboxes[1].GetValue():
                filters.append("genre")
            if checkboxes[2].GetValue():
                filters.append("developer")
            if checkboxes[3].GetValue():
                filters.append("year")
            
            self.build_tree(filters)
        
        dlg.Destroy()
    
    def on_preferences(self, event):
        """Show preferences dialog"""
        dlg = PreferencesDialog(self, self.library_manager)
        if dlg.ShowModal() == wx.ID_OK:
            self.refresh_game_list()
            self.build_tree()
        dlg.Destroy()
    
    def on_refresh(self, event):
        """Refresh/rescan libraries"""
        try:
            exc_count = self.library_manager.validate_and_scan_all_with_dialog(self)
            self.refresh_game_list()
            self.build_tree()
            
            if exc_count > 0:
                if wx.MessageBox(f"Added {exc_count} executables to exceptions. Open preferences?",
                                "Exceptions Added",
                                wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
                    self.on_preferences(None)
        
        except PermissionError as e:
            wx.MessageBox(str(e), "Permission Denied", wx.OK | wx.ICON_ERROR)
    
    def on_exit(self, event):
        """Exit application"""
        self.Close()
    
    def update_title(self):
        """Update window title with game count"""
        count = len(self.library_manager.games)
        self.SetTitle(f"Game Chooser ({count})")
    
    def load_saved_state(self):
        """Load saved window state"""
        state = self.library_manager.config["SavedState"]
        
        # Window size and position
        if state["window_size"]:
            self.SetSize(state["window_size"])
        else:
            # Default to 50% of screen
            display = wx.Display()
            rect = display.GetClientArea()
            self.SetSize(rect.width // 2, rect.height // 2)
        
        if state["window_position"]:
            self.SetPosition(state["window_position"])
        else:
            self.Centre()
        
        # Splitter position
        if state["splitter_position"]:
            self.splitter.SetSashPosition(state["splitter_position"])
        else:
            # Default 50/50 split
            width = self.GetSize()[0]
            self.splitter.SetSashPosition(width // 2)
        
        # Search term
        if state["last_search"]:
            self.search_combo.SetValue(state["last_search"])
    
    def save_state(self):
        """Save current window state"""
        state = self.library_manager.config["SavedState"]
        
        state["window_size"] = list(self.GetSize())
        state["window_position"] = list(self.GetPosition())
        state["splitter_position"] = self.splitter.GetSashPosition()
        state["last_search"] = self.search_combo.GetValue()
        
        # Save column widths
        self.game_list.save_column_widths()
        
        self.library_manager.save_config()
    
    def on_close(self, event):
        """Handle window close"""
        self.save_state()
        self.library_manager.save_games()
        self.library_manager.save_config()
        self.Destroy()