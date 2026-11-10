"""Finding the executable to launch after an install.

`select_best_executable` holds temporary fallback preference rules, used until the catalog records
launch paths.
"""
import functools
import os
from collections.abc import Callable
from pathlib import Path

from .errors import UnsafePath
from .paths import is_engine_dir
from .safety import _resolve_game_path, is_link

MAX_DEPTH = 10
_EXE_SUFFIXES = (".exe", ".bat")

ERROR_ELEVATION_REQUIRED = 740
ERROR_CANCELLED = 1223  # ShellExecuteExW's last error when the player declines the administrator prompt
_SE_ERR_ACCESSDENIED = 5  # the shell's answer (hInstApp) when the player declines the administrator prompt
_SEE_MASK_NOCLOSEPROCESS = 0x00000040  # hand back the started process's handle
_SEE_MASK_FLAG_NO_UI = 0x00000400  # no error boxes from the shell (as ShellExecuteW does)


class LaunchCancelled(Exception):
    """The player declined the administrator prompt for a game that needs it."""


@functools.cache
def _shell_execute_info():
    """The SHELLEXECUTEINFOW structure (defined on first use, so importing this module needs no ctypes)."""
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),  # a union with hMonitor
            ("hProcess", wintypes.HANDLE),
        ]
    return SHELLEXECUTEINFOW


def _shell_open(path: str, cwd: str) -> int | None:
    """Start `path` the way Explorer does, so Windows shows its administrator prompt (UAC) when the program asks
    for one. Returns the started process's ID, or None when the shell hands back no process. Raises
    LaunchCancelled if the player declines, OSError if it can't start."""
    import ctypes
    from ctypes import wintypes

    SHELLEXECUTEINFOW = _shell_execute_info()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell_execute = shell32.ShellExecuteExW
    shell_execute.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell_execute.restype = wintypes.BOOL
    kernel32.GetProcessId.argtypes = [wintypes.HANDLE]
    kernel32.GetProcessId.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS | _SEE_MASK_FLAG_NO_UI
    info.hwnd = None
    info.lpVerb = "open"
    info.lpFile = path
    info.lpParameters = None
    info.lpDirectory = cwd
    info.nShow = 1  # SW_SHOWNORMAL
    if not shell_execute(ctypes.byref(info)):
        error = ctypes.get_last_error()
        code = info.hInstApp or 0
        if error == ERROR_CANCELLED or code == _SE_ERR_ACCESSDENIED:
            raise LaunchCancelled()
        raise OSError(f"Windows couldn't start {path} (error {error or code})")
    if not info.hProcess:
        return None
    try:
        return kernel32.GetProcessId(info.hProcess) or None
    finally:
        kernel32.CloseHandle(info.hProcess)


def start_program(path: str, cwd: str, popen: Callable | None = None,
                  shell_open: Callable[[str, str], int | None] | None = None) -> int | None:
    """Start a game's program with its folder as the working directory. Returns the started process's ID, or
    None when none can be had.

    A program marked "run as administrator" (an installer can set that flag on the games it installs) can't be
    started directly: Windows answers ERROR_ELEVATION_REQUIRED. Such a program is started through the shell
    instead, which shows the administrator prompt as Explorer does. Raises LaunchCancelled if the player
    declines it, OSError if the program can't be started.
    """
    if popen is None:
        import subprocess
        popen = subprocess.Popen
    try:
        process = popen([path], cwd=cwd)
    except OSError as e:
        if getattr(e, "winerror", None) != ERROR_ELEVATION_REQUIRED:
            raise
        return (shell_open or _shell_open)(path, cwd)
    return getattr(process, "pid", None)


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
