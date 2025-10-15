#!/usr/bin/env python3
"""
Main window for Game Chooser application
"""

import wx
import os
import subprocess
import platform
import webbrowser
import threading
from pathlib import Path

from models import Game
from library_manager import GameLibraryManager
from game_list import GameListCtrl
from dialogs import GameDialog, PreferencesDialog, DeleteGameDialog, FirstTimeSetupDialog


class FilterWorker(threading.Thread):
    """Background thread for filtering games to prevent UI freezing"""

    def __init__(self, games, search_term, tree_criteria, callback):
        super().__init__(daemon=True)
        self.games = games
        self.search_term = search_term.lower().strip() if search_term else ""
        self.tree_criteria = tree_criteria
        self.callback = callback
        self._stop_event = threading.Event()

    def stop(self):
        """Signal the thread to stop"""
        self._stop_event.set()

    def run(self):
        """Filter games in background"""
        filtered = []

        for game in self.games:
            # Check if we should stop
            if self._stop_event.is_set():
                return

            # Apply tree filter first
            if self.tree_criteria:
                platform_match = not self.tree_criteria["platforms"] or \
                                any(p in self.tree_criteria["platforms"] for p in game.platforms)
                genre_match = not self.tree_criteria["genres"] or \
                             game.genre in self.tree_criteria["genres"] or \
                             (game.genre == "" and "Unknown Genre" in self.tree_criteria["genres"])
                dev_match = not self.tree_criteria["developers"] or \
                           game.developer in self.tree_criteria["developers"] or \
                           (game.developer == "" and "Unknown Developer" in self.tree_criteria["developers"])
                year_match = not self.tree_criteria["years"] or \
                            game.year in self.tree_criteria["years"] or \
                            (game.year == "" and "Unknown Year" in self.tree_criteria["years"])

                if not (platform_match and genre_match and dev_match and year_match):
                    continue

            # Apply search filter
            if self.search_term:
                # Exclude "unknown" from all searches
                if "unknown" in self.search_term:
                    continue
                if not any(self.search_term in field.lower() for field in
                          [game.title, game.developer]):
                    continue

            filtered.append(game)

        # Call the callback with results if not stopped
        if not self._stop_event.is_set():
            wx.CallAfter(self.callback, filtered)


