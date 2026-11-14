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

from pathlib import Path

import wx
import platform
from contextlib import nullcontext
from library_manager import LibraryError
from engine import messages
from engine.folders import library_label
from install_dialogs import RemoveLibraryDialog
from install_prefs import check_install_prefs, current_catalog_url, register_shared_components
from library_actions import executable_problem, library_row_name
from wx_bridge import run_in_background
from models import Game, is_web_path
from validation_service import ValidationService
from smart_list import SmartList, Column

# The file picker of Add Game and Locate: the files PathManager.is_executable accepts first
EXECUTABLE_WILDCARD = ("Executable files (*.exe)|*.exe|Batch files (*.bat)|*.bat|All files (*.*)|*.*"
                       if platform.system() == "Windows" else "Applications (*.app)|*.app|All files (*.*)|*.*")


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
        with wx.DirDialog(self, "Choose Game Library Directory") as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                try:
                    self.library_manager.add_library(dlg.GetPath())
                except LibraryError as e:
                    wx.MessageBox(str(e), "Library", wx.OK | wx.ICON_ERROR)

    def on_add_game(self, event):
        """Open add game dialog"""
        with GameDialog(self, self.library_manager) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.library_manager.games.append(dlg.game)
                self.parent_frame._save_games()

    def on_preferences(self, event):
        """Open preferences dialog"""
        with PreferencesDialog(self, self.library_manager, getattr(self.parent_frame, "service", None)) as dlg:
            dlg.ShowModal()

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
        if self.game.is_web:
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

        self.path_ctrl = wx.TextCtrl(panel, value=self.game.launch_path)
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
        self.year_ctrl = wx.TextCtrl(panel, value=self.game.year)  # empty: no year
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
            if not self.path_ctrl.GetValue():
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
        with wx.FileDialog(self, "Select Game Executable", wildcard=EXECUTABLE_WILDCARD) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.path_ctrl.SetValue(dlg.GetPath())

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

            problem = executable_problem(path, platform_val)  # the file exists and is a game executable
            if problem:
                wx.MessageBox(*problem, wx.OK | wx.ICON_ERROR)
                return

        # Validate year if provided
        year_str = self.year_ctrl.GetValue().strip()
        is_valid, error = ValidationService.validate_year(year_str)
        if not is_valid:
            wx.MessageBox(error, "Error", wx.OK | wx.ICON_ERROR)
            return

        # Asynchronously check for duplicates
        self._check_duplicates_async(path, platform_val, year_str)

    def _check_duplicates_async(self, path, platform_val, year_str):
        """Look for a game with this path on a worker (a drive can be slow to answer), then finish here"""
        def find():
            try:
                return self._find_duplicate_game(path), None
            except Exception as e:
                return None, str(e)
        run_in_background(find, lambda found: self._on_duplicate_check_complete(*found, path, platform_val, year_str),
                          "duplicate-check")

    def _find_duplicate_game(self, path):
        """Find if a game with this path already exists"""
        # Normalize the path for comparison
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
            if not is_web_path(path) and not game.is_web:
                try:
                    # A library game's path joined to its library; a game added by hand's as it is
                    game_full_path = self.library_manager.get_full_path(game)
                    if game_full_path and str(Path(game_full_path).resolve()) == normalized_path:
                        return {'type': 'resolved', 'game': game, 'match_path': game_full_path}
                except (OSError, ValueError):
                    # Path resolution failed, skip this comparison
                    continue

        return None

    def _on_duplicate_check_complete(self, duplicate_info, error, path, platform_val, year_str):
        """Handle duplicate check completion"""
        if not self or not self.IsModal():  # closed meanwhile (Escape), or an earlier Add's check finished it
            return
        if error is not None:
            wx.MessageBox(f"Error checking for duplicates: {error}\n\nGame will not be saved.",
                          "Duplicate Check Failed", wx.OK | wx.ICON_ERROR)
            return
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

    def _complete_validation(self, path, platform_val, year_str):
        """Complete validation and save the game"""
        # Get the current title
        title = self.title_ctrl.GetValue().strip()

        # Save validated data
        self.game.title = title
        self.game.genre = self.genre_ctrl.GetValue().strip()
        self.game.developer = self.developer_ctrl.GetValue().strip()
        self.game.year = year_str and str(int(year_str))  # "+2000" or "２０００" is kept as "2000"

        # Handle platform and path
        if platform_val == "Web Game":
            self.game.platforms = ["Web"]
        else:
            self.game.platforms = [platform_val]

        # A game added by hand always has an absolute path and no library
        self.game.library_id = ""
        self.game.launch_path = path

        self.EndModal(wx.ID_OK)



