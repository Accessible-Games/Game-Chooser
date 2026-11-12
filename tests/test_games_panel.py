"""The games view both tabs are built from (GamesPanel), on a hidden frame: no window is shown and nothing is
fetched. Skips when wxPython is not installed or there is no display."""

import types

import pytest

wx = pytest.importorskip("wx")
if not wx.App.IsDisplayAvailable():
    pytest.skip("no display", allow_module_level=True)

from smart_list import Column  # noqa: E402

from catalog_view import build_rows  # noqa: E402
from games_panel import GamesPanel  # noqa: E402

SRC = [{"platform": "windows", "install": {"method": "zip"}, "url": "https://x/e.zip"}]


def entry(id, genre="Puzzle"):
    return {"id": id, "kind": "game", "title": id.title(), "genres": [genre], "developer": ["Dev"],
            "description": f"About {id.title()}.", "sources": SRC}


def rows(*entries):
    return build_rows(list(entries), set())


ALPHA, BRAVO, CHARLIE, DELTA = entry("alpha"), entry("bravo", "Action"), entry("charlie"), entry("delta")


class FakeManager:
    """Stands in for GameLibraryManager: the config in memory, and a count of the saves."""

    def __init__(self, saved):
        self.config = {"SavedState": {"catalog_last_selected": None, "catalog_last_search": "",
                                      "catalog_tree_selections": [], **saved}}
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

    def make(**saved):
        top = wx.Frame(None)  # never shown
        frames.append(top)
        owner = types.SimpleNamespace(library_manager=FakeManager(saved), initializing=False, dialog_active=False)
        return GamesPanel(top, owner, name="Games", state_prefix="catalog_",
                          columns=[Column(title="Title", model_field=lambda r: r.title, width=200)],
                          categories=[("genre", "Genre"), ("developer", "Developer")],
                          on_action=lambda: None, context_menu=lambda row: None)

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


def press_escape_in_tree(view):
    event = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
    event.SetKeyCode(wx.WXK_ESCAPE)
    view.tree_ctrl.GetEventHandler().ProcessEvent(event)


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
    view = make_view(catalog_last_selected="charlie")
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
