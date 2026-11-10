# CLAUDE.md

Documentation for Game Chooser - a wxPython desktop app for managing and launching games, and for installing
them from a curated catalog (`https://catalog.accessiblegames.net/`).

The app no longer scans folders. A **library** is a folder that catalog installs go into. Games come from the
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
  `library_actions.py`, `install_service.py`, `library_manager.py`, `engine/`. UI files (`main_window.py`,
  `dialogs.py`, `install_dialogs.py`, `catalog_panel.py`, `progress_area.py`, `description_field.py`) are not
  unit-tested; `wx_bridge.py` takes injectable `post` functions so its threading is tested without wx.
- `tests/engine/test_no_wx.py` enforces that `engine/` and the wx-free modules never import wx.
- Layout: `tests/engine/` (engine; fakes in `tests/engine/fakes.py`) and `tests/test_*.py` (catalog view, install
  choices, install prefs, install service, library actions, library integration, library manager, wx bridge,
  catalog fixtures).
- `tests/tracing/test_*.py` (tests for the `tracing/` modules, no wx, admin rights, Process Monitor or network) and
  `tests/test_check_no_tracing.py` (the build check) cover the tracing build.
- Mock minimal structures, test edge cases (empty/None, mixed states), keep logic out of the UI layer.

### Local test catalog
`scripts/serve_test_catalog.py` builds and serves a small catalog at `http://127.0.0.1:8765/` for trying installs
by hand. Run `uv run python scripts/serve_test_catalog.py`, then set **Preferences > Installing > Catalog address**
to that URL.
- `--version 2` republishes Test Game as version 2 (to try Update)
- `--add-launch-path` republishes "Test Game Without Launch Path" with its launch path
- `--throttle KBPS` sends game files slowly (time to cancel a download)
- `--build-only` builds the catalog and stops; `--port N` and `--build-dir` change the port and build folder

Sources: `tests/catalog_fixtures/entries/*.json` and `tests/catalog_fixtures/games/`. Output goes to
`tests/catalog_fixtures/_build/` (git-ignored). Every download address points at the local server.

## Project Structure

### Files (root; line counts approximate)
- **game-chooser.py** (37) - entry point
- **main_window.py** (1,845) - `MainFrame`: notebook, menus, shortcuts, queue handlers, modal safety, state
- **dialogs.py** (825) - `FirstTimeSetupDialog`, `GameDialog` (add/edit by hand), `PreferencesDialog`, `DeleteGameDialog`
- **install_dialogs.py** (279) - `InstallConfirmDialog`, `UninstallDialog`, `RemoveLibraryDialog`
- **install_choices.py** (73) - wx-free wording and decisions for the install confirmation dialog
- **install_prefs.py** (30) - wx-free validation of the Installing preferences
- **install_service.py** (331) - `InstallService`: catalog index + engine + queue + games/records/libraries
- **library_actions.py** (116) - wx-free: Library tab context menu, Edit/Delete rules, Uninstall/Remove wording
- **library_manager.py** (297) - `GameLibraryManager`: config, games.json, libraries, catalog game bookkeeping
- **catalog_panel.py** (523) - the Catalog tab; **catalog_view.py** (220) - wx-free rows, filters, description text
- **description_field.py** (62) - read-only multi-line description; Enter on a link line opens it
- **progress_area.py** (143) - gauge, status line, Done and Cancel for the running job
- **wx_bridge.py** (329) - queue listener and installer-UI bridges to the UI thread, `run_in_background`
- **game_list.py** (260) - `GameListCtrl` (smart_list wrapper; has a Status column); **models.py** (75) - `Game`
- **path_manager.py** (114), **validation_service.py** (103) - library-relative path helpers, executable check,
  and title/URL/path/year validation used by `GameDialog`