class PreferencesDialog(wx.Dialog):
    """Preferences dialog with tabbed layout.

    It holds library ids only, never a Game, so the main window applies finished installs while it is open
    (`holds_no_games`) and calls on_job_done after each, which redraws the library list from the config. The
    prompts it opens itself still wait for job results (the parent's modal_dialog()). `service` (the
    InstallService) uninstalls a library's catalog games before Remove takes it away; without one, Remove only
    forgets the library.
    """

    holds_no_games = True

    def __init__(self, parent, library_manager, service=None):
        super().__init__(parent, title="Preferences", size=(700, 500))

        self.library_manager = library_manager
        self.service = service
        # Nested prompts are modal dialogs that may hold games: they defer job results like any other
        self._modal = getattr(parent, "modal_dialog", None) or nullcontext
        self._notify = getattr(parent, "notify", None)  # the main window's notification, for a slow count
        # Runs work once no prompt is open over Preferences (the main window's; it doesn't count Preferences itself)
        self._when_no_prompt = getattr(parent, "run_when_no_dialog", None) or (lambda fn, *args: fn(*args))
        self._counting = False  # a Remove is counting the player's files in the background
        # The catalog address when Preferences opened, so the main window can tell if it changed (even over
        # several Applies, and even when the player then chooses Cancel)
        self.catalog_url_at_open = current_catalog_url(library_manager.config["install"])

        # Create notebook
        self.notebook = wx.Notebook(self)

        # Create tabs
        self.create_library_paths_tab()
        self.create_installing_tab()

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
        self._fill_library_list()

        lib_sizer.Add(self.lib_list.control.control, 1, wx.EXPAND | wx.RIGHT, 5)

        # Library buttons
        lib_btn_panel = wx.Panel(lib_container)
        lib_btn_sizer = wx.BoxSizer(wx.VERTICAL)

        add_lib_btn = wx.Button(lib_btn_panel, label="Add")
        change_lib_btn = wx.Button(lib_btn_panel, label="Change folder")
        remove_lib_btn = wx.Button(lib_btn_panel, label="Remove")

        lib_btn_sizer.Add(add_lib_btn, 0, wx.EXPAND | wx.BOTTOM, 5)
        lib_btn_sizer.Add(change_lib_btn, 0, wx.EXPAND | wx.BOTTOM, 5)
        lib_btn_sizer.Add(remove_lib_btn, 0, wx.EXPAND)

        lib_btn_panel.SetSizer(lib_btn_sizer)
        lib_sizer.Add(lib_btn_panel, 0, wx.ALIGN_TOP)

        lib_container.SetSizer(lib_sizer)
        tab_sizer.Add(lib_container, 1, wx.EXPAND | wx.ALL, 5)

        tab_panel.SetSizer(tab_sizer)
        self.notebook.AddPage(tab_panel, "Library Paths")

        # Bind events
        add_lib_btn.Bind(wx.EVT_BUTTON, self.on_add_library)
        change_lib_btn.Bind(wx.EVT_BUTTON, self.on_change_library_folder)
        remove_lib_btn.Bind(wx.EVT_BUTTON, self.on_remove_library)

    def create_installing_tab(self):
        """Install preferences: the shared-components checkbox and the catalog address. The address field
        follows its label, so a screen reader announces it."""
        install = self.library_manager.config["install"]
        tab_panel = wx.Panel(self.notebook)
        tab_sizer = wx.BoxSizer(wx.VERTICAL)

        self.shared_check = wx.CheckBox(tab_panel, label=messages.PREFS_REGISTER_SHARED)
        self.shared_check.SetValue(register_shared_components(install))
        tab_sizer.Add(self.shared_check, 0, wx.ALL, 10)

        address_label = wx.StaticText(tab_panel, label=messages.PREFS_CATALOG_ADDRESS)
        tab_sizer.Add(address_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.address_ctrl = wx.TextCtrl(tab_panel, value=current_catalog_url(install), name=messages.PREFS_CATALOG_ADDRESS)
        tab_sizer.Add(self.address_ctrl, 0, wx.EXPAND | wx.ALL, 10)

        tab_panel.SetSizer(tab_sizer)
        self.notebook.AddPage(tab_panel, messages.PREFS_INSTALLING_TAB)
        self.installing_page = tab_panel

    def _save_install_prefs(self):
        """Check the Installing tab and store it. An invalid address says so, keeps the old values, and puts
        focus in the address field. Returns False then."""
        install = self.library_manager.config["install"]
        result = check_install_prefs(install, self.address_ctrl.GetValue(), self.shared_check.GetValue())
        if not result.ok:
            self.notebook.SetSelection(self.notebook.FindPage(self.installing_page))
            self._tell(messages.PREFS_BAD_CATALOG_ADDRESS, messages.PREFS_BAD_ADDRESS_TITLE)
            self.address_ctrl.SelectAll()
            self.address_ctrl.SetFocus()
            return False
        install["catalog_url"] = result.catalog_url
        install["register_shared_components"] = result.register_shared
        self.address_ctrl.ChangeValue(result.catalog_url)
        return True

    def _fill_library_list(self):
        """Show the libraries; rows carry the id so actions don't depend on the folder name. A library waiting
        for its games' uninstalls says so. The selected library stays selected (or the row where it was)."""
        selected = self._selected_library()
        index = self.lib_list.get_selected_index()
        removing = self.service.pending_removal if self.service is not None else set()
        rows = [{"id": lib["id"], "name": library_row_name(library_label(lib), lib["id"] in removing),
                 "path": lib["path"]}
                for lib in self.library_manager.config["libraries"]]
        self.lib_list.clear()
        self.lib_list.add_items(rows)
        if not rows or index < 0:
            return
        ids = [row["id"] for row in rows]
        if selected is not None and selected["id"] in ids:
            index = ids.index(selected["id"])
        self.lib_list.set_selected_index(min(index, len(rows) - 1))

    def _selected_library(self):
        """The selected row ({"id", "name", "path"}), or None"""
        index = self.lib_list.get_selected_index()
        return self.lib_list.find_item_from_index(index) if index >= 0 else None

    def _tell(self, text, caption="Library"):
        with self._modal():
            wx.MessageBox(text, caption, wx.OK | wx.ICON_ERROR, self)

    def _change_libraries(self, action, *args):
        """Run a library change, show why it can't be done, and redraw the list"""
        try:
            action(*args)
        except LibraryError as e:
            self._tell(str(e))
        self._fill_library_list()

    def on_job_done(self, job):
        """An install job ended and the library was updated (the main window's job_done_hooks): a library whose
        last uninstall finished is gone from the config now, so it leaves the list"""
        if self:
            self._fill_library_list()

    def on_add_library(self, event):
        """Add a new library path (applies immediately)"""
        with self._modal(), wx.DirDialog(self, "Choose Game Library Directory") as dlg:
            chosen, path = dlg.ShowModal() == wx.ID_OK, dlg.GetPath()
        if chosen:
            self._change_libraries(self.library_manager.add_library, path)

    def on_change_library_folder(self, event):
        """Point the selected library at another folder (applies immediately)"""
        selected = self._selected_library()
        if not selected:
            return
        if self.service is not None and selected["id"] in self.service.pending_removal:
            # its queued uninstalls find the game folders through the library when they run
            self._tell(messages.LIBRARY_BEING_REMOVED)
            return
        with self._modal(), wx.DirDialog(self, "Choose the library's new folder", defaultPath=selected["path"]) as dlg:
            chosen, path = dlg.ShowModal() == wx.ID_OK, dlg.GetPath()
        if chosen:
            self._change_libraries(self.library_manager.change_library_folder, selected["id"], path)

    def on_remove_library(self, event):
        """Remove the selected library (applies immediately). One holding catalog installs is removed once they
        are uninstalled, after the player confirms; its folder must be reachable for that. The player's files
        are counted in the background first; the confirmation opens when the count arrives."""
        selected = self._selected_library()
        if not selected or self._counting:
            return
        try:
            self._remove_library(selected["id"])
        finally:
            self.lib_list.control.SetFocus()  # back to the list (the prompts would otherwise leave it on Remove)

    def _removable(self, library_id):
        """The library and its tracked catalog games, or None after saying why it can't be removed now (gone
        meanwhile, already being removed, unreachable, a game busy). With no tracked games it is removed here
        (its other catalog games stay in the list as games added by hand)."""
        manager = self.library_manager
        lib = manager.library_by_id(library_id)
        if lib is None:  # gone meanwhile (its last uninstall just finished): say so, as Change folder does
            self._tell(messages.LIBRARY_FOLDER_MISSING)
            self._fill_library_list()
            return None
        if self.service is None:
            self._change_libraries(manager.remove_library, library_id)
            return None
        if library_id in self.service.pending_removal:
            self._tell(messages.LIBRARY_BEING_REMOVED)
            return None
        try:
            self.service.check_library_removable(library_id)  # before any file is read
        except LibraryError as e:
            self._tell(str(e))
            return None
        tracked, kept = self.service.library_games(library_id)
        if not tracked:
            self._change_libraries(self.service.remove_library, library_id, False)
            return None
        return lib, tracked, kept

    def _remove_library(self, library_id):
        found = self._removable(library_id)
        if found is None:
            return
        lib, tracked, _ = found
        service = self.service
        ids = [g.catalog_id for g in tracked]
        notify = self._notify
        label = library_label(lib)
        self._counting = True
        run_in_background(lambda: sum(service.player_files(cid) for cid in ids),
                          lambda count: self._counted(library_id, count), "library-player-files",
                          slow=(lambda: notify(messages.COUNTING_LIBRARY_FILES.format(label=label))) if notify else None,
                          default=0)

    def _counted(self, library_id, player_files):
        """The count arrived (UI thread): ask once no other prompt (a folder dialog, an error box) is open on top"""
        if self:
            self._when_no_prompt(self._confirm_remove, library_id, player_files)

    def _confirm_remove(self, library_id, player_files):
        """The count arrived (UI thread): check again, then ask"""
        if not self:  # Preferences closed meanwhile
            return
        self._counting = False
        if not self.IsModal():
            return
        try:
            found = self._removable(library_id)
            if found is None:
                return
            lib, tracked, kept = found
            with self._modal(), RemoveLibraryDialog(self, library_label(lib), [g.title or g.catalog_id for g in tracked],
                                                    player_files, [g.title or g.catalog_id for g in kept]) as dlg:
                confirmed = dlg.ShowModal() == wx.ID_OK
                delete_player_data = dlg.delete_player_data()
            if confirmed:
                self._change_libraries(self.service.remove_library, library_id, delete_player_data)
        finally:
            if self:
                self.lib_list.control.SetFocus()

    def catalog_url_changed(self) -> bool:
        """The saved catalog address differs from the one Preferences opened with."""
        return current_catalog_url(self.library_manager.config["install"]) != self.catalog_url_at_open

    def on_apply(self, event):
        """Save the config. Library actions already applied; the Installing tab is saved here, and an invalid
        address keeps the old values and the dialog open. Returns whether it saved."""
        if not self._save_install_prefs():
            return False
        self.library_manager.save_config()
        return True

    def on_ok(self, event):
        """Save and close (stays open when the catalog address isn't valid)"""
        if self.on_apply(event):
            self.EndModal(wx.ID_OK)