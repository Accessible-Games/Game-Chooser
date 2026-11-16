"""The Catalog tab's own part of its games view: what its action button does while an install job runs or waits
(it stays beside the progress area, so it can be pressed then), and how its columns sort. CatalogPanel on a frame
that is never shown, with stand-ins for the main window and the install service: no dialog opens and nothing is
queued, fetched or launched."""

from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")

from catalog_panel import CatalogPanel  # noqa: E402
from engine import messages, steam  # noqa: E402
from tests.test_games_panel import (FakeFrame, Saves, app, as_shown, click_header, entry, headers, press,  # noqa: E402,F401
                                    saved, titles)  # (app is a fixture)


class Frame(FakeFrame):
    """What CatalogPanel uses of MainFrame, recorded"""

    def __init__(self, manager):
        super().__init__(manager)
        self.confirmed, self.launched, self.changes = [], [], 0
        self.choice = SimpleNamespace(library_id="lib", library_path="C:/Games", extras=("manual",))
        self.picks, self.sent, self.asked, self.notified = [], [], [], []
        self.pick = lambda places: None
        self.steam = "sent"
        self.yes = True

    def choose_place(self, title, places):
        self.picks.append((title, [p.label for p in places]))
        return self.pick(places)

    def steam_send(self, action, app_id):
        self.sent.append((action, app_id))
        return self.steam

    def notify(self, text):
        self.notified.append(text)

    def _ask(self, text, caption, style=None):
        self.asked.append((text, caption))
        return self.yes

    def active_tab(self):
        return "catalog"

    def confirm_install(self, plan):
        self.confirmed.append(plan.catalog_id)
        return self.choice

    def games_changed(self):
        self.changes += 1

    def focus_active_list(self):
        pass

    def launch_game(self, game):
        self.launched.append(game.title)


class Service:
    """What CatalogPanel uses of InstallService. `jobs` maps a catalog id to its job's state."""

    def __init__(self, entries, installed=()):
        self.index_state = SimpleNamespace(newer_app_needed=False, error=None, entries=entries)
        self.jobs, self.submitted, self.added, self.pending = {}, [], [], []
        self.queue = SimpleNamespace(
            job_for=lambda cid: SimpleNamespace(state=self.jobs[cid]) if cid in self.jobs else None)
        self.manager = SimpleNamespace(catalog_game=lambda cid: SimpleNamespace(title=cid.title()),
                                       add_catalog_game=self.added.append,
                                       save_or_keep=lambda change, what: change() or True)  # saved
        self.entries = {e["id"]: e for e in entries}
        self.installed_ids = lambda: set(installed)

    def plan(self, entry_id):
        return SimpleNamespace(action="install", catalog_id=entry_id)

    def submit(self, *args):
        self.submitted.append(args)

    def steam_sent(self, app_id):
        self.pending.append(app_id)


@pytest.fixture
def make_panel(app, isolated_manager_factory, monkeypatch):
    monkeypatch.setattr("catalog_panel.current_platform", lambda: "windows")  # the entries have a windows source only
    frames = []

    def make(installed=(), entries=None, saved=None):
        top = wx.Frame(None)  # never shown
        frames.append(top)
        manager = isolated_manager_factory(config={"libraries": [], "SavedState": saved or {}})
        manager.save_config = Saves()
        frame = Frame(manager)
        service = Service(entries or [entry("alpha"), entry("bravo")], installed)
        return CatalogPanel(top, frame, service)

    yield make
    for top in frames:
        top.Destroy()


def press_action_button(panel, title):
    """Select the game's row, then press the button beside the running job's progress"""
    view = panel.view
    view.set_busy(True)
    view.select_row(next(row for row in view.displayed if row.title == title))
    button = view.action_btn
    assert button.IsShown() and button.IsEnabled()
    button.GetEventHandler().ProcessEvent(wx.CommandEvent(wx.wxEVT_BUTTON, button.GetId()))


@pytest.mark.parametrize("state, text", [("running", messages.JOB_ALREADY_RUNNING),
                                         ("queued", messages.JOB_ALREADY_WAITING)])
def test_install_on_a_game_that_already_has_a_job_says_so_and_queues_nothing(make_panel, state, text):
    panel = make_panel()
    panel.service.jobs = {"alpha": state}
    press_action_button(panel, "Alpha")
    assert panel.frame.told == [(text.format(title="Alpha"), "Catalog")]
    assert panel.frame.confirmed == [] and panel.service.submitted == [] and panel.frame.changes == 0
    assert panel.view.action_btn.GetLabel() == "Install Alpha"  # the label stays; pressing it explains


def test_install_on_another_game_while_a_job_runs_queues_it(make_panel):
    panel = make_panel()
    panel.service.jobs = {"alpha": "running"}
    press_action_button(panel, "Bravo")
    assert panel.frame.told == [] and panel.frame.confirmed == ["bravo"]
    assert panel.service.submitted == [("install", "bravo", "lib", "C:/Games", ("manual",))]
    assert panel.frame.changes == 1