- **exception_manager.py** (207) - filename filter used only by the temporary launch fallback (see Install Engine)
- **engine/** (26 modules, ~9,600 lines) - the install engine; **scripts/** - `fetch_tools.py`,
  `serve_test_catalog.py`; **bin/** - bundled tools (git-ignored). `smart_list` (pinned in `[tool.uv.sources]`)
  and its dependency `resource_finder` are uv git dependencies, not on PyPI

### Where data lives
- `games.json` - next to the app (the `sys.argv[0]` directory); git-ignored. It and `config.json` are written
  atomically (temp file, then `os.replace`). A file that can't be read is copied to `<name>.bad` (or
  `<name>.<YYYYMMDD-HHMMSS>.bad` when an earlier copy exists; no copy is ever overwritten) before anything can
  write over it, and the player is told once after startup (`manager.load_problems`). Until the copy exists,
  saves raise OSError and write nothing (see Configuration).
- Config dir: Windows `%APPDATA%\GameChooser\`, macOS `~/Library/Application Support/GameChooser/` (other systems
  use the macOS path). It holds:
  - `config.json`
  - `installs\<catalog id>.json` - per-user install records (`engine/records.py`, `RecordStore`)
  - `catalog\` - cached index (`index.json.gz`, `index-meta.json`); offline, the saved copy is used
  - `install.log` - the install queue's log
- Shared runtimes: `%ProgramData%\GameChooser\runtimes\<id>`.

## Architecture Overview

### Data model
**Game** (`models.py`): `title, genre, developer, year, platforms, launch_path, library_id, catalog_id,
installed_source, installed_extras`. `is_catalog` is `bool(catalog_id)`. `to_dict()` writes `catalog_id`,
`installed_source`, `installed_extras` only when set. `launch_path` is library-relative (forward slashes) for
desktop games, or an `http...` URL for web games.

### Libraries (install folders)
- Stored in `config["libraries"]` as `{"id": <secrets.token_hex(6)>, "path": <folder>}`. The id is stable; games
  reference `library_id`, so pointing a library at another folder keeps its games.
- `GameLibraryManager.add_library(path)`, `change_library_folder(id, path)`, `remove_library(id)` (config only),
  `library_by_id`, `library_reachable`. `_check_folder` raises `LibraryError` for a missing folder, a duplicate, or
  a folder inside or containing another library.
- Preferences > Library Paths: **Add**, **Change folder**, **Remove**. Remove on a library holding catalog
  installs uninstalls them first (`InstallService.remove_library`, `RemoveLibraryDialog`, optional delete of the
  player's files); the library goes when the last uninstall result is applied.
- A library whose folder is unreachable (unplugged drive) shows Status "Library not found"; launching explains it.
- No migration (alpha). Old-config handling is only in `load_config`: it drops the old `exceptions` key and the
  library `name`, and assigns an id to a library without one (saved once, so it stays stable).
- `reconcile_catalog_records()` runs at startup to keep games.json and the install records (games' only) in step:
  a record with no game gets one; a game whose record is gone (or unsafe id) becomes a game added by hand; a game
  whose uninstall stopped partway stays linked; one whose uninstall finished but never reached games.json leaves
  the list (the record stays).

### Main window (`MainFrame`)
A `wx.Notebook` with two pages (`TABS = ("library", "catalog")`; `active_tab()`):

```
┌────────────────────────────────────────────────┐
│ Menu bar: File / Edit / View / Launch          │
├────────────────────────────────────────────────┤
│ [Library] [Catalog]  (notebook tabs)           │
├────────────────────────────────────────────────┤
│ Library tab:  Search                           │
│  Game list (..., Status)  |  Filter tree       │
│  Description (read-only)  |  Platform→Genre    │
│  Launch button + progress |  →Developer→Year   │
│                                                │
│ Catalog tab:  status line + Retry, Search      │
│  Catalog list             |  Filter tree       │
│  Description, action button, progress area     │
└────────────────────────────────────────────────┘
```

- **Library tab**: the player's games (`GameListCtrl`: sorting, keyboard nav, persisted column widths) with a
  **Status** column, a description field (`DescriptionField`: catalog text for catalog games, empty for others), a
  filter tree (Platform/Genre/Developer/Year, multi-select OR) and View > Libraries to hide libraries.
  Status values (`engine/status.py`): `Ready`, `Update available`, `Executable not found`, `Library not found`.
  Statuses are computed on a worker thread (`refresh_statuses`) and applied on the UI thread.
- **Catalog tab** (`CatalogPanel`): index entries as rows (`catalog_view.build_rows`), a filter tree (platform,
  genre, developer, year, flags, installed), search, and an action button that reads Install / Update / Add /
  Launch per row (`catalog_view.action_kind`). Links open in the browser only when the player chooses them. The
  panel never fetches: `MainFrame.start_index_fetch` fetches on a worker and hands the state to `_take_index`
  through `run_when_no_dialog`. Retry and F5 fetch again.
- **Install confirmation** (`InstallConfirmDialog`; wording and decisions in `install_choices.py`): library
  choice (or a fixed one), optional extras, dependencies, and a blocked reason from `InstallService.blocked`.
  Returns an `InstallChoice(library_id, library_path, extras)`.
- **Progress area** (`ProgressArea`): shows the queue's current job, never the selected game. One copy on the
  Catalog tab and one beside Launch, all driven by `MainFrame._sync_progress`. Done appears while an installer
  runs; Cancel cancels the job. It never takes focus.
- **Library catalog actions** (`library_actions.py`): context menu for catalog games (Update, Undo, Extras,
  Repair, Uninstall, plus links). Delete on a tracked catalog install is **Uninstall** (`delete_kind`: "uninstall";
  "forget" for an untracked install, files untouched; else "delete"). An uninstall that stopped partway (record
  `uninstalled` with files left or a vendor uninstaller still to run: `is_uninstall_unfinished`) still offers
  Uninstall, and Delete opens it. Shared runtimes' records (kind "runtime") are never Library games: plan-2 code
  reads records through `RecordStore.game_records` / `load_game` / `delete_game`. Removing a library with installs is
  **Remove library**. **Edit is only for games added by hand** (`is_editable`: no `catalog_id`, no `library_id`).
- **Preferences** (`PreferencesDialog`): tabs Library Paths and Installing (`install_prefs.py`: catalog address,
  "register shared components"). An invalid address saves nothing; a changed address refetches the catalog on close.
- **FirstTimeSetupDialog**: shown when config.json didn't exist; the player can add a library or skip.
  `catalog_view.initial_tab(saved, is_first_run)` picks the starting tab.

### Install service (`install_service.py`)
`InstallService(manager, listener, installer_ui, ...)` ties together the index (`IndexState`), `InstallEngine`,
`InstallQueue` (log `install.log`), `manager.records` and the libraries. Main members: `visible_games`,
`installed_ids`, `plan`, `blocked`, `submit(action, entry_id, library_id, library_path, ...)`, `apply_result`,
`job_failed`, `forget`, `player_files`, `record_for`, `entry_for`, `statuses`, `ensure_library`, `library_games`,
`check_library_removable`, `remove_library`, `adopt_launch_paths`.

**Threading rule.** `fetch_index()` is the only member safe on any thread, and it changes nothing. Everything that
mutates games, records or libraries runs on the **UI thread only**: `take_index`, `load_cached_index`,
`refresh_index`, `adopt_launch_paths`, `apply_result`, `job_failed`, `submit`, `forget`, `remove_library`, and so
on. Pattern: fetch on a worker (`wx_bridge.run_in_background`), then `take_index` on the UI thread.

### Queue and installer bridges (`wx_bridge.py`)
- `WxQueueListener` runs on the queue's notifier thread and only posts to `MainFrame.on_job_started /
  on_job_progress / on_job_finished / on_job_failed / on_job_cancelled / on_queue_changed`. Outcomes also go into
  `JobOutcomes` so quit can apply any that never reached the UI.
- `WxInstallerUI` runs on the engine's worker: each prompt (announce target, wait for installer, locate folder)
  is posted to the UI thread and blocks on an Event. `release_all()` answers open prompts as cancelled on quit.
- `call_after(fn, *args)` and `run_in_background(work, done, name, ...)`.

### Modal-safety rule
wx runs `CallAfter` events inside a modal loop, so work that replaces games must not run under a dialog that
holds a game object.
- `MainFrame.modal_dialog()` (context manager; nests; sets `dialog_active`): **every dialog that holds a game
  goes inside it**, message boxes included.
- `MainFrame.run_when_no_dialog(fn, *args)` (UI thread): run now if no modal dialog is open, else queue it (in
  order) until the outermost `modal_dialog()` closes. Used for index takes, job outcomes
  (`_apply_finished`, `_apply_failed`, `_apply_cancelled`) and the uninstall confirmation.
- `PreferencesDialog` sets `holds_no_games = True`: it holds library ids only, is **not** opened in
  `modal_dialog()`, and `_modal_open()` ignores it, so job results apply while it is open (through
  `job_done_hooks`). The prompts it opens use the parent's `modal_dialog()` and do defer.
- `_live_game(game)` re-resolves a listed game to the library's current object (an index take can replace it).
- **Quit** (`on_close`): ask if a job runs; `installer_ui.release_all()`; `queue.cancel_all()`; `save_state()`;
  `queue.shutdown(QUIT_WAIT_SECONDS)`; `_apply_outcomes_on_quit()` (job outcomes are applied **before** games.json
  and config are saved); `Destroy()`; and `on_close` **ends with `ExitMainLoop()`** so a hidden window or balloon
  can't keep the app alive.

### Install Engine (`engine/`)
A wx-free package that installs, updates, repairs, undoes and uninstalls games from the catalog, with tests in
`tests/engine/`. It never imports wx (a test enforces it), never runs elevated itself (only regsvr32, a
machine-wide registry step, an installer flagged for elevation and the runtime lock-down chain go through UAC),
and never deletes or overwrites a player-owned file without a copy (only `.exe`, `.dll` and `.ocx` are replaced
in place).

- **jobs.py** - `InstallEngine.run(InstallRequest)` routes install/update/repair/undo/extras/uninstall; raises
  only `InstallFailed` (plain words from `messages.py`) or `Cancelled`.
- **index.py**, **entries.py** - catalog index fetch/cache (`CatalogIndex`, `IndexState`) and entry helpers.
- **safety.py** - the three-checksum planner and apply/uninstall rules; **records.py** - per-user install records
  in `%APPDATA%\GameChooser\installs`; **sources.py** / **http.py** - verified downloads (agarchive.net blocked).
- **extract.py** - unpacks with the bundled tools; **run_installer.py** / **run_actions.py** /
  **installer_watch.py** - installers that run.
- **runtimes.py** - shared runtimes under `%ProgramData%\GameChooser\runtimes\<id>` (sealed by an elevated chain,
  marker `.gamechooser-runtime.json`), or private COM copies (`comfree.py`). By default every runtime the game
  depends on that has a folder (not an installer that runs) also gets its `.dll`/`.ocx` files copied beside the
  game's launch exe (layout kept; the game's own file of that name wins; hash-checked from a trusted folder or the
  private staging copy; recorded with owner `runtime:<id>`, ids in the record's `runtime_copies`), so a game that
  loads a runtime DLL by name finds it; **steps.py**, **registry.py**,
  **elevation.py** - catalog steps and the one program that runs elevated.
- **queue.py**, **status.py**, **plan.py**, **folders.py** - the job queue, library status, the install
  confirmation plan, and target-folder naming (`<library>/<Title>`).

**Bundled tools requirement:** `engine/extract.py` looks for `7z.exe`, `7z.dll` and `innoextract.exe` in the
`bin/` folder beside the executable. `scripts/fetch_tools.py` downloads them (SHA256-pinned) into `bin/` with their
licenses in `bin/licenses/`. Without `bin/`, every zip, 7z, cab, Inno and NSIS install fails.

**Temporary launch fallback.** `engine.launch.select_best_executable` (prefers `game`/`launch`/`play`, then an exe
named like the folder, then the first) plus `ExceptionManager.should_auto_exclude` (skips setup/uninstall-style
exes) guess a launch exe when the catalog records no launch path. It is temporary until the catalog records
launch paths; `adopt_catalog_launch` switches a guessed path to the catalog's once one exists. **Nothing new may
call it.**

## Behavior Notes

### Launching
- Web games open the URL in the browser. Desktop games resolve the library-relative path through the library, run
  with the game's folder as working directory, and minimize the window after a successful launch.
- A missing executable branches on `library_actions.launch_problem`: `unreachable` (library folder gone),
  `repair` (tracked catalog install still in the catalog), `catalog` (say so, no file picker) or `locate`
  (hand-added game: pick the file).
- Platform compatibility is checked against the user's OS first; web games are compatible everywhere.

### Keyboard Shortcuts
- **F5**: Library: rebuild list, tree and menu and recheck Status. Catalog: check for catalog updates.
- **Ctrl+R**: random game from the active tab's filtered list (asks before launching).
- **Ctrl+Shift+R** (Launch > Random Game to Install): random game from the Catalog tab's filtered list that
  installs (`catalog_view.install_candidates`: not installed, not a web game, no job queued or running); switches to
  the Catalog tab first, asks, then opens the install confirmation as the Install button does.
- **Del**: Library only (Uninstall for a tracked catalog install, forget for an untracked one, else delete).
- **Ctrl+E**: Library only; refuses catalog games and says why (its accelerator stays enabled for that, while the
  Edit menu item is disabled). Edit and Delete menu items are disabled on the Catalog tab.
- **Ctrl+N** add game, **Ctrl+F** search, **Ctrl+Enter** open folder, **Ctrl+,** preferences, Enter/Space launch.

### Configuration (`config.json`)
- `libraries`: `[{"id", "path"}]`
- `install`: `catalog_url` (default `DEFAULT_CATALOG_URL`), `register_shared_components` (default true),
  `last_library` (library last chosen for an install)
- `SavedState`: `window_size`, `window_position`, `splitter_position`, `sort_column`, `sort_ascending`,
  `last_selected` (title), `last_search`, `column_widths`, `tree_expansion`, `tree_selections` (path strings such
  as `Platform/Windows`), `tree_filters` (subset of platform/genre/developer/year), `last_tab`
  ("library" or "catalog"), `catalog_last_selected`, `catalog_last_search`, `catalog_tree_selections`;
  `hidden_libraries` is added on first use.
- A missing config starts from defaults (first run); missing keys (also inside `SavedState` and `install`) are
  filled in. A config that can't be read (bad JSON or the wrong shape) is copied to `config.json.bad` first,
  the app runs on defaults, and the player is told once where the copy is. Re-adding a library mints a new id, so
  installed games stay "Library not found" until the copy is restored by hand. The same
  rule covers `games.json` (`games_load_failed` is set; the list starts empty).

### Accessibility and focus
- Screen reader announcement needs the sequence `Select()`, `Focus()`, `SetFocus()` (`focus_active_list`).
- Flags on `MainFrame`:
  - **`initializing`**: no `SetFocus()` during startup (don't steal focus from the first-run dialog)
  - **`dialog_active`**: set by `modal_dialog()`; blocks selection events and defers work during modal dialogs
  - **`restoring_tree`**: blocks `save_tree_selections()` while the tree is restored, to prevent loops
- `on_filter_complete()` fills the list, restores the selection by title and manages focus. The progress area and
  background refreshes never move focus. The description is a read-only multi-line field so a screen reader reads
  it line by line.
- Library search: filters on every keystroke (no delay), case-insensitive substring over title and developer; a
  search containing "unknown" matches nothing.

### Threading
UI thread for all wx work and every change to games, records and libraries. Background threads: `FilterWorker`,
index fetch, status computation, the install queue's worker and notifier. See the Install service and
Modal-safety sections.

## Development Notes
- Paths use forward slashes internally and are library-relative; `path_manager` converts.
- Executable detection: Windows `.exe`/`.bat`; macOS `.app` and executable files; Linux executable files. Installs
  are Windows-focused (registry, UAC, `bin/*.exe`).

## Tracing build (`tracing/`)
A build-time variant for the catalog admin: it records every file and registry write the launched games make, with
Process Monitor, and splits the capture into one folder per play session. Normal builds contain none of it.
Log format: `tracing/LOG-FORMAT.md`. Build: see Build and Distribution.

- **Rule:** nothing outside `tracing/` imports it; the one exception is `game-chooser.py`, inside
  `try/except ImportError`. `procmon_parser` is imported only inside `tracing/`, lazily, inside functions. Only
  `app_glue.py` imports wx. The game launch is unchanged: `MainFrame` calls `frame.launch_observer` around its
  `engine.launch.start_program` (passing on the process ID it returns), and shows `frame.title_note` in the title.
  The normal build is checked by `scripts/check_no_tracing.py` (fails the build on any tracing module or
  `tracing`/`procmon`/`traces` file, or a report with no modules).
- **Commands** (`game-chooser.py`, through `tracing/cli.py`): `--tracing` (turn tracing on from source; a compiled
  tracing build is always on), `--trace-recorder RUN --app-pid N --procmon EXE` (the elevated recorder, started by
  the app), `--trace-split [RUN...]` (split leftover runs after a crash; prints "No runs to split." when there are none;
  a named RUN that has `split.json` and no capture left prints "already split, nothing to do"; a named RUN whose
  capture was kept is split again; without RUN only runs with no `split.json` are split),
  `--trace-probe OUT [--workload BAT]` (one short capture to learn what the real capture holds),
  `--trace-check FOLDER` (the trace-test acceptance check on a session folder, for a machine with only the compiled
  build: prints `PASS` or one problem per line; exit 0 pass, 1 problems, 2 not a session folder), `--traces-dir PATH`
  (use another traces folder). Exit codes: 0 done, 1 a split problem, 2 bad command line.
- **Where logs go:** `traces/` beside the exe (from source: beside `game-chooser.py`; git-ignored). One folder per
  play session, `<catalog id or title>-<YYYYMMDD-HHMMSS>`, holding `session.json` and `events.jsonl`. The recorder's
  working files live in `traces/_runs/<YYYYMMDD-HHMMSS>-<pid>/`: `status.json`, `sessions/<id>.json`, `filter.pmc`,
  `capture.pml` (and `capture-N.pml`), `recorder.log`, `split.json` (`folders`, `problems`, `capture_kept`). The
  capture and `filter.pmc` are removed after a split with no problems, and kept (`capture_kept: true`) when any
  session has a problem or a session file can't be read. Atomic writes retry `os.replace` while Windows says the
  file is held (`runfiles.replace_with_retry`).
- **Attribution:** a session is the launched process plus its descendants by parent links in the capture. The root
  is matched by pid and the start time read after the launch; failing an exact match, by pid within 2 s of the launch
  call, closest to that start time (`attribution.find_root`). A launch that gave no pid (the administrator prompt's
  shell handed back no process) is recorded with `root_pid` 0 and no start time; its root is then the process whose
  image is the game's exe that started from 2 s before the launch call to 2 s after it returned, never one another
  session holds (pid-known sessions are matched first), preferring the earliest that started at or after the call,
  else the closest to it (`attribution.find_root_by_exe`; the live tree uses the same rule). `process_exit` lines
  are written for every process whose exit is in the capture. `session.json` env carries `app_build` (short git
  commit from source, else null).
- **Capture filter** (`procmon.EXCLUDED_PROCESSES`): no events from `Procmon.exe`, `Procmon64.exe`, `System`,
  `game-chooser.exe` (the app and the recorder) or the extraction tools `7z.exe` and `innoextract.exe`; installs in a
  traced run would otherwise fill the capture, and none of their writes are a game's.
- **Title:** `Game Chooser (tracing starting)`, `(tracing)` or `(tracing off)`. When off, a dialog says why and
  games launch untraced after the player confirms.
- **Mark key:** Ctrl+Alt+Shift+M adds a numbered mark to every running traced game. Windows OK beep = mark recorded;
  error beep = no game being traced.
- **Modules** (line counts approximate):
  - `cli.py` (200) - commands, `--traces-dir`, `tracing_wanted`, `attach_app`
  - `app_glue.py` (260) - the only wx module: starts the recorder (one UAC prompt), title, launch observer, mark key
  - `messages.py` (30) - every UI and recorder message
  - `client.py` (130) - app side: starts the recorder, writes session files, records launches and marks
  - `recorder.py` (175) - elevated side: drives Process Monitor for the whole run, then splits
  - `procmon.py` (120) - Process Monitor's filter file, start, ready check, stop
  - `pml.py` (100) - reads a capture into `ProcInfo` and `RawEvent` (lazy `procmon_parser`)
  - `live.py` (200) - live process snapshot (Windows) used for roots and the running tree
  - `runfiles.py` (150) - `RunDir`, `new_run`, `leftover_runs`; `splitrun.py` (70) - split a run folder
  - `split.py` (145) - attribute events to sessions and merge writes; `attribution.py` (90) - the process tree
  - `kinds.py` (50) - event to write kind; `virtualstore.py` (20) - the VirtualStore path
  - `output.py` (85) - writes `session.json` and `events.jsonl`; `model.py` (60), `clock.py` (30), `paths.py` (40)
  - `probe.py` (210) - the `--trace-probe` command
  - `check_trace_test.py` (80) - `python -m tracing.check_trace_test <session folder>` checks a session of the
    trace test game (`tests/catalog_fixtures/games/trace-test`)

## Build and Distribution

```bash
./build_windows.sh
```
Needs Nuitka (run as `python -m nuitka` through `uv`) and a C compiler. It fetches the extraction tools into `bin/`
when missing, builds a standalone folder `build/game-chooser.dist/` (`--include-package=engine`), then ships `bin/`
(with `bin/licenses/`) and `THIRD_PARTY.md` next to the exe. `./build_windows.sh --ship-only DIST` only ships the
tools into an existing build folder. Distribute the whole `game-chooser.dist` folder.

The normal build leaves out `tracing/` and `procmon_parser` (`--nofollow-import-to`), and afterwards runs
`scripts/check_no_tracing.py build/compilation-report.xml build/game-chooser.dist`, which fails the build if any
tracing module or `tracing`/`procmon`/`traces` file got in, or if the report lists no modules at all (an old report
is deleted before Nuitka runs; a build that fails the check has its dist folder removed).
`./build_windows.sh --tracing` builds the tracing variant into `build/tracing/` instead (with Process Monitor in
`procmon/` and a `TRACING-BUILD.txt` warning); it is for the catalog admin's test machines only and is never
distributed.

### License
GNU GPLv3 - see LICENSE.txt
