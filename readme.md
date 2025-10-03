# Game Chooser

Game Chooser is a simple desktop app that helps you organize and launch your entire game collection from one place. Point it at your game folders, and it automatically discovers and catalogs everything—no manual entry needed.

## What Problem Does It Solve?

If you have games scattered across multiple folders, external drives, or different launchers, Game Chooser brings them all together. Instead of hunting through folders or remembering where each game lives, you get a searchable, filterable library that launches games with a single click.

## Key Features

- **Automatic Discovery** - Scans your game folders and finds all playable executables automatically
- **Smart Filtering** - Installers, updaters, and other utility programs are automatically excluded
- **Multiple Libraries** - Manage games from different folders, drives, or locations in one unified view
- **Advanced Filtering** - Browse by platform (Windows, macOS, Linux), genre, developer, or release year
- **Fast Search** - Instantly search by game title or developer as you type
- **Manual Game Support** - Add web games (browser-based) or games outside your library folders
- **Keyboard-Friendly** - Full keyboard navigation with shortcuts for every action
- **Persistent Settings** - Remembers your window size, column sorting, selected game, and active filters between sessions
- **Cross-Platform** - Works on Windows, macOS, and Linux

## Getting Started

### First Launch

When you first open Game Chooser, you'll see a welcome screen with four options:

1. **Add Library** - Point to a folder containing games (recommended for most users)
2. **Add Game** - Manually add a single game or web game
3. **Preferences** - Configure libraries and exception filters
4. **Exit** - Close the app

**Most users should start with "Add Library"** to let the app scan and discover your games automatically.

### Adding Game Libraries

A game library is simply a folder where you keep games. Common examples:
- `C:\Games`
- `D:\Steam Games`
- `/Applications/Games`
- External hard drive game folders

To add a library:
1. Click **File → Preferences** (or press **Ctrl+,**)
2. In the **Library Paths** tab, click **Add**
3. Select the folder containing your games
4. Click **Apply** to scan for games

You can add as many libraries as you need. The app will scan all of them and combine the results.

### Adding Manual Games

For games outside your library folders or web-based games:

1. Right-click anywhere in the game list
2. Select **Add Game**
3. Fill in the details:
   - **Title**: Game name
   - **Platform**: Choose Windows, macOS, Linux, or Web
   - **Launch Path/URL**: For desktop games, browse to the .exe or executable; for web games, enter the URL
   - **Genre, Developer, Year**: Optional metadata for filtering

Manual games appear alongside auto-discovered games and can be launched the same way.

## Using the App

### Browsing Games

The main window shows your games in a list on the left and a filter tree on the right. The tree organizes games by:
- **Platform** (Windows, macOS, Linux, Web)
- **Genre** (Action, RPG, Strategy, etc.)
- **Developer** (Valve, Nintendo, etc.)
- **Year** (2024, 2023, etc.)

Click any category in the tree to filter the game list. You can select multiple categories (Ctrl+Click) to see games matching any of them.

### Searching

The search box at the top searches game titles and developers as you type. Just start typing—results appear automatically after a brief delay.

**Note**: Searching for "unknown" returns no results, since that's a placeholder for missing metadata.

### Launching Games

Three ways to launch a game:
1. **Double-click** the game in the list
2. Select the game and press **Enter** or **Space**
3. Select the game and click the **Launch** button at the bottom

Desktop games open and the window minimizes automatically. Web games open in your default browser.

### Editing Games

To change a game's details:
1. Select the game
2. Press **Ctrl+E** or right-click and choose **Edit Game**
3. Modify the details and click **OK**

**Important**: Editing the launch path of an auto-discovered game converts it to a manually-managed game.

### Managing Preferences

Press **Ctrl+,** or go to **File → Preferences** to access:

#### Library Paths Tab
- Add or remove game library folders
- Changes trigger an automatic rescan

