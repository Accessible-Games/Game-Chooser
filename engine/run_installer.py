"""Installers that must run: started silently or with the player, then found and tracked.

A run-mode installer puts the game wherever it likes. Before it starts, the engine snapshots the
target folder and Windows' installed-programs list (the Uninstall keys, engine.uninstall_keys).
Afterwards the game's folder is the target if the installer wrote there, else the folder of the new
Uninstall entry that is the game's, else the one the player points to. Files created during the
install window are recorded as shipped. A folder that also holds other content (a shared
`Program Files\\<Developer>`) is never recorded as covering it: the install is then untracked.

Run-mode updates (and the Repair and Undo that run a recorded installer again, engine.run_actions)
and vendor uninstalls can't control what the installer touches, so the player's
files (not recorded, or changed since install) are copied to `work/.setaside/<id>` first and put
back afterwards if the installer changed or removed them; the installer's version is kept as
`<name>.new`. The copies are deleted only when every file is back AND the installer's whole
process tree was seen to finish (engine.installer_watch); otherwise they are kept and the player is
told where.
"""

import os
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import messages
from .elevation import RunningProcess
from .errors import Cancelled, ElevationDeclined, InstallFailed, StepError, UnsafePath, check_cancel
from .installer_watch import ProcessWatch
from .launch import find_launch_exe, is_catalog_launch
from .messages import Progress
from .paths import STAGING_DIR_NAME, is_code, same_folder, walk_files, is_engine_dir, norm_key, unique_path
from .records import InstallRecord, InstalledSource, RecordedFile, hash_tree, is_runtime_owner, record_folder
from .safety import UninstallPlan, _prune_empty_dirs, _resolve_game_path, is_link, remove_unchanged
from .sources import hash_file
from .steps import FILE_ACTIONS, StepContext, run_file_steps, run_system_steps, split_steps, undo_steps
from .uninstall_keys import (folder_from_uninstall_entry, inside, is_missing_program, names_title,
                             new_uninstall_entries, snapshot_uninstall_keys, split_command, vendor_uninstaller,
                             windows_dir)

DEFAULT_SILENT_ARGS = {
    "inno": '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="{dir}"',
    "nsis": "/S /D={dir}",
    "msi": '/qn TARGETDIR="{dir}"',
}

# File times can lag time.time() and FAT drives keep them to 2 seconds.
CLOCK_SLACK = 2.0
POLL_SECONDS = 0.2
# A silent installer that has exited while programs it started still run (Inno's [Run] launching the
# game) is treated as finished after this long, if no one can press Done.
GRACE_SECONDS = 30.0
# How long a vendor uninstaller's leftover processes are waited for after the player presses Done.
SETTLE_SECONDS = 30.0
# How long a cancelled installer is given to stop.
STOP_SECONDS = 5.0
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


# ---- folders and the install window ----------------------------------------------------------------------


def _walk(root: Path) -> Iterator[tuple[str, Path]]:
    """(display path with forward slashes, full path) of every file under `root`.

    Never enters links or Game Chooser's own folders, the way hash_tree walks.
    """
    for full in walk_files(root, is_engine_dir):
        if not is_link(full):
            yield full.relative_to(root).as_posix(), full


def snapshot_folder(root: Path) -> set[str]:
    return {norm_key(rel) for rel, _ in _walk(Path(root))}


def _claimed(folder: Path, start: float, end: float, before: set[str] | None, slack: float) -> dict[str, str]:
    """norm_key -> display path of each file the install window produced.

    With `before` (the target folder): a file new since `before`, or created or changed in
    [start - slack, end + slack]. Without it (a folder that may be shared): a file created in the
    window; one created earlier isn't claimed just because something wrote to it meanwhile. Where
    creation times aren't kept, the change time decides.
    """
    low, high = start - slack, end + slack
    found = {}
    for rel, full in _walk(Path(folder)):
        key = norm_key(rel)
        try:
            st = full.stat()
        except OSError:
            continue
        born = getattr(st, "st_birthtime", None)
        if before is None:
            hit = low <= (born if born is not None else st.st_mtime) <= high
        else:
            hit = key not in before or any(t is not None and low <= t <= high for t in (st.st_mtime, born))
        if hit:
            found[key] = rel
    return found


def _personal_folders() -> list[str]:
    home = os.environ.get("USERPROFILE")
    folders = [os.environ.get(v) for v in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                                           "ProgramData", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                                           "OneDrive", "PUBLIC")]
    if home:
        folders += [os.path.join(home, name) for name in ("Desktop", "Documents", "Downloads", "Music",
                                                          "Pictures", "Videos", "Saved Games")]
    return [f for f in folders if f]


