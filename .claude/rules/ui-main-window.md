---
paths:
  - "/main_window.py"
  - "/dialogs.py"
  - "/install_dialogs.py"
  - "/catalog_panel.py"
  - "/games_panel.py"
  - "/progress_area.py"
  - "/description_field.py"
  - "/tests/test_games_panel.py"
---

# Main window and dialogs

## Files
- **main_window.py** - `MainFrame`: notebook, menus, shortcuts, queue handlers, modal safety, state
- **dialogs.py** - `FirstTimeSetupDialog`, `GameDialog` (add/edit by hand), `PreferencesDialog`, `DeleteGameDialog`
- **install_dialogs.py** - `InstallConfirmDialog`, `UninstallDialog`, `RemoveLibraryDialog`
- **description_field.py** - read-only multi-line description; Enter on a link line opens it
- **progress_area.py** - gauge, status line, Done and Cancel for the running job
- **games_panel.py** - `GamesPanel`: the games view both tabs are built from (Search, the games list, the filter
  tree, the action button with the install progress, the description)

## Main window (`MainFrame`)
A `wx.Notebook` with two pages (`TABS = ("library", "catalog")`; `active_tab()`). The Library page is a
`GamesPanel` (`MainFrame.library_view`); the Catalog page is `CatalogPanel`, which holds one (`catalog_panel.view`).
`MainFrame.views` maps each tab name to its view.

Menu bar: File, Edit, View, Launch. Library tab, top to bottom: Search; the game list with the filter tree to
its right; the Launch button with its progress area; the description. Catalog tab, top to bottom: the status line
and Retry; Search; the catalog list with the filter tree to its right; the action button (the progress area
takes its place while a job runs or waits); the description.

- **Library tab**: the player's games as rows (`catalog_view.library_rows`; `MainFrame.refresh_game_list` rebuilds
  them after every change to a game or to `manager.games`), columns Title / Genre / Developer / Year / Platform /
  **Status**, a description field (`DescriptionField`: catalog text for catalog games, empty for others), a
  filter tree (Platform/Genre/Developer/Year, multi-select OR; View > Filter Tree chooses which of the four show)
  and View > Libraries to hide libraries (their games leave the list; the tree still shows their values).
  `MainFrame.selected_game()` is the selected row's `Game`.
  Status values (`engine/status.py`): `Ready`, `Update available`, `Executable not found`, `Library not found`.
  A `self_updating` entry never shows `Update available`.
  Statuses are computed on a worker thread (`refresh_statuses`) and applied on the UI thread.
- **Catalog tab**, **Install confirmation**, **Progress area** and **Library catalog actions**: see
  `.claude/rules/catalog-install.md`.
- **Preferences** (`PreferencesDialog`): tabs Library Paths and Installing (`install_prefs.py`: catalog address,
  "register shared components"). An invalid address saves nothing; a changed address refetches the catalog on close.
- **FirstTimeSetupDialog**: shown when config.json didn't exist; the player can add a library or skip.
  `catalog_view.initial_tab(saved, is_first_run)` picks the starting tab.

## The games view (`GamesPanel`)
Both tabs' search, list, filter tree, selection memory, action button, progress area and description are this one
class. A tab gives it rows (`catalog_view.CatalogRow`; a Library row also holds its `Game`) through `set_rows`.
What a tab does differently is a setting of the class (the table below) or the tab's own code.

Rules it holds for both tabs (pinned by `tests/test_games_panel.py`):
- After every refill or sort the action label and the description are set directly, never left to a selection
  event: wx fires none when the selected row number stays the same and another game moves into it.
- Every programmatic select also sets the list's focused item (`ListCtrl.Focus`; not on the macOS DataView
  control), so the arrow keys start from the selected game.
- Every change to the games comes in through `set_rows`, which also syncs the tree: it is rebuilt only when what
  it shows changed, and the player's tree selections are kept.
- The selected game stays selected through every refill (a row is known by its id: the catalog id, else the
  launch path). When it is no longer listed: after `set_rows` (the games changed: Delete, an uninstall, a game
  that left the catalog) the selection keeps its row number, clamped to the list, and that row is remembered;
  after a filter the player changed (`apply_filters`: search, tree, View > Libraries, View > Filter Tree) it goes
  to the first row. `catalog_view.pick_index` picks the row.
- The remembered game (`last_selected`, what the next start selects) changes only when the player moves the
  selection, or when the selected game left the list.
- A filter is applied first and saved second; a config save that fails is logged (`GamesPanel.save_config`),
  never raised.
- `focus_list()` takes no focus while `MainFrame.initializing` or `dialog_active` is true.
- Search: filters on every keystroke (no delay), case-insensitive substring over title and developer (and a
  collection's `contains` titles); a search containing "unknown" matches nothing.
- Rows are in title order; a list with sort keys is then sorted by the chosen column, so rows that tie there
  stay in title order.

What differs between the tabs, each a setting passed to `GamesPanel`:

| Setting | Library | Catalog |
|---|---|---|
| `idle_label` (button text with nothing selected) | "Launch" | "Install" |
| `progress_replaces_action` | no: the progress area shows beside Launch | yes: it takes the button's place |
| `sort_keys` (header click, digit keys 1-6, header arrow, saved sort) | one per column | none: always by title |
| `categories` / `tree_keys` | 4 categories; View > Filter Tree picks which show | its 9 categories, all shown |
| `row_filter` | View > Libraries (hidden libraries) | none |
| `on_key` | Del, Ctrl+E | none |
| `on_shown` | `update_tab_menus` (Edit Game follows the selection) | none |
| `on_action`, `context_menu`, `columns` | launch; `build_list_menu`; the Status column | install, add or launch; the action and links |

Column widths are saved for both tabs (`column_widths`, `catalog_column_widths`). The splitter position is saved
for the Library only, by `MainFrame`: a notebook page that was never shown does not report a sash position that
can be trusted. The Catalog's status line and Retry are `CatalogPanel`'s own.

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
    (after the first-run dialog, just before the first `focus_active_list()`). `GamesPanel.focus_list()` (both
    tabs) and `CatalogPanel.set_index_state()` take no focus while it is true (don't steal focus from the
    first-run dialog)
  - **`dialog_active`**: set by `modal_dialog()`; blocks selection events and defers work during modal dialogs
- `GamesPanel.restoring_tree` is true while the view itself changes the tree (building it, restoring its
  selections, Escape): the tree's change events are then ignored.
- Filling a list, restoring its selection and the search rule are `GamesPanel`'s (above); filling never calls
  `SetFocus`. What never moves focus is in CLAUDE.md (Focus). The description is a read-only multi-line field so a
  screen reader reads it line by line.
