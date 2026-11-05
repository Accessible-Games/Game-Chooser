"""Installers that must run: started silently or with the player, then found and tracked.

A run-mode installer puts the game wherever it likes. Before it starts, the engine snapshots the
target folder and Windows' installed-programs list (the Uninstall keys). Afterwards the game's
folder is the target if the installer wrote there, else the folder a new Uninstall entry names,
else the one the player points to. Files created or changed during the install window are recorded
as shipped, so a shared folder like `Program Files\\<Developer>` never has another game's files
claimed. If none are found, the game is recorded as untracked.

Run-mode updates and vendor uninstalls can't control what the installer touches, so the player's
files (not recorded, or changed since install) are copied to `work/setaside/<id>` first and put back
afterwards if the installer changed or removed them; the installer's version is kept as
`<name>.new`. The set-aside copies are only deleted once every file is back.
"""

import os
import re
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Protocol

from . import messages
from .elevation import RunningProcess
from .errors import Cancelled, ElevationDeclined, InstallFailed, StepError
from .launch import find_launch_exe
from .messages import Progress
from .paths import STAGING_DIR_NAME, is_engine_dir, norm_key, unique_path
from .records import InstallRecord, InstalledSource, RecordedFile, hash_tree
from .safety import _prune_empty_dirs, is_link
from .sources import hash_file
from .steps import StepContext, run_file_steps, run_system_steps, undo_steps

DEFAULT_SILENT_ARGS = {
    "inno": '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="{dir}"',
    "nsis": "/S /D={dir}",
    "msi": '/qn TARGETDIR="{dir}"',
}

# File times can lag time.time() and FAT drives keep them to 2 seconds.
CLOCK_SLACK = 2.0
POLL_SECONDS = 0.2
# msiexec's "succeeded, restart needed" codes.
_RESTART_CODES = frozenset({1641, 3010})
_ERROR_ELEVATION_REQUIRED = 740


def silent_args_for(installer: dict) -> str | None:
    """The entry's silent_args, else the type's default; None means the player drives the installer."""
    if installer.get("silent_args") is not None:
        return installer["silent_args"]
    return DEFAULT_SILENT_ARGS.get(installer.get("type"))


def installer_command(file: Path, installer: dict, target: Path) -> tuple[str, str]:
    args = (silent_args_for(installer) or "").replace("{dir}", str(target))
    if installer.get("type") == "msi":
        return "msiexec", (f'/i "{file}" ' + args).rstrip()
    return str(file), args


class InstallerUI(Protocol):
    """What the player sees while an installer runs. Called on the engine's worker thread."""

    def announce_target(self, title: str, target: Path) -> None:
        """Tell the player where to install (and copy it to the clipboard); return once they've read it."""

    def wait_for_installer(self, title: str, finished: threading.Event) -> bool:
        """Show "Waiting for the installer" with Done. True for Done or `finished` set; False to cancel."""

    def locate_folder(self, title: str) -> Path | None:
        """Ask the player where the game went. None if they don't know."""


# ---- watching the installer -----------------------------------------------------------------------------


class ProcessWatch(Protocol):
    def poll(self, timeout: float) -> bool:
        """True once the installer and every program it started have exited; else False after `timeout`."""

    def exit_code(self) -> int | None:
        """The installer's own exit code, None if it is still running."""

    def terminate(self) -> None:
        """Stop the installer and what it started, as far as Windows allows."""

    def close(self) -> None:
        """Release the handles. Safe to call more than once."""


class _JobWatch:
    """An unelevated installer, in a job object: done when no process in the job is left.

    Installers often start a child and exit at once, so the installer's own exit isn't the end.
    WindowsElevator starts the program inside its own job (`process.job`), so even a child started
    at once is in it. A process without one is put in a job here, which misses children it started
    before that; if that fails too, only the installer itself is waited for.
    """

    def __init__(self, process: RunningProcess):
        import pywintypes
        import win32api
        import win32con
        import win32job
        self._process = process
        self._job = getattr(process, "job", None)
        self._own_job = None  # a job made here, closed here
        self._handle = None
        if self._job is not None:
            return
        try:
            self._own_job = win32job.CreateJobObject(None, "")
            self._handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
                                                False, process.pid)
            win32job.AssignProcessToJobObject(self._own_job, self._handle)
            self._job = self._own_job
        except pywintypes.error:
            self._job = None

    def poll(self, timeout: float) -> bool:
        import win32job
        if self._process.wait(timeout) is None:
            return False
        if self._job is None:
            return True
        info = win32job.QueryInformationJobObject(self._job, win32job.JobObjectBasicAccountingInformation)
        if info["ActiveProcesses"] == 0:
            return True
        time.sleep(timeout)
        return False

    def exit_code(self) -> int | None:
        return self._process.wait(0)

    def terminate(self) -> None:
        import win32api
        import win32job
        try:
            if self._job is not None:
                win32job.TerminateJobObject(self._job, 1)
            elif self._handle is not None:
                win32api.TerminateProcess(self._handle, 1)
        except Exception:
            pass  # Best effort: it may have just exited.

    def close(self) -> None:
        for handle in (self._own_job, self._handle):
            if handle is not None:
                try:
                    handle.Close()
                except Exception:
                    pass
        self._job = self._own_job = self._handle = None
        self._process.close()


