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

### Testing Strategy

#### Comprehensive Logic Testing (Without Dependencies)
When wxPython or other dependencies are unavailable, use isolated logic testing to verify core functionality:

```bash
# Test library manager functionality
python3 -c "from library_manager import GameLibraryManager; manager = GameLibraryManager(); print('✓ Library manager loads successfully')"

# Test auto-exclusion patterns
python3 -c "from library_manager import GameLibraryManager; from pathlib import Path; manager = GameLibraryManager(); print('setup.exe excluded:', manager._should_auto_exclude(Path('setup.exe')))"

# Test tree building logic with mock data
python3 -c "
class Game:
    def __init__(self, genre=None, developer=None, year=None):
        self.genre = genre
        self.developer = developer
        self.year = year
        self.platforms = ['PC']

# Test unknown handling
games = [Game(), Game(genre='Action'), Game(developer='Studio'), Game(genre='RPG', developer='Big Studio', year='2024')]
for i, game in enumerate(games):
    genre = game.genre or 'Unknown Genre'
    developer = game.developer or 'Unknown Developer'
    year = game.year or 'Unknown Year'
    print(f'Game {i}: genre=\"{genre}\", developer=\"{developer}\", year=\"{year}\"')
"

# Test filtering logic with edge cases
python3 -c "
class Game:
    def __init__(self, genre='', developer='', year=''):
        self.genre = genre
        self.developer = developer
        self.year = year
        self.platforms = ['PC']

games = [Game('', '', ''), Game('Action', '', '2023'), Game('', 'Studio', ''), Game('RPG', 'Big Studio', '2024')]

# Test criteria matching
tree_criteria = {'genres': {'Unknown Genre', 'Action'}, 'developers': {'Unknown Developer'}, 'years': {'Unknown Year', '2023'}}

for i, game in enumerate(games):
    genre_match = (not tree_criteria['genres'] or game.genre in tree_criteria['genres'] or (game.genre == '' and 'Unknown Genre' in tree_criteria['genres']))
    dev_match = (not tree_criteria['developers'] or game.developer in tree_criteria['developers'] or (game.developer == '' and 'Unknown Developer' in tree_criteria['developers']))
    year_match = (not tree_criteria['years'] or game.year in tree_criteria['years'] or (game.year == '' and 'Unknown Year' in tree_criteria['years']))
    matches = genre_match and dev_match and year_match
    print(f'Game {i}: matches={matches}')
"
```

#### Testing Principles
1. **Mock Data Approach**: Create minimal test classes that simulate real data structures
2. **Edge Case Coverage**: Test empty/None values, mixed data states, and boundary conditions
3. **Logic Isolation**: Test core algorithms separately from UI/dependency layers
4. **Incremental Validation**: Test each logical component before integration
5. **Real-world Scenarios**: Use realistic data combinations that mirror actual usage

#### Important Testing Notes
- **Avoid GUI Testing Without Dependencies**: When wxPython is unavailable, focus on logic testing rather than attempting to run the full application
- **Dependency-Free Validation**: All core business logic should be testable without external dependencies
- **Mock Classes Over Real Objects**: Use simple mock classes that implement only the required interface rather than complex test frameworks

## Project Structure

This is a multi-file Python desktop application built with wxPython for cross-platform game library management.

### File Structure (3,038 total lines)
- **game-chooser.py** (21 lines) - Application entry point
- **models.py** (41 lines) - Data models (Game class)
- **game_list.py** (150 lines) - Custom list control for game display
- **main_window.py** (769 lines) - Primary application window and UI logic
- **dialogs.py** (812 lines) - Dialog classes for various UI operations
- **library_manager.py** (857 lines) - Core data management and scanning logic
- **exception_manager.py** (203 lines) - Auto-exception pattern management
- **validation_service.py** (87 lines) - Centralized validation logic
- **path_manager.py** (98 lines) - Path normalization and operations

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

#### Utility Classes
- **ExceptionManager**: Auto-exception pattern matching and user exception handling
- **ValidationService**: Centralized validation for titles, URLs, paths
- **PathManager**: Path normalization and library-relative conversion

#### Dialog Classes
- **ScanProgressDialog**: Threaded scanning with progress and cancellation
- **GameDialog**: Unified dialog for adding and editing all game types (Windows/macOS/Web)
  - Replaces previous EditGameDialog and EditManualGameDialog
  - Dynamic UI morphing based on platform selection
  - Comprehensive validation pipeline with duplicate detection
  - Converts library-managed games to user-managed when path is edited
