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
  - "/tests/test_catalog_panel.py"
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

Menu bar: File, Edit, View, Launch. Both tabs, top to bottom: Search; the games list with the filter tree to its
right; the action button (Launch on the Library) with the progress area beside it while a job runs or waits; the
description. The Catalog tab has its status line and Retry above these.

- **Library tab**: the player's games as rows (`catalog_view.library_rows`), columns Title / Genre / Developer /
  Year / Platform / **Status**, a description field (`DescriptionField`: catalog text for catalog games, empty for
  others), a filter tree (Platform/Genre/Developer/Year, multi-select OR; View > Filter Tree chooses which of them
  show) and View > Libraries to hide libraries (their games leave the list; the tree still shows their values).
  `MainFrame.selected_game()` is the selected row's `Game`.
  `MainFrame.games_changed()` is the one call after every change to a game, to `manager.games`, to a library or
  to what is installed (Add, Edit, Delete, Locate, a job that ended, an index take, Preferences, F5, a web game
  added from the Catalog): it rebuilds both tabs' rows, the Libraries menu and the statuses.
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
Both tabs' search, list (with its sorting and type-ahead), filter tree, selection memory, action button,
progress area, splitter, random-game prompt and description are this one class. A tab gives it rows
(`catalog_view.CatalogRow`; a Library row also holds its `Game`) through `set_rows`. What a tab does differently
is a setting of the class (the table below) or the tab's own code.

Rules it holds for both tabs (pinned by `tests/test_games_panel.py`; the wx-free parts, rows, filters,
`pick_index` and the type-ahead matching, by `tests/test_catalog_view.py`):
- After every refill or sort the action label and the description are set directly, never left to a selection
  event: wx fires none when the selected row number stays the same and another game moves into it.
- Every programmatic select also sets the list's focused item (not on the macOS DataView control), so the arrow
  keys start from the selected game.
- A list is refilled in three ways only: `set_rows` (the games changed), `refill()` (what the rows show changed:
  the Status column) and `apply_filters()` (the player changed the search, the tree, View > Libraries, View >
  Filter Tree or the sort order).
- The selected game stays selected through every refill; `catalog_view.pick_index` finds its row. A Library row
  is found by its `Game` object, so a rename or a new launch path (Ctrl+E, Locate) keeps it selected. A game
  added by hand is never another object: when no row holds it, it is gone. A catalog game's object is replaced by
  a finished job or an index take, and its catalog id finds it then. A Catalog row is found by the row id, and
  so is the remembered row whenever no row was selected (the first fill, or a list that was empty).
- A row's id is the catalog id, else the launch path. `library_rows` numbers a launch path that repeats, in the
  games' order, so two rows never share an id; but such an id passes to the other game when the first is deleted
  or gets another path, which is why the object is looked for first.
- `set_rows` and `refill` are background refreshes. They never scroll the list away from where the player
  scrolled it: the selected row is brought into view only when it was in view before. A selected game that is no
  longer listed (Delete, an uninstall, a game that left the catalog) leaves the selection on its row number,
  clamped to the list.
- After `apply_filters` the selected row is brought into view, and a selected game the filter leaves out gives
  way to the first row.
- `set_rows` also syncs the tree. It is rebuilt only when what it shows changed, and a rebuild keeps the
  player's tree selections, the open categories and the tree's focused item: the root, a category or a value
  (when the value is gone, its category).
- The remembered game (`last_selected`, a row id: what the next start selects) changes only when the player
  moves the selection, or when a background refresh leaves the selected row with another id: the game got a new
  launch path, or it left the list and another game is in its row.
- A filter is applied first and saved second; a config save that fails is logged (`GamesPanel.save_config`),
  never raised.
- `focus_list()` takes no focus while `MainFrame.initializing` or `dialog_active` is true. The view moves focus
  on its own in one case only, and then to its list: the control that has focus is about to become unusable (the
  action button as the list empties; the progress area as it hides).
