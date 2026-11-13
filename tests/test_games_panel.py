"""The games view both tabs are built from (GamesPanel), on a hidden frame: no window is shown and nothing is
fetched. Skips when wxPython is not installed or there is no display."""

import logging

import pytest

wx = pytest.importorskip("wx")
if not wx.App.IsDisplayAvailable():
    pytest.skip("no display", allow_module_level=True)

from smart_list import Column  # noqa: E402

from catalog_view import build_rows, library_rows  # noqa: E402
from games_panel import GamesPanel  # noqa: E402
from main_window import MainFrame  # noqa: E402
from models import Game  # noqa: E402

SRC = [{"platform": "windows", "install": {"method": "zip"}, "url": "https://x/e.zip"}]


def entry(id, genre="Puzzle", year=2000):
    return {"id": id, "kind": "game", "title": id.title(), "genres": [genre], "developer": ["Dev"], "year": year,
            "description": f"About {id.title()}.", "sources": SRC}


def rows(*entries):
    return build_rows(list(entries), set())


ALPHA, BRAVO, CHARLIE, DELTA = entry("alpha"), entry("bravo", "Action"), entry("charlie"), entry("delta")
# For sorting: by title Ant, Bee, Cat; by year Cat, Bee, Ant
ANT, BEE, CAT = entry("ant", year=2003), entry("bee", "Action", year=2002), entry("cat", year=2001)
SORT_KEYS = [lambda r: r.title.lower(), lambda r: r.year]


class Saves:
    """In place of the manager's save_config: counts the saves, and can fail as a full disk does"""

    def __init__(self):
        self.count = 0
        self.fail = False

    def __call__(self):
        if self.fail:
            raise OSError("the disk is full")
        self.count += 1


class FakeFrame:
    """What a view uses of MainFrame: the manager, the two flags, modal_dialog() and _tell()"""

    modal_dialog = MainFrame.modal_dialog

    def __init__(self, manager):
        self.library_manager = manager
        self.initializing = False
        self.dialog_active = False
        self.told = []

    def _run_deferred(self):
        pass

    def _tell(self, text, caption, icon=None):
        self.told.append((text, caption))


@pytest.fixture(scope="module")
def app():
    yield wx.GetApp() or wx.App(False)


@pytest.fixture
def make_view(app, isolated_manager_factory):
    """Builds a view on a real GameLibraryManager, so its SavedState is the real default config (plus `saved`).
    `prefix` is the tab's: "" for the Library's keys, "catalog_" for the Catalog's."""
    frames = []

    def make(saved=None, prefix="", **settings):
        top = wx.Frame(None, size=(800, 600))  # never shown
        frames.append(top)
        manager = isolated_manager_factory(config={"libraries": [], "SavedState": saved or {}})
        manager.save_config = Saves()
        owner = FakeFrame(manager)
        settings = {"idle_label": "Install", "on_action": lambda: None, "context_menu": lambda row: None,
                    "sort_keys": SORT_KEYS, **settings}
        return GamesPanel(top, owner, name="Games", state_prefix=prefix,
                          columns=[Column(title="Title", model_field=lambda r: r.title, width=200),
                                   Column(title="Year", model_field=lambda r: r.year, width=80)],
                          categories=[("genre", "Genre"), ("developer", "Developer")], **settings)

    yield make
    for top in frames:
        top.Destroy()


def titles(view):
    return [row.title for row in view.displayed]


def selected(view):
    return view.list_ctrl.GetFirstSelected()


def focused(view):
    return view.list_ctrl.GetFocusedItem()


def saved(view):
    return view.frame.library_manager.config["SavedState"]


def saves(view):
    return view.frame.library_manager.save_config


def player_selects(view, index):
    """What an arrow key or a click does"""
    view.list_ctrl.Select(index)
    view.list_ctrl.Focus(index)


def tree_nodes(view):
    tree = view.tree_ctrl
    nodes = {}
    category, cookie = tree.GetFirstChild(tree.GetRootItem())
    while category.IsOk():
        nodes[tree.GetItemText(category)] = values = {}
        child, child_cookie = tree.GetFirstChild(category)
        while child.IsOk():
            values[tree.GetItemText(child)] = child
            child, child_cookie = tree.GetNextChild(category, child_cookie)
        category, cookie = tree.GetNextChild(tree.GetRootItem(), cookie)
    return nodes


def key_event(key_code, event_type=wx.wxEVT_KEY_DOWN, ctrl=False, alt=False, shift=False, unicode=None):
    """A key event as wx gives it: a character's unicode key is its key code unless `unicode` is given"""
    event = wx.KeyEvent(event_type)
    event.SetKeyCode(key_code)
    if event_type == wx.wxEVT_CHAR:
        event.SetUnicodeKey(key_code if unicode is None else unicode)
    event.SetControlDown(ctrl)
    event.SetAltDown(alt)
    event.SetShiftDown(shift)
    return event


def press(window, key_code, event_type=wx.wxEVT_KEY_DOWN, **modifiers):
    """True when a handler took the key"""
    return window.GetEventHandler().ProcessEvent(key_event(key_code, event_type, **modifiers))


def type_text(view, text):
    """Type into the list as wx delivers it: a key-down for each key and, unless a handler took that, the
    character"""
    for char in text:
        key_code = wx.WXK_SPACE if char == " " else ord(char.upper())
        if not press(view.list_ctrl, key_code):
            press(view.list_ctrl, ord(char), wx.wxEVT_CHAR)


def typing_view(make_view, saved=None, **settings):
    """A view with five titles and a clock the test moves: (view, now)"""
    view = make_view(saved, **settings)
    now = [1000.0]
    view.type_ahead.clock = lambda: now[0]
    view.set_rows(rows(*[entry(name) for name in ("alpha", "area 51", "bravo", "temple of doom", "temple run")]))
    assert sorted(titles(view)) == ["Alpha", "Area 51", "Bravo", "Temple Of Doom", "Temple Run"]
    return view, now


def press_escape_in_tree(view):
    press(view.tree_ctrl, wx.WXK_ESCAPE)


def click_header(view, column):
    event = wx.ListEvent(wx.wxEVT_LIST_COL_CLICK, view.list_ctrl.GetId())
    event.SetColumn(column)
    view.list_ctrl.GetEventHandler().ProcessEvent(event)


def headers(view):
    return [view.list_ctrl.GetColumn(i).GetText() for i in range(view.list_ctrl.GetColumnCount())]


