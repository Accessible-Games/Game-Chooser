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
            # Check if it's executable
            try:
                return os.access(path, os.X_OK) and path_obj.is_file()
            except:
                return False
        
        return False
    
    def is_valid_game_executable(self, path):
        """Check if executable matches valid game patterns"""
        name = Path(path).stem.lower()
        parent_name = Path(path).parent.name.lower()
        
        valid_names = ["game", "launch", "play", parent_name]
        return name in valid_names
    
    def scan_library(self, library_path, library_name, max_depth=10, progress_callback=None, cancel_check=None):
        """Recursively scan a library path for games"""
        # Check if library path exists
        library_path_obj = Path(library_path)
        if not library_path_obj.exists():
            print(f"Warning: Library path '{library_path}' does not exist. Skipping scan.")
            return [], []
        
        if not library_path_obj.is_dir():
            print(f"Warning: Library path '{library_path}' is not a directory. Skipping scan.")
            return [], []
        
        found_games = []
        new_exceptions = []
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
                    
                    if item.is_file() and self.is_executable(str(item)):
                        # Build relative path
                        rel_path = item.relative_to(Path(library_path).parent)
                        rel_str = str(rel_path).replace(os.sep, '\\')
                        
                        # Check if in exceptions
                        if rel_str in self.config["exceptions"]:
                            continue
                        
                        # Group by directory
                        dir_key = str(item.parent)
                        if dir_key not in directory_exes:
                            directory_exes[dir_key] = []
                        directory_exes[dir_key].append((item, rel_str))
                
                # Process each directory's executables
                for dir_path, exe_list in directory_exes.items():
                    # Check for cancellation
                    if cancel_check and cancel_check():
                        return
                        
                    if not exe_list:
                        continue
                    
                    # Find the first valid executable
                    main_exe = None
                    main_rel_str = None
                    
                    for exe_item, exe_rel_str in exe_list:
                        if self.is_valid_game_executable(str(exe_item)):
                            main_exe = exe_item
                            main_rel_str = exe_rel_str
                            break
                    
                    # If no valid executable found, use the first one
                    if main_exe is None and exe_list:
                        main_exe, main_rel_str = exe_list[0]
                    
                    if main_exe:
                        # Create game entry for main executable
                        title = main_exe.parent.name
                        system = platform.system()
                        plat = "Windows" if system == "Windows" else "macOS"
                        
                        # Check if game already exists
                        existing = None
                        for g in found_games:
                            if g.launch_path == main_rel_str:
                                existing = g
                                break
                        
                        if existing:
                            if plat not in existing.platforms:
                                existing.platforms.append(plat)
                        else:
                            game = Game(
                                title=title,
                                platforms=[plat],
                                launch_path=main_rel_str,
                                library_name=library_name
                            )
                            found_games.append(game)
                        
                        # Add all other executables in the same directory to exceptions
                        for exe_item, exe_rel_str in exe_list:
                            if exe_rel_str != main_rel_str:
                                new_exceptions.append(exe_rel_str)
                
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
                        scan_recursive(item, depth + 1)
            
            except PermissionError:
                # Handle permission errors
                raise PermissionError(f"Permission denied: {path}")
        
        scan_recursive(library_path)
        return found_games, new_exceptions
    
    def validate_and_scan_all(self, progress_callback=None, cancel_check=None):
        """Validate existing games and scan for new ones"""
        validated_games = []
        all_new_exceptions = []
        
        # FIRST: Check for missing libraries and remove them from config before any scanning
        missing_libraries = []
        valid_libraries = []
        
        for lib in self.config["libraries"]:
            if lib["name"] == "manual":
                valid_libraries.append(lib)  # Always keep manual library
                continue
                
            if not Path(lib["path"]).exists():
                missing_libraries.append(lib)
            else:
                valid_libraries.append(lib)
        
        # Remove missing libraries from config immediately
        removed_libraries = []
        if missing_libraries:
            print(f"Removing {len(missing_libraries)} missing library path(s) from config:")
            for lib in missing_libraries:
                print(f"  - {lib['name']}: {lib['path']}")
                removed_libraries.append(lib)
            
            # Update config to only contain valid libraries
            self.config["libraries"] = valid_libraries
            self.save_config()
            
            # Still need to validate existing games to remove ones from missing libraries
            for game in self.games:
                if game.launch_path.startswith("http"):
                    validated_games.append(game)
                    continue
                
                # Always keep manual games (they manage their own paths)
                if game.library_name == "manual":
                    validated_games.append(game)
                    continue
                
                # Only keep games from libraries that still exist
                if game.library_name in [lib["name"] for lib in valid_libraries]:
                    full_path = self.get_full_path(game)
                    if full_path and Path(full_path).exists():
                        validated_games.append(game)
            
            # Save updated games list and return early - no need to scan anything
            self.games = validated_games
            self.save_games()
            return 0, removed_libraries
        
        # SECOND: Validate existing games (only if no libraries were removed)
        for game in self.games:
            if cancel_check and cancel_check():
                return 0, []  # Return early if cancelled
                
            if game.launch_path.startswith("http"):
                validated_games.append(game)
                continue
            
            # Always keep manual games (they manage their own paths)
            if game.library_name == "manual":
                validated_games.append(game)
                continue
            
            full_path = self.get_full_path(game)
            if full_path and Path(full_path).exists():
                validated_games.append(game)
        
        # THIRD: Scan all valid libraries for new games (exclude manual library)
        for lib in valid_libraries:
            if cancel_check and cancel_check():
                return 0, []  # Return early if cancelled
                
            if lib["name"] == "manual":
                continue  # Skip manual library from autodiscovery
                
            try:
                new_games, new_exceptions = self.scan_library(
                    lib["path"], lib["name"], progress_callback=progress_callback, cancel_check=cancel_check
                )
                
                # Add new exceptions
                for exc in new_exceptions:
                    if exc not in self.config["exceptions"]:
                        self.config["exceptions"].append(exc)
                        all_new_exceptions.append(exc)
                
                # Merge new games
                for new_game in new_games:
                    if cancel_check and cancel_check():
                        return 0, []  # Return early if cancelled
                        
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
        
        return len(all_new_exceptions), []  # No libraries removed in this case
    
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
                
                exceptions_count, removed_libraries = self.validate_and_scan_all(progress_callback, cancel_check)
                scan_result["exceptions_count"] = exceptions_count
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
            return 0, []
            
        return scan_result["exceptions_count"], scan_result["removed_libraries"]