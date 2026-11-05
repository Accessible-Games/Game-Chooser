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
Main window for Game Chooser application
"""

import wx
import wx.adv
import os
import logging
import subprocess
import platform
import webbrowser
import threading
import random
from contextlib import contextmanager
from pathlib import Path

from models import Game
from library_manager import GameLibraryManager
from install_service import InstallService
from catalog_view import description_text, initial_tab
from engine.folders import library_label
from game_list import GameListCtrl
from catalog_panel import CatalogPanel
from description_field import DescriptionField
from dialogs import GameDialog, PreferencesDialog, DeleteGameDialog, FirstTimeSetupDialog

log = logging.getLogger(__name__)

TABS = ("library", "catalog")  # notebook page order


def _call_after(fn, *args):
    """wx.CallAfter from a worker thread; does nothing once the app is gone (closing while a worker runs)"""
    try:
        wx.CallAfter(fn, *args)
    except Exception:
        log.debug("The app closed before %s could run", getattr(fn, "__name__", fn))


class _NoQueueListener:
    """Ignores the install queue's events until the real listener exists (Task 10)."""

    def job_started(self, job):
        pass

    def job_progress(self, job, progress):
        pass

    def job_finished(self, job, result):
        pass

    def job_failed(self, job, message):
        pass

    def job_cancelled(self, job):
        pass

    def queue_changed(self, waiting):
        pass