- **PreferencesDialog**: Tabbed interface for library paths and exceptions management
  - Uses wx.Notebook with separate tabs instead of splitter layout
- **DeleteGameDialog**: Confirmation dialog for game deletion

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
- **Real-time Search**: 0.5s delay, searches game title and developer fields only
  - Automatically excludes searches containing "unknown" (returns no results)
  - Case-insensitive matching
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
- **Background Threads**:
  - Library scanning operations (ScanProgressDialog)
  - Game filtering operations (FilterWorker)
- **Progress Callbacks**: Real-time scanning feedback
- **Cancellation**: User can cancel long-running scans and filter operations

### Recent Improvements

#### Dialog System Improvements
- Unified GameDialog replaces separate edit dialogs
- Dynamic field validation with asynchronous duplicate checking
- Tabbed preferences interface (wx.Notebook) replaces old splitter layout
- Modal dialog protection with dialog_active flag to prevent spurious selection events

#### Launch Enhancements
- Proper working directory handling (sets cwd to game directory)
- Platform-specific launch logic for .app bundles vs regular executables

#### Keyboard Shortcuts
- Edit Game changed from 'e' to 'Ctrl+E' to avoid wxPython type-ahead conflicts

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

### FilterWorker (main_window.py)

#### Overview
Background thread class that handles game filtering based on tree selection and search criteria.

#### Key Methods
- **`run()`** (approximately lines 40-75):
  - Main filtering loop executing in background thread
  - **Tree Filter Phase**:
    - Applies platform, genre, developer, and year filters from tree control
    - Handles "Unknown" category matching for empty/null values
    - Uses AND logic (all criteria must match)
  - **Search Filter Phase**:
    - **Field Scope**: Searches only `game.title` and `game.developer` fields
    - **Unknown Exclusion**: Automatically rejects any search containing "unknown"
    - **Case Handling**: Converts search term to lowercase for case-insensitive matching
    - Returns no results if "unknown" appears anywhere in search term

#### Search Behavior Details
- **Included Fields**: title, developer
- **Excluded Fields**: genre, year, platforms (not searchable)
- **Special Handling**: "unknown" keyword triggers immediate exclusion
- **Matching Logic**: Substring matching within included fields

### GameDialog (dialogs.py)

#### Overview
Unified dialog that handles both adding and editing games of all types (Windows/macOS/Web).

#### Key Features
- **Dynamic UI**: Fields morph between path and URL input based on platform selection
- **Validation Pipeline**: Title → Platform → Path format → File exists → Executable type → Async duplicate check
- **Library Conversion**: Editing a library-managed game's path converts it to user-managed (empty library_name)
- **Platform Support**: Windows (.exe, .bat), macOS (.app bundles, executables), Web (HTTP/HTTPS URLs)

#### Key Methods
- **`__init__(parent, library_manager, game=None)`** (around line 116):
  - `is_new` flag distinguishes Add vs Edit mode
  - Pre-populates fields from existing game if editing
  - Builds genre/developer combo boxes from existing games

- **`on_browse()`**:
  - Opens file picker for desktop games
  - Opens URL dialog for web games
  - Updates path field with selected value

- **`validate_and_save()`**:
  - Runs comprehensive validation checks
  - Performs asynchronous duplicate detection
  - Updates game object if validation passes

### PreferencesDialog (dialogs.py)

#### UI Structure (Tabbed Layout)
- **wx.Notebook with Two Tabs**:
  - **Library Paths Tab** (`create_library_paths_tab()` at line 499):
    - Library list control with Add/Remove buttons
    - Browse dialog for adding new library folders
  - **Exceptions Tab** (`create_exceptions_tab()` at line 546):
    - Exception list control showing files and folders
    - Buttons: "Add" (files), "Add Folder" (folders), "Remove"

#### Key Methods
- **`on_add_exception(event)`**:
  - Opens text entry dialog for file exception paths
  - Adds relative paths to `config["exceptions"]` list
  - Validates and adds to UI list control

- **`on_add_folder_exception(event)`**:
  - Opens directory picker dialog
  - Converts absolute paths to library-relative using `_make_relative_to_library()`
  - Appends trailing "/" to distinguish folder exceptions
  - Validates folder is within configured library paths

- **`_make_relative_to_library(folder_path)`**:
  - Converts absolute folder paths to library-relative paths
  - Iterates through configured libraries to find containing library
  - Returns normalized relative path with forward slashes
  - Returns None if folder not within any library

