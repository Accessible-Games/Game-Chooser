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
"""

import os
import shutil
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from exception_manager import ExceptionManager

from . import messages
from .entries import extras_of, game_fields, is_mirror_url, mirror_url_for, platform_sources
from .errors import Cancelled, EngineError, ExtractError, InstallFailed, NoVerifiedSource, StepError, UnsafePath
from .folders import target_folder
from .launch import find_launch_exe
from .messages import Progress
from .paths import STAGING_DIR_NAME, is_engine_dir, norm_key
from .records import InstalledSource, InstallRecord, RecordedFile, RecordStore, hash_tree, record_folder
from .recycle import send_to_recycle_bin
from .safety import (ApplyResult, UpdatePlan, _resolve_game_path, apply_plan, apply_uninstall, is_link,
                     plan_uninstall, plan_update, preflight, restore_backups)
from .sources import Fetched, fetch_exact, fetch_verified, hash_file
from .steps import StepContext, run_file_steps, run_system_steps, undo_steps

ACTIONS = ("install", "update", "repair", "undo", "extras", "uninstall")

# Install methods whose files Game Chooser unpacked and recorded itself. Run-mode installs (Task 14)
# are recorded with method "run" and are not changed by these flows.
EXTRACT_METHODS = frozenset({"zip", "7z", "installer", "cab", "none"})


@dataclass
class InstallRequest:
    action: str
    catalog_id: str
    library_name: str = ""
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


def _scanner_exclude() -> Callable[[Path], bool]:
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
    installer_ui: object | None = None  # Task 14 narrows this to its InstallerUI protocol.
    recycle: Callable[[list[Path]], None] = send_to_recycle_bin
    exclude: Callable[[Path], bool] = field(default_factory=_scanner_exclude)


# Downloads one catalog item (the game or an extra) into an empty work folder.
Fetch = Callable[[dict, Path], Fetched]


def _same_folder(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)).lower() == os.path.normcase(os.path.abspath(b)).lower()


def _library_label(library_path: Path, libraries: list[dict]) -> str:
    for lib in libraries:
        if lib.get("path") and _same_folder(Path(lib["path"]), library_path):
            return lib.get("name") or str(library_path)
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


def _check(cancel: Callable[[], bool]) -> None:
    if cancel():
        raise Cancelled()


def _clear(path: Path) -> None:
    """Best-effort removal; use _clear_or_fail where a leftover would be merged into the install."""
    shutil.rmtree(path, ignore_errors=True)


def _clear_or_fail(path: Path) -> None:
    """Remove `path` completely, or stop the install: stale files must never be unpacked over."""
    _clear(path)
    if os.path.lexists(path):
        raise InstallFailed(messages.TEMP_FOLDER_NOT_CLEARED.format(path=path))


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
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not is_engine_dir(d) and not is_link(Path(dirpath) / d)]
        for name in filenames:
            full = Path(dirpath) / name
            st = full.stat()
            states[norm_key(full.relative_to(root).as_posix())] = (st.st_ino, st.st_size, st.st_mtime_ns)
    return states


def _title(entry: dict) -> str:
    return entry.get("title") or entry["id"]


def _launch_hint(entry: dict, platform: str) -> str | None:
    return ((entry.get("launch") or {}).get(platform) or {}).get("path")


def _installed_source(entry: dict, fetched: Fetched, platform: str) -> InstalledSource:
    return InstalledSource(
        url=fetched.source["url"], sha256=fetched.sha256, size=fetched.size,
        catalog_sha256=fetched.source.get("sha256"), catalog_size=fetched.source.get("size"),
        position=fetched.position, version=entry.get("version"),
        install=dict(fetched.source.get("install") or {}), steps=_steps(fetched),
        launch=_launch_hint(entry, platform),
    )


def _recorded_launch(recorded: InstalledSource, entry: dict, platform: str) -> str | None:
    """The launch hint the recorded source was installed with (today's, for records that predate it)."""
    return recorded.launch if recorded.install is not None else _launch_hint(entry, platform)


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

    def system_steps(self) -> list[dict]:
        """The game's steps, then each extra's, as one list (the shape `record.steps` is built from)."""
        return _steps(self.game) + [s for _, f in self.extras for s in _steps(f)]

    def notes(self) -> list[str]:
        return list(dict.fromkeys(f.note for f in [self.game, *(f for _, f in self.extras)] if f.note))


class InstallEngine:
    def __init__(self, ctx: EngineContext):
        self.ctx = ctx

    def run(self, req: InstallRequest, progress: Callable[[Progress], None],
            cancel: Callable[[], bool]) -> JobResult:
        """Carry out `req`. Raises InstallFailed (with plain words for the player) or Cancelled."""
        if req.action not in ACTIONS:
            raise ValueError(f"Unknown action: {req.action!r}")
        entry = self.ctx.entries.get(req.catalog_id)
        if req.action == "uninstall":  # A game that left the catalog can still be uninstalled.
            return self._uninstall(req, entry)
        if entry is None:
            raise InstallFailed(messages.NOT_IN_CATALOG)
        if req.action == "install":
            return self._install(entry, req, progress, cancel)
        rec, game_dir = self._installed(entry)
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
                if lib.get("path") and _same_folder(Path(lib["path"]), Path(req.library_path)):
                    return lib["name"], Path(req.library_path)
            elif lib.get("name") == req.library_name:
                return lib["name"], Path(lib["path"])
        raise ValueError(f"Not a configured library: {req.library_name!r} {req.library_path!r}")

    def _installed(self, entry: dict) -> tuple[InstallRecord, Path]:
        """The game's record and folder, for the actions that change an installed game."""
        title = _title(entry)
        rec = self.ctx.records.load(entry["id"])
        if rec is None or rec.uninstalled:
            raise InstallFailed(messages.NOT_INSTALLED.format(title=title))
        _require_changeable(rec, title)
        game_dir = record_folder(rec, self.ctx.libraries)
        if game_dir is None or not game_dir.is_dir():
            raise InstallFailed(messages.GAME_FOLDER_MISSING.format(title=title, path=game_dir or rec.folder))
        return rec, game_dir

    @contextmanager
    def _workspace(self, library_path: Path, game_id: str) -> Iterator[tuple[Path, Path]]:
        """Empty staging and work folders for one job, removed again when it ends."""
        staging_root = library_path / STAGING_DIR_NAME
        staging = staging_root / game_id
        work = self.ctx.work_root / game_id
        try:
            # A new library's folder may not exist yet; only the staging folder inside it is hidden.
            staging_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise InstallFailed(messages.LIBRARY_FOLDER_NOT_CREATED.format(path=library_path)) from e
        _hide(staging_root)
        _clear_or_fail(staging)
        _clear_or_fail(work)
        try:
            yield staging, work
        finally:
            _clear(staging)
            _clear(work)

    # ---- fetching and staging ------------------------------------------------

    def _catalog_fetch(self, progress, cancel) -> Fetch:
        """Download the catalog's current file for an item: the first source that verifies."""
        ctx = self.ctx

        def fetch(item: dict, work_dir: Path) -> Fetched:
            try:
                return fetch_verified(item, ctx.platform, work_dir, ctx.fetcher, progress, cancel)
            except NoVerifiedSource as e:
                reasons = " ".join(dict.fromkeys(e.reasons))
                raise InstallFailed(messages.NO_SOURCE.format(title=item.get("title") or item["id"],
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
            if recorded.install is not None:
                spec = {"install": recorded.install, "steps": list(recorded.steps or [])}
            else:
                spec = _catalog_source(catalog_item, recorded, ctx.platform)
                if spec is None:
                    raise _NoUnpackSpec(item["id"])
            urls = _exact_urls(recorded, platform_sources(catalog_item, ctx.platform) if catalog_item else [])
            fetched = fetch_exact(urls, recorded.sha256, work_dir, ctx.fetcher, progress, cancel)
            fetched.source = {**spec, **fetched.source}
            return fetched
        return fetch

    def _fetch_and_unpack(self, item: dict, dest: Path, work_dir: Path, fetch: Fetch,
                          progress, cancel) -> tuple[Fetched, Path]:
        """Download `item` into its own empty work folder, unpack it into `dest`, and delete the download."""
        _clear_or_fail(work_dir)
        fetched = fetch(item, work_dir)
        try:
            _check(cancel)
            progress(Progress("extracting"))
            root = self.ctx.extractor.extract(fetched.path, dest, fetched.source.get("install") or {}, cancel)
        except ExtractError as e:
            raise InstallFailed(str(e)) from e
        finally:
            _clear(work_dir)
        _check(cancel)
        return fetched, root

    def _stage(self, entry: dict, extras: list[dict], folder_name: str, staging: Path, work: Path,
               fetch: Fetch, launch: str | None, progress, cancel) -> _StagedGame:
        """Build the finished game tree in staging: game, its file steps, then each extra and its file steps."""
        fetched, root = self._fetch_and_unpack(entry, staging / "raw", work / "game", fetch, progress, cancel)
        # Named like the final folder, so exe detection can prefer an exe named after it.
        tree = staging / "game" / folder_name
        tree.parent.mkdir(parents=True)
        os.replace(root, tree)
        _run_file_steps(_steps(fetched), tree)
        owners, fetched_extras = self._merge_extras(tree, extras, staging, work, fetch, progress, cancel)

        progress(Progress("finishing"))
        exe = find_launch_exe(tree, launch, self.ctx.exclude)
        if exe is None:
            raise InstallFailed(messages.NO_EXE.format(title=_title(entry)) + "\n\n" + messages.DEFENDER_NOTE)
        return _StagedGame(tree, fetched, fetched_extras, owners, exe.relative_to(tree).as_posix())

    def _merge_extras(self, tree: Path, extras: list[dict], staging: Path, work: Path, fetch: Fetch,
                      progress, cancel) -> tuple[dict[str, str], list[tuple[dict, Fetched]]]:
        """Unpack each extra over `tree` and run its file steps there.

        Returns which extra wrote each file last (norm_key -> extra id) and what was fetched.
        """
        owners: dict[str, str] = {}
        fetched_extras = []
        for extra in extras:
            extra_id = extra["id"]
            before = _file_states(tree)
            x_fetched, x_root = self._fetch_and_unpack(extra, staging / "extras" / extra_id,
                                                       work / "extras" / extra_id, fetch, progress, cancel)
            shipped = set(_file_states(x_root))
            shutil.copytree(x_root, tree, dirs_exist_ok=True)
            _run_file_steps(_steps(x_fetched), tree)
            after = _file_states(tree)
            for key, state in after.items():
                if key in shipped or before.get(key) != state:
                    owners[key] = extra_id
            owners = {k: v for k, v in owners.items() if k in after}
            fetched_extras.append((extra, x_fetched))
            _check(cancel)
        return owners, fetched_extras

    # ---- fresh install -------------------------------------------------------

    def _install(self, entry: dict, req: InstallRequest, progress, cancel) -> JobResult:
        ctx = self.ctx
        game_id = entry["id"]
        title = _title(entry)
        library_name, library_path = self._library(req)
        extras = [x.entry for x in extras_of(entry, ctx.entries, ctx.platform)
                  if x.required or x.entry["id"] in req.extras]
        for item in [entry, *extras]:
            _require_extract_mode(item, ctx.platform)

        # Refusals come before anything is downloaded or written.
        if entry.get("dependencies"):
            raise InstallFailed(messages.DEPENDENCIES_NOT_SUPPORTED.format(title=title))
        record = ctx.records.load(game_id)
        folder = record_folder(record, ctx.libraries) if record is not None else None
        if folder is not None and folder.is_dir():
            # Its own folder, even after an uninstall that kept the player's files: installed
            # over under the update rules, so nothing of the player's is lost.
            _require_changeable(record, title)
            keep = set() if record.uninstalled else set(record.extras)
            return self._update(entry, record, folder, keep | set(req.extras), "install", progress, cancel)
        reason = blocked_reason(entry, library_path, ctx.records, ctx.libraries)
        if reason:
            raise InstallFailed(reason)
        target = target_folder(entry, library_path, ctx.records, ctx.libraries)

        notes: list[str] = []
        if record is not None:
            # Its folder is gone (or its library isn't set up any more): a fresh install, whose record
            # replaces this one. The old system steps are undone first, so none is left untracked and
            # the new ones record the player's values, not Game Chooser's leftovers.
            notes = self._forget_steps(record, folder or library_path / record.folder)
        with self._workspace(library_path, game_id) as (staging, work):
            staged = self._stage(entry, extras, target.name, staging, work, self._catalog_fetch(progress, cancel),
                                 _launch_hint(entry, ctx.platform), progress, cancel)
            _check(cancel)
            return self._place(entry, staged, target, library_name, library_path, staging, notes)

    def _forget_steps(self, record: InstallRecord, folder: Path) -> list[str]:
        """Undo a record's system steps (registry undo needs no folder) and save it without them."""
        if not record.steps:
            return []
        notes = undo_steps(record.steps, StepContext(folder.absolute(), self.ctx.elevator, self.ctx.registry))
        record.steps = []
        self.ctx.records.save(record)
        return notes

    def _place(self, entry: dict, staged: _StagedGame, target: Path, library_name: str, library_path: Path,
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
                catalog_id=game_id, kind=entry.get("kind", "game"), library_name=library_name,
                folder=target.name, launch_path=staged.launch_path,
                source=_installed_source(entry, staged.game, ctx.platform),
                extras={x["id"]: _installed_source(x, f, ctx.platform) for x, f in staged.extras},
                files=files, steps=applied, method=(staged.game.source.get("install") or {}).get("method", ""),
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

        return JobResult("install", game_id, _title(entry), _game_dict(entry, record), [*staged.notes(), *notes])

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
        wanted = [_bare(s) for s in wanted_steps]
        kept = [s for s in rec.steps if _bare(s) in wanted]
        kept_bare = [_bare(s) for s in kept]
        notes = undo_steps([s for s in rec.steps if _bare(s) not in wanted], step_ctx)
        try:
            result = apply_plan(plan, game_dir, tree, ctx.today())
        except Exception as e:
            partial = getattr(e, "partial", None)
            self._save_partial(rec, plan, game_dir, partial.backups if partial else [], kept, prior)
            raise InstallFailed(_failure_text(e, title)) from e
        try:
            applied = run_system_steps([s for s in wanted_steps if _bare(s) not in kept_bare], step_ctx)
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
        title = _title(entry)
        extras = [x.entry for x in extras_of(entry, ctx.entries, ctx.platform)
                  if x.required or x.entry["id"] in extra_ids]
        for item in [entry, *extras]:
            _require_extract_mode(item, ctx.platform)
        if entry.get("dependencies"):
            raise InstallFailed(messages.DEPENDENCIES_NOT_SUPPORTED.format(title=title))
        prior = _snapshot(rec)
        with self._workspace(game_dir.parent, game_id) as (staging, work):
            staged = self._stage(entry, extras, game_dir.name, staging, work, self._catalog_fetch(progress, cancel),
                                 _launch_hint(entry, ctx.platform), progress, cancel)
            _check(cancel)
            plan, result, steps, notes = self._change_folder(
                rec, game_dir, staged.tree, _incoming(staged.tree, staged.owners, game_id), rec.files,
                staged.system_steps(), prior, title, persist=entry.get("persist") or [])

        # A failed earlier attempt at this change may already have moved the player's copy aside and
        # written this version's file; that backup belongs to this undo point too.
        carried = [b for b in (rec.previous or {}).get("backups") or [] if _targets(plan, b)]
        rec.files = plan.new_files
        rec.steps = steps
        rec.source = _installed_source(entry, staged.game, ctx.platform)
        rec.extras = {x["id"]: _installed_source(x, f, ctx.platform) for x, f in staged.extras}
        rec.launch_path = staged.launch_path
        rec.method = (staged.game.source.get("install") or {}).get("method", "")
        rec.previous = {**prior, "backups": [*carried, *result.backups]}
        rec.previous_unavailable = None
        rec.uninstalled = False
        ctx.records.save(rec)
        return JobResult(action, game_id, title, _game_dict(entry, rec), [*staged.notes(), *notes],
                         backups=len(result.backups))

    def _stage_recorded(self, entry: dict, source: InstalledSource, extra_sources: dict[str, InstalledSource],
                        folder_name: str, staging: Path, work: Path, progress, cancel) -> _StagedGame:
        """Rebuild exactly a recorded version in staging: its files, unpacked and set up as recorded."""
        ctx = self.ctx
        extras = [ctx.entries.get(x) or {"id": x} for x in extra_sources]
        fetch = self._exact_fetch({entry["id"]: source, **extra_sources}, progress, cancel)
        return self._stage(entry, extras, folder_name, staging, work, fetch,
                           _recorded_launch(source, entry, ctx.platform), progress, cancel)

    def _repair(self, entry: dict, rec: InstallRecord, game_dir: Path, progress, cancel) -> JobResult:
        """Put back the exact files that were installed; changed code is restored, the player's edits kept."""
        ctx = self.ctx
        game_id = entry["id"]
        title = _title(entry)
        prior = _snapshot(rec)
        with self._workspace(game_dir.parent, game_id) as (staging, work):
            try:
                staged = self._stage_recorded(entry, rec.source, rec.extras, game_dir.name, staging, work,
                                              progress, cancel)
            except NoVerifiedSource as e:
                raise InstallFailed(messages.REPAIR_FILE_CHANGED.format(title=title)) from e
            except _NoUnpackSpec as e:
                raise InstallFailed(messages.REPAIR_NOT_POSSIBLE.format(title=title)) from e
            _check(cancel)
            # The steps stay the ones applied, whatever today's catalog says.
            plan, result, steps, notes = self._change_folder(
                rec, game_dir, staged.tree, _incoming(staged.tree, staged.owners, game_id), rec.files,
                [_bare(s) for s in rec.steps], prior, title, persist=entry.get("persist") or [],
                restore_changed_code=True)

        rec.files = plan.new_files
        rec.steps = steps
        rec.launch_path = staged.launch_path
        _keep_backups(rec, result.backups, prior)
        ctx.records.save(rec)
        return JobResult("repair", game_id, title, _game_dict(entry, rec), notes, backups=len(result.backups))

    def _undo(self, entry: dict, rec: InstallRecord, game_dir: Path, progress, cancel) -> JobResult:
        """Reinstall the previous version under the same rules, then put back the player's copies the
        update moved aside, where the update's file is still untouched."""
        ctx = self.ctx
        game_id = entry["id"]
        title = _title(entry)
        previous = rec.previous
        if previous is None:
            raise InstallFailed(messages.NOTHING_TO_UNDO.format(title=title))
        source = _source_from(previous["source"])
        extra_sources = {k: _source_from(v) for k, v in (previous.get("extras") or {}).items()}
        prior = _snapshot(rec)
        with self._workspace(game_dir.parent, game_id) as (staging, work):
            try:
                staged = self._stage_recorded(entry, source, extra_sources, game_dir.name, staging, work,
                                              progress, cancel)
            except (NoVerifiedSource, _NoUnpackSpec) as e:
                rec.previous_unavailable = messages.UNDO_UNAVAILABLE.format(title=title)
                ctx.records.save(rec)
                raise InstallFailed(rec.previous_unavailable) from e
            _check(cancel)
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
        restore_backups(game_dir, [_rewritten_by(plan.new_files, b) for b in untouched])
        rec.files = plan.new_files
        rec.steps = steps
        rec.source = source
        rec.extras = extra_sources
        rec.launch_path = staged.launch_path
        rec.method = (staged.game.source.get("install") or {}).get("method", rec.method)
        rec.previous = None
        rec.previous_unavailable = None
        ctx.records.save(rec)
        return JobResult("undo", game_id, title, _game_dict(entry, rec), notes, backups=len(result.backups))

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
        removed are planned, so nothing else in the folder can be deleted or replaced.
        """
        ctx = self.ctx
        game_id = entry["id"]
        title = _title(entry)
        chosen = [x.entry for x in extras_of(entry, ctx.entries, ctx.platform) if x.entry["id"] in extra_ids]
        if not chosen:
            raise InstallFailed(messages.EXTRAS_NOT_AVAILABLE.format(title=title))
        for item in chosen:
            _require_extract_mode(item, ctx.platform)
        chosen_ids = {x["id"] for x in chosen}
        base_extras = {k: v for k, v in rec.extras.items() if k not in chosen_ids}
        prior = _snapshot(rec)
        with self._workspace(game_dir.parent, game_id) as (staging, work):
            try:
                base = self._stage_recorded(entry, rec.source, base_extras, game_dir.name, staging, work,
                                            progress, cancel)
            except (NoVerifiedSource, _NoUnpackSpec) as e:
                raise InstallFailed(messages.EXTRAS_NEED_UPDATE.format(title=title)) from e
            before = set(hash_tree(base.tree))
            owners, fetched = self._merge_extras(base.tree, chosen, staging, work,
                                                 self._catalog_fetch(progress, cancel), progress, cancel)
            _check(cancel)
            after = _incoming(base.tree, owners, game_id)
            removed = before - set(after)
            incoming = {key: f for key, f in after.items() if key in owners}
            planned = set(incoming) | removed
            installed = {key: f for key, f in rec.files.items() if key in planned}
            # Steps already applied stay as they are; only the extras' new steps run.
            wanted_steps = [*(_bare(s) for s in rec.steps), *(s for _, f in fetched for s in _steps(f))]
            plan, result, steps, notes = self._change_folder(
                rec, game_dir, base.tree, incoming, installed, wanted_steps, prior, title,
                persist=entry.get("persist") or [])

        for key in removed:
            rec.files.pop(key, None)
        rec.files.update(plan.new_files)
        rec.extras.update({x["id"]: _installed_source(x, f, ctx.platform) for x, f in fetched})
        rec.steps = steps
        _keep_backups(rec, result.backups, prior)
        ctx.records.save(rec)
        notes = [*dict.fromkeys(f.note for _, f in fetched if f.note), *notes]
        return JobResult("extras", game_id, title, _game_dict(entry, rec), notes, backups=len(result.backups))

    # ---- uninstall -----------------------------------------------------------

    def _uninstall(self, req: InstallRequest, entry: dict | None) -> JobResult:
        """Undo the system steps, delete the recorded unchanged files, and keep (or recycle) the rest.

        Runtimes are separate records in their own folders, and are never touched here.
        """
        ctx = self.ctx
        rec = ctx.records.load(req.catalog_id)
        title = _title(entry) if entry else Path(rec.folder).name if rec else req.catalog_id
        if rec is None:
            raise InstallFailed(messages.NOT_INSTALLED.format(title=title))
        _require_changeable(rec, title)
        game_dir = record_folder(rec, ctx.libraries)
        if game_dir is None:
            raise InstallFailed(messages.GAME_FOLDER_MISSING.format(title=title, path=rec.folder))
        # Steps first: unregistering a component needs its file still in place.
        notes = undo_steps(rec.steps, StepContext(game_dir.absolute(), ctx.elevator, ctx.registry))
        rec.steps = []
        try:
            kept = apply_uninstall(plan_uninstall(rec, game_dir), game_dir, req.delete_player_data, ctx.recycle)
        except Exception as e:
            rec.files = {key: f for key, f in rec.files.items() if _still_there(game_dir, f)}
            rec.uninstalled = True
            ctx.records.save(rec)
            plain = isinstance(e, EngineError) and not isinstance(e, UnsafePath)
            raise InstallFailed("\n\n".join([str(e) if plain else messages.UNINSTALL_FAILED.format(title=title),
                                             *notes])) from e
        if os.path.lexists(game_dir):
            # The player's files are still there; the record keeps the folder recognised as this game's.
            rec.uninstalled = True
            rec.files = {}
            ctx.records.save(rec)
        else:
            ctx.records.delete(rec.catalog_id)
        return JobResult("uninstall", rec.catalog_id, title, None, notes, kept_player_files=kept)


def _require_extract_mode(item: dict, platform: str) -> None:
    """Run-mode installers (Task 14) and web games are not installed by this path yet."""
    for source in platform_sources(item, platform):
        install = source.get("install") or {}
        if install.get("method") == "url" or (install.get("installer") or {}).get("mode") == "run":
            raise NotImplementedError(f"{item.get('id')}: install method not supported yet")


def _require_changeable(rec: InstallRecord, title: str) -> None:
    """Only games whose files Game Chooser unpacked and recorded itself are changed by these flows."""
    if rec.kind == "runtime" or not rec.tracked or rec.method not in EXTRACT_METHODS:
        raise InstallFailed(messages.INSTALL_NOT_CHANGEABLE.format(title=title))


def _run_file_steps(steps: list[dict], tree: Path) -> None:
    try:
        run_file_steps(steps, tree)
    except StepError as e:
        raise InstallFailed(str(e)) from e


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


def _game_dict(entry: dict, rec: InstallRecord) -> dict:
    """The games.json fields for an installed catalog game."""
    return {
        **game_fields(entry),
        "launch_path": f"{rec.folder}/{rec.launch_path}",
        "library_name": rec.library_name,
        "catalog_id": rec.catalog_id,
        "installed_source": {"url": rec.source.url, "sha256": rec.source.sha256, "size": rec.source.size},
        "installed_extras": list(rec.extras),
    }


def _bare(step: dict) -> dict:
    """A step as the catalog wrote it, without the undo data an applied step carries."""
    return {k: v for k, v in step.items() if k != "undo"}


def _snapshot(rec: InstallRecord) -> dict:
    """The record's current version as a `previous` value (with no backups yet)."""
    return {"source": asdict(rec.source), "extras": {k: asdict(v) for k, v in rec.extras.items()},
            "backups": [], "steps": [dict(s) for s in rec.steps]}


def _keep_backups(rec: InstallRecord, backups: list[dict], prior: dict) -> None:
    """Keep backups in `record.previous`, so Undo can put the player's copies back."""
    if not backups:
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
        path, linked = _resolve_game_path(game_dir, rel)
        return new is not None and not linked and path.is_file() and hash_file(path) == new.sha256
    except (UnsafePath, OSError):
        return False


def _failure_text(e: Exception, title: str) -> str:
    """The player's words for a failure while changing a game's files."""
    if isinstance(e, EngineError) and not isinstance(e, UnsafePath):
        return str(e)
    return messages.FILES_NOT_CHANGED.format(title=title)


def _still_there(game_dir: Path, recorded: RecordedFile) -> bool:
    try:
        path, linked = _resolve_game_path(game_dir, recorded.path)
    except UnsafePath:
        return False
    return not linked and path.is_file()
