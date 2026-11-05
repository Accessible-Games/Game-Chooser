"""Runtime dependencies: installed before the game that needs them, and never removed.

A game's `dependencies` are resolved recursively, deepest first; a cycle, or a dependency that
isn't a runtime in the catalog, refuses the install before anything is downloaded. A runtime is
skipped when it has a runtime record or its `detect` hint says it's present.

With "Register shared components system-wide" on (the default), each missing runtime is installed
machine-wide, into its own folder, `<runtimes root>/<id>` (`%ProgramData%\\GameChooser\\runtimes`):
an archive or loose file is unpacked (with its file steps) in the work folder and copied there, its
system steps run there (a `register` step goes through one administrator prompt), and a
`kind="runtime"` record is saved. A runtime that is an installer that must run is run untracked and
checked again afterwards. Nothing here is ever removed: other games may need it.

Install records are per Windows account, but the runtimes folder is shared by every account, so a
successful install also leaves a marker in the runtime's folder (MARKER_NAME) that every account
reads as "present". A folder that is already there without a valid marker isn't Game Chooser's to
remove: it is adopted if its files match the download (missing files are added, none replaced), and
refused otherwise. Only a folder this run created is removed when an install fails.

Every account can create folders in %ProgramData%, so a runtime folder is trusted only when it is a
real folder (no link or junction anywhere on the way to it) and is owned by Administrators or SYSTEM,
or by the current user. A runtime with an elevated step (regsvr32, an HKLM setting) never gets its
folder from the player: one elevated command seals the folders above it, makes its folder (or finds
it already sealed by an earlier install), seals it (owner Administrators, inheritance off,
Administrators and SYSTEM full control, Users read and execute, nothing for anyone else), and copies
the files in from where the player unpacked them, so every file there was made by an administrator.
The folder is then walked and its files hashed again, so a file swapped in the player's staging is
caught and nothing is registered. The marker is copied in by the elevated command that runs the last
regsvr32, after it has succeeded, so it exists only for a registered runtime. A marker counts only in
a folder owned by Administrators or SYSTEM, or, for a runtime with no elevated step, by the current
user.

With the preference off, nothing gets an administrator prompt for a game's runtimes. A COM runtime
(one with a `register` step) is copied into the game's own folder instead, beside its exe, with a
registration-free COM manifest (engine.comfree). A runtime that only exists as an installer can't be
made private, so the game is installed without it and a note says it needs that runtime. Runtimes
with neither are still installed machine-wide (the preference covers COM components only).
The game's record lists the runtimes made private (`private_runtimes`); Repair and Undo make
copies of those again, and of no other.
"""

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import comfree, messages
from .entries import platform_sources
from .errors import Cancelled, ElevationDeclined, ExtractError, InstallFailed, StepError, UnsafePath
from .messages import Progress
from .paths import norm_key, safe_join
from .records import (RUNTIME_OWNER_PREFIX, InstalledSource, InstallRecord, RecordedFile, hash_tree,
                      is_runtime_owner, runtime_owner)
from .run_installer import run_installer_untracked
from .safety import _prune_empty_dirs, _resolve_game_path, is_link
from .sources import hash_file
from .steps import StepContext, run_file_steps, run_system_steps, undo_steps

log = logging.getLogger(__name__)

_CLASSES = "SOFTWARE\\Classes\\"
# Left in a runtime's folder once it is installed: {"id", "source_sha256", "files": {path: sha256}}.
MARKER_NAME = ".gamechooser-runtime.json"

ADMINISTRATORS, SYSTEM, USERS = "S-1-5-32-544", "S-1-5-18", "S-1-5-32-545"
CREATOR_OWNER = "S-1-3-0"
_TRUSTED_OWNERS = frozenset({ADMINISTRATORS, SYSTEM})
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_SID = re.compile(r"S-1-[0-9]+(-[0-9]+)*")


class WindowsSecurity:
    """Who owns a file or folder, and who is running Game Chooser, as string SIDs (None if unknown)."""

    def owner(self, path: Path) -> str | None:
        try:
            import pywintypes
            import win32security
        except ImportError:
            return None
        try:
            sd = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
                                                    win32security.OWNER_SECURITY_INFORMATION)
            return win32security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner())
        except pywintypes.error:
            return None

    def current_user(self) -> str | None:
        try:
            import pywintypes
            import win32api
            import win32security
        except ImportError:
            return None
        try:
            token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
            sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
            return win32security.ConvertSidToStringSid(sid)
        except pywintypes.error:
            return None


