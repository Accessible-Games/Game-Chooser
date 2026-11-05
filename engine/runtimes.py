"""Runtime dependencies: installed before the game that needs them, and never removed.

A game's `dependencies` are resolved recursively, deepest first; a cycle, or a dependency that
isn't a runtime in the catalog, refuses the install before anything is downloaded. A runtime is
skipped when it has a runtime record or its `detect` hint says it's present.

With "Register shared components system-wide" on (the default), each missing runtime is installed
machine-wide into its own folder, `<runtimes root>/<id>` (`%ProgramData%\\GameChooser\\runtimes`):
an archive or loose file is unpacked straight there, its file steps and then its system steps run
there (a `register` step goes through one administrator prompt), and a `kind="runtime"` record is
saved. A runtime that is an installer that must run is run untracked and checked again afterwards.
Nothing here is ever removed: other games may need it.

With the preference off, nothing gets an administrator prompt for a game's runtimes. A COM runtime
(one with a `register` step) is copied into the game's own folder instead, beside its exe, with a
registration-free COM manifest (engine.comfree). A runtime that only exists as an installer can't be
made private, so the game is installed without it and a note says it needs that runtime. Runtimes
with neither are still installed machine-wide: they need no administrator permission.
"""

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import comfree, messages
from .entries import platform_sources
from .errors import Cancelled, ElevationDeclined, ExtractError, InstallFailed, StepError, UnsafePath
from .messages import Progress
from .paths import norm_key, safe_join
from .records import InstalledSource, InstallRecord, RecordedFile, hash_tree, is_runtime_owner, runtime_owner
from .run_installer import run_installer_untracked
from .safety import _prune_empty_dirs, _resolve_game_path, is_link
from .sources import hash_file
from .steps import StepContext, run_file_steps, run_system_steps, undo_steps

_CLASSES = "SOFTWARE\\Classes\\"
_HIVE_NAMES = {"HKEY_CLASSES_ROOT": "HKCR", "HKEY_LOCAL_MACHINE": "HKLM", "HKEY_CURRENT_USER": "HKCU"}


def default_runtimes_root() -> Path:
    return Path(os.environ["PROGRAMDATA"]) / "GameChooser" / "runtimes"


# ---- what's present ---------------------------------------------------------------------------------------


def detect_present(detect: dict | None, registry) -> bool:
    """True if the catalog's `detect` hint finds the runtime already on this machine.

    `{"file": "%WINDIR%\\SysWOW64\\msvbvm60.dll"}`: the file exists (environment variables expanded).
    `{"registry": {"hive", "key", "name"?}}`: the value exists; without `name`, the key's default
    value. `HKCR` is looked up where Windows keeps it: HKLM's and then HKCU's `SOFTWARE\\Classes`.
    Any other shape is never present.
    """
    if not isinstance(detect, dict):
        return False
    if "file" in detect:
        path = detect["file"]
        return isinstance(path, str) and bool(path) and Path(os.path.expandvars(path)).exists()
    if "registry" in detect:
        return _registry_present(detect["registry"], registry)
    return False


def _registry_present(spec: object, registry) -> bool:
    if not isinstance(spec, dict):
        return False
    hive, key, name = spec.get("hive"), spec.get("key"), spec.get("name", "")
    if not all(isinstance(v, str) for v in (hive, key, name)):
        return False
    hive = _HIVE_NAMES.get(hive.upper(), hive.upper())
    key = key.strip("\\")
    places = [("HKLM", _CLASSES + key), ("HKCU", _CLASSES + key)] if hive == "HKCR" else [(hive, key)]
    for place_hive, place_key in places:
        try:
            if registry.get(place_hive, place_key, name)[0]:
                return True
        except (StepError, OSError):
            continue
    return False


def is_com_runtime(entry: dict) -> bool:
    """True if any of its sources registers a COM component."""
    return any(step.get("action") == "register"
               for source in entry.get("sources") or [] for step in source.get("steps") or [])


def runtime_present(entry: dict, records, registry) -> bool:
    """True if Game Chooser installed it (a runtime record) or its `detect` hint finds it."""
    record = records.load(entry["id"])
    if record is not None and record.kind == "runtime":
        return True
    return detect_present(entry.get("detect"), registry)


