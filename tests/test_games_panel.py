"""The games view both tabs are built from (GamesPanel), on a hidden frame: no window is shown and nothing is
fetched. Skips when wxPython is not installed or there is no display."""

import types

import pytest

wx = pytest.importorskip("wx")
if not wx.App.IsDisplayAvailable():
    pytest.skip("no display", allow_module_level=True)

from smart_list import Column  # noqa: E402

from catalog_view import build_rows, library_rows  # noqa: E402
from games_panel import GamesPanel  # noqa: E402
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


class FakeManager:
    """Stands in for GameLibraryManager: the config in memory, and a count of the saves."""

    def __init__(self, saved):
        self.config = {"SavedState": {"catalog_last_selected": None, "catalog_last_search": "",
                                      "catalog_tree_selections": [], "catalog_column_widths": None,
                                      "catalog_sort_column": 0, "catalog_sort_ascending": True, **saved}}
        self.saves = 0
        self.fail = False

    def save_config(self):
        if self.fail:
            raise OSError("the disk is full")
        self.saves += 1


@pytest.fixture(scope="module")
def app():
    yield wx.GetApp() or wx.App(False)


@pytest.fixture
def make_view(app):
    frames = []

    def make(saved=None, **settings):
        top = wx.Frame(None)  # never shown
        frames.append(top)
        owner = types.SimpleNamespace(library_manager=FakeManager(saved or {}), initializing=False,
                                      dialog_active=False)
        settings = {"idle_label": "Install", "progress_replaces_action": True, "on_action": lambda: None,
                    "context_menu": lambda row: None, **settings}
        return GamesPanel(top, owner, name="Games", state_prefix="catalog_",
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


def press(window, key_code, event_type=wx.wxEVT_KEY_DOWN):
    """True when a handler took the key"""
    event = wx.KeyEvent(event_type)
    event.SetKeyCode(key_code)
    return window.GetEventHandler().ProcessEvent(event)


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
    view = make_view({"catalog_last_selected": "charlie"})
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
    assert titles(view) == ["Alpha"] and saved(view)["catalog_tree_selections"] == ["Genre/Puzzle"]
    view.set_rows(rows(ALPHA, BRAVO, entry("echo", "Strategy")))
    assert list(tree_nodes(view)["Genre"]) == ["Action", "Puzzle", "Strategy"]
    assert view._selected_paths() == ["Genre/Puzzle"] and titles(view) == ["Alpha"]
    view.set_rows(rows(ALPHA))  # a value with no game left goes
    assert list(tree_nodes(view)["Genre"]) == ["Puzzle"] and view._selected_paths() == ["Genre/Puzzle"]


def test_when_the_selected_game_is_gone_the_selection_keeps_its_row_number(make_view):
    view = make_view()
    view.set_rows(rows(ALPHA, BRAVO, CHARLIE))
    player_selects(view, 1)
    assert saved(view)["catalog_last_selected"] == "bravo"
    view.set_rows(rows(ALPHA, CHARLIE))  # Bravo was deleted: Charlie is in its row now
    assert (selected(view), focused(view), view.action_btn.GetLabel()) == (1, 1, "Install Charlie")
    assert saved(view)["catalog_last_selected"] == "charlie"  # and is what the next start selects
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
    assert saved(view)["catalog_last_selected"] == "delta"


def test_a_refresh_that_changes_nothing_is_quiet(make_view):
    """Background refreshes run often, also inside a dialog's event loop: no selection event, no config save"""
    view = make_view()
    view.set_rows(rows(ALPHA, BRAVO, CHARLIE))
    player_selects(view, 1)
    events = []
    view.list_ctrl.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda event: (events.append(event.GetIndex()), event.Skip()))
    saves = view.frame.library_manager.saves
    view.set_rows(rows(ALPHA, BRAVO, CHARLIE))
    view.apply_filters()
    assert events == [] and view.frame.library_manager.saves == saves
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
    assert saved(view)["catalog_last_selected"] == "bravo" and view.frame.library_manager.saves == 1


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
    view.frame.library_manager.fail = True
    view.tree_ctrl.SelectItem(tree_nodes(view)["Genre"]["Action"], True)
    assert titles(view) == ["Bravo"] and view.action_btn.GetLabel() == "Install Bravo"
    assert saved(view)["catalog_tree_selections"] == ["Genre/Action"]  # kept for the next save that works
    view.set_rows(rows(ALPHA, BRAVO, CHARLIE))
    press_escape_in_tree(view)
    player_selects(view, 2)
    assert view.action_btn.GetLabel() == "Install Charlie" and saved(view)["catalog_last_selected"] == "charlie"


def test_escape_in_the_tree_clears_every_filter(make_view):
    view = make_view()
    view.set_rows(rows(ALPHA, BRAVO))
    view.tree_ctrl.SelectItem(tree_nodes(view)["Genre"]["Action"], True)
    assert titles(view) == ["Bravo"]
    press_escape_in_tree(view)
    assert titles(view) == ["Alpha", "Bravo"] and saved(view)["catalog_tree_selections"] == []
    press_escape_in_tree(view)  # nothing selected: wx fires no event, the filters are applied all the same
    assert titles(view) == ["Alpha", "Bravo"]


# ---- what differs between the tabs: each is a setting of the one view ------------------------------------------

