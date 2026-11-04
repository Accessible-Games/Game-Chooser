"""Install jobs: a request goes in, and either the game is placed and recorded or nothing is left behind.

A fresh install builds the whole game in `<library>/.gamechooser-staging/<id>/`: download, unpack,
file steps, extras, exe detection and hashing all happen there. The finished tree is moved into the
library in one rename, and only then do the system steps (registry, register, run) run, against the
final folder. If they fail they are undone and the folder goes back to staging to be removed.
"""

import os
import shutil
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from exception_manager import ExceptionManager

from . import messages
from .entries import extras_of, game_fields, platform_sources
from .errors import Cancelled, ExtractError, InstallFailed, NoVerifiedSource, StepError
from .folders import target_folder
from .launch import find_launch_exe
from .messages import Progress
from .paths import STAGING_DIR_NAME, is_engine_dir, norm_key
from .records import InstalledSource, InstallRecord, RecordedFile, RecordStore, hash_tree
from .recycle import send_to_recycle_bin
from .safety import is_link
from .sources import Fetched, fetch_verified
from .steps import StepContext, run_file_steps, run_system_steps, undo_steps

ACTIONS = ("install", "update", "repair", "undo", "extras", "uninstall")


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


def _installed_source(entry: dict, fetched: Fetched) -> InstalledSource:
    return InstalledSource(
        url=fetched.source["url"], sha256=fetched.sha256, size=fetched.size,
        catalog_sha256=fetched.source.get("sha256"), catalog_size=fetched.source.get("size"),
        position=fetched.position, version=entry.get("version"),
    )


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