class _HandleWatch:
    """An elevated installer: an unelevated program can't put it in a job, so its handle is waited on."""

    def __init__(self, process: RunningProcess):
        self._process = process

    def poll(self, timeout: float) -> bool:
        return self._process.wait(timeout) is not None

    def exit_code(self) -> int | None:
        return self._process.wait(0)

    def terminate(self) -> None:
        try:
            import win32api
            win32api.TerminateProcess(self._process.handle, 1)
        except Exception:
            pass  # An elevated process usually refuses this; best effort.

    def close(self) -> None:
        self._process.close()


def watch_process(process: RunningProcess, elevated: bool) -> ProcessWatch:
    return _HandleWatch(process) if elevated else _JobWatch(process)


# ---- Windows' installed-programs list --------------------------------------------------------------------

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


def _split_command(command: str) -> tuple[str, str]:
    exe = _command_path(command)
    rest = command.strip()
    rest = rest[len(exe) + 2:] if rest.startswith('"') else rest[len(exe):]
    return exe, rest.strip()


def _windows_dir() -> Path:
    return Path(os.environ.get("SystemRoot") or os.environ.get("windir") or "C:\\Windows")


def _folder_of_program(text: str) -> Path | None:
    if not text:
        return None
    path = PureWindowsPath(_command_path(text))
    if not path.drive or not path.root:
        return None  # "MsiExec.exe /X{...}": not a path into the game's folder
    folder = Path(str(path.parent))
    return None if _inside(folder, _windows_dir()) else folder


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


def _vendor_uninstaller(values: dict) -> str | None:
    return _text(values.get("QuietUninstallString")) or _text(values.get("UninstallString")) or None


# ---- folders and the install window ----------------------------------------------------------------------


def _walk(root: Path) -> Iterator[tuple[str, Path]]:
    """(display path with forward slashes, full path) of every file under `root`.

    Never enters links or Game Chooser's own folders, the way hash_tree walks.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not is_engine_dir(d) and not is_link(Path(dirpath) / d)]
        for name in filenames:
            full = Path(dirpath) / name
            if not is_link(full):
                yield full.relative_to(root).as_posix(), full


def snapshot_folder(root: Path) -> set[str]:
    return {norm_key(rel) for rel, _ in _walk(Path(root))}


def _claimed(folder: Path, start: float, end: float, before: set[str] | None, slack: float) -> dict[str, str]:
    """norm_key -> display path of each file created or changed in [start - slack, end + slack], or new
    since `before`."""
    low, high = start - slack, end + slack
    found = {}
    for rel, full in _walk(Path(folder)):
        key = norm_key(rel)
        try:
            st = full.stat()
        except OSError:
            continue
        times = [st.st_mtime, getattr(st, "st_birthtime", None)]
        if any(t is not None and low <= t <= high for t in times) or (before is not None and key not in before):
            found[key] = rel
    return found


def files_created_during(folder: Path, start: float, end: float, before: set[str] | None,
                         slack: float = 2.0) -> list[str]:
    return sorted(_claimed(folder, start, end, before, slack))


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _inside(path: Path, folder: Path) -> bool:
    """True if `path` is `folder` or inside it (compared case-insensitively)."""
    path, folder = (Path(os.path.normcase(os.path.abspath(p))) for p in (path, folder))
    return path == folder or folder in path.parents


def _unsafe_game_folder(folder: Path, ctx, catalog_id: str) -> bool:
    """Folders that can't be one game's folder: a drive, a library or anything holding one, a
    system folder, or another catalog game's folder."""
    folder = Path(os.path.abspath(folder))
    if folder == Path(folder.anchor):
        return True
    special = [lib["path"] for lib in ctx.libraries if lib.get("path")]
    special += [os.environ.get(v) for v in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                                            "ProgramData", "USERPROFILE", "APPDATA", "LOCALAPPDATA")]
    if any(p and _inside(Path(p), folder) for p in special):
        return True
    if _inside(folder, _windows_dir()):
        return True
    owner = ctx.records.owner_of_folder(folder, ctx.libraries)
    return owner is not None and owner.catalog_id != catalog_id


