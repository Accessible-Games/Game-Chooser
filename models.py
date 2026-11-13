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
Data models for Game Chooser application
"""

from typing import List, Dict, Any


class Game:
    """Represents a game in the library"""
    def __init__(self, title="", genre="", developer="", year="",
                 platforms=None, launch_path="", library_id="",
                 catalog_id="", installed_source=None, installed_extras=None):
        self.title = title
        self.genre = genre
        self.developer = developer
        self.year = year
        self.platforms = platforms or []
        self.launch_path = launch_path
        self.library_id = library_id
        self.catalog_id = catalog_id
        self.installed_source = installed_source
        self.installed_extras = installed_extras

    @property
    def is_web(self):
        return self.launch_path.lower().startswith(("http://", "https://"))

    def to_dict(self):
        data = {
            "title": self.title,
            "genre": self.genre,
            "developer": self.developer,
            "year": self.year,
            "platforms": self.platforms,
            "launch_path": self.launch_path,
            "library_id": self.library_id
        }
        if self.catalog_id:
            data["catalog_id"] = self.catalog_id
        if self.installed_source is not None:
            data["installed_source"] = self.installed_source
        if self.installed_extras is not None:
            data["installed_extras"] = self.installed_extras
        return data
    
    @classmethod
    def from_dict(cls, data):
        return cls(
            title=data.get("title", ""),
            genre=data.get("genre", ""),
            developer=data.get("developer", ""),
            year=data.get("year", ""),
            platforms=data.get("platforms", []),
            launch_path=data.get("launch_path", ""),
            library_id=data.get("library_id", ""),
            catalog_id=data.get("catalog_id", ""),
            installed_source=data.get("installed_source"),
            installed_extras=data.get("installed_extras")
        )