class InstallEngine:
    def __init__(self, ctx: EngineContext):
        self.ctx = ctx

    def run(self, req: InstallRequest, progress: Callable[[Progress], None],
            cancel: Callable[[], bool]) -> JobResult:
        """Carry out `req`. Raises InstallFailed (with plain words for the player) or Cancelled."""
        if req.action not in ACTIONS:
            raise ValueError(f"Unknown action: {req.action!r}")
        entry = self.ctx.entries.get(req.catalog_id)
        if entry is None:
            raise InstallFailed(messages.NOT_IN_CATALOG)
        if req.action == "install":
            return self._install(entry, req, progress, cancel)
        raise NotImplementedError(f"{req.action} is not supported yet")

    def _library(self, req: InstallRequest) -> tuple[str, Path]:
        for lib in self.ctx.libraries:
            if req.library_path is not None:
                if lib.get("path") and _same_folder(Path(lib["path"]), Path(req.library_path)):
                    return lib["name"], Path(req.library_path)
            elif lib.get("name") == req.library_name:
                return lib["name"], Path(lib["path"])
        raise ValueError(f"Not a configured library: {req.library_name!r} {req.library_path!r}")

    def _install(self, entry: dict, req: InstallRequest, progress, cancel) -> JobResult:
        ctx = self.ctx
        game_id = entry["id"]
        title = entry.get("title") or game_id
        library_name, library_path = self._library(req)
        extras = [x.entry for x in extras_of(entry, ctx.entries, ctx.platform)
                  if x.required or x.entry["id"] in req.extras]
        for item in [entry, *extras]:
            _require_extract_mode(item, ctx.platform)

        # Refusals come before anything is downloaded or written.
        if entry.get("dependencies"):
            raise InstallFailed(messages.DEPENDENCIES_NOT_SUPPORTED.format(title=title))
        reason = blocked_reason(entry, library_path, ctx.records, ctx.libraries)
        if reason:
            raise InstallFailed(reason)
        if ctx.records.load(game_id) is not None:  # Task 13 installs over its own record as an update.
            raise InstallFailed(messages.REINSTALL_NOT_SUPPORTED.format(title=title))
        target = target_folder(entry, library_path, ctx.records, ctx.libraries)

        staging_root = library_path / STAGING_DIR_NAME
        staging = staging_root / game_id
        work = ctx.work_root / game_id
        try:
            # A new library's folder may not exist yet; only the staging folder inside it is hidden.
            staging_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise InstallFailed(messages.LIBRARY_FOLDER_NOT_CREATED.format(path=library_path)) from e
        _hide(staging_root)
        _clear_or_fail(staging)
        _clear_or_fail(work)
        try:
            staged = self._stage(entry, extras, target.name, staging, work, progress, cancel)
            _check(cancel)
            return self._place(entry, staged, target, library_name, library_path, staging)
        finally:
            _clear(staging)
            _clear(work)

    def _fetch_and_unpack(self, item: dict, dest: Path, work_dir: Path, progress, cancel) -> tuple[Fetched, Path]:
        """Download `item` into its own empty work folder, unpack it into `dest`, and delete the download."""
        ctx = self.ctx
        _clear_or_fail(work_dir)
        try:
            fetched = fetch_verified(item, ctx.platform, work_dir, ctx.fetcher, progress, cancel)
        except NoVerifiedSource as e:
            reasons = " ".join(dict.fromkeys(e.reasons))
            raise InstallFailed(messages.NO_SOURCE.format(title=item.get("title") or item["id"],
                                                          reasons=reasons).strip()) from e
        try:
            _check(cancel)
            progress(Progress("extracting"))
            root = ctx.extractor.extract(fetched.path, dest, fetched.source.get("install") or {}, cancel)
        except ExtractError as e:
            raise InstallFailed(str(e)) from e
        finally:
            _clear(work_dir)
        _check(cancel)
        return fetched, root

    def _stage(self, entry: dict, extras: list[dict], folder_name: str, staging: Path, work: Path,
               progress, cancel) -> _StagedGame:
        """Build the finished game tree in staging: game, its file steps, then each extra and its file steps."""
        fetched, root = self._fetch_and_unpack(entry, staging / "raw", work / "game", progress, cancel)
        # Named like the final folder, so exe detection can prefer an exe named after it.
        tree = staging / "game" / folder_name
        tree.parent.mkdir(parents=True)
        os.replace(root, tree)
        _run_file_steps(_steps(fetched), tree)

        owners: dict[str, str] = {}
        fetched_extras = []
        for extra in extras:
            extra_id = extra["id"]
            before = _file_states(tree)
            x_fetched, x_root = self._fetch_and_unpack(extra, staging / "extras" / extra_id,
                                                       work / "extras" / extra_id, progress, cancel)
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

        progress(Progress("finishing"))
        launch = ((entry.get("launch") or {}).get(self.ctx.platform) or {}).get("path")
        exe = find_launch_exe(tree, launch, self.ctx.exclude)
        if exe is None:
            title = entry.get("title") or entry["id"]
            raise InstallFailed(messages.NO_EXE.format(title=title) + "\n\n" + messages.DEFENDER_NOTE)
        return _StagedGame(tree, fetched, fetched_extras, owners, exe.relative_to(tree).as_posix())

    def _place(self, entry: dict, staged: _StagedGame, target: Path, library_name: str, library_path: Path,
               staging: Path) -> JobResult:
        """Move the staged tree into the library, run the system steps there, and save the record."""
        ctx = self.ctx
        game_id = entry["id"]
        files = {key: RecordedFile(display, sha, staged.owners.get(key, game_id))
                 for key, (display, sha) in hash_tree(staged.tree).items()}
        system_steps = _steps(staged.game) + [s for _, f in staged.extras for s in _steps(f)]

        if os.path.lexists(target):  # Appeared while we were downloading: never merge into it.
            raise InstallFailed(messages.FOLDER_EXISTS.format(folder=target.name,
                                                              library=_library_label(library_path, ctx.libraries)))
        os.replace(staged.tree, target)
        step_ctx = StepContext(target.absolute(), ctx.elevator, ctx.registry)
        applied: list[dict] = []
        try:
            # On failure run_system_steps undoes the steps it applied before raising.
            applied = run_system_steps(system_steps, step_ctx)
            record = InstallRecord(
                catalog_id=game_id, kind=entry.get("kind", "game"), library_name=library_name,
                folder=target.name, launch_path=staged.launch_path,
                source=_installed_source(entry, staged.game),
                extras={x["id"]: _installed_source(x, f) for x, f in staged.extras},
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

        game = {
            **game_fields(entry),
            "launch_path": f"{target.name}/{staged.launch_path}",
            "library_name": library_name,
            "catalog_id": game_id,
            "installed_source": {"url": staged.game.source["url"], "sha256": staged.game.sha256,
                                 "size": staged.game.size},
            "installed_extras": [x["id"] for x, _ in staged.extras],
        }
        notes = [f.note for f in [staged.game, *(f for _, f in staged.extras)] if f.note]
        return JobResult("install", game_id, entry.get("title") or game_id, game, list(dict.fromkeys(notes)))


def _require_extract_mode(item: dict, platform: str) -> None:
    """Run-mode installers (Task 14) and web games are not installed by this path yet."""
    for source in platform_sources(item, platform):
        install = source.get("install") or {}
        if install.get("method") == "url" or (install.get("installer") or {}).get("mode") == "run":
            raise NotImplementedError(f"{item.get('id')}: install method not supported yet")


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