def _unsafe_game_folder(folder: Path, ctx, catalog_id: str) -> bool:
    """Folders that can't be one game's folder: a drive; a library, system or personal folder, or one
    holding any of them; anything under Windows; or a folder that holds, or is inside, another
    catalog game's folder."""
    folder = Path(os.path.abspath(folder))
    if folder == Path(folder.anchor) or inside(folder, windows_dir()):
        return True
    special = [lib["path"] for lib in ctx.libraries if lib.get("path")] + _personal_folders()
    if any(inside(Path(p), folder) for p in special):
        return True
    for record in ctx.records.all().values():
        other = record_folder(record, ctx.libraries)
        if record.catalog_id != catalog_id and other is not None and \
                (inside(folder, other) or inside(other, folder)):
            return True
    return False


def _narrowed(folder: Path, claimed: dict[str, str]) -> tuple[Path, dict[str, str]]:
    """The deepest folder holding every claimed file, with the claimed paths made relative to it."""
    parents = [os.path.dirname(rel) for rel in claimed.values()]
    common = os.path.commonpath(parents).replace("\\", "/") if parents and all(parents) else ""
    if not common:
        return folder, claimed
    cut = len(common) + 1
    return folder / common, {norm_key(rel[cut:]): rel[cut:] for rel in claimed.values()}


def _holds_other_content(folder: Path, claimed: dict[str, str]) -> bool:
    """True if a top-level file or folder of `folder` holds none of the install's files but does hold
    something (another game, or anything else that was already there)."""
    tops = {key.split("/", 1)[0] for key in claimed}
    for entry in os.scandir(folder):
        if norm_key(entry.name) in tops or is_engine_dir(entry.name):
            continue
        path = Path(entry.path)
        if is_link(path) or not entry.is_dir(follow_symlinks=False) or any(_walk(path)):
            return True
    return False


def _spans_older_folders(folder: Path, claimed: dict[str, str], since: float) -> bool:
    """True if the claimed files span several top-level entries of `folder` and one of those already held
    a file before `since`: then the window's files there may be another program's (a save written
    meanwhile), not the installer's."""
    tops = {rel.replace("\\", "/").split("/", 1)[0] for rel in claimed.values()}
    if len(tops) < 2:
        return False
    for top in tops:
        path = folder / top
        if not path.is_dir() or is_link(path):
            continue
        for _, full in _walk(path):
            try:
                st = full.stat()
            except OSError:
                continue
            born = getattr(st, "st_birthtime", None)
            if (born if born is not None else st.st_mtime) < since:
                return True
    return False


def _set_aside_new_files(folder: Path, before: set[str], dest: Path, title: str) -> list[str]:
    """Move the files that weren't in `folder` before an installer ran over an installed game into `dest`,
    keeping their layout, and say so. They may be the installer's, or the player's (a save the running game
    wrote during the window): nothing is deleted, and nothing already in `dest` from an earlier try is
    replaced. A file that can't be moved stays where it is."""
    moved = False
    if folder.is_dir():
        for rel, full in list(_walk(folder)):
            if norm_key(rel) in before:
                continue
            try:
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                os.replace(full, unique_path(dest / rel))
            except OSError:
                continue
            moved = True
            _prune_empty_dirs(full.parent, folder)
    return [messages.NEW_FILES_SET_ASIDE.format(title=title, path=dest)] if moved else []


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


class _StillRunning(Exception):
    """Cancelled, but the installer wouldn't stop: nothing may be cleaned up or put back under it."""


@dataclass
class _Outcome:
    code: int | None  # the installer's own exit code, None if it was still running
    tree_done: bool  # the installer and everything it started were seen to finish


def _interactive(ctx, installer: dict, title: str) -> bool:
    """True when the player drives the installer; refuses if no one can."""
    if silent_args_for(installer) is not None:
        return False
    if ctx.installer_ui is None:
        raise InstallFailed(messages.INSTALLER_NEEDS_PLAYER.format(title=title))
    return True


def _not_started(message: str) -> InstallFailed:
    """The program didn't start, so nothing of it can still be running (`tree_done`)."""
    error = InstallFailed(message)
    error.tree_done = True
    return error


def _start(ctx, exe: str, args: str, elevated: bool, cwd: Path | None, title: str) -> tuple[RunningProcess, bool]:
    try:
        return ctx.elevator.start(exe, args, elevated, cwd), elevated
    except ElevationDeclined as e:
        raise _not_started(messages.ELEVATION_DECLINED.format(what=title)) from e
    except StepError as e:
        if not elevated and getattr(e.__cause__, "winerror", None) == _ERROR_ELEVATION_REQUIRED:
            return _start(ctx, exe, args, True, cwd, title)  # Its manifest asks for administrator rights.
        raise _not_started(str(e)) from e