def test_label_and_description_follow_the_row_when_its_number_stays_but_the_game_changes(make_view):
    """wx fires no selection event when row 0 stays selected and another game moves into it"""
    view = make_view()
    view.set_rows(rows(ALPHA, BRAVO))
    assert (selected(view), view.action_btn.GetLabel()) == (0, "Install Alpha")
    view.search_combo.SetValue("bravo")  # as typed: the list is now only Bravo, in row 0
    assert titles(view) == ["Bravo"] and selected(view) == 0
    assert view.action_btn.GetLabel() == "Install Bravo"
    assert view.description.GetValue().startswith("About Bravo.")
    view.search_combo.SetValue("zzz")  # nothing listed: the label and the description are cleared
    assert (titles(view), selected(view)) == ([], -1)
    assert (view.action_btn.GetLabel(), view.action_btn.IsEnabled(), view.description.GetValue()) == \
        ("Install", False, "")


def test_every_programmatic_select_also_moves_the_focused_item(make_view):
    """Else the arrow keys start from the old focused row (row 0 after startup), not from the selected game"""
    view = make_view({"last_selected": "charlie"})
    view.set_rows(rows(ALPHA, BRAVO, CHARLIE))
    assert (selected(view), focused(view)) == (2, 2)  # the game remembered from the last run
    view.search_combo.SetValue("b")
    assert titles(view) == ["Bravo"] and (selected(view), focused(view)) == (0, 0)
    view.search_combo.SetValue("")
    view.select_row(view.displayed[2])
    assert (selected(view), focused(view)) == (2, 2)
    view.set_rows(rows(ALPHA, CHARLIE))  # Charlie moved up a row
    assert (selected(view), focused(view)) == (1, 1)


def test_tree_gains_a_node_for_a_new_value_and_keeps_its_selection(make_view):
    view = make_view()
    view.set_rows(rows(ALPHA, BRAVO))
    assert list(tree_nodes(view)["Genre"]) == ["Action", "Puzzle"]
    view.tree_ctrl.SelectItem(tree_nodes(view)["Genre"]["Puzzle"], True)  # as clicked
    assert titles(view) == ["Alpha"] and saved(view)["tree_selections"] == ["Genre/Puzzle"]
    view.set_rows(rows(ALPHA, BRAVO, entry("echo", "Strategy")))
    assert list(tree_nodes(view)["Genre"]) == ["Action", "Puzzle", "Strategy"]
    assert view._selected_paths() == ["Genre/Puzzle"] and titles(view) == ["Alpha"]
    view.set_rows(rows(ALPHA))  # a value with no game left goes
    assert list(tree_nodes(view)["Genre"]) == ["Puzzle"] and view._selected_paths() == ["Genre/Puzzle"]


def test_when_the_selected_game_is_gone_the_selection_keeps_its_row_number(make_view):
    view = make_view()
    view.set_rows(rows(ALPHA, BRAVO, CHARLIE))
    player_selects(view, 1)
    assert saved(view)["last_selected"] == "bravo"
    view.set_rows(rows(ALPHA, CHARLIE))  # Bravo was deleted: Charlie is in its row now
    assert (selected(view), focused(view), view.action_btn.GetLabel()) == (1, 1, "Install Charlie")
    assert saved(view)["last_selected"] == "charlie"  # and is what the next start selects
    view.set_rows(rows(ALPHA))  # the last row went: clamped to the list
    assert (selected(view), focused(view), view.action_btn.GetLabel()) == (0, 0, "Install Alpha")
    view.set_rows([])
    assert (selected(view), view.action_btn.GetLabel()) == (-1, "Install")


def test_a_filter_of_the_players_own_goes_to_the_first_row_and_is_not_remembered(make_view):
    view = make_view()
    view.set_rows(rows(ALPHA, BRAVO, CHARLIE, DELTA))
    player_selects(view, 3)
    view.search_combo.SetValue("a")  # Alpha, Bravo, Charlie, Delta all hold an "a"
    assert selected(view) == 3
    view.search_combo.SetValue("r")  # Bravo, Charlie: Delta is filtered out, it is not gone
    assert titles(view) == ["Bravo", "Charlie"] and (selected(view), focused(view)) == (0, 0)
    assert saved(view)["last_selected"] == "delta"


def test_a_refresh_that_changes_nothing_is_quiet(make_view):
    """Background refreshes run often, also inside a dialog's event loop: no selection event, no config save"""
    view = make_view()
    view.set_rows(rows(ALPHA, BRAVO, CHARLIE))
    player_selects(view, 1)
    events = []
    for kind in (wx.EVT_LIST_ITEM_SELECTED, wx.EVT_LIST_ITEM_FOCUSED):  # what a screen reader would announce
        view.list_ctrl.Bind(kind, lambda event: (events.append(event.GetIndex()), event.Skip()))
    before = saves(view).count
    view.set_rows(rows(ALPHA, BRAVO, CHARLIE))
    view.apply_filters()
    assert events == [] and saves(view).count == before
    assert (selected(view), focused(view), view.action_btn.GetLabel()) == (1, 1, "Install Bravo")


def test_a_refill_while_a_dialog_is_open_still_shows_the_selected_game(make_view):
    """Add, Edit and Locate refresh the list inside modal_dialog(), where selection events are ignored"""
    view = make_view()
    view.set_rows(rows(BRAVO, CHARLIE))
    player_selects(view, 1)
    view.frame.dialog_active = True
    view.set_rows(rows(ALPHA, BRAVO, CHARLIE))  # a game was added above it: Charlie is in row 2 now
    assert (selected(view), focused(view), view.action_btn.GetLabel()) == (2, 2, "Install Charlie")
    assert view.description.GetValue().startswith("About Charlie.")
    view.set_rows(rows(ALPHA, BRAVO))  # and when it goes: Bravo, in the last row
    assert (selected(view), focused(view), view.action_btn.GetLabel()) == (1, 1, "Install Bravo")
    assert view.description.GetValue().startswith("About Bravo.")
    assert saved(view)["last_selected"] == "bravo" and saves(view).count == 1


def test_focus_leaves_the_action_button_before_an_empty_list_disables_it(make_view):
    """As when the progress area hides it: focus goes to the list, never while a dialog is open"""
    view = make_view()
    view.set_rows(rows(ALPHA))
    focus = []
    view.list.control.SetFocus = lambda: focus.append(True)
    view.action_btn.HasFocus = lambda: True
    view.frame.dialog_active = True
    view.search_combo.SetValue("zzz")
    assert focus == [] and not view.action_btn.IsEnabled()
    view.frame.dialog_active = False
    view.search_combo.SetValue("")
    assert focus == [] and view.action_btn.IsEnabled()  # a game is selected again: nothing to move
    view.set_rows([])
    assert focus == [True] and not view.action_btn.IsEnabled()


