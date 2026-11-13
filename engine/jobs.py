"""Install jobs: a request goes in, and either the game is placed and recorded or nothing is left behind.

A fresh install builds the whole game in `<library>/.gamechooser-staging/<id>/`: download, unpack,
file steps, extras, exe detection and hashing all happen there. The finished tree is moved into the
library in one rename, and only then do the system steps (registry, register, run) run, against the
final folder. If they fail they are undone and the folder goes back to staging to be removed.

Update, repair, undo, adding extras, and installing over a folder the game's own record owns build
the incoming files in the same staging folder, then change the game folder only through the
three-checksum planner (engine.safety), which never deletes or overwrites a player's file without
a backup. If changing the folder fails partway, the record is saved to match what is on disk, with
the backups made so far, and the version it names stays the old one.

Which way a game is installed is decided by the source that was actually downloaded: an installer
that must run goes to engine.run_installer (install, update, and a vendor uninstaller at uninstall);
a web game is never installed here. Repair and Undo follow the recorded source they put back: an
installer that must run goes to engine.run_actions, an archive is rebuilt in staging as above.
A game whose installer runs gets its extras after the installer: they are downloaded and unpacked
before it starts, so a bad download changes nothing, and applied over its folder once it is tracked.

A game's missing runtimes are installed first, before it is downloaded (engine.runtimes): shared,
machine-wide, or with "Register shared components system-wide" off, a COM runtime is copied into the
staged game folder with a manifest beside its exe (Repair and Undo make them again), or for a game
whose installer runs, copied into its tracked folder afterwards and recorded as the runtime's. Once
the game's exe is known, its runtimes' DLL and OCX files are copied beside it too, and recorded as
theirs (engine.runtimes).

A game an installer put outside the libraries is staged under the work folder instead, since its
own folder's parent may be Program Files or shared with other games. Game Chooser never runs
elevated, so a folder there that needs administrator permission is refused before anything changes.
"""

import errno
import logging
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from exception_manager import ExceptionManager

from . import messages, run_actions, run_installer, runtimes
from .entries import (current_platform, extras_of, game_fields, is_mirror_url, is_web_game, mirror_url_for,
                      platform_sources, runs_installer, title_of)
from .errors import EngineError, InstallFailed, NoVerifiedSource, StepError, UnsafePath, check_cancel
from .folders import library_label, target_folder
from .launch import find_launch_exe, is_catalog_launch
from .messages import Progress
from .paths import STAGING_DIR_NAME, is_engine_dir, norm_key, safe_join, same_folder, walk_files
from .records import InstalledSource, InstallRecord, RecordedFile, RecordStore, hash_tree, record_folder
from .recycle import send_to_recycle_bin
from .installer_watch import ProcessWatch, watch_process
from .run_installer import InstallerUI, RunItem
from .runtimes import _clear, _clear_or_fail
from .uninstall_keys import UninstallReader, _winreg_reader
from .safety import (ApplyResult, UpdatePlan, _resolve_game_path, apply_plan, apply_uninstall,
                     plain_file, plan_uninstall, plan_update, preflight, restore_backups)
from .sources import Fetched, fetch_exact, fetch_verified, hash_file
from .steps import StepContext, _bare, run_system_steps, split_steps, undo_steps

log = logging.getLogger(__name__)

ACTIONS = ("install", "update", "repair", "undo", "extras", "uninstall")

# Windows error codes an OSError may carry instead of (or as well as) an errno.
_WINERROR_DISK_FULL = {39, 112}      # ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL
_WINERROR_LOCKED = {5, 32, 33}       # ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
_WINERROR_VANISHED = {2, 3}          # ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND

# Install methods whose files Game Chooser unpacked and recorded itself.
EXTRACT_METHODS = frozenset({"zip", "7z", "installer", "cab", "tar", "none"})
# Run-mode installs are recorded with method "run". When tracked, these actions work on them.
RUN_ACTIONS = frozenset(ACTIONS)


@dataclass
class InstallRequest:
    action: str
    catalog_id: str
    library_id: str = ""
    library_path: Path | None = None
    extras: tuple[str, ...] = ()
    delete_player_data: bool = False


@dataclass
class JobResult:
    action: str
    catalog_id: str
    title: str
    game: dict | None
    notes: list[str]
    backups: int = 0
    kept_player_files: int = 0


def _default_exclude() -> Callable[[Path], bool]:
    return ExceptionManager().should_auto_exclude


@dataclass
class EngineContext:
    entries: dict[str, dict]
    records: RecordStore
    fetcher: object
    extractor: object
    elevator: object
    registry: object
    work_root: Path
    runtimes_root: Path
    libraries: list[dict]
    register_shared_components: bool = True
    platform: str = "windows"
    today: Callable[[], date] = date.today
    installer_ui: InstallerUI | None = None
    recycle: Callable[[list[Path]], None] = send_to_recycle_bin
    exclude: Callable[[Path], bool] = field(default_factory=_default_exclude)
    # How a started installer is watched (a job object, or the handle when elevated).
    process_watch: Callable[[object, bool], ProcessWatch] = watch_process
    # Reads one Uninstall key location (hive, key, view) -> {subkey: values}; read-only.
    uninstall_reader: UninstallReader = _winreg_reader
    # Who owns a runtime folder and who is running Game Chooser (engine.runtimes.WindowsSecurity).
    security: object = field(default_factory=runtimes.WindowsSecurity)


# Downloads one catalog item (the game or an extra) into an empty work folder.
Fetch = Callable[[dict, Path], Fetched]


def _library_label(library_path: Path, libraries: list[dict]) -> str:
    for lib in libraries:
        if lib.get("path") and same_folder(Path(lib["path"]), library_path):
            return library_label(lib)
    return str(library_path)


def blocked_reason(entry: dict, library_path: Path, records: RecordStore, libraries: list[dict]) -> str | None:
    """Why the game can't be installed into this library, or None.

    A folder already at the target that this game's own record doesn't own belongs to the player,
    and is never installed over.
    """
    target = target_folder(entry, library_path, records, libraries)
    if not os.path.lexists(target):
        return None
    owner = records.owner_of_folder(target, libraries)
    if owner is not None and owner.catalog_id == entry["id"]:
        return None
    return messages.FOLDER_EXISTS.format(folder=target.name, library=_library_label(library_path, libraries))


