"""Lenient reading of catalog entries.

An entry with a value this app does not know is hidden, never an error.
Unknown fields are ignored.
"""
import functools
import os
import platform as _platform
import re
import sys
from dataclasses import dataclass
from urllib.parse import quote

from engine.errors import DependencyError

KNOWN_KINDS = frozenset({"game", "extra", "runtime", "access-mod"})
KNOWN_METHODS = frozenset({"zip", "7z", "installer", "cab", "tar", "none", "url"})
KNOWN_INSTALLER_TYPES = frozenset({"inno", "nsis", "msi", "installshield", "other"})
KNOWN_INSTALLER_MODES = frozenset({"extract", "run"})
KNOWN_STEP_ACTIONS = frozenset({"copy", "move", "delete", "registry", "register", "run"})
KNOWN_ARCHS = frozenset({"x64", "x86", "arm64", "universal"})

_MIRROR_PREFIX = "https://files.accessiblegames.net/files/"
# The catalog schema's id pattern. Ids become folder names and appear in elevated command lines.
_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_PLATFORM_LABELS = {"windows": "Windows", "macos": "macOS", "linux": "Linux", "web": "Web"}


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _sources(entry: dict) -> list[dict]:
    """The entry's sources that are objects; anything else is skipped."""
    sources = entry.get("sources")
    return [s for s in sources if isinstance(s, dict)] if isinstance(sources, list) else []


_MACHINE_NAMES = {"amd64": "x64", "x86_64": "x64", "x64": "x64", "arm64": "arm64", "aarch64": "arm64",
                  "x86": "x86", "i386": "x86", "i486": "x86", "i586": "x86", "i686": "x86"}
# IMAGE_FILE_MACHINE_* values IsWow64Process2 reports for the machine itself
_WINDOWS_MACHINES = {0x8664: "x64", 0xAA64: "arm64", 0x014C: "x86"}


def _windows_arch() -> str | None:
    """The processor Windows runs on, even from an x64 or x86 process emulated on an Arm PC."""
    try:
        import ctypes
        from ctypes import wintypes
        process, native = wintypes.USHORT(), wintypes.USHORT()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE  # a 64-bit pseudo-handle; the default int cuts it
        kernel32.IsWow64Process2.restype = wintypes.BOOL
        kernel32.IsWow64Process2.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT),
                                             ctypes.POINTER(wintypes.USHORT)]
        if kernel32.IsWow64Process2(kernel32.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)):
            return _WINDOWS_MACHINES.get(native.value)
    except (AttributeError, OSError):  # before Windows 10 1709 there is no IsWow64Process2
        pass
    name = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or ""
    return _MACHINE_NAMES.get(name.lower())


def _macos_translated() -> bool:
    """True when this process runs under Rosetta on an Apple Silicon Mac."""
    try:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c"))
        value, size = ctypes.c_int(0), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
        if libc.sysctlbyname(b"sysctl.proc_translated", ctypes.byref(value), ctypes.byref(size), None, 0) == 0:
            return value.value == 1
    except (AttributeError, OSError, TypeError):
        pass
    return False


@functools.lru_cache(maxsize=1)
def current_arch() -> str | None:
    """This machine's processor: "x64", "x86" or "arm64"; None when it can't be told (every source is then
    offered, as before sources named one)."""
    if sys.platform.startswith("win"):
        return _windows_arch()
    arch = _MACHINE_NAMES.get(_platform.machine().lower())
    if sys.platform == "darwin" and arch == "x64" and _macos_translated():
        return "arm64"
    return arch


def runs_on(source_arch, platform: str, machine: str | None) -> bool:
    """Can a build for `source_arch` run on this `platform` and `machine` processor?

    A missing or universal arch runs anywhere, as does a web source; an arch this app doesn't know runs nowhere.
    x86 builds run on x64 Windows and Linux; Windows on Arm runs x64 and x86 builds, and an Apple Silicon Mac
    runs x64 builds (Rosetta), by emulation. An Intel Mac doesn't run arm64 builds, and current macOS runs no
    32-bit (x86) programs.
    """
    if source_arch is None or source_arch == "universal" or platform == "web":
        return True
    if source_arch not in KNOWN_ARCHS:
        return False
    if machine is None or source_arch == machine:
        return True
    if machine == "x64":
        return source_arch == "x86" and platform != "macos"
    if machine == "arm64":
        return ((platform == "windows" and source_arch in ("x64", "x86"))
                or (platform == "macos" and source_arch == "x64"))
    return False