def test_a_failed_config_save_never_stops_the_filter(make_view):
    view = make_view()
    view.set_rows(rows(ALPHA, BRAVO))
    saves(view).fail = True
    view.tree_ctrl.SelectItem(tree_nodes(view)["Genre"]["Action"], True)
    assert titles(view) == ["Bravo"] and view.action_btn.GetLabel() == "Install Bravo"
    assert saved(view)["tree_selections"] == ["Genre/Action"]  # kept for the next save that works
    view.set_rows(rows(ALPHA, BRAVO, CHARLIE))
    press_escape_in_tree(view)
    player_selects(view, 2)
    assert view.action_btn.GetLabel() == "Install Charlie" and saved(view)["last_selected"] == "charlie"


def test_escape_in_the_tree_clears_every_filter(make_view):
    view = make_view()
    view.set_rows(rows(ALPHA, BRAVO))
    view.tree_ctrl.SelectItem(tree_nodes(view)["Genre"]["Action"], True)
    assert titles(view) == ["Bravo"]
    press_escape_in_tree(view)
    assert titles(view) == ["Alpha", "Bravo"] and saved(view)["tree_selections"] == []
    press_escape_in_tree(view)  # nothing selected: wx fires no event, the filters are applied all the same
    assert titles(view) == ["Alpha", "Bravo"]


# ---- what differs between the tabs: each is a setting of the one view ------------------------------------------

def test_saved_search_and_sort_are_read_when_the_view_is_built(make_view):
    """The list is filled before the main window loads the rest of its saved state"""
    view = make_view({"last_search": "a", "sort_column": 1, "sort_ascending": False},
                     sort_keys=SORT_KEYS)
    view.set_rows(rows(ANT, BEE, CAT))
    assert view.search_combo.GetValue() == "a" and titles(view) == ["Ant", "Cat"]  # newest first; no "a" in Bee
    assert headers(view) == ["Title", "Year ▼"]


def test_sorting_keeps_the_game_selected_and_its_label_right(make_view):
    view = make_view(sort_keys=SORT_KEYS)
    view.set_rows(rows(ANT, BEE, CAT))
    assert titles(view) == ["Ant", "Bee", "Cat"] and headers(view) == ["Title ▲", "Year"]
    click_header(view, 1)  # by year, oldest first: Ant moves from row 0 to row 2
    assert titles(view) == ["Cat", "Bee", "Ant"] and headers(view) == ["Title", "Year ▲"]
    assert (selected(view), focused(view), view.action_btn.GetLabel()) == (2, 2, "Install Ant")
    click_header(view, 1)  # the same column again: the other way round
    assert titles(view) == ["Ant", "Bee", "Cat"] and headers(view) == ["Title", "Year ▼"]
    assert (selected(view), focused(view), view.action_btn.GetLabel()) == (0, 0, "Install Ant")
    assert (saved(view)["sort_column"], saved(view)["sort_ascending"]) == (1, False)
    press(view.list_ctrl, ord("1"), wx.wxEVT_CHAR)  # a digit key sorts by that column
    assert headers(view) == ["Title ▲", "Year"] and saved(view)["sort_column"] == 0
    saves(view).fail = True  # a failed save doesn't stop the sort
    press(view.list_ctrl, ord("2"), wx.wxEVT_CHAR)
    assert titles(view) == ["Cat", "Bee", "Ant"] and view.action_btn.GetLabel() == "Install Ant"


def test_rows_that_tie_in_the_sorted_column_are_in_title_order(make_view):
    view = make_view(sort_keys=SORT_KEYS)
    view.set_rows(rows(entry("zed", year=2000), entry("amy", year=2000), entry("old", year=1990)))
    click_header(view, 1)
    assert titles(view) == ["Old", "Amy", "Zed"]
    click_header(view, 1)
    assert titles(view) == ["Amy", "Zed", "Old"]


def test_a_sort_key_whose_values_changed_sorts_again_on_refill(make_view):
    """The Library's Status column: the statuses arrive later, from a worker thread, and the main window then
    calls refill()"""
    status = {}
    view = make_view(sort_keys=[lambda r: r.title.lower(), lambda r: status.get(r.id, "")])
    view.set_rows(rows(ANT, BEE, CAT))
    click_header(view, 1)
    assert titles(view) == ["Ant", "Bee", "Cat"] and selected(view) == 0
    status.update(ant="Ready", bee="Executable not found", cat="Ready")
    view.refill()
    assert titles(view) == ["Bee", "Ant", "Cat"]
    assert (selected(view), focused(view), view.action_btn.GetLabel()) == (1, 1, "Install Ant")


def test_tree_shows_only_the_chosen_categories(make_view):
    view = make_view(tree_keys=["genre"])
    view.set_rows(rows(ALPHA, BRAVO))
    assert list(tree_nodes(view)) == ["Genre"]
    view.tree_ctrl.SelectItem(tree_nodes(view)["Genre"]["Action"], True)
    view.set_tree_keys(["genre", "developer"])
    assert list(tree_nodes(view)) == ["Genre", "Developer"]
    assert view._selected_paths() == ["Genre/Action"] and titles(view) == ["Bravo"]
    view.set_tree_keys(["developer"])  # the category that held the selection goes: it no longer filters
    assert list(tree_nodes(view)) == ["Developer"] and titles(view) == ["Alpha", "Bravo"]
    assert saved(view)["tree_selections"] == []
    view.set_tree_keys([])
    assert tree_nodes(view) == {} and titles(view) == ["Alpha", "Bravo"]


def test_row_filter_hides_rows_from_the_list_but_keeps_their_tree_values(make_view):
    hidden = set()
    view = make_view(row_filter=lambda row: row.id not in hidden)
    view.set_rows(rows(ALPHA, BRAVO))
    player_selects(view, 1)
    hidden.add("bravo")  # as hiding a library does
    view.apply_filters()
    assert titles(view) == ["Alpha"] and list(tree_nodes(view)["Genre"]) == ["Action", "Puzzle"]
    assert (selected(view), focused(view), view.action_btn.GetLabel()) == (0, 0, "Install Alpha")


