"""GameDialog (Add Game and Edit Game) on a frame that is never shown: no dialog opens and no box is shown. Skips
when wxPython is not installed or there is no display."""

import pytest

wx = pytest.importorskip("wx")
if not wx.App.IsDisplayAvailable():
    pytest.skip("no display", allow_module_level=True)

import dialogs  # noqa: E402
from models import Game  # noqa: E402
from tests.test_games_panel import app  # noqa: E402,F401  (app is a fixture)


@pytest.fixture
def make_dialog(app, isolated_manager, monkeypatch):
    """GameDialog(game) on a hidden frame. The duplicate check runs at once, a box is recorded in `dlg.boxes`
    instead of shown, and the dialog counts as open, so Add or Save can close it."""
    top = wx.Frame(None)
    monkeypatch.setattr(dialogs, "run_in_background", lambda work, done, name, **kw: done(work()))

    def make(game=None):
        dlg = dialogs.GameDialog(top, isolated_manager, game)
        dlg.boxes = []
        monkeypatch.setattr(wx, "MessageBox", lambda text, caption="", *args: dlg.boxes.append(caption))
        dlg.IsModal = lambda: True
        dlg.EndModal = lambda code: None
        return dlg
    yield make
    top.Destroy()


def test_a_game_from_2000_keeps_its_year(make_dialog, tmp_path):
    exe = tmp_path / "game.exe"
    exe.write_bytes(b"MZ")
    game = Game(title="Alpha", year="2000", platforms=["Windows"], launch_path=str(exe))
    dlg = make_dialog(game)
    dlg.on_ok(None)
    assert dlg.boxes == [] and game.year == "2000"
