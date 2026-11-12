# CLAUDE.md

Documentation for Game Chooser - a wxPython desktop app for managing and launching games, and for installing
them from a curated catalog (`https://catalog.accessiblegames.net/`).

The app does not scan folders. A **library** is a folder that catalog installs go into. Games come from the
catalog (installed into a library) or are added by hand.

## Development Commands

```bash
uv sync                      # install dependencies
uv run game-chooser.py       # run the app
uv run pytest -q             # the whole tests/ tree: no wxPython, pywin32 or network needed
uv run pytest -q -m tools    # only the tests that need the bundled extraction tools in bin/
uv run python scripts/fetch_tools.py   # fetch 7z.exe, 7z.dll, innoextract.exe into bin/ (not committed)
```

Markers (`pyproject.toml`): `tools` tests need `bin/7z.exe` and skip when it is absent; `windows` tests touch the
real registry (only `HKCU\Software\GameChooserTests\<uuid>`) or other Windows-only behavior and skip elsewhere.

### Testing Strategy
- Logic lives in wx-free modules so it is unit-tested: `catalog_view.py`, `install_choices.py`, `install_prefs.py`,
  `library_actions.py`, `install_service.py`, `library_manager.py`, `language_names.py`, `engine/`. UI files
  (`main_window.py`, `dialogs.py`, `install_dialogs.py`, `catalog_panel.py`, `progress_area.py`,
  `description_field.py`) are not unit-tested; `wx_bridge.py` takes injectable `post` functions so its threading
  is tested without a running wx app (the test skips when wxPython is not installed). `games_panel.py` (the games
  view both tabs are built from) is tested on a frame that is never shown (`tests/test_games_panel.py`, which
  skips when wxPython is not installed or there is no display); so are two parts of `catalog_panel.py`, what its
  action button does while a job runs and how its columns sort (`tests/test_catalog_panel.py`).
- `tests/engine/test_no_wx.py` enforces that `engine/` and the wx-free modules never import wx, and the two
  tracing rules: no `tracing/` module except `app_glue.py` imports wx, and nothing outside `tracing/` imports it
  (`game-chooser.py` excepted; the `tests` and `scripts` folders are not checked).
- Layout: `tests/engine/` (engine; fakes in `tests/engine/fakes.py`) and `tests/test_*.py` (catalog view, catalog
  panel, games panel, install choices, install prefs, install service, language names, library actions, library
  integration, library manager, wx bridge, catalog fixtures).
- `tests/tracing/test_*.py` (tests for the `tracing/` modules, no admin rights, Process Monitor or network; no wx
  except `test_app_glue.py`, which skips when wxPython is not installed) and `tests/test_check_no_tracing.py` (the
  build check) cover the tracing build.
- Mock minimal structures, test edge cases (empty/None, mixed states), keep logic out of the UI layer.

## Rules that hold everywhere

### Threading
UI thread for all wx work and every change to games, records and libraries; both tabs' lists are filtered and
sorted there too. Background threads: index fetch, status computation, the player-file counts, `GameDialog`'s
duplicate check, the install queue's worker and notifier.

**`InstallService` threading rule.** Three members change no games, records or libraries, and the code runs them
on worker threads: `fetch_index()` (it also saves the fetched index in the cache folder), `statuses()` and
`player_files()` (both only read). Everything that mutates games, records or libraries runs on the **UI thread
only**: `take_index`, `load_cached_index`, `refresh_index`, `adopt_launch_paths`, `apply_result`, `job_failed`,
`submit`, `forget`, `remove_library`, and so on. Pattern: fetch on a worker, then `take_index` on the UI thread.
What starts each worker: `MainFrame.start_index_fetch` and `MainFrame.refresh_statuses` each start their own
daemon thread, the player-file counts go through `wx_bridge.run_in_background`, and `GameDialog` starts its own
thread for the duplicate check.

### Modal safety
wx runs `CallAfter` events inside a modal loop, so work that replaces games must not run under a dialog that
holds a game object.
- `MainFrame.modal_dialog()` (context manager; nests; sets `dialog_active`): **every dialog that holds a game
  goes inside it**, message boxes included.
- `MainFrame.run_when_no_dialog(fn, *args)` (UI thread): run now if no modal dialog is open, else queue it (in
  order) until the outermost `modal_dialog()` closes.

The `PreferencesDialog` exception, `_live_game` and the quit sequence are in `.claude/rules/ui-main-window.md`.

### Install engine
- The install engine (`engine/`) never runs elevated itself (only regsvr32, a machine-wide registry step, an
  installer flagged for elevation or one whose manifest asks for administrator rights, and the runtime lock-down
  chain go through UAC), and never deletes or overwrites a player-owned file without a copy (only `.exe`, `.dll`
  and `.ocx` are replaced in place).