def _run_and_wait(ctx, title: str, exe: str, args: str, elevated: bool, cwd: Path | None,
                  progress, cancel, settle: float = 0.0) -> _Outcome:
    """Start the program and wait until it and everything it started have exited, or the player
    presses Done (the UI is offered whenever there is one, silent runs included).

    Without a UI, a silent program that has exited while what it started runs on is treated as done
    after GRACE_SECONDS. After an early Done, `settle` seconds more are given to the process tree.
    Raises Cancelled (with `tree_done`) once a cancelled program has stopped, or _StillRunning if it
    won't stop.
    """
    if cancel():
        # Cancelled before it started (the player told where it goes cancelled, or the app quit and released
        # that prompt): it never runs. Nothing of it can be running, so set-aside files go back as usual.
        error = Cancelled()
        error.tree_done = True
        raise error
    ui = ctx.installer_ui
    process, elevated = _start(ctx, exe, args, elevated, cwd, title)
    try:
        watch = ctx.process_watch(process, elevated)
    except BaseException:
        process.close()
        raise
    finished = threading.Event()
    stop = threading.Event()
    state = {"done": False, "main_exited": None}

    def watcher() -> None:
        try:
            while not stop.is_set():
                if watch.poll(POLL_SECONDS):
                    state["done"] = True
                    break
                if state["main_exited"] is None and watch.exit_code() is not None:
                    state["main_exited"] = time.monotonic()
                # cancel() is the job's flag check; it is safe to call from this thread too.
                if ui is not None and cancel():
                    break  # The player is in the UI's wait; finishing it lets the engine see the cancel.
        finally:
            finished.set()

    thread = threading.Thread(target=watcher, name="installer-watch", daemon=True)
    thread.start()
    try:
        progress(Progress("waiting_for_installer"))
        if ui is not None:
            going_on = ui.wait_for_installer(title, finished) and not cancel()
        else:
            going_on = True
            while not finished.wait(POLL_SECONDS):
                if cancel():
                    going_on = False
                    break
                exited = state["main_exited"]
                if exited is not None and time.monotonic() - exited >= GRACE_SECONDS:
                    break
        stop.set()
        thread.join(POLL_SECONDS * 10)
        if not going_on:
            watch.terminate()
            stopped = _poll_for(watch, STOP_SECONDS, never=lambda: False)
            if not stopped:
                raise _StillRunning()
            error = Cancelled()
            error.tree_done = watch.tree_observed()
            raise error
        done = state["done"] or (settle > 0 and _poll_for(watch, settle, cancel))
        return _Outcome(watch.exit_code(), done and watch.tree_observed())
    finally:
        stop.set()
        thread.join(POLL_SECONDS * 10)
        watch.close()


