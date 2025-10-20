# CLAUDE.md

Documentation for Game Chooser - a wxPython desktop app for managing and launching game libraries.

## Development Commands

### Running the Application
```bash
uv run game-chooser.py
# or if on wsl:
uv.exe run game-chooser.py
```

### Installing Dependencies
```bash
# use uv for dependency management
uv sync
```

### Testing Strategy

#### Logic Testing Without Dependencies
When wxPython is unavailable, test core logic in isolation:

```bash
# Test library manager
uv run python -c "from library_manager import GameLibraryManager; manager = GameLibraryManager(); print('Library manager loads')"

# Test auto-exclusion
uv run python -c "from library_manager import GameLibraryManager; from pathlib import Path; manager = GameLibraryManager(); print('setup.exe excluded:', manager._should_auto_exclude(Path('setup.exe')))"

# Test hierarchical extraction depth logic
uv run python -c "
from pathlib import Path
depths = [
    ('games/doom.exe', 0),  # Depth 0: title from exe
    ('games/Doom Eternal/play.exe', 1),  # Depth 1: title from dir
    ('games/id Software/Doom Eternal/play.exe', 2),  # Depth 2: developer + title
    ('games/FPS/id Software/Doom Eternal/play.exe', 3)  # Depth 3: genre + developer + title
]
for path_str, expected in depths:
    p = Path(path_str)
    rel = p.relative_to(Path('games'))
    depth = len(rel.parts)
    print(f'{path_str} -> depth {depth} (expected {expected})')
"

# Test folder exceptions
uv run python -c "
from library_manager import GameLibraryManager
manager = GameLibraryManager()
manager.config['exceptions'] = ['tools/', 'build/debug/']
print('tools/setup.exe excluded:', manager._is_path_exception('tools/setup.exe'))
print('games/game.exe excluded:', manager._is_path_exception('games/game.exe'))
"
```

#### Testing Principles
- Mock minimal data structures that simulate real objects
- Test edge cases: empty/None values, mixed states, boundaries
- Isolate logic from UI/dependency layers
- Avoid GUI testing without dependencies

## Project Structure

Multi-file Python desktop app built with wxPython. Total: ~3,751 lines.

### File Structure
- **game-chooser.py** (37 lines) - Entry point
- **models.py** (57 lines) - Game data model
- **game_list.py** (236 lines) - Smart list control for game display (uses smart_list submodule)
- **main_window.py** (1,064 lines) - Main UI and application logic
- **dialogs.py** (976 lines) - All dialog classes
- **library_manager.py** (925 lines) - Core data management and scanning
- **exception_manager.py** (239 lines) - Auto-exception pattern system
- **validation_service.py** (103 lines) - Validation logic
- **path_manager.py** (114 lines) - Path operations