- The temporary launch fallback (`engine.launch.select_best_executable` plus
  `ExceptionManager.should_auto_exclude`, described in `.claude/rules/engine.md`) guesses a launch exe.
  **Nothing new may call it.**

### Launch pages
On Windows a catalog game's launch page (`.html` or `.htm`) opens through the shell, in whatever program the
player has for `.html`, so `launch_kind`'s suffix check is the only gate: never widen `PAGE_SUFFIXES` assuming
"the browser" will handle the file safely.

An `.hta` (HTML Application) is not a page: it is a program with the player's full rights, like an exe. It never
goes into `PAGE_SUFFIXES`, `webbrowser` or the shell's file association. `engine.launch.start_hta` runs Windows'
own `mshta.exe`, taken from the system folder Windows reports (never PATH, never the game's folder), with the
file's full path as its one argument. It starts only when the catalog's launch path names it: it is never guessed,
and `library_actions.may_start_hta` allows only the file that is exactly the install record's own launch file
(`launch_from_catalog`), whatever path games.json holds. A game added by hand never gets the host. `MainFrame`
launches through `library_actions.start_launch_file`, never `start_game` directly.

### Tracing
Nothing outside `tracing/` and its tests (`tests/tracing/`) imports it; the one exception in app code is
`game-chooser.py`, inside `try/except ImportError`.

### Focus
The progress area and background refreshes never move focus, with two exceptions. The first is `GamesPanel`'s:
when the control that has focus is about to become unusable (the action button as its list empties, the progress
area as it hides), focus goes to that tab's games list; never while a dialog is open.

The second is `CatalogPanel`'s, decided on Nov 12, 2026: it stays, so that a catalog that could not be fetched is
announced. It moves focus on a background event in two places. When a fetched index is taken and there is no
catalog to show (the status line says why, with Retry) while the Catalog tab is showing, focus goes to the status
line (`set_index_state`). When the status line or Retry hides while it has focus, focus goes to the Catalog's
games list (`_update_status`). Neither happens while a dialog is open or the app is starting.

### Migration
No migration (alpha): write no migration code.

## Data model
**Game** (`models.py`): `title, genre, developer, year, platforms, launch_path, library_id, catalog_id,
installed_source, installed_extras`. `is_catalog` is `bool(catalog_id)`. `to_dict()` writes `catalog_id`,
`installed_source`, `installed_extras` only when set. `launch_path` is library-relative (forward slashes) for
a desktop game in a library, an absolute path for a desktop game with no `library_id` (one added by hand, or a
catalog game an installer put outside every library), or an `http...` URL for web games.

## Where data lives
- `games.json` - next to the app (the `sys.argv[0]` directory); git-ignored. It and `config.json` are written
  atomically (temp file, then `os.replace`). What happens to one that can't be read is in
  `.claude/rules/libraries-and-config.md`.
- Config dir: Windows `%APPDATA%\GameChooser\`, macOS `~/Library/Application Support/GameChooser/` (other systems
  use the macOS path). It holds:
  - `config.json`
  - `installs\<catalog id>.json` - per-user install records (`engine/records.py`, `RecordStore`)
  - `catalog\` - cached index (`index.json.gz`, `index-meta.json`); offline, the saved copy is used
  - `install.log` - the install queue's log
- Shared runtimes: `%ProgramData%\GameChooser\runtimes\<id>`.

## Detail by area (`.claude/rules/`)
Each file loads when a file it covers is read or edited.
- `engine.md` - the install engine (`engine/`, `tests/engine/`, `exception_manager.py`): its modules, extras,
  runtimes, the bundled tools and the temporary launch fallback.
- `tracing.md` - the tracing build (`tracing/`, `tests/tracing/`, `game-chooser.py`): commands, where logs go,
  attribution, the capture filter, modules.
- `catalog-install.md` - the install service, queue and installer bridges, the Catalog tab, catalog schema v2,
  install confirmation, progress area, Library catalog actions and launching.
- `ui-main-window.md` - the main window and Library tab, the games view both tabs are built from (`GamesPanel`),
  the rest of modal safety, the quit sequence, keyboard shortcuts, accessibility and focus, the Preferences and
  first-run dialogs.
- `libraries-and-config.md` - libraries (install folders), `reconcile_catalog_records`, `config.json`, a damaged
  `games.json` or `config.json`, paths and executable detection.
- `build.md` - `build_windows.sh`, the scripts, the dependencies that are not on PyPI, the license.
- `test-catalog.md` - the local test catalog and its fixtures.

## Keeping these docs current
- Detail lives in `.claude/rules/`. This file holds only the commands, the rules that hold everywhere, the data
  model and where data lives.
- Document a change in the rules file that covers the changed code (the list above).
- Keep this file under 200 lines.
- Do not record line counts or module counts.
