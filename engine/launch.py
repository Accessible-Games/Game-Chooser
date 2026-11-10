"""Finding the executable to launch after an install, and deciding how a launch file is started.

`select_best_executable` holds temporary fallback preference rules, used until the catalog records
launch paths.
"""
import os
from collections.abc import Callable
from typing import NamedTuple
from pathlib import Path
from urllib.parse import quote

from .errors import UnsafePath
from .paths import is_engine_dir
from .safety import _resolve_game_path, is_link

MAX_DEPTH = 10
_EXE_SUFFIXES = (".exe", ".bat")
# A launch file that opens in the browser; .hta is a program, never a page. On Windows `webbrowser.open` is a shell
# open: the file opens in whatever program the player associates with .html, not necessarily a browser. So
# `launch_kind`'s suffix check is the only gate: do not widen this on the assumption that "the browser" is safe.
PAGE_SUFFIXES = (".html", ".htm")

ERROR_ELEVATION_REQUIRED = 740
_SE_ERR_ACCESSDENIED = 5  # ShellExecuteW's answer when the player declines the administrator prompt


class LaunchCancelled(Exception):
    """The player declined the administrator prompt for a game that needs it."""


def _shell_open(path: str, cwd: str) -> None:
    """Start `path` the way Explorer does, so Windows shows its administrator prompt (UAC) when the program asks
    for one. Raises LaunchCancelled if the player declines, OSError if it can't start."""
    import ctypes
    shell_execute = ctypes.windll.shell32.ShellExecuteW
    shell_execute.restype = ctypes.c_ssize_t
    result = shell_execute(None, "open", path, None, cwd, 1)  # SW_SHOWNORMAL
    if result == _SE_ERR_ACCESSDENIED:
        raise LaunchCancelled()
    if result <= 32:
        raise OSError(f"Windows couldn't start {path} (error {result})")


def start_program(path: str, cwd: str, popen: Callable | None = None,
                  shell_open: Callable[[str, str], None] | None = None) -> None:
    """Start a game's program with its folder as the working directory.

    A program marked "run as administrator" (an installer can set that flag on the games it installs) can't be
    started directly: Windows answers ERROR_ELEVATION_REQUIRED. Such a program is started through the shell
    instead, which shows the administrator prompt as Explorer does. Raises LaunchCancelled if the player
    declines it, OSError if the program can't be started.
    """
    if popen is None:
        import subprocess
        popen = subprocess.Popen
    try:
        popen([path], cwd=cwd)
    except OSError as e:
        if getattr(e, "winerror", None) != ERROR_ELEVATION_REQUIRED:
            raise
        (shell_open or _shell_open)(path, cwd)


class Started(NamedTuple):
    """What `start_game` started: "page" or "program", and the program's process id when its starter gave one."""
    kind: str
    pid: int | None


def launch_kind(path: str) -> str:
    """"page" for a launch file that is a web page (.html, .htm), otherwise "program"."""
    return "page" if Path(path).suffix.lower() in PAGE_SUFFIXES else "program"


def page_address(path: str) -> str:
    """The file: address of a page on disk. ASCII characters that need it (space, #, %, ...) are escaped; other
    letters are kept as they are, because the Windows shell reads each %XX as one code-page character and would
    look for a different folder. The page's own name is kept (a link is not followed)."""
    posix = Path(path).absolute().as_posix()
    escaped = "".join(quote(c, safe="/:") if ord(c) < 128 else c for c in posix)
    if posix.startswith("//"):  # a network share: file://server/share/...
        return "file:" + escaped
    return "file:///" + escaped.lstrip("/")


def start_game(path: str, cwd: str, open_page: Callable[[str], object] | None = None,
               start: Callable[[str, str], int | None] | None = None) -> Started:
    """Start a game's launch file and return what it started: a page opens in the web browser, anything else
    runs through `start_program`. Raises OSError if the browser can't be opened; LaunchCancelled and the
    OSError from `start` pass through."""
    kind = launch_kind(path)
    if kind == "page":
        if open_page is None:
            import webbrowser
            open_page = webbrowser.open
        if open_page(page_address(path)) is False:
            raise OSError("The web browser couldn't be opened.")
        return Started(kind, None)
    return Started(kind, (start or start_program)(path, cwd))


def select_best_executable(exe_paths: list[Path], directory_name: str) -> Path | None:
    """Pick the best executable: game/launch/play, then one named like the folder, then the first."""
    if not exe_paths:
        return None
    for preferred in ("game", "launch", "play"):
        for exe_path in exe_paths:
            if exe_path.stem.lower() == preferred:
                return exe_path
    dir_lower = directory_name.lower()
    for exe_path in exe_paths:
        if exe_path.stem.lower() == dir_lower:
            return exe_path
    return exe_paths[0]


def _recorded_launch_exe(game_dir: Path, launch_path: str | None) -> Path | None:
    if not launch_path:
        return None
    try:
        path, through_link = _resolve_game_path(game_dir, launch_path)
    except (UnsafePath, ValueError, OSError):
        return None
    if through_link:
        return None
    try:
        if is_link(path) or not path.is_file():
            return None
    except OSError:
        return None
    return path


def is_catalog_launch(game_dir: Path, exe: Path, hint: str | None) -> bool:
    """Did `exe` come from the catalog's launch hint (the hint names a real file, and it is this one)?"""
    return _recorded_launch_exe(Path(game_dir), hint) == Path(exe)


def adopt_catalog_launch(record, entry: dict, platform: str, game_dir: Path) -> bool:
    """Switch a guessed launch path to the catalog's, when the entry now has one that exists in the game.

    Returns True when the record changed; the caller saves it."""
    if record.launch_from_catalog:
        return False
    hint = ((entry.get("launch") or {}).get(platform) or {}).get("path")
    exe = _recorded_launch_exe(Path(game_dir), hint)
    if exe is None:
        return False
    record.launch_path = exe.relative_to(Path(game_dir)).as_posix()
    record.launch_from_catalog = True
    return True


def find_launch_exe(game_dir: Path, launch_path: str | None,
                    exclude: Callable[[Path], bool]) -> Path | None:
    """The recorded launch path if it is a real file inside the game, else the best found exe.

    The search goes folder level by level (up to MAX_DEPTH levels down) and uses the first
    level that has any candidate. It skips engine folders and never follows links.
    """
    game_dir = Path(game_dir)
    recorded = _recorded_launch_exe(game_dir, launch_path)
    if recorded is not None:
        return recorded

    level = [game_dir]
    for depth in range(MAX_DEPTH + 1):
        candidates: list[Path] = []
        next_level: list[Path] = []
        for folder in level:
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for entry in entries:
                path = Path(entry.path)
                if is_link(path):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not is_engine_dir(entry.name):
                            next_level.append(path)
                    elif entry.is_file(follow_symlinks=False) \
                            and entry.name.lower().endswith(_EXE_SUFFIXES) and not exclude(path):
                        candidates.append(path)
                except OSError:
                    continue
        if candidates:
            return select_best_executable(sorted(candidates), game_dir.name)
        level = sorted(next_level)
    return None