def _narrowed(folder: Path, claimed: dict[str, str]) -> tuple[Path, dict[str, str]]:
    """The deepest folder holding every claimed file, with the claimed paths made relative to it."""
    parents = [os.path.dirname(rel) for rel in claimed.values()]
    common = os.path.commonpath(parents).replace("\\", "/") if parents and all(parents) else ""
    if not common:
        return folder, claimed
    cut = len(common) + 1
    return folder / common, {norm_key(rel[cut:]): rel[cut:] for rel in claimed.values()}


def _remove_new_files(folder: Path, before: set[str], existed: bool) -> None:
    """Remove the files that weren't in `folder` before the installer ran; never one that was."""
    if not folder.is_dir():
        return
    for rel, full in list(_walk(folder)):
        if norm_key(rel) not in before:
            try:
                full.unlink()
            except OSError:
                continue
            _prune_empty_dirs(full.parent, folder)
    if not existed:
        try:
            folder.rmdir()
        except OSError:
            pass


# ---- running and waiting -----------------------------------------------------------------------------------


def _check(cancel: Callable[[], bool]) -> None:
    if cancel():
        raise Cancelled()


def _interactive_ui(ctx, installer: dict, title: str):
    """The UI for an installer the player drives, or None for a silent one."""
    if silent_args_for(installer) is not None:
        return None
    if ctx.installer_ui is None:
        raise InstallFailed(messages.INSTALLER_NEEDS_PLAYER.format(title=title))
    return ctx.installer_ui


def _start(ctx, exe: str, args: str, elevated: bool, cwd: Path | None, title: str) -> tuple[RunningProcess, bool]:
    try:
        return ctx.elevator.start(exe, args, elevated, cwd), elevated
    except ElevationDeclined as e:
        raise InstallFailed(messages.ELEVATION_DECLINED.format(what=title)) from e
    except StepError as e:
        if not elevated and getattr(e.__cause__, "winerror", None) == _ERROR_ELEVATION_REQUIRED:
            return _start(ctx, exe, args, True, cwd, title)  # Its manifest asks for administrator rights.
        raise InstallFailed(str(e)) from e


def _run_and_wait(ctx, title: str, exe: str, args: str, elevated: bool, cwd: Path | None, ui,
                  progress, cancel) -> tuple[int | None, bool]:
    """Start the program and wait: for its whole process tree when silent, for the player's Done when `ui`.

    Returns (exit code or None if it was still running, True if it finished by itself). Raises
    Cancelled after stopping it when the job or the player cancels.
    """
    process, elevated = _start(ctx, exe, args, elevated, cwd, title)
    try:
        watch = ctx.process_watch(process, elevated)
    except BaseException:
        process.close()
        raise
    finished = threading.Event()
    stop = threading.Event()

    def watcher() -> None:
        try:
            while not stop.is_set():
                if watch.poll(POLL_SECONDS):
                    break
                if ui is not None and cancel():
                    break  # The player is in the UI's wait; finishing it lets the engine see the cancel.
        finally:
            finished.set()

    thread = threading.Thread(target=watcher, name="installer-watch", daemon=True)
    thread.start()
    try:
        progress(Progress("waiting_for_installer"))
        if ui is not None:
            going_on = ui.wait_for_installer(title, finished)
        else:
            going_on = True
            while not finished.wait(POLL_SECONDS):
                if cancel():
                    going_on = False
                    break
        if not going_on or cancel():
            watch.terminate()
            raise Cancelled()
        done = finished.is_set()  # Not set: the player pressed Done while it still runs.
        return (watch.exit_code() if done else None), done
    finally:
        stop.set()
        thread.join(POLL_SECONDS * 10)
        watch.close()