def _title(entry: dict) -> str:
    return entry.get("title") or entry["id"]


def dependency_order(entry: dict, entries: dict) -> list[dict]:
    """Every runtime `entry` depends on, recursively, each once, deepest first.

    Raises InstallFailed (plain words) on a cycle, or on a dependency that isn't a runtime in the catalog.
    """
    title = _title(entry)
    order: list[dict] = []
    done: set[str] = set()
    path = [entry["id"]]

    def visit(item: dict) -> None:
        for dep_id in item.get("dependencies") or []:
            if dep_id in done:
                continue
            if dep_id in path:
                raise InstallFailed(messages.DEPENDENCY_CYCLE.format(title=title))
            dep = entries.get(dep_id)
            if dep is None or dep.get("kind") != "runtime":
                raise InstallFailed(messages.DEPENDENCY_UNAVAILABLE.format(title=title))
            path.append(dep_id)
            visit(dep)
            path.pop()
            done.add(dep_id)
            order.append(dep)

    visit(entry)
    return order


def missing_runtimes(entry: dict, entries: dict, records, registry) -> list[dict]:
    """The runtimes `entry` needs that aren't present, deepest first (see dependency_order)."""
    return [r for r in dependency_order(entry, entries) if not runtime_present(r, records, registry)]


# ---- what to do about them --------------------------------------------------------------------------------


def _runs_installer(source: dict) -> bool:
    install = source.get("install") or {}
    return install.get("method") == "installer" and (install.get("installer") or {}).get("mode") == "run"


def _is_installer_runtime(entry: dict, platform: str) -> bool:
    sources = platform_sources(entry, platform)
    return bool(sources) and all(_runs_installer(s) for s in sources)


def needs_shared_notes(title: str, runtimes: list[dict]) -> list[str]:
    return [messages.NEEDS_SHARED_COMPONENT.format(title=title, component=_title(r)) for r in runtimes]


@dataclass
class RuntimePlan:
    """A game's missing runtimes, by what is done about each."""

    shared: list[dict] = field(default_factory=list)   # installed machine-wide, before the game
    private: list[dict] = field(default_factory=list)  # COM runtimes copied into the game's folder
    skipped: list[dict] = field(default_factory=list)  # installers that can't be made private


def plan_runtimes(entry: dict, ctx) -> RuntimePlan:
    """Decide, before anything is downloaded, what happens to each of the game's missing runtimes."""
    missing = missing_runtimes(entry, ctx.entries, ctx.records, ctx.registry)
    if ctx.register_shared_components:
        return RuntimePlan(shared=missing)
    plan = RuntimePlan()
    for runtime in missing:
        if _is_installer_runtime(runtime, ctx.platform):
            plan.skipped.append(runtime)
        elif is_com_runtime(runtime):
            plan.private.append(runtime)
        else:
            plan.shared.append(runtime)
    return plan


def private_runtimes(entry: dict, ctx) -> list[dict]:
    """The COM runtimes a rebuild (Repair, Undo) copies into the game's folder: those still missing here.

    That is what its install did, whatever the preference says now: a runtime installed machine-wide
    has a record, so it isn't missing, and without the copy the rebuild would remove the one the
    install made."""
    return [r for r in missing_runtimes(entry, ctx.entries, ctx.records, ctx.registry)
            if is_com_runtime(r) and not _is_installer_runtime(r, ctx.platform)]


# ---- shared installs --------------------------------------------------------------------------------------


def _check(cancel) -> None:
    if cancel():
        raise Cancelled()


