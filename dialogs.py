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
import json
import os
import platform
import threading
from models import Game
from validation_service import ValidationService
from smart_list import SmartList, Column


class ScanProgressDialog(wx.Dialog):
    """Dialog showing scanning progress with cancel button"""
    
    def __init__(self, parent):
        super().__init__(parent, title="Scanning for Games",
                        style=wx.CAPTION | wx.CLOSE_BOX | wx.SYSTEM_MENU)
        
        self.cancelled = False
        self.current_library = ""
        self.libraries_processed = 0
        self.total_libraries = 0
        self.games_found = 0
        
        self.init_ui()
        self.CenterOnParent()
        
    def init_ui(self):
        """Initialize the dialog UI"""
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Status text
        self.status_text = wx.StaticText(panel, label="Scanning for games...")
        main_sizer.Add(self.status_text, 0, wx.ALL | wx.EXPAND, 10)
        
        # Current library text
        self.library_text = wx.StaticText(panel, label="")
        main_sizer.Add(self.library_text, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)
        
        # Progress bar
        self.progress_bar = wx.Gauge(panel, range=100)
        main_sizer.Add(self.progress_bar, 0, wx.ALL | wx.EXPAND, 10)
        
        # Games found counter
        self.games_text = wx.StaticText(panel, label="Games found: 0")
        main_sizer.Add(self.games_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)
        
        # Cancel button
        cancel_btn = wx.Button(panel, wx.ID_CANCEL, "Cancel")
        cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        main_sizer.Add(cancel_btn, 0, wx.ALIGN_CENTER | wx.ALL, 10)
        
        panel.SetSizer(main_sizer)
        
        # Size the dialog
        main_sizer.Fit(panel)
        self.SetClientSize(panel.GetSize())
        self.SetMinSize((300, -1))
        
    def on_cancel(self, event):
        """Handle cancel button click"""
        self.cancelled = True
        self.status_text.SetLabel("Cancelling scan...")
        
    def update_progress(self, library_name, progress, games_found):
        """Update the progress display"""
        if self.cancelled:
            return
            
        # Use CallAfter to ensure UI updates happen on main thread
        wx.CallAfter(self._update_progress_ui, library_name, progress, games_found)
    
    def _update_progress_ui(self, library_name, progress, games_found):
        """Update progress UI on main thread"""
        if library_name != self.current_library:
            self.current_library = library_name
            self.library_text.SetLabel(f"Scanning: {library_name}")
            
        self.progress_bar.SetValue(int(progress))
        self.games_found = games_found
        self.games_text.SetLabel(f"Games found: {games_found}")
        
    def set_library_count(self, total):
        """Set total number of libraries to process"""
        self.total_libraries = total
        if total > 0:
            self.status_text.SetLabel("Scanning for games...")
    
    def finish_scan(self, games_found, exceptions_added):
        """Called when scan is complete"""
        wx.CallAfter(self._finish_scan_ui, games_found, exceptions_added)
        
    def _finish_scan_ui(self, games_found, exceptions_added):
        """Finish scan UI on main thread"""
        if self.cancelled:
            self.status_text.SetLabel("Scan cancelled")
        else:
            self.status_text.SetLabel(f"Scan complete! Found {games_found} games")
            if exceptions_added > 0:
                self.games_text.SetLabel(f"Games found: {games_found} (+{exceptions_added} exceptions added)")
        
        self.progress_bar.SetValue(100)
        
        # Auto-close after 1 second if not cancelled
        if not self.cancelled:
            wx.CallLater(1000, self.EndModal, wx.ID_OK)