- Search: filters on every keystroke (no delay), case-insensitive substring over title and developer (and a
  collection's `contains` titles); a search containing "unknown" matches nothing.
- Sorting: a header click or the column's digit key sorts by that column, the same again the other way round;
  an arrow in the header shows it and the sort is saved. Rows are put in title order first, so rows that tie in
  the chosen column stay in title order. A digit sorts only while no text is being typed (next rule).
- Type-ahead (`on_char`; the virtual list does none by itself): characters typed in the list within a second of
  each other build one text (`catalog_view.TypeAhead`), and the selection jumps to the first row at or after the
  selected one whose title starts with it, wrapping round (`catalog_view.type_ahead_index`). One letter, typed
  once or repeated, steps on to the next title that starts with it. Only the title counts, whatever the sort
  column, in any case (casefolded); the character is read with `GetUnicodeKey`. No match changes nothing.
  - The jump is the player's own move: `_select` makes the row the selected and focused item and scrolls to it,
    and the selection event does the rest, as after an arrow key (label, description, the remembered game).
  - While a text is being typed, Space and the digits extend it ("temple of", "area 51"); otherwise Space is the
    action and a digit sorts. So a title that starts with a digit can't be reached by typing. Enter always acts.
  - A key with Ctrl or Alt is never text (a letter typed with AltGr is: wx reports it with neither).
  - Space mid-text is taken in the key-down handler (`on_list_key`), never skipped on to `on_char`: wx turns a
    Space key-down that reaches the list control into an activation.
  - Not on the macOS DataView control: there every key but a sorting digit is left to the control.
- `restore_sash()` puts the splitter where it was saved, else in the middle; `MainFrame.load_saved_state` calls
  it for both views once the window has its size. A sash left to wx stays at the minimum pane size and does not
  follow a resize.
- `random_game(candidates, none_left, act)` is Ctrl+R and Ctrl+Shift+R on both tabs: with no candidate it says
  so; else it picks one, asks (`messages.CATALOG_RANDOM_PROMPT`, with the row's own verb) and on Yes selects the
  row and runs `act(row)`, all inside `modal_dialog()`.

What differs between the tabs, each a setting passed to `GamesPanel`:

| Setting | Library | Catalog |
|---|---|---|
| `idle_label` (button text with nothing selected) | "Launch" | "Install" |
| `columns` with one of `sort_keys` each | Title, Genre, Developer, Year, Platform (by how many), Status | Title, Developer, Genre, Price (`catalog_view.price_sort_key`: free first, then by the amount shown), Details |
| `categories` / `tree_keys` | Platform, Genre, Developer, Release Year; View > Filter Tree picks which show | `catalog_panel.TREE_CATEGORIES`, all shown |
| `row_filter` | View > Libraries (hidden libraries) | none |
| `on_key` | Del, Ctrl+E | none |
| `on_shown` | `update_tab_menus` (Edit Game follows the selection) | none |
| `on_action`, `context_menu` | launch; `build_list_menu` | install, add or launch; the action and links |
| `random_game` candidates, and what Yes does | the listed games that run on this system; launch | any listed row, its own action (Ctrl+R); the rows that install, the install confirmation (Ctrl+Shift+R) |

Each view saves under its own prefix (none for the Library, `catalog_` for the Catalog): the selected row, the
search, the tree selections, the sort, the column widths and the splitter position. The Catalog's status line and Retry are
`CatalogPanel`'s own.

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
- **Ctrl+R**: random game from the active tab's filtered list (`GamesPanel.random_game`: it asks with the row's
  own verb, and on Yes selects the row and acts). Library: among the games that run on this system (web games
  included); Yes launches. Catalog: any listed row; Yes runs that row's action (Install, Add or Launch).
- **Ctrl+Shift+R** (Launch > Random Game to Install): random game from the Catalog tab's filtered list that
  installs (`catalog_view.install_candidates`: not installed, not a web game, no job queued or running); switches to
  the Catalog tab first, asks, then opens the install confirmation as the Install button does.
- **Del**: Library only (Uninstall for a tracked catalog install, forget for an untracked one, else delete).
- **Ctrl+E**: Library only; refuses catalog games and says why (its accelerator stays enabled for that, while the
  Edit menu item is disabled). Edit and Delete menu items are disabled on the Catalog tab.
- **Ctrl+N** add game, **Ctrl+F** the active tab's search box, **Ctrl+Enter** open folder, **Ctrl+,** preferences.
- In a games list (both tabs; the games-view rules above): **Enter** and **Space** run the row's action (Launch on
  the Library; Install, Add or Launch on the Catalog), Space only while no text is being typed; **typing** jumps
  to the title that starts with what was typed; a **digit** sorts by that column while no text is being typed.

## Accessibility and focus
- Screen reader announcement needs this sequence: select the row, make it the list's focused item, then
  `SetFocus()` (`GamesPanel.focus_list`, reached through `MainFrame.focus_active_list`).
- Flags on `MainFrame`:
  - **`initializing`**: `MainFrame` sets it true when it is created and false at the end of `check_libraries()`
    (after the first-run dialog, just before the first `focus_active_list()`). `GamesPanel.focus_list()` (both
    tabs) and `CatalogPanel.set_index_state()` take no focus while it is true (don't steal focus from the
    first-run dialog)
  - **`dialog_active`**: set by `modal_dialog()`; blocks selection events and defers work during modal dialogs
- `GamesPanel.restoring_tree` is true while the view itself changes the tree (building it, restoring its
  selections, Escape): the tree's change events are then ignored.
- Filling a list, restoring its selection and the search rule are `GamesPanel`'s (above). A fill calls `SetFocus`
  in one case only: the list became empty while focus was on its action button. What never moves focus, and what
  `CatalogPanel`'s status line does today, is in CLAUDE.md (Focus).
- After a dialog the code calls `focus_active_list()` only in these places. Startup: at the end of
  `check_libraries`, which follows the first-run dialog when there is one. Library: after the message that a
  catalog game can't be edited (Ctrl+E); in the forget flow when nothing was forgotten; after a catalog action
  from the context menu (Update, Extras, Undo, Repair, Uninstall); when Uninstall is refused, and after its
  dialog; after the damaged-file message at startup. Catalog: at every exit of Install (already queued, could not
  be planned, the confirmation confirmed or cancelled) and of adding a web game. Add, Edit, Delete of a game
  added by hand, a forget that succeeded, Locate, the random-game prompt and the Catalog's "not in the Library"
  message make no focus call. Inside `random_game` the action runs within `modal_dialog()`, so a
  `focus_active_list()` call it makes is refused.
- The description is a read-only multi-line field so a screen reader reads it line by line.
