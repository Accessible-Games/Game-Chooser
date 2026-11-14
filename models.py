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

from dataclasses import dataclass, fields


def is_web_path(path: str) -> bool:
    return path.lower().startswith(("http://", "https://"))


@dataclass(eq=False)  # eq=False: a game equals only itself (rows find their game with `is`, lists remove by identity)
class Game:
    title: str = ""
    genre: str = ""
    developer: str = ""
    year: str = ""
    platforms: list = None
    launch_path: str = ""
    library_id: str = ""
    catalog_id: str = ""
    installed_source: dict = None
    installed_extras: list = None

    def __post_init__(self):
        self.platforms = self.platforms or []

    @property
    def is_web(self):
        return is_web_path(self.launch_path)

    def to_dict(self):
        """Every field, in order; catalog_id only when set, installed_source and installed_extras only when not None"""
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        if not self.catalog_id:
            del data["catalog_id"]
        return {k: v for k, v in data.items() if v is not None or not k.startswith("installed_")}

    @classmethod
    def from_dict(cls, data):
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})  # a row that is no object raises
