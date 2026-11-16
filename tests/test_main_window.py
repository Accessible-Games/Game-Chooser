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
from engine import messages, steam  # noqa: E402
from engine.entries import Place  # noqa: E402
from models import Game  # noqa: E402
from engine import steam_scan  # noqa: E402
from engine.steam_scan import Scan  # noqa: E402
from steam_games import ScanGate  # noqa: E402
from tests.test_games_panel import app, key_event as key_down  # noqa: E402,F401  (app is a fixture)
from tests.test_steam_games import found  # noqa: E402


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


# ---- when the Steam scan runs -----------------------------------------------------------------------------------

@pytest.fixture
def scans(frame, monkeypatch):
    """The stand-in frame with the scan's parts; `scans` lists each background start"""
    started = []
    monkeypatch.setattr(main_window, "run_in_background",
                        lambda work, done, name, default=None: started.append((work, done, name, default)))
    frame._steam_gate = ScanGate()
    frame.taken, frame.changes = [], []
    frame.service.steam_pending = {}
    frame.service.take_steam_scan = lambda scan: frame.taken.append(scan) or True
    frame.games_changed = lambda: frame.changes.append(1)
    frame.run_when_no_dialog = lambda fn, *args: fn(*args)
    for name in ("start_steam_scan", "_run_steam_scan", "_steam_scanned", "_take_steam_scan"):
        setattr(frame, name, lambda *args, _n=name: getattr(MainFrame, _n)(frame, *args))
    return started


def test_one_scan_runs_at_a_time_and_one_more_follows(frame, scans):
    for _ in range(3):
        MainFrame.start_steam_scan(frame)
    assert len(scans) == 1
    work, done, name, default = scans[0]
    assert (work, name, default) == (steam_scan.scan, "steam-scan", Scan("unknown"))
    done(found(5054570))
    assert frame.taken == [found(5054570)] and frame.changes == [1]
    assert len(scans) == 2                                   # the one more that was asked for
    scans[1][1](Scan("unknown"))
    assert len(scans) == 2


def test_a_scan_that_changes_nothing_refreshes_nothing(frame, scans):
    frame.service.take_steam_scan = lambda scan: False
    MainFrame.start_steam_scan(frame)
    scans[0][1](Scan("unknown"))
    assert frame.changes == []


def test_the_window_coming_forward_scans_only_while_an_id_is_pending(frame, scans):
    skipped = []
    event = lambda active: SimpleNamespace(GetActive=lambda: active, Skip=lambda: skipped.append(1))
    MainFrame.on_activate(frame, event(True))                # nothing pending
    frame.service.steam_pending = {5054570: False}
    MainFrame.on_activate(frame, event(False))               # the window lost the foreground
    assert scans == []
    MainFrame.on_activate(frame, event(True))
    assert len(scans) == 1 and len(skipped) == 3


def test_f5_scans_on_either_tab(frame, scans):
    frame.start_index_fetch = lambda: None
    for tab in ("library", "catalog"):
        frame.active_tab = lambda tab=tab: tab
        MainFrame.on_refresh(frame, None)
        scans[-1][1](Scan("unknown"))                        # that scan ends
    assert len(scans) == 2


# ---- a Steam row ------------------------------------------------------------------------------------------------

@pytest.fixture
def steam_frame(frame, monkeypatch):
    frame.game = Game(title="Dugout Dice", catalog_id="dice", platforms=[], steam_app_id=5054570)
    frame.selected_game = lambda: frame.game
    frame.sent, frame.scans, frame.pending, frame.answer = [], [], [], "sent"
    frame.steam_send = lambda action, app_id: frame.sent.append((action, app_id)) or frame.answer
    frame.start_steam_scan = lambda: frame.scans.append(1)
    frame.service.steam_sent = frame.pending.append
    frame.focus_active_list = lambda: None
    monkeypatch.setattr(main_window, "start_launch_file", refuse)      # a Steam row has no file to start
    return frame


def test_a_steam_row_launches_through_steam_and_does_not_minimize(steam_frame):
    MainFrame.launch_game(steam_frame, steam_frame.game)               # its platforms are empty: not asked
    assert steam_frame.sent == [(steam.LAUNCH_ACTION, 5054570)]
    assert steam_frame.boxes == [] and steam_frame.pending == [] and steam_frame.scans == []


def test_a_steam_row_is_uninstalled_through_steam_with_no_dialog_of_the_apps(steam_frame):
    MainFrame.uninstall_game(steam_frame, steam_frame.game)
    assert steam_frame.sent == [("uninstall", 5054570)] and steam_frame.pending == [5054570]
    assert steam_frame.boxes == []


@pytest.mark.parametrize("act", [MainFrame.launch_game, MainFrame.uninstall_game])
def test_steam_gone_says_so_sends_nothing_more_and_scans(steam_frame, act):
    steam_frame.answer = "absent"
    act(steam_frame, steam_frame.game)
    assert [(caption, inside) for caption, _, _, inside in steam_frame.boxes] == [("Steam", True)]
    assert steam_frame.scans == [1] and steam_frame.pending == []


def test_a_refused_uninstall_address_is_not_pending(steam_frame):
    steam_frame.answer = "failed"                                      # steam_send has told the player
    MainFrame.uninstall_game(steam_frame, steam_frame.game)
    assert steam_frame.pending == [] and steam_frame.scans == []


def test_open_folder_on_a_steam_row_opens_the_folder_the_scan_found(steam_frame, monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(main_window.subprocess, "Popen", opened.append)
    steam_frame.service.steam_folder_of = lambda game: tmp_path
    MainFrame.on_open_folder(steam_frame, None)
    assert opened == [["explorer", str(tmp_path)]] and steam_frame.boxes == []
    for folder in (None, tmp_path / "gone"):
        steam_frame.service.steam_folder_of = lambda game, folder=folder: folder
        MainFrame.on_open_folder(steam_frame, None)
    assert len(opened) == 1
    assert [caption for caption, *_ in steam_frame.boxes] == ["Folder Not Found"] * 2