def test_the_install_progress_shows_beside_the_action_button_which_stays(make_view):
    """On both tabs: while a job runs or waits the button is still there to press"""
    view = make_view(idle_label="Launch")
    assert view.action_btn.GetLabel() == "Launch"
    view.set_rows(rows(ALPHA))
    button = view.action_btn
    assert (button.IsShown(), button.IsEnabled(), view.progress_area.IsShown()) == (True, True, False)
    view.set_busy(True)
    assert (button.IsShown(), button.IsEnabled(), view.progress_area.IsShown()) == (True, True, True)
    view.set_busy(False)
    assert (button.IsShown(), button.IsEnabled(), view.progress_area.IsShown()) == (True, True, False)


def test_library_rows_launch_describe_and_are_remembered_by_id(make_view):
    games = [Game(title="Hand", launch_path="C:/games/hand.exe", platforms=["Windows"]),
             Game(title="Alpha", launch_path="Alpha/alpha.exe", library_id="lib", catalog_id="alpha")]
    view = make_view(idle_label="Launch")
    view.set_rows(library_rows(games, lambda game: ALPHA if game.catalog_id else None))
    assert view.selected_row().game is games[1] and view.action_btn.GetLabel() == "Launch Alpha"
    assert view.description.GetValue().startswith("About Alpha.")
    player_selects(view, 1)
    assert (view.action_btn.GetLabel(), view.description.GetValue()) == ("Launch Hand", "")
    assert saved(view)["last_selected"] == "C:/games/hand.exe"
    view.set_rows([])
    assert (view.action_btn.GetLabel(), view.action_btn.IsEnabled()) == ("Launch", False)


def test_column_widths_are_restored_and_saved(make_view):
    view = make_view({"column_widths": [123, 45]})
    assert [view.list_ctrl.GetColumnWidth(i) for i in range(2)] == [123, 45]
    view.list_ctrl.SetColumnWidth(0, 222)
    view.save_state()
    assert saved(view)["column_widths"] == [222, 45]


def test_search_and_tree_filters_are_saved_only_while_there_are_rows(make_view):
    """With no rows (the catalog offline with no saved copy) the last filters wait for the games"""
    view = make_view({"last_search": "bra", "tree_selections": ["Genre/Action"]})
    view.set_rows([])
    view.save_state()
    assert (saved(view)["last_search"], saved(view)["tree_selections"]) == ("bra", ["Genre/Action"])
    view.set_rows(rows(ALPHA, BRAVO))  # the tree has values now: the saved selections come back
    assert view._selected_paths() == ["Genre/Action"] and titles(view) == ["Bravo"]
    view.search_combo.SetValue("b")
    view.save_state()
    assert saved(view)["last_search"] == "b"


def test_a_tabs_own_keys_and_its_hook_after_every_refill(make_view):
    keys, shown, acted = [], [], []

    def on_key(event):
        keys.append(event.GetKeyCode())
        return event.GetKeyCode() == wx.WXK_DELETE

    view = make_view(on_key=on_key, on_action=lambda: acted.append(view.selected_row().title),
                     on_shown=lambda: shown.append(view.action_btn.GetLabel()))
    view.set_rows(rows(ALPHA, BRAVO))
    assert shown[-1] == "Install Alpha"
    view.search_combo.SetValue("bravo")  # no selection event (row 0 stays selected), the hook still runs
    assert shown[-1] == "Install Bravo"
    assert press(view.list_ctrl, wx.WXK_DELETE) and not press(view.list_ctrl, ord("X"))
    assert press(view.list_ctrl, wx.WXK_RETURN) and press(view.list_ctrl, wx.WXK_SPACE)
    assert keys == [wx.WXK_DELETE, ord("X")] and acted == ["Bravo", "Bravo"]


def test_focus_list_refuses_while_a_dialog_is_open_or_the_app_is_starting(make_view):
    view = make_view()
    view.set_rows(rows(ALPHA, BRAVO))
    focus = []
    view.list.control.SetFocus = lambda: focus.append(True)
    view.frame.dialog_active = True
    view.focus_list()
    view.frame.dialog_active, view.frame.initializing = False, True
    view.focus_list()
    assert focus == []
    view.frame.initializing = False
    view.focus_list()
    assert focus == [True] and (selected(view), focused(view)) == (0, 0)


def test_two_games_with_one_launch_path_are_two_rows_that_stay_apart(make_view):
    games = [Game(title="First", launch_path="C:/games/same.exe"),
             Game(title="Second", launch_path="C:/games/same.exe")]
    view = make_view(idle_label="Launch")
    view.set_rows(library_rows(games, lambda game: None))
    player_selects(view, 1)
    view.set_rows(library_rows(games, lambda game: None))
    view.refill()  # as every Status result does
    assert view.selected_row().game is games[1] and view.action_btn.GetLabel() == "Launch Second"


def twins():
    """Alpha and Mike Twin share a launch path (Locate has no duplicate check); Alpha comes first in games.json"""
    return [Game(title="Alpha", launch_path="C:/games/same.exe"), Game(title="Bravo", launch_path="C:/games/b.exe"),
            Game(title="Charlie", launch_path="C:/games/c.exe"),
            Game(title="Mike Twin", launch_path="C:/games/same.exe")]


def test_deleting_the_first_of_two_games_with_one_launch_path_leaves_the_selection_on_its_row(make_view):
    """The other one takes over the deleted game's row id; the selection must not jump to it"""
    games = twins()
    view = make_view(idle_label="Launch")
    view.set_rows(library_rows(games, lambda game: None))
    player_selects(view, 0)
    del games[0]  # Del on Alpha
    view.set_rows(library_rows(games, lambda game: None))
    assert titles(view) == ["Bravo", "Charlie", "Mike Twin"]
    assert (selected(view), focused(view), view.action_btn.GetLabel()) == (0, 0, "Launch Bravo")
    assert saved(view)["last_selected"] == "C:/games/b.exe"


def test_a_game_given_another_launch_path_stays_selected_and_is_the_one_the_next_start_selects(make_view):
    """Ctrl+E or Locate on the first of two games with one launch path: its row id changes and the other game
    takes the old one over. Enter must still launch the edited game, now and after a restart."""
    games = twins()
    view = make_view(idle_label="Launch")
    view.set_rows(library_rows(games, lambda game: None))
    player_selects(view, 0)
    games[0].launch_path = "C:/games/moved.exe"
    view.set_rows(library_rows(games, lambda game: None))
    assert view.selected_row().game is games[0] and view.action_btn.GetLabel() == "Launch Alpha"
    assert (selected(view), focused(view)) == (0, 0) and saved(view)["last_selected"] == "C:/games/moved.exe"
    restarted = make_view({"last_selected": saved(view)["last_selected"]}, idle_label="Launch")
    restarted.set_rows(library_rows(games, lambda game: None))
    assert restarted.selected_row().game is games[0]
    # the same when the game is not the first with that path: only its own id changes
    games[3].launch_path = "C:/games/twin.exe"
    player_selects(view, 3)
    view.set_rows(library_rows(games, lambda game: None))
    assert view.selected_row().game is games[3] and saved(view)["last_selected"] == "C:/games/twin.exe"