#### Exceptions Tab
- Manage files and folders excluded from scanning
- **Add**: Exclude a specific file (e.g., `tools/setup.exe`)
- **Add Folder**: Exclude an entire folder and its contents (e.g., `build/`)
- **Remove**: Un-exclude a file or folder

The app has 900+ built-in auto-exclusion patterns for common installers and utilities, but you can add more if needed.

## Keyboard Shortcuts

### Global Shortcuts
| Shortcut | Action |
|----------|--------|
| **Ctrl+F** | Jump to search box |
| **Ctrl+N** | Add new game |
| **Ctrl+,** | Open preferences |
| **F5** | Refresh/rescan all libraries |
| **Alt+F4** | Exit application |

### Game List Shortcuts
| Shortcut | Action |
|----------|--------|
| **Ctrl+E** | Edit selected game |
| **Delete** | Delete selected game |
| **Enter** or **Space** | Launch selected game |
| **Right-click** | Open context menu |
| **Tab** | Move between game list and filter tree |

### Filter Tree Shortcuts
| Shortcut | Action |
|----------|--------|
| **Escape** | Clear all filters (show all games) |
| **Ctrl+Click** | Select multiple filters (OR logic) |

### Column Sorting
Click any column header to sort by that column. Click again to reverse the sort order.

## Tips & Tricks

### Multi-Select Filtering
Hold **Ctrl** while clicking tree items to select multiple filters. For example, select "Windows" and "macOS" under Platform to see games for both systems.

### Managing Auto-Excluded Files
If the scanner missed a game because it thought it was a utility:
1. Go to **Preferences → Exceptions**
2. Find and remove the auto-added exception
3. Click **Apply** to rescan

### Column Customization
Drag column dividers to resize them. Your preferences are saved automatically.

### Quick Re-scan
Press **F5** to force a complete re-scan of all libraries. Use this after installing new games or if the library seems out of sync.

### Portable Game Library
The game database (`games.json`) uses relative paths, so you can copy your library folder and the database to another computer and everything still works.

### Keyboard Navigation
You can navigate the entire app without a mouse:
- **Tab** to move between controls
- **Arrow keys** to navigate lists and trees
- **Enter/Space** to activate buttons and launch games
- **Escape** to clear filters

## File Locations

Game Chooser creates two files:

### games.json (Portable)
- Located in the application folder
- Contains your game library with relative paths
- Can be backed up or shared between computers

### config.json (User-Specific)
- **Windows**: `C:\Users\YourName\AppData\Roaming\GameChooser\`
- **macOS**: `~/Library/Application Support/GameChooser/`
- **Linux**: `~/.config/GameChooser/`
- Contains library paths, exceptions, and window preferences
- Not portable (contains system-specific paths)

## Troubleshooting

### No games found after scanning
- Verify the library folder actually contains game executables (.exe files on Windows)
- Check folder permissions—the app needs read access
- Look in **Preferences → Exceptions** to see if games were auto-excluded

### Game won't launch
- If the executable moved, the app will prompt you to locate it
- Verify the game executable still exists and hasn't been deleted
- On macOS/Linux, check that the file has executable permissions

### Scan takes a long time
- Large libraries (1000+ games) take a minute or two to scan
- Subsequent scans are faster due to incremental scanning
- Add exclusion folders in Preferences to skip large non-game directories

### Game metadata is wrong
- Press **Ctrl+E** to edit the game and fix the metadata manually
- Genre, developer, and year are user-editable for all games

### Search isn't working
- Search only looks at game title and developer fields (not genre or year)
- Searches containing "unknown" return no results by design
- Clear the search box to see all games again

## How Game Detection Works

When scanning a folder, Game Chooser looks for executables in this order:

1. Files named `game`, `launch`, or `play` (with or without .exe)
2. An executable matching the parent folder's name
3. If none found, the first executable in the directory

All other executables in that folder are automatically added to the exceptions list, which you can manage in Preferences.

## Support

Game Chooser is an open-source project. If you encounter issues or have suggestions, please check the project repository for support options.
