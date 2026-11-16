---
paths:
  - "/install_*.py"
  - "/catalog_*.py"
  - "/games_panel.py"
  - "/library_*.py"
  - "/wx_bridge.py"
  - "/main_window.py"
  - "/progress_area.py"
  - "/description_field.py"
  - "/steam_games.py"
  - "/tests/test_install_*.py"
  - "/tests/test_catalog_view.py"
  - "/tests/test_library_*.py"
  - "/tests/test_wx_bridge.py"
  - "/tests/test_steam_games.py"
---

# Catalog, installing and launching

## Files
- **install_choices.py** - wx-free wording and decisions for the install confirmation dialog
- **install_prefs.py** - wx-free validation of the Installing preferences
- **install_service.py** - `InstallService`: catalog index + engine + queue + games/records/libraries
- **library_actions.py** - wx-free: Library tab context menu, Edit/Delete rules, Uninstall/Remove wording
- **catalog_panel.py** - the Catalog tab: its status line, Retry and what a row's action does, around a
  `GamesPanel` (`games_panel.py`, the games view both tabs share; see `.claude/rules/ui-main-window.md`);
  **catalog_view.py** - wx-free rows for both tabs (`build_rows` for catalog entries, `library_rows` for the
  player's games), filters, `pick_index`, the type-ahead matching, description text, links, and the English
  names of ISO 639-1 language codes (unknown codes shown as written)
- **wx_bridge.py** - queue listener and installer-UI bridges to the UI thread, `run_in_background`
- **steam_games.py** - wx-free: `steam_changes` (the Steam rows a scan adds and removes) and `ScanGate` (one Steam
  scan at a time)

## Install service (`install_service.py`)
`InstallService(manager, listener, installer_ui, ...)` ties together the index (`IndexState`), `InstallEngine`,
`InstallQueue` (log `install.log`), `manager.records` and the libraries. Main members:
`installed_ids`, `plan`, `blocked`, `submit(action, entry_id, library_id, library_path, ...)`, `apply_result`,
`job_failed`, `forget`, `player_files`, `record_for`, `entry_for`, `statuses`, `ensure_library`, `library_games`,
`check_library_removable`, `remove_library`, `adopt_launch_paths`, and for Steam `take_steam_scan`, `match_steam`,
`steam_sent`, `steam_folder_of`, `also_on_steam` (Installed Steam games, below).

Which thread each member runs on: see CLAUDE.md (Threading).

## Queue and installer bridges (`wx_bridge.py`)
- `WxQueueListener` runs on the queue's notifier thread and only posts to `MainFrame.on_job_started /
  on_job_progress / on_job_finished / on_job_failed / on_job_cancelled / on_queue_changed`. Outcomes also go into
  `JobOutcomes` so quit can apply any that never reached the UI.
- `WxInstallerUI` runs on the engine's worker: each prompt (announce target, wait for installer, locate folder)
  is posted to the UI thread and blocks on an Event. `release_all()` answers open prompts as cancelled on quit.
- `call_after(fn, *args)` and `run_in_background(work, done, name, ...)`.

## Catalog tab and catalog entries
- **Catalog tab** (`CatalogPanel`; its search, list, tree, button and description are a `GamesPanel`,
  `catalog_panel.view`): index entries as rows (`catalog_view.build_rows`), a filter tree (platform,
  genre (any of its genres), tags, price (each model plus "Can be played for free"), language, developer, year,
  support ("No longer supported"), installed), search (title, developer and a collection's `contains` titles),
  columns Title / Developer / Genre (the main one) / Price / Details (tags, no longer supported, installed), each
  one sortable (Price by `catalog_view.price_sort_key`: what can be played for free first, then by the amount
  shown, amounts compared as plain numbers whatever the currency; nothing recorded last), and an
  action button that reads Install / Add / Get / Launch per row (`catalog_view.action_kind`). Links open in the
  browser only when the player chooses them. The panel never fetches: `MainFrame.start_index_fetch` fetches on a
  worker and hands the state to `_take_index` through `run_when_no_dialog`. Retry and F5 fetch again.
- **Stores** (an entry's `stores`, read by `engine.stores.stores_of`). A row's places (`engine.entries.places`,
  `CatalogRow.places`) are the ways to get it on this system, in order: the app's own ("Install with Game
  Chooser" or "Add to Game Chooser"), then each store in entry order (Steam, then "Steam demo" when it has one,
  or the store's name). A row with any place but the app's alone reads Get (`CatalogPanel._get`): one place is
  gone to at once, several open `MainFrame.choose_place` (a list inside `modal_dialog()`; Cancel changes
  nothing). The app's place opens the install confirmation; a store opens its page in the browser; Steam is
  reached only through `MainFrame.steam_send(steam.GET_ACTION, app id)`. When Steam is absent (not installed,
  or not Windows: `messages.steam_absent`) the app asks whether to open the full game's Steam page instead, for
  the demo too; an address Windows refused is told and nothing more opens. A Get Steam took ("sent") makes its
  id pending (Installed Steam games, below). "Random Game to Install" picks by
  `catalog_view.app_action` (the app installs it), so a game that also has a store is still offered. It asks
  "Install now?", not the row's Get, and Yes opens the install confirmation. Each store's page is also a link
  ("<store> store page"), from `entry_links`, after the entry's own links and before the pricing pages.
- **Catalog schema v2** (index `schema_version` 2; a saved v1 index still loads, and its old shapes, `flags` and
  `links` as an object, are skipped). The description lists the entry's text, then Price (`engine/pricing.py`:
  player wording, "price as of", `pricing.note`), genres, tags, languages, `contains`, "No longer supported", then
  one `label: url` line per link (`catalog_view.entry_links`: `links` in order, a missing label shows the host
  name, then `pricing.url` and `pricing.donate_url`); Enter on such a line opens it. The Library description is the
  same text. Catalog games take the entry's main genre (`InstallService.refresh_catalog_genres` on every index
  take). The Library context menu's link items come from `catalog_view.link_items` (an untracked install gets only
  `homepage_item`: the link labelled Homepage, else the first link).

## Installing and the Library's catalog actions
- **Install confirmation** (`InstallConfirmDialog`; wording and decisions in `install_choices.py`): library
  choice (or a fixed one), optional extras, dependencies, and a blocked reason from `InstallService.blocked`.
  Returns an `InstallChoice(library_id, library_path, extras)`. Both tabs open it through
  `MainFrame.confirm_install(plan, fixed_library=None)`, and say things through `MainFrame._tell`.
- **Progress area** (`ProgressArea`): shows the queue's current job, never the selected game. One copy beside
  each tab's action button, all driven by `MainFrame._sync_progress`; the button stays usable. Done appears while
  an installer runs; Cancel cancels the job. Showing or hiding Done lays out the view around the area, so the
  area grows for Done as far as the window allows; only a window too narrow for the row narrows the status
  line. Focus: see CLAUDE.md (Focus).
- **Install while a job runs or waits** (`CatalogPanel._confirm_install`): on a game that itself has a job
  queued or running it says so (`messages.JOB_ALREADY_RUNNING` / `JOB_ALREADY_WAITING`) and changes nothing.
  That includes a running job that is being cancelled (`InstallQueue.job_for` still counts it: it may still be
  changing the game's files), for which the queue's `submit` would queue a new job; for any other, `submit`
  would return that same job anyway. Another game is planned, confirmed and queued as usual. An installed row
  reads Launch, which is the Library's launch and is not held back by a job.
- **Install that can't start**: when `InstallService.plan` or `submit` raises, the alert
  (`messages.install_not_started`) names the step (planning or queueing) and the error's type and text.
- **Library catalog actions** (`library_actions.py`): context menu for catalog games (Update, Undo, Extras,
  Repair, Uninstall, plus links). Delete on a tracked catalog install is **Uninstall** (`delete_kind`: "uninstall";
  "forget" for an untracked install, files untouched; else "delete"). An uninstall that stopped partway (record
  `uninstalled` with files left or a vendor uninstaller still to run: `is_uninstall_unfinished`) still offers
  Uninstall, and Delete opens it. Shared runtimes' records (kind "runtime") are never Library games: plan-2 code
  reads records through `RecordStore.game_records` / `load_game` / `delete_game`. Removing a library with installs is
  **Remove library**. **Edit is only for games added by hand** (`is_editable`: no `catalog_id`, no `library_id`).
  A Steam row's menu and Delete are Steam's (Installed Steam games, below).

## Launching
- Web games open the URL in the browser. Desktop games resolve the library-relative path through the library, run
  with the game's folder as working directory, and minimize the window after a successful launch.
- A missing executable branches on `library_actions.launch_problem`: `unreachable` (library folder gone),
  `repair` (tracked catalog install still in the catalog), `catalog` (say so, no file picker) or `locate`
  (hand-added game: pick the file).
- A catalog game whose launch file ends in `.html` or `.htm` opens in the default browser through
  `engine.launch.start_game` (no minimize). It is still an ordinary install, not a web game; the catalog must name
  the page (it is never guessed). The rule against widening `PAGE_SUFFIXES` is in CLAUDE.md (Launch pages).
- A catalog game whose launch file ends in `.hta` (an HTML Application, with no exe) is a program launch:
  `start_game` runs it in Windows' `mshta.exe` (`engine.launch.start_hta`, see `.claude/rules/engine.md`), the
  tracing observer is called, and the window minimizes, as for an exe. `MainFrame` starts every launch file
  through `library_actions.start_launch_file` (wx-free, tested), which passes `start_game` its `hta_allowed`:
  `may_start_hta(full_path, game, record, libraries)`, true only for a catalog game whose record says its launch
  path came from the catalog (it is never guessed) and only when the file being launched is exactly that record's
  launch file. The path in games.json is not trusted: `..`, an absolute path elsewhere or another `.hta` in the
  folder is refused. Windows only: on another system
  the platform check refuses first, and failing that `start_hta` raises, shown as "Failed to launch game: This game
  only runs on Windows." A game added by hand never runs an `.hta`: `library_actions.executable_problem` refuses one,
  so Add Game and the Locate dialog refuse it ("Invalid Executable"); should
  games.json name one anyway, the launch is refused ("Failed to launch game: " then `messages.HTA_NOT_ALLOWED`) and
  nothing is run.
- Platform compatibility is checked against the user's OS first; web games are compatible everywhere.
- A Steam row has no launch file: Steam starts it (Installed Steam games, below).

## Installed Steam games
A catalog game the player installed through Steam shows in the Library as a **Steam row** (`Game.is_steam`; the
data model and the address rule are in CLAUDE.md). The app never installs, updates or repairs its files, and never
writes in Steam's folders.

- **The scan** (`engine.steam_scan.scan`, on a worker; what it reads is in `.claude/rules/engine.md`) has three
  outcomes (`Scan.state`):
  - `found`: `apps` holds each installed app by id, and `unsure` the ids whose manifest could not be read.
    `complete` is false when a listed library could not be reached, or a library list entry could not be read
    (no usable absolute path).
  - `absent`: Steam is not installed, or this is not Windows.
  - `unknown`: could not tell. The library list could not be read, or holds no `libraryfolders` object. It
    changes nothing.

  A manifest whose folder name is not one plain name (empty, `.`, `..`, or holding `/`, `\`, `:` or a NUL) is not
  trusted: its id is `unsure`. No row is added for it, and a row it already has stays.
- **When it runs**: see `.claude/rules/ui-main-window.md` (Steam scan).
- **Its rules** (`steam_games.steam_changes(entries, scan, games, platform)`, wx-free; `tests/test_steam_games.py`)
  give the rows to add and the Steam rows to remove. Only ids in the index count, matched exactly.
  - No index, or a scan that could not tell: nothing changes.
  - A Steam row leaves when its entry is gone or is no longer a game (`kind` "game"), when its id is no longer
    the entry's Steam game or demo, when Steam is absent, when the app's own copy of that entry is in the Library,
    or when it is a second Steam row for one catalog id.
  - A row whose game is not seen installed leaves only when the scan is sure: the scan is `complete` and the id
    is not `unsure`.
  - A stale row is replaced in the same pass. A demo row gives way to the full game's once the full game is
    installed. A full game's row that is surely gone gives way to the demo's when the demo is installed.
  - On a `found` scan, an entry with no row gets one: its full game when installed, else its demo. The row takes
    the entry's fields, the current platform and that id.
- **The kept scan** (`InstallService.take_steam_scan`, on the UI thread, through `run_when_no_dialog`): a scan
  that could tell is kept (`steam_scan`) and matched (`match_steam`). `match_steam` saves games.json once when
  rows changed. It runs again on every index take (`take_index`) and job result (`apply_result`: an uninstall of
  the app's copy may leave the entry's Steam copy to show).
- **Pending ids** (`steam_pending`, set by `steam_sent`): an id the app sent Steam a get (the Catalog's Get) or an
  uninstall for, when `steam_send` answered "sent". A refused or absent send makes nothing pending. An id is
  settled only by a scan that could tell: one not installed when sent, once a scan sees it installed; one
  installed when sent, once a scan is sure it is gone. An `absent` scan settles them all. While any is pending,
  the window coming forward scans.
- **Status** is always `Ready` (`statuses`): Steam keeps its files. On the Catalog tab the entry counts as
  installed (`installed_ids`).
- **Menu**: "Uninstall through Steam" (`messages.MENU_UNINSTALL_STEAM`) and the entry's links
  (`catalog_menu_items`). No Update, Undo, Extras or Repair. Edit is refused, as for every catalog game.
- **Enter** (`launch_game`): handed to Steam (`steam_send(steam.LAUNCH_ACTION, id)`) before anything else. No
  platform check, no launch observer, and the window is not minimized.
- **Delete** (`delete_kind` "uninstall", then `uninstall_game`): Steam's uninstall (`steam_send("uninstall", id)`)
  before anything else. No file count and no dialog of the app's own: Steam asks. "sent" makes the id pending.
  It ends with `focus_active_list()`.
- **Steam absent** on Enter or Delete: the app says so (`messages.steam_absent`, inside `modal_dialog()`) and
  scans, so the row leaves. An address Windows refused has been told by `steam_send`; nothing more happens.
- **Open folder** (Ctrl+Enter): the folder the kept scan found (`steam_folder_of`), opened itself in Explorer.
  No folder, or one that is no longer a folder: "Game folder not found."
- **Description**: the app's own copy of a game whose Steam game or demo the scan found installed reads "Also
  installed through Steam." (`messages.ALSO_ON_STEAM`, from `also_on_steam`).