def _clear(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _clear_or_fail(path: Path) -> None:
    _clear(path)
    if os.path.lexists(path):
        raise InstallFailed(messages.TEMP_FOLDER_NOT_CLEARED.format(path=path))


def install_shared(ctx, runtimes: list[dict], work: Path, fetch, progress, cancel) -> None:
    """Install each runtime machine-wide, in order. `fetch(item, work_dir)` downloads the catalog's file
    (raising InstallFailed when none verifies); `work` is an empty folder for the downloads."""
    for runtime in runtimes:
        _check(cancel)
        progress(Progress("installing_dependency", detail=_title(runtime)))
        work_dir = work / runtime["id"]
        _clear_or_fail(work_dir)
        try:
            fetched = fetch(runtime, work_dir)
            _check(cancel)
            _make_root(ctx, runtime)
            target = ctx.runtimes_root / runtime["id"]
            _clear_or_fail(target)  # No record, so a folder here is an earlier try's leftovers.
            if _runs_installer(fetched.source):
                _run_installer(ctx, runtime, fetched, target, progress, cancel)
            else:
                _unpack(ctx, runtime, fetched, target, cancel)
        finally:
            _clear(work_dir)


def _make_root(ctx, runtime: dict) -> None:
    try:
        ctx.runtimes_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise InstallFailed(messages.RUNTIME_FOLDER_NOT_CREATED.format(path=ctx.runtimes_root,
                                                                       title=_title(runtime))) from e


def _source(runtime: dict, fetched) -> InstalledSource:
    return InstalledSource(
        url=fetched.source["url"], sha256=fetched.sha256, size=fetched.size,
        catalog_sha256=fetched.source.get("sha256"), catalog_size=fetched.source.get("size"),
        position=fetched.position, version=runtime.get("version"),
        install=dict(fetched.source.get("install") or {}), steps=list(fetched.source.get("steps") or []))


def _run_installer(ctx, runtime: dict, fetched, target: Path, progress, cancel) -> None:
    """Run the runtime's installer untracked; where it has a `detect` hint, it must find the runtime after.

    The record (untracked, no files) only stops the installer being run again for the next game.
    """
    title = _title(runtime)
    installer = dict((fetched.source.get("install") or {}).get("installer") or {})
    run_installer_untracked(fetched.path, installer, target, ctx, progress, cancel, title=title)
    if runtime.get("detect") and not runtime_present(runtime, ctx.records, ctx.registry):
        raise InstallFailed(messages.FAILED.format(title=title))
    try:
        ctx.records.save(InstallRecord(
            catalog_id=runtime["id"], kind="runtime", library_name="", folder=str(target), launch_path="",
            source=_source(runtime, fetched), extras={}, files={}, steps=[], method="run", tracked=False))
    except OSError as e:
        raise InstallFailed(messages.FAILED.format(title=title)) from e


def _unpack(ctx, runtime: dict, fetched, target: Path, cancel) -> None:
    """Unpack straight into `target`, run the file steps and then the system steps there, and record it.

    On any failure the system steps already applied are undone and `target` is removed.
    """
    title = _title(runtime)
    install = fetched.source.get("install") or {}
    steps = list(fetched.source.get("steps") or [])
    step_ctx = StepContext(target.absolute(), ctx.elevator, ctx.registry)
    applied: list[dict] = []
    try:
        root = ctx.extractor.extract(fetched.path, target, install, cancel)
        if root != target:  # `extract_dir`: only that subfolder is the runtime
            holding = target.with_name(target.name + ".unpacking")
            _clear_or_fail(holding)
            os.replace(root, holding)
            _clear_or_fail(target)
            os.replace(holding, target)
        run_file_steps(steps, target)
        _check(cancel)
        applied = run_system_steps(steps, step_ctx)  # undoes what it applied if a step fails
        files = {key: RecordedFile(display, sha, runtime["id"]) for key, (display, sha) in hash_tree(target).items()}
        ctx.records.save(InstallRecord(
            catalog_id=runtime["id"], kind="runtime", library_name="", folder=str(target), launch_path="",
            source=_source(runtime, fetched), extras={}, files=files, steps=applied,
            method=install.get("method", "")))
    except BaseException as e:
        undo_failures = [*getattr(e, "undo_failures", []), *undo_steps(applied, step_ctx)]
        _clear(target)
        if isinstance(e, StepError):
            if isinstance(e.__cause__, ElevationDeclined):
                raise InstallFailed(messages.ELEVATION_DECLINED.format(what=title)) from e
            raise InstallFailed("\n\n".join([str(e), *undo_failures])) from e
        if isinstance(e, ExtractError):
            raise InstallFailed(str(e)) from e
        if isinstance(e, Exception) and not isinstance(e, (InstallFailed, Cancelled)):
            raise InstallFailed(messages.FAILED.format(title=title)) from e
        raise


# ---- private copies (the preference off) ------------------------------------------------------------------


@dataclass
class PrivateCopy:
    """A COM runtime's files copied into a staged game tree, waiting for the game's exe to be found."""

    runtime: dict
    files: list[Path]   # the files this copy created in the tree
    dirs: list[Path]    # the folders it created for them
    dlls: list[str]     # its COM servers (the `register` steps' paths), relative to its own folder
    usable: bool = True
    root: Path | None = None  # where it was unpacked in staging


def stage_private(ctx, runtimes: list[dict], tree: Path, staging: Path, work: Path, fetch,
                  progress, cancel) -> list[PrivateCopy]:
    """Download each COM runtime, unpack it in staging, run its file steps, and copy its files into
    `tree` (a file the game already ships is kept, not replaced). Call before the exe is detected."""
    copies = unpack_private(ctx, runtimes, staging, work, fetch, progress, cancel)
    for copy in copies:
        if not copy.usable:
            continue
        try:
            _copy_into(copy.root, tree, copy)
        except OSError as e:
            _remove_copy(copy, tree)
            raise InstallFailed(messages.FAILED.format(title=_title(copy.runtime))) from e
    return copies


def unpack_private(ctx, runtimes: list[dict], staging: Path, work: Path, fetch,
                   progress, cancel) -> list[PrivateCopy]:
    """Download each COM runtime and unpack it in `staging/runtimes/<id>`, with its file steps run there.
    A runtime whose downloaded file is an installer that must run can't be copied: it is marked unusable."""
    copies = []
    for runtime in runtimes:
        _check(cancel)
        progress(Progress("installing_dependency", detail=_title(runtime)))
        work_dir, unpacked = work / "private" / runtime["id"], staging / "runtimes" / runtime["id"]
        _clear_or_fail(work_dir)
        _clear_or_fail(unpacked)
        try:
            fetched = fetch(runtime, work_dir)
            install = fetched.source.get("install") or {}
            if _runs_installer(fetched.source) or install.get("method") == "url":
                copies.append(PrivateCopy(runtime, [], [], [], usable=False))
                continue
            _check(cancel)
            root = ctx.extractor.extract(fetched.path, unpacked, install, cancel)
            steps = list(fetched.source.get("steps") or [])
            run_file_steps(steps, root)
        except (ExtractError, StepError) as e:
            raise InstallFailed(str(e)) from e
        except (ValueError, OSError) as e:
            raise InstallFailed(messages.FAILED.format(title=_title(runtime))) from e
        finally:
            _clear(work_dir)
        _check(cancel)
        copies.append(PrivateCopy(runtime, [], [], [s.get("path") for s in steps if s.get("action") == "register"],
                                  root=root))
    return copies


def _copy_into(root: Path, tree: Path, copy: PrivateCopy) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not is_link(Path(dirpath) / d)]
        for name in filenames:
            src = Path(dirpath) / name
            dest = tree / src.relative_to(root)
            if is_link(src) or os.path.lexists(dest):
                continue
            _make_dirs(dest.parent, copy)
            shutil.copy2(src, dest)
            copy.files.append(dest)


