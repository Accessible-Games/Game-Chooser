#!/usr/bin/env python3
"""
Game library management for Game Chooser application
"""

import json
import os
import sys
import platform
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

from models import Game


class GameLibraryManager:
    """Handles all game library operations and data management"""
    
    # Constants for scanning behavior
    MAX_SCAN_DEPTH = 10
    VALID_GAME_NAMES = ["game", "launch", "play"]
    
    def __init__(self):
        self.games = []
        self.config = {}
        self.app_dir = Path(os.path.dirname(os.path.abspath(sys.argv[0])))
        self.games_file = self.app_dir / "games.json"
        self.config_file = self.get_config_path()
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
        else:  # Linux/Unix
            config_dir = Path.home() / ".config" / "GameChooser"
        
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
                "tree_expansion": {}
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
    
    def add_to_exceptions(self, game):
        """Add a game's launch path to exceptions when user deletes it"""
        if game and game.launch_path:
            if game.launch_path not in self.config["exceptions"]:
                self.config["exceptions"].append(game.launch_path)
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
    
    def get_library_by_name(self, name):
        """Get library path by name"""
        for lib in self.config["libraries"]:
            if lib["name"] == name:
                return lib["path"]
        return None
    
    def get_full_path(self, game):
        """Construct full path from relative path and library"""
        if game.launch_path.startswith("http"):
            return game.launch_path
        
        # Handle manual games - return the direct path
        if game.library_name == "manual":
            return game.launch_path
        
        parts = Path(game.launch_path).parts
        if not parts:
            return None
        
        lib_name = parts[0]
        for lib in self.config["libraries"]:
            if lib["name"] == lib_name:
                full_path = Path(lib["path"]) / Path(*parts[1:])
                return str(full_path)
        
        return None
    
    def is_executable(self, path):
        """Check if file is an executable based on platform and extension"""
        path_obj = Path(path)
        system = platform.system()
        
        if system == "Windows":
            return path_obj.suffix.lower() in ['.exe', '.bat']
        elif system == "Darwin":  # macOS
            # Check for .app bundles (directories)
            if path_obj.suffix.lower() == '.app' and path_obj.is_dir():
                return True
            
            # Check for executable files with common game extensions
            if path_obj.suffix.lower() in ['.sh', '.command']:
                return True
            
            # Check if it's an executable file (has execute permission and is a regular file)
            try:
                if path_obj.is_file() and os.access(path, os.X_OK):
                    # Additional check: skip obvious non-game executables
                    name = path_obj.name.lower()
                    if any(skip in name for skip in ['uninstall', 'install', 'setup', 'update', 'crash', 'log']):
                        return False
                    return True
            except:
                return False
        else:  # Linux/Unix
            # Check for executable files with common extensions
            if path_obj.suffix.lower() in ['.sh', '.run']:
                return True
            
            # Check if it's an executable file
            try:
                if path_obj.is_file() and os.access(path, os.X_OK):
                    # Skip obvious non-game executables
                    name = path_obj.name.lower()
                    if any(skip in name for skip in ['uninstall', 'install', 'setup', 'update', 'crash', 'log']):
                        return False
                    return True
            except:
                return False
        
        return False
    
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
            
            # Always keep manual games (they manage their own paths)
            if game.library_name == "manual":
                validated_games.append(game)
                continue
            
            # Only keep games from libraries that still exist
            if game.library_name in valid_library_names:
                full_path = self.get_full_path(game)
                if full_path and Path(full_path).exists():
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
            if not game.launch_path.startswith("http") and game.library_name != "manual":
                try:
                    full_path = self.get_full_path(game)
                    if full_path:
                        game_dir = str(Path(full_path).parent)
                        known_game_dirs.add(game_dir)
                except:
                    pass  # Skip if path processing fails
        
        return known_game_dirs
    
    def scan_library(self, library_path, library_name, known_game_dirs=None, max_depth=None, progress_callback=None, cancel_check=None):
        """Recursively scan a library path for games
        
        Args:
            library_path: Path to library directory to scan
            library_name: Name of the library
            known_game_dirs: Optional set of directories to skip (for incremental scanning)
            max_depth: Maximum scan depth
            progress_callback: Optional progress callback function
            cancel_check: Optional cancellation check function
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
                            # Only add to scan list if not a known game directory (for incremental scanning)
                            if str(item) not in known_game_dirs:
                                directories_to_scan.append(str(item))
                                collect_directories(item, depth + 1)
                except (PermissionError, OSError):
                    pass
            collect_directories(library_path)
        
        directories_processed = 0
        
        def scan_recursive(path, depth=0):
            nonlocal directories_processed
            
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
                # First, collect all executables in each directory to handle duplicates
                directory_exes = {}
                
                for item in Path(path).iterdir():
                    # Check for cancellation
                    if cancel_check and cancel_check():
                        return
                        
                    # Skip hidden/system files and symlinks
                    if item.name.startswith('.'):
                        continue
                    if item.is_symlink():
                        continue
                    
                    if self.is_executable(str(item)):
                        # Build relative path
                        rel_path = item.relative_to(Path(library_path).parent)
                        rel_str = str(rel_path).replace(os.sep, '/')
                        
                        # Check if in exceptions
                        if rel_str in self.config["exceptions"]:
                            continue
                        
                        # Group by directory
                        dir_key = str(item.parent)
                        if dir_key not in directory_exes:
                            directory_exes[dir_key] = []
                        directory_exes[dir_key].append((item, rel_str))
                
                # Process each directory's executables - create a game for EACH executable
                for dir_path, exe_list in directory_exes.items():
                    # Check for cancellation
                    if cancel_check and cancel_check():
                        return
                        
                    if not exe_list:
                        continue
                    
                    # Create a game for EVERY executable found (not just one per directory)
                    for exe_item, exe_rel_str in exe_list:
                        # Check for cancellation
                        if cancel_check and cancel_check():
                            return
                            
                        # Create descriptive title - use directory name, or add executable name if multiple exes
                        base_title = exe_item.parent.name
                        if len(exe_list) > 1:
                            # Multiple executables in directory - add exe name to distinguish
                            exe_name = exe_item.stem  # filename without extension
                            title = f"{base_title} ({exe_name})"
                        else:
                            # Single executable - just use directory name
                            title = base_title
                            
                        system = platform.system()
                        plat = "Windows" if system == "Windows" else "macOS"
                        
                        # Check if game already exists
                        existing = None
                        for g in found_games:
                            if g.launch_path == exe_rel_str:
                                existing = g
                                break
                        
                        if existing:
                            if plat not in existing.platforms:
                                existing.platforms.append(plat)
                        else:
                            game = Game(
                                title=title,
                                platforms=[plat],
                                launch_path=exe_rel_str,
                                library_name=library_name
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
                        # Only recurse if not a known game directory (for incremental scanning)
                        if str(item) not in known_game_dirs:
                            scan_recursive(item, depth + 1)
            
            except PermissionError:
                # Handle permission errors
                raise PermissionError(f"Permission denied: {path}")
        
        scan_recursive(library_path)
        return found_games
    
    def validate_and_scan_all(self, progress_callback=None, cancel_check=None):
        """Validate existing games and scan for new ones"""
        
        # FIRST: Check for missing libraries and remove them from config
        valid_libraries, missing_libraries = self._validate_libraries()
        removed_libraries = self._remove_missing_libraries(missing_libraries)
        
        if removed_libraries:
            # Validate existing games to remove ones from missing libraries
            valid_library_names = {lib["name"] for lib in valid_libraries}
            validated_games = self._validate_existing_games(valid_library_names, cancel_check)
            
            # Save updated games list and return early - no need to scan anything
            self.games = validated_games
            self.save_games()
            return removed_libraries
        
        # SECOND: Validate existing games (only if no libraries were removed)
        valid_library_names = {lib["name"] for lib in valid_libraries}
        validated_games = self._validate_existing_games(valid_library_names, cancel_check)
        
        if cancel_check and cancel_check():
            return []
        
        # THIRD: Scan all valid libraries for new games (exclude manual library)
        for lib in valid_libraries:
            if cancel_check and cancel_check():
                return []  # Return early if cancelled
                
            if lib["name"] == "manual":
                continue  # Skip manual library from autodiscovery
                
            try:
                new_games = self.scan_library(
                    lib["path"], lib["name"], progress_callback=progress_callback, cancel_check=cancel_check
                )
                
                
                # Merge new games
                for new_game in new_games:
                    if cancel_check and cancel_check():
                        return []  # Return early if cancelled
                        
                    existing = None
                    for val_game in validated_games:
                        if val_game.launch_path == new_game.launch_path:
                            existing = val_game
                            break
                    
                    if existing:
                        # Update platforms if needed
                        for plat in new_game.platforms:
                            if plat not in existing.platforms:
                                existing.platforms.append(plat)
                    else:
                        validated_games.append(new_game)
            
            except PermissionError as e:
                # Will be handled by caller
                raise e
        
        self.games = validated_games
        self.save_games()
        self.save_config()
        
        return []  # No libraries removed in this case
    
    def validate_and_scan_incrementally(self, progress_callback=None, cancel_check=None):
        """Validate existing games and scan only new directories for faster startup"""
        
        # FIRST: Check for missing libraries and remove them from config
        valid_libraries, missing_libraries = self._validate_libraries()
        removed_libraries = self._remove_missing_libraries(missing_libraries)
        
        if removed_libraries:
            # Validate existing games to remove ones from missing libraries
            valid_library_names = {lib["name"] for lib in valid_libraries}
            validated_games = self._validate_existing_games(valid_library_names, cancel_check)
            
            # Save updated games list and return early - no need to scan anything
            self.games = validated_games
            self.save_games()
            return removed_libraries
        
        # SECOND: Validate existing games (remove missing ones)
        valid_library_names = {lib["name"] for lib in valid_libraries}
        validated_games = self._validate_existing_games(valid_library_names, cancel_check)
        
        if cancel_check and cancel_check():
            return []
        
        # THIRD: Build set of known game directories from validated games
        known_game_dirs = self._build_known_game_dirs(validated_games)
        
        # FOURTH: Scan for new games only (skip known directories)
        for lib in valid_libraries:
            if cancel_check and cancel_check():
                return []  # Return early if cancelled
                
            if lib["name"] == "manual":
                continue  # Skip manual library from autodiscovery
                
            try:
                new_games = self.scan_library(
                    lib["path"], lib["name"], known_game_dirs=known_game_dirs,
                    progress_callback=progress_callback, cancel_check=cancel_check
                )
                
                
                # Merge new games
                for new_game in new_games:
                    if cancel_check and cancel_check():
                        return []  # Return early if cancelled
                        
                    existing = None
                    for val_game in validated_games:
                        if val_game.launch_path == new_game.launch_path:
                            existing = val_game
                            break
                    
                    if existing:
                        # Update platforms if needed
                        for plat in new_game.platforms:
                            if plat not in existing.platforms:
                                existing.platforms.append(plat)
                    else:
                        validated_games.append(new_game)
            
            except PermissionError as e:
                # Will be handled by caller
                raise e
        
        self.games = validated_games
        self.save_games()
        self.save_config()
        
        return []  # No libraries removed in this case
    
    def validate_and_scan_all_with_dialog(self, parent_window):
        """Validate and scan all libraries with progress dialog"""
        # Import here to avoid circular dependency
        from dialogs import ScanProgressDialog
        
        # Count libraries excluding manual
        library_count = sum(1 for lib in self.config["libraries"] if lib["name"] != "manual")
        
        if library_count == 0:
            return self.validate_and_scan_all()
        
        # Create and show progress dialog
        progress_dialog = ScanProgressDialog(parent_window)
        progress_dialog.set_library_count(library_count)
        
        # Variables to store results from background thread
        scan_result = {"exceptions_count": 0, "removed_libraries": [], "error": None, "cancelled": False}
        
        def background_scan():
            """Background thread function for scanning"""
            try:
                def progress_callback(library_name, progress, games_found):
                    if not progress_dialog.cancelled:
                        progress_dialog.update_progress(library_name, progress, games_found)
                
                def cancel_check():
                    return progress_dialog.cancelled
                
                removed_libraries = self.validate_and_scan_all(progress_callback, cancel_check)
                scan_result["exceptions_count"] = 0
                scan_result["removed_libraries"] = removed_libraries
                scan_result["cancelled"] = progress_dialog.cancelled
                
            except Exception as e:
                scan_result["error"] = e
            finally:
                # Close the progress dialog appropriately
                if not scan_result["cancelled"]:
                    if scan_result["removed_libraries"]:
                        # Libraries were removed - close dialog immediately without completion message
                        # Let the main window show the missing library dialog first
                        import wx
                        wx.CallAfter(progress_dialog.EndModal, wx.ID_OK)
                    else:
                        # Normal completion - show completion message and auto-close
                        progress_dialog.finish_scan(len(self.games), scan_result["exceptions_count"])
                else:
                    # Scan was cancelled - close dialog immediately
                    import wx
                    wx.CallAfter(progress_dialog.EndModal, wx.ID_CANCEL)
        
        # Start background thread
        thread = threading.Thread(target=background_scan, daemon=True)
        thread.start()
        
        # Show dialog modally
        result = progress_dialog.ShowModal()
        progress_dialog.Destroy()
        
        # Wait for thread to finish if still running
        thread.join(timeout=1.0)
        
        # Handle results
        if scan_result["error"]:
            raise scan_result["error"]
        
        if scan_result["cancelled"]:
            return None  # Return None to indicate cancellation
            
        return scan_result["removed_libraries"]
    
    def validate_and_scan_targeted(self, new_library_names, progress_callback=None, cancel_check=None):
        """Validate existing games and scan only targeted libraries"""
        
        # FIRST: Check for missing libraries and remove them from config
        valid_libraries, missing_libraries = self._validate_libraries()
        removed_libraries = self._remove_missing_libraries(missing_libraries)
        
        if removed_libraries:
            # Validate existing games to remove ones from missing libraries
            valid_library_names = {lib["name"] for lib in valid_libraries}
            validated_games = self._validate_existing_games(valid_library_names, cancel_check)
            
            # Save updated games list and return early - no need to scan anything
            self.games = validated_games
            self.save_games()
            return removed_libraries
        
        # SECOND: Validate existing games (remove missing ones)
        valid_library_names = {lib["name"] for lib in valid_libraries}
        validated_games = self._validate_existing_games(valid_library_names, cancel_check)
        
        if cancel_check and cancel_check():
            return []
        
        # THIRD: Build set of known game directories from validated games
        known_game_dirs = self._build_known_game_dirs(validated_games)
        
        # FOURTH: Scan libraries (full scan for new ones, incremental for existing)
        for lib in valid_libraries:
            if cancel_check and cancel_check():
                return []  # Return early if cancelled
                
            if lib["name"] == "manual":
                continue  # Skip manual library from autodiscovery
                
            try:
                if lib["name"] in new_library_names:
                    # New library - use full scan to discover all games
                    new_games = self.scan_library(
                        lib["path"], lib["name"], progress_callback=progress_callback, cancel_check=cancel_check
                    )
                else:
                    # Existing library - use incremental scan (skip known game directories)
                    new_games = self.scan_library(
                        lib["path"], lib["name"], known_game_dirs=known_game_dirs, 
                        progress_callback=progress_callback, cancel_check=cancel_check
                    )
                
                
                # Merge new games
                for new_game in new_games:
                    if cancel_check and cancel_check():
                        return []  # Return early if cancelled
                        
                    existing = None
                    for val_game in validated_games:
                        if val_game.launch_path == new_game.launch_path:
                            existing = val_game
                            break
                    
                    if existing:
                        # Update platforms if needed
                        for plat in new_game.platforms:
                            if plat not in existing.platforms:
                                existing.platforms.append(plat)
                    else:
                        validated_games.append(new_game)
            
            except PermissionError as e:
                # Will be handled by caller
                raise e
        
        self.games = validated_games
        self.save_games()
        self.save_config()
        
        return []  # No libraries removed in this case
    
    def validate_and_scan_targeted_with_dialog(self, parent_window, new_library_names=None):
        """Validate existing games and scan targeted libraries with progress dialog"""
        if new_library_names is None:
            new_library_names = set()
        
        # Import here to avoid circular dependency
        from dialogs import ScanProgressDialog
        
        # Count libraries excluding manual
        library_count = sum(1 for lib in self.config["libraries"] if lib["name"] != "manual")
        
        if library_count == 0:
            return self.validate_and_scan_targeted(new_library_names)
        
        # Create and show progress dialog
        progress_dialog = ScanProgressDialog(parent_window)
        progress_dialog.set_library_count(library_count)
        
        # Use standard scanning text
        progress_dialog.status_text.SetLabel("Scanning for games...")
        
        # Variables to store results from background thread
        scan_result = {"exceptions_count": 0, "removed_libraries": [], "error": None, "cancelled": False}
        
        def background_scan():
            """Background thread function for targeted scanning"""
            try:
                def progress_callback(library_name, progress, games_found):
                    if not progress_dialog.cancelled:
                        progress_dialog.update_progress(library_name, progress, games_found)
                
                def cancel_check():
                    return progress_dialog.cancelled
                
                removed_libraries = self.validate_and_scan_targeted(new_library_names, progress_callback, cancel_check)
                scan_result["exceptions_count"] = 0
                scan_result["removed_libraries"] = removed_libraries
                scan_result["cancelled"] = progress_dialog.cancelled
                
            except Exception as e:
                scan_result["error"] = e
            finally:
                # Close the progress dialog appropriately
                if not scan_result["cancelled"]:
                    if scan_result["removed_libraries"]:
                        # Libraries were removed - close dialog immediately without completion message
                        # Let the main window show the missing library dialog first
                        import wx
                        wx.CallAfter(progress_dialog.EndModal, wx.ID_OK)
                    else:
                        # Normal completion - show completion message and auto-close
                        progress_dialog.finish_scan(len(self.games), scan_result["exceptions_count"])
                else:
                    # Scan was cancelled - close dialog immediately
                    import wx
                    wx.CallAfter(progress_dialog.EndModal, wx.ID_CANCEL)
        
        # Start background thread
        thread = threading.Thread(target=background_scan, daemon=True)
        thread.start()
        
        # Show dialog modally
        result = progress_dialog.ShowModal()
        progress_dialog.Destroy()
        
        # Wait for thread to finish if still running
        thread.join(timeout=1.0)
        
        # Handle results
        if scan_result["error"]:
            raise scan_result["error"]
        
        if scan_result["cancelled"]:
            return None  # Return None to indicate cancellation
            
        return scan_result["removed_libraries"]
    
    def validate_and_scan_incrementally_with_dialog(self, parent_window):
        """Validate existing games and scan incrementally with progress dialog (faster startup)"""
        # Import here to avoid circular dependency
        from dialogs import ScanProgressDialog
        
        # Count libraries excluding manual
        library_count = sum(1 for lib in self.config["libraries"] if lib["name"] != "manual")
        
        if library_count == 0:
            return self.validate_and_scan_incrementally()
        
        # Create and show progress dialog
        progress_dialog = ScanProgressDialog(parent_window)
        progress_dialog.set_library_count(library_count)
        
        # Use standard scanning text
        progress_dialog.status_text.SetLabel("Scanning for games...")
        
        # Variables to store results from background thread
        scan_result = {"exceptions_count": 0, "removed_libraries": [], "error": None, "cancelled": False}
        
        def background_scan():
            """Background thread function for incremental scanning"""
            try:
                def progress_callback(library_name, progress, games_found):
                    if not progress_dialog.cancelled:
                        progress_dialog.update_progress(library_name, progress, games_found)
                
                def cancel_check():
                    return progress_dialog.cancelled
                
                removed_libraries = self.validate_and_scan_incrementally(progress_callback, cancel_check)
                scan_result["exceptions_count"] = 0
                scan_result["removed_libraries"] = removed_libraries
                scan_result["cancelled"] = progress_dialog.cancelled
                
            except Exception as e:
                scan_result["error"] = e
            finally:
                # Close the progress dialog appropriately
                if not scan_result["cancelled"]:
                    if scan_result["removed_libraries"]:
                        # Libraries were removed - close dialog immediately without completion message
                        # Let the main window show the missing library dialog first
                        import wx
                        wx.CallAfter(progress_dialog.EndModal, wx.ID_OK)
                    else:
                        # Normal completion - show completion message and auto-close
                        progress_dialog.finish_scan(len(self.games), scan_result["exceptions_count"])
                else:
                    # Scan was cancelled - close dialog immediately
                    import wx
                    wx.CallAfter(progress_dialog.EndModal, wx.ID_CANCEL)
        
        # Start background thread
        thread = threading.Thread(target=background_scan, daemon=True)
        thread.start()
        
        # Show dialog modally
        result = progress_dialog.ShowModal()
        progress_dialog.Destroy()
        
        # Wait for thread to finish if still running
        thread.join(timeout=1.0)
        
        # Handle results
        if scan_result["error"]:
            raise scan_result["error"]
        
        if scan_result["cancelled"]:
            return None  # Return None to indicate cancellation
            
        return scan_result["removed_libraries"]