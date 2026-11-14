---
paths:
  - "/library_manager.py"
  - "/models.py"
  - "/validation_service.py"
  - "/dialogs.py"
  - "/tests/test_library_manager.py"
  - "/tests/test_library_integration.py"
---

# Libraries and configuration

## Files
- **library_manager.py** - `GameLibraryManager`: config, games.json, libraries, catalog game bookkeeping
- **validation_service.py** - the title, URL and year checks `GameDialog` uses (module functions)

## Libraries (install folders)
- Stored in `config["libraries"]` as `{"id": <secrets.token_hex(6)>, "path": <folder>}`. The id is stable; games
  reference `library_id`, so pointing a library at another folder keeps its games.
- `GameLibraryManager.add_library(path)`, `change_library_folder(id, path)`, `remove_library(id)` (config only),
  `library_by_id`, `library_reachable`. `_check_folder` raises `LibraryError` for a missing folder, a duplicate, or
  a folder inside or containing another library.
- Preferences > Library Paths: **Add**, **Change folder**, **Remove**. Remove on a library holding catalog
  installs uninstalls them first (`InstallService.remove_library`, `RemoveLibraryDialog`, optional delete of the
  player's files); the library goes when the last uninstall result is applied.
- A library whose folder is unreachable (unplugged drive) shows Status "Library not found"; launching explains it.
- An older config is not upgraded: a library that is not an object with an `id` makes the config damaged (below),
  and old keys stay in the file. The no-migration rule is in CLAUDE.md (Migration).
- `reconcile_catalog_records()` runs at startup to keep games.json and the install records (games' only) in step:
  a record with no game gets one; a game whose record is gone (or unsafe id) becomes a game added by hand; a game
  whose uninstall stopped partway stays linked; one whose uninstall finished but never reached games.json leaves
  the list (the record stays).
  - A record with no game first looks for a game added by hand (not a web game) that launches the same file: the
    full path, ignoring case and slashes, or the same library id and relative path while that library is unknown.
    That game is linked again (catalog id, library, relative path, installed source and extras from the record;
    its title and details stay) instead of a second game being added. This undoes one start without the records
    (another `%APPDATA%`, a missing `installs\`), which unlinks every catalog game. Another exe in the same folder,
    the same relative path in another library, or an uninstalled record never links a game.

## Configuration (`config.json`)
- `libraries`: `[{"id", "path"}]`
- `install`: `catalog_url` (default `DEFAULT_CATALOG_URL`), `register_shared_components` (default true),
  `last_library` (library last chosen for an install)
- `SavedState`: `window_size`, `window_position`, `last_tab` ("library" or "catalog"). Each tab's games view
  (`GamesPanel`) keeps `last_selected` (the selected row's id: the catalog id, else the launch path),
  `last_search`, `tree_selections` (path strings such as `Platform/Windows`), `sort_column`, `sort_ascending`,
  `column_widths` and `splitter_position`: the Library under those names, the Catalog under the same names with
  `catalog_` in front. Library only: `tree_filters` (subset of platform/genre/developer/year);
  `hidden_libraries` is written when the player first hides or shows a library. Every `SavedState` value is read
  through `GameLibraryManager.saved(key, kind, default, item)`: a value of the wrong kind (a hand-edited file) is
  ignored and the default used, and a list keeps only its items of the right kind.
- A missing config starts from defaults (first run); missing keys (also inside `SavedState` and `install`) are
  filled in.

## A `games.json` or `config.json` that can't be read
- A `games.json` or `config.json` that can't be read is copied to `<name>.bad` (or
  `<name>.<YYYYMMDD-HHMMSS>.bad` when an earlier copy exists; no copy is ever overwritten) before anything can
  write over it, and the player is told once after startup (`manager.load_problems`). Until the copy exists,
  saves raise OSError and write nothing.
- A config that can't be read (bad JSON or the wrong shape) is copied to `config.json.bad` first,
  the app runs on defaults, and the player is told once where the copy is. Re-adding a library mints a new id, so
  installed games stay "Library not found" until a config listing their libraries is put back by hand (the copy,
  if it can be read or repaired; it may hold nothing useful, such as a file holding just `{`). The same
  rule covers `games.json` (`games_load_failed` is set; the list starts empty).

## Paths and executable detection
- Paths inside a library use forward slashes internally and are library-relative; `GameLibraryManager.get_full_path`
  joins one to its library's folder (a web address or an absolute path is returned as it is).
- Executable detection (`library_actions.executable_problem`, used by Add Game and Locate): a file ending in
  `.exe` or `.bat`, or
  a folder ending in `.app` (a macOS bundle). Nothing else counts: `.cmd` is not accepted, the executable bit is
  not checked and there is no Linux case.