def test_a_selected_catalog_game_whose_object_was_replaced_stays_selected(make_view):
    """What a finished job and an index take do to a catalog game in the Library: a new Game, the same catalog id"""
    games = [Game(title="Hand", launch_path="C:/games/hand.exe"),
             Game(title="Alpha", launch_path="Alpha/alpha.exe", library_id="lib", catalog_id="alpha"),
             Game(title="Zed", launch_path="C:/games/zed.exe")]
    view = make_view(idle_label="Launch")
    view.set_rows(library_rows(games, lambda game: None))
    player_selects(view, 1)
    player_selects(view, 0)  # Alpha
    games[1] = Game.from_dict({**games[1].to_dict(), "title": "Omega"})  # replaced, and renamed by the catalog
    view.set_rows(library_rows(games, lambda game: None))
    assert view.selected_row().game is games[1] and view.action_btn.GetLabel() == "Launch Omega"
    assert saved(view)["last_selected"] == "alpha"
    del games[1]  # uninstalled: the row number
    view.set_rows(library_rows(games, lambda game: None))
    assert (selected(view), view.action_btn.GetLabel()) == (1, "Launch Zed")


def test_a_background_refill_scrolls_only_to_a_selected_row_that_was_in_view(make_view):
    """A finished job or a Status result must not scroll the list away from where the player scrolled it. The
    list is never shown here, so what it has in view is given by hand."""
    view = make_view()
    many = [entry(f"game{number:03d}") for number in range(100)]
    view.set_rows(rows(*many))
    player_selects(view, 50)
    scrolled, lc = [], view.list_ctrl
    lc.EnsureVisible = scrolled.append
    lc.GetTopItem, lc.GetCountPerPage = (lambda: 0), (lambda: 10)  # the player scrolled to the top: rows 0 to 9
    view.set_rows(rows(entry("aaa"), *many))  # a game appeared above the selected one
    view.refill()
    assert (selected(view), focused(view)) == (51, 51) and scrolled == []
    lc.GetTopItem = lambda: 45  # the selected row is in view now
    view.set_rows(rows(entry("aaa"), entry("aab"), *many))
    assert (selected(view), focused(view)) == (52, 52) and scrolled == [52]
    lc.GetTopItem = lambda: 0  # out of view again: what the player does always brings the selected row into view
    view.search_combo.SetValue("game")
    assert (selected(view), scrolled) == (50, [52, 50])
    view.select_row(view.displayed[70])
    view.focus_list()
    assert scrolled == [52, 50, 70, 70]


def test_the_first_fill_brings_the_remembered_row_into_view(make_view):
    view = make_view({"last_selected": "game090"})
    scrolled = []
    view.list_ctrl.EnsureVisible = scrolled.append
    view.set_rows(rows(*[entry(f"game{number:03d}") for number in range(100)]))
    assert (selected(view), focused(view), scrolled) == (90, 90, [90])


def test_a_tree_rebuild_keeps_the_open_categories_and_the_focused_item(make_view):
    view = make_view()
    view.set_rows(rows(ALPHA, BRAVO))
    tree, nodes = view.tree_ctrl, tree_nodes(view)
    tree.Expand(tree.GetItemParent(nodes["Developer"]["Dev"]))  # open, with nothing selected under it
    tree.SetFocusedItem(nodes["Developer"]["Dev"])  # the player moved there without selecting it
    before = saves(view).count
    view.set_rows(rows(ALPHA, BRAVO, entry("echo", "Strategy")))  # a refresh that adds a tree value
    nodes = tree_nodes(view)
    assert tree.IsExpanded(tree.GetItemParent(nodes["Developer"]["Dev"]))
    assert not tree.IsExpanded(tree.GetItemParent(nodes["Genre"]["Action"]))  # a closed one stays closed
    assert tree.GetItemText(tree.GetFocusedItem()) == "Dev"
    assert view._selected_paths() == [] and titles(view) == ["Alpha", "Bravo", "Echo"]
    assert saves(view).count == before  # moving the tree's focus is not a change of filter
    tree.SelectItem(nodes["Genre"]["Puzzle"], True)
    tree.SetFocusedItem(nodes["Genre"]["Action"])
    view.set_rows(rows(ALPHA, BRAVO))  # Strategy went: rebuilt again, with a selection this time
    assert tree.GetItemText(tree.GetFocusedItem()) == "Action"
    assert view._selected_paths() == ["Genre/Puzzle"] and titles(view) == ["Alpha"]
    assert tree.IsExpanded(tree.GetItemParent(tree_nodes(view)["Developer"]["Dev"]))
    view.set_rows(rows(ALPHA))  # the focused value is gone: its category has the focus
    assert tree.GetItemText(tree.GetFocusedItem()) == "Genre" and view._selected_paths() == ["Genre/Puzzle"]


def test_a_tree_rebuild_keeps_the_focus_on_the_root(make_view):
    """"Filters", the root, is where the tree's focus starts; it has no category or value of its own"""
    view = make_view()
    view.set_rows(rows(ALPHA, BRAVO))
    tree = view.tree_ctrl
    tree.SelectItem(tree_nodes(view)["Genre"]["Puzzle"], True)
    tree.SetFocusedItem(tree.GetRootItem())
    assert view._selected_paths() == ["Genre/Puzzle"]
    before = saves(view).count
    view.set_rows(rows(ALPHA, BRAVO, entry("echo", "Strategy")))  # a refresh that adds a tree value
    root = tree.GetRootItem()
    assert tree.GetFocusedItem() == root and tree.GetItemText(root) == "Filters"
    assert not tree.IsSelected(root) and view._selected_paths() == ["Genre/Puzzle"]
    assert titles(view) == ["Alpha"] and saves(view).count == before


def test_only_values_are_selected_from_the_saved_tree_selections(make_view):
    """A category's own name in a hand-edited config.json selects nothing"""
    view = make_view({"tree_selections": ["Genre", "Genre/Action", "Genre/Gone"]})
    view.set_rows(rows(ALPHA, BRAVO))
    tree = view.tree_ctrl
    assert [tree.GetItemText(item) for item in tree.GetSelections()] == ["Action"] and titles(view) == ["Bravo"]


