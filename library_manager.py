#!/usr/bin/env python3
"""
Game library management for Game Chooser application
"""

import json
import os
import sys
import platform
import threading
import fnmatch
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

from models import Game
from exception_manager import ExceptionManager
from path_manager import PathManager


class GameLibraryManager:
    """Handles all game library operations and data management"""
    
    # Constants for scanning behavior
    MAX_SCAN_DEPTH = 10
    VALID_GAME_NAMES = ["game", "launch", "play", "start", "run"]
    
    def __init__(self):
        self.games = []
        self.config = {}
        self.app_dir = Path(os.path.dirname(os.path.abspath(sys.argv[0])))
        self.games_file = self.app_dir / "games.json"
        self.config_file = self.get_config_path()
        self.exception_manager = ExceptionManager()
        self.path_manager = PathManager()
        self.load_config()
        self.load_games()
        self.cleanConfigs()
        self._last_auto_exception_count = 0
    
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
            "exceptions": [],
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
                "tree_selections": []
            }
        }
        
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
                    self.config = loaded
            except:
                self.config = default_config
        else:
            self.config = default_config
            self.save_config()
    
    def save_config(self):
        """Save configuration to JSON file"""
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)

    def _normalize_exception_entry(self, entry: str) -> str:
        return self.path_manager.normalize(entry)

    def _normalize_for_match(self, entry: str) -> str:
        return self._normalize_exception_entry(entry).lower()

    def _is_path_exception(self, rel_path: str) -> bool:
        """Check if path matches user exceptions."""
        return self.exception_manager.is_user_exception(
            rel_path,
            self.config.get("exceptions", [])
        )

    def _add_exception_entry(self, entry: str) -> bool:
        normalized = self._normalize_exception_entry(entry)
        candidate_lower = normalized.lower()

        for existing in self.config["exceptions"]:
            existing_norm = self._normalize_exception_entry(existing)
            existing_lower = existing_norm.lower()
            if existing_lower == candidate_lower:
                return False
            if fnmatch.fnmatch(candidate_lower, existing_lower):
                return False

        self.config["exceptions"].append(normalized)
        return True

    def _build_keyword_pattern(self, keyword: str, suffix: str) -> str:
        keyword = keyword.lower()
        suffix = suffix.lower()
        if suffix:
            return f"*{keyword}*{suffix}"
        return f"*{keyword}*"

    def _build_filename_pattern(self, name: str) -> str:
        return f"*{name.lower()}"

    def _generate_auto_exception_patterns(self, item: Path) -> List[str]:
        patterns: List[str] = []
        name = item.name
        suffix = item.suffix.lower()
        stem = item.stem.lower()

        def add_pattern(pattern: Optional[str]):
            if pattern and pattern not in patterns:
                patterns.append(pattern)

        # Check exact stem matches
        if stem in self.AUTO_EXCEPTION_EXACT_STEMS:
            add_pattern(self._build_filename_pattern(name))

        # Check batch file specific stems
        if suffix in {'.bat', '.cmd'} and stem in self.AUTO_EXCEPTION_BATCH_STEMS:
            add_pattern(self._build_filename_pattern(name))

        # Check keyword matches within the stem
        for keyword in self.AUTO_EXCEPTION_KEYWORDS:
            if keyword in stem:
                add_pattern(self._build_keyword_pattern(keyword, suffix))

        # Check prefix matches
        for prefix in self.AUTO_EXCEPTION_PREFIXES:
            if stem == prefix:
                add_pattern(self._build_keyword_pattern(prefix, suffix))
            elif stem.startswith(f"{prefix}-") or stem.startswith(f"{prefix}_"):
                add_pattern(self._build_keyword_pattern(prefix, suffix))
            elif stem.startswith(prefix) and prefix in {"git"}:
                add_pattern(self._build_keyword_pattern(prefix, suffix))

        # Check suffix patterns (e.g., filename ends with -setup, -installer, etc.)
        for exception_suffix in self.AUTO_EXCEPTION_SUFFIXES:
            if stem.endswith(exception_suffix):
                # Create pattern that matches the suffix
                add_pattern(f"*{exception_suffix}{suffix}")

        # Fallback for batch files that don't match valid game names
        if not patterns and suffix in {'.bat', '.cmd'} and stem not in self.VALID_GAME_NAMES:
            add_pattern(self._build_filename_pattern(name))

        return patterns

    def _should_auto_exclude(self, item: Path) -> bool:
        """Check if path should be auto-excluded."""
        return self.exception_manager.should_auto_exclude(item)

    def add_to_exceptions(self, game):
        """Add a game's launch path to exceptions when user deletes it"""
        if game and game.launch_path:
            # launch_path is already library-relative, use it directly
            normalized_path = self._normalize_exception_entry(game.launch_path)
            if self._add_exception_entry(normalized_path):
                self.save_config()
    
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

    def cleanConfigs(self, progress_callback=None):
        """Clean configuration by removing games with paths in exceptions and redundant exceptions.

        Args:
            progress_callback: Optional callback for progress updates
        """
        games_changed = False
        exceptions_changed = False

        if progress_callback:
            progress_callback("Cleaning configuration...", 0, len(self.games))

        # Remove games with paths matching exceptions
        original_game_count = len(self.games)
        games_to_remove = []

        for game in self.games:
            if game.launch_path and self._is_path_exception(game.launch_path):
                games_to_remove.append(game)

        if games_to_remove:
            for game in games_to_remove:
                self.games.remove(game)
            games_changed = True
            if progress_callback:
                progress_callback(f"Removed {len(games_to_remove)} games matching exceptions", 0, len(self.games))

        # Remove redundant file exceptions covered by folder exceptions
        exceptions = self.config.get("exceptions", [])
        folder_exceptions = []
        file_exceptions = []

        # Separate folder and file exceptions
        for exc in exceptions:
            if exc.endswith('/'):
                folder_exceptions.append(exc)
            else:
                file_exceptions.append(exc)

        # Find file exceptions that are covered by folder exceptions
        redundant_exceptions = []
        for file_exc in file_exceptions:
            for folder_exc in folder_exceptions:
                folder_path = folder_exc.rstrip('/')
                if file_exc.startswith(folder_path + '/'):
                    redundant_exceptions.append(file_exc)
                    break

        # Remove redundant exceptions
        if redundant_exceptions:
            for exc in redundant_exceptions:
                self.config["exceptions"].remove(exc)
            exceptions_changed = True
            if progress_callback:
                progress_callback(f"Removed {len(redundant_exceptions)} redundant exceptions", 0, len(self.games))

        # Save changes if any were made
        if games_changed:
            self.save_games()
        if exceptions_changed:
            self.save_config()

        if progress_callback and (games_changed or exceptions_changed):
            progress_callback("Configuration cleaning completed", 0, len(self.games))

    def get_library_by_name(self, name):
        """Get library path by name"""
        for lib in self.config["libraries"]:
            if lib["name"] == name:
                return lib["path"]
        return None
    
    def get_full_path(self, game):
        """Construct full path from relative path and library"""
        return self.path_manager.get_full_path(
            game.launch_path,
            self.config["libraries"],
            game.library_name
        )
    
    def is_executable(self, path):
        """Check if file is an executable based on platform and extension"""
        path_obj = Path(path)
        return self.path_manager.is_executable(path_obj)
    
    def is_valid_game_executable(self, path):
        """Check if executable matches valid game patterns"""
        name = Path(path).stem.lower()
        parent_name = Path(path).parent.name.lower()
        
        valid_names = self.VALID_GAME_NAMES + [parent_name]
        return name in valid_names
    
    def _validate_libraries(self):
        """Validate library paths and separate valid from missing ones
        
        Returns:
            tuple: (valid_libraries, missing_libraries)
        """
        valid_libraries = []
        missing_libraries = []
        
        for lib in self.config["libraries"]:
            if lib["name"] == "manual":
                valid_libraries.append(lib)  # Always keep manual library
                continue
                
            if not Path(lib["path"]).exists():
                missing_libraries.append(lib)
            else:
                valid_libraries.append(lib)
        
        return valid_libraries, missing_libraries
    
    def _remove_missing_libraries(self, missing_libraries):
        """Remove missing libraries from config and return list of removed libraries
        
        Args:
            missing_libraries: List of missing library configs
            
        Returns:
            list: List of removed library configs
        """
        if not missing_libraries:
            return []
            
        removed_libraries = []
        print(f"Removing {len(missing_libraries)} missing library path(s) from config:")
        for lib in missing_libraries:
            print(f"  - {lib['name']}: {lib['path']}")
            removed_libraries.append(lib)
        
        # Update config to only contain valid libraries
        valid_libraries, _ = self._validate_libraries()
        self.config["libraries"] = [lib for lib in self.config["libraries"] 
                                   if lib not in missing_libraries]
        self.save_config()
        
        return removed_libraries
    
    def _validate_existing_games(self, valid_library_names, cancel_check=None):
        """Validate existing games and return only those that still exist
        
        Args:
            valid_library_names: Set of valid library names
            cancel_check: Optional function to check for cancellation
            
        Returns:
            list: List of validated games that still exist
        """
        validated_games = []
        
        for game in self.games:
            if cancel_check and cancel_check():
                break
                
            # Always keep web games
            if game.launch_path.startswith("http"):
                validated_games.append(game)
                continue
            
            # Always keep user-managed games (they manage their own paths)
            if game.library_name == "manual" or game.library_name == "":
                validated_games.append(game)
                continue
            
            # Only keep games from libraries that still exist
            if game.library_name in valid_library_names:
                full_path = self.get_full_path(game)
                if full_path and Path(full_path).exists() and self.is_executable(full_path):
                    validated_games.append(game)
        
        return validated_games
    
    def _build_known_game_dirs(self, validated_games):
        """Build set of known game directories from validated games
        
        Args:
            validated_games: List of validated games
            
        Returns:
            set: Set of directory paths that contain known games
        """
        known_game_dirs = set()
        
        for game in validated_games:
            if not game.launch_path.startswith("http") and game.library_name != "manual" and game.library_name != "":
                try:
                    full_path = self.get_full_path(game)
                    if full_path:
                        game_dir = str(Path(full_path).parent)
                        known_game_dirs.add(game_dir)
                except:
                    pass  # Skip if path processing fails
        
        return known_game_dirs
    
    def scan_library(self, library_path, library_name, known_game_dirs=None, max_depth=None,
                     progress_callback=None, cancel_check=None):
        """Recursively scan a library path for games.

        Args:
            library_path: Path to library directory to scan
            library_name: Name of the library
            known_game_dirs: Optional set of directories to skip (for incremental scanning)
            max_depth: Maximum scan depth
            progress_callback: Optional progress callback function
            cancel_check: Optional cancellation check function

        Returns:
            tuple[list[Game], int]: Newly discovered games and number of auto exceptions added.
        """
        if max_depth is None:
            max_depth = self.MAX_SCAN_DEPTH
        if known_game_dirs is None:
            known_game_dirs = set()
            
        # Check if library path exists
        library_path_obj = Path(library_path)
        if not library_path_obj.exists():
            print(f"Warning: Library path '{library_path}' does not exist. Skipping scan.")
            return [], []
        
        if not library_path_obj.is_dir():
            print(f"Warning: Library path '{library_path}' is not a directory. Skipping scan.")
            return [], []

        found_games = []
        directories_to_scan = []
        auto_exceptions_added = 0

        # First pass: collect all directories to get total count for progress
        if progress_callback:
            def collect_directories(path, depth=0):
                if depth > max_depth or (cancel_check and cancel_check()):
                    return
                try:
                    for item in Path(path).iterdir():
                        if item.name.startswith('.') or item.is_symlink():
                            continue
                        if item.is_dir():
                            # Skip .app bundles on macOS - they're executables, not folders to scan
                            if item.suffix.lower() == '.app':
                                continue
                            # Check if this directory is excluded by folder exceptions
                            rel_path = item.relative_to(Path(library_path))
                            rel_str = self.path_manager.normalize(rel_path)
                            if self._is_path_exception(rel_str):
                                continue
                            # Only add to scan list if not a known game directory (for incremental scanning)
                            if str(item) not in known_game_dirs:
                                directories_to_scan.append(str(item))
                                collect_directories(item, depth + 1)
                except (PermissionError, OSError):
                    pass
            collect_directories(library_path)

        directories_processed = 0

        def scan_recursive(path, depth=0):
            nonlocal directories_processed, auto_exceptions_added

            if depth > max_depth or (cancel_check and cancel_check()):
                return

            # SKIP if this directory already contains a known game (incremental scanning)
            if str(path) in known_game_dirs:
                return
            
            # Update progress
            if progress_callback and directories_to_scan:
                progress = (directories_processed / len(directories_to_scan)) * 100
                progress_callback(library_name, progress, len(found_games))
                directories_processed += 1
            
            try:
                # Collect executables in this directory (also handles auto-exception counting)
                executables, exceptions_added = self._collect_executables_with_exceptions(Path(path), library_path)
                auto_exceptions_added += exceptions_added

                if executables:
                    # This directory has games
                    directory_name = Path(path).name

                    # Create games for all executables in directory
                    for exe_path, rel_str in executables:
                        # Check for cancellation
                        if cancel_check and cancel_check():
                            return

                        # Check if game already exists
                        existing = None
                        for g in found_games:
                            if g.launch_path == rel_str:
                                existing = g
                                break

                        if existing:
                            # Update platforms if needed
                            import platform
                            platform_name = "Windows" if platform.system() == "Windows" else "macOS"
                            if platform_name not in existing.platforms:
                                existing.platforms.append(platform_name)
                        else:
                            # Create new game
                            game = self._create_game_from_executable(
                                exe_path, rel_str, library_name,
                                directory_name, len(executables) > 1
                            )
                            found_games.append(game)
                
                # Continue recursing into subdirectories
                for item in Path(path).iterdir():
                    # Check for cancellation
                    if cancel_check and cancel_check():
                        return
                        
                    if item.name.startswith('.'):
                        continue
                    if item.is_symlink():
                        continue
                    if item.is_dir():
                        # Skip .app bundles on macOS - they're executables, not folders to scan
                        if item.suffix.lower() == '.app':
                            continue
                        # Check if this directory is excluded by folder exceptions
                        rel_path = item.relative_to(Path(library_path))
                        rel_str = self.path_manager.normalize(rel_path)
                        if self._is_path_exception(rel_str):
                            continue
                        # Only recurse if not a known game directory (for incremental scanning)
                        if str(item) not in known_game_dirs:
                            scan_recursive(item, depth + 1)
            
            except PermissionError:
                # Handle permission errors
                raise PermissionError(f"Permission denied: {path}")
        
        scan_recursive(library_path)
        return found_games, auto_exceptions_added

    def _collect_executables_with_exceptions(self, directory_path, library_path):
        """
        Collect all valid executables in a directory and track auto-exceptions.

        Returns:
            Tuple of (executables_list, exceptions_added_count)
        """
        executables = []
        exceptions_added = 0

        for item in directory_path.iterdir():
            if item.name.startswith('.'):
                continue
            if item.is_symlink():
                continue
            # Skip directories unless they are .app bundles on macOS
            if not item.is_file() and not (item.suffix.lower() == '.app' and item.is_dir()):
                continue

            # Check if it's an executable
            if not self.is_executable(str(item)):
                continue

            # Get relative path
            rel_path = item.relative_to(Path(library_path))
            rel_str = self.path_manager.normalize(rel_path)

            # Check exceptions
            if self._is_path_exception(rel_str):
                continue

            # Check auto-exclusions
            if self._should_auto_exclude(item):
                # Add to exceptions if not already there
                if self._add_exception_entry(rel_str):
                    exceptions_added += 1
                continue

            executables.append((item, rel_str))

        return executables, exceptions_added

    def _select_best_executable(self, executables, directory_name):
        """
        Select the best executable from a list based on preferences.

        Returns:
            Path to selected executable or None
        """
        if not executables:
            return None

        exe_paths = [exe[0] for exe in executables]

        # First preference: game/launch/play named executables
        for preferred in ['game', 'launch', 'play']:
            for exe_path in exe_paths:
                if exe_path.stem.lower() == preferred:
                    return exe_path

        # Second preference: executable matching directory name
        dir_lower = directory_name.lower()
        for exe_path in exe_paths:
            if exe_path.stem.lower() == dir_lower:
                return exe_path

        # Default: first executable
        return exe_paths[0]

    def _create_game_from_executable(self, exe_path, rel_path_str, library_name, directory_name, multiple_exes):
        """
        Create a Game object from an executable.

        Args:
            exe_path: Path to executable
            rel_path_str: Library-relative path string
            library_name: Name of the library
            directory_name: Name of the game directory
            multiple_exes: Whether there are multiple executables in this directory

        Returns:
            Game object
        """
        # Create title
        if multiple_exes:
            title = f"{directory_name} ({exe_path.stem})"
        else:
            title = directory_name

        # Determine platform
        import platform
        system = platform.system()
        platform_name = "Windows" if system == "Windows" else "macOS"

        from models import Game
        return Game(
            title=title,
            platforms=[platform_name],
            launch_path=rel_path_str,
            library_name=library_name
        )

    def _merge_games(self, existing_games, new_games, cancel_check=None):
        """Merge new games into existing games list, updating platforms if needed."""
        for new_game in new_games:
            if cancel_check and cancel_check():
                return False

            # Find existing game with same launch path
            existing = None
            for game in existing_games:
                if game.launch_path == new_game.launch_path:
                    existing = game
                    break

            if existing:
                # Update platforms if needed
                for platform in new_game.platforms:
                    if platform not in existing.platforms:
                        existing.platforms.append(platform)
            else:
                existing_games.append(new_game)

        return True

    def validate_and_scan(self, libraries_to_scan=None, progress_callback=None, cancel_check=None):
        """
        Unified scanning method that intelligently handles all scanning scenarios.

        Args:
            libraries_to_scan: Optional set of library names to scan. If None, scans all.
            progress_callback: Optional callback for progress updates
            cancel_check: Optional callback to check for cancellation

        Returns:
            List of removed libraries (empty if none removed)
        """
        # Step 0: Clean configuration before scanning
        self.cleanConfigs(progress_callback)

        # Step 1: Validate libraries and handle missing ones
        valid_libraries, missing_libraries = self._validate_libraries()
        removed_libraries = self._remove_missing_libraries(missing_libraries)

        if removed_libraries:
            valid_library_names = {lib["name"] for lib in valid_libraries}
            validated_games = self._validate_existing_games(valid_library_names, cancel_check)
            self.games = validated_games
            self.save_games()
            self._last_auto_exception_count = 0
            return removed_libraries

        # Step 2: Validate existing games
        valid_library_names = {lib["name"] for lib in valid_libraries}
        validated_games = self._validate_existing_games(valid_library_names, cancel_check)

        if cancel_check and cancel_check():
            self._last_auto_exception_count = 0
            return []

        # Step 3: Determine scanning strategy
        # If games.json doesn't exist, do full scan (no optimization)
        # Otherwise, build known_game_dirs for incremental scanning
        known_game_dirs = None
        if self.games_file.exists() and len(validated_games) > 0:
            known_game_dirs = self._build_known_game_dirs(validated_games)

        # Step 4: Filter libraries to scan
        if libraries_to_scan is not None:
            libraries_to_process = [lib for lib in valid_libraries if lib["name"] in libraries_to_scan]
        else:
            libraries_to_process = valid_libraries

        # Step 5: Scan libraries
        total_auto_exceptions = 0
        for lib in libraries_to_process:
            if cancel_check and cancel_check():
                self._last_auto_exception_count = total_auto_exceptions
                return []

            if lib["name"] == "manual":
                continue  # Skip manual library

            try:
                # Determine if this library should use incremental scanning
                use_incremental = known_game_dirs is not None

                # If specific libraries were requested and this is a new one, don't use incremental
                if libraries_to_scan and lib["name"] in libraries_to_scan:
                    # Check if this library has any existing games
                    has_existing_games = any(g.library_name == lib["name"] for g in validated_games)
                    if not has_existing_games:
                        use_incremental = False

                new_games, added_exceptions = self.scan_library(
                    lib["path"],
                    lib["name"],
                    known_game_dirs=known_game_dirs if use_incremental else None,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check
                )
                total_auto_exceptions += added_exceptions

                # Merge new games
                if not self._merge_games(validated_games, new_games, cancel_check):
                    self._last_auto_exception_count = total_auto_exceptions
                    return []  # Cancelled during merge

            except PermissionError as e:
                raise e

        # Step 6: Save results
        self.games = validated_games
        self.save_games()
        self.save_config()
        self._last_auto_exception_count = total_auto_exceptions

        return []


    def scan_with_dialog(self, parent_window, libraries_to_scan=None):
        """
        Unified dialog wrapper for scanning with progress display.

        Args:
            parent_window: Parent window for the dialog
            libraries_to_scan: Optional set of library names to scan

        Returns:
            Tuple of (exceptions_count, removed_libraries) or None if cancelled
        """
        from dialogs import ScanProgressDialog

        # Count libraries (excluding manual)
        library_count = sum(1 for lib in self.config["libraries"] if lib["name"] != "manual")

        # If no libraries with UI needed, run without dialog
        if library_count == 0:
            removed_libraries = self.validate_and_scan(libraries_to_scan)
            return (self._last_auto_exception_count, removed_libraries)

        # Create progress dialog
        progress_dialog = ScanProgressDialog(parent_window)
        progress_dialog.set_library_count(library_count)

        # Result storage for background thread
        scan_result = {
            "exceptions_count": 0,
            "removed_libraries": [],
            "error": None,
            "cancelled": False
        }

        def background_scan():
            """Background thread for scanning"""
            try:
                def progress_callback(library_name, progress, games_found):
                    if not progress_dialog.cancelled:
                        progress_dialog.update_progress(library_name, progress, games_found)

                def cancel_check():
                    return progress_dialog.cancelled

                removed_libraries = self.validate_and_scan(
                    libraries_to_scan, progress_callback, cancel_check
                )
                scan_result["exceptions_count"] = self._last_auto_exception_count
                scan_result["removed_libraries"] = removed_libraries
                scan_result["cancelled"] = progress_dialog.cancelled

            except Exception as e:
                scan_result["error"] = e
            finally:
                # Handle dialog closing
                if not scan_result["cancelled"]:
                    if scan_result["removed_libraries"]:
                        import wx
                        wx.CallAfter(progress_dialog.EndModal, wx.ID_OK)
                    else:
                        progress_dialog.finish_scan(len(self.games), scan_result["exceptions_count"])
                else:
                    import wx
                    wx.CallAfter(progress_dialog.EndModal, wx.ID_CANCEL)

        # Start background thread
        import threading
        thread = threading.Thread(target=background_scan, daemon=True)
        thread.start()

        # Show dialog
        result = progress_dialog.ShowModal()
        progress_dialog.Destroy()

        # Handle results
        if scan_result["error"]:
            if isinstance(scan_result["error"], PermissionError):
                import wx
                wx.MessageBox(
                    f"Permission denied accessing:\n{scan_result['error']}",
                    "Permission Error",
                    wx.OK | wx.ICON_ERROR
                )
            else:
                raise scan_result["error"]

        if scan_result["cancelled"]:
            return None

        return (scan_result["exceptions_count"], scan_result["removed_libraries"])

