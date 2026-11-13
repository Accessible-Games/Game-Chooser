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

from pathlib import Path

class PathManager:
    """Centralized path operations and normalization."""

    @staticmethod
    def get_full_path(launch_path, library_paths, library_id):
        """
        Convert a library-relative path to absolute.

        Args:
            launch_path: Library-relative path
            library_paths: List of library path dictionaries
            library_id: Id of the library containing the game ("" for an absolute path)

        Returns:
            Absolute path as string or None if library not found
        """
        if not library_id:
            return launch_path

        # Find library
        for lib in library_paths:
            if lib["id"] == library_id:
                lib_path = Path(lib["path"])
                full_path = lib_path / launch_path
                return str(full_path)

        return None

    @staticmethod
    def is_executable(path):
        """
        Check if a path is an executable game file.

        Only detects Windows (.exe, .bat) and macOS (.app) games.
        Linux support has been removed for simplicity.

        Args:
            path: Path object to check

        Returns:
            bool: True if file is a game executable
        """
        # macOS .app bundles (directories)
        if path.suffix.lower() == '.app' and path.is_dir():
            return True

        # Windows executables and batch files only
        if path.is_file() and path.suffix.lower() in ['.exe', '.bat']:
            return True

        return False