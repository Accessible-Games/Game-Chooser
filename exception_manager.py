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

import re
import fnmatch
from pathlib import Path


class ExceptionManager:
    """Manages auto-exception patterns and user exceptions for game scanning."""

    # Simplified auto-exception keywords - only relevant for Windows/Mac games
    # Keywords match anywhere in the filename (substring matching, not word boundaries)
    AUTO_EXCEPTION_KEYWORDS = [
        # Installation and setup
        "setup", "install", "installer", "installshield", "unins", "uninstall",
        "update", "updater", "upgrade", "patch", "patcher",
        # Configuration
        "config", "configure", "settings", "configtool",
        # System redistributables
        "vcredist", "directx", "dxsetup", "runtime", "dotnet", "redist",
        # Documentation
        "readme", "license", "credit", "credits", "docs",
        # Anti-piracy detection
        "keygen", "crack", "trainer", "cheat",
        # User-provided keywords
        "registration", "register", "server", "mapuploader", "level editor",
        "uploader", "leveltool", "pack_creater", "scoresystem",
        "gamemenu", "(1)", "upnpc"
    ]

    AUTO_EXCEPTION_EXACT_STEMS = {
        # Common uninstaller names
        "unins000", "unins001", "unins002",
        # System redistributables
        "dxsetup", "vcredist", "vcredist_x86", "vcredist_x64",
        # Common installer names
        "setup", "installer", "install",
        # Game utilities that are not games
        "mapmaker", "myaccount", "joystick",
        # User-provided exact stems
        "1oom_gfxconv", "1oom_lbxedit", "1oom_pbxdump", "1oom_pbxmake",
        "1oom_saveconv", "unzip", "nvda", "ag_say", "cwsdpmi", "lha",
        "perl", "qlaunch", "quake", "zqds", "zquake-gl", "zquake-vidnull",
        "up", "checkup", "sayit", "rsb", "gthelp", "golfcourse-maker",
        "lwparse", "lwwb2000", "speechconfig", "w9xpopen", "mazecreate",
        "monopolyboardmaker", "pmthelp", "signtool", "scwhelp", "sodhelp",
        "oggenc2", "sbhhelp", "swr", "reg",
        "spacer", "reader", "waver", "encrypt", "elevate", "snowreg",
        "firstrun", "remove", "tbecore", "tools", "lame", "cpsdistr"
    }

    AUTO_EXCEPTION_PREFIXES = [
        "unins"  # For uninstaller variations
    ]

    AUTO_EXCEPTION_BATCH_STEMS = {
        "help", "readme", "change", "site", "emake"
    }

    AUTO_EXCEPTION_SUFFIXES = [
        # Common utility suffixes
        "setup", "-setup", "-installer", "-install", "-uninstall", "-unins",
        "-update", "-updater", "-upgrade", "-patch", "-patcher",
        "-config", "-configure", "-configurator", "-register", "-registration",
        "-checkup", "-checker", "-diagnostic", "-repair", "-cleanup",
        "-tool", "-utility", "-helper", "-manager", "-launcher",
        "-editor", "-viewer", "-monitor", "-scanner", "-tester",
        # Version/build suffixes
        "-debug", "-release", "-beta", "-alpha", "-demo", "-trial",
        "-lite", "-full", "-pro", "-premium", "-server", "-client",
        # Platform/architecture suffixes
        "-32bit", "-64bit", "-x86", "-x64", "-win32", "-win64",
        "-console", "-gui", "-cli", "-ui", "-api",
        # File/data suffixes
        "-data", "-backup", "-temp", "-tmp", "-cache", "-log",
        "-crash", "-dump", "-report", "-export", "-import"
    ]

    # Valid game names (from original library_manager.py)
    VALID_GAME_NAMES = ["game", "launch", "play", "start", "run"]

    def __init__(self):
        """Initialize exception manager with optimized lookup structures."""
        # Convert to sets for O(1) lookup
        self.keywords = set(self.AUTO_EXCEPTION_KEYWORDS)
        self.exact_stems = set(self.AUTO_EXCEPTION_EXACT_STEMS)
        self.suffixes = set(self.AUTO_EXCEPTION_SUFFIXES)
        self.prefixes = set(self.AUTO_EXCEPTION_PREFIXES)
        self.batch_stems = set(self.AUTO_EXCEPTION_BATCH_STEMS)
        self.valid_game_names = set(self.VALID_GAME_NAMES)

        # Compile regex patterns once for efficiency
        self.keyword_pattern = self._compile_keyword_pattern()

    def _compile_keyword_pattern(self):
        """Compile keyword patterns into a single regex for efficiency.

        Keywords match anywhere in the filename (substring matching).
        For example, 'server' will match 'server', 'gameserver', 'game-server', etc.
        """
        escaped = [re.escape(kw) for kw in self.keywords]
        # No word boundaries - match keywords anywhere as substrings
        pattern = r'(' + '|'.join(escaped) + r')'
        return re.compile(pattern, re.IGNORECASE)

    def should_auto_exclude(self, path):
        """
        Check if a path should be automatically excluded based on patterns.

        Args:
            path: Path object or string to check

        Returns:
            bool: True if path matches auto-exception patterns
        """
        if isinstance(path, str):
            path = Path(path)

        name = path.name
        suffix = path.suffix.lower()
        stem = path.stem.lower()

        # Check exact stem matches
        if stem in self.exact_stems:
            return True

        # Check stem without parentheses and numbers for variations like "oggenc2 (1)"
        stem_clean = re.sub(r'\s*\([^)]*\)', '', stem).strip()
        if stem_clean in self.exact_stems:
            return True

        # Check batch file specific stems
        if suffix in {'.bat', '.cmd'} and stem in self.batch_stems:
            return True

        # Check keyword matches within the stem (with word boundaries)
        if self.keyword_pattern.search(stem):
            return True

        # Check prefix matches
        for prefix in self.prefixes:
            if stem == prefix:
                return True
            elif stem.startswith(f"{prefix}-") or stem.startswith(f"{prefix}_"):
                return True
            elif stem.startswith(prefix) and prefix in {"git"}:
                return True

        # Check suffix patterns (e.g., filename ends with -setup, -installer, etc.)
        for exception_suffix in self.suffixes:
            if stem.endswith(exception_suffix):
                return True

        # Check additional specific patterns for utilities
        name_lower = name.lower()

        # Runtime libraries
        if stem.startswith('msvc') or stem.startswith('vbrun'):
            return True

        # Update/installer patterns
        if stem.startswith('update_'):
            return True

        # Uninstaller variations (catches "unins000 (1)", "uninst*" etc.)
        if stem.startswith('unins') or stem.startswith('uninst'):
            return True

        # Server pattern (e.g., "Server3000")
        if re.match(r'^server\d+$', stem):
            return True

        # Map maker pattern (with space)
        if 'map maker' in name_lower:
            return True

        # Additional installer/updater patterns as substrings for compound words
        if ('installer' in stem or 'updater' in stem or
            ('install' in stem and len(stem) > 7) or  # avoid matching "install" alone
            ('runtime' in stem)):
            return True

        # Fallback for batch files that don't match valid game names
        if suffix in {'.bat', '.cmd'}:
            # Allow if stem exactly matches valid game names
            if stem in self.valid_game_names:
                return False
            # Allow if stem starts with valid game name followed by space (compound launcher names)
            if any(stem.startswith(valid_name + ' ') for valid_name in self.valid_game_names):
                return False
            # Otherwise exclude batch file
            return True

        return False

    def is_user_exception(self, rel_path, exceptions_list):
        """
        Check if a path matches user-defined exceptions.

        Args:
            rel_path: Library-relative path to check
            exceptions_list: List of user exception patterns

        Returns:
            bool: True if path matches user exceptions
        """
        rel_str = str(rel_path).replace('\\', '/')

        for exception in exceptions_list:
            exception = exception.strip().replace('\\', '/')

            # Handle folder exceptions (ending with /)
            if exception.endswith('/'):
                # For folder exceptions, check if the path starts with the folder path
                folder_pattern = exception.rstrip('/')
                if rel_str.startswith(folder_pattern + '/') or rel_str == folder_pattern:
                    return True
            # Handle exact matches
            elif rel_str == exception:
                return True
            # Handle wildcard patterns
            elif '*' in exception:
                if fnmatch.fnmatch(rel_str, exception):
                    return True

        return False