def test_saved_search_and_sort_are_read_when_the_view_is_built(make_view):
    """The list is filled before the main window loads the rest of its saved state"""
    view = make_view({"catalog_last_search": "a", "catalog_sort_column": 1, "catalog_sort_ascending": False},
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
    assert (saved(view)["catalog_sort_column"], saved(view)["catalog_sort_ascending"]) == (1, False)
    press(view.list_ctrl, ord("1"), wx.wxEVT_CHAR)  # a digit key sorts by that column
    assert headers(view) == ["Title ▲", "Year"] and saved(view)["catalog_sort_column"] == 0
    view.frame.library_manager.fail = True  # a failed save doesn't stop the sort
    press(view.list_ctrl, ord("2"), wx.wxEVT_CHAR)
    assert titles(view) == ["Cat", "Bee", "Ant"] and view.action_btn.GetLabel() == "Install Ant"


def test_rows_that_tie_in_the_sorted_column_are_in_title_order(make_view):
    view = make_view(sort_keys=SORT_KEYS)
    view.set_rows(rows(entry("zed", year=2000), entry("amy", year=2000), entry("old", year=1990)))
    click_header(view, 1)
    assert titles(view) == ["Old", "Amy", "Zed"]
    click_header(view, 1)
    assert titles(view) == ["Amy", "Zed", "Old"]


def test_a_sort_key_whose_values_changed_sorts_again_on_apply_filters(make_view):
    """The Library's Status column: the statuses arrive later, from a worker thread"""
    status = {}
    view = make_view(sort_keys=[lambda r: r.title.lower(), lambda r: status.get(r.id, "")])
    view.set_rows(rows(ANT, BEE, CAT))
    click_header(view, 1)
    assert titles(view) == ["Ant", "Bee", "Cat"] and selected(view) == 0
    status.update(ant="Ready", bee="Executable not found", cat="Ready")
    view.apply_filters()
    assert titles(view) == ["Bee", "Ant", "Cat"]
    assert (selected(view), focused(view), view.action_btn.GetLabel()) == (1, 1, "Install Ant")


def test_a_view_without_sort_keys_stays_sorted_by_title(make_view):
    view = make_view()
    view.set_rows(rows(CAT, ANT, BEE))
    click_header(view, 1)
    press(view.list_ctrl, ord("2"), wx.wxEVT_CHAR)
    assert titles(view) == ["Ant", "Bee", "Cat"] and headers(view) == ["Title", "Year"]


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
    assert saved(view)["catalog_tree_selections"] == []
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


def test_progress_takes_the_buttons_place_or_shows_beside_it(make_view):
    replacing, beside = make_view(), make_view(progress_replaces_action=False, idle_label="Launch")
    assert (replacing.action_btn.GetLabel(), beside.action_btn.GetLabel()) == ("Install", "Launch")
    for view in (replacing, beside):
        view.set_busy(True)
    assert (replacing.action_btn.IsShown(), replacing.progress_area.IsShown()) == (False, True)
    assert (beside.action_btn.IsShown(), beside.progress_area.IsShown()) == (True, True)
    for view in (replacing, beside):
        view.set_busy(False)
        assert (view.action_btn.IsShown(), view.progress_area.IsShown()) == (True, False)


def test_library_rows_launch_describe_and_are_remembered_by_id(make_view):
    games = [Game(title="Hand", launch_path="C:/games/hand.exe", platforms=["Windows"]),
             Game(title="Alpha", launch_path="Alpha/alpha.exe", library_id="lib", catalog_id="alpha")]
    view = make_view(idle_label="Launch")
    view.set_rows(library_rows(games, lambda game: ALPHA if game.catalog_id else None))
    assert view.selected_row().game is games[1] and view.action_btn.GetLabel() == "Launch Alpha"
    assert view.description.GetValue().startswith("About Alpha.")
    player_selects(view, 1)
    assert (view.action_btn.GetLabel(), view.description.GetValue()) == ("Launch Hand", "")
    assert saved(view)["catalog_last_selected"] == "C:/games/hand.exe"
    view.set_rows([])
    assert (view.action_btn.GetLabel(), view.action_btn.IsEnabled()) == ("Launch", False)


def test_column_widths_are_restored_and_saved(make_view):
    view = make_view({"catalog_column_widths": [123, 45]})
    assert [view.list_ctrl.GetColumnWidth(i) for i in range(2)] == [123, 45]
    view.list_ctrl.SetColumnWidth(0, 222)
    view.save_state()
    assert saved(view)["catalog_column_widths"] == [222, 45]


def test_search_and_tree_filters_are_saved_only_while_there_are_rows(make_view):
    """With no rows (the catalog offline with no saved copy) the last filters wait for the games"""
    view = make_view({"catalog_last_search": "bra", "catalog_tree_selections": ["Genre/Action"]})
    view.set_rows([])
    view.save_state()
    assert (saved(view)["catalog_last_search"], saved(view)["catalog_tree_selections"]) == ("bra", ["Genre/Action"])
    view.set_rows(rows(ALPHA, BRAVO))  # the tree has values now: the saved selections come back
    assert view._selected_paths() == ["Genre/Action"] and titles(view) == ["Bravo"]
    view.search_combo.SetValue("b")
    view.save_state()
    assert saved(view)["catalog_last_search"] == "b"


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
