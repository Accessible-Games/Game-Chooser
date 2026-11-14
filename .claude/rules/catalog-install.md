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
  - "/tests/test_install_*.py"
  - "/tests/test_catalog_view.py"
  - "/tests/test_library_*.py"
  - "/tests/test_wx_bridge.py"
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

## Install service (`install_service.py`)
`InstallService(manager, listener, installer_ui, ...)` ties together the index (`IndexState`), `InstallEngine`,
`InstallQueue` (log `install.log`), `manager.records` and the libraries. Main members:
`installed_ids`, `plan`, `blocked`, `submit(action, entry_id, library_id, library_path, ...)`, `apply_result`,
`job_failed`, `forget`, `player_files`, `record_for`, `entry_for`, `statuses`, `ensure_library`, `library_games`,
`check_library_removable`, `remove_library`, `adopt_launch_paths`.

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
  action button that reads Install / Add / Launch per row (`catalog_view.action_kind`). Links open in the
  browser only when the player chooses them. The panel never fetches: `MainFrame.start_index_fetch` fetches on a
  worker and hands the state to `_take_index` through `run_when_no_dialog`. Retry and F5 fetch again.
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
- **Library catalog actions** (`library_actions.py`): context menu for catalog games (Update, Undo, Extras,
  Repair, Uninstall, plus links). Delete on a tracked catalog install is **Uninstall** (`delete_kind`: "uninstall";
  "forget" for an untracked install, files untouched; else "delete"). An uninstall that stopped partway (record
  `uninstalled` with files left or a vendor uninstaller still to run: `is_uninstall_unfinished`) still offers
  Uninstall, and Delete opens it. Shared runtimes' records (kind "runtime") are never Library games: plan-2 code
  reads records through `RecordStore.game_records` / `load_game` / `delete_game`. Removing a library with installs is
  **Remove library**. **Edit is only for games added by hand** (`is_editable`: no `catalog_id`, no `library_id`).

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
