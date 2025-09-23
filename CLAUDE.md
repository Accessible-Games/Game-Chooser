# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Running the Application
```bash
python game-chooser.py
```

### Installing Dependencies
```bash
pip install wxPython
```

### Testing Commands
```bash
# Test library manager functionality
python3 -c "from library_manager import GameLibraryManager; manager = GameLibraryManager(); print('✓ Library manager loads successfully')"

# Test auto-exclusion patterns
python3 -c "from library_manager import GameLibraryManager; from pathlib import Path; manager = GameLibraryManager(); print('setup.exe excluded:', manager._should_auto_exclude(Path('setup.exe')))"
```

## Project Structure

This is a multi-file Python desktop application built with wxPython for cross-platform game library management.

### File Structure (2,836 total lines)
- **game-chooser.py** (21 lines) - Application entry point
- **models.py** (41 lines) - Data models (Game class)
- **game_list.py** (144 lines) - Custom list control for game display
- **main_window.py** (705 lines) - Primary application window and UI logic
- **dialogs.py** (586 lines) - Dialog classes for various UI operations
- **library_manager.py** (1,339 lines) - Core data management and scanning logic

### Configuration Files
- **games.json** - Portable game library with library-relative paths
- **config.json** - User-specific configuration in platform directories:
  - Windows: `%APPDATA%\GameChooser\`
  - macOS: `~/Library/Application Support/GameChooser/`
  - Linux: `~/.config/GameChooser/`

## Architecture Overview

### Core Classes

#### Data Models
- **Game**: Represents individual games with metadata (title, genre, developer, year, platforms, launch_path, library_name)
  - `to_dict()` / `from_dict()` for JSON serialization
  - Supports web games (HTTP URLs) and manual games

#### Main Application
- **GameChooserApp**: wxPython application entry point
- **MainFrame**: Primary window with splitter layout, search, menus, and keyboard shortcuts
- **GameListCtrl**: Enhanced list control with sorting, keyboard navigation, and persistent state

#### Data Management
- **GameLibraryManager**: Central class for all library operations
  - Configuration management (load/save config)
  - Game library persistence (games.json)
  - Multiple scanning strategies (full, incremental, targeted)
  - Comprehensive auto-exception system
  - Cross-platform path handling

#### Dialog Classes
- **ScanProgressDialog**: Threaded scanning with progress and cancellation
- **EditGameDialog**: Game metadata editing
- **EditManualGameDialog**: Manual game entry (outside libraries)
- **PreferencesDialog**: Library paths and exceptions management

### Exception Handling System

The application features a sophisticated auto-exclusion system to filter out non-game executables:

#### Pattern Categories (900+ total patterns)
- **AUTO_EXCEPTION_KEYWORDS** (314 patterns): Utility-related keywords (setup, install, config, etc.)
- **AUTO_EXCEPTION_EXACT_STEMS** (522 patterns): Specific filenames to exclude (unins000, git, bash, etc.)
- **AUTO_EXCEPTION_SUFFIXES** (64 patterns): Common utility filename endings (-setup, -installer, etc.)

#### Exception Storage
- Exceptions are stored as **library-relative paths** (e.g., `tools/setup.exe`)
- User-friendly display without library folder clutter
- Only actual discovered files are added to exceptions (not patterns)

### Scanning Strategies

#### Full Scan (`validate_and_scan_all`)
- Validates existing games and scans all libraries completely
- Used for comprehensive library refresh

#### Incremental Scan (`validate_and_scan_incrementally`)
- Skips directories that already contain known games
- Faster startup and preference changes

#### Targeted Scan (`validate_and_scan_targeted`)
- Scans only new libraries while using incremental for existing ones
- Optimal for adding new library paths

### UI Architecture

#### Main Window Layout
```
┌─────────────────────────────────────┐
│ Menu Bar + Search Box               │
├─────────────────┬───────────────────┤
│ Game List       │ Tree Filter       │
│ (sortable)      │ Platform→Genre    │
│                 │ →Developer→Year   │
├─────────────────┴───────────────────┤
│ Launch Button                       │
└─────────────────────────────────────┘
```

#### Key Features
- **Persistent State**: Window size, position, splitter, sort preferences, selections
- **Real-time Search**: 0.5s delay, searches all metadata fields
- **Keyboard Navigation**: Full keyboard shortcuts for accessibility
- **Context Menus**: Right-click operations for game management
- **Multi-select Filtering**: Tree control supports OR filtering

### Cross-Platform Support

#### Executable Detection
- **Windows**: `.exe`, `.bat` files
- **macOS**: `.app` bundles, executable files with permissions
- **Linux**: Executable files with permissions

#### Path Handling
- Library-relative paths for portability
- Platform-specific config directories
- Normalized path separators

### Threading Model

- **UI Thread**: Main application and user interactions
- **Background Threads**: Library scanning operations
- **Progress Callbacks**: Real-time scanning feedback
- **Cancellation**: User can cancel long-running scans

### Recent Improvements

#### Exception System Enhancements
- Expanded from ~87 to 900+ auto-exception patterns
- Library-relative path storage for user-friendly exceptions list
- Word boundary matching to prevent false positives
- Comprehensive utility detection (system tools, dev tools, compression, etc.)

#### Performance Optimizations
- Multiple scanning strategies for different use cases
- Incremental scanning to avoid re-scanning known game directories
- Efficient exception checking during scanning

### Key Implementation Details

#### Game Detection Logic
1. Look for preferred executables: `game`, `launch`, `play`
2. Check for executable matching parent directory name
3. If none found, use first executable in directory
4. Apply auto-exception filters during scanning
5. Store library-relative paths for portability

#### Configuration Management
- Graceful handling of missing/corrupted config files
- Default configuration fallbacks
- Automatic migration of config format changes

#### Error Handling
- Permission error handling during scanning
- Missing library path detection and cleanup
- Graceful degradation for invalid game paths

### Development Notes

- File paths should always use forward slashes internally (normalized)
- Exception patterns are case-insensitive
- Library scanning is depth-limited (MAX_SCAN_DEPTH = 10)
- Games are stored with relative paths but displayed with full context

## Key Methods Documentation

### PreferencesDialog (dialogs.py)

#### UI Structure
- **Exception Management Section** (lines 445-484):
  - `exc_list`: wx.ListCtrl displaying current exceptions (files and folders)
  - Exception buttons: "Add" (files), "Add Folder" (folders), "Remove"
  - Located in `create_path_management()` method

#### Key Methods
- **`on_add_exception(event)`** (lines 513-522):
  - Opens text entry dialog for file exception paths
  - Adds relative paths to `config["exceptions"]` list
  - Validates and adds to UI list control

- **`on_add_folder_exception(event)`** (lines 524-540):
  - Opens directory picker dialog
  - Converts absolute paths to library-relative using `_make_relative_to_library()`
  - Appends trailing "/" to distinguish folder exceptions
  - Validates folder is within configured library paths

- **`_make_relative_to_library(folder_path)`** (lines 542-561):
  - Converts absolute folder paths to library-relative paths
  - Iterates through configured libraries to find containing library
  - Returns normalized relative path with forward slashes
  - Returns None if folder not within any library

- **`on_apply(event)`** (lines 529-582):
  - Saves configuration and triggers rescanning when libraries change
  - Uses targeted scanning for new libraries, incremental for existing
  - Refreshes exception list display after auto-exceptions added

### GameLibraryManager (library_manager.py)

#### Exception Handling Methods
- **`_normalize_exception_entry(entry)`** (line 325):
  - Normalizes paths by converting backslashes to forward slashes
  - Strips whitespace from exception entries

- **`_is_path_exception(rel_path)`** (lines 330-349):
  - **Enhanced for folder support**: Checks if path matches any exception
  - **Folder Logic**: If exception ends with "/", matches paths starting with folder path
  - **File Logic**: Original exact match or wildcard (fnmatch) matching
  - Takes library-relative path as input
  - Returns True if path should be excluded

- **`_add_exception_entry(entry)`** (lines 351-364):
  - Adds new exception to config if not duplicate
  - Prevents redundant exceptions with fnmatch overlap checking
  - Returns True if actually added, False if duplicate/redundant

#### Scanning Methods
- **`scan_library(library_path, library_name, ...)`** (lines 710+):
  - **Two-phase scanning**: collect_directories() then scan_recursive()
  - **collect_directories()**: Builds directory list for progress tracking
    - **Lines 742-750**: Added folder exception checking before adding to scan list
  - **scan_recursive()**: Main scanning logic with executable detection
    - **Lines 868-875**: Added folder exception checking before directory recursion
  - Both phases skip directories matching folder exceptions

#### Scanning Integration Points
- **collect_directories() folder check** (lines 742-746):
  ```python
  # Check if this directory is excluded by folder exceptions
  rel_path = item.relative_to(Path(library_path))
  rel_str = str(rel_path).replace(os.sep, '/')
  if self._is_path_exception(rel_str):
      continue
  ```

- **scan_recursive() folder check** (lines 868-872):
  ```python
  # Check if this directory is excluded by folder exceptions
  rel_path = item.relative_to(Path(library_path))
  rel_str = str(rel_path).replace(os.sep, '/')
  if self._is_path_exception(rel_str):
      continue
  ```

#### Exception Storage Format
- **File exceptions**: `"tools/setup.exe"` (no trailing slash)
- **Folder exceptions**: `"tools/"` (trailing slash marker)
- **Mixed storage**: Both types coexist in `config["exceptions"]` list
- **Recursive behavior**: Folder exceptions exclude all subdirectories automatically

### Testing Commands
```bash
# Test folder exception logic
python3 -c "
from library_manager import GameLibraryManager
manager = GameLibraryManager()
manager.config['exceptions'] = ['tools/', 'build/debug/']
print('tools/setup.exe excluded:', manager._is_path_exception('tools/setup.exe'))
print('build/debug/test.exe excluded:', manager._is_path_exception('build/debug/test.exe'))
print('games/game.exe excluded:', manager._is_path_exception('games/game.exe'))
"
```