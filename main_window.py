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
import logging
import subprocess
import platform
import webbrowser
import threading
from contextlib import contextmanager
from pathlib import Path

from smart_list import Column

from library_manager import GameLibraryManager
from install_service import InstallService
from catalog_view import initial_tab, library_rows
from engine.folders import library_label
from engine.launch import LaunchCancelled, launch_kind
from games_panel import GamesPanel
from catalog_panel import CatalogPanel
from dialogs import GameDialog, PreferencesDialog, FirstTimeSetupDialog
from engine import messages
from engine.entries import current_platform as catalog_platform
from engine.records import record_folder
from engine.status import update_available
from install_dialogs import InstallConfirmDialog, UninstallDialog
import library_actions
from library_actions import catalog_menu_items, delete_kind, is_editable, launch_problem, start_launch_file
from wx_bridge import JobOutcomes, WxInstallerUI, WxQueueListener, run_in_background
from wx_bridge import call_after as _call_after  # wx.CallAfter that does nothing once the app is gone

log = logging.getLogger(__name__)

TABS = ("library", "catalog")  # notebook page order
QUIT_WAIT_SECONDS = 10  # how long quitting waits for a cancelled install to stop
# The Library tree's categories, (criteria key, label) in tree order; View > Filter Tree chooses which show
LIBRARY_TREE = [("platform", "Platform"), ("genre", "Genre"), ("developer", "Developer"), ("year", "Release Year")]