# cmd.exe refuses a command line longer than 8191 characters; leave room for "cmd.exe " and the quoting.
COMMAND_LINE_LIMIT = 8000


def _icacls(path: Path, what: str) -> str:
    # Folders only, one path each, /L (a link itself, never what it points to), no /T, no wildcard: nothing
    # planted inside a folder, and no hard link, can steer an elevated change onto anything else.
    return f'icacls "{path}" {what} /L /Q'


def _real(path: Path) -> str:
    # Stops the chain if `path`, just sealed, is a link or junction after all: sealed, it can't be swapped
    # any more, so the check holds for everything the chain does with it afterwards.
    return f'(fsutil reparsepoint query "{path}" >nul 2>&1 && exit 1 || type nul)'


def _absent(path: Path) -> str:
    return f'(if exist "{path}" exit 1)'


def _depth_first_order(files: list[str]) -> list[tuple[str, bool]]:
    """(relative path, is a folder) for every expected entry, each folder before anything inside it."""
    entries: dict[str, bool] = {}
    for rel in files:
        parts = PurePosixPath(rel).parts
        for i in range(1, len(parts)):
            entries.setdefault("/".join(parts[:i]), True)
        entries[rel] = False
    return sorted(entries.items(), key=lambda item: (item[0].count("/"), item[0].lower()))


def register_and_mark_args(dll: Path, staged_marker: Path, marker: Path) -> str:
    """The arguments of the elevated `cmd.exe` that runs a runtime's last register step: regsvr32, and then,
    only if it succeeded, the marker copied in from the player's work folder. A failed copy doesn't fail
    the command (the runtime is registered; the next install adopts the folder)."""
    return f'/d /v:off /s /c "regsvr32.exe /s "{dll}" && (copy /b "{staged_marker}" "{marker}" >nul || type nul)"'


def copy_marker_args(staged_marker: Path, marker: Path) -> str:
    """The arguments of the elevated `cmd.exe` that only copies the marker in: for a runtime whose last step
    isn't a register step, once every step has succeeded."""
    return f'/d /v:off /s /c "copy /b "{staged_marker}" "{marker}" >nul"'


def lock_down_commands(folder: Path, user_sid: str, staging: Path, files: list[str],
                       existing: set[str] | None = None) -> list[tuple[str, str]]:
    """The elevated commands that make and fill a runtime's folder and lock it, and the two folders above
    it, down: [(exe, arguments)], one per administrator prompt (usually one; more only if the command
    line would be too long). `files` are the download's files, relative to `folder` and to `staging`
    (where the player unpacked them) with forward slashes. `existing` is None when `folder` isn't there
    yet; else the expected entries it already holds (an Administrators-owned folder being adopted).
    `user_sid` is checked by the caller and appears in no command: the installing user gets no grant.

    The chain stops at the first command that fails, and never changes the owner or permissions of a
    FILE (a hard link shares them with its other names): icacls runs on folders only, one explicit path
    at a time with /L, never /T or a wildcard. In order:
    - `<GameChooser>`, then `<GameChooser>/runtimes`: sealed (owner Administrators, inheritance off,
      Administrators and SYSTEM full control, Users read and execute), then checked to be a real folder.
      `runtimes` also lets Users create a folder in it (AD, on that folder only; no delete-child) and
      gives the creator full control of the folder they made (CREATOR OWNER, inherited only), for
      runtimes that need no elevated step and are unpacked by the player.
    - `folder`: made with mkdir, which fails if anything appeared there meanwhile, then sealed; nobody
      but administrators can put anything in it afterwards (the marker arrives with the last register
      step, see register_and_mark_args). A folder being adopted is sealed, checked to be real, and must
      hold no marker, which would say "installed" before anything is registered.
    - Each expected sub-folder the same way, parent first; one already there is sealed and checked.
    - Each expected file not already there is copied in from `staging` by explicit path, so the
      elevated process creates it and it inherits only from its sealed folder. Into a folder that was
      already there, the copy first checks that nothing (no link, nothing at all) sits at that name.
    """
    runtimes, game_chooser = folder.parent, folder.parent.parent
    base = f"*{ADMINISTRATORS}:(OI)(CI)F *{SYSTEM}:(OI)(CI)F *{USERS}:(OI)(CI)RX"
    owner = f"/setowner *{ADMINISTRATORS}"
    present = None if existing is None else {norm_key(rel) for rel in existing}

    def seal(path: Path, extra: str = "") -> list[str]:
        return [_icacls(path, owner), _icacls(path, f"/inheritance:r /grant:r {base}{extra}")]

    def there(rel: str) -> bool:
        return present is not None and norm_key(rel) in present

    commands = [*seal(game_chooser), _real(game_chooser), *seal(runtimes, f" *{CREATOR_OWNER}:(OI)(CI)(IO)F"),
                _icacls(runtimes, f"/grant *{USERS}:(AD)"), _real(runtimes)]
    if present is None:
        commands += [f'mkdir "{folder}"', *seal(folder)]
    else:
        commands += [*seal(folder), _real(folder), _absent(folder / MARKER_NAME)]
    lead = len(commands)  # always in the first invocation: nothing inside is touched before this is done
    for rel, is_folder in _depth_first_order(files):
        parts = PurePosixPath(rel).parts
        path = folder.joinpath(*parts)
        if is_folder:
            commands += [*seal(path), _real(path)] if there(rel) else [f'mkdir "{path}"', *seal(path)]
        elif not there(rel):
            if present is not None and (len(parts) == 1 or there("/".join(parts[:-1]))):
                commands += [_real(path), _absent(path)]  # the folder was there: so could something at this name
            commands.append(f'copy /b "{staging.joinpath(*parts)}" "{path}" >nul')

    invocations, batch = [], []
    for i, command in enumerate(commands):
        if i >= lead and len(_cmd_args(batch + [command])) > COMMAND_LINE_LIMIT:
            invocations.append(batch)
            batch = []
        batch.append(command)
    invocations.append(batch)
    return [("cmd.exe", _cmd_args(batch)) for batch in invocations]


