"""MainFrame methods that need no main window: each runs on a stand-in for the frame, so no window is built, no
dialog opens and nothing is launched. Skips when wxPython is not installed or there is no display."""

from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")
if not wx.App.IsDisplayAvailable():
    pytest.skip("no display", allow_module_level=True)

import main_window  # noqa: E402
from main_window import MainFrame  # noqa: E402
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
