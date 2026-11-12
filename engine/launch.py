"""Finding the executable to launch after an install, and deciding how a launch file is started.

`select_best_executable` holds temporary fallback preference rules, used until the catalog records
launch paths.

Three ways a launch file starts (`start_game`): a page (.html, .htm) opens in the browser; an HTML Application
(.hta) runs in Windows' own mshta.exe (`start_hta`); anything else is run directly (`start_program`).
"""
import functools
import ntpath
import os
import sys
from collections.abc import Callable
from typing import NamedTuple
from pathlib import Path
from urllib.parse import quote

from . import messages
from .errors import UnsafePath
from .paths import is_engine_dir
from .safety import _resolve_game_path, is_link

MAX_DEPTH = 10
# What the temporary guess may pick. An .hta is never guessed: it starts only when the catalog's launch path names it.
_EXE_SUFFIXES = (".exe", ".bat")
# A launch file that opens in the browser; .hta is a program, never a page. On Windows `webbrowser.open` is a shell
# open: the file opens in whatever program the player associates with .html, not necessarily a browser. So
# `launch_kind`'s suffix check is the only gate: do not widen this on the assumption that "the browser" is safe.
PAGE_SUFFIXES = (".html", ".htm")
# An HTML Application: a program with the player's full rights, run by Windows' own host (`start_hta`).
HTA_SUFFIX = ".hta"
HTA_HOST_NAME = "mshta.exe"
# Which mshta.exe runs an .hta game: 32 (SysWOW64 on 64-bit Windows) or 64 (System32). 32, because:
# - it is what Windows itself runs for an .hta: on 64-bit Windows 11 the `htafile` open command is
#   `C:\Windows\SysWOW64\mshta.exe "%1" ...` (read 2026-11-11; cited as evidence only, the association is never used);
# - the games are from 2002-2004, written for 32-bit Windows, so any component one of them creates is a 32-bit
#   one, which only the 32-bit host can load. A 64-bit host gains nothing for them.
# Read from the catalog's files: Jump and Manhunt create no component at all (script and <bgsound> only), and
# neither do the .hta files of 3-d Snake and Duck Blaster; those two keep their game script in a .js file that was
# not read, so what that script creates is not known.
# With the 32-bit host on 64-bit Windows, a path a game writes as C:\Windows\system32 is read from SysWOW64.
# (64 would need more work in a 32-bit Game Chooser, where the system folder is itself redirected to SysWOW64.)
HTA_HOST_BITS = 32

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


class Started(NamedTuple):
    """What `start_game` started: "page" or "program", and the program's process id when its starter gave one."""
    kind: str
    pid: int | None


def launch_kind(path: str) -> str:
    """"page" for a launch file that is a web page (.html, .htm), otherwise "program" (an .hta is a program)."""
    return "page" if Path(path).suffix.lower() in PAGE_SUFFIXES else "program"


def is_hta(path: str) -> bool:
    """Is this launch file an HTML Application (.hta)?"""
    return Path(path).suffix.lower() == HTA_SUFFIX


def _windows_folder(function: Callable) -> str | None:
    """The folder a GetSystemDirectoryW-style function names, or None when Windows names none: it returns 0 (the
    32-bit twin on 32-bit Windows), or it writes no text.

    The buffer starts at MAX_PATH and grows when Windows says how much it needs. Not 32768: on 64-bit Windows 11
    GetSystemWow64DirectoryW given exactly that size returned 20 and wrote nothing."""
    import ctypes

    size = 260
    for _ in range(3):
        buffer = ctypes.create_unicode_buffer(size)
        length = function(buffer, size)
        if length < size:
            return (buffer.value or None) if length else None
        size = length + 1  # too small: the answer is the size needed
    return None


def _system_dirs() -> tuple[str, str | None]:
    """Windows' system folder and its 32-bit twin (None on 32-bit Windows, which has none), asked of Windows
    itself: never read from the environment or the search path, which a game or another program can change."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for function in (kernel32.GetSystemDirectoryW, kernel32.GetSystemWow64DirectoryW):
        function.argtypes = [wintypes.LPWSTR, wintypes.UINT]
        function.restype = wintypes.UINT
    system = _windows_folder(kernel32.GetSystemDirectoryW)
    if system is None:
        raise OSError(messages.HTA_HOST_MISSING)
    return system, _windows_folder(kernel32.GetSystemWow64DirectoryW)


def hta_host(bits: int | None = None,
             system_dirs: Callable[[], tuple[str, str | None]] | None = None) -> str:
    """The full path of the mshta.exe that runs .hta games: the `bits`-bit one (HTA_HOST_BITS when not given) in
    Windows' own system folder. The 32-bit host on 32-bit Windows is the one in the system folder."""
    system, wow64 = (system_dirs or _system_dirs)()
    if bits is None:
        bits = HTA_HOST_BITS
    return ntpath.join(wow64 if bits == 32 and wow64 else system, HTA_HOST_NAME)


def start_hta(path: str, cwd: str, popen: Callable | None = None,
              system_dirs: Callable[[], tuple[str, str | None]] | None = None,
              windows: bool | None = None) -> int | None:
    """Start an .hta game: Windows' own mshta.exe, with the file's full path as its one argument and the game's
    folder as the working directory. Returns the host's process ID.

    An .hta is a program with the player's full rights, so it never goes through the browser or the shell's
    file association (whatever program the player has for .hta), and the host is never looked for in the game's
    folder or on the search path. Raises OSError (plain words from `messages`) on a system that is not Windows
    or a Windows without mshta.exe; any other OSError from starting it passes through.
    """
    if windows is None:
        windows = sys.platform == "win32"
    if not windows:
        raise OSError(messages.HTA_NEEDS_WINDOWS)
    host = hta_host(system_dirs=system_dirs)
    if popen is None:
        import subprocess
        popen = subprocess.Popen
    try:
        process = popen([host, ntpath.abspath(path)], cwd=cwd)
    except FileNotFoundError:
        raise OSError(messages.HTA_HOST_MISSING) from None
    return getattr(process, "pid", None)


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
               start: Callable[[str, str], int | None] | None = None,
               hta_start: Callable[[str, str], int | None] | None = None, hta_allowed: bool = False) -> Started:
    """Start a game's launch file and return what it started: a page opens in the web browser, an .hta runs in
    Windows' mshta.exe through `start_hta` (a program, like an exe), anything else runs through `start_program`.

    An .hta gets the host only with `hta_allowed`, which the caller sets when the catalog's launch path named the
    file (`library_actions.may_start_hta`). Without it nothing starts an .hta: OSError (`messages.HTA_NOT_ALLOWED`).
    It never reaches `start_program`, whose administrator-prompt route is a shell open, which for an .hta is the
    file association.
    Raises OSError if the browser can't be opened; LaunchCancelled and the OSError from `start` or `hta_start`
    pass through."""
    kind = launch_kind(path)
    if kind == "page":
        if open_page is None:
            import webbrowser
            open_page = webbrowser.open
        if open_page(page_address(path)) is False:
            raise OSError("The web browser couldn't be opened.")
        return Started(kind, None)
    if is_hta(path):
        if not hta_allowed:
            raise OSError(messages.HTA_NOT_ALLOWED)
        return Started(kind, (hta_start or start_hta)(path, cwd))
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
