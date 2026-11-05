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

"""The install service: ties the catalog index, the install engine and queue, and the game library together.

wx-free. Every member is for the UI thread except `fetch_index`, which is safe on any thread and changes
nothing: the UI fetches in the background and hands the state to `take_index` on its own thread. The UI calls
`apply_result` and `job_failed` on its thread when the queue's listener reports a job's end.
"""

import logging
import os
import tempfile
from pathlib import Path

from engine import messages
from engine.elevation import WindowsElevator
from engine.entries import current_platform
from engine.entries import visible_games as _visible_games
from engine.extract import Extractor, default_tools_dir
from engine.http import Fetcher
from engine.index import DEFAULT_CATALOG_URL, CatalogIndex, IndexState, normalize_catalog_url
from engine.jobs import EngineContext, InstallEngine, InstallRequest, JobResult, blocked_reason, game_fields_for
from engine.launch import adopt_catalog_launch
from engine.plan import InstallPlan, build_install_plan
from engine.queue import InstallQueue, Job, QueueListener
from engine.records import InstallRecord, record_folder
from engine.registry import RegistryAccess
from engine.runtimes import WindowsSecurity, default_runtimes_root
from engine.status import game_status
from library_manager import LibraryError

log = logging.getLogger(__name__)


def _is_web(game) -> bool:
    return game.launch_path.startswith("http")


