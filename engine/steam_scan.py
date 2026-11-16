"""Which Steam games are installed, read from Steam's own files. Only reads; never writes under Steam's folders."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import keyvalues
from .steam import ReadValue, steam_folder

_MANIFEST = re.compile(r"appmanifest_(\d+)\.acf")
_INSTALLED_FLAG = 4


@dataclass(frozen=True)
class SteamApp:
    app_id: int
    name: str
    folder: Path  # <library>/steamapps/common/<installdir>


@dataclass(frozen=True)
class Scan:
    state: str  # "found", "absent" (Steam is not installed) or "unknown" (could not tell)
    apps: dict = field(default_factory=dict)  # installed apps, by id: {int: SteamApp}
    unsure: frozenset = frozenset()  # ids whose manifest could not be read
    complete: bool = True  # every listed library folder was reachable
    unread: frozenset | None = None  # ids the libraries not read say they hold; None when one of them does not say

    def gone(self, app_id: int) -> bool:
        """This scan can tell the app is not installed."""
        if self.state != "found":
            return self.state == "absent"
        if app_id in self.apps or app_id in self.unsure:
            return False
        return self.complete or (self.unread is not None and app_id not in self.unread)


def _read(path: Path) -> dict:
    return keyvalues.parse(path.read_text(encoding="utf-8", errors="replace"))


def _held(value) -> frozenset | None:
    """The ids a current-form library entry's `apps` object says it holds; None when it does not say."""
    apps = value.get("apps") if isinstance(value, dict) else None
    if not isinstance(apps, dict) or not all(k.isascii() and k.isdigit() for k in apps):
        return None
    return frozenset(int(k) for k in apps)


def _libraries(steam_dir: Path) -> tuple[dict, bool, frozenset | None]:
    """Steam's own folder, then each folder the list names, once each, with the ids each says it holds (None when
    it does not say); whether every entry gave a path; and the ids the entries with no usable path say they hold.
    Raises OSError or ValueError when the list can't be read or is not a library list."""
    listed = _read(steam_dir / "steamapps" / "libraryfolders.vdf").get("libraryfolders")
    if not isinstance(listed, dict):
        raise ValueError("not a library list")
    found, complete, unread = {steam_dir: None}, True, frozenset()
    for key, value in listed.items():
        if not key.isdigit():
            continue
        path = value.get("path") if isinstance(value, dict) else value  # current form, old form
        if not isinstance(path, str) or not path or "\0" in path or not Path(path).is_absolute():
            complete = False
            unread = _union(unread, _held(value))
        elif found.get(Path(path)) is None:
            found[Path(path)] = _held(value)
    return found, complete, unread


def _union(a: frozenset | None, b: frozenset | None) -> frozenset | None:
    return None if a is None or b is None else a | b  # once a library does not say, the scan cannot tell


def _manifest(path: Path, app_id: int, common: Path) -> SteamApp | None:
    """The installed app, None when not installed; ValueError or OSError when the file can't be trusted."""
    state = _read(path).get("appstate")
    if not isinstance(state, dict) or state.get("appid") != str(app_id):
        raise ValueError("another app")
    flags = state.get("stateflags")
    if not isinstance(flags, str):
        raise ValueError("StateFlags is not a text")
    flags = int(flags)
    installdir = state.get("installdir")
    if not isinstance(installdir, str) or installdir in ("", ".", "..") or any(c in installdir for c in "/\\:\0"):
        raise ValueError("installdir is not a plain folder name")
    folder = common / installdir
    if folder.parent != common:  # a second guard: the folder must sit directly in common
        raise ValueError("installdir leaves common")
    if not folder.is_dir():
        return None
    if not flags & _INSTALLED_FLAG:
        raise ValueError("Steam is updating or downloading it")  # its folder is there: it may be installed
    name = state.get("name")
    return SteamApp(app_id, name if isinstance(name, str) and name else installdir, folder)


def scan_folder(steam_dir: Path) -> Scan:
    try:
        libraries, complete, unread = _libraries(steam_dir)
    except (OSError, ValueError):
        return Scan("unknown")
    apps: dict[int, SteamApp] = {}
    unsure: set[int] = set()
    for lib, held in libraries.items():
        steamapps = lib / "steamapps"
        try:
            names = sorted(p.name for p in steamapps.iterdir())
        except (OSError, ValueError):
            complete = False
            unread = _union(unread, held)
            continue
        for name in names:
            match = _MANIFEST.fullmatch(name)
            if not match or not (steamapps / name).is_file():
                continue
            app_id = int(match.group(1))
            if app_id in apps:
                continue
            try:
                app = _manifest(steamapps / name, app_id, steamapps / "common")
            except (OSError, ValueError):
                unsure.add(app_id)
                continue
            if app:
                apps[app_id] = app
    return Scan("found", apps, frozenset(unsure - apps.keys()), complete, unread)


def scan(read_value: ReadValue | None = None, platform: str | None = None) -> Scan:
    folder = steam_folder(read_value, platform)
    return Scan("absent") if folder is None else scan_folder(folder)