def _installer_failed(code: int | None, ui) -> bool:
    """A silent installer's non-zero exit is a failure; for an interactive one, the player's Done decides."""
    return ui is None and code is not None and code != 0 and code not in _RESTART_CODES


def _code_notes(title: str, code: int | None) -> list[str]:
    if code in _RESTART_CODES:
        return [messages.INSTALLER_RESTART_NOTE.format(title=title)]
    if code:
        return [messages.INSTALLER_CODE_NOTE.format(title=title, code=code)]
    return []


def _elevated(installer: dict) -> bool:
    return bool(installer.get("elevation"))


# ---- the engine's entry points -----------------------------------------------------------------------------


@dataclass
class RunItem:
    """One downloaded installer, as the engine hands it over."""

    catalog_id: str
    kind: str
    title: str
    file: Path
    installer: dict
    steps: list[dict]
    source: InstalledSource
    launch_hint: str | None


def run_installer_untracked(file: Path, installer: dict, target: Path, ctx, progress, cancel,
                            title: str | None = None) -> None:
    """Run an installer whose files aren't tracked (runtimes). A cancelled or failed silent run removes
    what it added to `target`."""
    title = title or Path(target).name
    ui = _interactive_ui(ctx, installer, title)
    target = Path(target)
    existed = target.exists()
    target.mkdir(parents=True, exist_ok=True)
    before = snapshot_folder(target)
    if ui is not None:
        ui.announce_target(title, target)
    exe, args = installer_command(file, installer, target)
    try:
        code, _ = _run_and_wait(ctx, title, exe, args, _elevated(installer), Path(file).parent, ui, progress, cancel)
        if _installer_failed(code, ui):
            raise InstallFailed(messages.INSTALLER_FAILED.format(title=title, code=code))
    except BaseException:
        _remove_new_files(target, before, existed)
        raise


def install(ctx, item: RunItem, target: Path, library_name: str, library_path: Path,
            progress, cancel) -> tuple[InstallRecord, list[str]]:
    """Run the installer for a fresh install, find and track the game, and save its record.

    Returns the record and notes for the player.
    """
    title = item.title
    ui = _interactive_ui(ctx, item.installer, title)
    _check(cancel)
    if ui is not None:
        ui.announce_target(title, target)
    keys_before = snapshot_uninstall_keys(ctx.uninstall_reader)
    existed = target.exists()
    before = snapshot_folder(target)
    exe, args = installer_command(item.file, item.installer, target)
    start = time.time()
    placed = False
    try:
        code, _ = _run_and_wait(ctx, title, exe, args, _elevated(item.installer), item.file.parent, ui,
                                progress, cancel)
        if _installer_failed(code, ui):
            raise InstallFailed(messages.INSTALLER_FAILED.format(title=title, code=code))
        progress(Progress("finishing"))
        found = _find_folder(ctx, item, target, before, start, time.time(), keys_before, ui)
        if found is None:
            if code and code not in _RESTART_CODES:
                raise InstallFailed(messages.INSTALLER_FAILED.format(title=title, code=code))
            raise InstallFailed(messages.INSTALL_FOLDER_NOT_FOUND.format(title=title))
        folder, in_target, vendor = found
        record, notes = _track_and_record(ctx, item, folder, in_target, before, start, vendor,
                                          library_name, library_path)
        placed = True
    except BaseException:
        if not placed:
            _remove_new_files(target, before, existed)
        raise
    notes = [*_code_notes(title, code), *notes]
    return record, [*notes, *_apply_system_steps(ctx, record, item.steps)]


def _find_folder(ctx, item: RunItem, target: Path, before: set[str], start: float, end: float,
                 keys_before: dict, ui) -> tuple[Path, bool, str | None] | None:
    """(the game's folder, whether it is the target, the vendor's uninstall command) or None."""
    new_entries = new_uninstall_entries(keys_before, snapshot_uninstall_keys(ctx.uninstall_reader))
    if _claimed(target, start, end, before, CLOCK_SLACK):
        return target, True, _matching_uninstaller(new_entries, target)
    for values in new_entries:
        folder = folder_from_uninstall_entry(values)
        if folder is not None and folder.is_dir() and not _unsafe_game_folder(folder, ctx, item.catalog_id):
            return folder, False, _matching_uninstaller(new_entries, folder)
    if ui is None:
        return None
    folder = ui.locate_folder(item.title)
    if folder is None or not Path(folder).is_dir() or _unsafe_game_folder(Path(folder), ctx, item.catalog_id):
        return None
    return Path(folder), False, _matching_uninstaller(new_entries, Path(folder))