def platform_sources(entry: dict, platform: str, machine: str | None = None) -> list[dict]:
    """The entry's sources for `platform` (and web sources) whose processor can run on `machine` (by default
    this machine's, current_arch)."""
    machine = machine if machine is not None else current_arch()
    return [s for s in _sources(entry) if s.get("platform") in (platform, "web")
            and runs_on(s.get("arch"), s.get("platform"), machine)]


def _source_is_readable(source: object) -> bool:
    if not isinstance(source, dict) or not isinstance(source.get("install"), dict):
        return False
    install = source["install"]
    method = install.get("method")
    if method not in KNOWN_METHODS:
        return False
    if method == "installer":
        installer = install.get("installer")
        if not isinstance(installer, dict):
            return False
        if installer.get("type") not in KNOWN_INSTALLER_TYPES:
            return False
        if installer.get("mode") not in KNOWN_INSTALLER_MODES:
            return False
    steps = source.get("steps") or []
    return isinstance(steps, list) and all(isinstance(step, dict) and step.get("action") in KNOWN_STEP_ACTIONS
                                           for step in steps)


def is_readable(entry: dict) -> bool:
    if entry.get("kind") not in KNOWN_KINDS:
        return False
    if not isinstance(entry.get("id"), str) or not _ID.fullmatch(entry["id"]):
        return False
    sources = entry.get("sources") or []
    return isinstance(sources, list) and all(_source_is_readable(s) for s in sources)


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
        for x in _extras(entry)
        if x.get("required")
    )


def _extras(entry: dict) -> list[dict]:
    extras = entry.get("extras")
    return [x for x in extras if isinstance(x, dict)] if isinstance(extras, list) else []


def visible_games(entries: dict, platform: str) -> list[dict]:
    return [e for e in entries.values() if _game_is_visible(e, entries, platform)]


@dataclass
class ExtraInfo:
    entry: dict
    required: bool


def extras_of(entry: dict, entries: dict, platform: str) -> list[ExtraInfo]:
    result = []
    for x in _extras(entry):
        extra = entries.get(x.get("id"))
        if _usable(extra, platform):
            result.append(ExtraInfo(extra, bool(x.get("required"))))
    return result


def title_of(entry: dict) -> str:
    return entry.get("title") or entry["id"]


def runs_installer(source: dict) -> bool:
    """True for a source that is an installer that must run."""
    install = source.get("install") or {}
    return install.get("method") == "installer" and (install.get("installer") or {}).get("mode") == "run"


def first_developer(entry: dict) -> str:
    developers = entry.get("developer") or []
    return developers[0] if developers else ""


def is_mirror_url(url: str) -> bool:
    return url.startswith(_MIRROR_PREFIX)


def mirror_url_for(sha256: str, name: str) -> str:
    return f"{_MIRROR_PREFIX}{sha256}/{quote(name)}"


def game_fields(entry: dict, platform: str | None = None) -> dict:
    """The games.json fields. With `platform`, the platform label is that of the first source for it (the one
    an install uses); without, the entry's first source."""
    sources = platform_sources(entry, platform) if platform else _sources(entry)
    platforms = [_PLATFORM_LABELS.get(sources[0].get("platform"), "")] if sources else []
    genres = [g for g in entry.get("genres") or [] if isinstance(g, str) and g.strip()]
    year = entry.get("year")
    return {
        "title": title_of(entry),
        "developer": ", ".join(entry.get("developer") or []),
        "genre": genres[0].strip() if genres else "",  # the main genre
        "year": str(year) if year is not None else "",
        "platforms": platforms,
    }