class FirstTimeSetupDialog(wx.Dialog):
    """First-time setup dialog shown when no config exists"""

    def __init__(self, parent, library_manager):
        super().__init__(parent, title="Welcome to Game Chooser",
                        style=wx.CAPTION | wx.CLOSE_BOX | wx.SYSTEM_MENU)

        self.library_manager = library_manager
        self.parent_frame = parent
        self.reminder_shown = False
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

    def check_and_show_reminder(self):
        """Check if reminder should be shown after successful addition"""
        if self.reminder_shown:
            return

        # Check if user has added anything
        has_libraries = len(self.library_manager.config["libraries"]) > 0
        has_games = len(self.library_manager.games) > 0

        if has_libraries or has_games:
            wx.MessageBox(
                "You can add folders that should be excluded from a game library, "
                "as well as manage other preferences, by clicking the button here or pressing "
                "\"Ctrl+,\" at any time.",
                "Friendly Reminder", wx.OK | wx.ICON_INFORMATION)
            self.reminder_shown = True

    def on_add_library(self, event):
        """Open directory picker to add library"""
        dlg = wx.DirDialog(self, "Choose Game Library Directory")
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            name = os.path.basename(path)
            self.library_manager.config["libraries"].append({
                "name": name,
                "path": path
            })
            self.library_manager.save_config()
            self.check_and_show_reminder()
        dlg.Destroy()

    def on_add_game(self, event):
        """Open add game dialog"""
        dlg = GameDialog(self, self.library_manager)
        if dlg.ShowModal() == wx.ID_OK:
            self.library_manager.games.append(dlg.game)
            self.library_manager.save_games()
            self.check_and_show_reminder()
        dlg.Destroy()

    def on_preferences(self, event):
        """Open preferences dialog"""
        dlg = PreferencesDialog(self, self.library_manager)
        if dlg.ShowModal() == wx.ID_OK:
            self.check_and_show_reminder()
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
        self.create_exceptions_tab()

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
        
        # Store original config for cancel
        self.original_config = json.loads(json.dumps(library_manager.config))
    
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

    def create_exceptions_tab(self):
        """Create exceptions tab"""
        tab_panel = wx.Panel(self.notebook)
        tab_sizer = wx.BoxSizer(wx.VERTICAL)

        # Exceptions section
        exc_label = wx.StaticText(tab_panel, label="Exceptions:")
        tab_sizer.Add(exc_label, 0, wx.ALL, 5)

        exc_container = wx.Panel(tab_panel)
        exc_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.exc_list = SmartList(parent=exc_container, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.exc_list.SetLabel("Exceptions")
        self.exc_list.set_columns([
            Column(title="Path", model_field=lambda exc: exc, width=500)
        ])

        # Populate exceptions list
        self.exc_list.add_items(self.library_manager.config["exceptions"])

        exc_sizer.Add(self.exc_list.control.control, 1, wx.EXPAND | wx.RIGHT, 5)

        # Exception buttons
        exc_btn_panel = wx.Panel(exc_container)
        exc_btn_sizer = wx.BoxSizer(wx.VERTICAL)

        add_exc_btn = wx.Button(exc_btn_panel, label="Add")
        add_folder_btn = wx.Button(exc_btn_panel, label="Add Folder")
        remove_exc_btn = wx.Button(exc_btn_panel, label="Remove")

        exc_btn_sizer.Add(add_exc_btn, 0, wx.EXPAND | wx.BOTTOM, 5)
        exc_btn_sizer.Add(add_folder_btn, 0, wx.EXPAND | wx.BOTTOM, 5)
        exc_btn_sizer.Add(remove_exc_btn, 0, wx.EXPAND)

        exc_btn_panel.SetSizer(exc_btn_sizer)
        exc_sizer.Add(exc_btn_panel, 0, wx.ALIGN_TOP)

        exc_container.SetSizer(exc_sizer)
        tab_sizer.Add(exc_container, 1, wx.EXPAND | wx.ALL, 5)

        tab_panel.SetSizer(tab_sizer)
        self.notebook.AddPage(tab_panel, "Exceptions")

        # Bind events
        add_exc_btn.Bind(wx.EVT_BUTTON, self.on_add_exception)
        add_folder_btn.Bind(wx.EVT_BUTTON, self.on_add_folder_exception)
        remove_exc_btn.Bind(wx.EVT_BUTTON, self.on_remove_exception)
    
    def on_add_library(self, event):
        """Add a new library path"""
        dlg = wx.DirDialog(self, "Choose Game Library Directory")
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            name = os.path.basename(path)

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
    
    def on_add_exception(self, event):
        """Add a new file exception"""
        # Determine file filter based on platform
        system = platform.system()
        if system == "Windows":
            wildcard = "Executable files (*.exe;*.bat)|*.exe;*.bat|All files (*.*)|*.*"
        else:
            wildcard = "All files (*.*)|*.*"

        dlg = wx.FileDialog(self, "Select File to Exclude from Scanning",
                           wildcard=wildcard,
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            file_path = dlg.GetPath()
            if file_path:
                # Convert to relative path if within a library
                relative_path = self._make_relative_to_library(file_path)
                if relative_path:
                    self.exc_list.add_item(relative_path)
                    self.library_manager.config["exceptions"].append(relative_path)
                else:
                    wx.MessageBox("Selected file is not within any configured library path.",
                                "Invalid File", wx.OK | wx.ICON_WARNING)
        dlg.Destroy()

    def on_add_folder_exception(self, event):
        """Add a new folder exception"""
        dlg = wx.DirDialog(self, "Choose Folder to Exclude from Scanning")
        if dlg.ShowModal() == wx.ID_OK:
            folder_path = dlg.GetPath()
            if folder_path:
                # Convert to relative path if within a library
                relative_path = self._make_relative_to_library(folder_path)
                if relative_path:
                    # Add trailing slash to indicate this is a folder exception
                    folder_exception = relative_path.rstrip('/') + '/'
                    self.exc_list.add_item(folder_exception)
                    self.library_manager.config["exceptions"].append(folder_exception)
                else:
                    wx.MessageBox("Selected folder is not within any configured library path.",
                                "Invalid Folder", wx.OK | wx.ICON_WARNING)
        dlg.Destroy()

    def _make_relative_to_library(self, folder_path):
        """Convert absolute folder path to library-relative path"""
        import os
        from pathlib import Path

        folder_path = Path(folder_path)

        # Check if folder is within any configured library
        for lib in self.library_manager.config["libraries"]:
            lib_path = Path(lib["path"])
            try:
                # Check if folder_path is within lib_path
                rel_path = folder_path.relative_to(lib_path)
                # Return the relative path with forward slashes
                return str(rel_path).replace(os.sep, '/')
            except ValueError:
                # folder_path is not within this library
                continue

        return None

    def on_remove_exception(self, event):
        """Remove selected exception"""
        selected_item = self.exc_list.get_selected_item()
        if selected_item:
            self.library_manager.config["exceptions"].remove(selected_item)
            self.exc_list.delete_item(selected_item)
    
    def on_apply(self, event):
        """Apply changes and rescan if needed"""
        old_libs = self.original_config["libraries"]
        new_libs = self.library_manager.config["libraries"]

        # Check if libraries changed
        libs_changed = json.dumps(old_libs) != json.dumps(new_libs)

        # Check if exceptions changed
        old_exceptions = self.original_config.get("exceptions", [])
        new_exceptions = self.library_manager.config.get("exceptions", [])
        exceptions_changed = old_exceptions != new_exceptions

        self.library_manager.save_config()

        if libs_changed and self.library_manager.config["libraries"]:
            # Determine which libraries are completely new
            old_lib_names = set(lib["name"] for lib in old_libs)
            new_lib_names = set(lib["name"] for lib in new_libs)

            # Find new libraries that need full scan
            new_libraries_added = new_lib_names - old_lib_names

            # Ask user if they want to scan now
            response = wx.MessageBox(
                "Library configuration has changed. Would you like to scan for games now?\n\n"
                "You can also scan later using Refresh (F5).",
                "Scan Libraries?",
                wx.YES_NO | wx.ICON_QUESTION
            )

            if response != wx.YES:
                # User declined - just save and return
                return

            try:
                # Use unified scanning - method will handle new libraries intelligently
                result = self.library_manager.scan_with_dialog(
                    self.GetParent(),
                    new_libraries_added if new_libraries_added else None
                )
                
                # If scan was cancelled, just continue without showing dialogs
                if result is None:
                    return
                
                exc_count, removed_libraries = result
                
                # Check for removed libraries first
                if removed_libraries:
                    lib_paths = "\n".join([f"• {lib['name']}: {lib['path']}" for lib in removed_libraries])
                    message = f"The following library paths were not found and have been removed from your configuration:\n\n{lib_paths}"
                    wx.MessageBox(message, "Missing Library Paths Removed", wx.OK | wx.ICON_WARNING)
                    # Refresh the libraries list in the dialog
                    self.lib_list.clear()
                    self.lib_list.add_items(self.library_manager.config["libraries"])
                elif exc_count > 0:
                    if wx.MessageBox(f"Added {exc_count} executables to exceptions. Open preferences?",
                                    "Exceptions Added", 
                                    wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
                        # Already in preferences, just refresh the exceptions list
                        self.exc_list.clear()
                        self.exc_list.add_items(self.library_manager.config["exceptions"])
            except PermissionError as e:
                wx.MessageBox(str(e), "Permission Denied", wx.OK | wx.ICON_ERROR)
        elif exceptions_changed:
            # Only exceptions changed, not libraries - run cleanConfigs directly
            self.library_manager.cleanConfigs()

            # Refresh the exceptions list to show cleaned exceptions
            self.exc_list.clear()
            self.exc_list.add_items(self.library_manager.config["exceptions"])

        # Update original config
        self.original_config = json.loads(json.dumps(self.library_manager.config))
    
    def on_ok(self, event):
        """Save and close"""
        self.on_apply(event)
        self.EndModal(wx.ID_OK)


class DeleteGameDialog(wx.Dialog):
    """Custom dialog for game deletion with exception option"""

    # Return codes for different choices
    ID_DELETE_ONLY = wx.ID_HIGHEST + 1
    ID_DELETE_AND_EXCEPTION = wx.ID_HIGHEST + 2

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

        # Explanation
        explanation = wx.StaticText(self,
            label="Adding to exceptions prevents re-discovery in future scans.")
        explanation.SetFont(explanation.GetFont().Smaller())
        sizer.Add(explanation, 0, wx.LEFT | wx.RIGHT | wx.CENTER, 15)

        # Buttons
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)

        yes_btn = wx.Button(self, wx.ID_YES, "Yes")
        yes_exception_btn = wx.Button(self, self.ID_DELETE_AND_EXCEPTION, "Yes and add to exceptions")
        no_btn = wx.Button(self, wx.ID_NO, "No")

        button_sizer.Add(yes_btn, 0, wx.ALL, 5)
        button_sizer.Add(yes_exception_btn, 0, wx.ALL, 5)
        button_sizer.Add(no_btn, 0, wx.ALL, 5)

        sizer.Add(button_sizer, 0, wx.ALL | wx.CENTER, 10)

        # Bind events
        self.Bind(wx.EVT_BUTTON, self.on_yes, yes_btn)
        self.Bind(wx.EVT_BUTTON, self.on_yes_exception, yes_exception_btn)
        self.Bind(wx.EVT_BUTTON, self.on_no, no_btn)

        self.SetSizer(sizer)
        sizer.Fit(self)

    def on_yes(self, event):
        """Handle Yes button"""
        self.EndModal(wx.ID_YES)

    def on_yes_exception(self, event):
        """Handle Yes and add to exceptions button"""
        self.EndModal(self.ID_DELETE_AND_EXCEPTION)

    def on_no(self, event):
        """Handle No button"""
        self.EndModal(wx.ID_NO)