"""Windows' installed-programs list (the Uninstall keys): read-only snapshots, and what an entry says.

A new entry after an installer ran names where the game went (InstallLocation, else the folder of
DisplayIcon or UninstallString) and the vendor's uninstaller.
"""

import os
import re
from collections.abc import Callable
from pathlib import Path, PureWindowsPath

_UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
# (hive, key, view): HKLM's 64-bit and 32-bit (WOW6432Node) views, and HKCU.
UNINSTALL_KEYS = (("HKLM", _UNINSTALL, "64"), ("HKLM", _UNINSTALL, "32"), ("HKCU", _UNINSTALL, ""))

UninstallReader = Callable[[str, str, str], dict[str, dict]]


def _winreg_reader(hive: str, key: str, view: str) -> dict[str, dict]:
    """Every subkey of `key` with its values, read-only. Unreadable subkeys are skipped."""
    import winreg
    root = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}[hive]
    access = winreg.KEY_READ | {"64": winreg.KEY_WOW64_64KEY, "32": winreg.KEY_WOW64_32KEY}.get(view, 0)
    result: dict[str, dict] = {}
    try:
        parent = winreg.OpenKey(root, key, 0, access)
    except OSError:
        return result
    with parent:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(parent, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(parent, name, 0, access) as sub:
                    values = {}
                    value_index = 0
                    while True:
                        try:
                            value_name, data, _ = winreg.EnumValue(sub, value_index)
                        except OSError:
                            break
                        values[value_name] = data
                        value_index += 1
            except OSError:
                continue
            result[name] = values
    return result


def snapshot_uninstall_keys(read: UninstallReader = _winreg_reader) -> dict[str, dict]:
    """`hive:view:subkey` (lowercase) -> that entry's values, over every UNINSTALL_KEYS location."""
    snapshot: dict[str, dict] = {}
    for hive, key, view in UNINSTALL_KEYS:
        try:
            entries = read(hive, key, view)
        except OSError:
            continue
        for name, values in entries.items():
            snapshot[f"{hive}:{view}:{name}".lower()] = values
    return snapshot


def new_uninstall_entries(before: dict[str, dict], after: dict[str, dict]) -> list[dict]:
    return [after[key] for key in sorted(after) if key not in before]


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _command_path(text: str) -> str:
    """The program path at the start of a command line: quoted, or up to the first .exe/.ico/.dll."""
    text = text.strip()
    if text.startswith('"'):
        return text[1:].split('"', 1)[0]
    match = re.match(r"(.+?\.(?:exe|ico|dll))(?:\s|,|$)", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return text.split(" ", 1)[0]


def split_command(command: str) -> tuple[str, str]:
    """(program, arguments) of a command line such as an UninstallString."""
    exe = _command_path(command)
    rest = command.strip()
    rest = rest[len(exe) + 2:] if rest.startswith('"') else rest[len(exe):]
    return exe, rest.strip()


def windows_dir() -> Path:
    return Path(os.environ.get("SystemRoot") or os.environ.get("windir") or "C:\\Windows")


def _folder_of_program(text: str) -> Path | None:
    if not text:
        return None
    path = PureWindowsPath(_command_path(text))
    if not path.drive or not path.root:
        return None  # "MsiExec.exe /X{...}": not a path into the game's folder
    folder = Path(str(path.parent))
    return None if inside(folder, windows_dir()) else folder


def folder_from_uninstall_entry(values: dict) -> Path | None:
    """The installed folder an Uninstall entry names: InstallLocation, else DisplayIcon's or
    UninstallString's folder. None for a folder under Windows (MSI's cached icons, msiexec)."""
    location = _text(values.get("InstallLocation")).strip('"')
    if location:
        path = PureWindowsPath(location)
        if path.drive and path.root:
            return Path(str(path))
    icon = re.sub(r",\s*-?\d+\s*$", "", _text(values.get("DisplayIcon")))
    return _folder_of_program(icon) or _folder_of_program(_text(values.get("UninstallString")))


def is_missing_program(command: str) -> bool:
    """True if the command's program is an absolute path that no longer exists."""
    program = PureWindowsPath(_command_path(command))
    return bool(program.drive and program.root) and not os.path.lexists(_command_path(command))


def _letters(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def names_title(values: dict, title: str) -> bool:
    """True if the entry's DisplayName is the game's title (or contains it, as "Title 1.0" does)."""
    name, wanted = _letters(_text(values.get("DisplayName"))), _letters(title)
    return len(wanted) >= 3 and bool(name) and (wanted in name or name in wanted and len(name) >= 3)


def vendor_uninstaller(values: dict) -> str | None:
    """The entry's quiet uninstall command, else its uninstall command."""
    return _text(values.get("QuietUninstallString")) or _text(values.get("UninstallString")) or None


def inside(path: Path, folder: Path) -> bool:
    """True if `path` is `folder` or inside it (compared case-insensitively)."""
    path, folder = (Path(os.path.normcase(os.path.abspath(p))) for p in (path, folder))
    return path == folder or folder in path.parents