@pytest.mark.parametrize("prefix", ["", "catalog_"])
@pytest.mark.parametrize("selections", [None, 7, [["Genre/Action"], 7, None, "Genre/Action"]])
def test_saved_tree_selections_that_are_not_a_list_of_paths_do_not_stop_the_first_fill(make_view, prefix, selections):
    """A hand-edited config.json: the app must still start. Only the paths in a list count."""
    view = make_view({prefix + "tree_selections": selections}, prefix=prefix)
    view.set_rows(rows(ALPHA, BRAVO))
    assert titles(view) == (["Bravo"] if isinstance(selections, list) else ["Alpha", "Bravo"])


@pytest.mark.parametrize("prefix", ["", "catalog_"])
@pytest.mark.parametrize("position", ["wide", 3.5, [300], None])
def test_a_saved_splitter_position_that_is_not_a_number_gives_the_middle(make_view, prefix, position):
    view = make_view({prefix + "splitter_position": position}, prefix=prefix)
    view.GetTopLevelParent().SendSizeEvent()
    view.restore_sash()
    assert view.splitter.GetSashPosition() == view.splitter.GetSize()[0] // 2 > 200


def test_the_splitter_goes_where_it_was_saved_or_to_the_middle_and_is_saved(make_view):
    """restore_sash() is called once the window has its size. Left to wx, the sash would sit at the minimum pane
    size (100) for good, as the Catalog's did."""
    fresh, saved_before = make_view(), make_view({"splitter_position": 300})
    for view in (fresh, saved_before):
        view.GetTopLevelParent().SendSizeEvent()  # the window gets its size
        view.restore_sash()
    width = fresh.splitter.GetSize()[0]
    assert width > 400 and fresh.splitter.GetSashPosition() == width // 2
    assert saved_before.splitter.GetSashPosition() == 300
    saved_before.splitter.SetSashPosition(250)  # as dragged
    saved_before.save_state()
    assert saved(saved_before)["splitter_position"] == 250


def test_random_game_asks_then_selects_the_picked_row_and_acts_on_it(make_view, monkeypatch):
    """One skeleton for Ctrl+R on both tabs and Ctrl+Shift+R; the tab gives the candidates, the text for when
    there are none, and what Yes does"""
    asked, acted, answers = [], [], [wx.NO, wx.YES, wx.YES]
    monkeypatch.setattr(wx, "MessageBox",
                        lambda text, caption, style: (asked.append((text, caption)), answers.pop(0))[1])
    monkeypatch.setattr("games_panel.random.choice", lambda candidates: candidates[-1])
    games = [Game(title="Hand", launch_path="C:/games/hand.exe"), Game(title="Zed", launch_path="C:/games/zed.exe")]
    view = make_view(idle_label="Launch")
    view.set_rows(library_rows(games, lambda game: None))
    nothing = ("No games available to choose from.", "No Games")

    def act_in(frame):  # the row is held while it is acted on: no refresh may replace it meanwhile
        return lambda row: acted.append((row.title, frame.dialog_active))

    view.random_game([], nothing, act_in(view.frame))
    assert view.frame.told == [nothing] and asked == [] and acted == []
    view.random_game(view.displayed, nothing, act_in(view.frame))  # the player says No
    assert asked == [("Your game for today is...\n\nZed\n\nLaunch now?", "Random Game")]  # the Library's wording
    assert acted == [] and selected(view) == 0
    view.random_game(view.displayed, nothing, act_in(view.frame))  # and then Yes
    assert acted == [("Zed", True)] and not view.frame.dialog_active
    assert (selected(view), focused(view), view.action_btn.GetLabel()) == (1, 1, "Launch Zed")
    catalog = make_view(prefix="catalog_")
    catalog.set_rows(rows(ALPHA, BRAVO))
    catalog.random_game(catalog.displayed, nothing, act_in(catalog.frame), caption="Random Game to Install")
    assert asked[-1] == ("Your game for today is...\n\nBravo\n\nInstall now?", "Random Game to Install")
    assert acted[-1] == ("Bravo", True) and catalog.action_btn.GetLabel() == "Install Bravo"


@pytest.mark.parametrize("prefix", ["", "catalog_"])
def test_a_view_uses_only_saved_state_keys_the_default_config_has(make_view, prefix):
    """The Library's keys have no prefix, the Catalog's start with catalog_. A key the defaults lack is a KeyError
    when the main window starts; one the view adds on its own is never filled in for a config without it."""
    view = make_view(prefix=prefix)
    defaults = set(saved(view))
    view.restore_sash()
    view.set_rows(rows(ALPHA, BRAVO, CHARLIE))
    player_selects(view, 2)
    view.tree_ctrl.SelectItem(tree_nodes(view)["Genre"]["Puzzle"], True)
    view.search_combo.SetValue("a")
    click_header(view, 1)
    view.set_tree_keys(["genre"])
    view.set_rows(rows(ALPHA, BRAVO))  # Charlie, the selected game, went: the row in its place is remembered
    view.save_state()
    state, other = saved(view), "" if prefix else "catalog_"
    assert set(state) == defaults
    assert (state[prefix + "last_selected"], state[prefix + "last_search"], state[prefix + "tree_selections"]) == \
        ("alpha", "a", ["Genre/Puzzle"])
    assert len(state[prefix + "column_widths"]) == 2 and state[prefix + "splitter_position"] > 0
    assert (state[prefix + "sort_column"], state[prefix + "sort_ascending"]) == (1, True)
    assert (state[other + "sort_column"], state[other + "sort_ascending"]) == (0, True)
    assert (state[other + "last_selected"], state[other + "last_search"], state[other + "tree_selections"],
            state[other + "column_widths"], state[other + "splitter_position"]) == (None, "", [], None, None)


def test_the_tree_is_rebuilt_only_when_what_it_shows_changed(make_view):
    view = make_view()
    builds, build = [], view._build_tree
    view._build_tree = lambda nodes: (builds.append(len(nodes)), build(nodes))
    view.set_rows(rows(ALPHA, BRAVO))
    view.set_rows(rows(ALPHA, BRAVO))  # a background refresh
    view.set_rows(rows(BRAVO, ALPHA, DELTA))  # another game, no new tree value
    view.apply_filters()
    assert len(builds) == 1
    view.set_rows(rows(ALPHA, BRAVO, entry("echo", "Strategy")))
    assert len(builds) == 2


