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

"""Add Game's checks of what the player typed. Each gives (is_valid, error_message)."""

from engine.pricing import http_url


def validate_title(title):
    """`title` is already stripped"""
    if not title:
        return False, "Title cannot be empty"
    if len(title) > 255:
        return False, "Title too long (max 255 characters)"
    return True, None


def validate_url(url):
    """A web game's address: the catalog's own test of a web address (engine.pricing.http_url)"""
    if not url:
        return False, "URL cannot be empty"
    if http_url(url) is None:
        return False, "Invalid URL format"
    return True, None


def validate_year(year):
    """`year` is the text typed in the Year field; empty means no year"""
    if not year:
        return True, None
    try:
        year_int = int(year)
    except ValueError:
        return False, "Year must be a number"
    if year_int < 1000 or year_int > 9999:
        return False, "Year must be between 1000 and 9999"
    return True, None
