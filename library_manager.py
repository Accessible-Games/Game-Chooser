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
Game library management for Game Chooser application
"""

import json
import logging
import os
import sys
import platform
import secrets
import time
from pathlib import Path

from engine import messages
from engine.folders import library_label
from engine.index import DEFAULT_CATALOG_URL, CatalogIndex
from engine.jobs import game_fields_for
from engine.records import RecordStore
from library_actions import is_uninstall_unfinished
from models import Game
from path_manager import PathManager


log = logging.getLogger(__name__)

# What a games.json or config.json that can't be read raises: unreadable, not JSON (or not UTF-8), or JSON of the
# wrong shape (a list where an object belongs, a library that isn't an object, a game row Game can't take).
_LOAD_ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError)


class LibraryError(Exception):
    """A library change that can't be made; the message is plain words for the player."""


def _write_json_atomic(path, data):
    """Write JSON to a temporary file beside `path`, then replace `path` with it in one step, so a crash or a full
    disk mid-write leaves the old file whole (as RecordStore.save does). OSError if it can't be written."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        _remove_quietly(tmp)
        raise


def keep_damaged_copy(path):
    """Copy a file that couldn't be read beside it, before anything can write over it; returns the copy's path.

    The rule: the first copy is `<name>.bad`; when that is taken (an earlier damaged file, kept), it is
    `<name>.<YYYYMMDD-HHMMSS>.bad` (then `-2`, `-3`, ...). An existing copy is never overwritten. The copy is
    read back and compared. OSError if no copy could be made."""
    path = Path(path)
    data = path.read_bytes()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    names = [f"{path.name}.bad", f"{path.name}.{stamp}.bad", *(f"{path.name}.{stamp}-{n}.bad" for n in range(2, 100))]
    for name in names:
        copy = path.with_name(name)
        try:
            with open(copy, "xb") as f:  # exclusive: never over an earlier copy
                f.write(data)
        except FileExistsError:
            continue
        except OSError:
            _remove_quietly(copy)  # only a partial copy of ours can be there
            raise
        if copy.read_bytes() != data:
            _remove_quietly(copy)
            raise OSError(f"The copy of {path} differs from it: {copy}")
        return copy
    raise OSError(f"No free name for a copy of {path}")


def _remove_quietly(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def _is_inside(path, parent):
    try:
        return path != parent and os.path.commonpath([path, parent]) == parent
    except ValueError:  # different drives: never inside each other
        return False


class GameLibraryManager:
    """Handles all game library operations and data management"""
    
    def __init__(self):
        self.games = []
        self.config = {}
        self.app_dir = Path(os.path.dirname(os.path.abspath(sys.argv[0])))
        self.games_file = self.app_dir / "games.json"
        self.config_file = self.get_config_path()
        self.is_first_run = not self.config_file.exists()
        self.path_manager = PathManager()
        # Files that couldn't be read whose damaged copy isn't kept yet: nothing writes over them until it is
        self._uncopied_damage = set()
        self._damage_copies = {}  # damaged file -> the copy kept of it
        self.load_problems = []  # plain words for the player, shown once after startup (then cleared by the UI)
        self.load_config()
        self.load_games()
        self.config_dir = self.config_file.parent
        self.records = RecordStore(self.config_dir / "installs")
        self.reconcile_catalog_records()
    
    def get_config_path(self):
        """Get platform-specific config path"""
        system = platform.system()
        if system == "Windows":
            app_data = Path(os.environ.get('APPDATA', ''))
            config_dir = app_data / "GameChooser"
        elif system == "Darwin":  # macOS
            config_dir = Path.home() / "Library" / "Application Support" / "GameChooser"
        else:  # Fallback for other systems - use macOS style
            config_dir = Path.home() / "Library" / "Application Support" / "GameChooser"
        
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "config.json"
    
    def load_config(self):
        """Load configuration from JSON file"""
        default_config = {
            "libraries": [],
            "SavedState": {
                "window_size": None,
                "window_position": None,
                "splitter_position": None,
                "sort_column": 0,
                "sort_ascending": True,
                "last_selected": None,
                "last_search": "",
                "column_widths": None,
                "tree_selections": [],
                "tree_filters": ["platform", "genre", "developer", "year"],
                "last_tab": None,
                "catalog_last_selected": None,
                "catalog_last_search": "",
                "catalog_tree_selections": [],
                "catalog_column_widths": None,
                "catalog_splitter_position": None,
                "catalog_sort_column": 0,
                "catalog_sort_ascending": True
            },
            "install": {
                "catalog_url": DEFAULT_CATALOG_URL,
                "register_shared_components": True,
                "last_library": None
            }
        }
        
        upgraded = False
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding="utf-8") as f:
                    loaded = json.load(f)
                    # Merge with defaults to ensure all keys exist
                    for key in default_config:
                        if key not in loaded:
                            loaded[key] = default_config[key]
                    for section in ("SavedState", "install"):
                        if isinstance(loaded.get(section), dict):
                            for state_key, value in default_config[section].items():
                                if state_key not in loaded[section]:
                                    loaded[section][state_key] = value
                    # The only old-file handling: ids are assigned once and then saved, so they stay stable
                    upgraded = loaded.pop("exceptions", None) is not None
                    for lib in loaded["libraries"]:
                        upgraded |= lib.pop("name", None) is not None
                        if "id" not in lib:
                            lib["id"] = secrets.token_hex(6)
                            upgraded = True
                    self.config = loaded
            except _LOAD_ERRORS as e:
                log.warning("config.json couldn't be read, so the default settings are used: %s", e)
                self.config = default_config
                upgraded = False
                self._damaged(self.config_file, messages.CONFIG_FILE_DAMAGED, messages.CONFIG_FILE_DAMAGED_NOT_COPIED)
            if upgraded:
                try:
                    self.save_config()
                except OSError as e:
                    # keep the loaded config; the upgrade is saved with the next change
                    logging.getLogger(__name__).warning("Couldn't save the upgraded config: %s", e)
        else:
            self.config = default_config
            self.save_config()
    
    def save_config(self):
        """Save configuration to JSON file (atomically). OSError if it can't be saved."""
        self._before_writing(self.config_file)
        _write_json_atomic(self.config_file, self.config)

    def load_games(self):
        """Load games from JSON file"""
        self.games_load_failed = False
        if self.games_file.exists():
            try:
                with open(self.games_file, 'r', encoding="utf-8") as f:
                    data = json.load(f)
                    self.games = [Game.from_dict(g) for g in data]
            except _LOAD_ERRORS as e:
                log.warning("games.json couldn't be read, so the games list starts empty: %s", e)
                self.games = []
                self.games_load_failed = True
                self._damaged(self.games_file, messages.GAMES_FILE_DAMAGED, messages.GAMES_FILE_DAMAGED_NOT_COPIED)
        else:
            self.games = []

    def save_games(self):
        """Save games to JSON file (atomically). OSError if it can't be saved."""
        self._before_writing(self.games_file)
        _write_json_atomic(self.games_file, [g.to_dict() for g in self.games])

    def _damaged(self, path, copied_text, not_copied_text):
        """A file couldn't be read: keep a copy of it before anything can write over it, and tell the player once.
        If the copy can't be made now, nothing saves over the file until it can (_before_writing)."""
        self._uncopied_damage.add(path)
        try:
            self._before_writing(path)
        except OSError as e:
            log.warning("Couldn't keep a copy of the damaged %s, so it won't be saved over: %s", path, e)
            self.load_problems.append(not_copied_text.format(path=path))
        else:
            self.load_problems.append(copied_text.format(path=self._damage_copies.get(path, path)))

    def _before_writing(self, path):
        """Keep the copy of a damaged file before its first save; OSError (nothing written) if it can't be kept."""
        if path not in self._uncopied_damage:
            return
        if path.exists():
            copy = keep_damaged_copy(path)
            log.warning("The damaged %s was copied to %s", path, copy)
            self._damage_copies[path] = copy
        self._uncopied_damage.discard(path)

    def catalog_game(self, catalog_id):
        """The game installed from this catalog entry, or None"""
        for game in self.games:
            if game.catalog_id == catalog_id:
                return game
        return None

    def add_catalog_game(self, fields):
        """Add the game for an installed catalog entry (replacing its old one) and save"""
        game = Game.from_dict(fields)
        self.games = [g for g in self.games if g.catalog_id != game.catalog_id]
        self.games.append(game)
        self.save_games()
        return game

    def remove_catalog_game(self, catalog_id):
        self.games = [g for g in self.games if g.catalog_id != catalog_id]
        self.save_games()

    def unlink_catalog_game(self, game):
        """Make a catalog game a game added by hand, in place (not saved): an absolute launch path outside every
        library and no catalog fields. The game stays in the list (games are never dropped silently)."""
        path = self.get_full_path(game)
        if path:
            game.launch_path, game.library_id = str(path), ""
        game.catalog_id, game.installed_source, game.installed_extras = "", None, None

    def catalog_games_in(self, library_id):
        return [g for g in self.games if g.catalog_id and g.library_id == library_id]

    def reconcile_catalog_records(self):
        """Make games.json agree with the install records (they are the truth about what is installed).

        A record with no game gets one, using the cached catalog index only (never the network); a game
        whose record is gone becomes a game added by hand and is kept. Only games' records count: a shared
        runtime's record (kind "runtime") is never a game.

        An uninstalled record: if the uninstall stopped partway (installed files left, or the game's own
        uninstaller still to run), the game stays as it is, so Uninstall can finish it. If it finished but its
        result never reached games.json (the app quit first), the game leaves the list now: an uninstall the
        player confirmed. Its record stays as it is (it keeps the folder of the player's files known).
        """
        records = self.records.game_records()
        changed = False
        finished = []
        for game in self.games:
            if not game.catalog_id or game.launch_path.startswith("http"):
                continue  # web games have no install record by design
            rec = records.get(game.catalog_id)
            if rec is None:
                if not self._record_unreadable(game.catalog_id):  # an unreadable one may still be the game's
                    self.unlink_catalog_game(game)
                    changed = True
            elif rec.uninstalled and not is_uninstall_unfinished(rec):
                finished.append(game)
        if finished:
            log.info("Uninstalls finished before the app quit: %s", ", ".join(g.catalog_id for g in finished))
            self.games = [g for g in self.games if not any(g is f for f in finished)]
            changed = True
        missing = [r for r in records.values() if not r.uninstalled and self.catalog_game(r.catalog_id) is None]
        if missing:
            entries = self._cached_entries()
            for rec in missing:
                entry = entries.get(rec.catalog_id) or {"id": rec.catalog_id, "title": rec.catalog_id}
                self.games.append(Game.from_dict(game_fields_for(entry, rec)))
            changed = True
        if changed:  # a damaged games.json is copied first, or not written over (save_games)
            try:
                self.save_games()
            except OSError as e:
                # keep the result in memory; games.json is written with the next change
                logging.getLogger(__name__).warning("Couldn't save games.json: %s", e)

    def _record_unreadable(self, catalog_id):
        """True when a record file is there for this id but can't be read, so it may still be the game's. A
        readable record that isn't a game's (a runtime's) is no game record; nor is an unsafe id's."""
        try:
            if not self.records.exists(catalog_id):
                return False
        except ValueError:  # a hand-edited games.json: no file can have that name
            logging.getLogger(__name__).warning("Not a safe catalog id, so it has no install record: %r", catalog_id)
            return False
        return self.records.load(catalog_id) is None

    def _cached_entries(self):
        url = self.config.get("install", {}).get("catalog_url", DEFAULT_CATALOG_URL)
        return CatalogIndex(self.config_dir / "catalog", url, fetcher=None).load_cached().entries

    def library_by_id(self, library_id):
        """The library with this id, or None"""
        for lib in self.config["libraries"]:
            if lib["id"] == library_id:
                return lib
        return None

    def library_reachable(self, library_id):
        """True if the library's folder is there right now (a drive can be unplugged)"""
        lib = self.library_by_id(library_id)
        return lib is not None and Path(lib["path"]).is_dir()

    def _check_folder(self, path, ignoring=None):
        """Raise LibraryError unless path can be a library (other than the library `ignoring`)"""
        if not Path(path).is_dir():
            raise LibraryError(messages.LIBRARY_FOLDER_MISSING)
        new = _norm(path)
        for lib in self.config["libraries"]:
            if lib["id"] == ignoring:
                continue
            existing = _norm(lib["path"])
            if new == existing:
                raise LibraryError(messages.LIBRARY_ALREADY_ADDED)
            if _is_inside(new, existing):
                raise LibraryError(messages.LIBRARY_INSIDE.format(label=library_label(lib)))
            if _is_inside(existing, new):
                raise LibraryError(messages.LIBRARY_CONTAINS.format(label=library_label(lib)))

    def add_library(self, path):
        """Add a library folder and return it; raises LibraryError if it can't be one"""
        self._check_folder(path)
        lib = {"id": secrets.token_hex(6), "path": path}
        self.config["libraries"].append(lib)
        self.save_config()
        return lib

    def change_library_folder(self, library_id, path):
        """Point a library at another folder; its games follow because they are stored relative to it"""
        lib = self.library_by_id(library_id)
        if lib is None:
            raise LibraryError(messages.LIBRARY_FOLDER_MISSING)
        self._check_folder(path, ignoring=library_id)
        lib["path"] = path
        self.save_config()

    def remove_library(self, library_id):
        """Forget a library (config only; nothing on disk or in games.json changes)"""
        self.config["libraries"] = [lib for lib in self.config["libraries"] if lib["id"] != library_id]
        self.save_config()
    
    def get_full_path(self, game):
        """Construct full path from relative path and library"""
        return self.path_manager.get_full_path(
            game.launch_path,
            self.config["libraries"],
            game.library_id
        )
    
    def is_executable(self, path):
        """Check if file is an executable based on platform and extension"""
        path_obj = Path(path)
        return self.path_manager.is_executable(path_obj)