def _cmd_args(commands: list[str]) -> str:
    return f'/d /v:off /s /c "{" && ".join(commands)}"'


def linked_component(root: Path, folder: Path) -> Path | None:
    """The first of `root`'s parent, `root` and `folder` that is a link, junction or other reparse point."""
    for path in (Path(root).parent, Path(root), Path(folder)):
        try:
            attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        except OSError:
            continue
        if is_link(path) or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            return path
    return None


def _owner_is(security, path: Path, allow_user: bool) -> bool:
    """True if `path` is owned by Administrators or SYSTEM (or, with `allow_user`, by the current user)."""
    owner = security.owner(path)
    if owner in _TRUSTED_OWNERS:
        return True
    return allow_user and owner is not None and owner == security.current_user()


def _elevated_steps(steps: list[dict]) -> bool:
    return any(s.get("action") == "register" or (s.get("action") == "registry" and
                                                  str(s.get("hive", "")).upper() == "HKLM") for s in steps)


def _has_elevated_steps(entry: dict) -> bool:
    return any(_elevated_steps(source.get("steps") or []) for source in entry.get("sources") or [])
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


def runtime_present(entry: dict, records, registry, root: Path | None = None, security=None) -> bool:
    """True if Game Chooser installed it: a runtime record (this account), or a trusted marker in its
    folder under `root` (any account; `security` reads who owns it); or if its `detect` hint finds it."""
    record = records.load(entry["id"])
    if record is not None and record.kind == "runtime":
        return True
    if root is not None and security is not None and marker_present(
            Path(root) / entry["id"], entry["id"], security, allow_user=not _has_elevated_steps(entry)):
        return True
    return detect_present(entry.get("detect"), registry)


