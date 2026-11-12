"""MainFrame methods that need no main window: each runs on a stand-in for the frame, so no window is built, no
dialog opens and nothing is launched. Skips when wxPython is not installed or there is no display."""

from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")
if not wx.App.IsDisplayAvailable():
    pytest.skip("no display", allow_module_level=True)

from main_window import MainFrame  # noqa: E402
from tests.test_games_panel import app  # noqa: E402,F401  (a fixture)


def key_down(code, ctrl=False, alt=False):
    event = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
    event.SetKeyCode(code)
    event.SetControlDown(ctrl)
    event.SetAltDown(alt)
    return event


def test_the_library_lists_own_keys_are_ctrl_e_and_del(app):
    """AltGr+E is a letter on several layouts (é, ę, €) and reaches the list with Ctrl and Alt both down: it is
    not Ctrl+E"""
    done = []
    frame = SimpleNamespace(on_edit_game=lambda event: done.append("edit"),
                            on_delete_game=lambda event: done.append("delete"))
    assert MainFrame.on_list_key(frame, key_down(ord("E"), ctrl=True)) and done == ["edit"]
    assert MainFrame.on_list_key(frame, key_down(wx.WXK_DELETE)) and done == ["edit", "delete"]
    assert not MainFrame.on_list_key(frame, key_down(ord("E"), ctrl=True, alt=True))
    assert not MainFrame.on_list_key(frame, key_down(ord("E"))) and done == ["edit", "delete"]
