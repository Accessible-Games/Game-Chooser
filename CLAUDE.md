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

## Architecture Overview

This is a single-file Python desktop application built with wxPython for cross-platform game library management. The application follows a traditional GUI architecture with these key components:

### Core Classes

- **Game**: Data model representing individual games with metadata (title, genre, developer, year, platforms, launch_path, library_name)
- **GameLibraryManager**: Central data management class handling game library operations, configuration, and persistence
- **MainFrame**: Primary application window containing the main UI layout with splitter panels
- **GameListCtrl**: Custom list control for displaying games with sorting and selection capabilities
- **ScanProgressDialog**: Modal dialog showing scanning progress with cancellation support

### Data Management

- **games.json**: Portable game library stored in application directory with relative paths
- **config.json**: User-specific configuration stored in platform-appropriate locations:
  - Windows: `%APPDATA%\GameChooser\`
  - macOS: `~/Library/Application Support/GameChooser/`
  - Linux: `~/.config/GameChooser/`

### Key Features

- **Auto-discovery**: Scans configured folders to find game executables
- **Smart detection**: Identifies game launchers while filtering out tools/updaters
- **Hierarchical filtering**: Tree-based browsing by Platform → Genre → Developer → Year
- **Cross-platform support**: Handles Windows (.exe, .bat) and macOS/Linux executables
- **Web games**: Support for browser-based games with URL launching
- **Persistent state**: Remembers window layout, sort preferences, and selections

### UI Structure

The main window uses a splitter layout:
- Left panel: Tree control for hierarchical filtering
- Right panel: List control displaying filtered games
- Top: Search box with real-time filtering
- Context menus for game management operations

### Threading

Scanning operations use background threads with progress dialogs to prevent UI blocking during library discovery.