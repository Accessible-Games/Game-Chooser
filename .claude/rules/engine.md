---
paths:
  - "engine/**/*.py"
  - "tests/engine/**/*.py"
  - "/exception_manager.py"
---

# Install Engine (`engine/`)

A wx-free package that installs, updates, repairs, undoes and uninstalls games from the catalog, with tests in
`tests/engine/`. Its rules on importing wx, running elevated and player-owned files are in CLAUDE.md (Testing
Strategy, and Install engine under Rules that hold everywhere).

Installs are Windows-focused (registry, UAC, `bin/*.exe`).

## Modules

- **jobs.py** - `InstallEngine.run(InstallRequest)` routes install/update/repair/undo/extras/uninstall; raises
  `InstallFailed` (plain words from `messages.py`) or `Cancelled` for what the player can act on, and `ValueError`
  for an unknown action or a library that is not configured, which the queue reports as an unexpected error.
- **index.py**, **entries.py** - catalog index fetch/cache (`CatalogIndex`, `IndexState`) and entry helpers.
  `platform_sources` skips sources whose `arch` this machine can't run (`runs_on`, `current_arch`: x86 runs on x64
  Windows and Linux; Windows on Arm runs x64 and x86; an Apple Silicon Mac runs x64; pass `machine` in tests).
  **pricing.py** - how `pricing` reads, the Price filter values and the install confirmation's price warning.
- **safety.py** - the three-checksum planner and apply/uninstall rules; **records.py** - per-user install records
  in `%APPDATA%\GameChooser\installs`; **sources.py** / **http.py** - verified downloads (agarchive.net blocked).
  Uninstall (`apply_uninstall`) removes every empty folder in the game's folder, box ticked or not (one the archive
  shipped empty, like `saves`, has no recorded file under it); a folder holding a player's file stays, with its
  parents; links are never entered. A game folder left with nothing in it goes, and so does its record.
- **extract.py** - unpacks with the bundled tools (tar.gz/xz/bz2 with Python's `tarfile`, "data" filter, links
  refused); **run_installer.py** / **run_actions.py** / **installer_watch.py** - installers that run.
- **runtimes.py** - shared runtimes under `%ProgramData%\GameChooser\runtimes\<id>` (sealed by an elevated chain
  when the runtime has an elevated step, marker `.gamechooser-runtime.json`), or private COM copies
  (`comfree.py`). By default every runtime the game depends on that has a folder (not an installer that runs)
  also gets its `.dll`/`.ocx` files copied beside the game's launch exe (layout kept; the game's own file of that
  name wins; hash-checked from a trusted folder or the private staging copy; recorded with owner `runtime:<id>`,
  ids in the record's `runtime_copies`), so a game that loads a runtime DLL by name finds it; **steps.py**,
  **registry.py**, **elevation.py** - catalog steps and the one program that runs elevated.
- **Extras**: an extra's `extends` is a list of one or more game ids (there is no `install_into`), and the index
  lists it in the `extras` ({id, required}) of each of those games, so one extra id can be in several games'
  `extras`. The app never reads `extends`: `entries.extras_of` and the install plan go by the game's own `extras`.
  An extra installs into the folder of whichever game it is added to (a required one with each game), and each
  game's install record holds its own copy (`InstallRecord.extras`, its files owned by the extra's id); an extra
  never has a record of its own, so updating, repairing, undoing or uninstalling one game leaves another game's
  copy alone (`tests/engine/test_shared_extras.py`).