def test_a_view_starts_from_any_hand_edited_saved_state(make_view):
    """config.json may hold anything; what isn't the right kind is ignored and the view builds as on a first run"""
    view = make_view(saved={"column_widths": 5, "last_search": 5, "tree_selections": 5, "last_selected": [1],
                            "splitter_position": "middle", "sort_column": "2"})
    view.set_rows(rows(ALPHA, BRAVO))
    view.restore_sash()
    assert view.search_combo.GetValue() == "" and titles(view) == ["Alpha", "Bravo"] and selected(view) == 0


def test_a_config_save_that_fails_is_logged_and_never_raised(make_view, caplog):
    """Called directly: an exception inside a wx event handler is swallowed by wx and would prove nothing"""
    view = make_view(sort_keys=SORT_KEYS)
    view.set_rows(rows(ANT, BEE, CAT))
    saves(view).fail = True
    with caplog.at_level(logging.WARNING):
        view.save_config()
        view.sort_by(1)
        view.set_tree_keys(["genre"])
        view.on_tree_selection()
    assert caplog.text.count("Couldn't save the config") == 4


def test_the_filter_is_applied_before_the_config_is_saved(make_view):
    view = make_view(sort_keys=SORT_KEYS)
    view.set_rows(rows(ALPHA, BRAVO))
    seen = []
    view.frame.library_manager.save_config = lambda: seen.append(titles(view))  # what the list shows at each save
    view.tree_ctrl.SelectItem(tree_nodes(view)["Genre"]["Action"], True)
    assert seen == [["Bravo"]]
    press_escape_in_tree(view)
    assert seen[-1] == ["Alpha", "Bravo"]
    click_header(view, 0)  # Title again: the other way round
    assert seen[-1] == ["Bravo", "Alpha"]


def test_focus_goes_to_the_list_when_the_progress_area_hides_with_focus_inside(make_view):
    """Real keyboard focus, which a frame that is never shown does hold within its own thread"""
    view = make_view()
    view.set_rows(rows(ALPHA))
    view.action_btn.SetFocus()
    view.set_busy(True)  # a job starts: the button stays, so focus stays on it
    assert view.action_btn.HasFocus()
    view.progress_area.cancel_btn.SetFocus()  # the player is on Cancel when the job ends
    assert view.progress_area.cancel_btn.HasFocus()
    view.set_busy(False)
    assert view.list_ctrl.HasFocus()


def test_a_selection_event_while_a_dialog_is_open_is_not_the_players(make_view):
    """No dialog lets the player reach the list, so such an event is spurious: nothing is remembered or saved"""
    view = make_view()
    view.set_rows(rows(ALPHA, BRAVO, CHARLIE))
    player_selects(view, 1)
    view.frame.dialog_active = True
    player_selects(view, 2)
    assert saved(view)["last_selected"] == "bravo" and saves(view).count == 1


@pytest.mark.parametrize("column", [7, -1, "1", None])
def test_a_saved_sort_column_the_list_does_not_have_is_ignored(make_view, column):
    """A hand-edited config.json must not stop every fill with an IndexError"""
    view = make_view({"sort_column": column, "sort_ascending": False}, sort_keys=SORT_KEYS)
    view.set_rows(rows(CAT, ANT, BEE))
    assert titles(view) == ["Ant", "Bee", "Cat"] and headers(view) == ["Title ▲", "Year"]


# ---- type-ahead: typing in the list jumps to the title that starts with what was typed --------------------------

def test_typing_jumps_to_the_title_as_the_players_own_move(make_view):
    """As an arrow key: the label, the description, the focused item, the scroll and the remembered game follow"""
    view, _ = typing_view(make_view)
    scrolled = []
    view.list_ctrl.EnsureVisible = scrolled.append
    type_text(view, "te")
    assert (selected(view), focused(view), scrolled[-1]) == (3, 3, 3)
    assert view.action_btn.GetLabel() == "Install Temple Of Doom"
    assert view.description.GetValue().startswith("About Temple Of Doom.")
    assert saved(view)["last_selected"] == "temple of doom" and saves(view).count == 1
    restarted = make_view({"last_selected": saved(view)["last_selected"]})
    restarted.set_rows(list(view.displayed))
    assert restarted.selected_row().title == "Temple Of Doom"


def test_space_belongs_to_the_text_for_three_seconds_and_acts_after_that(make_view):
    acted = []
    view, now = typing_view(make_view, on_action=lambda: acted.append(view.selected_row().title))
    type_text(view, "temple")
    assert selected(view) == 3
    now[0] += 1.2  # the player waits for the screen reader to finish the row
    # The key-down itself is taken: wx turns a Space key-down that reaches the list control into an activation
    assert press(view.list_ctrl, wx.WXK_SPACE) and acted == []
    now[0] += 0.5
    type_text(view, "r")  # "temple r", within a second of the Space: past "Temple Of Doom", and nothing launched
    assert acted == [] and selected(view) == 4
    type_text(view, "\r")  # Enter always acts, in the middle of a text too
    assert acted == ["Temple Run"]
    now[0] += 3.5  # more than 3 seconds after the last character: Space is the action again
    type_text(view, " ")
    assert acted == ["Temple Run", "Temple Run"] and selected(view) == 4
    type_text(view, "t")  # steps on to "Temple Of Doom"; then a Space every 0.8 seconds
    for _ in range(3):
        now[0] += 0.8
        type_text(view, " ")
    assert acted == ["Temple Run", "Temple Run"] and selected(view) == 3
    now[0] += 0.8  # 3.2 seconds after "t": the Spaces before it did not push the limit out
    type_text(view, " ")
    assert acted == ["Temple Run", "Temple Run", "Temple Of Doom"]


@pytest.mark.parametrize("held", [{"shift": True}, {"ctrl": True}, {"ctrl": True, "shift": True},
                                  {"ctrl": True, "alt": True}, {"alt": True, "shift": True}])
def test_a_space_with_any_modifier_but_alt_alone_is_a_plain_space(make_view, held):
    """Passed on, Ctrl+Space made the native list clear the selection while the button still named the game,
    and Enter then acted on nothing. Only Alt alone passes a Space on (the window menu)."""
    acted = []
    view, now = typing_view(make_view, on_action=lambda: acted.append(view.selected_row().title))
    type_text(view, "temple")
    now[0] += 1.2
    assert press(view.list_ctrl, wx.WXK_SPACE, **held) and acted == []  # part of the text
    type_text(view, "r")  # "temple r"
    assert selected(view) == 4
    now[0] += 3.5
    assert press(view.list_ctrl, wx.WXK_SPACE, **held) and acted == ["Temple Run"]
    assert not press(view.list_ctrl, wx.WXK_SPACE, alt=True) and acted == ["Temple Run"]