def _matching_uninstaller(new_entries: list[dict], folder: Path) -> str | None:
    """The uninstaller of the new entry for `folder`; or of the only new entry, if it names no folder."""
    for values in new_entries:
        named = folder_from_uninstall_entry(values)
        if named is not None and _same(named, folder):
            return _vendor_uninstaller(values)
    if len(new_entries) == 1 and folder_from_uninstall_entry(new_entries[0]) is None:
        return _vendor_uninstaller(new_entries[0])
    return None


def _track_and_record(ctx, item: RunItem, folder: Path, in_target: bool, before: set[str], start: float,
                      vendor: str | None, library_name: str, library_path: Path
                      ) -> tuple[InstallRecord, list[str]]:
    """Run the file steps, record the files the install window produced, and save the record."""
    claimed = _claimed(folder, start, time.time(), before if in_target else None, CLOCK_SLACK)
    if claimed and not in_target:
        folder, claimed = _narrowed(folder, claimed)  # Not the rest of a shared developer folder.
    if item.steps:
        try:
            run_file_steps(item.steps, folder)
        except StepError as e:
            raise InstallFailed(str(e)) from e
        if claimed:  # What the steps wrote is the install's too.
            claimed = _claimed(folder, start, time.time(), before if in_target else None, CLOCK_SLACK)
    tracked = bool(claimed)
    exe = find_launch_exe(folder, item.launch_hint, _only(ctx.exclude, folder, claimed if tracked else None))
    if exe is None:
        raise InstallFailed(messages.NO_EXE.format(title=item.title) + "\n\n" + messages.DEFENDER_NOTE)
    stored_library, stored_folder = _where(folder, library_name, library_path, ctx.libraries)
    record = InstallRecord(
        catalog_id=item.catalog_id, kind=item.kind, library_name=stored_library, folder=stored_folder,
        launch_path=exe.relative_to(folder).as_posix(), source=item.source, extras={},
        files=_recorded(folder, claimed, item.catalog_id), steps=[], method="run", tracked=tracked,
        vendor_uninstaller=vendor,
    )
    ctx.records.save(record)
    return record, ([] if tracked else [messages.UNTRACKED_NOTE.format(title=item.title)])


def _only(exclude: Callable[[Path], bool], folder: Path, claimed: dict[str, str] | None) -> Callable[[Path], bool]:
    """The scanner's exclusions, plus, for a tracked install, every file the install didn't write."""
    if claimed is None:
        return exclude

    def excluded(path: Path) -> bool:
        return exclude(path) or norm_key(Path(path).relative_to(folder).as_posix()) not in claimed
    return excluded


def _recorded(folder: Path, claimed: dict[str, str], owner: str) -> dict[str, RecordedFile]:
    return {key: RecordedFile(rel, hash_file(folder / rel), owner) for key, rel in claimed.items()}


def _where(folder: Path, library_name: str, library_path: Path, libraries: list[dict]) -> tuple[str, str]:
    """(library name, folder relative to it) for a folder inside a library; ("", absolute path) otherwise.

    The requested library is tried first.
    """
    candidates = [{"name": library_name, "path": str(library_path)}, *libraries]
    for lib in candidates:
        if not lib.get("path") or not lib.get("name"):
            continue
        root = Path(os.path.abspath(lib["path"]))
        absolute = Path(os.path.abspath(folder))
        if _inside(absolute, root) and not _same(absolute, root):
            return lib["name"], Path(os.path.relpath(absolute, root)).as_posix()
    return "", str(Path(os.path.abspath(folder)))


def _apply_system_steps(ctx, record: InstallRecord, steps: list[dict], keep: list[dict] = ()) -> list[str]:
    """Run the source's registry/register/run steps in the game's folder and record them.

    Applied steps in `keep` that are still wanted stay as they are; the others are undone first.
    Returns notes for undo failures. A failing step leaves the game recorded and raises InstallFailed.
    """
    from .records import record_folder
    folder = record_folder(record, ctx.libraries)
    step_ctx = StepContext(Path(os.path.abspath(folder)), ctx.elevator, ctx.registry)
    wanted = [_bare(s) for s in steps]
    kept = [s for s in keep if _bare(s) in wanted]
    notes = undo_steps([s for s in keep if _bare(s) not in wanted], step_ctx)
    record.steps = kept
    ctx.records.save(record)
    try:
        applied = run_system_steps([s for s in steps if _bare(s) not in [_bare(k) for k in kept]], step_ctx)
    except StepError as e:
        raise InstallFailed("\n\n".join([str(e), *getattr(e, "undo_failures", []), *notes])) from e
    record.steps = kept + applied
    ctx.records.save(record)
    return notes