def _make_dirs(folder: Path, copy: PrivateCopy) -> None:
    missing = []
    while not os.path.lexists(folder):
        missing.append(folder)
        folder = folder.parent
    for made in reversed(missing):
        made.mkdir()
        copy.dirs.append(made)


def _remove_copy(copy: PrivateCopy, tree: Path) -> None:
    for path in copy.files:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    for folder in reversed(copy.dirs):
        _prune_empty_dirs(folder, tree)
    copy.files, copy.dirs = [], []


def is_private_file(copies: list[PrivateCopy]):
    """A test for exe detection: True for a file a private copy put in the tree (never the game's exe)."""
    ours = {os.path.normcase(str(p)) for c in copies for p in c.files}
    return lambda path: os.path.normcase(str(path)) in ours


def finish_private(tree: Path, exe: Path, copies: list[PrivateCopy], title: str) -> list[str]:
    """Once the exe is known: move the copies beside it and write its manifest (engine.comfree).

    If that can't work (an embedded manifest, no readable type library, private COM turned off), the
    copies are removed and the notes say the game needs each shared component. Returns those notes.
    """
    usable = [c for c in copies if c.usable]
    notes = needs_shared_notes(title, [c.runtime for c in copies if not c.usable])
    if not usable:
        return notes
    beside = exe.parent
    try:
        dlls = []
        for copy in usable:
            if beside != tree:
                _move_beside(copy, tree, beside)
            dlls += [safe_join(beside, rel) for rel in copy.dlls]
        if not comfree.PRIVATE_COM_SUPPORTED:
            raise comfree.PrivateComUnavailable("private COM is turned off")
        # Looked up when called, so the reader can be replaced (tests; a later injectable reader).
        comfree.write_private_com(tree, exe, dlls, reader=comfree.read_com_info)
    except (comfree.PrivateComUnavailable, UnsafePath, OSError, TypeError):
        for copy in usable:
            _remove_copy(copy, tree)
        notes += needs_shared_notes(title, [c.runtime for c in usable])
    return notes


