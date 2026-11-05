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
from pathlib import Path

from engine import messages
from engine.folders import library_label
from models import Game
from path_manager import PathManager


class LibraryError(Exception):
    """A library change that can't be made; the message is plain words for the player."""


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
        self.load_config()
        self.load_games()
    
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
                "tree_expansion": {},
                "tree_selections": [],
                "tree_filters": ["platform", "genre", "developer", "year"]
            }
        }
        
        upgraded = False
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    loaded = json.load(f)
                    # Merge with defaults to ensure all keys exist
                    for key in default_config:
                        if key not in loaded:
                            loaded[key] = default_config[key]
                    if "SavedState" in loaded:
                        for state_key in default_config["SavedState"]:
                            if state_key not in loaded["SavedState"]:
                                loaded["SavedState"][state_key] = default_config["SavedState"][state_key]
                    # The only old-file handling: ids are assigned once and then saved, so they stay stable
                    upgraded = loaded.pop("exceptions", None) is not None
                    for lib in loaded["libraries"]:
                        upgraded |= lib.pop("name", None) is not None
                        if "id" not in lib:
                            lib["id"] = secrets.token_hex(6)
                            upgraded = True
                    self.config = loaded
            except:
                self.config = default_config
                upgraded = False
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
        """Save configuration to JSON file"""
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)

    def load_games(self):
        """Load games from JSON file"""
        if self.games_file.exists():
            try:
                with open(self.games_file, 'r') as f:
                    data = json.load(f)
                    self.games = [Game.from_dict(g) for g in data]
            except:
                self.games = []
        else:
            self.games = []
    
    def save_games(self):
        """Save games to JSON file"""
        with open(self.games_file, 'w') as f:
            json.dump([g.to_dict() for g in self.games], f, indent=2)

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