def _bare(step: dict) -> dict:
    return {k: v for k, v in step.items() if k != "undo"}


# ---- the player's files, set aside ---------------------------------------------------------------------


def _player_files(record: InstallRecord, game_dir: Path) -> list[str]:
    """Files in the game folder the record doesn't cover: not recorded, or changed since install.

    Game Chooser's backup folders are the player's too. Links are never entered.
    """
    found = []
    for dirpath, dirnames, filenames in os.walk(game_dir):
        dirnames[:] = [d for d in dirnames if d != STAGING_DIR_NAME and not is_link(Path(dirpath) / d)]
        for name in filenames:
            full = Path(dirpath) / name
            if is_link(full):
                continue
            rel = full.relative_to(game_dir).as_posix()
            recorded = record.files.get(norm_key(rel))
            if recorded is None or hash_file(full) != recorded.sha256:
                found.append(rel)
    return found


def _setaside_root(ctx, catalog_id: str) -> Path:
    return Path(ctx.work_root) / "setaside" / catalog_id


def require_no_setaside(ctx, catalog_id: str, title: str) -> None:
    """Refuse while copies of the player's files from an earlier try are still set aside."""
    root = _setaside_root(ctx, catalog_id)
    if root.exists() and any(p.is_file() for p in root.rglob("*")):
        raise InstallFailed(messages.SETASIDE_LEFT_OVER.format(title=title, path=root))


class _SetAside:
    """Copies of the player's files in `work/setaside/<id>`, put back after an installer has run."""

    def __init__(self, root: Path, game_dir: Path, files: list[str]):
        self.root = root
        self.game_dir = game_dir
        self.files = files
        self.renamed: dict[str, str] = {}  # rel -> where the installer's version was moved

    @classmethod
    def copy(cls, ctx, record: InstallRecord, game_dir: Path, title: str) -> "_SetAside":
        require_no_setaside(ctx, record.catalog_id, title)
        root = _setaside_root(ctx, record.catalog_id)
        shutil.rmtree(root, ignore_errors=True)
        files = _player_files(record, game_dir)
        try:
            for rel in files:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(game_dir / rel, root / rel)
                if hash_file(root / rel) != hash_file(game_dir / rel):
                    raise OSError(f"Copy differs: {rel}")
        except OSError as e:
            shutil.rmtree(root, ignore_errors=True)  # Only copies: every original is untouched.
            raise InstallFailed(messages.FILES_NOT_SET_ASIDE.format(title=title)) from e
        return cls(root, game_dir, files)

    def restore(self, title: str) -> list[str]:
        """Put back each file that is now missing or different, keeping the installer's as `<name>.new`.

        The copies are deleted only once every file is back; otherwise a note says where they are.
        """
        failed = []
        for rel in self.files:
            saved, current = self.root / rel, self.game_dir / rel
            try:
                wanted = hash_file(saved)
                if current.is_file() and not is_link(current) and hash_file(current) == wanted:
                    continue
                if os.path.lexists(current):
                    if current.is_dir() or is_link(current):
                        raise OSError(f"Not a file: {rel}")
                    moved = unique_path(current.with_name(current.name + ".new"))
                    os.replace(current, moved)
                    self.renamed[rel] = moved.relative_to(self.game_dir).as_posix()
                current.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(saved, current)
                if hash_file(current) != wanted:
                    raise OSError(f"Copy differs: {rel}")
            except OSError:
                failed.append(rel)
        if failed:
            return [messages.SETASIDE_KEPT.format(title=title, path=self.root)]
        shutil.rmtree(self.root, ignore_errors=True)
        return []