def test_a_digit_belongs_to_the_text_while_typing_and_sorts_otherwise(make_view):
    view, now = typing_view(make_view)
    type_text(view, "area 51")
    assert selected(view) == 1 and headers(view) == ["Title ▲", "Year"]  # "5" and "1" did not sort
    now[0] += 2
    type_text(view, "2")  # nothing is being typed: the digit sorts by its column
    assert headers(view) == ["Title", "Year ▲"] and view.selected_row().title == "Area 51"
    now[0] += 2
    type_text(view, "9")  # no such column, and a digit never starts a text
    assert headers(view) == ["Title", "Year ▲"] and view.selected_row().title == "Area 51"
    assert not view.type_ahead.typing()


def test_a_pause_starts_a_new_text_and_a_repeated_letter_steps_on(make_view):
    view, now = typing_view(make_view)
    type_text(view, "t")
    assert selected(view) == 3
    now[0] += 1.5
    type_text(view, "a")  # a new text: "a", not "ta"
    assert selected(view) == 0
    type_text(view, "a")  # the same letter again: the next title that starts with it
    assert selected(view) == 1
    type_text(view, "a")
    assert selected(view) == 0  # and round again
    now[0] += 1.5
    type_text(view, "ar")  # several letters: at or after the current row
    assert selected(view) == 1


def test_typing_that_matches_no_title_and_keys_with_ctrl_alone_or_alt_alone_change_nothing(make_view):
    view, now = typing_view(make_view)
    player_selects(view, 2)
    before = saves(view).count
    type_text(view, "zz")
    now[0] += 2
    for modifiers in ({"ctrl": True}, {"alt": True}):
        press(view.list_ctrl, ord("t"), wx.wxEVT_CHAR, **modifiers)
    for key in (wx.WXK_ESCAPE, wx.WXK_BACK, wx.WXK_TAB, wx.WXK_DELETE):  # wx sends these as characters too
        press(view.list_ctrl, key, wx.wxEVT_CHAR)
    assert selected(view) == 2 and saves(view).count == before and not view.type_ahead.typing()


def test_typing_looks_at_titles_only_in_the_order_the_list_shows(make_view):
    view, now = typing_view(make_view, {"sort_column": 0, "sort_ascending": False})
    assert titles(view)[0] == "Temple Run" and selected(view) == 0
    type_text(view, "dev")  # every row's developer is "Dev": the title alone counts
    assert selected(view) == 0
    now[0] += 2
    type_text(view, "t")  # steps on from the current row, downwards in the order shown
    assert view.selected_row().title == "Temple Of Doom"


def test_typing_reads_the_character_not_the_key_code(make_view):
    """wx gives a character outside Latin-1 no key code (WXK_NONE): only GetUnicodeKey holds it"""
    view = make_view()
    view.set_rows(rows(entry("alpha"), entry("zebra"), entry("żuk")))
    assert titles(view) == ["Alpha", "Zebra", "Żuk"]
    assert press(view.list_ctrl, wx.WXK_NONE, wx.wxEVT_CHAR, unicode=ord("Ż"))  # in capitals: any case finds it
    assert view.selected_row().title == "Żuk" and saved(view)["last_selected"] == "żuk"


def test_space_in_a_text_never_moves_the_selection(make_view):
    """The player types `a`, lands on Alpha and presses Space: the text is then "a ", which only "A Hero's Call"
    starts with, and the selection must not go there"""
    hero = {**entry("a hero's call"), "title": "A Hero's Call"}
    acted = []
    view = make_view(on_action=lambda: acted.append(view.selected_row().title))
    now = [1000.0]
    view.type_ahead.clock = lambda: now[0]
    view.set_rows(rows(hero, entry("alpha"), entry("apple"), entry("bravo")))
    assert titles(view) == ["A Hero's Call", "Alpha", "Apple", "Bravo"] and selected(view) == 0
    type_text(view, "a ")
    assert view.selected_row().title == "Alpha" and acted == []
    now[0] += 4  # the text is over: Space is the action, on the game the player landed on
    type_text(view, " ")
    assert acted == ["Alpha"]
    now[0] += 4
    type_text(view, "a h")  # a Space only extends the text; the next character does the jump
    assert view.selected_row().title == "A Hero's Call" and acted == ["Alpha"]


@pytest.mark.parametrize("key", [
    "ESCAPE", "BACK", "UP", "DOWN", "HOME", "END", "PAGEUP", "PAGEDOWN",
    "NUMPAD_UP", "NUMPAD_DOWN", "NUMPAD_HOME", "NUMPAD_END", "NUMPAD_PAGEUP", "NUMPAD_PAGEDOWN"])  # Num Lock off
def test_a_typed_text_ends_on_a_key_that_ends_a_thought(make_view, key):
    """Escape, Backspace and the keys that move through the list: what is typed next is a new text, and Space
    acts at once on the row the player moved to"""
    key = getattr(wx, f"WXK_{key}")
    acted = []
    view, _ = typing_view(make_view, on_action=lambda: acted.append(view.selected_row().title))
    type_text(view, "t")
    assert selected(view) == 3
    assert not press(view.list_ctrl, key)  # the key itself is left to the list
    type_text(view, "a")  # "a", not "ta", which no title starts with
    assert view.selected_row().title == "Alpha"
    press(view.list_ctrl, key)
    player_selects(view, 2)  # where an arrow key takes the player
    type_text(view, " ")
    assert acted == ["Bravo"]


def test_a_character_typed_with_altgr_is_text(make_view):
    """Ctrl and Alt both down is what AltGr is to Windows. wx hands over a letter outside the system code page
    that way (\u0119 and \u017c on a Polish layout, under code page 1252), and with no key code"""
    view = make_view()
    view.set_rows(rows(entry("alpha"), entry("zebra"), entry("\u017cuk")))
    press(view.list_ctrl, wx.WXK_NONE, wx.wxEVT_CHAR, ctrl=True, alt=True, unicode=ord("\u017c"))
    assert view.selected_row().title == "\u017buk"


def test_a_digit_sorts_only_with_neither_ctrl_nor_alt_held(make_view):
    view, _ = typing_view(make_view)
    for modifiers in ({"alt": True}, {"ctrl": True}, {"ctrl": True, "alt": True}):
        press(view.list_ctrl, ord("2"), wx.wxEVT_CHAR, **modifiers)
        assert headers(view) == ["Title \u25b2", "Year"] and not view.type_ahead.typing()
    press(view.list_ctrl, ord("2"), wx.wxEVT_CHAR, shift=True)  # some layouts (French) give digits only with Shift
    assert headers(view) == ["Title", "Year \u25b2"]