def _hide(path: Path) -> None:
    """Give `path` the Windows hidden attribute. Best effort: does nothing elsewhere or on failure."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        attributes = os.stat(path).st_file_attributes | stat.FILE_ATTRIBUTE_HIDDEN
        ctypes.windll.kernel32.SetFileAttributesW(str(path), attributes)
    except (OSError, AttributeError):
        pass


def _file_states(root: Path) -> dict[str, tuple[int, int, int]]:
    """norm_key -> (file id, size, mtime) for every file under `root`, walked the way hash_tree walks."""
    states = {}
    for full in walk_files(root, is_engine_dir):
        st = full.stat()
        states[norm_key(full.relative_to(root).as_posix())] = (st.st_ino, st.st_size, st.st_mtime_ns)
    return states


def _launch_hint(entry: dict, platform: str) -> str | None:
    return ((entry.get("launch") or {}).get(platform) or {}).get("path")


def _installed_source(entry: dict, fetched: Fetched, platform: str) -> InstalledSource:
    return runtimes.installed_source(entry, fetched, _launch_hint(entry, platform))


def _recorded_launch(recorded: InstalledSource, entry: dict, platform: str) -> str | None:
    """The launch hint the recorded source was installed with (today's, for records that predate it)."""
    if recorded.install is None:
        return _launch_hint(entry, platform)
    return _launch_hint(entry, platform) or recorded.launch


class _NoUnpackSpec(Exception):
    """A recorded file can't be rebuilt: the record predates kept install specs and the catalog
    no longer lists it."""


def _steps(fetched: Fetched) -> list[dict]:
    return list(fetched.source.get("steps") or [])


@dataclass
class _StagedGame:
    """A game tree built in staging, ready to be placed."""

    tree: Path
    game: Fetched
    extras: list[tuple[dict, Fetched]]
    owners: dict[str, str]  # norm_key -> extra id, for files an extra wrote last
    launch_path: str
    launch_from_catalog: bool
    dependency_notes: list[str] = field(default_factory=list)  # runtimes it had to go without
    private_ids: list[str] = field(default_factory=list)  # runtimes it has private COM copies of
    copy_ids: list[str] = field(default_factory=list)  # runtimes whose files are copied beside its exe
    copy_notes: list[str] = field(default_factory=list)  # runtime files that couldn't be copied beside its exe

    def system_steps(self) -> list[dict]:
        """The game's steps, then each extra's, as one list (the shape `record.steps` is built from)."""
        return _steps(self.game) + [s for _, f in self.extras for s in _steps(f)]

    def notes(self) -> list[str]:
        fetched = [f.note for f in [self.game, *(f for _, f in self.extras)] if f.note]
        return list(dict.fromkeys([*fetched, *self.dependency_notes, *self.copy_notes]))

    def record_into(self, rec: InstallRecord, files: dict[str, RecordedFile], steps: list[dict]) -> None:
        """Write what an installed game's record takes from this tree once it is applied (Update, Repair, Undo):
        its files and steps as applied, its launch file, its runtimes and how it was unpacked."""
        rec.files = files
        rec.steps = steps
        rec.launch_path = self.launch_path
        rec.launch_from_catalog = self.launch_from_catalog
        rec.private_runtimes = self.private_ids
        rec.runtime_copies = self.copy_ids
        rec.method = (self.game.source.get("install") or {}).get("method", "")


class InstallEngine:
    def __init__(self, ctx: EngineContext):
        self.ctx = ctx

    def run(self, req: InstallRequest, progress: Callable[[Progress], None],
            cancel: Callable[[], bool]) -> JobResult:
        """Carry out `req`. Raises InstallFailed (with plain words for the player) or Cancelled.

        A file error anywhere in the job (the drive is full, a file is locked or gone) has had its cleanup
        and rollback done where it happened; here it gets its plain words (see plain_file_error).
        """
        try:
            result = self._run(req, progress, cancel)
            result.notes = list(dict.fromkeys(result.notes))  # one download note for several changed files
            return result
        except OSError as e:
            log.warning("File error during %s of %s", req.action, req.catalog_id, exc_info=True)
            raise InstallFailed(plain_file_error(e, self._title_of(req))) from e

    def _title_of(self, req: InstallRequest) -> str:
        entry = self.ctx.entries.get(req.catalog_id)
        if entry is not None:
            return title_of(entry)
        rec = self.ctx.records.load(req.catalog_id)
        return Path(rec.folder).name if rec is not None else req.catalog_id

    def _run(self, req: InstallRequest, progress: Callable[[Progress], None],
             cancel: Callable[[], bool]) -> JobResult:
        if req.action not in ACTIONS:
            raise ValueError(f"Unknown action: {req.action!r}")
        entry = self.ctx.entries.get(req.catalog_id)
        if req.action == "uninstall":  # A game that left the catalog can still be uninstalled.
            return self._uninstall(req, entry, progress, cancel)
        if entry is None:
            raise InstallFailed(messages.NOT_IN_CATALOG)
        if req.action == "install":
            return self._install(entry, req, progress, cancel)
        rec, game_dir = self._installed(entry, req.action)
        if req.action == "update":
            return self._update(entry, rec, game_dir, set(rec.extras), "update", progress, cancel)
        if req.action == "repair":
            return self._repair(entry, rec, game_dir, progress, cancel)
        if req.action == "undo":
            return self._undo(entry, rec, game_dir, progress, cancel)
        return self._add_extras(entry, rec, game_dir, set(req.extras), progress, cancel)

    def _library(self, req: InstallRequest) -> tuple[str, Path]:
        for lib in self.ctx.libraries:
            if req.library_path is not None:
                if lib.get("path") and same_folder(Path(lib["path"]), Path(req.library_path)):
                    return lib["id"], Path(req.library_path)
            elif lib.get("id") == req.library_id:
                return lib["id"], Path(lib["path"])
        raise ValueError(f"Not a configured library: {req.library_id!r} {req.library_path!r}")

    def _installed(self, entry: dict, action: str) -> tuple[InstallRecord, Path]:
        """The game's record and folder, for the actions that change an installed game."""
        title = title_of(entry)
        rec = self.ctx.records.load(entry["id"])
        if rec is None or rec.uninstalled:
            raise InstallFailed(messages.NOT_INSTALLED.format(title=title))
        _require_changeable(rec, title, action)
        game_dir = record_folder(rec, self.ctx.libraries)
        if game_dir is None or not game_dir.is_dir():
            raise InstallFailed(messages.GAME_FOLDER_MISSING.format(title=title, path=game_dir or rec.folder))
        return rec, game_dir

    @contextmanager
    def _staging_folder(self, library_path: Path, game_id: str) -> Iterator[Path]:
        """An empty staging folder for one job, on the library's drive, removed again when it ends."""
        staging_root = library_path / STAGING_DIR_NAME
        staging = staging_root / game_id
        try:
            # A new library's folder may not exist yet; only the staging folder inside it is hidden.
            staging_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            if same_folder(library_path, self.ctx.work_root):
                raise InstallFailed(messages.WORK_FOLDER_NOT_CREATED.format(path=library_path)) from e
            raise InstallFailed(messages.LIBRARY_FOLDER_NOT_CREATED.format(path=library_path)) from e
        _hide(staging_root)
        _clear_or_fail(staging)
        try:
            yield staging
        finally:
            _clear(staging)

    @contextmanager
    def _work_folder(self, game_id: str) -> Iterator[Path]:
        """An empty work folder (for downloads) for one job, removed again when it ends."""
        work = self.ctx.work_root / game_id
        _clear_or_fail(work)
        try:
            yield work
        finally:
            _clear(work)

    @contextmanager
    def _workspace(self, library_path: Path, game_id: str) -> Iterator[tuple[Path, Path]]:
        """Empty staging and work folders for one job, removed again when it ends."""
        with self._staging_folder(library_path, game_id) as staging, self._work_folder(game_id) as work:
            yield staging, work

    def _staging_root(self, rec: InstallRecord, game_dir: Path) -> Path:
        """Where changes to an installed game are staged: beside its folder in a library; for a folder
        outside the libraries, under the work folder (Ruling R24)."""
        return game_dir.parent if rec.library_id else self.ctx.work_root

    def _require_writable(self, rec: InstallRecord, game_dir: Path, title: str) -> None:
        """Refuse, before anything changes, a folder outside the libraries that only an administrator
        can change: Game Chooser never changes files elevated."""
        if _blocked(rec, game_dir) == "admin":
            raise InstallFailed(messages.FOLDER_NEEDS_ADMIN.format(title=title, path=game_dir))

    # ---- fetching and staging ------------------------------------------------

    def _catalog_fetch(self, progress, cancel) -> Fetch:
        """Download the catalog's current file for an item: the first source that verifies."""
        ctx = self.ctx

        def fetch(item: dict, work_dir: Path) -> Fetched:
            try:
                return fetch_verified(item, ctx.platform, work_dir, ctx.fetcher, progress, cancel)
            except NoVerifiedSource as e:
                reasons = " ".join(dict.fromkeys(e.reasons))
                raise InstallFailed(messages.NO_SOURCE.format(title=title_of(item),
                                                              reasons=reasons).strip()) from e
        return fetch

    def _exact_fetch(self, sources: dict[str, InstalledSource], progress, cancel) -> Fetch:
        """Download exactly the recorded file for an item: its recorded URL, then its mirror path.

        Raises NoVerifiedSource if neither serves that file. It is unpacked and set up with the
        recorded install spec and steps (for older records, those of the catalog's matching source;
        _NoUnpackSpec if there is none).
        """
        ctx = self.ctx

        def fetch(item: dict, work_dir: Path) -> Fetched:
            recorded = sources[item["id"]]
            catalog_item = ctx.entries.get(item["id"])
            spec = self._recorded_spec(item["id"], recorded)
            if spec is None:
                raise _NoUnpackSpec(item["id"])
            urls = _exact_urls(recorded, platform_sources(catalog_item, ctx.platform) if catalog_item else [])
            try:
                fetched = fetch_exact(urls, recorded.sha256, work_dir, ctx.fetcher, progress, cancel)
            except NoVerifiedSource as e:
                e.item = item  # which file failed: the game's own, or an extra's
                raise
            fetched.source = {**spec, **fetched.source}
            return fetched
        return fetch

    def _recorded_spec(self, item_id: str, recorded: InstalledSource) -> dict | None:
        """How a recorded file was unpacked and set up (`install` and `steps`): as recorded, or for older
        records, as the catalog's matching source says. None if neither is known."""
        if recorded.install is not None:
            return {"install": recorded.install, "steps": list(recorded.steps or [])}
        return _catalog_source(self.ctx.entries.get(item_id), recorded, self.ctx.platform)

    def _recorded_runs_installer(self, item_id: str, recorded: InstalledSource) -> bool:
        """True if putting back this recorded file means running it: it is an installer that must run."""
        spec = self._recorded_spec(item_id, recorded)
        return spec is not None and runs_installer(spec)

    def _fetch_game(self, entry: dict, work: Path, progress, cancel) -> Fetched:
        """Download the catalog's current file for the game, which decides how it is installed.

        A web game is refused before anything is downloaded.
        """
        if is_web_game(entry, self.ctx.platform):
            raise InstallFailed(messages.WEB_GAME_NOT_INSTALLED.format(title=title_of(entry)))
        folder = work / "fetched"
        _clear_or_fail(folder)
        fetched = self._catalog_fetch(progress, cancel)(entry, folder)
        if _method(fetched.source) == "url":
            raise InstallFailed(messages.WEB_GAME_NOT_INSTALLED.format(title=title_of(entry)))
        return fetched

    def _install_run(self, entry: dict, fetched: Fetched, unpacked: list[tuple[dict, Fetched, Path]],
                     staging: Path, action: str,
                     place: Callable[[RunItem, list[dict]], tuple[InstallRecord, list[str]]],
                     cancel, prior: dict | None = None,
                     private: list[runtimes.PrivateCopy] = ()) -> JobResult:
        """Hand a downloaded installer that must run to engine.run_installer, through `place` (given the
        item and the extras' steps), then apply the already unpacked extras over the game's folder, then
        the already unpacked private COM copies (the preference off) beside its launch exe."""
        title = title_of(entry)
        install = fetched.source.get("install") or {}
        item = RunItem(entry["id"], entry.get("kind", "game"), title, fetched.path,
                       dict(install.get("installer") or {}), _steps(fetched),
                       _installed_source(entry, fetched, self.ctx.platform), _launch_hint(entry, self.ctx.platform))
        record, notes = place(item, _extra_steps(unpacked))
        extra_notes, backups = self._extras_after_installer(entry, record, unpacked, staging, prior, cancel)
        private_notes = self._private_after_installer(entry, record, list(private))
        copy_notes = self._copies_after_installer(entry, record, list(private))
        return JobResult(action, entry["id"], title, game_fields_for(entry, record, self.ctx.platform),
                         [*([fetched.note] if fetched.note else []), *notes, *extra_notes, *private_notes,
                          *copy_notes], backups=backups)

    def _copies_after_installer(self, entry: dict, rec: InstallRecord, copies: list[runtimes.PrivateCopy],
                                ids: list[str] | None = None) -> list[str]:
        """Copy the DLL and OCX files of a game's runtimes beside its launch exe, once its installer has run
        (engine.runtimes; `ids`: the runtimes a rebuilt version had). An untracked folder, or one outside the
        libraries that needs administrator permission, gets none: the notes say so."""
        title = title_of(entry)
        game_dir = record_folder(rec, self.ctx.libraries)
        blocked = _blocked(rec, game_dir)
        if blocked:
            text = messages.RUNTIME_FILES_UNTRACKED if blocked == "untracked" else messages.RUNTIME_FILES_NEED_ADMIN
            return runtimes.copies_skipped_notes(self.ctx, entry, copies, title, text, path=game_dir)
        return runtimes.copies_after_installer(self.ctx, entry, rec, game_dir, copies, title, ids)

    def _private_after_installer(self, entry: dict, rec: InstallRecord,
                                 copies: list[runtimes.PrivateCopy]) -> list[str]:
        """Give a game whose installer has run its private COM copies (engine.runtimes), with their downloads'
        notes. An untracked folder, or one outside the libraries that needs administrator permission, gets
        none: the notes say it needs each shared component."""
        title = title_of(entry)
        game_dir = record_folder(rec, self.ctx.libraries)
        if not copies and not any(runtimes.is_runtime_owner(f.owner) for f in rec.files.values()):
            notes = []
        elif _blocked(rec, game_dir):
            notes = runtimes.needs_shared_notes(title, [c.runtime for c in copies])
        else:
            return [*(c.note for c in copies if c.note),
                    *runtimes.private_after_installer(self.ctx, rec, game_dir, copies, title)]
        if rec.private_runtimes is None:
            rec.private_runtimes = []
            self.ctx.records.save(rec)
        return notes

    def _extras_after_installer(self, entry: dict, rec: InstallRecord, unpacked: list[tuple[dict, Fetched, Path]],
                                staging: Path, prior: dict | None, cancel,
                                recorded: dict[str, InstalledSource] | None = None) -> tuple[list[str], int]:
        """After an installer has run: apply the game's extras over its folder and record them.

        They are all its extras now, so files an earlier extra shipped that none of them ships are
        removed if unchanged. `recorded` gives the exact sources they were fetched by (Repair, Undo);
        otherwise they are recorded as the catalog's. They are recorded before they are applied, so if
        applying them fails, a retried Update or Repair still applies them. `prior` is the undo point
        backups are kept in if there is none yet; None (a fresh install) makes none.

        An untracked folder, or one outside the libraries that needs administrator permission, gets no
        extras: they are recorded as not added, and a note names them (and says which the game needs).
        Returns notes for the player and the number of backups made.
        """
        if not unpacked and all(f.owner == rec.catalog_id or runtimes.is_runtime_owner(f.owner)
                                for f in rec.files.values()):
            if rec.extras:
                rec.extras = {}
                self.ctx.records.save(rec)
            return [], 0
        game_dir = record_folder(rec, self.ctx.libraries)
        blocked = _blocked(rec, game_dir)
        if blocked:
            text = messages.EXTRAS_SKIPPED_UNTRACKED if blocked == "untracked" else messages.EXTRAS_SKIPPED_NEEDS_ADMIN
            return self._extras_skipped(entry, rec, unpacked, text, path=game_dir), 0
        rec.extras = {x["id"]: (recorded or {}).get(x["id"]) or _installed_source(x, f, self.ctx.platform)
                      for x, f, _ in unpacked}
        self.ctx.records.save(rec)
        tree = run_actions.copy_installed_files(rec, game_dir, staging / "game" / game_dir.name, title_of(entry))
        fetched, result, notes = self._apply_extras(entry, rec, game_dir, tree, unpacked, prior, cancel, every=True)
        self.ctx.records.save(rec)
        return [*dict.fromkeys(f.note for _, f in fetched if f.note), *notes], len(result.backups)

    def _extras_skipped(self, entry: dict, rec: InstallRecord, unpacked: list[tuple[dict, Fetched, Path]],
                        text: str, **values) -> list[str]:
        """Record that the extras weren't added, and say so: which ones, and which of them the game needs."""
        rec.extras = {}
        self.ctx.records.save(rec)
        if not unpacked:
            return []
        title = title_of(entry)
        needed = {x.entry["id"] for x in extras_of(entry, self.ctx.entries, self.ctx.platform) if x.required}
        skipped = [title_of(x) for x, _, _ in unpacked]
        required = [title_of(x) for x, _, _ in unpacked if x["id"] in needed]
        notes = [text.format(title=title, extras=messages.names(skipped), **values)]
        if required:
            notes.append(messages.EXTRAS_REQUIRED_MISSING.format(title=title, extras=messages.names(required)))
        return notes

    def _fetch_and_unpack(self, item: dict, dest: Path, work_dir: Path, fetch: Fetch,
                          progress, cancel) -> tuple[Fetched, Path]:
        """Download `item` into its own empty work folder, unpack it into `dest`, and delete the download."""
        _clear_or_fail(work_dir)
        fetched = fetch(item, work_dir)
        try:
            if _method(fetched.source) == "url" or runs_installer(fetched.source):
                # The game itself is routed before it gets here, so this is an extra.
                raise InstallFailed(messages.EXTRA_NOT_SUPPORTED.format(extra=title_of(item)))
            return fetched, runtimes.unpack(self.ctx, fetched, dest, progress, cancel)
        finally:
            _clear(work_dir)

    def _stage(self, entry: dict, extras: list[dict], folder_name: str, staging: Path, work: Path,
               fetch: Fetch, launch: str | None, progress, cancel, private: list[dict] = (),
               copies: bool = True, copy_ids: list[str] | None = None,
               installed: tuple[InstallRecord, Path] | None = None) -> _StagedGame:
        """Build the finished game tree in staging: game, its file steps, then each extra and its file steps.

        `private` COM runtimes (the preference off) are copied in before the exe is detected, and given
        their manifest once it is (engine.runtimes). Then, with `copies`, the DLL and OCX files of its
        runtimes (`copy_ids`, else all it depends on) are copied beside the exe and owned by their runtime;
        `installed` is the version being replaced, whose copies stay where there is nothing to copy from."""
        fetched, root = self._fetch_and_unpack(entry, staging / "raw", work / "game", fetch, progress, cancel)
        # Named like the final folder, so exe detection can prefer an exe named after it.
        tree = staging / "game" / folder_name
        tree.parent.mkdir(parents=True)
        os.replace(root, tree)
        runtimes.run_file_steps_or_fail(_steps(fetched), tree)
        owners, fetched_extras = self._merge_extras(tree, extras, staging, work, fetch, progress, cancel)
        private_copies = runtimes.stage_private(self.ctx, list(private), tree, staging, work,
                                                self._catalog_fetch(progress, cancel), progress, cancel)

        progress(Progress("finishing"))
        copied = runtimes.is_private_file(private_copies)
        exe = find_launch_exe(tree, launch, lambda path: self.ctx.exclude(path) or copied(path))
        if exe is None:
            raise InstallFailed(messages.NO_EXE.format(title=title_of(entry)) + "\n\n" + messages.DEFENDER_NOTE)
        notes, private_ids = runtimes.finish_private(tree, exe, private_copies, title_of(entry))
        notes = [*(c.note for c in private_copies if c.note), *notes]
        copy_owners, copy_notes, copied_ids = {}, [], []
        if copies:
            copy_owners, copy_notes, copied_ids = runtimes.copies_in_tree(
                self.ctx, entry, tree, exe, private_copies, title_of(entry), copy_ids, installed)
        return _StagedGame(tree, fetched, fetched_extras, {**owners, **copy_owners}, exe.relative_to(tree).as_posix(),
                           is_catalog_launch(tree, exe, launch), notes, private_ids, copied_ids, copy_notes)

    def _merge_extras(self, tree: Path, extras: list[dict], staging: Path, work: Path, fetch: Fetch,
                      progress, cancel) -> tuple[dict[str, str], list[tuple[dict, Fetched]]]:
        """Unpack each extra over `tree` and run its file steps there.

        Returns which extra wrote each file last (norm_key -> extra id) and what was fetched.
        """
        return self._merge_unpacked(tree, self._unpack_extras(extras, staging, work, fetch, progress, cancel),
                                    cancel)

    def _unpack_extras(self, extras: list[dict], staging: Path, work: Path, fetch: Fetch,
                       progress, cancel) -> list[tuple[dict, Fetched, Path]]:
        """Download and unpack each extra into staging: (extra, what was fetched, its unpacked root)."""
        return [(extra, *self._fetch_and_unpack(extra, staging / "extras" / extra["id"],
                                                work / "extras" / extra["id"], fetch, progress, cancel))
                for extra in extras]

    def _merge_unpacked(self, tree: Path, unpacked: list[tuple[dict, Fetched, Path]],
                        cancel) -> tuple[dict[str, str], list[tuple[dict, Fetched]]]:
        """Copy each unpacked extra over `tree`, in order, and run its file steps there."""
        owners: dict[str, str] = {}
        fetched_extras = []
        for extra, x_fetched, x_root in unpacked:
            extra_id = extra["id"]
            before = _file_states(tree)
            shipped = set(_file_states(x_root))
            shutil.copytree(x_root, tree, dirs_exist_ok=True)
            runtimes.run_file_steps_or_fail(_steps(x_fetched), tree)
            after = _file_states(tree)
            for key, state in after.items():
                if key in shipped or before.get(key) != state:
                    owners[key] = extra_id
            owners = {k: v for k, v in owners.items() if k in after}
            fetched_extras.append((extra, x_fetched))
            check_cancel(cancel)
        return owners, fetched_extras

    # ---- fresh install -------------------------------------------------------

    def _install(self, entry: dict, req: InstallRequest, progress, cancel) -> JobResult:
        ctx = self.ctx
        game_id = entry["id"]
        title = title_of(entry)
        library_id, library_path = self._library(req)
        extras = [x.entry for x in extras_of(entry, ctx.entries, ctx.platform)
                  if x.required or x.entry["id"] in req.extras]

        # Refusals come before anything is downloaded or written.
        record = ctx.records.load(game_id)
        folder = record_folder(record, ctx.libraries) if record is not None else None
        if folder is not None and folder.is_dir():
            # Its own folder, even after an uninstall that kept the player's files: installed
            # over under the update rules, so nothing of the player's is lost.
            _require_changeable(record, title, "install")
            keep = set() if record.uninstalled else set(record.extras)
            return self._update(entry, record, folder, keep | set(req.extras), "install", progress, cancel)
        reason = blocked_reason(entry, library_path, ctx.records, ctx.libraries)
        if reason:
            raise InstallFailed(reason)
        target = target_folder(entry, library_path, ctx.records, ctx.libraries)
        deps = runtimes.plan_runtimes(entry, ctx)  # refuses a dependency cycle

        notes: list[str] = []
        with self._workspace(library_path, game_id) as (staging, work):
            if record is not None:
                # Its folder is gone (or its library isn't set up any more): a fresh install, whose record
                # replaces this one. The old system steps are undone first, so none is left untracked and
                # the new ones record the player's values, not Game Chooser's leftovers. Only once the new
                # install can go ahead: with the library's drive unplugged, the old game stays as it is.
                notes = self._forget_steps(record, folder or library_path / record.folder)
            notes += self._install_runtimes(deps, work, progress, cancel)
            fetched = self._fetch_game(entry, work, progress, cancel)
            if runs_installer(fetched.source):
                # Downloaded and unpacked before the installer starts, so a bad download changes nothing.
                private = runtimes.unpack_private(ctx, deps.private, staging, work,
                                                  self._catalog_fetch(progress, cancel), progress, cancel)
                unpacked = self._unpack_extras(extras, staging, work, self._catalog_fetch(progress, cancel),
                                               progress, cancel)
                result = self._install_run(entry, fetched, unpacked, staging, "install", lambda item, spare: (
                    run_installer.install(ctx, item, target, library_id, library_path, progress, cancel)), cancel,
                    private=private)
                result.notes[:0] = notes
                result.notes += _went_without(title, deps)
                return result
            staged = self._stage(entry, extras, target.name, staging, work,
                                 _prefetched(self._catalog_fetch(progress, cancel), game_id, fetched),
                                 _launch_hint(entry, ctx.platform), progress, cancel, private=deps.private)
            check_cancel(cancel)
            return self._place(entry, staged, target, library_id, library_path, staging,
                               [*notes, *_went_without(title, deps)])

    def _install_runtimes(self, deps: runtimes.RuntimePlan, work: Path, progress, cancel) -> list[str]:
        """Install the game's missing runtimes machine-wide, before the game is downloaded. Returns their
        notes (see runtimes.install_shared)."""
        return runtimes.install_shared(self.ctx, deps.shared, work / "runtimes",
                                       self._catalog_fetch(progress, cancel), progress, cancel)

    def _forget_steps(self, record: InstallRecord, folder: Path) -> list[str]:
        """Undo a record's system steps (registry undo needs no folder) and save it without them."""
        if not record.steps:
            return []
        notes = undo_steps(record.steps, StepContext(folder.absolute(), self.ctx.elevator, self.ctx.registry))
        record.steps = []
        self.ctx.records.save(record)
        return notes

    def _place(self, entry: dict, staged: _StagedGame, target: Path, library_id: str, library_path: Path,
               staging: Path, notes: list[str]) -> JobResult:
        """Move the staged tree into the library, run the system steps there, and save the record."""
        ctx = self.ctx
        game_id = entry["id"]
        files = _incoming(staged.tree, staged.owners, game_id)

        if os.path.lexists(target):  # Appeared while we were downloading: never merge into it.
            raise InstallFailed(messages.FOLDER_EXISTS.format(folder=target.name,
                                                              library=_library_label(library_path, ctx.libraries)))
        os.replace(staged.tree, target)
        step_ctx = StepContext(target.absolute(), ctx.elevator, ctx.registry)
        applied: list[dict] = []
        try:
            # On failure run_system_steps undoes the steps it applied before raising.
            applied = run_system_steps(staged.system_steps(), step_ctx)
            record = InstallRecord(
                catalog_id=game_id, kind=entry.get("kind", "game"), library_id=library_id,
                folder=target.name, launch_path=staged.launch_path,
                launch_from_catalog=staged.launch_from_catalog,
                source=_installed_source(entry, staged.game, ctx.platform),
                extras={x["id"]: _installed_source(x, f, ctx.platform) for x, f in staged.extras},
                files=files, steps=applied, method=(staged.game.source.get("install") or {}).get("method", ""),
                private_runtimes=staged.private_ids, runtime_copies=staged.copy_ids,
            )
            ctx.records.save(record)
        except BaseException as e:
            # `applied` is only non-empty when the steps all ran and saving the record failed.
            undo_failures = [*getattr(e, "undo_failures", []), *undo_steps(applied, step_ctx)]
            if not _take_back(target, staging):
                undo_failures.append(messages.FOLDER_NOT_REMOVED.format(path=target))
                if isinstance(e, Exception) and not isinstance(e, (StepError, InstallFailed)):
                    raise InstallFailed("\n\n".join(undo_failures)) from e
            if isinstance(e, StepError):
                raise InstallFailed("\n\n".join([str(e), *undo_failures])) from e
            raise

        return JobResult("install", game_id, title_of(entry), game_fields_for(entry, record, ctx.platform), [*staged.notes(), *notes])

    # ---- changing an installed game -------------------------------------------

    def _change_folder(self, rec: InstallRecord, game_dir: Path, tree: Path, incoming: dict[str, RecordedFile],
                       installed: dict[str, RecordedFile], wanted_steps: list[dict], prior: dict, title: str,
                       persist=(), restore_changed_code: bool = False
                       ) -> tuple[UpdatePlan, ApplyResult, list[dict], list[str]]:
        """Bring the files in `game_dir` from `installed` to `incoming` (staged in `tree`) under the
        three-checksum rule, and its system steps to `wanted_steps`.

        The plan is checked for conflicts first; a conflict refuses with nothing changed, steps
        included. Applied steps that aren't wanted any more are then undone, while the files they name
        are still there. Applied steps that are still wanted are kept with their original undo data and
        not run again, so their undo never captures a value Game Chooser set itself. Returns the plan,
        what applying it did, the record's new steps, and plain-text notes for undo failures.

        If the files can't all be changed, or a new step fails, the record is saved as it now stands
        (see _save_partial) and InstallFailed is raised.
        """
        ctx = self.ctx
        plan = plan_update(installed, hash_tree(game_dir), incoming, persist, restore_changed_code)
        try:
            preflight(plan, game_dir, tree, ctx.today())
        except Exception as e:
            raise InstallFailed(_failure_text(e, title)) from e
        step_ctx = StepContext(game_dir.absolute(), ctx.elevator, ctx.registry)
        kept, unwanted, new = split_steps(rec.steps, wanted_steps)
        notes = undo_steps(unwanted, step_ctx)
        try:
            result = apply_plan(plan, game_dir, tree, ctx.today())
        except Exception as e:
            partial = getattr(e, "partial", None)
            self._save_partial(rec, plan, game_dir, partial.backups if partial else [], kept, prior)
            raise InstallFailed(_failure_text(e, title)) from e
        try:
            applied = run_system_steps(new, step_ctx)
        except StepError as e:
            self._save_partial(rec, plan, game_dir, result.backups, kept, prior)
            raise InstallFailed("\n\n".join([str(e), *getattr(e, "undo_failures", []), *notes])) from e
        return plan, result, kept + applied, notes

    def _save_partial(self, rec: InstallRecord, plan: UpdatePlan, game_dir: Path, backups: list[dict],
                      steps: list[dict], prior: dict) -> None:
        """Save the record of a change that stopped partway, true to what is on disk.

        A file the change already wrote is recorded as the new version's; everything else keeps its
        old entry, and the source and version stay the old ones. The backups made so far are kept for
        Undo, so the player's copies can still be put back.
        """
        try:
            on_disk = hash_tree(game_dir)
        except OSError:
            on_disk = {}
        for key, new in plan.new_files.items():
            if key in on_disk and on_disk[key][1] == new.sha256:
                rec.files[key] = new
        rec.steps = steps
        _keep_backups(rec, backups, prior)
        self.ctx.records.save(rec)

    def _update(self, entry: dict, rec: InstallRecord, game_dir: Path, extra_ids: set[str], action: str,
                progress, cancel) -> JobResult:
        """Install the catalog's current version over the game's folder.

        The extras are the required ones plus `extra_ids` that are still in the catalog.
        """
        ctx = self.ctx
        game_id = entry["id"]
        title = title_of(entry)
        extras = [x.entry for x in extras_of(entry, ctx.entries, ctx.platform)
                  if x.required or x.entry["id"] in extra_ids]
        deps = runtimes.plan_runtimes(entry, ctx)  # refuses a dependency cycle
        prior = _snapshot(rec)
        with self._work_folder(game_id) as work:
            runtime_notes = self._install_runtimes(deps, work, progress, cancel)
            fetched = self._fetch_game(entry, work, progress, cancel)
            if runs_installer(fetched.source):
                if extras or rec.extras:  # applied by Game Chooser after the installer, so checked first
                    self._require_writable(rec, game_dir, title)
                with self._staging_folder(self._staging_root(rec, game_dir), game_id) as staging:
                    private = runtimes.unpack_private(ctx, deps.private, staging, work,
                                                      self._catalog_fetch(progress, cancel), progress, cancel)
                    unpacked = self._unpack_extras(extras, staging, work, self._catalog_fetch(progress, cancel),
                                                   progress, cancel)
                    result = self._install_run(entry, fetched, unpacked, staging, action, lambda item, spare: (
                        run_installer.update(ctx, item, rec, game_dir, progress, cancel, previous=prior,
                                             spare_steps=spare)), cancel, prior, private=private)
                    result.notes += [*runtime_notes, *_went_without(title, deps)]
                    return result
            self._require_writable(rec, game_dir, title)
            with self._staging_folder(self._staging_root(rec, game_dir), game_id) as staging:
                staged = self._stage(entry, extras, game_dir.name, staging, work,
                                     _prefetched(self._catalog_fetch(progress, cancel), game_id, fetched),
                                     _launch_hint(entry, ctx.platform), progress, cancel, private=deps.private,
                                     installed=(rec, game_dir))
                check_cancel(cancel)
                plan, result, steps, notes = self._change_folder(
                    rec, game_dir, staged.tree, _incoming(staged.tree, staged.owners, game_id), rec.files,
                    staged.system_steps(), prior, title, persist=entry.get("persist") or [])

        # A failed earlier attempt at this change may already have moved the player's copy aside and
        # written this version's file; that backup belongs to this undo point too.
        carried = [b for b in (rec.previous or {}).get("backups") or [] if _targets(plan, b)]
        staged.record_into(rec, plan.new_files, steps)
        rec.source = _installed_source(entry, staged.game, ctx.platform)
        rec.extras = {x["id"]: _installed_source(x, f, ctx.platform) for x, f in staged.extras}
        rec.vendor_uninstaller = None  # An installer's own uninstaller no longer matches these files.
        rec.previous = {**prior, "backups": [*carried, *result.backups]}
        rec.previous_unavailable = None
        rec.uninstalled = False
        ctx.records.save(rec)
        return JobResult(action, game_id, title, game_fields_for(entry, rec, ctx.platform),
                         [*runtime_notes, *staged.notes(), *notes, *_went_without(title, deps)],
                         backups=len(result.backups))

    def _stage_recorded(self, entry: dict, source: InstalledSource, extra_sources: dict[str, InstalledSource],
                        folder_name: str, staging: Path, work: Path, progress, cancel,
                        private: list[dict] = (), copies: bool = True, copy_ids: list[str] | None = None,
                        installed: tuple[InstallRecord, Path] | None = None) -> _StagedGame:
        """Rebuild exactly a recorded version in staging: its files, unpacked and set up as recorded.

        `private` are the runtimes that version had private COM copies of (runtimes.private_runtimes):
        they are made again (from today's catalog), so a Repair or Undo doesn't remove them. So are the
        copies of `copy_ids`' files beside its exe (the version's `runtime_copies`; None: every runtime
        the game depends on today), unless `copies` is False."""
        ctx = self.ctx
        extras = [ctx.entries.get(x) or {"id": x} for x in extra_sources]
        fetch = self._exact_fetch({entry["id"]: source, **extra_sources}, progress, cancel)
        return self._stage(entry, extras, folder_name, staging, work, fetch,
                           _recorded_launch(source, entry, ctx.platform), progress, cancel, private=private,
                           copies=copies, copy_ids=copy_ids, installed=installed)

    def _repair(self, entry: dict, rec: InstallRecord, game_dir: Path, progress, cancel) -> JobResult:
        """Put back the exact files that were installed; changed code is restored, the player's edits kept.

        An installer that must run is run again (engine.run_actions); an archive is rebuilt in staging.
        """
        ctx = self.ctx
        game_id = entry["id"]
        title = title_of(entry)
        if self._recorded_runs_installer(game_id, rec.source):
            return run_actions.repair(self, entry, rec, game_dir, progress, cancel)
        self._require_writable(rec, game_dir, title)
        private = runtimes.private_runtimes(rec, entry, ctx)
        prior = _snapshot(rec)
        with self._workspace(self._staging_root(rec, game_dir), game_id) as (staging, work):
            try:
                staged = self._stage_recorded(entry, rec.source, rec.extras, game_dir.name, staging, work,
                                              progress, cancel, private=private, copy_ids=rec.runtime_copies,
                                              installed=(rec, game_dir))
            except (NoVerifiedSource, _NoUnpackSpec) as e:
                raise InstallFailed(_repair_failure_text(e, game_id, title)) from e
            check_cancel(cancel)
            # The steps stay the ones applied, whatever today's catalog says.
            plan, result, steps, notes = self._change_folder(
                rec, game_dir, staged.tree, _incoming(staged.tree, staged.owners, game_id), rec.files,
                [_bare(s) for s in rec.steps], prior, title, persist=entry.get("persist") or [],
                restore_changed_code=True)

        staged.record_into(rec, plan.new_files, steps)
        _keep_backups(rec, result.backups, prior)
        ctx.records.save(rec)
        return JobResult("repair", game_id, title, game_fields_for(entry, rec, ctx.platform),
                         [*staged.notes(), *notes], backups=len(result.backups))

    def _undo(self, entry: dict, rec: InstallRecord, game_dir: Path, progress, cancel) -> JobResult:
        """Reinstall the previous version under the same rules, then put back the player's copies the
        update moved aside, where the update's file is still untouched.

        A previous version that is an installer that must run is run again (engine.run_actions); an
        archive is rebuilt in staging, whichever way the current version was installed.
        """
        ctx = self.ctx
        game_id = entry["id"]
        title = title_of(entry)
        previous = rec.previous
        if previous is None:
            raise InstallFailed(messages.NOTHING_TO_UNDO.format(title=title))
        source = _source_from(previous["source"])
        if self._recorded_runs_installer(game_id, source):
            return run_actions.undo(self, entry, rec, game_dir, source, progress, cancel)
        self._require_writable(rec, game_dir, title)
        extra_sources = {k: _source_from(v) for k, v in (previous.get("extras") or {}).items()}
        # The previous version's private copies; an undo point from before they were kept: the current ones.
        private = runtimes.private_runtimes(rec, entry, ctx, previous.get("private_runtimes"))
        prior = _snapshot(rec)
        with self._workspace(self._staging_root(rec, game_dir), game_id) as (staging, work):
            try:
                staged = self._stage_recorded(entry, source, extra_sources, game_dir.name, staging, work,
                                              progress, cancel, private=private,
                                              copy_ids=_previous_copies(previous, rec), installed=(rec, game_dir))
            except (NoVerifiedSource, _NoUnpackSpec) as e:
                raise self._undo_failed(e, rec, title) from e
            check_cancel(cancel)
            incoming = _incoming(staged.tree, staged.owners, game_id)
            # Decided before anything changes: a backup goes back only over the file the update wrote.
            untouched = _untouched_backups(game_dir, previous.get("backups") or [])
            wanted_steps = previous["steps"] if "steps" in previous else staged.system_steps()
            try:
                plan, result, steps, notes = self._change_folder(
                    rec, game_dir, staged.tree, incoming, rec.files, wanted_steps, prior, title,
                    persist=entry.get("persist") or [])
            except InstallFailed:
                self._retarget_backups(rec, game_dir, untouched, incoming)
                raise

        # The undo has just rewritten those files, so each is compared with what the undo wrote.
        restored = restore_backups(game_dir, [_rewritten_by(plan.new_files, b) for b in untouched])
        notes += _backups_kept_note(game_dir, previous.get("backups") or [], restored, title)
        staged.record_into(rec, plan.new_files, steps)
        rec.source = source
        rec.extras = extra_sources
        rec.vendor_uninstaller = previous.get("vendor_uninstaller")  # the previous version's, if it had one
        rec.previous = None
        rec.previous_unavailable = None
        ctx.records.save(rec)
        return JobResult("undo", game_id, title, game_fields_for(entry, rec, ctx.platform),
                         [*staged.notes(), *notes], backups=len(result.backups))

    def _undo_failed(self, e: NoVerifiedSource | _NoUnpackSpec, rec: InstallRecord, title: str) -> InstallFailed:
        """Why Undo couldn't get the previous version back. Unless nothing could be reached (maybe only for
        now, so Undo stays offered), the record says it is unavailable."""
        if isinstance(e, NoVerifiedSource) and not e.downloaded:
            return InstallFailed(messages.DOWNLOAD_UNREACHABLE.format(title=title))
        rec.previous_unavailable = messages.UNDO_UNAVAILABLE.format(title=title)
        self.ctx.records.save(rec)
        return InstallFailed(rec.previous_unavailable)

    def _retarget_backups(self, rec: InstallRecord, game_dir: Path, untouched: list[dict],
                          incoming: dict[str, RecordedFile]) -> None:
        """After an undo stopped partway: a backup that was due to go back, whose file the undo already
        rewrote, is now compared with what the undo wrote, so retrying the undo still puts it back."""
        if rec.previous is None:
            return
        due = {(b["original"], b["backup"]) for b in untouched if _holds(game_dir, b["original"], incoming)}
        if not due:
            return
        rec.previous = {**rec.previous, "backups": [
            _rewritten_by(incoming, b) if (b.get("original"), b.get("backup")) in due else b
            for b in rec.previous.get("backups") or []]}
        self.ctx.records.save(rec)

    def _add_extras(self, entry: dict, rec: InstallRecord, game_dir: Path, extra_ids: set[str],
                    progress, cancel) -> JobResult:
        """Add extras to an installed game.

        The extras are applied, with their file steps, over an exact rebuild of the installed version,
        as they would be at install. Only the files they wrote and the installed files their steps
        removed are planned, so nothing else in the folder can be deleted or replaced. An installer
        that must run can't be rebuilt in staging, so for one the installed version is the recorded
        files the folder still holds unchanged.
        """
        ctx = self.ctx
        game_id = entry["id"]
        title = title_of(entry)
        chosen = [x.entry for x in extras_of(entry, ctx.entries, ctx.platform) if x.entry["id"] in extra_ids]
        if not chosen:
            raise InstallFailed(messages.EXTRAS_NOT_AVAILABLE.format(title=title))
        chosen_ids = {x["id"] for x in chosen}
        base_extras = {k: v for k, v in rec.extras.items() if k not in chosen_ids}
        self._require_writable(rec, game_dir, title)
        prior = _snapshot(rec)
        with self._workspace(self._staging_root(rec, game_dir), game_id) as (staging, work):
            if self._recorded_runs_installer(game_id, rec.source):
                tree = run_actions.copy_installed_files(rec, game_dir, staging / "game" / game_dir.name, title)
            else:
                try:
                    tree = self._stage_recorded(entry, rec.source, base_extras, game_dir.name, staging, work,
                                                progress, cancel, copies=False).tree
                except NoVerifiedSource as e:
                    text = (messages.EXTRAS_NEED_UPDATE if e.downloaded else messages.DOWNLOAD_UNREACHABLE)
                    raise InstallFailed(text.format(title=title)) from e
                except _NoUnpackSpec as e:
                    raise InstallFailed(messages.EXTRAS_NEED_UPDATE.format(title=title)) from e
            unpacked = self._unpack_extras(chosen, staging, work, self._catalog_fetch(progress, cancel),
                                           progress, cancel)
            fetched, result, notes = self._apply_extras(entry, rec, game_dir, tree, unpacked, prior, cancel)

        rec.extras.update({x["id"]: _installed_source(x, f, ctx.platform) for x, f in fetched})
        ctx.records.save(rec)
        notes = [*dict.fromkeys(f.note for _, f in fetched if f.note), *notes]
        return JobResult("extras", game_id, title, game_fields_for(entry, rec, ctx.platform), notes, backups=len(result.backups))

    def _apply_extras(self, entry: dict, rec: InstallRecord, game_dir: Path, tree: Path,
                      unpacked: list[tuple[dict, Fetched, Path]], prior: dict | None, cancel, every: bool = False
                      ) -> tuple[list[tuple[dict, Fetched]], ApplyResult, list[str]]:
        """Merge unpacked extras over `tree` (the installed version) and change only their files in
        `game_dir`: the files they wrote and the installed files their steps removed. With `every`, they
        are all the game's extras now, so the files earlier extras shipped that none of them ships are
        planned too, and removed where unchanged. Updates the record's files and steps (not its extras)
        and keeps the backups for Undo; the caller saves it.
        """
        game_id = entry["id"]
        before = set(hash_tree(tree))
        owners, fetched = self._merge_unpacked(tree, unpacked, cancel)
        check_cancel(cancel)
        after = _incoming(tree, owners, game_id)
        removed = before - set(after)
        incoming = {key: f for key, f in after.items() if key in owners}
        dropped = ({key for key, f in rec.files.items()
                    if f.owner != game_id and not runtimes.is_runtime_owner(f.owner) and key not in incoming}
                   if every else set())
        planned = set(incoming) | removed | dropped
        installed = {key: f for key, f in rec.files.items() if key in planned}
        # Steps already applied stay as they are; only the extras' new steps run.
        wanted_steps = [*(_bare(s) for s in rec.steps), *(s for _, f in fetched for s in _steps(f))]
        plan, result, steps, notes = self._change_folder(
            rec, game_dir, tree, incoming, installed, wanted_steps, prior, title_of(entry),
            persist=entry.get("persist") or [])
        for key in removed | dropped:
            rec.files.pop(key, None)
        rec.files.update(plan.new_files)
        rec.steps = steps
        _keep_backups(rec, result.backups, prior)
        return fetched, result, notes

    # ---- uninstall -----------------------------------------------------------

    def _uninstall(self, req: InstallRequest, entry: dict | None, progress, cancel) -> JobResult:
        """Undo the system steps, delete the recorded unchanged files, and keep (or recycle) the rest.

        The record goes first. A game whose installer registered its own uninstaller then has that run
        too, only for what the record can't undo (its entries in Windows), with the player's files set
        aside; the record's leftovers are removed afterwards. If the record can't remove the files
        itself (an install in Program Files), the vendor uninstaller still runs and the record tries
        again after it. Runtimes are separate records in their own folders, and are never touched here.
        """
        ctx = self.ctx
        rec = ctx.records.load(req.catalog_id)
        title = title_of(entry) if entry else Path(rec.folder).name if rec else req.catalog_id
        if rec is None:
            raise InstallFailed(messages.NOT_INSTALLED.format(title=title))
        _require_changeable(rec, title, "uninstall")
        game_dir = record_folder(rec, ctx.libraries)
        if game_dir is None:
            raise InstallFailed(messages.GAME_FOLDER_MISSING.format(title=title, path=rec.folder))
        vendor = rec.vendor_uninstaller
        if vendor:
            run_installer.require_no_setaside(ctx, rec.catalog_id, title)
        # Steps first: unregistering a component needs its file still in place.
        notes = undo_steps(rec.steps, StepContext(game_dir.absolute(), ctx.elevator, ctx.registry))
        rec.steps = []
        try:
            plan = plan_uninstall(rec, game_dir)
            if vendor:
                plan = run_installer.keep_vendor_uninstaller(plan, vendor, game_dir)
            kept = apply_uninstall(plan, game_dir, req.delete_player_data, ctx.recycle)
        except Exception as e:
            if not vendor:
                self._uninstall_failed(rec, game_dir, title, notes, e)
        if vendor:
            try:
                notes += run_installer.run_vendor_uninstaller(ctx, rec, game_dir, title, progress, cancel)
            except BaseException:
                rec.files = {key: f for key, f in rec.files.items() if _still_there(game_dir, f)}
                rec.uninstalled = True  # Its files are removed; uninstalling again runs its uninstaller again.
                ctx.records.save(rec)
                raise
            rec.vendor_uninstaller = None
            try:  # The record's leftovers: the uninstaller's own files, and anything it couldn't remove.
                kept = apply_uninstall(plan_uninstall(rec, game_dir), game_dir, req.delete_player_data, ctx.recycle)
            except Exception as e:
                self._uninstall_failed(rec, game_dir, title, notes, e)
        if os.path.lexists(game_dir):
            # The player's files are still there; the record keeps the folder recognised as this game's.
            rec.uninstalled = True
            rec.files = {}
            ctx.records.save(rec)
        else:
            ctx.records.delete(rec.catalog_id)
        return JobResult("uninstall", rec.catalog_id, title, None, notes, kept_player_files=kept)

    def _uninstall_failed(self, rec: InstallRecord, game_dir: Path, title: str, notes: list[str],
                          e: Exception) -> None:
        """Save what is left of an uninstall that stopped partway, and raise its plain words."""
        rec.files = {key: f for key, f in rec.files.items() if _still_there(game_dir, f)}
        rec.uninstalled = True
        self.ctx.records.save(rec)
        plain = isinstance(e, EngineError) and not isinstance(e, UnsafePath)
        raise InstallFailed("\n\n".join([str(e) if plain else messages.UNINSTALL_FAILED.format(title=title),
                                         *notes])) from e


def plain_file_error(e: OSError, title: str) -> str:
    """The player's words for an OSError that stopped a job: what happened, and what to do."""
    code, winerror = e.errno, getattr(e, "winerror", None)
    if code == errno.ENOSPC or winerror in _WINERROR_DISK_FULL:
        return messages.DISK_FULL.format(title=title)
    if code in (errno.EACCES, errno.EPERM, errno.EBUSY) or winerror in _WINERROR_LOCKED:
        return messages.FILE_LOCKED.format(title=title)
    if code == errno.ENOENT or winerror in _WINERROR_VANISHED:
        return messages.FILE_VANISHED.format(title=title)
    path = e.filename or e.filename2 or "a file"
    return messages.FILE_ERROR.format(title=title, path=path, detail=e.strerror or str(e))


def _went_without(title: str, deps: runtimes.RuntimePlan) -> list[str]:
    """Notes for the runtimes the game was installed without: installers that can't be made private."""
    return runtimes.needs_shared_notes(title, deps.skipped)


def _method(source: dict) -> str | None:
    return (source.get("install") or {}).get("method")


def _extra_steps(unpacked: list[tuple[dict, Fetched, Path]]) -> list[dict]:
    """The steps of the extras about to be applied, in order."""
    return [s for _, fetched, _ in unpacked for s in _steps(fetched)]


def _prefetched(fetch: Fetch, item_id: str, fetched: Fetched) -> Fetch:
    """`fetch`, except that the first request for `item_id` gets the file already downloaded."""
    pending = [fetched]

    def get(item: dict, work_dir: Path) -> Fetched:
        if item["id"] == item_id and pending:
            return pending.pop()
        return fetch(item, work_dir)
    return get


def _require_changeable(rec: InstallRecord, title: str, action: str) -> None:
    """Only tracked games are changed: those Game Chooser unpacked itself, and those an installer put
    in place whose files were found. Runtimes never are."""
    changeable = rec.method in EXTRACT_METHODS or (rec.method == "run" and action in RUN_ACTIONS)
    if rec.kind == "runtime" or not rec.tracked or not changeable:
        raise InstallFailed(messages.INSTALL_NOT_CHANGEABLE.format(title=title))


def _blocked(rec: InstallRecord, game_dir: Path | None) -> str | None:
    """Why Game Chooser may not change the game's folder: "untracked" (no tracked folder), or "admin" for a
    folder outside the libraries where a file can't be created (and removed again) without administrator
    permission; else None."""
    if not rec.tracked or game_dir is None:
        return "untracked"
    if rec.library_id:
        return None
    try:
        fd, name = tempfile.mkstemp(prefix=".gc-", suffix=".tmp", dir=game_dir)
    except OSError:
        return "admin"
    os.close(fd)
    with suppress(OSError):
        os.unlink(name)
    return None


def _take_back(target: Path, staging: Path) -> bool:
    """Move a just-placed game folder back into staging (which the caller then removes).

    Returns False if part of the folder is still in the library.
    """
    try:
        staging.mkdir(parents=True, exist_ok=True)
        os.replace(target, staging / "placed")
    except OSError:
        # Only files this install created moments ago are in there.
        _clear(target)
    return not os.path.lexists(target)


def _incoming(tree: Path, owners: dict[str, str], game_id: str) -> dict[str, RecordedFile]:
    """Every file in a staged tree, recorded with the extra that wrote it last (else the game)."""
    return {key: RecordedFile(display, sha, owners.get(key, game_id))
            for key, (display, sha) in hash_tree(tree).items()}


def game_fields_for(entry: dict, rec: InstallRecord, platform: str | None = None) -> dict:
    """The games.json fields for an installed catalog game, labelled with the platform it was installed for.

    A game an installer put outside every library has library_id "" and an absolute launch path.
    """
    platform = platform or current_platform()
    if rec.library_id:
        launch_path, library_id = f"{rec.folder}/{rec.launch_path}", rec.library_id
    else:
        launch_path, library_id = str(Path(rec.folder) / rec.launch_path), ""
    return {
        **game_fields(entry, platform),
        "launch_path": launch_path,
        "library_id": library_id,
        "catalog_id": rec.catalog_id,
        "installed_source": {"url": rec.source.url, "sha256": rec.source.sha256, "size": rec.source.size},
        "installed_extras": list(rec.extras),
    }


def _snapshot(rec: InstallRecord) -> dict:
    """The record's current version as a `previous` value (with no backups yet).

    An installer's version also keeps its recorded files, so undoing back to it can tell a file it
    shipped too from one only the newer version had (engine.run_installer.update).
    """
    snapshot = {"source": asdict(rec.source), "extras": {k: asdict(v) for k, v in rec.extras.items()},
                "backups": [], "steps": [dict(s) for s in rec.steps], "vendor_uninstaller": rec.vendor_uninstaller,
                "private_runtimes": rec.private_runtimes, "runtime_copies": rec.runtime_copies}
    if rec.method == "run":
        snapshot["files"] = {k: asdict(f) for k, f in rec.files.items()}
    return snapshot


def _previous_copies(previous: dict, rec: InstallRecord) -> list[str] | None:
    """The runtimes whose files the previous version had copied beside its exe; an undo point from before they
    were kept: the current version's (None for a record from before then too: all the game depends on)."""
    ids = previous.get("runtime_copies")
    return list(ids) if isinstance(ids, list) else rec.runtime_copies


def _keep_backups(rec: InstallRecord, backups: list[dict], prior: dict | None) -> None:
    """Keep backups in `record.previous`, so Undo can put the player's copies back. With no undo point
    yet, `prior` becomes one; None (a fresh install) makes none, and the backups just stay in the folder."""
    if not backups or (rec.previous is None and prior is None):
        return
    if rec.previous is not None:
        rec.previous = {**rec.previous, "backups": [*(rec.previous.get("backups") or []), *backups]}
    else:
        rec.previous = {**prior, "backups": list(backups)}
        rec.previous_unavailable = None


def _source_from(data: dict) -> InstalledSource:
    return InstalledSource(**{f.name: data.get(f.name) for f in fields(InstalledSource)})


def _catalog_source(item: dict | None, recorded: InstalledSource, platform: str) -> dict | None:
    """The catalog source a recorded file came from: same checksum, else same URL, else same position."""
    sources = platform_sources(item, platform) if item else []
    for source in sources:
        if (source.get("sha256") or "").lower() == recorded.sha256:
            return source
    for source in sources:
        if source.get("url") == recorded.url:
            return source
    if 0 <= recorded.position < len(sources):
        return sources[recorded.position]
    return sources[0] if sources else None


def _exact_urls(recorded: InstalledSource, catalog_sources: list[dict]) -> list[str]:
    """The recorded URL, then the mirror's copy of the recorded file.

    The mirror path is `files/<sha256>/<original filename>`, so it is taken from the catalog when a
    current source is that mirror file, then from the recorded URL if it was the mirror, and only
    otherwise built from the recorded URL's file name.
    """
    mirror = next((s["url"] for s in catalog_sources
                   if is_mirror_url(s.get("url") or "") and _names_checksum(s["url"], recorded.sha256)), None)
    if mirror is None and is_mirror_url(recorded.url):
        mirror = recorded.url
    if mirror is None:
        name = unquote(PurePosixPath(urlparse(recorded.url).path).name) or "download"
        mirror = mirror_url_for(recorded.sha256, name)
    return list(dict.fromkeys([recorded.url, mirror]))


def _names_checksum(url: str, sha256: str) -> bool:
    return sha256.lower() in [part.lower() for part in urlparse(url).path.split("/")]


def _backups_kept_note(game_dir: Path, backups: list[dict], restored: list[str], title: str) -> list[str]:
    """After an undo: a note naming the player's copies that are still in the backup folder (the file they
    would replace has changed since the update, or couldn't be replaced), if there are any."""
    kept = []
    for b in backups:
        try:
            if b["original"] not in restored and safe_join(game_dir, b["backup"]).is_file():
                kept.append(b["original"])
        except (KeyError, TypeError, UnsafePath, OSError):
            continue
    if not kept:
        return []
    return [messages.UNDO_BACKUPS_KEPT.format(title=title, names=messages.names(list(dict.fromkeys(kept))))]


def _untouched_backups(game_dir: Path, backups: list[dict]) -> list[dict]:
    """The backups whose original path still holds exactly what the update wrote there (or nothing)."""
    untouched = []
    for b in backups:
        try:
            path, linked = _resolve_game_path(game_dir, b["original"])
            if linked or path.is_dir():
                continue
            if not path.exists() or hash_file(path) == b["installed_sha256"]:
                untouched.append(b)
        except (KeyError, TypeError, UnsafePath, OSError):
            continue
    return untouched


def _rewritten_by(files: dict[str, RecordedFile], backup: dict) -> dict:
    """`backup`, compared against the file in `files` at its path, if there is one."""
    new = files.get(norm_key(backup["original"]))
    return {**backup, "installed_sha256": new.sha256} if new else backup


def _targets(plan: UpdatePlan, backup: dict) -> bool:
    """True if `plan` installs, at the backup's path, exactly the file its player copy was moved aside for."""
    new = plan.new_files.get(norm_key(backup.get("original") or ""))
    return new is not None and new.sha256 == backup.get("installed_sha256")


def _holds(game_dir: Path, rel: str, files: dict[str, RecordedFile]) -> bool:
    """True if the game's file at `rel` is exactly the one `files` lists there."""
    new = files.get(norm_key(rel))
    try:
        return new is not None and plain_file(game_dir, rel, new.sha256) is not None
    except (UnsafePath, OSError):
        return False


def _repair_failure_text(e: NoVerifiedSource | _NoUnpackSpec, game_id: str, title: str) -> str:
    """Why Repair couldn't get an installed file back: not reachable now, changed (naming an extra), or
    not known how it was unpacked."""
    if isinstance(e, _NoUnpackSpec):
        return messages.REPAIR_NOT_POSSIBLE.format(title=title)
    if not e.downloaded:
        return messages.DOWNLOAD_UNREACHABLE.format(title=title)
    item = getattr(e, "item", None) or {"id": game_id}
    if item["id"] != game_id:
        return messages.REPAIR_EXTRA_CHANGED.format(extra=title_of(item), title=title)
    return messages.REPAIR_FILE_CHANGED.format(title=title)


def _failure_text(e: Exception, title: str) -> str:
    """The player's words for a failure while changing a game's files."""
    if isinstance(e, EngineError) and not isinstance(e, UnsafePath):
        return str(e)
    return messages.FILES_NOT_CHANGED.format(title=title)


def _still_there(game_dir: Path, recorded: RecordedFile) -> bool:
    try:
        return plain_file(game_dir, recorded.path) is not None
    except UnsafePath:
        return False