class InstallService:
    def __init__(self, manager, listener: QueueListener, installer_ui, fetcher=None, extractor=None,
                 elevator=None, registry=None):
        self.manager = manager
        self.installer_ui = installer_ui
        self.fetcher = fetcher if fetcher is not None else Fetcher()
        self.extractor = extractor if extractor is not None else Extractor(default_tools_dir())
        self.elevator = elevator if elevator is not None else WindowsElevator()
        self.registry = registry if registry is not None else RegistryAccess(self.elevator)
        self.security = WindowsSecurity()
        self.index_state = IndexState()  # entries come from it, so the two never disagree
        self.pending_removal: set[str] = set()  # library ids waiting for their games' uninstalls
        self.queue = InstallQueue(self._new_engine, listener, manager.config_dir / "install.log")

    # ---- the catalog index ----------------------------------------------------------------------------------

    def _index(self) -> CatalogIndex:
        url = self.manager.config["install"].get("catalog_url") or DEFAULT_CATALOG_URL
        return CatalogIndex(self.manager.config_dir / "catalog", normalize_catalog_url(url) or url, self.fetcher)

    @property
    def entries(self) -> dict[str, dict]:
        return self.index_state.entries

    def fetch_index(self) -> IndexState:
        """Fetch the catalog index (offline, the saved copy) without changing anything. Safe on any thread."""
        return self._index().refresh()

    def take_index(self, state: IndexState) -> IndexState:
        """Use a fetched index, and adopt the launch paths it now names. UI thread only."""
        self.index_state = state  # one assignment: a reader on the worker thread sees the old or the new
        self.adopt_launch_paths()
        return state

    def load_cached_index(self) -> IndexState:
        """Use the saved copy of the index. UI thread only."""
        return self.take_index(self._index().load_cached())

    def refresh_index(self) -> IndexState:
        """Fetch and use the index in one step. UI thread only (it changes games.json); never call it from
        another thread, use fetch_index there and take_index back on the UI thread."""
        return self.take_index(self.fetch_index())

    def adopt_launch_paths(self) -> int:
        """Switch guessed launch paths to the catalog's, where the catalog now names one; returns how many.

        UI thread only. A game with a queued or running job is left alone: the job owns its record."""
        changed = 0
        for game in list(self.manager.games):
            if not game.catalog_id or _is_web(game) or self.queue.job_for(game.catalog_id) is not None:
                continue
            try:
                changed += self._adopt(game.catalog_id)
            except Exception:  # a malformed entry or a file error never stops the others
                log.warning("Couldn't adopt the catalog launch path of %s", game.catalog_id, exc_info=True)
        return changed

    def _adopt(self, catalog_id: str) -> bool:
        platform = current_platform()
        entry = self.entries.get(catalog_id)
        rec = self.manager.records.load(catalog_id)
        if entry is None or rec is None or rec.uninstalled:
            return False
        if rec.library_id and not self.manager.library_reachable(rec.library_id):
            return False
        game_dir = record_folder(rec, self.manager.config["libraries"])
        if game_dir is None or not adopt_catalog_launch(rec, entry, platform, game_dir):
            return False
        self.manager.records.save(rec)
        self.manager.add_catalog_game(game_fields_for(entry, rec, platform))
        return True

    def visible_games(self) -> list[dict]:
        return _visible_games(self.entries, current_platform())

    def installed_ids(self) -> set[str]:
        """Catalog ids shown as installed: an install record that isn't uninstalled, or a catalog web game."""
        ids = {g.catalog_id for g in self.manager.games if g.catalog_id and _is_web(g)}
        ids |= {cid for cid, rec in self.manager.records.all().items() if not rec.uninstalled}
        return ids

    # ---- installing -----------------------------------------------------------------------------------------

    def _new_engine(self) -> InstallEngine:
        """A fresh engine for each job, so the current catalog, libraries and preferences reach it."""
        install = self.manager.config["install"]
        return InstallEngine(EngineContext(
            entries=self.entries,
            records=self.manager.records,
            fetcher=self.fetcher,
            extractor=self.extractor,
            elevator=self.elevator,
            registry=self.registry,
            work_root=Path(tempfile.gettempdir()) / "GameChooser",
            runtimes_root=default_runtimes_root(),
            libraries=[dict(lib) for lib in self.manager.config["libraries"]],
            register_shared_components=bool(install.get("register_shared_components", True)),
            platform=current_platform(),
            installer_ui=self.installer_ui,
            security=self.security,
        ))

    def plan(self, entry_id: str, action: str = "install") -> InstallPlan:
        install = self.manager.config["install"]
        return build_install_plan(entry_id, self.entries, self.manager.config["libraries"], self.manager.records,
                                  self.registry, bool(install.get("register_shared_components", True)),
                                  install.get("last_library"), current_platform(), action,
                                  runtimes_root=default_runtimes_root(), security=self.security)

    def blocked(self, entry_id: str, library_path: Path) -> str | None:
        """Why the game can't be installed into this library folder right now, or None."""
        target = os.path.normcase(os.path.abspath(library_path))
        for lib in self.manager.config["libraries"]:
            if lib["id"] in self.pending_removal and os.path.normcase(os.path.abspath(lib["path"])) == target:
                return messages.LIBRARY_BEING_REMOVED
        return blocked_reason(self.entries[entry_id], Path(library_path), self.manager.records,
                              self.manager.config["libraries"])

    def _title(self, catalog_id: str) -> str:
        entry = self.entries.get(catalog_id)
        if entry is not None and entry.get("title"):
            return entry["title"]
        game = self.manager.catalog_game(catalog_id)
        return game.title if game is not None and game.title else catalog_id

    def submit(self, action: str, entry_id: str, library_id: str = "", library_path: Path | None = None,
               extras=(), delete_player_data: bool = False) -> Job:
        request = InstallRequest(action, entry_id, library_id, library_path, tuple(extras), delete_player_data)
        job = self.queue.submit(request, self._title(entry_id))
        if library_id:  # remembered only once the job is queued (submit raises after shutdown)
            self.manager.config["install"]["last_library"] = library_id
            try:
                self.manager.save_config()
            except OSError as e:
                log.warning("Couldn't save the last library: %s", e)
        return job

    def apply_result(self, result: JobResult) -> None:
        """Bring games.json up to date with a finished job (on the UI thread)."""
        if result.game is not None:
            self.manager.add_catalog_game(result.game)
        elif result.action == "uninstall":
            self.manager.remove_catalog_game(result.catalog_id)
        for library_id in list(self.pending_removal):
            if not self.manager.catalog_games_in(library_id):
                self._finish_removal(library_id)

    def job_failed(self, job: Job) -> None:
        """A job failed (or was cancelled): a library waiting on it to be removed stays."""
        game = self.manager.catalog_game(job.request.catalog_id)
        rec = self.manager.records.load(job.request.catalog_id) if game is None else None
        library_id = game.library_id if game is not None else rec.library_id if rec is not None else None
        self.pending_removal.discard(library_id)

    # ---- library status -------------------------------------------------------------------------------------

    def record_for(self, game) -> InstallRecord | None:
        return self.manager.records.load(game.catalog_id) if game.catalog_id else None

    def entry_for(self, game) -> dict | None:
        return self.entries.get(game.catalog_id) if game.catalog_id else None

    def statuses(self) -> dict[tuple[str, str], str]:
        """Each game's status, keyed by (library_id, launch_path)."""
        platform = current_platform()
        records = self.manager.records.all()
        result = {}
        for game in self.manager.games:
            full_path = self.manager.get_full_path(game)
            reachable = self.manager.library_reachable(game.library_id) if game.library_id else True
            record = records.get(game.catalog_id) if game.catalog_id else None
            result[(game.library_id, game.launch_path)] = game_status(
                str(full_path) if full_path else None, _is_web(game), record, self.entry_for(game), platform,
                library_reachable=reachable)
        return result

    # ---- libraries ------------------------------------------------------------------------------------------

    def ensure_library(self, folder: Path) -> dict:
        """Create the folder if needed and add it as a library."""
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise LibraryError(messages.LIBRARY_FOLDER_NOT_CREATED.format(path=folder)) from e
        return self.manager.add_library(str(folder))

    def remove_library(self, library_id: str, delete_player_data: bool) -> list[Job]:
        """Remove a library; its catalog games are uninstalled first, and it goes once they all are."""
        games = self.manager.catalog_games_in(library_id)
        if not games:
            self.manager.remove_library(library_id)
            return []
        if not self.manager.library_reachable(library_id):
            raise LibraryError(messages.LIBRARY_REMOVE_UNREACHABLE)
        for game in games:  # its job (an update, say) would stand in for the uninstall
            if self.queue.job_for(game.catalog_id) is not None:
                raise LibraryError(messages.LIBRARY_REMOVE_BUSY.format(title=game.title or game.catalog_id))
        self.pending_removal.add(library_id)
        return [self.submit("uninstall", g.catalog_id, delete_player_data=delete_player_data) for g in games]

    def _finish_removal(self, library_id: str) -> None:
        self.pending_removal.discard(library_id)
        for cid, rec in self.manager.records.all().items():
            if rec.uninstalled and rec.library_id == library_id:
                self.manager.records.delete(cid)
        self.manager.remove_library(library_id)
