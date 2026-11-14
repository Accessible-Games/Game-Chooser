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

from engine.pricing import http_url

class ValidationService:
    """Centralized validation logic for game data."""

    @staticmethod
    def validate_title(title):
        """
        Validate a game title.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not title or not title.strip():
            return False, "Title cannot be empty"

        if len(title) > 255:
            return False, "Title too long (max 255 characters)"

        return True, None

    @staticmethod
    def validate_url(url):
        """
        Validate a URL for web games: the catalog's own test of a web address (engine.pricing.http_url).

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not url:
            return False, "URL cannot be empty"
        if http_url(url) is None:
            return False, "Invalid URL format"

        return True, None

    @staticmethod
    def validate_path(path, must_exist=True):
        """
        Validate a file path.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not path:
            return False, "Path cannot be empty"

        try:
            p = Path(path)

            if must_exist and not p.exists():
                return False, f"Path does not exist: {path}"

            if must_exist and not p.is_file():
                return False, f"Path is not a file: {path}"

            return True, None

        except Exception as e:
            return False, f"Invalid path: {str(e)}"

    @staticmethod
    def validate_year(year):
        """
        Validate a year value.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not year:
            return True, None  # Year is optional

        try:
            year_int = int(year)
            if year_int < 1000 or year_int > 9999:
                return False, "Year must be between 1000 and 9999"
            return True, None
        except ValueError:
            return False, "Year must be a number"