- **Rules written once in `engine/`** (use these, in `engine/` and outside it; never write another copy. Left on
  purpose: `library_actions._same_file` follows links for the `.hta` check, and a Game's "title, else catalog id"
  in `dialogs.py` and `install_service.py` is a game's, not an entry's): `entries.title_of` (an entry's title,
  else its id), `runs_installer`, `is_web_game`, `dependency_order` (also used for the catalog's visibility, which
  further requires each runtime to be usable here); `paths.same_folder` and `folder_key`, `is_link`, `is_code`,
  `walk_files` (skips only linked folders and those `skip` names: backups are the player's), `write_atomic` (temp
  file, then replace; the temp file goes if that fails); `pricing.http_url` (an http or https address with a host
  and no spaces); `safety.plain_file` ("is this still the recorded file", read errors raised) and
  `remove_unchanged`; `runtimes.runtime_route` (shared, private or skipped); `errors.check_cancel`.
- **queue.py**, **status.py**, **plan.py**, **folders.py** - the job queue, library status, the install
  confirmation plan (the price warning on installs, `first_run_download_size` added to the size), and
  target-folder naming (`<library>/<Title>`).

## Bundled tools requirement

`engine/extract.py` runs `7z.exe` and `innoextract.exe` from the `bin/` folder beside the executable; `7z.dll`
sits beside them. `scripts/fetch_tools.py` downloads them (SHA256-pinned) into `bin/` with their licenses in
`bin/licenses/`. Without `bin/`, every zip, 7z, cab, Inno and NSIS install fails.

## Temporary launch fallback

`engine.launch.select_best_executable` (prefers `game`/`launch`/`play`, then an exe named like the folder, then
the first) plus `ExceptionManager.should_auto_exclude` (skips setup/uninstall-style exes) guess a launch exe when
the catalog records no launch path, or the path it records is not a real file inside the game. It is temporary
until the catalog records launch paths; `adopt_catalog_launch` switches a guessed path to the catalog's once one
exists. **Nothing new may call it.** The guess only picks `.exe` and `.bat` files: never a page, never an `.hta`.

## How a launch file starts (`engine/launch.py`)

`launch_kind` / `start_game` decide: `.html`/`.htm` open in the browser (kind "page"); an `.hta` runs through
`start_hta` when the caller passes `hta_allowed`; anything else runs through `start_program`. The last two are
kind "program" and return the started process's ID.

- **`.hta` (HTML Application)**: a catalog game with no exe, started only when the catalog's launch path names the
  file (`_recorded_launch_exe` accepts any real file inside the game, so the install and Status need nothing
  special). `start_game` gives it the host only with `hta_allowed=True`, which
  `library_actions.start_launch_file` (the one caller, used by `MainFrame`) sets from
  `library_actions.may_start_hta(full_path, game, record, libraries)`: a catalog game whose record has
  `launch_from_catalog`, and the file being launched is exactly `record_folder(record) / record.launch_path`
  (resolved; case ignored on Windows), so a games.json path pointing anywhere else is refused. Without it
  nothing starts an `.hta`: `start_game` raises `OSError` (`messages.HTA_NOT_ALLOWED`) and never calls
  `start_program`, whose administrator-prompt route (`_shell_open`) would be the `.hta` file association.
  `start_hta` runs `mshta.exe` with the file's full path as its one argument and the game's folder as
  working directory. `hta_host` builds the host's path from the folders Windows reports (`GetSystemWow64DirectoryW`,
  `GetSystemDirectoryW`): never from PATH, the environment or the game's folder. Windows only: elsewhere, or when
  Windows has no `mshta.exe`, it raises `OSError` with words from `messages.py` (`HTA_NEEDS_WINDOWS`,
  `HTA_HOST_MISSING`).
- **`HTA_HOST_BITS`** (32) is the one place that chooses the 32-bit host (`SysWOW64\mshta.exe` on 64-bit Windows;
  the system folder's on 32-bit Windows) or the 64-bit one; the evidence is in the comment above it. With the
  32-bit host, a game that reads a file by the path `C:\Windows\system32\...` is given `C:\Windows\SysWOW64\...`.
- The rule that an `.hta` is never a page is in CLAUDE.md (Launch pages).

- **exception_manager.py** - filename filter used only by the temporary launch fallback
