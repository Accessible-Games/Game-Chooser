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

"""
Dialog classes for Game Chooser application
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
import os
import platform
import threading
from models import Game
from validation_service import ValidationService
from smart_list import SmartList, Column


class FirstTimeSetupDialog(wx.Dialog):
    """First-time setup dialog shown when no config exists"""

    def __init__(self, parent, library_manager):
        super().__init__(parent, title="Welcome to Game Chooser",
                        style=wx.CAPTION | wx.CLOSE_BOX | wx.SYSTEM_MENU)

        self.library_manager = library_manager
        self.parent_frame = parent
        self.initial_library_count = len(library_manager.config["libraries"])

        self.init_ui()
        self.CenterOnParent()

        # Handle close button (X) click
        self.Bind(wx.EVT_CLOSE, self.on_close)

    def init_ui(self):
        """Initialize the dialog UI"""
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Welcome message
        welcome_text = wx.StaticText(panel,
            label="Welcome to Game Chooser!\n\nChoose how you'd like to get started:")
        main_sizer.Add(welcome_text, 0, wx.ALL, 20)

        # Buttons
        add_library_btn = wx.Button(panel, label="Add Library")
        add_library_btn.Bind(wx.EVT_BUTTON, self.on_add_library)
        main_sizer.Add(add_library_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 20)

        add_game_btn = wx.Button(panel, label="Add Game")
        add_game_btn.Bind(wx.EVT_BUTTON, self.on_add_game)
        main_sizer.Add(add_game_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 20)

        preferences_btn = wx.Button(panel, label="Preferences")
        preferences_btn.Bind(wx.EVT_BUTTON, self.on_preferences)
        main_sizer.Add(preferences_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 20)

        exit_btn = wx.Button(panel, label="Exit")
        exit_btn.Bind(wx.EVT_BUTTON, self.on_exit)
        main_sizer.Add(exit_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 20)

        panel.SetSizer(main_sizer)
        main_sizer.Fit(panel)
        self.SetClientSize(panel.GetSize())

    def on_add_library(self, event):
        """Open directory picker to add library"""
        dlg = wx.DirDialog(self, "Choose Game Library Directory")
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            # Drive roots (e.g. G:) have no basename; fall back to the path itself
            name = os.path.basename(path.rstrip("\\/")) or path
            self.library_manager.config["libraries"].append({
                "name": name,
                "path": path
            })
            self.library_manager.save_config()
        dlg.Destroy()

    def on_add_game(self, event):
        """Open add game dialog"""
        dlg = GameDialog(self, self.library_manager)
        if dlg.ShowModal() == wx.ID_OK:
            self.library_manager.games.append(dlg.game)
            self.library_manager.save_games()
        dlg.Destroy()

    def on_preferences(self, event):
        """Open preferences dialog"""
        dlg = PreferencesDialog(self, self.library_manager)
        dlg.ShowModal()
        dlg.Destroy()

    def on_close(self, event):
        """Handle close button (X) click"""
        # Check if libraries were added during this session
        current_library_count = len(self.library_manager.config["libraries"])
        libraries_added = current_library_count > self.initial_library_count

        # Return custom code if libraries were added, otherwise cancel
        if libraries_added:
            self.EndModal(wx.ID_OK)  # Indicate libraries were added
        else:
            self.EndModal(wx.ID_CANCEL)

    def on_exit(self, event):
        """Exit the setup dialog"""
        # Use the same logic as close
        self.on_close(event)


class GameDialog(wx.Dialog):
    """Unified dialog for adding and editing games"""

    def __init__(self, parent, library_manager, game=None):
        self.is_new = game is None
        title = "Add Game" if self.is_new else "Edit Game"
        super().__init__(parent, title=title, size=(400, 350))

        self.game = game or Game()
        self.library_manager = library_manager
        self.original_path = game.launch_path if game else ""

        # Get existing genres and developers for combo boxes
        genres = sorted(set(g.genre for g in library_manager.games if g.genre))
        developers = sorted(set(g.developer for g in library_manager.games if g.developer))

        panel = wx.Panel(self)
        sizer = wx.GridBagSizer(5, 5)

        # Title
        sizer.Add(wx.StaticText(panel, label="Title:"),
                 pos=(0, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        self.title_ctrl = wx.TextCtrl(panel, value=self.game.title)
        sizer.Add(self.title_ctrl, pos=(0, 1), flag=wx.EXPAND)

        # Platform
        sizer.Add(wx.StaticText(panel, label="Platform:"),
                 pos=(1, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        platforms = ["Windows", "macOS", "Web Game"]
        self.platform_ctrl = wx.ComboBox(panel, choices=platforms, style=wx.CB_DROPDOWN | wx.CB_READONLY)

        # Determine initial platform
        if self.game.launch_path.startswith("http"):
            initial_platform = "Web Game"
        elif self.game.platforms:
            initial_platform = self.game.platforms[0]
        else:
            system = platform.system()
            initial_platform = "Windows" if system == "Windows" else "macOS"
        self.platform_ctrl.SetValue(initial_platform)

        sizer.Add(self.platform_ctrl, pos=(1, 1), flag=wx.EXPAND)

        # Path/URL field (dynamic based on platform)
        self.path_label = wx.StaticText(panel, label="Launch Path:")
        sizer.Add(self.path_label, pos=(2, 0), flag=wx.ALIGN_CENTER_VERTICAL)

        # For editing, convert relative path to absolute for display
        display_path = self._get_display_path()
        self.path_ctrl = wx.TextCtrl(panel, value=display_path)
        self.browse_btn = wx.Button(panel, label="Browse...")
        self.browse_btn.Bind(wx.EVT_BUTTON, self.on_browse)

        sizer.Add(self.path_ctrl, pos=(2, 1), flag=wx.EXPAND)
        sizer.Add(self.browse_btn, pos=(2, 2), flag=wx.LEFT, border=5)

        # Genre
        sizer.Add(wx.StaticText(panel, label="Genre:"),
                 pos=(3, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        self.genre_ctrl = wx.ComboBox(panel, value=self.game.genre, choices=genres)
        sizer.Add(self.genre_ctrl, pos=(3, 1), flag=wx.EXPAND)

        # Developer
        sizer.Add(wx.StaticText(panel, label="Developer:"),
                 pos=(4, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        self.developer_ctrl = wx.ComboBox(panel, value=self.game.developer,
                                         choices=developers)
        sizer.Add(self.developer_ctrl, pos=(4, 1), flag=wx.EXPAND)

        # Year
        sizer.Add(wx.StaticText(panel, label="Year:"),
                 pos=(5, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        year_val = 2000 if not self.game.year else int(self.game.year)
        self.year_ctrl = wx.SpinCtrl(panel, value=str(year_val),
                                    min=1000, max=9999, initial=year_val)
        sizer.Add(self.year_ctrl, pos=(5, 1), flag=wx.EXPAND)

        # Buttons
        btn_sizer = wx.StdDialogButtonSizer()
        ok_label = "Add" if self.is_new else "Save"
        ok_btn = wx.Button(panel, wx.ID_OK, ok_label)
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, "Cancel")
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()

        sizer.Add(btn_sizer, pos=(6, 0), span=(1, 2),
                 flag=wx.ALIGN_CENTER | wx.TOP, border=10)

        sizer.AddGrowableCol(1)
        panel.SetSizer(sizer)

        # Event bindings
        ok_btn.Bind(wx.EVT_BUTTON, self.on_ok)
        self.platform_ctrl.Bind(wx.EVT_COMBOBOX, self.on_platform_change)

        # Update UI based on initial platform
        self._update_path_ui()

    def _get_display_path(self):
        """Get the path to display in the field (absolute for editing library games)"""
        if not self.game.launch_path:
            return ""

        if self.game.launch_path.startswith("http"):
            return self.game.launch_path

        if self.game.library_name and self.game.library_name != "":
            # Library game - convert to absolute path for editing
            full_path = self.library_manager.get_full_path(self.game)
            return full_path if full_path else self.game.launch_path
        else:
            # User-managed game - already absolute
            return self.game.launch_path

    def on_platform_change(self, event):
        """Handle platform selection change"""
        self._update_path_ui()

    def _update_path_ui(self):
        """Update the path field UI based on platform selection"""
        platform = self.platform_ctrl.GetValue()

        if platform == "Web Game":
            self.path_label.SetLabel("URL:")
            self.browse_btn.Hide()
            # If switching to web game and field is empty, prefill with https://
            if not self.path_ctrl.GetValue() or not self.path_ctrl.GetValue().startswith("http"):
                self.path_ctrl.SetValue("https://")
        else:
            self.path_label.SetLabel("Launch Path:")
            self.browse_btn.Show()
            # If switching from web game, clear the https:// prefix
            current_value = self.path_ctrl.GetValue()
            if current_value == "https://":
                self.path_ctrl.SetValue("")

        # Force layout update
        self.Layout()

    def on_browse(self, event):
        """Browse for executable file"""
        system = platform.system()
        if system == "Windows":
            wildcard = "Executable files (*.exe)|*.exe|Batch files (*.bat)|*.bat|All files (*.*)|*.*"
        else:  # macOS
            wildcard = "Applications (*.app)|*.app|All files (*.*)|*.*"

        dlg = wx.FileDialog(self, "Select Game Executable", wildcard=wildcard)
        if dlg.ShowModal() == wx.ID_OK:
            self.path_ctrl.SetValue(dlg.GetPath())
        dlg.Destroy()

    def on_ok(self, event):
        """Validate and save changes"""
        # Validate title
        title = self.title_ctrl.GetValue().strip()
        is_valid, error = ValidationService.validate_title(title)
        if not is_valid:
            wx.MessageBox(error, "Error", wx.OK | wx.ICON_ERROR)
            return

        # Validate platform
        platform_val = self.platform_ctrl.GetValue().strip()
        if not platform_val:
            wx.MessageBox("Platform is required", "Error", wx.OK | wx.ICON_ERROR)
            return

        # Validate path/URL
        path = self.path_ctrl.GetValue().strip()
        if platform_val == "Web Game":
            is_valid, error = ValidationService.validate_url(path)
            if not is_valid:
                wx.MessageBox(error, "Error", wx.OK | wx.ICON_ERROR)
                return
        else:
            # Validate path format
            is_valid, error = ValidationService.validate_path(path, must_exist=False)
            if not is_valid:
                wx.MessageBox(error, "Error", wx.OK | wx.ICON_ERROR)
                return

            # Validate executable file exists and is supported
            from pathlib import Path
            from path_manager import PathManager
            path_obj = Path(path)

            # Check if file exists
            if not path_obj.exists():
                wx.MessageBox(f"File does not exist: {path}",
                             "File Not Found", wx.OK | wx.ICON_ERROR)
                return

            # Use PathManager's robust executable validation
            if not PathManager.is_executable(path_obj):
                if platform_val == "Windows":
                    valid_exts_str = ".exe, .bat"
                else:  # macOS
                    valid_exts_str = ".app"
                wx.MessageBox(f"File is not a valid executable.\n\nSupported types for {platform_val}: {valid_exts_str}",
                             "Invalid Executable", wx.OK | wx.ICON_ERROR)
                return

        # Validate year if provided
        year_val = self.year_ctrl.GetValue()
        year_str = str(year_val) if year_val != 2000 else ""
        if year_str:
            is_valid, error = ValidationService.validate_year(year_str)
            if not is_valid:
                wx.MessageBox(error, "Error", wx.OK | wx.ICON_ERROR)
                return

        # Asynchronously check for duplicates
        self._check_duplicates_async(path, platform_val, year_str)

    def _check_duplicates_async(self, path, platform_val, year_str):
        """Asynchronously check for duplicate games and complete validation"""
        def check_duplicates():
            """Background thread to check for duplicates"""
            try:
                duplicate_info = self._find_duplicate_game(path)
                wx.CallAfter(self._on_duplicate_check_complete, duplicate_info, path, platform_val, year_str)
            except Exception as e:
                wx.CallAfter(self._on_duplicate_check_error, str(e))

        # Start background thread
        thread = threading.Thread(target=check_duplicates, daemon=True)
        thread.start()

    def _find_duplicate_game(self, path):
        """Find if a game with this path already exists"""
        # Normalize the path for comparison
        from pathlib import Path
        normalized_path = str(Path(path).resolve())

        for game in self.library_manager.games:
            if game == self.game:  # Skip the current game if editing
                continue

            # Check exact path match
            if game.launch_path == path:
                return {
                    'type': 'exact',
                    'game': game,
                    'match_path': path
                }

            # For non-web games, also check resolved absolute paths
            if not path.startswith('http') and not game.launch_path.startswith('http'):
                try:
                    if game.library_name and game.library_name != "":
                        # Library game - resolve to absolute path
                        game_full_path = self.library_manager.get_full_path(game)
                        if game_full_path:
                            game_resolved = str(Path(game_full_path).resolve())
                            if game_resolved == normalized_path:
                                return {
                                    'type': 'resolved',
                                    'game': game,
                                    'match_path': game_full_path
                                }
                    else:
                        # User-managed game - compare resolved paths
                        game_resolved = str(Path(game.launch_path).resolve())
                        if game_resolved == normalized_path:
                            return {
                                'type': 'resolved',
                                'game': game,
                                'match_path': game.launch_path
                            }
                except (OSError, ValueError):
                    # Path resolution failed, skip this comparison
                    continue

        return None

    def _on_duplicate_check_complete(self, duplicate_info, path, platform_val, year_str):
        """Handle duplicate check completion"""
        if duplicate_info:
            duplicate_game = duplicate_info['game']
            match_type = duplicate_info['type']
            match_path = duplicate_info['match_path']

            if match_type == 'exact':
                message = f"A game with this exact path already exists:\n\n" \
                         f"Title: {duplicate_game.title}\n" \
                         f"Path: {match_path}\n\n" \
                         f"Cannot add duplicate game."
            else:
                message = f"A game pointing to the same file already exists:\n\n" \
                         f"Title: {duplicate_game.title}\n" \
                         f"Existing path: {match_path}\n" \
                         f"Your path: {path}\n\n" \
                         f"Both paths resolve to the same file. Cannot add duplicate."

            wx.MessageBox(message, "Duplicate Game Found", wx.OK | wx.ICON_WARNING)
            return

        # No duplicates found, complete the validation
        self._complete_validation(path, platform_val, year_str)

    def _on_duplicate_check_error(self, error_msg):
        """Handle duplicate check error"""
        wx.MessageBox(f"Error checking for duplicates: {error_msg}\n\nGame will not be saved.",
                     "Duplicate Check Failed", wx.OK | wx.ICON_ERROR)

    def _complete_validation(self, path, platform_val, year_str):
        """Complete validation and save the game"""
        # Get the current title
        title = self.title_ctrl.GetValue().strip()

        # Save validated data
        self.game.title = title
        self.game.genre = self.genre_ctrl.GetValue().strip()
        self.game.developer = self.developer_ctrl.GetValue().strip()
        self.game.year = year_str or ""

        # Handle platform and path
        if platform_val == "Web Game":
            self.game.platforms = ["Web"]
        else:
            self.game.platforms = [platform_val]

        # Handle library_name based on path changes
        if self.is_new:
            # New game - always user-managed
            self.game.library_name = ""
            self.game.launch_path = path
        else:
            # Editing existing game
            if self.game.library_name and path != self.original_path:
                # Library game with changed path becomes user-managed
                self.game.library_name = ""
                self.game.launch_path = path
            else:
                # Keep existing library_name, update path
                self.game.launch_path = path

        self.EndModal(wx.ID_OK)



class PreferencesDialog(wx.Dialog):
    """Preferences dialog with tabbed layout"""

    def __init__(self, parent, library_manager):
        super().__init__(parent, title="Preferences", size=(700, 500))

        self.library_manager = library_manager

        # Create notebook
        self.notebook = wx.Notebook(self)

        # Create tabs
        self.create_library_paths_tab()

        # Main sizer with buttons
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(self.notebook, 1, wx.EXPAND)
        
        # Buttons
        btn_panel = wx.Panel(self)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.AddStretchSpacer()
        
        ok_btn = wx.Button(btn_panel, wx.ID_OK, "OK")
        cancel_btn = wx.Button(btn_panel, wx.ID_CANCEL, "Cancel")
        apply_btn = wx.Button(btn_panel, wx.ID_APPLY, "Apply")
        
        btn_sizer.Add(ok_btn, 0, wx.RIGHT, 5)
        btn_sizer.Add(cancel_btn, 0, wx.RIGHT, 5)
        btn_sizer.Add(apply_btn, 0, wx.RIGHT, 5)
        
        btn_panel.SetSizer(btn_sizer)
        main_sizer.Add(btn_panel, 0, wx.EXPAND | wx.ALL, 5)
        
        self.SetSizer(main_sizer)
        
        # Bind events
        ok_btn.Bind(wx.EVT_BUTTON, self.on_ok)
        apply_btn.Bind(wx.EVT_BUTTON, self.on_apply)
        
    
    def create_library_paths_tab(self):
        """Create library paths tab"""
        tab_panel = wx.Panel(self.notebook)
        tab_sizer = wx.BoxSizer(wx.VERTICAL)

        # Library paths section
        lib_label = wx.StaticText(tab_panel, label="Game Library Paths:")
        tab_sizer.Add(lib_label, 0, wx.ALL, 5)

        lib_container = wx.Panel(tab_panel)
        lib_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.lib_list = SmartList(parent=lib_container, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.lib_list.SetLabel("Game Library Paths")
        self.lib_list.set_columns([
            Column(title="Name", model_field="name", width=100),
            Column(title="Path", model_field="path", width=400)
        ])

        # Populate library list
        self.lib_list.add_items(self.library_manager.config["libraries"])

        lib_sizer.Add(self.lib_list.control.control, 1, wx.EXPAND | wx.RIGHT, 5)

        # Library buttons
        lib_btn_panel = wx.Panel(lib_container)
        lib_btn_sizer = wx.BoxSizer(wx.VERTICAL)

        add_lib_btn = wx.Button(lib_btn_panel, label="Add")
        remove_lib_btn = wx.Button(lib_btn_panel, label="Remove")

        lib_btn_sizer.Add(add_lib_btn, 0, wx.EXPAND | wx.BOTTOM, 5)
        lib_btn_sizer.Add(remove_lib_btn, 0, wx.EXPAND)

        lib_btn_panel.SetSizer(lib_btn_sizer)
        lib_sizer.Add(lib_btn_panel, 0, wx.ALIGN_TOP)

        lib_container.SetSizer(lib_sizer)
        tab_sizer.Add(lib_container, 1, wx.EXPAND | wx.ALL, 5)

        tab_panel.SetSizer(tab_sizer)
        self.notebook.AddPage(tab_panel, "Library Paths")

        # Bind events
        add_lib_btn.Bind(wx.EVT_BUTTON, self.on_add_library)
        remove_lib_btn.Bind(wx.EVT_BUTTON, self.on_remove_library)

    def on_add_library(self, event):
        """Add a new library path"""
        dlg = wx.DirDialog(self, "Choose Game Library Directory")
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            # Drive roots (e.g. G:) have no basename; fall back to the path itself
            name = os.path.basename(path.rstrip("\\/")) or path

            # Create library dict
            new_lib = {"name": name, "path": path}

            # Add to list control
            self.lib_list.add_item(new_lib)

            # Add to config
            self.library_manager.config["libraries"].append(new_lib)
        dlg.Destroy()
    
    def on_remove_library(self, event):
        """Remove selected library path"""
        selected_item = self.lib_list.get_selected_item()
        if selected_item:
            # Remove from config
            self.library_manager.config["libraries"].remove(selected_item)
            # Remove from list
            self.lib_list.delete_item(selected_item)

    def on_apply(self, event):
        """Save the config"""
        self.library_manager.save_config()

    def on_ok(self, event):
        """Save and close"""
        self.on_apply(event)
        self.EndModal(wx.ID_OK)


class DeleteGameDialog(wx.Dialog):
    """Confirmation dialog for game deletion"""

    def __init__(self, parent, game_title):
        super().__init__(parent, title="Confirm Delete",
                        style=wx.CAPTION | wx.CLOSE_BOX | wx.SYSTEM_MENU)

        self.game_title = game_title
        self.init_ui()
        self.CenterOnParent()

    def init_ui(self):
        """Initialize the dialog UI"""
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Message
        message = wx.StaticText(self, label=f"Delete '{self.game_title}' from library?")
        sizer.Add(message, 0, wx.ALL | wx.CENTER, 15)

        # Buttons
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)

        yes_btn = wx.Button(self, wx.ID_YES, "Yes")
        no_btn = wx.Button(self, wx.ID_NO, "No")

        button_sizer.Add(yes_btn, 0, wx.ALL, 5)
        button_sizer.Add(no_btn, 0, wx.ALL, 5)

        sizer.Add(button_sizer, 0, wx.ALL | wx.CENTER, 10)

        # Bind events
        self.Bind(wx.EVT_BUTTON, self.on_yes, yes_btn)
        self.Bind(wx.EVT_BUTTON, self.on_no, no_btn)

        self.SetSizer(sizer)
        sizer.Fit(self)

    def on_yes(self, event):
        """Handle Yes button"""
        self.EndModal(wx.ID_YES)

    def on_no(self, event):
        """Handle No button"""
        self.EndModal(wx.ID_NO)