class MainFrame(wx.Frame):
    """Main application window"""
    
    def __init__(self):
        super().__init__(None, title="Game Chooser (0)")
        self.launch_observer = None  # optional: before_launch(game) -> token or None (cancel), launched(...)
        self.title_note = ""  # text shown after "Game Chooser" in the title

        self.library_manager = GameLibraryManager()
        # Windows toast notifications; False in a development run, where notify() falls back to a balloon
        try:
            wx.adv.NotificationMessage.MSWUseToasts()
        except Exception:
            log.debug("Toast notifications aren't available", exc_info=True)
        # The queue's events and the engine's installer prompts reach this window on the UI thread only
        self.installer_ui = WxInstallerUI(self)
        self.job_outcomes = JobOutcomes()  # job ends not yet applied to the library (each is applied once)
        self.service = InstallService(self.library_manager, WxQueueListener(self, outcomes=self.job_outcomes),
                                      installer_ui=self.installer_ui)
        self._quitting = False  # on_close is applying the last job ends: no dialogs, notifications or refreshes
        self._shown_job_id = None  # the job the progress areas show
        self._cancelling_job_id = None  # the job the player pressed Cancel on
        self._installer_wait_done = None  # Done's handler while "Waiting for the installer" shows
        self._counting = set()  # catalog ids whose player files are being counted for the Uninstall dialog
        # Called with the Job on the UI thread after each finished, failed or cancelled job has been applied
        # to the library (Preferences can refresh its library list from here)
        self.job_done_hooks = []
        self._index_fetching = False  # a catalog index fetch is running or waiting for a dialog to close
        self._index_refetch = False  # the catalog address changed during a fetch: fetch again after it
        self.statuses = {}  # (library_id, launch_path) -> Status text, filled by refresh_statuses()
        self._status_generation = 0  # only the newest status computation is shown
        # Before the list is first filled: using the saved index may adopt catalog launch paths
        self.service.load_cached_index()
        self.dialog_active = False  # Flag to block spurious events when modal dialogs are open
        self._deferred = []  # callables waiting for every modal dialog to close (run_when_no_dialog)
        self._deferred_retry = False  # a wx.CallLater retry of _run_deferred is pending
        self.initializing = True  # Flag to prevent focus stealing during startup
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

        # A games.json or config.json that couldn't be read: say so once, after the window is up
        if self.library_manager.load_problems:
            wx.CallAfter(self.run_when_no_dialog, self._tell_load_problems)

        # Update title
        self.update_title()

        # Status column, then the catalog index from the network (the saved copy is already in use)
        self.refresh_statuses()
        self.start_index_fetch()
        
        # Bind close event
        self.Bind(wx.EVT_CLOSE, self.on_close)
    
    def init_ui(self):
        """Initialize the user interface"""
        # Notebook: page 0 Library (the player's games), page 1 Catalog. Both tabs are built from GamesPanel;
        # what differs between them is in the settings passed here and in CatalogPanel.
        self.notebook = wx.Notebook(self)
        self.create_menu_bar()

        tree_keys = self.library_manager.config["SavedState"].get("tree_filters")
        if not isinstance(tree_keys, list):  # an empty list is a valid saved choice
            tree_keys = [key for key, _ in LIBRARY_TREE]
        self.library_view = GamesPanel(
            self.notebook, self, name="Games", categories=LIBRARY_TREE, tree_keys=tree_keys, state_prefix="",
            idle_label="Launch",
            columns=[
                Column(title="Title", model_field=lambda r: r.title, width=200),
                Column(title="Genre", model_field=lambda r: r.genre, width=150),
                Column(title="Developer", model_field=lambda r: r.developer, width=150),
                Column(title="Year", model_field=lambda r: r.year, width=80),
                Column(title="Platform", model_field=lambda r: ", ".join(r.platforms), width=150),
                Column(title="Status", model_field=lambda r: self.status_lookup(r.game), width=150),
            ],
            sort_keys=[  # one per column
                lambda r: r.title.lower(),
                lambda r: r.genre.lower(),
                lambda r: r.developer.lower(),
                lambda r: r.year,
                lambda r: len(r.platforms),
                lambda r: self.status_lookup(r.game).lower(),
            ],
            # View > Libraries: a hidden library's games leave the list. Games outside every library
            # (library_id "") and web games always show.
            row_filter=lambda r: r.game.library_id not in self.hidden_libraries,
            on_action=lambda: self.on_launch(None),
            context_menu=lambda row: self.build_list_menu(row.game if row else None),
            on_key=self.on_list_key, on_shown=self.update_tab_menus)
        self.catalog_panel = CatalogPanel(self.notebook, self, self.service)
        self.views = {"library": self.library_view, "catalog": self.catalog_panel.view}  # by TABS name
        for view in self.views.values():
            view.progress_area.on_cancel = self.cancel_current_job
        self.notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.on_tab_changed)
        self.notebook.AddPage(self.library_view, "Library")
        self.notebook.AddPage(self.catalog_panel, "Catalog")

        # Set up keyboard shortcuts
        self.setup_accelerators()

        # Fill the Library list (the view has read its saved search and sort itself)
        self._refresh_library_rows()

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

        self.filter_items = {}  # criteria key -> check item, for state updates
        for key, label in LIBRARY_TREE:
            item = filter_tree_menu.AppendCheckItem(wx.ID_ANY, "&" + label.replace("Release ", ""))  # menu: "Year"
            self.Bind(wx.EVT_MENU, lambda event, k=key: self._toggle_filter(k), item)
            self.filter_items[key] = item
        view_menu.AppendSubMenu(filter_tree_menu, "&Filter Tree")

        # Libraries submenu (populated in load_saved_state)
        self.libraries_menu = wx.Menu()
        view_menu.AppendSubMenu(self.libraries_menu, "&Libraries")

        menu_bar.Append(view_menu, "&View")

        # Launch menu
        launch_menu = wx.Menu()
        random_game_item = launch_menu.Append(wx.ID_ANY, "&Random Game\tCtrl+R")
        self.Bind(wx.EVT_MENU, self.on_random_game, random_game_item)
        random_install_item = launch_menu.Append(wx.ID_ANY, "Random Game to &Install\tCtrl+Shift+R")
        self.Bind(wx.EVT_MENU, self.on_random_install, random_install_item)
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

        # Ctrl+Shift+R for a random game to install from the catalog
        random_install_id = wx.NewIdRef()
        accel_entries.append((wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('R'), random_install_id))
        self.Bind(wx.EVT_MENU, self.on_random_install, id=random_install_id)

        # Ctrl+E for Edit Game, also while the menu item is disabled for a catalog game: it then says why
        edit_game_id = wx.NewIdRef()
        accel_entries.append((wx.ACCEL_CTRL, ord('E'), edit_game_id))
        self.Bind(wx.EVT_MENU, self.on_edit_game, id=edit_game_id)

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
        self.views[self.active_tab()].save_config()  # a save that fails is logged; the menus and focus still follow
        self.update_tab_menus()
        wx.CallAfter(self.focus_active_list)

    def update_tab_menus(self):
        """Edit Game and Delete Game act on the Library list only (disabling them also stops their menu shortcuts).
        Edit Game is also unavailable while a catalog game is selected; Ctrl+E then still says why (on_edit_game),
        through its own accelerator."""
        on_library = self.active_tab() == "library"
        game = self.selected_game() if on_library else None
        self.edit_game_item.Enable(on_library and (game is None or is_editable(game)))
        self.delete_game_item.Enable(on_library)

    def focus_active_list(self):
        """Move keyboard focus to the active tab's games list (screen reader pattern: select, focused item, SetFocus).
        Not while the app is starting up or a dialog is open (GamesPanel.focus_list)."""
        if not self:  # the window closed before this ran
            return
        self.views[self.active_tab()].focus_list()

    def selected_game(self):
        """The game selected in the Library list, or None"""
        row = self.library_view.selected_row()
        return row.game if row else None

    def on_find(self, event):
        """Ctrl+F: the active tab's search box"""
        self.views[self.active_tab()].search_combo.SetFocus()

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
        self.library_view.refill()  # the Status column; a list sorted by it is sorted again

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

    def refetch_index(self):
        """The catalog address changed: fetch again from the new one. A fetch already under way used the old
        address, so another follows it."""
        if self._index_fetching:
            self._index_refetch = True
        else:
            self.start_index_fetch()

    def _run_pending_refetch(self):
        if self._index_refetch and not self._index_fetching:
            self._index_refetch = False
            self.start_index_fetch()

    def _index_fetch_failed(self):
        if not self:
            return
        self._index_fetching = False
        self.catalog_panel.set_checking(False)
        self._run_pending_refetch()

    def _index_fetched(self, state):
        """A fetched index arrived (UI thread). Using it may adopt catalog launch paths, which replaces
        manager.games and saves games.json, so it waits until no dialog holds a game (run_when_no_dialog)."""
        if not self:
            return
        self.run_when_no_dialog(self._take_index, state)

    def _take_index(self, state):
        taken = False
        try:
            self.service.take_index(state)
            taken = True
        except Exception:
            log.exception("Couldn't use the fetched catalog index")
        finally:
            self._index_fetching = False
        try:
            if taken:
                # Always: the index may have replaced games (an adopted launch path), changed a genre, or changed
                # what a game's description says
                self.games_changed()
                self.catalog_panel.set_index_state(state)
        except Exception:
            log.exception("Couldn't show the fetched catalog index")
        finally:
            self.catalog_panel.set_checking(False)  # "Checking for catalog updates" never outlives the fetch
        self._run_pending_refetch()

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
        """True while a modal dialog that may hold a game is open. A dialog marked `holds_no_games`
        (Preferences, which holds library ids only) doesn't count; the prompts it opens do."""
        if self.dialog_active:
            return True
        return any(isinstance(w, wx.Dialog) and w.IsModal() and not getattr(w, "holds_no_games", False)
                   for w in wx.GetTopLevelWindows())

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
            with self.modal_dialog(), FirstTimeSetupDialog(self, self.library_manager) as dlg:
                dlg.ShowModal()

            # Refresh UI in case they added stuff
            self.games_changed()

        # Always continue to main window
        self.initializing = False
        self.focus_active_list()
    
    def notify(self, text: str):
        """Tell the player something without a dialog (a Windows notification)"""
        try:
            wx.adv.NotificationMessage("Game Chooser", text).Show()
        except Exception:
            log.warning("Couldn't show a notification", exc_info=True)

    # ---- background installs (WxQueueListener and WxInstallerUI call these on the UI thread) -------------------

    @property
    def progress_areas(self):
        """Both progress areas (each beside its tab's action button). They show the
        same job, whatever is selected."""
        return [view.progress_area for view in self.views.values()]

    def on_job_started(self, job):
        if self:
            self._sync_progress()

    def on_queue_changed(self, waiting):
        if self:
            self._sync_progress()

    def on_job_progress(self, job, progress):
        """Progress only changes the areas' gauge and status, never focus or games, so it applies at once"""
        if not self:
            return
        self._sync_progress()
        if self.service.queue.current() is not job or job.id == self._cancelling_job_id:
            return  # a late report from a job that has ended, or one being cancelled (it says so)
        waiting = self.service.queue.waiting_count()
        for area in self.progress_areas:
            area.update(progress, waiting)

    def on_job_finished(self, job, result):
        self._job_ended("finished", job, result)

    def on_job_failed(self, job, message):
        self._job_ended("failed", job, message)

    def on_job_cancelled(self, job):
        self._job_ended("cancelled", job)

    def _job_ended(self, kind, job, detail=None):
        if not self:
            return
        if self._cancelling_job_id == job.id:
            self._cancelling_job_id = None
        self._sync_progress()
        self.run_when_no_dialog(self._apply_outcome, kind, job, detail)

    def _sync_progress(self):
        """Show the progress areas while a job runs or waits (and the queue's current job in them); hide them
        once the queue is empty. The queue itself is the truth: its events can arrive after it has moved on."""
        self.installer_ui.check_wait()
        queue = self.service.queue
        job = queue.current()
        waiting = queue.waiting_count()
        if job is not None and job.id != self._shown_job_id:
            self._shown_job_id = job.id
            for area in self.progress_areas:
                area.show_job(job.title)
                if job.id == self._cancelling_job_id:
                    area.set_label(messages.JOB_CANCELLING.format(title=job.title))
            # the installer's prompt can arrive before job_started (they come from different threads)
            if self._installer_wait_done is not None and self.installer_ui.waiting():
                self.show_installer_wait(self._installer_wait_done)
        for area in self.progress_areas:
            area.set_waiting(waiting)
        self._show_progress(job is not None or waiting > 0)

    def _show_progress(self, active):
        for view in self.views.values():
            view.set_busy(active)  # never leaves focus on a control that just hid
        if not active:
            self._shown_job_id = None

    def show_installer_wait(self, on_done):
        """WxInstallerUI: "Waiting for the installer", with Done, in both areas (focus stays where it is)"""
        self._installer_wait_done = on_done
        for area in self.progress_areas:
            area.set_label(messages.INSTALLER_WAITING)
            area.show_done_button(on_done)

    def hide_installer_wait(self):
        self._installer_wait_done = None
        for area in self.progress_areas:
            area.hide_done_button()

    def cancel_current_job(self):
        """Cancel, in either progress area: stop the running job (an installer wait answers False first)"""
        queue = self.service.queue
        job = queue.current()
        if job is None:
            return
        self._cancelling_job_id = job.id
        self.installer_ui.cancel_wait()
        queue.cancel(job.id)
        for area in self.progress_areas:
            area.hide_done_button()
            area.set_label(messages.JOB_CANCELLING.format(title=job.title))

    def _apply_outcome(self, kind, job, detail):
        """A job's end ("finished", "failed" or "cancelled") reaches the library, then the player hears about it.
        Runs only while no dialog is open (run_when_no_dialog), so no dialog holds a game this replaces."""
        if self.job_outcomes.take(job):  # not already applied while quitting
            self._outcome(kind, job, detail)

    def _outcome(self, kind, job, detail):
        """A cancelled job is a failed one to the service (an uninstall's library stays), without the message"""
        try:
            if kind == "finished":
                self.service.apply_result(detail)
            else:
                self.service.job_failed(job)
        except Exception:
            log.exception("Couldn't apply how %s ended (%s)", job.title, kind)
        if self._quitting:
            return
        self.games_changed()
        if kind == "finished":
            self.notify(messages.finished_text(detail.action, detail.title or job.title, detail.notes,
                                               detail.backups))
        elif kind == "failed":
            self.notify(messages.failed_text(job.request.action, job.title))
            self._tell(detail, messages.INSTALL_PROBLEM_TITLE)  # a second failure waits for this box, never stacks
        self._run_job_done_hooks(job)

    def _apply_outcomes_on_quit(self):
        """Quitting: bring the library up to date with every job end whose UI event hasn't been applied, quietly.
        First what waits for a dialog (a job that ended during the quit question), then the ends still in wx's
        queue or never posted (a job that ended during the shutdown wait; Destroy drops those events)."""
        self._run_deferred()
        for kind, job, detail in self.job_outcomes.drain():
            try:
                self._outcome(kind, job, detail)
            except Exception:
                log.exception("Couldn't apply how %s ended while quitting", job.title)

    def games_changed(self):
        """A game, the games list, a library or what is installed changed, or what the catalog says about a game:
        bring everything that shows them up to date. Both tabs' rows (each view then brings its tree up to date
        and keeps its selection), the Libraries menu and the statuses. Every such change calls this, whoever
        made it. A step that fails is logged and the others still run.

        Focus stays where it is, with one exception that is the view's: when a list becomes empty while focus is
        on its action button, focus goes to that list (the button is about to be disabled)."""
        for refresh in (self._refresh_library_rows, self.build_libraries_menu, self.catalog_panel.refresh_rows,
                        self.refresh_statuses):
            try:
                refresh()
            except Exception:
                log.exception("Couldn't refresh after the games changed")

    def _save_games(self):
        """The player changed a game (Add, Edit, Delete, Locate): write games.json, then show the change"""
        self.library_manager.save_games()
        self.games_changed()

    def _run_job_done_hooks(self, job):
        for hook in list(self.job_done_hooks):
            try:
                hook(job)
            except Exception:
                log.exception("A job-done hook failed")

    def _refresh_library_rows(self):
        """The Library's part of games_changed(): rebuild its rows from the games and their catalog entries.
        The view brings its tree up to date, filters again and keeps the selected game selected."""
        self.library_view.set_rows(library_rows(self.library_manager.games, self.service.entry_for))
        self.update_title()

    def build_list_menu(self, game):
        """The games list's context menu for a game (or None): Launch, Edit (games added by hand only), Open folder,
        Delete, then a catalog game's actions and links (library_actions decides which), then Add Game.
        Handlers are bound on the menu, so nothing piles up on the frame."""
        menu = wx.Menu()
        if game:
            for label, handler in [("Launch", self.on_launch),
                                   ("Edit", self.on_edit_game) if is_editable(game) else (None, None),
                                   ("Open folder", self.on_open_folder), ("Delete", self.on_delete_game)]:
                if label:
                    item = menu.Append(wx.ID_ANY, label)
                    menu.Bind(wx.EVT_MENU, handler, item)
            for action in self._catalog_menu_items(game):
                item = menu.Append(wx.ID_ANY, action.label.replace("&", "&&"))
                item.Enable(action.enabled)
                menu.Bind(wx.EVT_MENU, lambda e, a=action, g=game: self.on_catalog_action(a, g), item)
            menu.AppendSeparator()

        add_game_item = menu.Append(wx.ID_ANY, "Add Game")
        menu.Bind(wx.EVT_MENU, self.on_add_game, add_game_item)
        return menu

    def _catalog_menu_items(self, game):
        if not game.catalog_id:
            return []
        record = self.service.record_for(game)
        entry = self.service.entry_for(game)
        extras = False
        if entry is not None and library_actions.is_tracked(record):
            try:
                extras = bool(self.service.plan(game.catalog_id, "extras").extras)
            except Exception:
                log.warning("Couldn't list the extras of %s", game.catalog_id, exc_info=True)
        return catalog_menu_items(game, record, entry, update_available(record, entry, catalog_platform()), extras)

    def on_list_key(self, event) -> bool:
        """The Library list's own keys (the view handles Enter, Space and the context menu keys); True for a key
        that was taken"""
        key = event.GetKeyCode()
        if key == ord('E') and event.ControlDown() and not event.AltDown():  # with Alt it is AltGr+E, a letter
            self.on_edit_game(None)
        elif key == wx.WXK_DELETE:
            self.on_delete_game(None)
        else:
            return False
        return True

    def on_open_folder(self, event):
        """Open the game's folder in the file manager"""
        if self.active_tab() != "library":
            return
        game = self.selected_game()
        if not game:
            return

        # Skip web games - no local folder to open
        if game.is_web:
            return

        # Get full path to game
        full_path = self.library_manager.get_full_path(game)
        if not full_path or not Path(full_path).exists():
            self._tell("Game folder not found.", "Folder Not Found")
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
            self._tell(f"Failed to open folder: {e}", "Error")

    def launch_game(self, game):
        """Launch a specific game"""
        # Check platform compatibility for non-web games
        if not game.is_web:
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

                self._tell(f"You're trying to run {game.title} on {current_platform}, but it only supports "
                           f"{supported}.", "Uh-oh")
                return

        if game.is_web:
            # Web game
            webbrowser.open(game.launch_path)
        else:
            # Regular game
            full_path = self.library_manager.get_full_path(game)
            if not full_path or not Path(full_path).exists():
                problem = launch_problem(game, self.library_manager.library_reachable(game.library_id),
                                         self.service.record_for(game), self.service.entry_for(game))
                if problem != "locate":
                    self._launch_problem(game, problem)
                    return
                with self.modal_dialog():
                    self._tell(messages.LAUNCH_NOT_FOUND_LOCATE, messages.LAUNCH_NOT_FOUND_TITLE)
                    with wx.FileDialog(self, "Locate Game Executable") as dlg:
                        located = dlg.ShowModal() == wx.ID_OK
                        new_path = dlg.GetPath()
                    # Any path on disk will do; it is no longer tied to a library
                    live = self._live_game(game)
                    if located and live is not None:
                        live.launch_path = new_path
                        live.library_id = ""
                        self._save_games()  # the view finds it again by the Game object, under its new id
                return

            # Tracing watches a program launch only; a web page is never observed
            observe = self.launch_observer is not None and launch_kind(full_path) == "program"
            token = None
            if observe:
                token = self.launch_observer.before_launch(game)
                if token is None:
                    return

            # Launch the game
            try:
                game_dir = str(Path(full_path).parent)
                system = platform.system()
                if system == "Darwin" and full_path.endswith(".app"):
                    # macOS .app bundle
                    subprocess.Popen(["open", "-a", full_path], cwd=game_dir)
                else:
                    # Regular executable (one marked "run as administrator" goes through Windows' prompt)
                    # start_launch_file decides whether an .hta may run (only the one the catalog named)
                    started = start_launch_file(full_path, game_dir, game, self.service.record_for,
                                                self.library_manager.config["libraries"])
                    if started.kind == "page":
                        return  # a web page opens in the browser; the window stays as it is
                    if observe:
                        self.launch_observer.launched(token, game, started.pid, full_path, game_dir)

                # Minimize window
                self.Iconize(True)

            except LaunchCancelled:
                pass  # the player declined the administrator prompt: nothing started, nothing to say
            except Exception as e:
                self._tell(f"Failed to launch game: {e}", "Launch Error")

    def _launch_problem(self, game, problem):
        """A launch found no executable: the library is unreachable, or a catalog game's files are missing (a
        tracked one is offered Repair; the player never picks a catalog game's executable)"""
        if problem == "unreachable":
            self._library_unreachable(game)
            return
        if problem == "catalog":
            self._tell(messages.LAUNCH_NOT_FOUND_CATALOG, messages.LAUNCH_NOT_FOUND_TITLE)
            return
        answer = self._ask(messages.LAUNCH_NOT_FOUND_REPAIR.format(title=game.title), messages.LAUNCH_NOT_FOUND_TITLE,
                           wx.ICON_ERROR)
        live = self._live_game(game)
        if answer and live is not None and live.catalog_id and not self._game_busy(live):
            self._submit_catalog("repair", live)

    def on_launch(self, event):
        """Launch selected game"""
        game = self.selected_game()
        if not game:
            return

        self.launch_game(game)

    def on_random_game(self, event):
        """Ctrl+R: a random game from the active tab's list as shown (GamesPanel.random_game asks, selects its row
        and acts). Library: among the games that run on this system (web games included); Yes launches it."""
        if self.active_tab() == "catalog":
            self.catalog_panel.on_random_game()
            return
        current_system = platform.system()
        platform_map = {
            "Windows": "Windows",
            "Darwin": "macOS",
            "Linux": "Linux"
        }
        current_platform = platform_map.get(current_system, current_system)
        rows = self.library_view.displayed
        compatible = [row for row in rows
                      if row.game.is_web or current_platform in row.game.platforms]
        if rows:
            none_left = (f"No games compatible with {current_platform} in the current filtered list.",
                         "No Compatible Games")
        else:
            none_left = (messages.CATALOG_NO_GAMES, "No Games")
        self.library_view.random_game(compatible, none_left, lambda row: self.launch_game(row.game))

    def on_random_install(self, event):
        """Pick a random game to install from the Catalog tab's filtered list (switching to that tab first)"""
        if self.active_tab() != "catalog":
            self.notebook.SetSelection(TABS.index("catalog"))
        self.catalog_panel.on_random_install()

    def on_edit_game(self, event):
        """Edit selected game"""
        if self.active_tab() != "library":
            return
        game = self._live_game(self.selected_game())
        if not game:
            return
        if not is_editable(game):  # the catalog owns a catalog game's details; Ctrl+E says so, never silent
            self._tell(messages.CATALOG_NOT_EDITABLE.format(title=game.title), messages.EDIT_GAME_TITLE,
                       wx.ICON_INFORMATION)
            self.focus_active_list()
            return

        with self.modal_dialog(), GameDialog(self, self.library_manager, game) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self._save_games()

    def on_delete_game(self, event):
        """Delete selected game"""
        if self.active_tab() != "library":
            return
        game = self._live_game(self.selected_game())
        if not game:
            return

        kind = delete_kind(game, self.service.record_for(game))
        if kind == "uninstall":  # a catalog install: uninstalled, it leaves the list when that finishes
            self.uninstall_game(game)
            return
        if kind == "forget":
            self._forget_game(game)
            return

        # The queued refreshes run after this handler returns, so after the removal below
        if self._ask(messages.DELETE_GAME_CONFIRM.format(title=game.title), messages.DELETE_GAME_TITLE):
            live = self._live_game(game)
            self.library_manager.games[:] = [g for g in self.library_manager.games if g is not live]
            # The selection stays on the deleted game's row number, also when another game has the same launch
            # path and so takes over its row id (catalog_view.pick_index)
            self._save_games()

    def _forget_game(self, game):
        """Delete an untracked catalog install: its record goes and it leaves the list; its files stay"""
        if self._game_busy(game):
            self.focus_active_list()
            return
        answer = self._ask(messages.FORGET_GAME_CONFIRM.format(title=game.title), messages.DELETE_GAME_TITLE)
        live = self._live_game(game)
        if not answer or live is None or not live.catalog_id or self._game_busy(live):
            self.focus_active_list()
            return
        try:
            self.service.forget(live.catalog_id)
        except OSError as e:
            log.warning("Couldn't forget %s: %s", live.catalog_id, e)
            self._tell(messages.FORGET_FAILED.format(title=live.title), messages.DELETE_GAME_TITLE)
            if self._live_game(live) is None:  # it left the list after all (games.json couldn't be saved)
                self.games_changed()
            else:  # nothing changed: the selection stays on it
                self.focus_active_list()
            return
        self.games_changed()

    # ---- a catalog game's actions (Library context menu, Delete, a launch that finds no executable) ------------

    def _tell(self, text, caption, icon=wx.ICON_ERROR):
        with self.modal_dialog():
            wx.MessageBox(text, caption, wx.OK | icon, self)

    def _ask(self, text, caption, style=wx.ICON_QUESTION) -> bool:
        """Yes or No (True for Yes), inside modal_dialog()"""
        with self.modal_dialog():
            return wx.MessageBox(text, caption, wx.YES_NO | style, self) == wx.YES

    def _tell_load_problems(self):
        """Once, after startup: games.json or config.json couldn't be read (and where the copy of it was kept)"""
        problems, self.library_manager.load_problems = self.library_manager.load_problems, []
        if problems and self and not self._quitting:
            self._tell("\n\n".join(problems), messages.FILE_DAMAGED_TITLE, wx.ICON_WARNING)
            self.focus_active_list()

    def _library_unreachable(self, game):
        """Say so and return True when the game's library folder can't be reached (a drive that isn't connected)"""
        if not game.library_id or self.library_manager.library_reachable(game.library_id):
            return False
        lib = self.library_manager.library_by_id(game.library_id)
        text = (messages.LIBRARY_UNREACHABLE.format(path=lib["path"]) if lib
                else messages.LIBRARY_ROW_GONE.format(title=game.title))  # its library was removed from the config
        self._tell(text, messages.LIBRARY_NOT_FOUND_TITLE)
        return True

    def _game_busy(self, game):
        """Say so and return True while the game has a job queued or running"""
        if self.service.queue.job_for(game.catalog_id) is None:
            return False
        self._tell(messages.GAME_BUSY.format(title=game.title), "Game Chooser", wx.ICON_INFORMATION)
        return True

    def _submit_catalog(self, action, game, extras=(), delete_player_data=False):
        """Queue a job for a catalog game (update, undo, extras, repair, uninstall); its progress shows below the
        games list. No library is passed: these act on the game's own folder (and last_library stays)."""
        try:
            self.service.submit(action, game.catalog_id, extras=extras, delete_player_data=delete_player_data)
        except Exception:
            log.exception("Couldn't queue %s of %s", action, game.catalog_id)
            self._tell(messages.ACTION_NOT_STARTED.format(title=game.title), "Game Chooser")
        self.refresh_statuses()

    def _fixed_library(self, game):
        """The library an update or extras go into: the game's own (shown in the dialog, not chosen)"""
        lib = self.library_manager.library_by_id(game.library_id) if game.library_id else None
        if lib is not None:
            return lib
        record = self.service.record_for(game)
        folder = record_folder(record, self.library_manager.config["libraries"]) if record else None
        return {"id": game.library_id, "path": str(folder.parent) if folder else ""}

    def on_catalog_action(self, item, game):
        """A catalog item of the Library context menu"""
        if item.action == library_actions.LINK:
            webbrowser.open(item.url)
            return
        game = self._live_game(game)
        if game is None or not game.catalog_id:
            return
        try:
            if item.action == library_actions.UNINSTALL:
                self.uninstall_game(game)
            elif not self._library_unreachable(game) and not self._game_busy(game):
                if item.action in (library_actions.UPDATE, library_actions.EXTRAS):
                    self._confirm_change(game, item.action)
                else:  # undo, repair
                    self._submit_catalog(item.action, game)
        finally:
            self.focus_active_list()

    def confirm_install(self, plan, fixed_library=None):
        """The confirmation dialog for a plan (install, update or extras): the player's InstallChoice, or None
        when they cancelled"""
        with self.modal_dialog(), InstallConfirmDialog(self, plan, self.service, fixed_library=fixed_library) as dlg:
            return dlg.get_choice() if dlg.ShowModal() == wx.ID_OK else None

    def _confirm_change(self, game, action):
        """Update or Extras: the confirmation dialog for that plan, with the game's library fixed"""
        try:
            plan = self.service.plan(game.catalog_id, action)
        except Exception:
            log.exception("Couldn't plan %s of %s", action, game.catalog_id)
            self._tell(messages.ACTION_NOT_STARTED.format(title=game.title), "Game Chooser")
            return
        choice = self.confirm_install(plan, fixed_library=self._fixed_library(game))
        live = self._live_game(game)
        if choice is not None and live is not None and live.catalog_id:
            self._submit_catalog(plan.action, live, extras=choice.extras)

    def uninstall_game(self, game):
        """Uninstall a catalog install (Del, Delete or Uninstall): "Uninstall <title>", the player's files kept
        unless ticked. The game leaves the list when the uninstall finishes.

        The player's files are counted (hashed) on a worker thread first, after the checks that could refuse it;
        a slow count is announced with a notification, and the dialog opens when the count arrives."""
        cid = game.catalog_id
        if cid in self._counting:  # already counting for this game: its dialog is coming
            return
        if self._library_unreachable(game) or self._game_busy(game):
            self.focus_active_list()
            return
        self._counting.add(cid)
        service, title = self.service, game.title
        run_in_background(lambda: service.player_files(cid), lambda count: self._uninstall_counted(game, count),
                          "player-files", slow=lambda: self and self.notify(messages.COUNTING_FILES.format(title=title)),
                          default=0, post=_call_after)

    def _uninstall_counted(self, game, player_files):
        if not self:
            return
        self._counting.discard(game.catalog_id)
        if not self._quitting:
            self.run_when_no_dialog(self._confirm_uninstall, game, player_files)

    def _confirm_uninstall(self, game, player_files):
        """The count arrived and no dialog is open: check again (things may have changed), then ask. Never while
        quitting: the quit's run of deferred work only brings the library up to date."""
        if self._quitting:
            return
        live = self._live_game(game)
        try:
            if live is None or not live.catalog_id or self._library_unreachable(live) or self._game_busy(live):
                return
            with self.modal_dialog(), UninstallDialog(self, live.title, player_files) as dlg:
                confirmed = dlg.ShowModal() == wx.ID_OK
                delete_player_data = dlg.delete_player_data()
            live = self._live_game(live)
            if confirmed and live is not None and live.catalog_id:
                self._submit_catalog("uninstall", live, delete_player_data=delete_player_data)
        finally:
            self.focus_active_list()
    
    def on_add_game(self, event):
        """Add a new game"""
        # Block spurious selection events (and game-changing refreshes) while modal dialog is active
        with self.modal_dialog(), GameDialog(self, self.library_manager) as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.library_manager.games.append(dlg.game)
                self._save_games()

    def _toggle_filter(self, filter_key):
        """Show or hide one category of the Library tree"""
        keys = list(self.library_view.tree_keys)
        if filter_key in keys:
            keys.remove(filter_key)
        else:
            keys.append(filter_key)
        self.library_manager.config["SavedState"]["tree_filters"] = keys
        # The view rebuilds the tree, filters again (a selection under a category that went no longer applies)
        # and then saves the config
        self.library_view.set_tree_keys(keys)
        self.update_filter_menu_state()

    def update_filter_menu_state(self):
        """Update filter menu item check states based on current filters"""
        for key, item in self.filter_items.items():
            item.Check(key in self.library_view.tree_keys)

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

        # Filter first, save second: a save that fails never stops the filter
        self.library_manager.config["SavedState"]["hidden_libraries"] = sorted(self.hidden_libraries)
        self.library_view.apply_filters()
        self.library_view.save_config()

    def on_preferences(self, event):
        """Show preferences dialog.

        Preferences holds library ids, never a Game, so finished installs are applied while it is open (it is
        marked holds_no_games, and isn't opened in modal_dialog()): a library whose last uninstall finishes
        leaves its list at once. The prompts it opens still defer them, through modal_dialog()."""
        with PreferencesDialog(self, self.library_manager, self.service) as dlg:
            hook = dlg.on_job_done
            self.job_done_hooks.append(hook)
            try:
                dlg.ShowModal()
                # Refresh even on Cancel: Apply may already have saved changes (a new address too).
                # Deferred to avoid blocking the dialog close.
                wx.CallAfter(self.refresh_ui_after_preferences, dlg.catalog_url_changed())
            finally:
                self.job_done_hooks.remove(hook)

    def refresh_ui_after_preferences(self, catalog_changed=False):
        """Refresh UI after preferences dialog closes; a new catalog address fetches the catalog again"""
        if catalog_changed:
            self.refetch_index()
        self.games_changed()  # a library may have been added, moved or removed
    
    def on_refresh(self, event):
        """F5: on the Catalog tab check for catalog updates; on Library rebuild the game list and tree and
        recheck each game's Status"""
        if self.active_tab() == "catalog":
            self.catalog_panel.check_for_updates()
            return
        self.games_changed()

    def on_exit(self, event):
        """Exit application"""
        self.Close()
    
    def update_title(self):
        """Update window title with game count"""
        count = len(self.library_manager.games)
        self.SetTitle(f"Game Chooser{self.title_note} ({count})")
    
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

        # Each tab's splitter, now that the window has its size (the search, sort, tree filters and column widths
        # were read by each view when it was built)
        for view in self.views.values():
            view.restore_sash()

        # Update menu item check states based on current filters
        self.update_filter_menu_state()

        # Build libraries menu with current state
        self.build_libraries_menu()

        # Opening tab: the saved one, else Catalog on a first run and Library otherwise.
        # ChangeSelection, not SetSelection: no page-changed event (and no focus move) during startup.
        tab = initial_tab(state.get("last_tab"), self.library_manager.is_first_run)
        self.notebook.ChangeSelection(TABS.index(tab))
        self.update_tab_menus()

    def save_state(self):
        """Save current window state"""
        state = self.library_manager.config["SavedState"]

        state["window_size"] = list(self.GetSize())
        state["window_position"] = list(self.GetPosition())
        state["last_tab"] = self.active_tab()
        for view in self.views.values():
            view.save_state()  # splitter position, column widths, search and tree selections
        # (tree_filters and hidden_libraries are stored by their own toggles)

        self.library_manager.save_config()
    
    def on_close(self, event):
        """Handle window close. While a job runs, ask first; quitting cancels it and waits for it to stop."""
        queue = self.service.queue
        job = queue.current()
        if job is not None and event.CanVeto() and not self.confirm_quit_while_busy(job.title, job.request.action):
            event.Veto()
            return
        self._quitting = True
        try:
            # A prompt the worker is blocked on answers "cancelled" first, so nothing can wait on this window
            self.installer_ui.release_all()
            queue.cancel_all()
            self._quit_step("save the window state", self.save_state)
            if queue.busy():
                self.Hide()  # the window goes at once; the cancelled job gets a moment to stop and tidy up
            if not queue.shutdown(QUIT_WAIT_SECONDS):
                log.warning("The install queue didn't stop within %s seconds", QUIT_WAIT_SECONDS)
            # a job that ended meanwhile (an uninstall often ignores the cancel)
            self._quit_step("apply the last job results", self._apply_outcomes_on_quit)
            self._quit_step("save games.json", self.library_manager.save_games)
            self._quit_step("save config.json", self.library_manager.save_config)
        finally:
            # Always: the window may already be hidden, and an error must never leave an invisible process
            self.Destroy()
            # A notification shown as a balloon (no toasts) keeps a hidden window, and so the app, alive for a while
            app = wx.GetApp()
            if app is not None:
                app.ExitMainLoop()

    def _quit_step(self, what, fn):
        """One step of quitting: a failure is logged and the rest of the quit goes on"""
        try:
            fn()
        except Exception:
            log.exception("Couldn't %s while quitting", what)

    def confirm_quit_while_busy(self, title, action="install"):
        """"<title> is still installing. Cancel it and quit?" (worded by the job's action). True for Yes."""
        return self._ask(messages.quit_while_busy_text(action, title), messages.QUIT_WHILE_BUSY_TITLE,
                         wx.NO_DEFAULT | wx.ICON_QUESTION)