- **`on_apply(event)`**:
  - Saves configuration and triggers rescanning when libraries change
  - Uses targeted scanning for new libraries, incremental for existing
  - Refreshes exception list display after auto-exceptions added

### MainFrame (main_window.py)

#### Dialog Protection
- **`dialog_active` flag** (line 88):
  - Set to True when modal dialogs open
  - Prevents spurious selection events during dialog lifecycle
  - Used in `on_selection_changed()` to block unwanted refreshes

#### Game Launch
- **`on_launch(event)`**:
  - **Web games**: Opens URL in default browser
  - **Desktop games**:
    - Resolves library-relative paths to absolute paths
    - Sets working directory to game's parent directory (`cwd=game_dir`)
    - Platform-specific handling for macOS .app bundles vs regular executables
    - Minimizes window after successful launch

#### Game Editing
- **`on_edit_game(event)`**:
  - Uses unified GameDialog for all game types
  - Protected by dialog_active flag
  - Refreshes game list on successful edit

#### Keyboard Shortcuts
- Edit Game: **Ctrl+E** (changed from 'e' to avoid wxPython type-ahead search conflicts)

### GameLibraryManager (library_manager.py)

#### Exception Handling Methods
- **`_normalize_exception_entry(entry)`**:
  - Normalizes paths by converting backslashes to forward slashes
  - Strips whitespace from exception entries

- **`_is_path_exception(rel_path)`**:
  - **Enhanced for folder support**: Checks if path matches any exception
  - **Folder Logic**: If exception ends with "/", matches paths starting with folder path
  - **File Logic**: Original exact match or wildcard (fnmatch) matching
  - Takes library-relative path as input
  - Returns True if path should be excluded

- **`_add_exception_entry(entry)`**:
  - Adds new exception to config if not duplicate
  - Prevents redundant exceptions with fnmatch overlap checking
  - Returns True if actually added, False if duplicate/redundant

#### Scanning Methods
- **`scan_library(library_path, library_name, ...)`**:
  - **Two-phase scanning**: collect_directories() then scan_recursive()
  - **collect_directories()**: Builds directory list for progress tracking
    - Checks folder exceptions before adding to scan list
  - **scan_recursive()**: Main scanning logic with executable detection
    - Checks folder exceptions before directory recursion
  - Both phases skip directories matching folder exceptions

#### Exception Storage Format
- **File exceptions**: `"tools/setup.exe"` (no trailing slash)
- **Folder exceptions**: `"tools/"` (trailing slash marker)
- **Mixed storage**: Both types coexist in `config["exceptions"]` list
- **Recursive behavior**: Folder exceptions exclude all subdirectories automatically

### ExceptionManager (exception_manager.py)

#### Overview
Extracted auto-exception logic from GameLibraryManager into standalone class for better organization.

#### Pattern Categories
- **AUTO_EXCEPTION_KEYWORDS**: Common utility keywords (setup, install, config, etc.)
- **AUTO_EXCEPTION_EXACT_STEMS**: Specific filenames to exclude (unins000, vcredist, etc.)
- **AUTO_EXCEPTION_SUFFIXES**: Filename endings (-setup, -installer, etc.)

#### Key Methods
- **`should_auto_exclude(path)`**: Checks if executable should be auto-excluded based on patterns
- Uses word boundary matching to prevent false positives
- Case-insensitive pattern matching

### ValidationService (validation_service.py)

#### Overview
Centralized validation logic for game data, extracted for reusability and testing.

#### Key Methods
- **`validate_title(title)`**: Returns (is_valid, error_message) tuple
  - Checks for empty/whitespace-only titles
  - Enforces max length (255 characters)

- **`validate_url(url)`**: Validates web game URLs
  - Ensures proper HTTP/HTTPS format
  - Basic URL structure validation

- **`validate_path(path, platform)`**: Validates desktop game paths
  - Checks file existence
  - Validates executable type based on platform (.exe/.bat for Windows, .app for macOS)

### PathManager (path_manager.py)

#### Overview
Centralized path operations and normalization.

#### Key Methods
- **`normalize(path)`**: Converts all paths to forward slashes
  - Handles both string and Path objects
  - Strips whitespace

- **`to_library_relative(full_path, library_paths)`**: Converts absolute to library-relative
  - Iterates through configured libraries to find containing path
  - Returns normalized relative path

- **`to_absolute(rel_path, library_paths)`**: Converts library-relative to absolute
  - Resolves relative path against configured library paths
  - Returns None if library not found

### Additional Testing Commands

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