def _read_marker(folder: Path) -> dict | None:
    try:
        data = json.loads((folder / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return None
    return data


def marker_present(folder: Path, runtime_id: str, security, allow_user: bool = False) -> bool:
    """True if `folder` has Game Chooser's marker for `runtime_id`, every file it lists is still there,
    nothing on the way to it is a link, and it is owned by Administrators or SYSTEM (or, with
    `allow_user`, for a runtime with no elevated step, by the current user)."""
    folder = Path(folder)
    if linked_component(folder.parent, folder) is not None or not _owner_is(security, folder, allow_user):
        return False
    data = _read_marker(folder)
    if data is None or data.get("id") != runtime_id:
        return False
    for rel in data["files"]:
        try:
            path, linked = _resolve_game_path(folder, rel)
        except (UnsafePath, TypeError):
            return False
        if linked or not path.is_file():
            return False
    return True


def _write_marker(folder: Path, marker: dict) -> None:
    """For a runtime with no elevated step, whose folder the player filled. Best effort: without it,
    another account adopts the folder instead of skipping it."""
    try:
        (folder / MARKER_NAME).write_text(json.dumps(marker, indent=2), encoding="utf-8")
    except OSError:
        log.warning("Couldn't write the runtime marker in %s", folder, exc_info=True)


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


def missing_runtimes(entry: dict, entries: dict, records, registry, root: Path | None = None,
                     security=None) -> list[dict]:
    """The runtimes `entry` needs that aren't present, deepest first (see dependency_order)."""
    return [r for r in dependency_order(entry, entries)
            if not runtime_present(r, records, registry, root, security)]


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
    missing = missing_runtimes(entry, ctx.entries, ctx.records, ctx.registry, ctx.runtimes_root, ctx.security)
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


def _register_names(runtime: dict) -> set[str]:
    return {PureWindowsPath(step["path"]).name.lower()
            for source in runtime.get("sources") or [] for step in source.get("steps") or []
            if step.get("action") == "register" and isinstance(step.get("path"), str)}


def recorded_private(rec: InstallRecord, entry: dict, entries: dict) -> list[str]:
    """The ids of the runtimes the game's install made private copies of.

    Records from before `private_runtimes` was kept: the runtimes that own recorded files, plus each
    COM dependency whose DLL is recorded beside a recorded `<exe>.manifest`.
    """
    if rec.private_runtimes is not None:
        return list(rec.private_runtimes)
    ids = dict.fromkeys(f.owner[len(RUNTIME_OWNER_PREFIX):] for f in rec.files.values() if is_runtime_owner(f.owner))
    paths = [PurePosixPath(f.path) for f in rec.files.values()]
    manifests = {p.parent for p in paths if p.name.lower().endswith(".exe.manifest")}
    beside = {p.name.lower() for p in paths if p.parent in manifests}
    try:
        dependencies = dependency_order(entry, entries)
    except InstallFailed:
        dependencies = []
    for runtime in dependencies:
        if is_com_runtime(runtime) and _register_names(runtime) & beside:
            ids.setdefault(runtime["id"])
    return list(ids)


def private_runtimes(rec: InstallRecord, entry: dict, ctx, ids: list[str] | None = None) -> list[dict]:
    """The runtime entries a rebuild (Repair, Undo) makes private copies of: exactly those the version
    being rebuilt had (`ids`, else the record's), never a new one. Raises InstallFailed if one of them
    isn't a runtime in the catalog any more."""
    entries = []
    for runtime_id in recorded_private(rec, entry, ctx.entries) if ids is None else ids:
        runtime = ctx.entries.get(runtime_id)
        if runtime is None or runtime.get("kind") != "runtime":
            raise InstallFailed(messages.DEPENDENCY_UNAVAILABLE.format(title=_title(entry)))
        entries.append(runtime)
    return entries


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
            if _runs_installer(fetched.source):
                _run_installer(ctx, runtime, fetched, target, progress, cancel)
            else:
                _unpack(ctx, runtime, fetched, target, work_dir / "unpacked", cancel)
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
    # A failed or cancelled run removes only the files it added to `target`.
    run_installer_untracked(fetched.path, installer, target, ctx, progress, cancel, title=title)
    if runtime.get("detect") and not runtime_present(runtime, ctx.records, ctx.registry):
        raise InstallFailed(messages.FAILED.format(title=title))
    # No marker: the installer put the runtime wherever it likes, so the record or the detect hint says.
    try:
        ctx.records.save(InstallRecord(
            catalog_id=runtime["id"], kind="runtime", library_name="", folder=str(target), launch_path="",
            source=_source(runtime, fetched), extras={}, files={}, steps=[], method="run", tracked=False))
    except OSError as e:
        raise InstallFailed(messages.FAILED.format(title=title)) from e


def _unpack(ctx, runtime: dict, fetched, target: Path, unpack_dir: Path, cancel) -> None:
    """Unpack into `unpack_dir` and run the file steps there; get the result into `target`, run the system
    steps there, and leave the record and the marker.

    A runtime with no elevated step is copied into `target` by the player. One with an elevated step
    never is: one elevated command makes `target` (or seals it again), seals it and copies the files in
    (lock_down_commands), and the folder is then walked and hashed again before any step runs, so a
    swap in the player's staging is caught and nothing is registered.

    `target`, or a folder on the way to it, that is a link is refused. A `target` that is already there
    (another account's install, or one whose record was lost) is never removed or changed: it is adopted
    only if no file in it differs from the download and it is owned by Administrators or SYSTEM (or, for
    a runtime with no elevated step, the current user); for a runtime with an elevated step it may hold
    nothing else and no marker. Only its missing files are added. Otherwise the install is refused. On
    any failure, the system steps already applied are undone (but in a folder it found, never a
    registration, which may predate this run). A folder the player filled is removed if this run made
    it, else just the files it added; a sealed folder stays as it is (without a marker it counts as
    absent, and a retry adopts it).
    """
    title = _title(runtime)
    install = fetched.source.get("install") or {}
    steps = list(fetched.source.get("steps") or [])
    elevated = _elevated_steps(steps)
    step_ctx = StepContext(target.absolute(), ctx.elevator, ctx.registry)
    applied: list[dict] = []
    linked = linked_component(ctx.runtimes_root, target)
    if linked is not None:
        raise InstallFailed(messages.RUNTIME_FOLDER_LINKED.format(title=title, path=linked))
    found = os.path.lexists(target)
    made = False  # True once this run (or its elevated command) has created `target`
    added = PrivateCopy(runtime, [], [], [])  # what the player added to a folder it found
    try:
        root = ctx.extractor.extract(fetched.path, unpack_dir, install, cancel)
        run_file_steps(steps, root)
        _check(cancel)
        incoming = hash_tree(root)
        if found and not _adoptable(ctx, target, incoming, elevated):
            raise InstallFailed(messages.RUNTIME_FOLDER_NOT_OURS.format(title=title, path=target))
        if elevated:
            _lock_down(ctx, target, title, root, incoming, found)
            made = not found
            # Checked again now that no one else can change it: a junction anywhere on the way to it or
            # inside it, an entry the download doesn't have, a hard link, or a file swapped in staging.
            if (linked_component(ctx.runtimes_root, target) is not None
                    or not _owner_is(ctx.security, target, allow_user=False)
                    or not _only_expected(target, incoming) or not _holds_download(target, incoming)):
                raise InstallFailed(messages.RUNTIME_FOLDER_CHANGED.format(title=title, path=target))
        else:
            if not found:
                target.mkdir()  # FileExistsError if something made it meanwhile: then it isn't ours
                made = True
            _copy_into(root, target, added)
        marker = _marker(runtime["id"], fetched.sha256, incoming)
        if elevated:
            # The marker can only get into the sealed folder elevated: it rides on the last register step.
            staged_marker = unpack_dir.parent / MARKER_NAME
            staged_marker.write_text(json.dumps(marker, indent=2), encoding="utf-8")
            _run_steps_marking(ctx, steps, step_ctx, target, staged_marker, applied)
        else:
            for step in steps:  # one at a time, so a failure leaves `applied` true to what was done
                applied += run_system_steps([step], step_ctx)
        files = {key: RecordedFile(display, sha, runtime["id"]) for key, (display, sha) in incoming.items()}
        ctx.records.save(InstallRecord(
            catalog_id=runtime["id"], kind="runtime", library_name="", folder=str(target), launch_path="",
            source=_source(runtime, fetched), extras={}, files=files, steps=applied,
            method=install.get("method", "")))
    except BaseException as e:
        undo = [s for s in applied if made or s.get("action") != "register"]
        undo_failures = [*getattr(e, "undo_failures", []), *undo_steps(undo, step_ctx)]
        if not elevated:  # a sealed folder isn't the player's to change (R39)
            if made:
                _clear(target)
            else:
                _remove_copy(added, target)
        if isinstance(e, StepError):
            if isinstance(e.__cause__, ElevationDeclined):
                raise InstallFailed(messages.ELEVATION_DECLINED.format(what=title)) from e
            raise InstallFailed("\n\n".join([str(e), *undo_failures])) from e
        if isinstance(e, ExtractError):
            raise InstallFailed(str(e)) from e
        if isinstance(e, Exception) and not isinstance(e, (InstallFailed, Cancelled)):
            raise InstallFailed(messages.FAILED.format(title=title)) from e
        raise
    if not elevated:
        _write_marker(target, marker)
    elif not (target / MARKER_NAME).is_file():
        log.warning("The runtime marker wasn't copied into %s; the next install will adopt the folder", target)


def _marker(runtime_id: str, source_sha256: str, incoming: dict[str, tuple[str, str]]) -> dict:
    return {"id": runtime_id, "source_sha256": source_sha256, "files": {d: s for d, s in incoming.values()}}


def _run_steps_marking(ctx, steps: list[dict], step_ctx: StepContext, target: Path, staged_marker: Path,
                       applied: list[dict]) -> None:
    """Run the system steps one at a time (so `applied`, which is the caller's, is true to what was done
    when one fails). The last step, when it is a register step, also copies the marker in once regsvr32
    has succeeded; otherwise one more elevated command copies it after every step has succeeded. A
    marker that can't be copied doesn't fail the install: the next install adopts the folder."""
    for i, step in enumerate(steps):
        if i == len(steps) - 1 and step.get("action") == "register":
            applied.append(_register_and_mark(ctx, step, target, staged_marker))
        else:
            applied += run_system_steps([step], step_ctx)
    if not steps or steps[-1].get("action") != "register":
        try:
            code = ctx.elevator.run("cmd.exe", copy_marker_args(staged_marker, target / MARKER_NAME), True)
        except (ElevationDeclined, StepError):
            code = -1
        if code != 0:
            log.warning("The runtime marker couldn't be copied into %s", target)


def _register_and_mark(ctx, step: dict, target: Path, staged_marker: Path) -> dict:
    """A register step as steps.run_system_steps runs it (same checks, same errors), through
    register_and_mark_args so the marker arrives with a successful registration."""
    rel = step.get("path")
    try:
        path = safe_join(target, str(rel))
    except (UnsafePath, TypeError) as e:
        raise StepError(messages.STEP_FILE_MISSING.format(path=rel)) from e
    if not path.is_file():
        raise StepError(messages.STEP_FILE_MISSING.format(path=rel))
    try:
        code = ctx.elevator.run("cmd.exe", register_and_mark_args(path, staged_marker, target / MARKER_NAME), True)
    except ElevationDeclined as e:
        raise StepError(messages.ELEVATION_DECLINED.format(what=path.name)) from e
    if code != 0:
        raise StepError(messages.STEP_REGISTER_FAILED.format(name=path.name))
    return {**step}


def _adoptable(ctx, target: Path, incoming: dict[str, tuple[str, str]], elevated: bool) -> bool:
    """True if a `target` that is already there may be used for this install (see _unpack)."""
    if not _owner_is(ctx.security, target, allow_user=not elevated) or not _matches_download(target, incoming):
        return False
    if elevated:
        # The elevated command adds the missing files and leaves everything else alone, so what is there
        # must be what an earlier install's elevated command left: every expected entry owned by
        # Administrators or SYSTEM (a matching file the player put there would be registered as theirs,
        # and its owner can always regain write access), nothing but the download's entries, and no
        # marker, which would say "installed" as soon as the files are complete, before anything is
        # registered.
        files = [display for display, _ in incoming.values()]
        if not all(_owner_is(ctx.security, target / rel, allow_user=False)
                   for rel, _ in _depth_first_order(files) if os.path.lexists(target / rel)):
            return False
        return _only_expected(target, incoming) and not os.path.lexists(target / MARKER_NAME)
    return True


def _lock_down(ctx, target: Path, title: str, staging: Path, incoming: dict[str, tuple[str, str]],
               found: bool) -> None:
    """Run lock_down_commands elevated (the runtime's folder, made or adopted, and the two above it),
    passed inline: never through a script file a player could swap. A declined prompt is a declined
    install (R6)."""
    user = ctx.security.current_user()
    files = [display for display, _ in incoming.values()]
    if (not user or not _SID.fullmatch(user) or "%" in str(target) or "%" in str(staging)
            or any("%" in f for f in files)):
        raise InstallFailed(messages.RUNTIME_FOLDER_NOT_SECURED.format(title=title, path=target))  # cmd expands %
    existing = {rel for rel, _ in _depth_first_order(files) if os.path.lexists(target / rel)} if found else None
    for exe, args in lock_down_commands(target, user, staging, files, existing):
        try:
            code = ctx.elevator.run(exe, args, True)
        except ElevationDeclined as e:
            raise InstallFailed(messages.ELEVATION_DECLINED.format(what=title)) from e
        except StepError as e:
            raise InstallFailed(messages.RUNTIME_FOLDER_NOT_SECURED.format(title=title, path=target)) from e
        if code != 0:
            raise InstallFailed(messages.RUNTIME_FOLDER_NOT_SECURED.format(title=title, path=target))


def _expected_entries(incoming: dict[str, tuple[str, str]]) -> tuple[set[str], set[str]]:
    """(files, folders) the runtime's folder may hold, as norm_keys: the download's files and the marker."""
    files = {norm_key(display) for display, _ in incoming.values()} | {norm_key(MARKER_NAME)}
    folders = {norm_key(rel) for rel, is_folder in _depth_first_order(sorted(files)) if is_folder}
    return files, folders


def _only_expected(folder: Path, incoming: dict[str, tuple[str, str]]) -> bool:
    """True if `folder` holds nothing but expected plain files and folders: walked without following
    anything, it refuses any link, junction or other reparse point, any hard-linked file, and any entry
    the download doesn't have."""
    files, folders = _expected_entries(incoming)
    pending = [Path(folder)]
    try:
        while pending:
            current = pending.pop()
            for entry in os.scandir(current):
                path = Path(entry.path)
                info = os.lstat(path)
                key = norm_key(path.relative_to(folder).as_posix())
                if is_link(path) or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
                    return False
                if entry.is_dir(follow_symlinks=False):
                    if key not in folders:
                        return False
                    pending.append(path)
                elif not entry.is_file(follow_symlinks=False) or key not in files or info.st_nlink > 1:
                    return False
    except (OSError, ValueError):
        return False
    return True


def _holds_download(folder: Path, incoming: dict[str, tuple[str, str]]) -> bool:
    """True if `folder` has every file of the download, each a plain file with the download's contents."""
    for display, sha in incoming.values():
        try:
            path, linked = _resolve_game_path(folder, display)
            if linked or not path.is_file() or hash_file(path) != sha:
                return False
        except (UnsafePath, OSError):
            return False
    return True


def _matches_download(folder: Path, incoming: dict[str, tuple[str, str]]) -> bool:
    """True if every file of the download that `folder` already has is the same file (none differs or is
    a folder or a link there)."""
    for display, sha in incoming.values():
        try:
            path, linked = _resolve_game_path(folder, display)
        except UnsafePath:
            return False
        if linked or (os.path.lexists(path) and (not path.is_file() or hash_file(path) != sha)):
            return False
    return True


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


def finish_private(tree: Path, exe: Path, copies: list[PrivateCopy], title: str) -> tuple[list[str], list[str]]:
    """Once the exe is known: move the copies beside it and write its manifest (engine.comfree).

    If that can't work (an embedded manifest, no readable type library, private COM turned off), the
    copies are removed and the notes say the game needs each shared component. Returns those notes,
    and the ids of the runtimes made private (for the record's `private_runtimes`).
    """
    usable = [c for c in copies if c.usable]
    notes = needs_shared_notes(title, [c.runtime for c in copies if not c.usable])
    if not usable:
        return notes, []
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
        return notes + needs_shared_notes(title, [c.runtime for c in usable]), []
    return notes, [c.runtime["id"] for c in usable]


def _move_beside(copy: PrivateCopy, tree: Path, beside: Path) -> None:
    """Move a copy's files from the tree's top to the exe's folder, keeping their layout. Where the game
    already has a file of that name there, the game's is kept and the copy dropped."""
    old_dirs, copy.dirs = copy.dirs, []
    for path in list(copy.files):
        dest = beside / path.relative_to(tree)
        if os.path.lexists(dest):
            path.unlink()
            copy.files.remove(path)
            continue
        _make_dirs(dest.parent, copy)
        os.replace(path, dest)
        copy.files[copy.files.index(path)] = dest  # kept true to disk, so a failure removes what moved
    copy.dirs = [*old_dirs, *copy.dirs]
    for folder in reversed(old_dirs):
        _prune_empty_dirs(folder, tree)


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
            rec.private_runtimes = [c.runtime["id"] for c in usable]
        except (comfree.PrivateComUnavailable, UnsafePath, OSError, TypeError):
            if wrote_manifest:
                _unlink_quietly(manifest)
            for copy in usable:
                _remove_copy(copy, game_dir)
            notes += needs_shared_notes(title, [c.runtime for c in usable])
            rec.private_runtimes = []
    else:
        rec.private_runtimes = []
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