def test_launch_on_an_installed_game_is_the_same_with_or_without_a_job_for_it(make_panel):
    """An update, repair or uninstall of an installed game: its row reads Launch, and Launch is the Library's
    launch, which no job holds back on either tab"""
    panel = make_panel(installed={"alpha"})
    press_action_button(panel, "Alpha")
    panel.service.jobs = {"alpha": "running"}
    press_action_button(panel, "Alpha")
    assert panel.frame.launched == ["Alpha", "Alpha"] and panel.frame.told == [] and panel.service.submitted == []


def priced(id, genre, developer, tag, **pricing):
    return {**entry(id, genre), "developer": [developer], "tags": [tag], "pricing": pricing}


# Every column, and Price both ways, puts these four in an order of its own, so a column that sorts nothing, or
# by another column's key, shows. By title: Alpha, Bravo, Charlie, Delta. Alpha and Delta tie on the genre.
PRICED = [priced("alpha", "Action", "Cy", "Zombies", model="one-time", price=5, currency="USD"),
          priced("bravo", "Puzzle", "Ann", "Horror", model="one-time", price=10, currency="USD"),
          priced("charlie", "Board", "Bob", "Arcade", model="free"),
          priced("delta", "Action", "Dee", "Maze", model="one-time", price=7, currency="USD")]


def test_the_catalog_list_sorts_by_each_column_and_remembers_it(make_panel):
    view = make_panel(entries=PRICED).view
    assert titles(view) == ["Alpha", "Bravo", "Charlie", "Delta"]
    assert headers(view) == as_shown(view, ["Title ▲", "Developer", "Genre", "Price", "Details"])
    click_header(view, 3)  # Price: free first, then by amount ("Buy once, $10" is before "Buy once, $5" as text)
    assert titles(view) == ["Charlie", "Alpha", "Delta", "Bravo"] and headers(view)[3] == as_shown(view, "Price ▲")
    assert (saved(view)["catalog_sort_column"], saved(view)["catalog_sort_ascending"]) == (3, True)
    click_header(view, 3)
    assert titles(view) == ["Bravo", "Delta", "Alpha", "Charlie"] and headers(view)[3] == as_shown(view, "Price ▼")
    for digit, column, order in (("3", "Genre", ["Alpha", "Delta", "Charlie", "Bravo"]),  # a tie: in title order
                                 ("2", "Developer", ["Bravo", "Charlie", "Alpha", "Delta"]),
                                 ("5", "Details", ["Charlie", "Bravo", "Delta", "Alpha"])):
        press(view.list_ctrl, ord(digit), wx.wxEVT_CHAR)  # a digit key
        assert titles(view) == order and headers(view)[int(digit) - 1] == as_shown(view, f"{column} ▲")
    assert saved(view)["catalog_sort_column"] == 4


def test_the_catalog_list_starts_in_the_saved_sort(make_panel):
    view = make_panel(entries=PRICED, saved={"catalog_sort_column": 3, "catalog_sort_ascending": False}).view
    assert titles(view) == ["Bravo", "Delta", "Alpha", "Charlie"] and headers(view)[3] == as_shown(view, "Price ▼")



def test_an_ampersand_in_a_title_or_a_link_label_is_shown_not_taken_as_an_access_key(make_panel):
    tom = {**entry("tom"), "title": "Tom & Jerry", "links": [{"label": "Manual & FAQ", "url": "https://m.example"}]}
    panel = make_panel(entries=[tom])
    view = panel.view
    view.select_row(view.displayed[0])
    assert view.action_btn.GetLabelText() == "Install Tom & Jerry"
    menu = panel.context_menu(view.selected_row())
    assert [item.GetItemLabelText() for item in menu.GetMenuItems()] == ["Install Tom & Jerry", "Open Manual & FAQ"]
    menu.Destroy()


# ---- Get: a row that is got from a store ------------------------------------------------------------------------

STEAM = {"store": "steam", "app_id": 1234560, "platforms": ["windows"]}
STEAM_DEMO = dict(STEAM, demo_app_id=1234570)
ITCH = {"store": "itch.io", "url": "https://dev.itch.io/game", "platforms": ["windows"]}
PAGE = "https://store.steampowered.com/app/1234560/"


def store_entry(id, *stores, download=True):
    e = dict(entry(id), stores=list(stores))
    if not download:
        del e["sources"]
    return e


@pytest.fixture
def opened(monkeypatch):
    pages = []
    monkeypatch.setattr("catalog_panel.webbrowser.open", pages.append)
    return pages


def test_one_store_goes_straight_to_its_page(make_panel, opened):
    panel = make_panel(entries=[store_entry("alpha", ITCH, download=False)])
    press_action_button(panel, "Alpha")
    assert panel.view.action_btn.GetLabel() == "Get Alpha"
    assert opened == ["https://dev.itch.io/game"] and panel.frame.picks == [] and panel.frame.sent == []


