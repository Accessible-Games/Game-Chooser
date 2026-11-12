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

"""The Catalog tab: browse the curated catalog, filter it, and install, add or launch a game.

UI thread only. The panel never fetches anything itself: MainFrame fetches the index on a worker thread and
hands the result to `set_index_state`. Links open in the browser only when the player chooses them.
"""

import logging
import webbrowser

import wx

from smart_list import Column

from catalog_view import action_kind, action_label, build_rows, install_candidates, link_items
from engine import messages
from engine.entries import current_platform, game_fields, platform_sources
from games_panel import GamesPanel
from install_service import InstallService

log = logging.getLogger(__name__)

# (criteria key, tree category label), in tree order
TREE_CATEGORIES = [
    ("platform", "Platform"),
    ("genre", "Genre"),
    ("tag", "Tags"),
    ("price", "Price"),
    ("language", "Language"),
    ("developer", "Developer"),
    ("year", "Release Year"),
    ("support", "Support"),
    ("installed", "Installed"),
]


class CatalogPanel(wx.Panel):
    """Tab order: status line, Retry, then the games view (Search, games list, filter tree, action button,
    description)."""

    def __init__(self, parent, frame, service: InstallService):
        super().__init__(parent)
        self.frame = frame
        self.service = service
        self.state = service.index_state
        self._checking = False
        self._newer_shown = False  # the "newer version needed" message shows once per session

        sizer = wx.BoxSizer(wx.VERTICAL)

        # Status line (hidden unless there is a message). The hidden label before it is what a screen reader reads.
        self._status_label = wx.StaticText(self, label="Catalog status")
        self._status_label.Hide()
        self.status = wx.TextCtrl(self, style=wx.TE_READONLY)
        self.status.SetName("Catalog status")
        sizer.Add(self.status, 0, wx.EXPAND | wx.ALL, 5)
        self.retry_btn = wx.Button(self, label="Retry")
        self.retry_btn.Bind(wx.EVT_BUTTON, self.on_retry)
        sizer.Add(self.retry_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        # Search, the games list with its filter tree, the action button and the description
        self.view = GamesPanel(
            self, frame, name="Catalog games", categories=TREE_CATEGORIES, state_prefix="catalog_",
            idle_label=action_label(None),
            columns=[
                Column(title="Title", model_field=lambda r: r.title, width=200),
                Column(title="Developer", model_field=lambda r: r.developer, width=180),
                Column(title="Genre", model_field=lambda r: r.genre, width=120),
                Column(title="Price", model_field=lambda r: r.price, width=200),
                Column(title="Details", model_field=lambda r: r.details_text, width=200),
            ],
            on_action=self.on_action, context_menu=self.context_menu)
        sizer.Add(self.view, 1, wx.EXPAND)
        self.SetSizer(sizer)

        self._update_status()
        self.refresh_rows()

    # ---- the index and the status line -------------------------------------------------------------------------

    def set_index_state(self, state):
        """A fetched (or saved) index was taken: update the status line. The rows are MainFrame.games_changed()'s,
        which runs first."""
        self.state = state
        self._checking = False
        self._update_status()
        if (self.retry_btn.IsShown() and self.frame.active_tab() == "catalog" and not self.frame.initializing
                and not self.frame.dialog_active):
            self.status.SetFocus()  # no catalog at all: a screen reader reads why, and Retry is next

    def set_checking(self, checking: bool):
        """Show or clear "Checking for catalog updates" while MainFrame fetches the index"""
        self._checking = checking
        self._update_status()

    def check_for_updates(self):
        """F5 and Retry: fetch the index again (MainFrame runs the fetch on a worker thread)"""
        self.frame.start_index_fetch()

    def on_retry(self, event):
        self.check_for_updates()
        if self.status.IsShown():  # the Retry button is about to hide; a screen reader follows to the status
            self.status.SetFocus()

    def _status_message(self) -> tuple[str, bool]:
        """(status line text, whether Retry shows)"""
        if self._checking:
            return messages.CATALOG_CHECKING, False
        state = self.state
        if state.newer_app_needed and not self._newer_shown:
            self._newer_shown = True
            return messages.CATALOG_NEWER_APP_NEEDED, bool(state.error and not state.entries)
        if state.error and not state.entries:
            return messages.CATALOG_LOAD_FAILED, True
        return "", False

    def _update_status(self):
        text, retry = self._status_message()
        lost_focus = ((self.status.IsShown() and not text and self.status.HasFocus())
                      or (self.retry_btn.IsShown() and not retry and self.retry_btn.HasFocus()))
        if self.status.GetValue() != text:
            self.status.ChangeValue(text)
        self.status.Show(bool(text))
        self.retry_btn.Show(retry)
        self.Layout()
        if lost_focus:  # never leave focus on a control that just hid (focus_list refuses under a dialog)
            self.view.focus_list()

    def refresh_rows(self):
        """Rebuild the rows from the service (installed state may have changed) and re-filter, keeping the
        selected game selected"""
        self.view.set_rows(build_rows(self.service.visible_games(), self.service.installed_ids()))

    # ---- actions -------------------------------------------------------------------------------------------------

    def on_action(self):
        """Enter, Space, double-click or the button: launch an installed game; otherwise install or add it"""
        row = self.view.selected_row()
        if row is None:
            return
        if row.installed:
            self._launch(row)
        else:
            self._confirm_install(row)

    def _launch(self, row):
        game = self.service.manager.catalog_game(row.id)
        if game is None:
            log.warning("%s is installed but has no Library entry", row.id)
            self.frame._tell(messages.CATALOG_NOT_IN_LIBRARY.format(title=row.title), "Catalog", wx.ICON_INFORMATION)
            return
        self.frame.launch_game(game)

    def _confirm_install(self, row):
        """Install: confirm where and what first. A web game just gets added to the Library."""
        if action_kind(row) == "add":
            self._add_web_game(row)
            return
        job = self.service.queue.job_for(row.id)
        if job is not None:  # already queued or running: say so, and change nothing (not even last_library)
            text = messages.JOB_ALREADY_RUNNING if job.state == "running" else messages.JOB_ALREADY_WAITING
            self.frame._tell(text.format(title=row.title), "Catalog", wx.ICON_INFORMATION)
            self.frame.focus_active_list()
            return
        try:
            plan = self.service.plan(row.id)
        except Exception:
            log.exception("Couldn't plan installing %s", row.id)
            self.frame._tell(messages.INSTALL_NOT_STARTED.format(title=row.title), "Catalog", wx.ICON_ERROR)
            self.frame.focus_active_list()
            return
        choice = self.frame.confirm_install(plan)
        if choice is not None:
            try:
                self.service.submit(plan.action, row.id, choice.library_id, choice.library_path, choice.extras)
            except Exception:
                log.exception("Couldn't queue installing %s", row.id)
                self.frame._tell(messages.INSTALL_NOT_STARTED.format(title=row.title), "Catalog", wx.ICON_ERROR)
            self.frame.games_changed()
        self.frame.focus_active_list()  # back to the row: it stayed selected

    def _add_web_game(self, row):
        sources = platform_sources(row.entry, current_platform())
        fields = game_fields(row.entry, current_platform())
        fields.update(platforms=["Web"], launch_path=sources[0].get("url", ""), library_id="", catalog_id=row.id)
        manager = self.service.manager
        before = list(manager.games)
        try:
            manager.add_catalog_game(fields)
        except OSError as e:  # games.json couldn't be saved: nothing changes (the file on disk is whole)
            log.warning("Couldn't add the web game %s: %s", row.id, e)
            manager.games = before
            self.frame._tell(messages.WEB_GAME_NOT_ADDED.format(title=fields["title"] or row.title), "Catalog",
                             wx.ICON_ERROR)
            self.frame.focus_active_list()
            return
        self.frame.games_changed()
        self.frame.notify(messages.ADDED.format(title=fields["title"] or row.title))
        self.frame.focus_active_list()

    def on_random_game(self):
        """Ctrl+R: a random game from the list as shown (filters applied); Yes runs the row's own action"""
        self.view.random_game(self.view.displayed, (messages.CATALOG_NO_GAMES, "No Games"),
                              lambda row: self.on_action())

    def on_random_install(self):
        """Ctrl+Shift+R: a random game to install from the list as shown (filters applied), leaving out installed
        games, web games and games already queued; Yes opens the install confirmation"""
        busy = {r.id for r in self.view.displayed if self.service.queue.job_for(r.id) is not None}
        self.view.random_game(install_candidates(self.view.displayed, busy),
                              (messages.CATALOG_NO_GAMES_TO_INSTALL, "No Games"), self._confirm_install,
                              caption="Random Game to Install")

    def context_menu(self, row):
        """The action, then a link item for each link the game has"""
        if row is None:
            return None
        menu = wx.Menu()
        action = menu.Append(wx.ID_ANY, action_label(row))
        menu.Bind(wx.EVT_MENU, lambda e: self.on_action(), action)
        for label, url in link_items(row.entry):
            item = menu.Append(wx.ID_ANY, label)
            menu.Bind(wx.EVT_MENU, lambda e, link=url: webbrowser.open(link), item)
        return menu
