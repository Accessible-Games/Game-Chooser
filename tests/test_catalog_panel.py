"""What the Catalog tab's action button does while an install job runs or waits (it stays beside the progress
area, so it can be pressed then). CatalogPanel on a frame that is never shown, with stand-ins for the main window
and the install service: no dialog opens and nothing is queued, fetched or launched."""

from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")

from catalog_panel import CatalogPanel  # noqa: E402
from engine import messages  # noqa: E402
from tests.test_games_panel import FakeFrame, Saves, app, entry  # noqa: E402,F401  (app is a fixture)


class Frame(FakeFrame):
    """What CatalogPanel uses of MainFrame, recorded"""

    def __init__(self, manager):
        super().__init__(manager)
        self.confirmed, self.launched, self.changes = [], [], 0
        self.choice = SimpleNamespace(library_id="lib", library_path="C:/Games", extras=("manual",))

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
        self.jobs, self.submitted = {}, []
        self.queue = SimpleNamespace(
            job_for=lambda cid: SimpleNamespace(state=self.jobs[cid]) if cid in self.jobs else None)
        self.manager = SimpleNamespace(catalog_game=lambda cid: SimpleNamespace(title=cid.title()))
        self.visible_games = lambda: entries
        self.installed_ids = lambda: set(installed)

    def plan(self, entry_id):
        return SimpleNamespace(action="install", catalog_id=entry_id)

    def submit(self, *args):
        self.submitted.append(args)


@pytest.fixture
def make_panel(app, isolated_manager_factory):
    frames = []

    def make(installed=()):
        top = wx.Frame(None)  # never shown
        frames.append(top)
        manager = isolated_manager_factory(config={"libraries": []})
        manager.save_config = Saves()
        frame = Frame(manager)
        service = Service([entry("alpha"), entry("bravo")], installed)
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
