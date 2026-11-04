"""Finding the executable to launch after an install.

`select_best_executable` holds the preference rules shared with the library scanner.
"""
import os
from collections.abc import Callable
from pathlib import Path

from .errors import UnsafePath
from .paths import is_engine_dir
from .safety import _resolve_game_path, is_link

MAX_DEPTH = 10
_EXE_SUFFIXES = (".exe", ".bat")


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