class MainFrame(wx.Frame):
    """Main application window"""
    
    def __init__(self):
        super().__init__(None, title="Game Chooser (0)")
        
        self.library_manager = GameLibraryManager()
        self.filtered_games = []
        self.filter_worker = None  # Background filtering thread
        self._tree_cache = None  # Cache for tree categories
        self._games_hash = None  # Hash to detect when games list changes
        self.dialog_active = False  # Flag to block spurious events when modal dialogs are open
        self.restoring_tree = False  # Flag to block saves during tree restoration
        self.initializing = True  # Flag to prevent focus stealing during startup

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
        self.game_list.SetLabel("Games")
        self.game_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_game_selected)
        self.game_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_game_activated)
        self.game_list.Bind(wx.EVT_CONTEXT_MENU, self.on_list_context)
        self.game_list.Bind(wx.EVT_KEY_DOWN, self.on_list_key)
        
        # Tree control
        tree_panel = wx.Panel(splitter)
        tree_sizer = wx.BoxSizer(wx.VERTICAL)

        # Add hidden label for accessibility
        tree_label = wx.StaticText(tree_panel, label="Filter games by:")
        tree_label.Hide()
        tree_sizer.Add(tree_label, 0, wx.ALL, 0)

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
        
        # Build initial tree (without restoring selections yet)
        self.build_tree(restore_selections=False)
        
        # Populate initial game list
        self.refresh_game_list()
    
    def create_menu_bar(self):
        """Create the menu bar"""
        menu_bar = wx.MenuBar()

        # File menu
        file_menu = wx.Menu()
        add_game_item = file_menu.Append(wx.ID_ANY, "&Add Game\tCtrl+N")
        self.Bind(wx.EVT_MENU, self.on_add_game, add_game_item)

        refresh_item = file_menu.Append(wx.ID_ANY, "&Refresh Library\tF5")
        self.Bind(wx.EVT_MENU, self.on_refresh, refresh_item)

        file_menu.AppendSeparator()

        # Use platform-appropriate exit shortcut (wxPython handles Ctrl->Cmd mapping on macOS)
        system = platform.system()
        exit_shortcut = "Ctrl+Q" if system == "Darwin" else "Alt+F4"
        exit_item = file_menu.Append(wx.ID_EXIT, f"E&xit\t{exit_shortcut}")
        self.Bind(wx.EVT_MENU, self.on_exit, exit_item)
        menu_bar.Append(file_menu, "&File")

        # Edit menu
        edit_menu = wx.Menu()
        edit_game_item = edit_menu.Append(wx.ID_ANY, "&Edit Game\tCtrl+E")
        self.Bind(wx.EVT_MENU, self.on_edit_game, edit_game_item)

        delete_game_item = edit_menu.Append(wx.ID_ANY, "&Delete Game\tDel")
        self.Bind(wx.EVT_MENU, self.on_delete_game, delete_game_item)

        edit_menu.AppendSeparator()

        # Use Ctrl for both platforms (wxPython maps to Cmd on macOS automatically)
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
        
        # Ctrl+N for new game
        new_game_id = wx.NewIdRef()
        accel_entries.append((wx.ACCEL_CTRL, ord('N'), new_game_id))
        self.Bind(wx.EVT_MENU, self.on_add_game, id=new_game_id)
        
        # F5 for refresh
        refresh_id = wx.NewIdRef()
        accel_entries.append((wx.ACCEL_NORMAL, wx.WXK_F5, refresh_id))
        self.Bind(wx.EVT_MENU, self.on_refresh, id=refresh_id)
        
        accel_table = wx.AcceleratorTable(accel_entries)
        self.SetAcceleratorTable(accel_table)
    
    def check_libraries(self):
        """Check if first run and show setup dialog"""
        if self.library_manager.is_first_run:
            dlg = FirstTimeSetupDialog(self, self.library_manager)
            dlg.ShowModal()
            dlg.Destroy()
            # Refresh UI in case they added stuff
            self.refresh_game_list()
            self.build_tree(force_rebuild=True)

        # Always continue to main window (no forced scanning)
        self.initializing = False
        self.game_list.SetFocus()
    
    def build_tree(self, filters=None, force_rebuild=False, restore_selections=True):
        """Build the tree control hierarchy with flat 2-level structure using cache"""
        if filters is None:
            filters = ["platform", "genre", "developer", "year"]

        # Generate a simple hash of games to detect changes
        current_hash = len(self.library_manager.games)
        if current_hash > 0:
            # Include first and last game titles for better change detection
            current_hash = hash((current_hash,
                               self.library_manager.games[0].title if self.library_manager.games else "",
                               self.library_manager.games[-1].title if self.library_manager.games else ""))

        # Check if we can use cached data
        if not force_rebuild and self._tree_cache is not None and self._games_hash == current_hash:
            categories = self._tree_cache
        else:
            # Collect unique values for each category
            categories = {
                "platform": set(),
                "genre": set(),
                "developer": set(),
                "year": set()
            }

            for game in self.library_manager.games:
                # Collect platforms
                if "platform" in filters:
                    for platform in game.platforms:
                        categories["platform"].add(platform)

                # Collect genres
                if "genre" in filters:
                    genre = game.genre or "Unknown Genre"
                    categories["genre"].add(genre)

                # Collect developers
                if "developer" in filters:
                    developer = game.developer or "Unknown Developer"
                    categories["developer"].add(developer)

                # Collect years
                if "year" in filters:
                    year = game.year or "Unknown Year"
                    categories["year"].add(year)

            # Cache the results
            self._tree_cache = categories
            self._games_hash = current_hash

        # Clear and rebuild tree control
        self.tree_ctrl.DeleteAllItems()
        root = self.tree_ctrl.AddRoot("Filters")

        # Category labels and their children
        category_labels = {
            "platform": "Platform",
            "genre": "Genre",
            "developer": "Developer",
            "year": "Release Year"
        }

        for category_key in ["platform", "genre", "developer", "year"]:
            if category_key in filters and categories[category_key]:
                # Add category node
                category_node = self.tree_ctrl.AppendItem(root, category_labels[category_key])

                # Add all values under this category
                for value in sorted(categories[category_key]):
                    self.tree_ctrl.AppendItem(category_node, value)

        self.tree_ctrl.ExpandAll()

        # Restore saved selections (only if requested)
        if restore_selections:
            self.restore_tree_selections()
    
    def on_tree_selection(self, event):
        """Handle tree selection changes"""
        # Save tree selections (skip during restoration to avoid multiple saves)
        if not self.restoring_tree:
            self.save_tree_selections()
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

        # Category label mapping
        category_mapping = {
            "Platform": "platforms",
            "Genre": "genres",
            "Developer": "developers",
            "Release Year": "years"
        }

        for item in selections:
            item_text = self.tree_ctrl.GetItemText(item)
            parent = self.tree_ctrl.GetItemParent(item)

            # Skip root node
            if parent == self.tree_ctrl.GetRootItem() or not parent:
                # This is a category node - ignore for now (could add "select all" logic later)
                continue

            # Get parent category
            parent_text = self.tree_ctrl.GetItemText(parent)
            if parent_text in category_mapping:
                criteria_key = category_mapping[parent_text]
                criteria[criteria_key].add(item_text)

        return criteria

    def save_tree_selections(self):
        """Save current tree selections to config"""
        selections = self.tree_ctrl.GetSelections()
        paths = []

        for item in selections:
            item_text = self.tree_ctrl.GetItemText(item)
            parent = self.tree_ctrl.GetItemParent(item)

            # Skip root node
            if parent == self.tree_ctrl.GetRootItem() or not parent:
                continue

            # Build path: "Parent/Child"
            parent_text = self.tree_ctrl.GetItemText(parent)
            path = f"{parent_text}/{item_text}"
            paths.append(path)

        self.library_manager.config["SavedState"]["tree_selections"] = paths
        self.library_manager.save_config()

    def restore_tree_selections(self):
        """Restore saved tree selections"""
        saved_paths = self.library_manager.config["SavedState"]["tree_selections"]
        if not saved_paths:
            return

        # Set flag to prevent saving during restoration
        self.restoring_tree = True

        try:
            # Clear current selections
            self.tree_ctrl.UnselectAll()

            # Walk tree to find and select items
            root = self.tree_ctrl.GetRootItem()
            if not root:
                return

            # Iterate through category nodes
            category_item, cookie = self.tree_ctrl.GetFirstChild(root)
            while category_item:
                category_text = self.tree_ctrl.GetItemText(category_item)

                # Iterate through child items
                child_item, child_cookie = self.tree_ctrl.GetFirstChild(category_item)
                while child_item:
                    child_text = self.tree_ctrl.GetItemText(child_item)
                    path = f"{category_text}/{child_text}"

                    if path in saved_paths:
                        self.tree_ctrl.SelectItem(child_item, True)

                    child_item, child_cookie = self.tree_ctrl.GetNextChild(category_item, child_cookie)

                category_item, cookie = self.tree_ctrl.GetNextChild(root, cookie)
        finally:
            # Always clear the flag
            self.restoring_tree = False

    def apply_filters(self):
        """Apply search and tree filters using background thread"""
        # Stop any existing filter operation
        if self.filter_worker and self.filter_worker.is_alive():
            self.filter_worker.stop()
            self.filter_worker.join(timeout=0.1)  # Brief wait for cleanup

        search_term = self.search_combo.GetValue()
        tree_criteria = self.get_tree_selection_criteria()

        # Start new filter operation in background
        self.filter_worker = FilterWorker(
            self.library_manager.games,
            search_term,
            tree_criteria,
            self.on_filter_complete
        )
        self.filter_worker.start()

    def on_filter_complete(self, filtered_games):
        """Called when background filtering is complete"""
        self.filtered_games = filtered_games
        self.game_list.populate(filtered_games)

        # Select and focus item for screen reader accessibility
        if filtered_games:
            # Try to restore previously selected game
            last_selected = self.library_manager.config["SavedState"]["last_selected"]
            selected_index = 0  # Default to first item

            if last_selected:
                # Search for the saved game in filtered results
                for i, game in enumerate(filtered_games):
                    if game.title == last_selected:
                        selected_index = i
                        break

            # Select and focus the item (for screen reader compatibility)
            # But don't steal keyboard focus - let user stay where they are
            self.game_list.Select(selected_index)
            self.game_list.Focus(selected_index)
            # Removed SetFocus() - was stealing focus from tree control on every filter change
    
    def refresh_game_list(self):
        """Refresh the game list display"""
        self.apply_filters()
        self.update_title()
    
    def on_search_text(self, event):
        """Handle search text changes immediately"""
        # Apply filters immediately - virtual list makes this fast
        self.apply_filters()
    
    def on_search_select(self, event):
        """Handle combo box selection"""
        self.apply_filters()
    
    def on_game_selected(self, event):
        """Handle game selection in list"""
        # Ignore spurious selection events when modal dialogs are active
        if self.dialog_active:
            return

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
        
        add_game_item = menu.Append(wx.ID_ANY, "Add Game")
        self.Bind(wx.EVT_MENU, self.on_add_game, add_game_item)
        
        self.PopupMenu(menu)
        menu.Destroy()
    
    def on_list_key(self, event):
        """Handle list keyboard events"""
        key = event.GetKeyCode()

        if key == ord('E') and event.ControlDown():
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

        # Check platform compatibility for non-web games
        if not game.launch_path.startswith("http"):
            # Get current platform
            current_system = platform.system()

            # Map system name to our platform names
            platform_map = {
                "Windows": "Windows",
                "Darwin": "macOS",
                "Linux": "Linux"
            }
            current_platform = platform_map.get(current_system, current_system)

            # Check if current platform is supported by the game
            if current_platform not in game.platforms:
                # Build supported platforms string
                supported = ", ".join(game.platforms) if game.platforms else "no platforms"

                # Show error dialog
                wx.MessageBox(
                    f"You're trying to run {game.title} on {current_platform}, but it only supports {supported}.",
                    "Uh-oh",
                    wx.OK | wx.ICON_ERROR
                )
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
                            game.launch_path = str(rel_path).replace(os.sep, '/')
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
                game_dir = str(Path(full_path).parent)
                system = platform.system()
                if system == "Darwin" and full_path.endswith(".app"):
                    # macOS .app bundle
                    subprocess.Popen(["open", "-a", full_path], cwd=game_dir)
                else:
                    # Regular executable
                    subprocess.Popen([full_path], cwd=game_dir)
                
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

        dlg = GameDialog(self, self.library_manager, game)

        # Block spurious selection events while modal dialog is active
        self.dialog_active = True
        try:
            if dlg.ShowModal() == wx.ID_OK:
                self.library_manager.save_games()
                self.refresh_game_list()
        finally:
            self.dialog_active = False
            dlg.Destroy()
    
    def on_delete_game(self, event):
        """Delete selected game"""
        game = self.game_list.get_selected_game()
        if not game:
            return

        # Remember the current index position to maintain list position after deletion
        current_index = self.game_list.GetFirstSelected()

        # Show custom delete dialog
        dlg = DeleteGameDialog(self, game.title)

        # Block spurious selection events while modal dialog is active
        self.dialog_active = True
        try:
            result = dlg.ShowModal()
        finally:
            self.dialog_active = False
            dlg.Destroy()

        if result == wx.ID_YES:
            # Delete without adding to exceptions
            self.library_manager.games.remove(game)
            self.library_manager.save_games()
            self.refresh_game_list()

            # Select previous item to stay in same area of list
            new_index = max(0, current_index - 1) if current_index > 0 else 0
            if new_index < len(self.filtered_games):
                self.game_list.Select(new_index)
                self.game_list.Focus(new_index)

        elif result == DeleteGameDialog.ID_DELETE_AND_EXCEPTION:
            # Delete and add to exceptions
            self.library_manager.add_to_exceptions(game)
            self.library_manager.games.remove(game)
            self.library_manager.save_games()
            self.refresh_game_list()

            # Select previous item to stay in same area of list
            new_index = max(0, current_index - 1) if current_index > 0 else 0
            if new_index < len(self.filtered_games):
                self.game_list.Select(new_index)
                self.game_list.Focus(new_index)
    
    def on_add_game(self, event):
        """Add a new game"""
        dlg = GameDialog(self, self.library_manager)

        # Block spurious selection events while modal dialog is active
        self.dialog_active = True
        try:
            if dlg.ShowModal() == wx.ID_OK:
                self.library_manager.games.append(dlg.game)
                self.library_manager.save_games()
                self.refresh_game_list()
        finally:
            self.dialog_active = False
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

        # Block spurious selection events while modal dialog is active
        self.dialog_active = True
        try:
            if dlg.ShowModal() == wx.ID_OK:
                # Defer UI refresh to avoid blocking the dialog close
                wx.CallAfter(self.refresh_ui_after_preferences)
        finally:
            self.dialog_active = False
            dlg.Destroy()

    def refresh_ui_after_preferences(self):
        """Refresh UI after preferences dialog closes"""
        self.refresh_game_list()
        self.build_tree(force_rebuild=True)
    
    def on_refresh(self, event):
        """Refresh/rescan libraries"""
        # Guard: do nothing if no libraries configured
        if not self.library_manager.config["libraries"]:
            return

        try:
            result = self.library_manager.scan_with_dialog(self)

            # If scan was cancelled, just refresh the UI and continue without showing dialogs
            if result is None:
                self.refresh_game_list()
                self.build_tree(force_rebuild=True)
                return

            exceptions_count, removed_libraries = result
            self.refresh_game_list()
            self.build_tree(force_rebuild=True)
            
            # Check for removed libraries first
            if removed_libraries:
                lib_paths = "\n".join([f"• {lib['name']}: {lib['path']}" for lib in removed_libraries])
                message = f"The following library paths were not found and have been removed from your configuration:\n\n{lib_paths}\n\nWould you like to update your library settings?"
                if wx.MessageBox(message, "Missing Library Paths Removed", 
                                wx.YES_NO | wx.ICON_WARNING) == wx.YES:
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

        # Save tree selections
        self.save_tree_selections()

        self.library_manager.save_config()
    
    def on_close(self, event):
        """Handle window close"""
        self.save_state()
        self.library_manager.save_games()
        self.library_manager.save_config()
        self.Destroy()