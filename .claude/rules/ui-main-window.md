---
paths:
  - "/main_window.py"
  - "/dialogs.py"
  - "/install_dialogs.py"
  - "/catalog_panel.py"
  - "/game_list.py"
  - "/progress_area.py"
  - "/description_field.py"
---

# Main window and dialogs

## Files
- **main_window.py** - `MainFrame`: notebook, menus, shortcuts, queue handlers, modal safety, state
- **dialogs.py** - `FirstTimeSetupDialog`, `GameDialog` (add/edit by hand), `PreferencesDialog`, `DeleteGameDialog`
- **install_dialogs.py** - `InstallConfirmDialog`, `UninstallDialog`, `RemoveLibraryDialog`
- **description_field.py** - read-only multi-line description; Enter on a link line opens it
- **progress_area.py** - gauge, status line, Done and Cancel for the running job
- **game_list.py** - `GameListCtrl` (smart_list wrapper; has a Status column)

## Main window (`MainFrame`)
A `wx.Notebook` with two pages (`TABS = ("library", "catalog")`; `active_tab()`).

Menu bar: File, Edit, View, Launch. Library tab, top to bottom: Search; the game list with the filter tree to
its right; the Launch button with its progress area; the description. Catalog tab, top to bottom: the status line
and Retry; Search; the catalog list with the filter tree to its right; the action button (the progress area
takes its place while a job runs or waits); the description.

- **Library tab**: the player's games (`GameListCtrl`: sorting, keyboard nav, persisted column widths) with a
  **Status** column, a description field (`DescriptionField`: catalog text for catalog games, empty for others), a
  filter tree (Platform/Genre/Developer/Year, multi-select OR) and View > Libraries to hide libraries.
  Status values (`engine/status.py`): `Ready`, `Update available`, `Executable not found`, `Library not found`.
  A `self_updating` entry never shows `Update available`.
  Statuses are computed on a worker thread (`refresh_statuses`) and applied on the UI thread.
- **Catalog tab**, **Install confirmation**, **Progress area** and **Library catalog actions**: see
  `.claude/rules/catalog-install.md`.
- **Preferences** (`PreferencesDialog`): tabs Library Paths and Installing (`install_prefs.py`: catalog address,
  "register shared components"). An invalid address saves nothing; a changed address refetches the catalog on close.
- **FirstTimeSetupDialog**: shown when config.json didn't exist; the player can add a library or skip.
  `catalog_view.initial_tab(saved, is_first_run)` picks the starting tab.

## Modal safety
The rule itself, `MainFrame.modal_dialog()` and `MainFrame.run_when_no_dialog(fn, *args)` are in CLAUDE.md
(Modal safety).
- `run_when_no_dialog` is used for index takes, job outcomes (`_apply_finished`, `_apply_failed`,
  `_apply_cancelled`) and the uninstall confirmation.
- `PreferencesDialog` sets `holds_no_games = True`: it holds library ids only, is **not** opened in
  `modal_dialog()`, and `_modal_open()` ignores it, so job results apply while it is open (through
  `job_done_hooks`). The prompts it opens use the parent's `modal_dialog()` and do defer.
- `_live_game(game)` re-resolves a listed game to the library's current object (an index take can replace it).
- **Quit** (`on_close`): ask if a job runs; `installer_ui.release_all()`; `queue.cancel_all()`; `save_state()`;
  `queue.shutdown(QUIT_WAIT_SECONDS)`; `_apply_outcomes_on_quit()` (job outcomes are applied **before** games.json
  and config are saved); `Destroy()`; and `on_close` **ends with `ExitMainLoop()`** so a hidden window or balloon
  can't keep the app alive.

## Keyboard Shortcuts
- **F5**: Library: rebuild list, tree and menu and recheck Status. Catalog: check for catalog updates.
- **Ctrl+R**: random game from the active tab's filtered list. Library: picks among the games that run on this
  system (web games included), asks "Launch now?" and launches on Yes. Catalog: asks with the row's own verb
  (Install, Add or Launch) and on Yes selects the row and runs that action.
- **Ctrl+Shift+R** (Launch > Random Game to Install): random game from the Catalog tab's filtered list that
  installs (`catalog_view.install_candidates`: not installed, not a web game, no job queued or running); switches to
  the Catalog tab first, asks, then opens the install confirmation as the Install button does.
- **Del**: Library only (Uninstall for a tracked catalog install, forget for an untracked one, else delete).
- **Ctrl+E**: Library only; refuses catalog games and says why (its accelerator stays enabled for that, while the
  Edit menu item is disabled). Edit and Delete menu items are disabled on the Catalog tab.
- **Ctrl+N** add game, **Ctrl+F** search, **Ctrl+Enter** open folder, **Ctrl+,** preferences, Enter/Space launch.

## Accessibility and focus
- Screen reader announcement needs the sequence `Select()`, `Focus()`, `SetFocus()` (`focus_active_list`).
- Flags on `MainFrame`:
  - **`initializing`**: `MainFrame` sets it true when it is created and false at the end of `check_libraries()`
    (after the first-run dialog, just before the first `focus_active_list()`). Only `CatalogPanel` reads it: its
    `focus_list()` and `set_index_state()` take no focus while it is true (don't steal focus from the first-run
    dialog)
  - **`dialog_active`**: set by `modal_dialog()`; blocks selection events and defers work during modal dialogs
  - **`restoring_tree`**: blocks `save_tree_selections()` while the tree is restored, to prevent loops
- `on_filter_complete()` fills the list, restores the selection by title and manages focus. What never moves focus
  is in CLAUDE.md (Focus). The description is a read-only multi-line field so a screen reader reads
  it line by line.
- Library search: filters on every keystroke (no delay), case-insensitive substring over title and developer; a
  search containing "unknown" matches nothing.