### Configuration Files
- **games.json** - Game library with library-relative paths
- **config.json** - User config in platform-specific locations:
  - Windows: `%APPDATA%\GameChooser\`
  - macOS: `~/Library/Application Support/GameChooser/`
  - Linux: `~/.config/GameChooser/`

## Architecture Overview

### Data Models
- **Game**: Represents games with metadata (title, genre, developer, year, platforms, launch_path, library_name)
  - `to_dict()` / `from_dict()` for JSON serialization
  - Supports web games (HTTP URLs) and manual games

### Main Application
- **GameChooserApp**: wxPython app entry point
- **MainFrame**: Main window with splitter layout, search, menus, keyboard shortcuts
  - **First Run**: Shows `FirstTimeSetupDialog` if config.json doesn't exist
  - **Post-Setup Scan**: Prompts user to scan if libraries were added during first-time setup
  - **Manual Refresh**: F5 or Refresh menu triggers scanning
- **GameListCtrl**: Smart list control (from smart_list submodule) with sorting, keyboard nav, persistent state

### Data Management
- **GameLibraryManager**: Central class for all operations
  - Config and game persistence (config.json, games.json)
  - Multiple scanning strategies (full, incremental, targeted)
  - Auto-exception system with 168 patterns
  - Cross-platform path handling
  - First-run detection via `is_first_run` flag
  - **Hierarchical Metadata Extraction**: Depth-based field extraction from directory structure

### Utility Classes
- **ExceptionManager**: Pattern matching for non-game executables
- **ValidationService**: Title, URL, path validation
- **PathManager**: Path normalization and library-relative conversion

### Dialog Classes
- **FirstTimeSetupDialog**: Welcome screen on first run
  - Shows when config.json doesn't exist
  - Non-blocking: user can skip setup
  - Returns `wx.ID_OK` if libraries added, triggering scan prompt
  - Session-based reminder after first addition
- **ScanProgressDialog**: Threaded scanning with progress and cancellation
- **GameDialog**: Unified dialog for adding/editing all game types
  - Dynamic UI morphs based on platform selection
  - Validation pipeline with duplicate detection
  - Converts library-managed to user-managed when path edited
- **PreferencesDialog**: Tabbed interface (wx.Notebook) for libraries and exceptions
  - Scan guard: only scans if libraries exist after changes
- **DeleteGameDialog**: Deletion confirmation

### Exception System

Auto-exclusion filters non-game executables during scanning.

#### Pattern Categories (168 total)
- **AUTO_EXCEPTION_KEYWORDS** (42): Utility keywords (setup, install, config)
- **AUTO_EXCEPTION_EXACT_STEMS** (61): Specific filenames (unins000, vcredist)
- **AUTO_EXCEPTION_SUFFIXES** (65): Filename endings (-setup, -installer)

#### Exception Storage
- Stored as library-relative paths (e.g., `tools/setup.exe`)
- File exceptions: `"tools/setup.exe"` (no trailing slash)
- Folder exceptions: `"tools/"` (trailing slash excludes all subdirectories)
- User-friendly display without library path clutter

### Hierarchical Metadata Extraction

Automatically extracts genre, developer, and title based on directory depth:

- **Depth 0** (`games/madden26.exe`): Title from exe name
- **Depth 1** (`games/Madden NFL 26/play.exe`): Title from directory
- **Depth 2** (`games/EA Sports/Madden NFL 26/play.exe`): Developer + title
- **Depth 3+** (`games/Sports/EA Sports/Madden NFL 26/play.exe`): Genre + developer + title

Supports any organization style from flat to deep hierarchies.

### Scanning Strategies

#### Full Scan (`validate_and_scan_all`)
Validates existing games and scans all libraries completely. Used for comprehensive refresh.

#### Incremental Scan (`validate_and_scan_incrementally`)
Skips directories with known games. Faster for startup and preference changes.

#### Targeted Scan (`validate_and_scan_targeted`)
Scans only new libraries, incremental for existing. Optimal for adding library paths.

### UI Architecture

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
- **Persistent State**: Window size/position, splitter, sort, game selection, tree filters
  - Game selection saved by title, restored after filtering
  - Tree filter selections saved as path strings (e.g., "Platform/Windows", "Genre/RPG")
  - Tree filters persist across restarts via SavedState.tree_filters
  - Categories collapse by default for cleaner UI
- **Screen Reader Support**: Proper focus management for accessibility
  - Calls `Select()`, `Focus()`, `SetFocus()` sequence for screen reader announcement
  - Skips `SetFocus()` during init to avoid stealing focus from scan dialog
  - Game list receives keyboard focus at launch
- **Real-time Search**: 0.5s delay, searches title and developer only
  - Case-insensitive substring matching
  - Auto-excludes searches containing "unknown"
- **Keyboard Navigation**: Full shortcuts for accessibility
- **Context Menus**: Right-click operations
- **Multi-select Filtering**: Tree control supports OR filtering

### Cross-Platform Support

#### Executable Detection
- **Windows**: `.exe`, `.bat`
- **macOS**: `.app` bundles, executable files with permissions
- **Linux**: Executable files with permissions

#### Path Handling
- Library-relative paths for portability
- Platform-specific config directories
- Normalized path separators (forward slashes internally)

### Threading Model

- **UI Thread**: Main app and user interactions
- **Background Threads**: Library scanning, game filtering
- **Progress Callbacks**: Real-time feedback
- **Cancellation**: User can cancel long operations

## Recent Features

### Random Game Picker (Oct 15, 2025)
- Launch menu with "Random Game" option (Ctrl+R)
- Picks random game from currently filtered list
- Platform compatibility check: filters for user's OS
- Web games included in all platforms
- Dialog: "Your game for today is... [title]. Launch now?"

### Tree Filter State Persistence (Oct 15, 2025)
- SavedState.tree_filters stores active filters
- Filter dialog reflects current state instead of defaulting to all checked
- Filter changes save immediately
- State persists across restarts
- Categories collapse by default

### Post-Setup Scanning (Oct 15, 2025)
- FirstTimeSetupDialog returns wx.ID_OK if libraries added
- MainFrame.check_libraries() prompts to scan after setup
- Handles scan results including removed libraries and exceptions

### Other Recent Additions
- **Open Folder** (Ctrl+Enter): Opens game directory in file explorer
- **Platform Compatibility**: Checks user's OS before launching games
- **List Position Preservation**: Maintains position when deleting games
- **Case-Insensitive Tree Sorting**: Natural alphabetical order
- **Windows Build Tooling**: build_windows.sh script
- **UV Configuration**: Modern Python dependency management
- **Smart List Integration**: Replaced wx.ListCtrl with smart_list submodule for better cross-platform accessibility

## Key Implementation Notes

### Game Detection Logic
1. Look for preferred executables: `game`, `launch`, `play`
2. Check for exe matching parent directory name
3. If none found, use first executable in directory
4. Apply auto-exception filters
5. Store library-relative paths

### Configuration Management
- Graceful handling of missing/corrupted config
- Default fallbacks
- **First-Run Detection**: `GameLibraryManager.is_first_run` flag set during init
- **SavedState Structure**:
  - `last_selected`: Game title for selection restoration
  - `tree_selections`: Path strings for tree selection restoration
  - `tree_filters`: Active filter list (["platform", "genre", "developer", "year"])

### Launch Behavior
- **Web Games**: Opens URL in default browser
- **Desktop Games**:
  - Resolves library-relative to absolute paths
  - Sets working directory to game's parent directory
  - Platform-specific handling for .app bundles vs regular executables
  - Minimizes window after successful launch

### Keyboard Shortcuts
- **F5**: Refresh libraries
- **Ctrl+R**: Random game picker
- **Ctrl+E**: Edit game (changed from 'e' to avoid wxPython type-ahead conflicts)
- **Ctrl+Enter**: Open folder
- **Ctrl+,**: Preferences

### Selection and Focus Management

#### Flags
- **`restoring_tree`**: Blocks save_tree_selections() during restoration to prevent loops
- **`initializing`**: Prevents SetFocus() during startup to avoid stealing from scan dialog
- **`dialog_active`**: Blocks selection events during modal dialogs

#### Methods
- **`save_tree_selections()`**: Extracts tree paths, saves to SavedState.tree_selections
- **`restore_tree_selections()`**: Restores tree selections from saved paths
- **`on_filter_complete()`**: Populates game list, restores selection by title, manages accessibility focus

### FilterWorker Search Behavior
- **Field Scope**: Searches only game.title and game.developer
- **Unknown Exclusion**: Auto-rejects searches containing "unknown"
- **Case Handling**: Lowercase conversion for case-insensitive matching

### Exception Handling
- **File Exceptions**: `"tools/setup.exe"` (exact path match)
- **Folder Exceptions**: `"tools/"` (trailing slash matches all subdirectories)
- **Mixed Storage**: Both types in config["exceptions"] list
- **Word Boundary Matching**: Prevents false positives

### Scan Guards
- **MainFrame.on_refresh()**: Returns early if no libraries
- **PreferencesDialog.on_apply()**: Only scans if libraries exist after changes

### Error Handling
- Permission errors during scanning
- Missing library path detection and cleanup
- Graceful degradation for invalid game paths

## Development Notes

- File paths use forward slashes internally (normalized)
- Exception patterns are case-insensitive
- Library scanning depth-limited (MAX_SCAN_DEPTH = 10)
- Games stored with relative paths, displayed with context
- Smart list provides better cross-platform accessibility than wx.ListCtrl

## Build and Distribution

### Windows
```bash
./build_windows.sh
```

Creates standalone executable for Windows distribution.

### License
GNU GPLv3 - see LICENSE.txt
