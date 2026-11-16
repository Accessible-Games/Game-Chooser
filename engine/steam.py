"""Steam: the addresses Game Chooser hands to Windows, and where Steam is installed.

Only `address` builds a Steam address, from a plain positive int: no string from the catalog, the saved
index, games.json or Steam's files is ever handed to Windows as an address.
"""

import os
import sys
from collections.abc import Callable
from pathlib import Path

from . import messages

# Checked by ear on Windows with Steam installed, Nov 16, 2026, for a game the player does not own and that
# is not released: `steam://run/<id>` only opened Steam's store home page; `steam://store/<id>` opened the
# game's own page; `steam://install/<id>` for its free demo offered to install the demo. So "Steam" opens the
# game's page and "Steam demo" installs the demo. The launch address has not been checked with an update pending.
GET_ACTION = "store"
GET_DEMO_ACTION = "install"
LAUNCH_ACTION = "rungameid"

# A fixed list, never built from the two choices above: changing one of them must not drop an address
_ACTIONS = ("run", "rungameid", "install", "store", "uninstall")

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