class FilterWorker(threading.Thread):
    """Background thread for filtering games to prevent UI freezing"""

    def __init__(self, games, search_term, tree_criteria, library_filter, callback):
        super().__init__(daemon=True)
        self.games = games
        self.search_term = search_term.lower().strip() if search_term else ""
        self.tree_criteria = tree_criteria
        self.library_filter = library_filter  # Frozen set of hidden library names
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

            # Apply library filter
            # Games outside every library (library_id "") and web games are always included
            if game.library_id and game.library_id in self.library_filter:
                continue

            # Apply tree filter
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
        # The no-op listener and installer_ui=None stand in until the install progress UI exists (Task 10)
        self.service = InstallService(self.library_manager, _NoQueueListener(), installer_ui=None)
        self._index_fetching = False  # a catalog index fetch is running or waiting for a dialog to close
        self.statuses = {}  # (library_id, launch_path) -> Status text, filled by refresh_statuses()
        self._status_generation = 0  # only the newest status computation is shown
        # Before the list is first filled: using the saved index may adopt catalog launch paths
        self.service.load_cached_index()
        self.filtered_games = []
        self.filter_worker = None  # Background filtering thread
        self._tree_cache = None  # Cache for tree categories
        self._games_hash = None  # Hash to detect when games list changes
        self.dialog_active = False  # Flag to block spurious events when modal dialogs are open
        self._deferred = []  # callables waiting for every modal dialog to close (run_when_no_dialog)
        self._deferred_retry = False  # a wx.CallLater retry of _run_deferred is pending
        self.restoring_tree = False  # Flag to block saves during tree restoration
        self.initializing = True  # Flag to prevent focus stealing during startup
        self.current_tree_filters = ["platform", "genre", "developer", "year"]  # Track active tree filters
        # Libraries hidden via View > Libraries. Storing hidden (not shown) names means
        # newly added libraries are visible by default and stale names are harmless.
        self.hidden_libraries = self._load_hidden_libraries()
        self.library_menu_items = []  # (library name, menu item) pairs in the Libraries submenu

        # Set up UI
        self.init_ui()
        
        # Load saved state
        self.load_saved_state()
        
        # Initial library check
        self.check_libraries()
        
        # Update title
        self.update_title()

        # Status column, then the catalog index from the network (the saved copy is already in use)
        self.refresh_statuses()
        self.start_index_fetch()
        
        # Bind close event
        self.Bind(wx.EVT_CLOSE, self.on_close)
    
    def init_ui(self):
        """Initialize the user interface"""
        # Notebook: page 0 Library (the player's games), page 1 Catalog
        self.notebook = wx.Notebook(self)
        self.library_page = wx.Panel(self.notebook)
        self.catalog_panel = CatalogPanel(self.notebook, self, self.service)
        self.catalog_page = self.catalog_panel
        self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.on_tab_changed)
        panel = self.library_page
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Menu bar
        self.create_menu_bar()
        
        # Library tab order: search, game list, tree, Launch, description
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
        self.game_list = GameListCtrl(splitter, self.library_manager, self.status_lookup)
        self.game_list.SetLabel("Games")
        self.game_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_game_selected)
        self.game_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_game_activated)
        # Context menu: right-click handled by list event, keyboard shortcuts in on_list_key
        self.game_list.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self.on_list_context)
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

        tree_sizer.Add(self.tree_ctrl, 1, wx.EXPAND)
        tree_panel.SetSizer(tree_sizer)

        # Set up splitter - use exposed control property
        splitter.SplitVertically(self.game_list.control, tree_panel)
        splitter.SetSashGravity(0.5)
        
        main_sizer.Add(splitter, 1, wx.EXPAND)
        
        # Launch button
        self.launch_btn = wx.Button(panel, label="Launch")
        self.launch_btn.Bind(wx.EVT_BUTTON, self.on_launch)
        main_sizer.Add(self.launch_btn, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        
        # Description of the selected catalog game (empty for other games)
        self.description = DescriptionField(panel, "Description")
        self.description.SetMinSize((-1, 120))
        main_sizer.Add(self.description.label_ctrl, 0, wx.LEFT | wx.RIGHT, 5)
        main_sizer.Add(self.description, 0, wx.EXPAND | wx.ALL, 5)
        
        panel.SetSizer(main_sizer)

        self.notebook.AddPage(self.library_page, "Library")
        self.notebook.AddPage(self.catalog_page, "Catalog")
        
        # Store splitter reference
        self.splitter = splitter
        
        # Set up keyboard shortcuts
        self.setup_accelerators()

        # Note: Tree will be built after loading saved state
        # to use the saved filter configuration

        # Populate initial game list
        self.refresh_game_list()
    
    def create_menu_bar(self):
        """Create the menu bar"""
        menu_bar = wx.MenuBar()

        # File menu
        file_menu = wx.Menu()
        add_game_item = file_menu.Append(wx.ID_ANY, "&Add Game\tCtrl+N")
        self.Bind(wx.EVT_MENU, self.on_add_game, add_game_item)

        refresh_item = file_menu.Append(wx.ID_ANY, "&Refresh\tF5")
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
        # Kept so they can be disabled while the Catalog tab is active
        self.edit_game_item = edit_menu.Append(wx.ID_ANY, "&Edit Game\tCtrl+E")
        self.Bind(wx.EVT_MENU, self.on_edit_game, self.edit_game_item)

        self.delete_game_item = edit_menu.Append(wx.ID_ANY, "&Delete Game\tDel")
        self.Bind(wx.EVT_MENU, self.on_delete_game, self.delete_game_item)

        edit_menu.AppendSeparator()

        # Use Ctrl for both platforms (wxPython maps to Cmd on macOS automatically)
        pref_item = edit_menu.Append(wx.ID_PREFERENCES, "&Preferences\tCtrl+,")
        self.Bind(wx.EVT_MENU, self.on_preferences, pref_item)
        menu_bar.Append(edit_menu, "&Edit")

        # View menu
        view_menu = wx.Menu()

        # Filter Tree submenu
        filter_tree_menu = wx.Menu()

        # Store menu items as instance variables for state updates
        self.filter_platform_item = filter_tree_menu.AppendCheckItem(wx.ID_ANY, "&Platform")
        self.Bind(wx.EVT_MENU, self.on_toggle_filter_platform, self.filter_platform_item)

        self.filter_genre_item = filter_tree_menu.AppendCheckItem(wx.ID_ANY, "&Genre")
        self.Bind(wx.EVT_MENU, self.on_toggle_filter_genre, self.filter_genre_item)

        self.filter_developer_item = filter_tree_menu.AppendCheckItem(wx.ID_ANY, "&Developer")
        self.Bind(wx.EVT_MENU, self.on_toggle_filter_developer, self.filter_developer_item)

        self.filter_year_item = filter_tree_menu.AppendCheckItem(wx.ID_ANY, "&Year")
        self.Bind(wx.EVT_MENU, self.on_toggle_filter_year, self.filter_year_item)

        view_menu.AppendSubMenu(filter_tree_menu, "&Filter Tree")

        # Libraries submenu (populated in load_saved_state)
        self.libraries_menu = wx.Menu()
        view_menu.AppendSubMenu(self.libraries_menu, "&Libraries")

        menu_bar.Append(view_menu, "&View")

        # Launch menu
        launch_menu = wx.Menu()
        random_game_item = launch_menu.Append(wx.ID_ANY, "&Random Game\tCtrl+R")
        self.Bind(wx.EVT_MENU, self.on_random_game, random_game_item)
        menu_bar.Append(launch_menu, "&Launch")

        self.SetMenuBar(menu_bar)
    
    def setup_accelerators(self):
        """Set up keyboard accelerators"""
        accel_entries = []

        # Ctrl+F for search
        search_id = wx.NewIdRef()
        accel_entries.append((wx.ACCEL_CTRL, ord('F'), search_id))
        self.Bind(wx.EVT_MENU, self.on_find, id=search_id)

        # Ctrl+N for new game
        new_game_id = wx.NewIdRef()
        accel_entries.append((wx.ACCEL_CTRL, ord('N'), new_game_id))
        self.Bind(wx.EVT_MENU, self.on_add_game, id=new_game_id)

        # Ctrl+Enter for open folder
        open_folder_id = wx.NewIdRef()
        accel_entries.append((wx.ACCEL_CTRL, wx.WXK_RETURN, open_folder_id))
        self.Bind(wx.EVT_MENU, self.on_open_folder, id=open_folder_id)

        # F5 for refresh
        refresh_id = wx.NewIdRef()
        accel_entries.append((wx.ACCEL_NORMAL, wx.WXK_F5, refresh_id))
        self.Bind(wx.EVT_MENU, self.on_refresh, id=refresh_id)

        # Ctrl+R for random game
        random_game_id = wx.NewIdRef()
        accel_entries.append((wx.ACCEL_CTRL, ord('R'), random_game_id))
        self.Bind(wx.EVT_MENU, self.on_random_game, id=random_game_id)

        accel_table = wx.AcceleratorTable(accel_entries)
        self.SetAcceleratorTable(accel_table)
    
    def active_tab(self):
        """The notebook page showing now, as a TABS name ("library" or "catalog")"""
        return TABS[self.notebook.GetSelection()]

    def on_tab_changed(self, event):
        """Remember the tab, enable its menu items, and put focus in its games list"""
        event.Skip()
        if event.GetEventObject() is not self.notebook:  # a book control inside a page
            return
        self.library_manager.config["SavedState"]["last_tab"] = self.active_tab()
        self.library_manager.save_config()
        self.update_tab_menus()
        if self.active_tab() == "catalog":
            self.catalog_panel.refresh_rows()  # Library changes may have changed what is installed
        wx.CallAfter(self.focus_active_list)

    def update_tab_menus(self):
        """Edit Game and Delete Game act on the Library list only (disabling them also stops Ctrl+E and Del)"""
        on_library = self.active_tab() == "library"
        self.edit_game_item.Enable(on_library)
        self.delete_game_item.Enable(on_library)

    def focus_active_list(self):
        """Move keyboard focus to the active tab's games list (screen reader pattern: Select, Focus, SetFocus)"""
        if not self:  # the window closed before this ran
            return
        if self.active_tab() == "library":
            index = self.game_list.GetFirstSelected()
            if index < 0 and self.filtered_games:
                index = 0
            if index >= 0:
                self.game_list.Select(index)
                self.game_list.Focus(index)
            self.game_list.SetFocus()
        else:
            self.catalog_panel.focus_list()

    def on_find(self, event):
        """Ctrl+F: the Library search box"""
        if self.active_tab() == "library":
            self.search_combo.SetFocus()

    def status_lookup(self, game):
        """The Status column's text for a game"""
        return self.statuses.get((game.library_id, game.launch_path), "")

    def refresh_statuses(self):
        """Work out every game's Status in the background (statuses() only reads), then show it"""
        self._status_generation += 1
        generation = self._status_generation
        service = self.service

        def work():
            try:
                statuses = service.statuses()
            except Exception:
                log.warning("Couldn't work out the games' status", exc_info=True)
                return
            _call_after(self._statuses_ready, generation, statuses)

        threading.Thread(target=work, name="library-status", daemon=True).start()

    def _statuses_ready(self, generation, statuses):
        if not self or generation != self._status_generation:  # closed, or a newer result is coming
            return
        self.statuses = statuses
        self.game_list.refresh_rows()

    def start_index_fetch(self):
        """Fetch the catalog index on a daemon thread (startup, F5 and Retry on the Catalog tab).
        Only fetch_index() may run off the UI thread. Does nothing while a fetch is already under way."""
        if self._index_fetching:
            return
        self._index_fetching = True
        self.catalog_panel.set_checking(True)
        service = self.service

        def work():
            try:
                state = service.fetch_index()
            except Exception:
                log.warning("Couldn't fetch the catalog index", exc_info=True)
                _call_after(self._index_fetch_failed)
                return
            _call_after(self._index_fetched, state)

        threading.Thread(target=work, name="catalog-index", daemon=True).start()

    def _index_fetch_failed(self):
        if not self:
            return
        self._index_fetching = False
        self.catalog_panel.set_checking(False)

    def _index_fetched(self, state):
        """A fetched index arrived (UI thread). Using it may adopt catalog launch paths, which replaces
        manager.games and saves games.json, so it waits until no dialog holds a game (run_when_no_dialog)."""
        if not self:
            return
        self.run_when_no_dialog(self._take_index, state)

    def _take_index(self, state):
        taken = False
        try:
            games_before = self.library_manager.games
            self.service.take_index(state)
            taken = True
            if self.library_manager.games is not games_before:  # a game was adopted (the list is replaced)
                self.refresh_game_list()
                self.build_tree(force_rebuild=True)
            self.refresh_statuses()
            self.show_description(self.game_list.get_selected_game())
        except Exception:
            log.exception("Couldn't use the fetched catalog index")
        finally:
            self._index_fetching = False
        try:
            if taken:
                self.catalog_panel.set_index_state(state)
        except Exception:
            log.exception("Couldn't show the fetched catalog index")
        finally:
            self.catalog_panel.set_checking(False)  # "Checking for catalog updates" never outlives the fetch

    def show_description(self, game):
        """The Library description: the catalog text for a catalog game, empty otherwise"""
        self.description.set_text(description_text(self.service.entry_for(game)) if game else "")

    # ---- work that must wait for modal dialogs ----------------------------------------------------------------

    @contextmanager
    def modal_dialog(self):
        """Wrap every modal dialog that can read or change games (sets dialog_active; nests).

        wx runs CallAfter events inside a modal loop; while this is active, run_when_no_dialog queues them.
        When the outermost one closes, the queue runs (after the current handler returns)."""
        was_active = self.dialog_active
        self.dialog_active = True
        try:
            yield
        finally:
            self.dialog_active = was_active
            if not was_active:
                wx.CallAfter(self._run_deferred)

    def _modal_open(self):
        if self.dialog_active:
            return True
        return any(isinstance(w, wx.Dialog) and w.IsModal() for w in wx.GetTopLevelWindows())

    def run_when_no_dialog(self, fn, *args):
        """Run fn(*args) now on the UI thread if no modal dialog is open, else once the last one closes.

        UI thread only. For work that changes manager.games, games.json or records at a moment the player
        didn't choose (index refreshes; Task 10: the queue's apply_result and job_failed), so an open Edit or
        Delete dialog never holds a game object that was replaced under it. Queued calls run in order."""
        self._deferred.append((fn, args))
        self._run_deferred()

    def _run_deferred(self):
        if not self:
            return
        while self._deferred:
            if self._modal_open():
                # modal_dialog() runs the queue when it closes; a dialog it doesn't wrap is polled for
                if not self.dialog_active and not self._deferred_retry:
                    self._deferred_retry = True
                    wx.CallLater(250, self._retry_deferred)
                return
            fn, args = self._deferred.pop(0)
            try:
                fn(*args)
            except Exception:  # one failure must not keep the rest from running
                log.exception("Deferred %s failed", getattr(fn, "__name__", fn))

    def _retry_deferred(self):
        if not self:
            return
        self._deferred_retry = False
        self._run_deferred()

    def _live_game(self, game):
        """The library's current object for a game shown in the list, or None if it is gone.

        An index refresh can replace a catalog game's object before the list is refilled."""
        if game is None:
            return None
        games = self.library_manager.games
        if any(g is game for g in games):
            return game
        for g in games:
            if game.catalog_id and g.catalog_id == game.catalog_id:
                return g
        for g in games:
            if (g.library_id, g.launch_path) == (game.library_id, game.launch_path):
                return g
        return None

    def check_libraries(self):
        """Check if first run and show setup dialog"""
        if self.library_manager.is_first_run:
            dlg = FirstTimeSetupDialog(self, self.library_manager)
            with self.modal_dialog():
                dlg.ShowModal()
            dlg.Destroy()

            # Refresh UI in case they added stuff
            self.refresh_game_list()
            self.build_tree(self.current_tree_filters, force_rebuild=True)
            self.build_libraries_menu()

        # Always continue to main window
        self.initializing = False
        self.focus_active_list()
    
    def build_tree(self, filters=None, force_rebuild=False, restore_selections=True):
        """Build the tree control hierarchy with flat 2-level structure using cache"""
        # Default to the user's current filter levels so refreshes don't reset them
        if filters is None:
            filters = self.current_tree_filters

        # Store the current filters for dialog state
        self.current_tree_filters = list(filters)
        filters = self.current_tree_filters

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

            # Collect every category regardless of active filters, so the cache
            # stays valid when the user turns a filter level back on
            for game in self.library_manager.games:
                for platform in game.platforms:
                    categories["platform"].add(platform)
                categories["genre"].add(game.genre or "Unknown Genre")
                categories["developer"].add(game.developer or "Unknown Developer")
                categories["year"].add(game.year or "Unknown Year")

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

                # Add all values under this category (case-insensitive sort)
                for value in sorted(categories[category_key], key=str.lower):
                    self.tree_ctrl.AppendItem(category_node, value)

        # Expand only the root to show categories, but keep all categories collapsed
        self.tree_ctrl.Expand(root)

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
            frozenset(self.hidden_libraries),
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
        else:
            self.show_description(None)
    
    def notify(self, text: str):
        """Tell the player something without a dialog (a Windows notification)"""
        try:
            wx.adv.NotificationMessage("Game Chooser", text).Show()
        except Exception:
            log.warning("Couldn't show a notification", exc_info=True)

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
        self.show_description(game)
        if game:
            self.launch_btn.SetLabel(f"Launch {game.title}")

            # Save selected game
            self.library_manager.config["SavedState"]["last_selected"] = game.title
            self.library_manager.save_config()
    
    def on_game_activated(self, event):
        """Handle double-click on game"""
        self.on_launch(None)
    
    def on_list_context(self, event):
        """Show context menu for list (supports both mouse and keyboard)"""
        menu = wx.Menu()

        game = self.game_list.get_selected_game()
        if game:
            launch_item = menu.Append(wx.ID_ANY, "Launch")
            self.Bind(wx.EVT_MENU, self.on_launch, launch_item)

            edit_item = menu.Append(wx.ID_ANY, "Edit")
            self.Bind(wx.EVT_MENU, self.on_edit_game, edit_item)

            open_folder_item = menu.Append(wx.ID_ANY, "Open folder")
            self.Bind(wx.EVT_MENU, self.on_open_folder, open_folder_item)

            delete_item = menu.Append(wx.ID_ANY, "Delete")
            self.Bind(wx.EVT_MENU, self.on_delete_game, delete_item)

            menu.AppendSeparator()

        add_game_item = menu.Append(wx.ID_ANY, "Add Game")
        self.Bind(wx.EVT_MENU, self.on_add_game, add_game_item)

        # Determine if this is a mouse event or keyboard event
        if hasattr(event, 'GetEventType') and event.GetEventType() == wx.wxEVT_LIST_ITEM_RIGHT_CLICK:
            # Mouse right-click: show at default position (wxPython positions automatically)
            self.PopupMenu(menu)
        else:
            # Keyboard event (Shift+F10, context menu key): show at selected item
            # Get selected item position
            selected_index = self.game_list.GetFirstSelected()
            if selected_index >= 0:
                # Get item rectangle to position menu near selected item
                rect = self.game_list.control.GetItemRect(selected_index)
                pos = wx.Point(rect.x + 10, rect.y + rect.height // 2)
                self.game_list.control.PopupMenu(menu, pos)
            else:
                # No selection, show at default position
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
        elif key == wx.WXK_WINDOWS_MENU or (key == wx.WXK_F10 and event.ShiftDown()):
            # Applications/context menu key or Shift+F10
            self.on_list_context(event)
        else:
            event.Skip()

    def on_open_folder(self, event):
        """Open the game's folder in the file manager"""
        if self.active_tab() != "library":
            return
        game = self.game_list.get_selected_game()
        if not game:
            return

        # Skip web games - no local folder to open
        if game.launch_path.startswith("http"):
            return

        # Get full path to game
        full_path = self.library_manager.get_full_path(game)
        if not full_path or not Path(full_path).exists():
            wx.MessageBox("Game folder not found.",
                        "Folder Not Found", wx.OK | wx.ICON_ERROR)
            return

        # Get the game directory
        game_dir = str(Path(full_path).parent)

        # Open folder with platform-specific command
        try:
            system = platform.system()
            if system == "Windows":
                # Open folder and highlight the executable
                subprocess.Popen(['explorer', '/select,', full_path])
            elif system == "Darwin":
                # macOS - just open the folder
                subprocess.Popen(['open', game_dir])
        except Exception as e:
            wx.MessageBox(f"Failed to open folder: {e}",
                        "Error", wx.OK | wx.ICON_ERROR)

    def launch_game(self, game):
        """Launch a specific game"""
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
                with self.modal_dialog():
                    wx.MessageBox("Game executable not found. Please locate the game.",
                                "File Not Found", wx.OK | wx.ICON_ERROR)

                    # File dialog to relocate
                    dlg = wx.FileDialog(self, "Locate Game Executable")
                    if dlg.ShowModal() == wx.ID_OK:
                        new_path = dlg.GetPath()

                        # Any path on disk will do; it is no longer tied to a library
                        live = self._live_game(game)
                        if live is not None:
                            live.launch_path = new_path
                            live.library_id = ""
                            self.library_manager.save_games()
                            self.refresh_statuses()
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

    def on_launch(self, event):
        """Launch selected game"""
        game = self.game_list.get_selected_game()
        if not game:
            return

        self.launch_game(game)

    def on_random_game(self, event):
        """Pick a random game from filtered list and offer to launch it"""
        if self.active_tab() == "catalog":
            self.catalog_panel.on_random_game()
            return
        if not self.filtered_games:
            wx.MessageBox("No games available to choose from.",
                        "No Games", wx.OK | wx.ICON_INFORMATION)
            return

        # Filter for platform-compatible games only
        current_system = platform.system()
        platform_map = {
            "Windows": "Windows",
            "Darwin": "macOS",
            "Linux": "Linux"
        }
        current_platform = platform_map.get(current_system, current_system)

        # Get games compatible with current platform (or web games)
        compatible_games = [
            game for game in self.filtered_games
            if game.launch_path.startswith("http") or current_platform in game.platforms
        ]

        if not compatible_games:
            wx.MessageBox(
                f"No games compatible with {current_platform} in the current filtered list.",
                "No Compatible Games",
                wx.OK | wx.ICON_INFORMATION
            )
            return

        # Pick random game
        random_game = random.choice(compatible_games)

        # Show dialog (the game object is held until it launches, so no refresh may replace it meanwhile)
        with self.modal_dialog():
            response = wx.MessageBox(
                f"Your game for today is...\n\n{random_game.title}\n\nLaunch now?",
                "Random Game",
                wx.YES_NO | wx.ICON_QUESTION
            )

            if response == wx.YES:
                self.launch_game(random_game)

    def on_edit_game(self, event):
        """Edit selected game"""
        if self.active_tab() != "library":
            return
        game = self._live_game(self.game_list.get_selected_game())
        if not game:
            return

        dlg = GameDialog(self, self.library_manager, game)

        # Block spurious selection events (and game-changing refreshes) while modal dialog is active
        with self.modal_dialog():
            try:
                if dlg.ShowModal() == wx.ID_OK:
                    self.library_manager.save_games()
                    self.refresh_game_list()
                    self.refresh_statuses()
            finally:
                dlg.Destroy()
    
    def on_delete_game(self, event):
        """Delete selected game"""
        if self.active_tab() != "library":
            return
        game = self.game_list.get_selected_game()
        if not game:
            return

        # Remember the current index position to maintain list position after deletion
        current_index = self.game_list.GetFirstSelected()

        # Show custom delete dialog
        dlg = DeleteGameDialog(self, game.title)

        # Block spurious selection events (and game-changing refreshes) while modal dialog is active.
        # The queued refreshes run after this handler returns, so after the removal below.
        with self.modal_dialog():
            try:
                result = dlg.ShowModal()
            finally:
                dlg.Destroy()

        if result == wx.ID_YES:
            live = self._live_game(game)
            games = self.library_manager.games
            for i, g in enumerate(games):
                if g is live:
                    del games[i]
                    break
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

        # Block spurious selection events (and game-changing refreshes) while modal dialog is active
        with self.modal_dialog():
            try:
                if dlg.ShowModal() == wx.ID_OK:
                    self.library_manager.games.append(dlg.game)
                    self.library_manager.save_games()
                    self.refresh_game_list()
                    self.refresh_statuses()
            finally:
                dlg.Destroy()

    def on_toggle_filter_platform(self, event):
        """Toggle platform filter in tree"""
        self._toggle_filter("platform")

    def on_toggle_filter_genre(self, event):
        """Toggle genre filter in tree"""
        self._toggle_filter("genre")

    def on_toggle_filter_developer(self, event):
        """Toggle developer filter in tree"""
        self._toggle_filter("developer")

    def on_toggle_filter_year(self, event):
        """Toggle year filter in tree"""
        self._toggle_filter("year")

    def _toggle_filter(self, filter_key):
        """Toggle a filter and rebuild tree"""
        if filter_key in self.current_tree_filters:
            self.current_tree_filters.remove(filter_key)
        else:
            self.current_tree_filters.append(filter_key)

        # Rebuild tree with updated filters
        self.build_tree(self.current_tree_filters)

        # Update menu item check states
        self.update_filter_menu_state()

        # Save filter state immediately (prune selections under removed levels)
        self.save_tree_selections()
        self.library_manager.config["SavedState"]["tree_filters"] = list(self.current_tree_filters)
        self.library_manager.save_config()

        # Re-filter: a selection under a removed level no longer applies
        self.apply_filters()

    def update_filter_menu_state(self):
        """Update filter menu item check states based on current filters"""
        self.filter_platform_item.Check("platform" in self.current_tree_filters)
        self.filter_genre_item.Check("genre" in self.current_tree_filters)
        self.filter_developer_item.Check("developer" in self.current_tree_filters)
        self.filter_year_item.Check("year" in self.current_tree_filters)

    def _load_hidden_libraries(self):
        """The ids of the libraries the player has hidden"""
        state = self.library_manager.config["SavedState"]
        state.setdefault("hidden_libraries", [])
        return set(state["hidden_libraries"])

    def build_libraries_menu(self):
        """Build/rebuild the libraries submenu with current libraries"""
        # Clear existing items and their handlers
        for _, item in self.library_menu_items:
            self.Unbind(wx.EVT_MENU, id=item.GetId())
            self.libraries_menu.Delete(item)
        self.library_menu_items = []

        # One item per library, in config order. Two libraries with the same folder name
        # (two folders both named Games) are separate items, told apart by their path.
        for lib in self.library_manager.config["libraries"]:
            library_id = lib["id"]
            label = library_label(lib)
            # Escape & so it isn't treated as a mnemonic marker
            text = f"{label} ({lib['path']})".replace("&", "&&")
            menu_item = self.libraries_menu.AppendCheckItem(wx.ID_ANY, text)
            self.library_menu_items.append((library_id, menu_item))
            self.Bind(wx.EVT_MENU, lambda evt, lid=library_id: self.on_toggle_library(evt, lid), menu_item)
            menu_item.Check(library_id not in self.hidden_libraries)

    def on_toggle_library(self, event, library_id):
        """Toggle library visibility"""
        if library_id in self.hidden_libraries:
            self.hidden_libraries.discard(library_id)
        else:
            self.hidden_libraries.add(library_id)

        # Update menu state
        for item_id, item in self.library_menu_items:
            if item_id == library_id:
                item.Check(library_id not in self.hidden_libraries)

        # Save state
        self.library_manager.config["SavedState"]["hidden_libraries"] = sorted(self.hidden_libraries)
        self.library_manager.save_config()

        # Apply filters
        self.apply_filters()

    def on_preferences(self, event):
        """Show preferences dialog"""
        dlg = PreferencesDialog(self, self.library_manager)

        # Block spurious selection events (and game-changing refreshes) while modal dialog is active
        with self.modal_dialog():
            try:
                dlg.ShowModal()
                # Refresh even on Cancel: Apply may already have saved changes.
                # Deferred to avoid blocking the dialog close.
                wx.CallAfter(self.refresh_ui_after_preferences)
            finally:
                dlg.Destroy()

    def refresh_ui_after_preferences(self):
        """Refresh UI after preferences dialog closes"""
        self.refresh_game_list()
        self.build_tree(force_rebuild=True)
        self.build_libraries_menu()
        self.refresh_statuses()  # a library may have been added, moved or removed
    
    def on_refresh(self, event):
        """F5: on the Catalog tab check for catalog updates; on Library rebuild the game list and tree and
        recheck each game's Status"""
        if self.active_tab() == "catalog":
            self.catalog_panel.check_for_updates()
            return
        self.refresh_game_list()
        self.build_tree(force_rebuild=True)
        self.build_libraries_menu()
        self.refresh_statuses()

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

        # Tree filters (an empty list is a valid saved choice)
        if isinstance(state.get("tree_filters"), list):
            self.current_tree_filters = list(state["tree_filters"])

        # Build tree with saved filters (without restoring selections yet)
        self.build_tree(filters=self.current_tree_filters, restore_selections=False)

        # Update menu item check states based on current filters
        self.update_filter_menu_state()

        # Build libraries menu with current state
        self.build_libraries_menu()

        # Opening tab: the saved one, else Catalog on a first run and Library otherwise.
        # ChangeSelection, not SetSelection: no page-changed event (and no focus move) during startup.
        tab = initial_tab(state.get("last_tab"), self.library_manager.is_first_run)
        self.notebook.ChangeSelection(TABS.index(tab))
        self.update_tab_menus()

        # Re-filter now that all saved state (search, filters, hidden libraries) is loaded
        self.refresh_game_list()

    def save_state(self):
        """Save current window state"""
        state = self.library_manager.config["SavedState"]

        state["window_size"] = list(self.GetSize())
        state["window_position"] = list(self.GetPosition())
        state["splitter_position"] = self.splitter.GetSashPosition()
        state["last_search"] = self.search_combo.GetValue()
        state["last_tab"] = self.active_tab()
        self.catalog_panel.save_state()

        # Save tree filters
        state["tree_filters"] = list(self.current_tree_filters)

        # Save hidden libraries
        state["hidden_libraries"] = sorted(self.hidden_libraries)
        state.pop("active_libraries", None)

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