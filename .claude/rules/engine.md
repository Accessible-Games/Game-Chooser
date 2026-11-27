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
  The Windows Defender advice (`messages.DEFENDER_PARAGRAPH`, on "no program found" and "executable not
  found") is added on Windows only.
- **index.py**, **entries.py** - catalog index fetch/cache (`CatalogIndex`, `IndexState`) and entry helpers.
  **entry_types.py** - the types of the entry fields the app reads, in two tables, checked once for every entry
  as the index is read (the download and the saved copy alike, `index._entries_by_id`), so the rest of the app
  reads an entry without asking what type a value is. `checked(entry)`: a wrong type in an install field
  (`INSTALL_FIELDS`: `kind`, `version`, `self_updating`, `sources` with `install` and `steps`, `launch`,
  `dependencies`, `extras`, `persist`, `detect`), or a member the code reads without asking that is missing (a
  source's `url`, a step's `path`), hides the entry; a wrong type in a display field (`DISPLAY_FIELDS`: `title`,
  `developer`, `genres`, `tags`, `languages`, `contains`, `year`, `description`, `unsupported`, `links`,
  `pricing`) only leaves that field out, so one slip in how the site writes a display field can't empty the
  Catalog tab. JSON `null` fits nothing. `wrong_fields(entry)` names what failed. A field the app starts to read
  goes into one of the two tables. The loader also leaves out an entry whose id fails `entries.is_id`, and an
  index whose `entries` is not a list, or a meta whose `sha256` or `index_url` is not text, is a bad download
  (the saved copy is kept), never an empty catalog.
  `platform_sources` skips sources whose `arch` this machine can't run (`runs_on`, `current_arch`: x86 runs on x64
  Windows and Linux; Windows on Arm runs x64 and x86; an Apple Silicon Mac runs x64; pass `machine` in tests).
  **pricing.py** - how `pricing` reads, the Price filter values and the install confirmation's price warning.
  **steam.py** - the only builder of a Steam address (`address`, from a plain positive int), `store_page`, and
  where Steam is installed. **stores.py** - `stores_of(entry)` reads an entry's `stores` into `Store`s (name,
  platforms, page address, Steam's app and demo ids); an item of the wrong shape is skipped.
  **keyvalues.py** - `parse` reads Steam's KeyValues text (keys lowercased, a value is a str or a dict; raises
  `ValueError` on anything malformed or nested deeper than 64). **steam_scan.py** - `scan` / `scan_folder` read
  Steam's `libraryfolders.vdf` and each library's `appmanifest_<id>.acf` (read only, never written). Installed
  means `StateFlags` has bit 4 and `steamapps/common/<installdir>` is a folder (`installdir` must be one plain
  name: no `/`, `\`, `:` or NUL, not empty, `.` or `..`, on every system; the folder must sit directly in `common`). Three outcomes: `absent` (no Steam), `unknown`
  (the library list can't be read, parsed, or has no `libraryfolders` object), `found`. `unsure` holds ids whose manifest can't be read or trusted;
  `complete` is false when a listed library's `steamapps` is unreachable, or a numbered entry has no usable absolute path. Only `steamapps` itself is read.
  A manifest whose folder is there but whose bit 4 is clear is a game Steam is updating or downloading: its id is
  `unsure`. A value of the wrong type (an object for `appid`, `StateFlags` or `installdir`) makes that manifest
  `unsure` too. `unread` holds the ids that the libraries not read say they hold (the keys of their entry's `apps`
  object). It is `None` when one of them does not say: an old-form entry, no `apps` object, or a key that is not
  a whole number. An empty `apps` object says the library holds nothing. `Scan.gone(id)` is the one test of "this
  scan can tell the game is not installed": true for `absent`, false for `unknown`; for `found`, the id is not in
  `apps` or `unsure`, and the scan is `complete` or `unread` says no unread library holds it.
- **safety.py** - the three-checksum planner and apply/uninstall rules; **records.py** - per-user install records
  in `%APPDATA%\GameChooser\installs`; **sources.py** / **http.py** - verified downloads (agarchive.net blocked).
  An HTTP 404 or 410 is `DownloadGone` ("no longer available"), never "couldn't be reached"; `NoVerifiedSource.gone`
  only when no address failed in a way that may pass. Undo that fails on a changed or gone file, or on a previous
  version without its unpack details, sets the record's `previous_unavailable` (Undo shown unavailable, with why;
  the dialog names the backup folder holding the player's copies); one that couldn't reach an address stays offered.
  Uninstall (`apply_uninstall`) removes every empty folder in the game's folder, box ticked or not (one the archive
  shipped empty, like `saves`, has no recorded file under it); a folder holding a player's file stays, with its
  parents; links are never entered. On a Mac a link the download shipped is recorded and removed like a file (the
  link itself, never what it points to) while its target is unchanged; a changed one is the player's. A game
  folder left with nothing in it goes, and so does its record.
  Before that, `jobs._uninstall` guards the game folder itself. A game folder that is, or holds, a library folder
  (`_holds_a_library`, compared as text and on disk with `paths.same_on_disk`, so another spelling or a link to the
  library counts, but never on a drive that gives every file the ID 0; a library can't be added there, so only a
  damaged record or config reaches it) has only its record
  deleted, nothing on disk touched (`GAME_FOLDER_IS_LIBRARY_FORGOTTEN`); Update, Repair, Undo and Extras refuse it
  (`_installed`, `GAME_FOLDER_IS_LIBRARY`). A game folder that is itself a link (a game moved to another drive) has
  its system steps undone and cleared from the record, then only the link and the record go: nothing behind the
  link is touched (`GAME_FOLDER_LINK_REMOVED` says where the files are, `GAME_FOLDER_LINK_BROKEN` that the target
  is gone). While a vendor uninstaller is still there (`uninstall_keys.is_missing_program`), its Windows entry runs
  through the link, so it refuses and changes nothing (`GAME_FOLDER_LINK_VENDOR`). Update, Repair and Undo still
  work through such a link, changing only the game's own files.
- **extract.py** - unpacks with the bundled tools (tar.gz/xz/bz2 with Python's `tarfile`, "data" filter, links
  refused; on a Mac other archives keep links that stay inside, see below); **run_installer.py** /
  **run_actions.py** / **installer_watch.py** - installers that run. When the installer did not write to the
  target, the game's folder is that of the new Windows uninstall entry `run_installer._best_entry` picks, among
  the entries whose folders hold files from the install window: the one whose folder holds the catalog's launch
  file, else the one whose folder holds a new `.exe`. Entries of one rank that name more than one folder are not
  told apart (an installer may chain a redistributable that registers itself): the player is asked for the folder
  (`InstallerUI.locate_folder`). An entry's DisplayName is never read, and the saved uninstall command is only
  ever that of the entry naming the game's folder (`_matching_uninstaller`), because on the site a game's title
  is an open field.
- **runtimes.py** - shared runtimes under `%ProgramData%\GameChooser\runtimes\<id>` (sealed by an elevated chain
  when the runtime has an elevated step, marker `.gamechooser-runtime.json`), or private COM copies
  (`comfree.py`). By default every runtime the game depends on that has a folder (not an installer that runs)
  also gets its `.dll`/`.ocx` files copied beside the game's launch exe (layout kept; the game's own file of that
  name wins; hash-checked from a trusted folder or the private staging copy; recorded with owner `runtime:<id>`,
  ids in the record's `runtime_copies`), so a game that loads a runtime DLL by name finds it; **steps.py**,
  **registry.py**, **elevation.py** - catalog steps and the one program that runs elevated.
  A copy or move step whose source and target are one path, or one inside the other, is refused before
  anything changes (`steps._overlaps`), case ignored on every system: on a Mac's usual disk, as on Windows,
  "A" and "a" are one file.
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
  else its id), `entries.is_id` (the one test of an id the app takes: the catalog's pattern, at most
  `MAX_ID_LENGTH` characters, and not a name Windows keeps for a device, `paths.is_device_name`; an id names the
  install folder, the record file and the work folders), `runs_installer`, `is_web_game`, `dependency_order` (also used for the catalog's visibility, which
  further requires each runtime to be usable here); `paths.same_folder` and `folder_key` (as text),
  `same_on_disk`, `is_link`, `is_code`, `walk_files` (skips only linked folders and those `skip` names: backups are
  the player's), `write_atomic` (temp file, then replace; the temp file goes if that fails); `pricing.http_url` (an
  http or https address with a host and no spaces); `steam.address` (the only builder of a Steam address, from a
  plain positive int); `stores.stores_of` (the only reader of an entry's `stores`); `safety.plain_file` ("is this still the recorded file", read
  errors raised) and `remove_unchanged`; `runtimes.runtime_route` (shared, private or skipped); `errors.check_cancel`.
- **queue.py**, **status.py**, **plan.py**, **folders.py** - the job queue, library status, the install
  confirmation plan (the price warning on installs, `first_run_download_size` added to the size), and
  target-folder naming: `folders.target_folder` is `<library>/<id>`, always. On the site the title and the
  developer are open fields, so neither names anything on the player's disk; a folder already there that the
  game's own record does not own is refused (`jobs.blocked_reason`). A game installed before this keeps the
  folder its record holds. For a game that is no longer in the catalog the engine has no title: it shows
  `InstallRequest.title` (the Library's title, set by `InstallService.submit`), else the id.

## Bundled tools requirement

`engine/extract.py` runs `7z.exe` and `innoextract.exe` from the `bin/` folder beside the executable; `7z.dll`
sits beside them. `scripts/fetch_tools.py` downloads them (SHA256-pinned) into `bin/` with their licenses in
`bin/licenses/`. Without `bin/`, every zip, 7z, cab, Inno and NSIS install fails.

On macOS (`Extractor(platform=...)`, default `sys.platform`): a zip goes through macOS' own `/usr/bin/ditto -x -k`,
which keeps a Mac app's links and its programs' "can run" bit (7-Zip drops links that go through other links, and
`zipfile` drops the bit); 7z, cab, NSIS and other 7-Zip installers go through `bin/7zz` (7-Zip's Mac build, arm64
only); Inno and MSI raise `EXTRACT_NEEDS_WINDOWS` before anything runs. `fetch_tools.py` on a Mac fetches only
`7zz` and its license.
After unpacking on a Mac, `mark_mac_programs` gives each `.app`'s own program (Info.plist's `CFBundleExecutable`, a
name in `Contents/MacOS` or NVGT's `MacOS/<name>`) the run bit for whoever may read it, since a zip or 7z made on
Windows keeps no Unix modes. It skips an app whose plist can't be read, a name that leaves `Contents/MacOS`, and
anything through a link. Nothing else gets the bit.
Mac apps ship links (a framework's `Versions/Current -> B`), so on a Mac (`paths.KEEPS_LINKS`) `_refuse_links_leaving`
keeps a link whose target is relative and names something that exists inside the folder; an absolute, leaving or
broken link is still `EXTRACT_UNSAFE_LINK`. Such a link is a game file: `hash_tree` records it with `link:` plus its
target in place of a checksum (`walk_entries`, never entering it), and Update, Repair and Undo write, keep or
delete it like a file (`safety._current_state`, `_write_file`), never writing through a link (`UPDATE_PATH_CONFLICT`).
Elsewhere every link is refused and nothing of this applies.
A disk image (`method: "dmg"`, or a `.dmg` inside a zip, 7z or tar that the source's `install.dmg` names, relative
to the archive root) is opened read-only with `/usr/bin/hdiutil attach -nobrowse -readonly -noautoopen` on a
temporary mount folder, stdin closed so an image that asks to accept a license fails (`EXTRACT_DISK_IMAGE_FAILED`).
Its top-level items are copied with `/usr/bin/ditto`, leaving out hidden ones (`.DS_Store`, `.fseventsd`,
`.background`) and links (the usual shortcut to /Applications). A nested image is copied into the folder that holds
it, then deleted, before `extract_dir` is applied; one that isn't there is `EXTRACT_DISK_IMAGE_MISSING`. The image is
always detached, after a cancel or a failure too. Elsewhere both raise `EXTRACT_NEEDS_MAC` before anything runs.

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

- **Mac app (`.app` folder)**: `start_program` starts it with `/usr/bin/open` (`OPEN_COMMAND`) and returns no
  process ID (the process is `open`'s). A catalog launch path that ends in `.app` is taken when it is a folder
  inside the game, not through a link (`safety.plain_app`); every other launch path must be a plain file
  (`plain_file`).

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
