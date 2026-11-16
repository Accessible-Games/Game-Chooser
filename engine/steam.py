"""Steam: the addresses Game Chooser hands to Windows, and where Steam is installed.

Only `address` builds a Steam address, from a plain positive int: no string from the catalog, the saved
index, games.json or Steam's files is ever handed to Windows as an address.
"""

import os
import sys
from collections.abc import Callable
from pathlib import Path

from . import messages

# The two choices plan 3's Task 0 can change. The spec's facts section says what each address does:
# `run` is the one that gets a game Steam does not have, `rungameid` starts one it has.
GET_ACTION = "run"
LAUNCH_ACTION = "rungameid"

_ACTIONS = (GET_ACTION, LAUNCH_ACTION, "install", "store", "uninstall")

# (hive, key, name, view) -> the value, or None. hive is "HKCU" or "HKLM"; view is "", "32" or "64".
ReadValue = Callable[[str, str, str, str], object]

_LOCATIONS = (
    ("HKCU", r"Software\Valve\Steam", "SteamPath", ""),
    ("HKLM", r"SOFTWARE\Valve\Steam", "InstallPath", "32"),
    ("HKLM", r"SOFTWARE\Valve\Steam", "InstallPath", "64"),
)


def is_app_id(value) -> bool:
    return type(value) is int and value > 0


def address(action: str, app_id: int) -> str:
    if action not in _ACTIONS or not is_app_id(app_id):
        raise ValueError(f"not a Steam address: {action!r} {app_id!r}")
    return f"steam://{action}/{app_id}"


def store_page(app_id: int) -> str:
    if not is_app_id(app_id):
        raise ValueError(f"not a Steam app id: {app_id!r}")
    return f"https://store.steampowered.com/app/{app_id}/"


def _winreg_value(hive: str, key: str, name: str, view: str):
    import winreg
    root = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}[hive]
    access = winreg.KEY_READ | {"64": winreg.KEY_WOW64_64KEY, "32": winreg.KEY_WOW64_32KEY}.get(view, 0)
    try:
        with winreg.OpenKey(root, key, 0, access) as opened:
            return winreg.QueryValueEx(opened, name)[0]
    except OSError:
        return None


def steam_folder(read_value: ReadValue | None = None, platform: str | None = None) -> Path | None:
    """The first registry value that names a folder that exists, else None (also off Windows)."""
    if (platform or sys.platform) != "win32":
        return None
    read_value = read_value or _winreg_value
    for location in _LOCATIONS:
        try:
            value = read_value(*location)
        except OSError:
            continue
        if isinstance(value, str) and value.strip() and Path(value).is_dir():
            return Path(value)
    return None


def send(action: str, app_id: int, open_address=None) -> None:
    """Hand Windows the address for `action`. ValueError for a bad action or id; OSError if it can't."""
    target = address(action, app_id)
    if open_address is None:
        open_address = getattr(os, "startfile", None)
        if open_address is None:
            raise OSError(messages.STEAM_NEEDS_WINDOWS)
    open_address(target)