def update(ctx, item: RunItem, record: InstallRecord, game_dir: Path, progress, cancel
           ) -> tuple[InstallRecord, list[str]]:
    """Run a newer installer over an installed game, keeping the player's files.

    Returns the saved record and notes for the player.
    """
    title = item.title
    ui = _interactive_ui(ctx, item.installer, title)
    _check(cancel)
    aside = _SetAside.copy(ctx, record, game_dir, title)
    if ui is not None:
        ui.announce_target(title, game_dir)
    keys_before = snapshot_uninstall_keys(ctx.uninstall_reader)
    before = snapshot_folder(game_dir)
    exe, args = installer_command(item.file, item.installer, game_dir)
    start = time.time()
    try:
        code, _ = _run_and_wait(ctx, title, exe, args, _elevated(item.installer), item.file.parent, ui,
                                progress, cancel)
        if _installer_failed(code, ui):
            raise InstallFailed(messages.INSTALLER_FAILED.format(title=title, code=code))
        progress(Progress("finishing"))
        if item.steps:
            try:
                run_file_steps(item.steps, game_dir)
            except StepError as e:
                raise InstallFailed(str(e)) from e
        # Decided before the player's files go back: a copy put back is new on disk, not the installer's.
        claimed = _claimed(game_dir, start, time.time(), before, CLOCK_SLACK)
    except BaseException as e:
        _remove_new_files(game_dir, before, True)
        kept = aside.restore(title)
        if kept and isinstance(e, InstallFailed):
            raise InstallFailed("\n\n".join([e.message, *kept])) from e
        raise
    notes = aside.restore(title)

    for rel, moved in aside.renamed.items():
        if norm_key(rel) in claimed:
            claimed[norm_key(moved)] = moved
    player = {norm_key(rel) for rel in aside.files}
    for key in player:
        claimed.pop(key, None)
    files = _recorded(game_dir, claimed, item.catalog_id)
    on_disk = hash_tree(game_dir)
    for key, old in record.files.items():
        # A shipped file that wasn't the player's is the installer's now, even one it rewrote
        # keeping its old dates.
        if key in files or key in player or key not in on_disk:
            continue
        rel, sha = on_disk[key]
        files[key] = old if sha == old.sha256 else RecordedFile(rel, sha, item.catalog_id)

    exe = (find_launch_exe(game_dir, item.launch_hint, _only(ctx.exclude, game_dir, files))
           or find_launch_exe(game_dir, record.launch_path, _only(ctx.exclude, game_dir, files)))
    record.files = files
    record.source = item.source
    record.method = "run"
    record.tracked = True
    record.extras = {}
    if exe is not None:
        record.launch_path = exe.relative_to(game_dir).as_posix()
    new_entries = new_uninstall_entries(keys_before, snapshot_uninstall_keys(ctx.uninstall_reader))
    record.vendor_uninstaller = _matching_uninstaller(new_entries, game_dir) or record.vendor_uninstaller
    record.previous = None  # Undo can't run an older installer under these rules.
    record.previous_unavailable = None
    record.uninstalled = False
    ctx.records.save(record)
    notes = [*_code_notes(title, code), *notes]
    return record, [*notes, *_apply_system_steps(ctx, record, item.steps, keep=record.steps)]


def run_vendor_uninstaller(ctx, record: InstallRecord, game_dir: Path, title: str, progress, cancel) -> list[str]:
    """Run the vendor's uninstaller with the player's files set aside, and put them back afterwards.

    The caller then deletes the recorded leftovers and keeps (or recycles) the player's files.
    Returns notes for the player.
    """
    exe, args = _split_command(record.vendor_uninstaller)
    installer = ((record.source.install or {}).get("installer") or {})
    _check(cancel)
    program = PureWindowsPath(exe)
    if program.drive and program.root and not os.path.lexists(exe):
        # Gone (deleted, or replaced by an update from an archive): Game Chooser's record does the work.
        return [messages.VENDOR_UNINSTALLER_MISSING.format(title=title)]
    if game_dir.is_dir():
        aside = _SetAside.copy(ctx, record, game_dir, title)
    else:
        require_no_setaside(ctx, record.catalog_id, title)
        aside = _SetAside(_setaside_root(ctx, record.catalog_id), game_dir, [])
    try:
        code, finished = _run_and_wait(ctx, title, exe, args, _elevated(installer), None, ctx.installer_ui,
                                       progress, cancel)
        if finished and code not in (None, 0, *_RESTART_CODES):
            raise InstallFailed(messages.UNINSTALLER_FAILED.format(title=title, code=code))
    except BaseException as e:
        kept = aside.restore(title)
        if kept and isinstance(e, InstallFailed):
            raise InstallFailed("\n\n".join([e.message, *kept])) from e
        raise
    return aside.restore(title)
