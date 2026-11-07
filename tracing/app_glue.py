"""The tracing build's wx glue: starts the recorder, shows tracing's state in the title, records launches and marks.

The only module in tracing/ that imports wx. The main window knows nothing about tracing: it calls
`frame.launch_observer.before_launch(game)` and `.launched(...)` around its unchanged Popen, and shows
`frame.title_note` in its title. Every message goes through `frame.run_when_no_dialog` and `frame.modal_dialog()`.
"""
import logging
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import wx

import wx_bridge
from tracing import cli, messages, paths
from tracing.client import TraceClient, TraceState
from tracing.clock import now_filetime

log = logging.getLogger(__name__)

HOTKEY_ID = 0x7A01  # application hotkey ids are 0x0000-0xBFFF
POLL_MS = 1000
TITLE_NOTES = {"starting": messages.TITLE_STARTING, "on": messages.TITLE_ON, "off": messages.TITLE_OFF}


def _start_recorder(compiled: bool):
    """Start the recorder elevated (one UAC prompt); returns its RunningProcess."""
    from engine.elevation import WindowsElevator

    def start(argv_list: list[str]):
        exe, lead = paths.self_command(compiled)
        return WindowsElevator().start(exe, subprocess.list2cmdline(lead + argv_list), elevated=True, show=False)
    return start


def _app_exe(compiled: bool) -> str:
    exe, lead = paths.self_command(compiled)
    return exe if compiled else lead[0]


def _mtime_iso(path: str) -> str | None:
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _env(compiled: bool) -> dict:
    app_exe = _app_exe(compiled)
    return {
        "windows": platform.platform(),
        "local_appdata": os.environ.get("LOCALAPPDATA", ""),
        "user_profile": os.environ.get("USERPROFILE", ""),
        "app_exe": app_exe,
        "app_exe_mtime": _mtime_iso(app_exe),
        "compiled": compiled,
    }


def attach(frame, compiled: bool, argv: list[str]) -> "TracingGlue":
    traces_dir = paths.traces_dir(compiled, cli.traces_dir_override(argv))
    client = TraceClient(traces_dir, paths.procmon_exe(compiled), _start_recorder(compiled), _env(compiled))
    return TracingGlue(frame, client)


class TracingGlue:
    """The main window's launch observer in the tracing build. UI thread only."""

    def __init__(self, frame, client: TraceClient):
        self._frame = frame
        self._client = client
        self._frame_id = frame.GetId()
        self._hwnd = frame.GetHandle()
        self._phase = "starting"
        self._told_off = False

        frame.launch_observer = self
        frame.title_note = messages.TITLE_STARTING
        frame.update_title()

        try:
            registered = frame.RegisterHotKey(HOTKEY_ID, wx.MOD_CONTROL | wx.MOD_ALT | wx.MOD_SHIFT, ord("M"))
        except Exception:
            log.exception("Registering the mark key failed")
            registered = False
        if registered:
            frame.Bind(wx.EVT_HOTKEY, self._on_hotkey, id=HOTKEY_ID)
        else:
            self._tell(messages.HOTKEY_FAILED)
        self._hotkey = registered

        self._timer = wx.Timer(frame)
        frame.Bind(wx.EVT_TIMER, self._on_timer, self._timer)
        frame.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)
        self._timer.Start(POLL_MS)

        wx_bridge.run_in_background(client.start, self._on_started, "tracing start")

    # --- the launch observer ---

    def before_launch(self, game):
        """("traced", before) when tracing is on; otherwise ask, and ("untraced", None) or None (don't launch)."""
        try:
            state = self._client.poll()
            self._apply_state(state)
        except Exception:
            log.exception("Checking tracing before a launch failed")
            state = self._client.state  # the last known state, not an assumed off
        if state.phase == "on":
            return ("traced", now_filetime())
        text = messages.LAUNCH_STARTING if state.phase == "starting" else messages.LAUNCH_OFF
        with self._frame.modal_dialog():
            answer = wx.MessageBox(text.format(title=game.title), messages.DIALOG_CAPTION,
                                   wx.YES_NO | wx.ICON_QUESTION, self._frame)
        return ("untraced", None) if answer == wx.YES else None

    def launched(self, token, game, proc, full_path, game_dir) -> None:
        """Right after Popen: record the session. Never raises into launch_game."""
        try:
            after = now_filetime()
            if not token or token[0] != "traced":
                return
            info = {
                "title": game.title,
                "catalog_id": game.catalog_id,
                "library_id": game.library_id,
                "launch_path": game.launch_path,
                "exe": full_path,
                "game_dir": game_dir,
            }
            if self._client.game_launched(proc.pid, token[1], after, info) is None:
                log.warning("Tracing went off before the launch of %s could be recorded", game.title)
        except Exception as exc:
            log.exception("Recording a launch failed")
            self._tell(messages.LAUNCH_RECORD_FAILED.format(title=getattr(game, "title", ""), error=exc))

    # --- state ---

    def _on_started(self, _result) -> None:
        if not self._frame:
            return
        self._apply_state(self._client.state)

    def _on_timer(self, _event) -> None:
        if not self._frame or getattr(self._frame, "_quitting", False):
            return
        try:
            state = self._client.poll()
        except Exception:
            log.exception("Polling the recorder failed")
            return
        self._apply_state(state)

    def _apply_state(self, state: TraceState) -> None:
        if state.phase == self._phase or not self._frame:
            return
        self._phase = state.phase
        self._frame.title_note = TITLE_NOTES.get(state.phase, messages.TITLE_OFF)
        self._frame.update_title()
        if state.phase == "off":
            self._timer.Stop()  # off is final: nothing more to poll for
            if not self._told_off:
                self._told_off = True
                self._tell(state.reason or messages.RECORDER_GONE)

    # --- the mark key ---

    def _on_hotkey(self, _event) -> None:
        try:
            marked = self._client.mark()
        except Exception:
            log.exception("Recording a mark failed")
            marked = []
        _beep(bool(marked))
        try:
            self._apply_state(self._client.state)
        except Exception:
            log.exception("Updating tracing's state after a mark failed")

    # --- teardown and messages ---

    def _on_destroy(self, event) -> None:
        """When the frame is destroyed: stop polling and give the mark key back.

        By the time this event arrives the frame's Python wrapper already counts as deleted (GetEventObject() is a
        new proxy and frame.UnregisterHotKey raises RuntimeError), so the frame is recognised by id and the key is
        unregistered through user32 with the window handle saved at attach; the window itself still exists."""
        event.Skip()
        if event.GetId() != self._frame_id:
            return  # not the frame
        self._timer.Stop()
        if self._hotkey:
            self._hotkey = False
            try:
                import ctypes
                ctypes.windll.user32.UnregisterHotKey(ctypes.c_void_p(self._hwnd), HOTKEY_ID)
            except Exception:
                log.debug("Unregistering the mark key failed", exc_info=True)

    def _tell(self, text: str) -> None:
        """Show text once no modal dialog is open; posted, so it never opens a dialog inside the caller."""
        frame = self._frame

        def show():
            if frame and not getattr(frame, "_quitting", False):
                frame._tell(text, messages.DIALOG_CAPTION, wx.ICON_WARNING)

        def post():
            if frame and not getattr(frame, "_quitting", False):
                frame.run_when_no_dialog(show)

        try:
            wx.CallAfter(post)
        except Exception:
            log.exception("Showing a tracing message failed: %s", text)


def _beep(marked: bool) -> None:
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_OK if marked else winsound.MB_ICONHAND)
    except Exception:
        log.debug("Mark sound failed", exc_info=True)
