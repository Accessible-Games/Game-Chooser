"""Lenient reading of catalog entries.

An entry with a value this app does not know is hidden, never an error.
Unknown fields are ignored.
"""
import sys
from dataclasses import dataclass
from urllib.parse import quote

from engine.errors import DependencyError

KNOWN_KINDS = frozenset({"game", "extra", "runtime", "access-mod"})
KNOWN_METHODS = frozenset({"zip", "7z", "installer", "cab", "none", "url"})
KNOWN_INSTALLER_TYPES = frozenset({"inno", "nsis", "msi", "installshield", "other"})
KNOWN_INSTALLER_MODES = frozenset({"extract", "run"})
KNOWN_STEP_ACTIONS = frozenset({"copy", "move", "delete", "registry", "register", "run"})

_MIRROR_PREFIX = "https://files.accessiblegames.net/files/"
_PLATFORM_LABELS = {"windows": "Windows", "macos": "macOS", "linux": "Linux", "web": "Web"}


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def platform_sources(entry: dict, platform: str) -> list[dict]:
    return [s for s in entry.get("sources") or [] if s.get("platform") in (platform, "web")]


def _source_is_readable(source: dict) -> bool:
    install = source.get("install") or {}
    method = install.get("method")
    if method not in KNOWN_METHODS:
        return False
    if method == "installer":
        installer = install.get("installer") or {}
        if installer.get("type") not in KNOWN_INSTALLER_TYPES:
            return False
        if installer.get("mode") not in KNOWN_INSTALLER_MODES:
            return False
    return all(step.get("action") in KNOWN_STEP_ACTIONS for step in source.get("steps") or [])


def is_readable(entry: dict) -> bool:
    if entry.get("kind") not in KNOWN_KINDS:
        return False
    return all(_source_is_readable(s) for s in entry.get("sources") or [])


def resolve_dependencies(entry_id: str, entries: dict) -> list[str]:
    """Dependency ids in install order (depth first, each once)."""
    order: list[str] = []
    path: list[str] = []

    def visit(current: str) -> None:
        entry = entries.get(current)
        if entry is None:
            raise DependencyError(f"missing dependency: {current}")
        if current in path:
            raise DependencyError("dependency cycle: " + " -> ".join(path + [current]))
        path.append(current)
        for dep in entry.get("dependencies") or []:
            if dep not in order:
                visit(dep)
                order.append(dep)
        path.pop()

    visit(entry_id)
    return order


def _usable(entry: dict | None, platform: str) -> bool:
    return entry is not None and is_readable(entry) and bool(platform_sources(entry, platform))


def _game_is_visible(entry: dict, entries: dict, platform: str) -> bool:
    if entry.get("kind") != "game" or not _usable(entry, platform):
        return False
    try:
        deps = resolve_dependencies(entry["id"], entries)
    except DependencyError:
        return False
    for dep_id in deps:
        dep = entries[dep_id]
        if dep.get("kind") != "runtime" or not _usable(dep, platform):
            return False
    return all(
        _usable(entries.get(x.get("id")), platform)
        for x in entry.get("extras") or []
        if x.get("required")
    )


def visible_games(entries: dict, platform: str) -> list[dict]:
    return [e for e in entries.values() if _game_is_visible(e, entries, platform)]


@dataclass
class ExtraInfo:
    entry: dict
    required: bool


def extras_of(entry: dict, entries: dict, platform: str) -> list[ExtraInfo]:
    result = []
    for x in entry.get("extras") or []:
        extra = entries.get(x.get("id"))
        if _usable(extra, platform):
            result.append(ExtraInfo(extra, bool(x.get("required"))))
    return result


def first_developer(entry: dict) -> str:
    developers = entry.get("developer") or []
    return developers[0] if developers else ""


def is_mirror_url(url: str) -> bool:
    return url.startswith(_MIRROR_PREFIX)


def mirror_url_for(sha256: str, name: str) -> str:
    return f"{_MIRROR_PREFIX}{sha256}/{quote(name)}"


def game_fields(entry: dict) -> dict:
    sources = entry.get("sources") or []
    platforms = [_PLATFORM_LABELS.get(sources[0].get("platform"), "")] if sources else []
    genres = entry.get("genres") or []
    year = entry.get("year")
    return {
        "title": entry.get("title", ""),
        "developer": ", ".join(entry.get("developer") or []),
        "genre": genres[0] if genres else "",
        "year": str(year) if year is not None else "",
        "platforms": platforms,
    }