def _poll_for(watch: ProcessWatch, seconds: float, never: Callable[[], bool]) -> bool:
    """Poll `watch` on this thread for up to `seconds` (stopping early if `never()` turns true)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not never():
        if watch.poll(POLL_SECONDS):
            return True
    return False


def _run_installer(ctx, title: str, file: Path, installer: dict, target: Path, interactive: bool,
                   progress, cancel) -> _Outcome:
    """Run the installer into `target` and wait for it (see _run_and_wait).

    A silent installer's non-zero exit, other than a restart, raises InstallFailed (with `tree_done`);
    for an interactive one, the player's Done decides.
    """
    exe, args = installer_command(file, installer, target)
    outcome = _run_and_wait(ctx, title, exe, args, _elevated(installer), Path(file).parent, progress, cancel)
    if not interactive and outcome.code not in (None, 0, *_RESTART_CODES):
        error = InstallFailed(messages.INSTALLER_FAILED.format(title=title, code=outcome.code))
        error.tree_done = outcome.tree_done
        raise error
    return outcome


def _code_notes(title: str, code: int | None) -> list[str]:
    if code in _RESTART_CODES:
        return [messages.INSTALLER_RESTART_NOTE.format(title=title)]
    if code:
        return [messages.INSTALLER_CODE_NOTE.format(title=title, code=code)]
    return []


def _elevated(installer: dict) -> bool:
    return bool(installer.get("elevation"))


# ---- file steps on a live folder ---------------------------------------------------------------------------


def _check_file_steps(steps: list[dict], folder: Path, allowed: set[str], title: str) -> None:
    """Refuse file steps that would copy over, move or delete a file the install didn't write.

    Paths the steps can't use at all are left for run_file_steps to refuse.
    """
    for step in steps:
        action = step.get("action")
        if action not in FILE_ACTIONS:
            continue
        rels = [step.get("path")] if action == "delete" else [step.get("from"), step.get("to")]
        if action == "copy":
            rels = rels[1:]  # copying only reads its source
        for rel in rels:
            if not isinstance(rel, str):
                continue
            try:
                path, linked = _resolve_game_path(folder, rel)
            except (UnsafePath, OSError):
                continue
            if linked:
                continue
            if path.is_file():
                touched = [norm_key(path.relative_to(folder).as_posix())]
            elif path.is_dir():
                touched = [norm_key((path / r).relative_to(folder).as_posix()) for r, _ in _walk(path)]
            else:
                continue
            if any(key not in allowed for key in touched):
                raise InstallFailed(messages.STEP_TOUCHES_OTHER_FILES.format(title=title))


def _run_live_file_steps(steps: list[dict], folder: Path, allowed: set[str], title: str) -> None:
    if not any(s.get("action") in FILE_ACTIONS for s in steps):
        return
    _check_file_steps(steps, folder, allowed, title)
    try:
        run_file_steps(steps, folder)
    except StepError as e:
        raise InstallFailed(str(e)) from e


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
                            title: str | None = None) -> list[str]:
    """Run an installer whose files aren't tracked (runtimes). A cancelled or failed silent run removes
    what it added to `target`. Returns notes for the player (a restart it asked for, an exit code)."""
    title = title or Path(target).name
    interactive = _interactive(ctx, installer, title)
    target = Path(target)
    existed = target.exists()
    target.mkdir(parents=True, exist_ok=True)
    before = snapshot_folder(target)
    if interactive:
        ctx.installer_ui.announce_target(title, target)
    try:
        outcome = _run_installer(ctx, title, file, installer, target, interactive, progress, cancel)
    except _StillRunning as e:
        raise InstallFailed(messages.INSTALLER_STILL_RUNNING.format(title=title)) from e
    except BaseException:
        _remove_new_files(target, before, existed)
        raise
    return _code_notes(title, outcome.code)


def install(ctx, item: RunItem, target: Path, library_id: str, library_path: Path,
            progress, cancel) -> tuple[InstallRecord, list[str]]:
    """Run the installer for a fresh install, find and track the game, and save its record.

    Returns the record and notes for the player.
    """
    title = item.title
    interactive = _interactive(ctx, item.installer, title)
    check_cancel(cancel)
    if interactive:
        ctx.installer_ui.announce_target(title, target)
    keys_before = snapshot_uninstall_keys(ctx.uninstall_reader)
    existed = target.exists()
    before = snapshot_folder(target)
    start = time.time()
    placed = False
    try:
        code = _run_installer(ctx, title, item.file, item.installer, target, interactive, progress, cancel).code
        progress(Progress("finishing"))
        found = _find_folder(ctx, item, target, before, start, time.time(), keys_before)
        if found is None:
            if code and code not in _RESTART_CODES:
                raise InstallFailed(messages.INSTALLER_FAILED.format(title=title, code=code))
            raise InstallFailed(messages.INSTALL_FOLDER_NOT_FOUND.format(title=title))
        folder, in_target, vendor = found
        record, notes = _track_and_record(ctx, item, folder, in_target, before, start, vendor,
                                          library_id, library_path)
        placed = True
    except _StillRunning as e:
        raise InstallFailed(messages.INSTALLER_STILL_RUNNING.format(title=title)) from e
    except BaseException:
        if not placed:
            _remove_new_files(target, before, existed)
        raise
    notes = [*_code_notes(title, code), *notes]
    return record, [*notes, *_apply_system_steps(ctx, record, item.steps)]


def _find_folder(ctx, item: RunItem, target: Path, before: set[str], start: float, end: float,
                 keys_before: dict) -> tuple[Path, bool, str | None] | None:
    """(the game's folder, whether it is the target, the vendor's uninstall command) or None.

    A new Uninstall entry is only taken as the game's if its folder holds files from the install
    window, and then by preference: its name is the title, its folder has the launch path, or its
    folder has a new program. So a redistributable the installer chained (VC++, OpenAL) isn't.
    """
    new_entries = new_uninstall_entries(keys_before, snapshot_uninstall_keys(ctx.uninstall_reader))
    if _claimed(target, start, end, before, CLOCK_SLACK):
        return target, True, _matching_uninstaller(new_entries, target, item.title)
    ranked = []
    for index, values in enumerate(new_entries):
        folder = folder_from_uninstall_entry(values)
        if folder is None or not folder.is_dir() or _unsafe_game_folder(folder, ctx, item.catalog_id):
            continue
        claimed = _claimed(folder, start, end, None, CLOCK_SLACK)
        if not claimed:
            continue
        if names_title(values, item.title):
            rank = 0
        elif item.launch_hint and norm_key(item.launch_hint) in claimed:
            rank = 1
        elif any(key.endswith(".exe") for key in claimed):
            rank = 2
        else:
            continue
        ranked.append((rank, index, folder, values))
    if ranked:
        _, _, folder, values = min(ranked, key=lambda r: r[:2])
        return folder, False, vendor_uninstaller(values)
    if ctx.installer_ui is None:
        return None
    folder = ctx.installer_ui.locate_folder(item.title)
    if folder is None or not Path(folder).is_dir() or _unsafe_game_folder(Path(folder), ctx, item.catalog_id):
        return None
    return Path(folder), False, _matching_uninstaller(new_entries, Path(folder), item.title)


def _matching_uninstaller(new_entries: list[dict], folder: Path, title: str) -> str | None:
    """The uninstaller of the new entry for `folder`, or of the new entry named like the game."""
    for values in new_entries:
        named = folder_from_uninstall_entry(values)
        if named is not None and same_folder(named, folder):
            return vendor_uninstaller(values)
    for values in new_entries:
        if folder_from_uninstall_entry(values) is None and names_title(values, title):
            return vendor_uninstaller(values)
    return None


def _track_and_record(ctx, item: RunItem, folder: Path, in_target: bool, before: set[str], start: float,
                      vendor: str | None, library_id: str, library_path: Path
                      ) -> tuple[InstallRecord, list[str]]:
    """Record the files the install window produced, run the file steps on them, and save the record."""
    window_before = before if in_target else None
    claimed = _claimed(folder, start, time.time(), window_before, CLOCK_SLACK)
    if claimed and not in_target:
        folder, claimed = _narrowed(folder, claimed)  # Not the rest of a shared developer folder.
        if _holds_other_content(folder, claimed) or _spans_older_folders(folder, claimed, start - CLOCK_SLACK):
            claimed = {}  # Its files can't be told from what else is there.
    notes = []
    if claimed:
        _run_live_file_steps(item.steps, folder, set(claimed), item.title)
        claimed = _claimed(folder, start, time.time(), window_before, CLOCK_SLACK)  # what the steps wrote too
    else:
        notes.append(messages.UNTRACKED_NOTE.format(title=item.title))
        if any(s.get("action") in FILE_ACTIONS for s in item.steps):
            notes.append(messages.FILE_STEPS_SKIPPED.format(title=item.title))
    tracked = bool(claimed)
    exe = find_launch_exe(folder, item.launch_hint, _only(ctx.exclude, folder, claimed if tracked else None))
    if exe is None:
        raise InstallFailed(messages.NO_EXE.format(title=item.title) + "\n\n" + messages.DEFENDER_NOTE)
    stored_library, stored_folder = _where(folder, library_id, library_path, ctx.libraries)
    record = InstallRecord(
        catalog_id=item.catalog_id, kind=item.kind, library_id=stored_library, folder=stored_folder,
        launch_path=exe.relative_to(folder).as_posix(), source=item.source, extras={},
        launch_from_catalog=is_catalog_launch(folder, exe, item.launch_hint),
        files=_recorded(folder, claimed, item.catalog_id), steps=[], method="run", tracked=tracked,
        vendor_uninstaller=vendor,
    )
    ctx.records.save(record)
    return record, notes


def _only(exclude: Callable[[Path], bool], folder: Path, claimed: dict | None) -> Callable[[Path], bool]:
    """The scanner's exclusions, plus, for a tracked install, every file the install didn't write."""
    if claimed is None:
        return exclude

    def excluded(path: Path) -> bool:
        return exclude(path) or norm_key(Path(path).relative_to(folder).as_posix()) not in claimed
    return excluded


def _recorded(folder: Path, claimed: dict[str, str], owner: str) -> dict[str, RecordedFile]:
    return {key: RecordedFile(rel, hash_file(folder / rel), owner) for key, rel in claimed.items()}


def _where(folder: Path, library_id: str, library_path: Path, libraries: list[dict]) -> tuple[str, str]:
    """(library id, folder relative to it) for a folder inside a library; ("", absolute path) otherwise.

    The requested library is tried first.
    """
    candidates = [{"id": library_id, "path": str(library_path)}, *libraries]
    for lib in candidates:
        if not lib.get("path") or not lib.get("id"):
            continue
        root = Path(os.path.abspath(lib["path"]))
        absolute = Path(os.path.abspath(folder))
        if inside(absolute, root) and not same_folder(absolute, root):
            return lib["id"], Path(os.path.relpath(absolute, root)).as_posix()
    return "", str(Path(os.path.abspath(folder)))


def _step_ctx(ctx, record: InstallRecord) -> StepContext:
    return StepContext(Path(os.path.abspath(record_folder(record, ctx.libraries))), ctx.elevator, ctx.registry)


def _apply_system_steps(ctx, record: InstallRecord, steps: list[dict], notes: list[str] = ()) -> list[str]:
    """Run the source's new registry/register/run steps in the game's folder and add them to the record.

    Returns `notes` (the undo failures of the steps undone once the installer had run). A failing step leaves
    the game recorded and raises InstallFailed, with `notes` after its words.
    """
    try:
        applied = run_system_steps(steps, _step_ctx(ctx, record))
    except StepError as e:
        raise InstallFailed("\n\n".join([str(e), *getattr(e, "undo_failures", []), *notes])) from e
    record.steps = record.steps + applied
    ctx.records.save(record)
    return list(notes)


# ---- the player's files, set aside ---------------------------------------------------------------------


def _player_files(record: InstallRecord, game_dir: Path) -> dict[str, str]:
    """rel -> sha256 of the files in the game folder the record doesn't cover: not recorded, or
    changed since install. Game Chooser's backup folders are the player's too. Links are never entered."""
    found = {}
    if not game_dir.is_dir():
        return found
    for full in walk_files(game_dir, lambda name: name == STAGING_DIR_NAME):
        if is_link(full):
            continue
        rel = full.relative_to(game_dir).as_posix()
        recorded = record.files.get(norm_key(rel))
        sha = hash_file(full)
        if recorded is None or sha != recorded.sha256:
            found[rel] = sha
    return found


def _setaside_root(ctx, catalog_id: str) -> Path:
    # A dot name can't be a catalog id, so no game's work folder is ever this one.
    return Path(ctx.work_root) / ".setaside" / catalog_id


def _new_files_root(ctx, catalog_id: str) -> Path:
    """Where a failed or cancelled run over an installed game moves the files that appeared during its window
    (beside the set-aside copies; an id never has a dot, so it is no other game's). Never removed by the engine:
    a later run neither clears it nor refuses because of it."""
    return Path(ctx.work_root) / ".setaside" / f"{catalog_id}.new"


def require_no_setaside(ctx, catalog_id: str, title: str) -> None:
    """Refuse while copies of the player's files from an earlier try are still set aside."""
    root = _setaside_root(ctx, catalog_id)
    if root.exists() and any(p.is_file() for p in root.rglob("*")):
        raise InstallFailed(messages.SETASIDE_LEFT_OVER.format(title=title, path=root))


class _SetAside:
    """Copies of the player's files in `work/.setaside/<id>`, put back after an installer has run."""

    def __init__(self, root: Path, game_dir: Path, files: list[str]):
        self.root = root
        self.game_dir = game_dir
        self.files = files
        self.renamed: dict[str, str] = {}  # rel -> where the installer's version was moved

    @classmethod
    def copy(cls, ctx, catalog_id: str, game_dir: Path, files: list[str], title: str) -> "_SetAside":
        require_no_setaside(ctx, catalog_id, title)
        root = _setaside_root(ctx, catalog_id)
        shutil.rmtree(root, ignore_errors=True)
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

    def kept_note(self, title: str, text: str = messages.SETASIDE_KEPT) -> list[str]:
        return [text.format(title=title, path=self.root)] if self.files else []

    def restore(self, title: str, tree_done: bool) -> list[str]:
        """Put back each file that is now missing or different, keeping the installer's as `<name>.new`.

        The copies are deleted only when every file is back and the installer's process tree was
        seen to finish (`tree_done`); otherwise a note says where they are.
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
            return self.kept_note(title)
        if not tree_done:  # Every file is back, but something may still write: the copies stay (R21).
            return self.kept_note(title, messages.SETASIDE_KEPT_TO_BE_SAFE)
        shutil.rmtree(self.root, ignore_errors=True)
        return []


# Passed as `previous` to update(): the record's undo point stays as it is.
KEEP_UNDO_POINT = object()


def update(ctx, item: RunItem, record: InstallRecord, game_dir: Path, progress, cancel, *, previous,
           spare_steps: list[dict] = (), undo_of: dict | None = None) -> tuple[InstallRecord, list[str]]:
    """Run an installer over an installed game, keeping the player's files: a newer one for Update,
    the recorded one for Repair, the previous one for Undo.

    The player's .exe/.dll/.ocx files aren't set aside: like any update, the installer's code
    replaces them without a copy. `previous` is the record's undo point afterwards (KEEP_UNDO_POINT
    leaves it as it is); it is saved with the installer's files, before the system steps run.
    Applied steps the installer's source no longer has are undone once it has run, before Undo removes
    their files, so a failed or cancelled run leaves every step alone; those in `spare_steps` (the
    extras' steps, for extras the caller applies next) aren't. The new steps run after the installer.
    The record's extras are left as they are: the caller applies and records them next, so if that
    fails, a retry still knows which extras the game has.

    For Undo, `undo_of` is the undo point being undone. A file the newer version recorded that this
    run didn't claim, and that is unchanged, is removed, unless the older version recorded that path
    too (an installer skipping a file it found in place): then it is kept, recorded as it is. A file
    that can't be removed stays recorded, and a note says so. The older version's vendor uninstaller
    comes back unless this run registered one.
    Returns the saved record and notes for the player.
    """
    title = item.title
    interactive = _interactive(ctx, item.installer, title)
    check_cancel(cancel)
    player = _player_files(record, game_dir)
    aside = _SetAside.copy(ctx, item.catalog_id, game_dir, [r for r in player if not is_code(r)], title)
    player_code = {rel: sha for rel, sha in player.items() if is_code(rel)}
    if interactive:
        ctx.installer_ui.announce_target(title, game_dir)
    keys_before = snapshot_uninstall_keys(ctx.uninstall_reader)
    before = snapshot_folder(game_dir)
    start = time.time()
    tree_done = False
    try:
        outcome = _run_installer(ctx, title, item.file, item.installer, game_dir, interactive, progress, cancel)
        tree_done = outcome.tree_done
        progress(Progress("finishing"))
        if item.steps:
            theirs_now = {norm_key(r) for r in player}  # never the installer's, whatever their dates
            claimed = _claimed(game_dir, start, time.time(), before, CLOCK_SLACK)
            allowed = (set(claimed) | set(record.files)) - theirs_now
            _run_live_file_steps(item.steps, game_dir, allowed, title)
        # Decided before the player's files go back: a copy put back is new on disk, not the installer's.
        claimed = _claimed(game_dir, start, time.time(), before, CLOCK_SLACK)
    except _StillRunning as e:
        # Nothing is removed or put back while it may still be writing; the copies stay.
        raise InstallFailed("\n\n".join([messages.INSTALLER_STILL_RUNNING.format(title=title),
                                         *aside.kept_note(title)])) from e
    except BaseException as e:
        tree_done = getattr(e, "tree_done", tree_done)
        # A file that appeared during the window may be the player's (the running game writing a save), so it
        # is moved beside the set-aside copies, never deleted; the copies go back as usual (R21 still decides
        # when they are discarded).
        kept = _set_aside_new_files(game_dir, before, _new_files_root(ctx, item.catalog_id), title)
        kept += aside.restore(title, tree_done)
        if kept and isinstance(e, InstallFailed):
            raise InstallFailed("\n\n".join([e.message, *kept])) from e
        if kept and isinstance(e, Cancelled):
            raise InstallFailed("\n\n".join([messages.INSTALLER_CANCELLED.format(title=title), *kept])) from e
        raise
    notes = aside.restore(title, tree_done)
    # Applied steps not wanted any more are undone now, before Undo removes the files they name.
    kept_steps, unwanted, new_steps = split_steps(record.steps, item.steps, spare_steps)
    step_notes = undo_steps(unwanted, _step_ctx(ctx, record))
    if unwanted:
        record.steps = kept_steps
        ctx.records.save(record)

    for rel, moved in aside.renamed.items():
        if norm_key(rel) in claimed:
            claimed[norm_key(moved)] = moved
    # The player's files stay theirs: those put back, and code the installer left as it was.
    theirs = {norm_key(rel) for rel in aside.files}
    theirs |= {norm_key(rel) for rel, sha in player_code.items()
               if (game_dir / rel).is_file() and hash_file(game_dir / rel) == sha}
    for key in theirs:
        claimed.pop(key, None)
    files = _recorded(game_dir, claimed, item.catalog_id)
    for key, old in record.files.items():
        # A private COM copy the installer left as it was stays the runtime's, even inside the window.
        if is_runtime_owner(old.owner) and key in files and files[key].sha256 == old.sha256:
            files[key] = old
    on_disk = hash_tree(game_dir)
    older = (undo_of or {}).get("files") or {}
    not_removed = []
    for key, old in record.files.items():
        # A shipped file that wasn't the player's is the installer's now, even one it rewrote
        # keeping its old dates. The player's files aren't recorded: they stay theirs.
        if key in files or key in theirs or key not in on_disk:
            continue
        rel, sha = on_disk[key]
        if undo_of is not None and sha == old.sha256:
            if key in older:  # the older version had this path too: never removed, recorded as it is
                files[key] = RecordedFile(rel, sha, older[key].get("owner") or old.owner)
                continue
            try:
                if remove_unchanged(game_dir, rel, sha):
                    continue  # the newer version's, unchanged: Undo removes it, as an update would
            except OSError:
                not_removed.append(rel)
        files[key] = old if sha == old.sha256 else RecordedFile(rel, sha, item.catalog_id)
    if not_removed:
        notes.append(messages.UNDO_FILES_NOT_REMOVED.format(title=title))

    exe = (find_launch_exe(game_dir, item.launch_hint, _only(ctx.exclude, game_dir, files))
           or find_launch_exe(game_dir, record.launch_path, _only(ctx.exclude, game_dir, files)))
    record.files = files
    record.source = item.source
    record.method = "run"
    record.tracked = True
    if exe is not None:
        record.launch_path = exe.relative_to(game_dir).as_posix()
        record.launch_from_catalog = is_catalog_launch(game_dir, exe, item.launch_hint)
    new_entries = new_uninstall_entries(keys_before, snapshot_uninstall_keys(ctx.uninstall_reader))
    if undo_of is not None and "vendor_uninstaller" in undo_of:
        fallback = undo_of["vendor_uninstaller"]
    else:
        fallback = record.vendor_uninstaller
    record.vendor_uninstaller = _matching_uninstaller(new_entries, game_dir, title) or fallback
    if previous is not KEEP_UNDO_POINT:
        record.previous = previous
        record.previous_unavailable = None
    record.uninstalled = False
    ctx.records.save(record)
    notes = [*_code_notes(title, outcome.code), *notes]
    return record, [*notes, *_apply_system_steps(ctx, record, new_steps, step_notes)]


# ---- vendor uninstallers ---------------------------------------------------------------------------------


def keep_vendor_uninstaller(plan: UninstallPlan, command: str, game_dir: Path) -> UninstallPlan:
    """`plan` without the vendor uninstaller's own files (`unins000.exe`, `unins000.dat`, ...), so it can
    still run after Game Chooser has removed the rest."""
    program = Path(split_command(command)[0])
    if not inside(program, game_dir):
        return plan
    parent_rel = Path(os.path.relpath(program.parent, game_dir)).as_posix()
    folder = "" if parent_rel == "." else norm_key(parent_rel)
    stem = program.stem.lower()

    def its_own(rel: str) -> bool:
        parent, _, name = norm_key(rel).rpartition("/")
        return parent == folder and name.split(".", 1)[0] == stem

    kept = [rel for rel in plan.delete if its_own(rel)]
    return UninstallPlan([r for r in plan.delete if r not in kept], plan.player_files,
                         {k: v for k, v in plan.hashes.items() if k not in kept})


def run_vendor_uninstaller(ctx, record: InstallRecord, game_dir: Path, title: str, progress, cancel) -> list[str]:
    """Run the vendor's uninstaller for what Game Chooser's record can't undo (its Windows entries),
    with the player's files set aside, and put them back once it has finished.

    Called after the record's own files are removed. The copies are put back (and deleted) only if
    the uninstaller's whole process tree was seen to finish; otherwise they stay set aside and a
    note says where. Returns notes for the player.
    """
    command = record.vendor_uninstaller
    if is_missing_program(command):
        # Gone (deleted, or replaced by an update from an archive): the record did the work.
        return [messages.VENDOR_UNINSTALLER_MISSING.format(title=title)]
    exe, args = split_command(command)
    installer = ((record.source.install or {}).get("installer") or {})
    check_cancel(cancel)
    aside = _SetAside.copy(ctx, record.catalog_id, game_dir, list(_player_files(record, game_dir)), title)
    progress(Progress("vendor_uninstaller"))
    try:
        outcome = _run_and_wait(ctx, title, exe, args, _elevated(installer), None, progress, cancel,
                                settle=SETTLE_SECONDS)
    except _StillRunning as e:
        raise InstallFailed("\n\n".join([messages.INSTALLER_STILL_RUNNING.format(title=title),
                                         *aside.kept_note(title, messages.SETASIDE_NOT_PUT_BACK)])) from e
    except BaseException as e:
        if getattr(e, "tree_done", False):  # It stopped, or never started: the files go back.
            kept = aside.restore(title, True)
        else:
            kept = aside.kept_note(title, messages.SETASIDE_NOT_PUT_BACK)
        if kept and isinstance(e, InstallFailed):
            raise InstallFailed("\n\n".join([e.message, *kept])) from e
        if kept and isinstance(e, Cancelled):
            raise InstallFailed("\n\n".join([messages.UNINSTALLER_CANCELLED.format(title=title), *kept])) from e
        raise
    notes = [messages.VENDOR_UNINSTALLER_RAN.format(title=title)]
    if outcome.code not in (None, 0, *_RESTART_CODES):
        notes.append(messages.UNINSTALLER_FAILED.format(title=title, code=outcome.code))
    if outcome.tree_done:
        return notes + aside.restore(title, True)
    return notes + aside.kept_note(title, messages.SETASIDE_NOT_PUT_BACK)