def _move_beside(copy: PrivateCopy, tree: Path, beside: Path) -> None:
    """Move a copy's files from the tree's top to the exe's folder, keeping their layout. Where the game
    already has a file of that name there, the game's is kept and the copy dropped."""
    moved, old_dirs = [], copy.dirs
    copy.dirs = []
    for path in copy.files:
        dest = beside / path.relative_to(tree)
        if os.path.lexists(dest):
            path.unlink()
            continue
        _make_dirs(dest.parent, copy)
        os.replace(path, dest)
        moved.append(dest)
    for folder in reversed(old_dirs):
        _prune_empty_dirs(folder, tree)
    copy.files = moved


# ---- private copies after an installer has run --------------------------------------------------------------

def private_after_installer(ctx, rec: InstallRecord, game_dir: Path, copies: list[PrivateCopy],
                            title: str) -> list[str]:
    """For a tracked game whose installer has run and whose launch exe is known: replace its private COM
    copies with `copies` (already unpacked), beside the exe, with the manifest, and save the record.

    The earlier copies' files (recorded with a runtime owner) are removed where unchanged; a changed
    one is the player's now. The new files are recorded with their runtime as owner, so uninstall
    removes them and the extras never claim them. If the copies can't work, they are removed again
    and the notes say the game needs each shared component. Returns those notes.
    """
    _remove_recorded_private(rec, game_dir)
    usable = [c for c in copies if c.usable]
    notes = needs_shared_notes(title, [c.runtime for c in copies if not c.usable])
    if usable:
        exe = game_dir / rec.launch_path
        manifest = exe.with_name(exe.name + ".manifest")
        wrote_manifest = False
        try:
            for copy in usable:
                _copy_into(copy.root, exe.parent, copy)
            dlls = [safe_join(exe.parent, rel) for copy in usable for rel in copy.dlls]
            if not comfree.PRIVATE_COM_SUPPORTED:
                raise comfree.PrivateComUnavailable("private COM is turned off")
            comfree.write_private_com(game_dir, exe, dlls, reader=comfree.read_com_info)
            wrote_manifest = True
            added = {path: copy.runtime["id"] for copy in usable for path in copy.files}
            added[manifest] = usable[0].runtime["id"]
            recorded = {}
            for path, runtime_id in added.items():
                rel = path.relative_to(game_dir).as_posix()
                recorded[norm_key(rel)] = RecordedFile(rel, hash_file(path), runtime_owner(runtime_id))
            rec.files.update(recorded)
        except (comfree.PrivateComUnavailable, UnsafePath, OSError, TypeError):
            if wrote_manifest:
                _unlink_quietly(manifest)
            for copy in usable:
                _remove_copy(copy, game_dir)
            notes += needs_shared_notes(title, [c.runtime for c in usable])
    ctx.records.save(rec)
    return notes


def _remove_recorded_private(rec: InstallRecord, game_dir: Path) -> None:
    for key, recorded in list(rec.files.items()):
        if not is_runtime_owner(recorded.owner):
            continue
        try:
            path, linked = _resolve_game_path(game_dir, recorded.path)
            if not linked and path.is_file() and hash_file(path) == recorded.sha256:
                path.unlink()
                _prune_empty_dirs(path.parent, game_dir)
        except UnsafePath:
            pass
        except OSError:
            continue  # still there (in use?): it stays recorded
        rec.files.pop(key)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