def test_steam_alone_sends_the_get_address(make_panel, opened):
    panel = make_panel(entries=[store_entry("alpha", STEAM, download=False)])
    press_action_button(panel, "Alpha")
    assert panel.frame.sent == [(steam.GET_ACTION, 1234560)] and panel.frame.picks == [] and opened == []


def test_more_than_one_place_asks_and_goes_where_the_player_chose(make_panel, opened):
    panel = make_panel(entries=[store_entry("alpha", STEAM_DEMO, ITCH)])
    labels = ["Install with Game Chooser", "Steam", "Steam demo", "itch.io"]
    for chosen in (None, 0, 1, 2, 3):
        panel.frame.pick = lambda places, i=chosen: None if i is None else places[i]
        press_action_button(panel, "Alpha")
    assert panel.frame.picks == [("Alpha", labels)] * 5
    assert panel.frame.confirmed == ["alpha"]                       # only choice 0 opens the install confirmation
    assert panel.frame.sent == [(steam.GET_ACTION, 1234560), (steam.GET_DEMO_ACTION, 1234570)]
    assert opened == ["https://dev.itch.io/game"]


def test_steam_opens_the_games_page_and_steam_demo_installs_the_demo(make_panel, opened):
    panel = make_panel(entries=[store_entry("alpha", STEAM_DEMO, download=False)])
    for chosen in (0, 1):                                            # "Steam", then "Steam demo"
        panel.frame.pick = lambda places, i=chosen: places[i]
        press_action_button(panel, "Alpha")
    assert panel.frame.sent == [("store", 1234560), ("install", 1234570)]


@pytest.mark.parametrize("platform, why", [("windows", messages.STEAM_NOT_INSTALLED),
                                           ("macos", messages.STEAM_NEEDS_WINDOWS)])
def test_steam_missing_offers_the_store_page_of_the_full_game(make_panel, opened, monkeypatch, platform, why):
    demo_mac = dict(STEAM_DEMO, platforms=["windows", "macos"])
    panel = make_panel(entries=[store_entry("alpha", demo_mac)])     # its windows download is the app's place
    monkeypatch.setattr("catalog_panel.current_platform", lambda: platform)
    panel.refresh_rows()
    panel.frame.steam = "absent"
    panel.frame.pick = lambda places: places[-1]                     # the demo
    press_action_button(panel, "Alpha")
    assert panel.frame.asked == [(messages.STEAM_OPEN_PAGE_INSTEAD.format(why=why), "Steam")]
    assert opened == [PAGE]
    panel.frame.yes = False
    press_action_button(panel, "Alpha")
    assert opened == [PAGE]                                          # No opens nothing


def test_a_refused_address_opens_nothing_more(make_panel, opened):
    panel = make_panel(entries=[store_entry("alpha", STEAM, download=False)])
    panel.frame.steam = "failed"                                     # steam_send has told the player
    press_action_button(panel, "Alpha")
    assert panel.frame.asked == [] and opened == []


def test_a_get_sent_to_steam_is_pending_and_a_refused_one_is_not(make_panel, opened):
    panel = make_panel(entries=[store_entry("alpha", STEAM, download=False)])
    for answer in ("sent", "failed", "absent"):
        panel.frame.steam = answer
        press_action_button(panel, "Alpha")
    assert panel.service.pending == [1234560]


def test_a_random_install_of_a_game_with_a_store_opens_the_install_confirmation(make_panel, opened):
    panel = make_panel(entries=[store_entry("alpha", ITCH)])
    panel._confirm_install(panel.view.displayed[0])                  # what Ctrl+Shift+R runs on its pick
    assert panel.frame.confirmed == ["alpha"] and panel.frame.picks == []


def test_the_apps_place_for_a_web_game_with_a_store_adds_it_and_installs_nothing(make_panel, opened):
    web = {"platform": "web", "install": {"method": "url"}, "url": "https://play.example/web"}
    panel = make_panel(entries=[dict(store_entry("web", ITCH), sources=[web])])
    panel.frame.pick = lambda places: places[0]
    press_action_button(panel, "Web")
    assert panel.frame.picks == [("Web", ["Add to Game Chooser", "itch.io"])]
    assert [(g["title"], g["launch_path"]) for g in panel.service.added] == [("Web", "https://play.example/web")]
    assert panel.frame.notified == [messages.ADDED.format(title="Web")]
    assert panel.frame.confirmed == [] and panel.service.submitted == [] and opened == []


def test_a_random_install_asks_install_now_not_get_now(make_panel, monkeypatch):
    """Its row reads Get, but Yes opens the install confirmation: the question names that"""
    asked = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, caption, style: (asked.append((text, caption)), wx.NO)[1])
    panel = make_panel(entries=[store_entry("alpha", ITCH)])
    panel.on_random_install()
    assert asked == [("Your game for today is...\n\nAlpha\n\nInstall now?", "Random Game to Install")]
