"""MainFrame methods that need no main window: each runs on a stand-in for the frame, so no window is built, no
dialog opens and nothing is launched. Skips when wxPython is not installed or there is no display."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")
if not wx.App.IsDisplayAvailable():
    pytest.skip("no display", allow_module_level=True)

import main_window  # noqa: E402
from main_window import MainFrame  # noqa: E402
from engine import messages  # noqa: E402
from engine.entries import Place  # noqa: E402
from models import Game  # noqa: E402
from tests.test_games_panel import app, key_event as key_down  # noqa: E402,F401  (app is a fixture)


def test_the_library_lists_own_key_is_del(app):
    """Ctrl+E is the frame's shortcut; AltGr+E (Ctrl and Alt both down) is a letter on several layouts (é, ę, €)"""
    done = []
    frame = SimpleNamespace(on_delete_game=lambda event: done.append("delete"))
    assert MainFrame.on_list_key(frame, key_down(wx.WXK_DELETE)) and done == ["delete"]
    for event in (key_down(ord("E"), ctrl=True), key_down(ord("E"), ctrl=True, alt=True), key_down(ord("E"))):
        assert not MainFrame.on_list_key(frame, event)
    assert done == ["delete"]


# ---- message boxes shown while a game is held -------------------------------------------------------------------

@pytest.fixture
def frame(app, monkeypatch, tmp_path):
    """A stand-in for the main window on Windows, with one selected game whose file exists. No box opens:
    `frame.boxes` lists each as (caption, style, parent, whether modal_dialog() was open)."""
    exe = tmp_path / "game.exe"
    exe.write_bytes(b"MZ")
    game = Game(title="Alpha", platforms=["Windows"], launch_path=str(exe))
    frame = SimpleNamespace(
        game=game, boxes=[], dialog_active=False, launch_observer=None, _run_deferred=lambda: None,
        active_tab=lambda: "library", selected_game=lambda: game,
        library_manager=SimpleNamespace(get_full_path=lambda game: game.launch_path, config={"libraries": []}),
        service=SimpleNamespace(record_for=lambda game: None))
    frame.modal_dialog = lambda: MainFrame.modal_dialog(frame)
    frame._tell = lambda *args: MainFrame._tell(frame, *args)
    monkeypatch.setattr(wx, "MessageBox", lambda text, caption="", style=0, parent=None:
                        frame.boxes.append((caption, style, parent, frame.dialog_active)))
    monkeypatch.setattr(main_window.platform, "system", lambda: "Windows")
    monkeypatch.setattr(main_window, "catalog_platform", lambda: "windows")
    return frame


def refuse(*args, **kwargs):
    raise OSError("it can't be started (says the test)")


def folder_is_gone(frame, monkeypatch):
    frame.game.launch_path += ".gone"
    MainFrame.on_open_folder(frame, None)


def folder_cannot_be_opened(frame, monkeypatch):
    monkeypatch.setattr(main_window.subprocess, "Popen", refuse)
    MainFrame.on_open_folder(frame, None)


def game_is_for_another_system(frame, monkeypatch):
    frame.game.platforms = ["macOS"]
    MainFrame.launch_game(frame, frame.game)


def game_cannot_be_started(frame, monkeypatch):
    monkeypatch.setattr(main_window, "start_launch_file", refuse)
    MainFrame.launch_game(frame, frame.game)


@pytest.mark.parametrize("caption, step", [("Folder Not Found", folder_is_gone), ("Error", folder_cannot_be_opened),
                                           ("Uh-oh", game_is_for_another_system),
                                           ("Launch Error", game_cannot_be_started)])
def test_a_message_box_shown_while_a_game_is_held_is_inside_modal_dialog(frame, monkeypatch, caption, step):
    """Modal safety: wx runs CallAfter events under the box, and an index take or a job result arriving there
    would replace the game the handler holds"""
    step(frame, monkeypatch)
    assert frame.boxes == [(caption, wx.OK | wx.ICON_ERROR, frame, True)] and not frame.dialog_active



@pytest.mark.parametrize("saves", [True, False])
def test_a_games_list_that_cannot_be_saved_is_told_and_the_change_still_shows(isolated_manager, saves):
    """Add, Edit, Delete and Locate: games.json beside the app may be read-only, or the disk full"""
    shown, told = [], []
    if not saves:
        isolated_manager.save_games = lambda: (_ for _ in ()).throw(OSError("read-only"))
    frame = SimpleNamespace(library_manager=isolated_manager, games_changed=lambda: shown.append(True),
                            _tell=lambda text, caption: told.append(text))
    MainFrame._save_games(frame)
    assert shown == [True]
    assert told == ([] if saves else [messages.GAMES_NOT_SAVED.format(path=isolated_manager.games_file)])



@pytest.mark.parametrize("name, accepted", [("game.exe", True), ("menu.hta", False)])
def test_locate_accepts_only_the_files_add_game_accepts(frame, monkeypatch, tmp_path, name, accepted):
    picked = tmp_path / "picked" / name
    picked.parent.mkdir()
    picked.write_bytes(b"MZ")
    gone = frame.game.launch_path = frame.game.launch_path + ".gone"
    frame.library_manager.library_reachable = lambda library_id: True
    frame.service.entry_for = lambda game: None
    frame._live_game = lambda game: game
    frame._save_games = lambda: frame.boxes.append("saved")
    monkeypatch.setattr(wx, "FileDialog", lambda *args, **kwargs: nullcontext(
        SimpleNamespace(ShowModal=lambda: wx.ID_OK, GetPath=lambda: str(picked))))
    MainFrame.launch_game(frame, frame.game)
    assert frame.boxes[0][0] == messages.LAUNCH_NOT_FOUND_TITLE
    if accepted:
        assert frame.game.launch_path == str(picked) and frame.boxes[1:] == ["saved"]
    else:
        assert frame.game.launch_path == gone and [box[0] for box in frame.boxes[1:]] == ["Invalid Executable"]


def test_steam_send_checks_that_steam_is_there_first(frame, monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(main_window.steam, "send", lambda action, app_id: sent.append((action, app_id)))
    monkeypatch.setattr(main_window.steam, "steam_folder", lambda: None)
    assert MainFrame.steam_send(frame, "run", 1234560) == "absent" and sent == [] and frame.boxes == []
    monkeypatch.setattr(main_window.steam, "steam_folder", lambda: tmp_path)
    assert MainFrame.steam_send(frame, "run", 1234560) == "sent" and sent == [("run", 1234560)]
    monkeypatch.setattr(main_window.steam, "send", refuse)
    assert MainFrame.steam_send(frame, "uninstall", 1234560) == "failed"
    assert [(caption, inside) for caption, _, _, inside in frame.boxes] == [("Steam", True)]


def test_the_choice_of_places_is_a_list_inside_modal_dialog(frame, monkeypatch):
    seen = []

    class Choice:
        def __init__(self, parent, message, caption, choices):
            seen.append((message, caption, choices, frame.dialog_active))
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def ShowModal(self): return self.answer
        def GetSelection(self): return 1

    monkeypatch.setattr(main_window.wx, "SingleChoiceDialog", Choice)
    places = [Place("app", "Install with Game Chooser"), Place("store", "itch.io")]
    Choice.answer = wx.ID_OK
    assert MainFrame.choose_place(frame, "Alpha", places) is places[1]
    Choice.answer = wx.ID_CANCEL
    assert MainFrame.choose_place(frame, "Alpha", places) is None
    assert seen[0] == ("Where do you want to get Alpha?", "Get Game", ["Install with Game Chooser", "itch.io"], True)
