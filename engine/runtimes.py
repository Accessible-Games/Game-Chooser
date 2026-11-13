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
it already sealed by an earlier install), seals it to Administrators and SYSTEM alone (owner
Administrators, inheritance off, nothing for anyone else), and copies the files in from where the
player unpacked them, checking each copy's SHA256 against the download's before going on: a copy
that reads something else (a redirect planted in staging while the prompt waited) is deleted and the
chain stops, with the folder still unreadable to everyone but administrators. Only as its last act
does the chain let Users read the folder. It is then walked and its files hashed again, unelevated,
so nothing is registered unless every file is the download's. The marker is written by the elevated
command that runs the last regsvr32, after it has succeeded, so it exists only for a registered
runtime; it holds the download's checksum, which the engine computed, so the elevated command reads
nothing the player supplied. A marker counts only in a folder owned by Administrators or SYSTEM, or,
for a runtime with no elevated step, by the current user.

With the preference off, a COM runtime (one with a `register` step) is copied into the game's own
folder instead, beside its exe, with a registration-free COM manifest (engine.comfree), and gets no
administrator prompt. A runtime that only exists as an installer can't be made private, so the game
is installed without it and a note says it needs that runtime. Runtimes with neither are still
installed machine-wide (the preference covers COM components only), and one with an HKLM registry
step still gets the prompt for it. The game's record lists the runtimes made private
(`private_runtimes`); Repair and Undo make copies of those again, and of no other.

Either way, every runtime in the game's dependency closure that has a folder of its own (anything but an
installer that runs) also gets its DLL and OCX files copied beside the game's launch exe, keeping their layout,
whether it was installed now or already there: an old game that loads a runtime DLL by name (dx8vb.dll) finds it
there however it is started. They are copied from the runtime's private copy in staging, or from its shared
folder, only when marker_present trusts it, and each copy must hash to what was installed: the marker's file
list, this account's runtime record, or, for another account's sealed folder, the file as it is there (only an
administrator can have put it there). A file the game already has at that name is kept. The copies are recorded
as the runtime's (runtime_owner), so uninstall removes them where unchanged and extras never claim them, and the
record lists the runtimes copied (`runtime_copies`) for Repair and Undo; Update works them out again. Where a
copy can't be made, a note says so and the game is installed all the same.
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
from .steps import StepContext, _existing_file, run_file_steps, run_system_steps, undo_steps

log = logging.getLogger(__name__)

_CLASSES = "SOFTWARE\\Classes\\"
# Left in a runtime's folder once it is installed. In a folder the player filled (no elevated step):
# {"id", "source_sha256", "files": {path: sha256}}. In a folder an elevated chain sealed and filled: one line,
# the download's sha256 in hex, written by that chain (see write_marker_args).
MARKER_NAME = ".gamechooser-runtime.json"
_HEX64 = re.compile(r"[0-9a-f]{64}")

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


def _verified(path: Path, sha256: str) -> str:
    # The copy just made must hash to the download's checksum, or it is deleted again (nobody but
    # administrators can read the folder yet) and the chain stops. certutil on Windows 10 and later prints the
    # hex on a line of its own, in lowercase (/i covers case); Windows 7's spaced form isn't matched.
    # `&` before `exit 1`, not `&&`: the chain stops even if the delete fails.
    return (f'((certutil -hashfile "{path}" SHA256 | findstr /i /x /l "{sha256}" >nul) || '
            f'(del /f /q "{path}" & exit 1))')


def _write_marker_command(marker: Path, source_sha256: str) -> str:
    # In parentheses, so cmd reads `echo <hex>` and then the redirect: with `echo <hex>> file` a trailing digit
    # would be read as a stream number, and the marker would get a trailing space.
    if not _HEX64.fullmatch(source_sha256):
        raise ValueError(f"Not a sha256 hex digest: {source_sha256!r}")
    return f'(echo {source_sha256}) > "{marker}"'


def register_and_mark_args(dll: Path, marker: Path, source_sha256: str) -> str:
    """The arguments of the elevated `cmd.exe` that runs a runtime's last register step: regsvr32, and then,
    only if it succeeded, the marker written with the download's checksum (`source_sha256`, computed by the
    engine: the command reads nothing the player supplied). A marker that can't be written doesn't fail the
    command (the runtime is registered; the next install adopts the folder)."""
    return f'/d /v:off /s /c "regsvr32.exe /s "{dll}" && ({_write_marker_command(marker, source_sha256)} || type nul)"'


def write_marker_args(marker: Path, source_sha256: str) -> str:
    """The arguments of the elevated `cmd.exe` that only writes the marker: for a runtime whose last step
    isn't a register step, once every step has succeeded."""
    return f'/d /v:off /s /c "{_write_marker_command(marker, source_sha256)}"'


def lock_down_commands(folder: Path, user_sid: str, staging: Path, files: dict[str, str],
                       existing: set[str] | None = None) -> list[tuple[str, str]]:
    """The elevated commands that make and fill a runtime's folder and lock it, and the two folders above
    it, down: [(exe, arguments)], one per administrator prompt (usually one; more only if the command
    line would be too long). `files` maps each of the download's files, relative to `folder` and to
    `staging` (where the player unpacked them) with forward slashes, to its sha256. `existing` is None
    when `folder` isn't there yet; else the expected entries it already holds (an Administrators-owned
    folder being adopted). `user_sid` is checked by the caller and appears in no command: the installing
    user gets no grant.

    The chain stops at the first command that fails, and never changes the owner or permissions of a
    FILE (a hard link shares them with its other names): icacls runs on folders only, one explicit path
    at a time with /L, never /T or a wildcard. In order:
    - `<GameChooser>`, then `<GameChooser>/runtimes`: sealed (owner Administrators, inheritance off,
      Administrators and SYSTEM full control, Users read and execute), then checked to be a real folder.
      `runtimes` also lets Users create a folder in it (AD, on that folder only; no delete-child) and
      gives the creator full control of the folder they made (CREATOR OWNER, inherited only), for
      runtimes that need no elevated step and are unpacked by the player.
    - `folder`: made with mkdir, which fails if anything appeared there meanwhile, then sealed to
      Administrators and SYSTEM alone: while it is filled, nobody else can read it, let alone write to
      it (the marker arrives with the last register step, see register_and_mark_args). A folder being
      adopted is sealed the same way (Users lose the read access an earlier install gave them until the
      end), checked to be real, and must hold no marker, which would say "installed" before anything is
      registered.
    - Each expected sub-folder the same way, parent first; one already there is sealed and checked.
    - Each expected file not already there is copied in from `staging` by explicit path, so the
      elevated process creates it and it inherits only from its sealed folder, and then checked against
      its sha256 (see _verified): the elevated process reads the player's staging, where a redirect to a
      file the player can't read may have been planted while the prompt waited, so what it copied is
      deleted again unless it is the download's file. Into a folder that was already there, the copy
      first checks that nothing (no link, nothing at all) sits at that name.
    - Last of all, once every check has passed: Users get read and execute on `folder` and on each
      expected sub-folder (each sealed with inheritance off, so each by name; files inherit it). Until
      then, and for good if the chain stops, nothing the chain copied can be read by anyone but an
      administrator.
    """
    runtimes, game_chooser = folder.parent, folder.parent.parent
    admins = f"*{ADMINISTRATORS}:(OI)(CI)F *{SYSTEM}:(OI)(CI)F"
    base = f"{admins} *{USERS}:(OI)(CI)RX"
    owner = f"/setowner *{ADMINISTRATORS}"
    present = None if existing is None else {norm_key(rel) for rel in existing}

    def seal(path: Path, grants: str = base, extra: str = "") -> list[str]:
        return [_icacls(path, owner), _icacls(path, f"/inheritance:r /grant:r {grants}{extra}")]

    def there(rel: str) -> bool:
        return present is not None and norm_key(rel) in present

    commands = [*seal(game_chooser), _real(game_chooser), *seal(runtimes, extra=f" *{CREATOR_OWNER}:(OI)(CI)(IO)F"),
                _icacls(runtimes, f"/grant *{USERS}:(AD)"), _real(runtimes)]
    if present is None:
        commands += [f'mkdir "{folder}"', *seal(folder, admins)]
    else:
        commands += [*seal(folder, admins), _real(folder), _absent(folder / MARKER_NAME)]
    lead = len(commands)  # always in the first invocation: nothing inside is touched before this is done
    opened = [folder]
    for rel, is_folder in _depth_first_order(list(files)):
        parts = PurePosixPath(rel).parts
        path = folder.joinpath(*parts)
        if is_folder:
            commands += [*seal(path, admins), _real(path)] if there(rel) else [f'mkdir "{path}"', *seal(path, admins)]
            opened.append(path)
        elif not there(rel):
            if present is not None and (len(parts) == 1 or there("/".join(parts[:-1]))):
                commands += [_real(path), _absent(path)]  # the folder was there: so could something at this name
            commands += [f'copy /b "{staging.joinpath(*parts)}" "{path}" >nul', _verified(path, files[rel])]
    commands += [_icacls(path, f"/grant *{USERS}:(OI)(CI)RX") for path in opened]

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


def _read_marker(folder: Path) -> dict | str | None:
    """The marker's contents: the file list (a dict) a player-filled folder's marker holds, the download's
    checksum (a str) an elevated chain wrote, or None for no marker or one in neither shape."""
    try:
        text = (folder / MARKER_NAME).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    line = text.strip().lower()
    if _HEX64.fullmatch(line):
        return line
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return None
    return data


def marker_present(folder: Path, runtime_id: str, security, allow_user: bool = False) -> bool:
    """True if `folder` has Game Chooser's marker for `runtime_id`, nothing on the way to it is a link, and
    it is owned by Administrators or SYSTEM (or, with `allow_user`, for a runtime with no elevated step, by
    the current user).

    A marker in a sealed folder (one line: the download's checksum) counts only under Administrators or
    SYSTEM: nobody else can have written it there, which is the evidence. A player-filled folder's marker
    (the file list) counts only while every file it lists is still there."""
    folder = Path(folder)
    if linked_component(folder.parent, folder) is not None or not _owner_is(security, folder, allow_user):
        return False
    data = _read_marker(folder)
    if isinstance(data, str):
        return _owner_is(security, folder, allow_user=False)
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


def unpack(ctx, fetched, dest: Path, progress, cancel) -> Path:
    """Unpack a downloaded catalog item (a game, an extra or a runtime) into `dest`; returns its content root."""
    _check(cancel)
    progress(Progress("extracting"))
    try:
        root = ctx.extractor.extract(fetched.path, dest, fetched.source.get("install") or {}, cancel)
    except ExtractError as e:
        raise InstallFailed(str(e)) from e
    _check(cancel)
    return root


def run_file_steps_or_fail(steps: list[dict], tree: Path) -> None:
    """Run an item's file steps in `tree`; a step that fails stops the install with its own words."""
    try:
        run_file_steps(steps, tree)
    except StepError as e:
        raise InstallFailed(str(e)) from e


def install_shared(ctx, runtimes: list[dict], work: Path, fetch, progress, cancel) -> list[str]:
    """Install each runtime machine-wide, in order. `fetch(item, work_dir)` downloads the catalog's file
    (raising InstallFailed when none verifies); `work` is an empty folder for the downloads. Returns notes
    for the player: the downloads' (the developer changed a file) and the installers' (a restart)."""
    notes = []
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
                notes += _run_installer(ctx, runtime, fetched, target, progress, cancel)
            else:
                _unpack(ctx, runtime, fetched, target, work_dir / "unpacked", progress, cancel)
        finally:
            _clear(work_dir)
        if fetched.note:
            notes.append(fetched.note)
    return notes


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


def _run_installer(ctx, runtime: dict, fetched, target: Path, progress, cancel) -> list[str]:
    """Run the runtime's installer untracked; where it has a `detect` hint, it must find the runtime after.
    Returns the installer's notes (a restart it asked for).

    The record (untracked, no files) only stops the installer being run again for the next game.
    """
    title = _title(runtime)
    installer = dict((fetched.source.get("install") or {}).get("installer") or {})
    # A failed or cancelled run removes only the files it added to `target`.
    notes = run_installer_untracked(fetched.path, installer, target, ctx, progress, cancel, title=title)
    if runtime.get("detect") and not runtime_present(runtime, ctx.records, ctx.registry):
        raise InstallFailed(messages.FAILED.format(title=title))
    # No marker: the installer put the runtime wherever it likes, so the record or the detect hint says.
    try:
        ctx.records.save(InstallRecord(
            catalog_id=runtime["id"], kind="runtime", library_id="", folder=str(target), launch_path="",
            source=_source(runtime, fetched), extras={}, files={}, steps=[], method="run", tracked=False))
    except OSError as e:
        raise InstallFailed(messages.FAILED.format(title=title)) from e
    return notes


def _unpack(ctx, runtime: dict, fetched, target: Path, unpack_dir: Path, progress, cancel) -> None:
    """Unpack into `unpack_dir` and run the file steps there; get the result into `target`, run the system
    steps there, and leave the record and the marker.

    A runtime with no elevated step is copied into `target` by the player. One with an elevated step
    never is: one elevated command makes `target` (or seals it again), seals it, copies the files in,
    checking each against the download, and only then lets Users read it (lock_down_commands); the
    folder is then walked and hashed again before any step runs, so a swap in the player's staging is
    caught twice over and nothing is registered.

    `target`, or a folder on the way to it, that is a link is refused. A `target` that is already there
    (another account's install, or one whose record was lost) is never removed or changed: it is adopted
    only if no file in it differs from the download and it is owned by Administrators or SYSTEM (or, for
    a runtime with no elevated step, the current user); for a runtime with an elevated step it may hold
    nothing else and no marker. Only its missing files are added. Otherwise the install is refused. On
    any failure, the system steps already applied are undone (but in a folder it found, never a
    registration, which may predate this run), unless the marker is already there: then the runtime is
    installed for every account, whatever failed afterwards, and nothing is undone (R45). A folder the
    player filled is removed if this run made it, else just the files it added; a sealed folder stays as
    it is (without a marker it counts as absent, and a retry adopts it).
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
        root = unpack(ctx, fetched, unpack_dir, progress, cancel)
        run_file_steps(steps, root)
        _check(cancel)
        incoming = hash_tree(root)
        if found and not _adoptable(ctx, target, incoming, elevated):
            raise InstallFailed(messages.RUNTIME_FOLDER_NOT_OURS.format(title=title, path=target))
        if elevated:
            _lock_down(ctx, target, title, root, incoming, found)
            made = not found
            # Checked again now that no one else can change it: a junction anywhere on the way to it or
            # inside it, an entry the download doesn't have, a hard link, an entry an administrator didn't
            # make, or a file swapped in staging (defence in depth: the chain checked each copy itself).
            if (linked_component(ctx.runtimes_root, target) is not None
                    or not _owner_is(ctx.security, target, allow_user=False)
                    or not _only_expected(target, incoming) or not _owned_by_administrators(ctx, target, incoming)
                    or not _holds_download(target, incoming)):
                raise InstallFailed(messages.RUNTIME_FOLDER_CHANGED.format(title=title, path=target))
        else:
            if not found:
                target.mkdir()  # FileExistsError if something made it meanwhile: then it isn't ours
                made = True
            _copy_into(root, target, added)
        if elevated:
            # The marker can only get into the sealed folder elevated: it rides on the last register step.
            _run_steps_marking(ctx, steps, step_ctx, target, fetched.sha256, applied)
        else:
            for step in steps:  # one at a time, so a failure leaves `applied` true to what was done
                applied += run_system_steps([step], step_ctx)
        files = {key: RecordedFile(display, sha, runtime["id"]) for key, (display, sha) in incoming.items()}
        record = InstallRecord(
            catalog_id=runtime["id"], kind="runtime", library_id="", folder=str(target), launch_path="",
            source=_source(runtime, fetched), extras={}, files=files, steps=applied,
            method=install.get("method", ""))
        if elevated and (target / MARKER_NAME).is_file():
            # Installed for every account now, whatever happens to this account's record (R45).
            try:
                ctx.records.save(record)
            except OSError:
                log.warning("The runtime record for %s couldn't be saved; its marker in %s says it is installed",
                            runtime["id"], target, exc_info=True)
            return
        ctx.records.save(record)
    except BaseException as e:
        if elevated and (target / MARKER_NAME).is_file():
            undo = []  # registered and marked: installed for every account, so nothing is undone (R45)
        else:
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
        if isinstance(e, Exception) and not isinstance(e, (InstallFailed, Cancelled, OSError)):
            raise InstallFailed(messages.FAILED.format(title=title)) from e
        raise  # an OSError gets its plain words (disk full, locked file) at the engine's boundary
    if not elevated:
        _write_marker(target, _marker(runtime["id"], fetched.sha256, incoming))
    elif not (target / MARKER_NAME).is_file():
        log.warning("The runtime marker wasn't written into %s; the next install will adopt the folder", target)


def _marker(runtime_id: str, source_sha256: str, incoming: dict[str, tuple[str, str]]) -> dict:
    return {"id": runtime_id, "source_sha256": source_sha256, "files": {d: s for d, s in incoming.values()}}


def _run_steps_marking(ctx, steps: list[dict], step_ctx: StepContext, target: Path, source_sha256: str,
                       applied: list[dict]) -> None:
    """Run the system steps one at a time (so `applied`, which is the caller's, is true to what was done
    when one fails). The last step, when it is a register step, also writes the marker once regsvr32 has
    succeeded; otherwise one more elevated command writes it after every step has succeeded. A marker
    that can't be written doesn't fail the install: the next install adopts the folder."""
    for i, step in enumerate(steps):
        if i == len(steps) - 1 and step.get("action") == "register":
            applied.append(_register_and_mark(ctx, step, step_ctx, target, source_sha256))
        else:
            applied += run_system_steps([step], step_ctx)
    if not steps or steps[-1].get("action") != "register":
        try:
            code = ctx.elevator.run("cmd.exe", write_marker_args(target / MARKER_NAME, source_sha256), True,
                                    show=False)
        except (ElevationDeclined, StepError):
            code = -1
        if code != 0:
            log.warning("The runtime marker couldn't be written into %s", target)


def _register_and_mark(ctx, step: dict, step_ctx: StepContext, target: Path, source_sha256: str) -> dict:
    """A register step as steps.run_system_steps runs it (same checks, same errors), through
    register_and_mark_args so the marker arrives with a successful registration."""
    path = _existing_file(step_ctx, step.get("path"))
    try:
        code = ctx.elevator.run("cmd.exe", register_and_mark_args(path, target / MARKER_NAME, source_sha256), True,
                                show=False)
    except ElevationDeclined as e:
        raise StepError(messages.ELEVATION_DECLINED.format(what=path.name)) from e
    if code != 0:
        raise StepError(messages.STEP_REGISTER_FAILED.format(name=path.name))
    return {**step}


def _owned_by_administrators(ctx, folder: Path, incoming: dict[str, tuple[str, str]]) -> bool:
    """True if every expected entry that is in `folder` is owned by Administrators or SYSTEM."""
    return all(_owner_is(ctx.security, folder / rel, allow_user=False)
               for rel, _ in _depth_first_order([display for display, _ in incoming.values()])
               if os.path.lexists(folder / rel))


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
        if not _owned_by_administrators(ctx, target, incoming):
            return False
        return _only_expected(target, incoming) and not os.path.lexists(target / MARKER_NAME)
    return True


def _lock_down(ctx, target: Path, title: str, staging: Path, incoming: dict[str, tuple[str, str]],
               found: bool) -> None:
    """Run lock_down_commands elevated (the runtime's folder, made or adopted, and the two above it),
    passed inline: never through a script file a player could swap. A declined prompt is a declined
    install (R6)."""
    user = ctx.security.current_user()
    files = {display: sha for display, sha in incoming.values()}
    if (not user or not _SID.fullmatch(user) or "%" in str(target) or "%" in str(staging)
            or any("%" in f for f in files) or not all(_HEX64.fullmatch(sha) for sha in files.values())):
        raise InstallFailed(messages.RUNTIME_FOLDER_NOT_SECURED.format(title=title, path=target))  # cmd expands %
    existing = {rel for rel, _ in _depth_first_order(list(files)) if os.path.lexists(target / rel)} if found else None
    for exe, args in lock_down_commands(target, user, staging, files, existing):
        try:
            code = ctx.elevator.run(exe, args, True, show=False)
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
    hashes: dict[str, tuple[str, str]] = field(default_factory=dict)  # hash_tree(root), taken once it was unpacked
    note: str | None = None  # the download's note for the player (the developer changed the file)


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
        except OSError:
            _remove_copy(copy, tree)
            raise  # gets its plain words (disk full, locked file) at the engine's boundary
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
            if _runs_installer(fetched.source) or (fetched.source.get("install") or {}).get("method") == "url":
                copies.append(PrivateCopy(runtime, [], [], [], usable=False))
                continue
            root = unpack(ctx, fetched, unpacked, progress, cancel)
            steps = list(fetched.source.get("steps") or [])
            run_file_steps_or_fail(steps, root)
            hashes = hash_tree(root)
        except ValueError as e:
            raise InstallFailed(messages.FAILED.format(title=_title(runtime))) from e
        finally:
            _clear(work_dir)
        _check(cancel)
        copies.append(PrivateCopy(runtime, [], [], [s.get("path") for s in steps if s.get("action") == "register"],
                                  root=root, hashes=hashes, note=fetched.note))
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
        if is_runtime_owner(recorded.owner):
            _drop_recorded(rec, key, game_dir)


def _drop_recorded(rec: InstallRecord, key: str, game_dir: Path) -> None:
    """Remove a recorded file where it is unchanged (a changed one is the player's now) and stop recording it."""
    recorded = rec.files[key]
    try:
        path, linked = _resolve_game_path(game_dir, recorded.path)
        if not linked and path.is_file() and hash_file(path) == recorded.sha256:
            path.unlink()
            _prune_empty_dirs(path.parent, game_dir)
    except UnsafePath:
        pass
    except OSError:
        return  # still there (in use?): it stays recorded
    rec.files.pop(key)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# ---- copies beside the game's exe ----------------------------------------------------------------------------

# What a game can load by name from beside its exe. A runtime's other files (an exe, a readme, its marker) aren't
# copied: an exe there could be taken for the game's own program, and nothing else is looked up there by name.
LOADABLE_SUFFIXES = frozenset({".dll", ".ocx"})


def _loadable(rel: str) -> bool:
    return PurePosixPath(rel.replace("\\", "/")).suffix.lower() in LOADABLE_SUFFIXES


@dataclass
class CopySource:
    """Where one runtime's loadable files are copied from for a game, in this job."""

    runtime: dict
    folder: Path | None = None  # None: there is nothing to copy them from (`note` says why, if it matters)
    files: dict[str, str] = field(default_factory=dict)  # path in `folder`, forward slashes -> expected sha256
    note: str | None = None


def copy_sources(ctx, entry: dict, copies: list[PrivateCopy], title: str,
                 ids: list[str] | None = None) -> list[CopySource]:
    """For each runtime the game depends on (`ids`, the ones a rebuilt version had, else every runtime in its
    dependency closure) that has a folder of its own, where its DLL and OCX files are copied from: its private
    copy's unpacked files in staging, else its shared folder. A runtime that is an installer that runs has no
    folder and gets nothing."""
    if ids is None:
        try:
            wanted = dependency_order(entry, ctx.entries)
        except InstallFailed:
            return []  # an install or update refused this already; a rebuild just copies nothing
    else:
        wanted = []
        for runtime_id in ids:
            runtime = ctx.entries.get(runtime_id)
            if runtime is None or runtime.get("kind") != "runtime":
                log.info("Runtime %s isn't in the catalog any more; its files aren't copied for %s", runtime_id, title)
                continue
            wanted.append(runtime)
    private = {c.runtime["id"]: c for c in copies if c.usable and c.root is not None}
    sources = []
    for runtime in wanted:
        if _is_installer_runtime(runtime, ctx.platform):
            continue
        copy = private.get(runtime["id"])
        if copy is not None:
            sources.append(CopySource(runtime, copy.root, {d: s for d, s in copy.hashes.values() if _loadable(d)}))
        else:
            sources.append(_shared_source(ctx, runtime, title))
    return sources


def _shared_source(ctx, runtime: dict, title: str) -> CopySource:
    """A runtime's shared folder, trusted the way marker_present decides, with the hash each file must have:
    the marker's file list (a folder the player filled); for a sealed folder, whose marker holds only the
    download's checksum, this account's runtime record, else each loadable file in it, which only an
    administrator can have put there (see _sealed_files)."""
    folder = Path(ctx.runtimes_root) / runtime["id"]
    untrusted = CopySource(runtime, note=messages.RUNTIME_FILES_NOT_COPIED.format(title=title,
                                                                                  component=_title(runtime)))
    try:
        if not os.path.lexists(folder):
            return CopySource(runtime)  # present some other way (its detect hint): nothing of ours to copy
        record = ctx.records.load(runtime["id"])
        if record is not None and record.kind == "runtime" and record.method == "run":
            return CopySource(runtime)  # an installer ran: its files are wherever it put them
        if not marker_present(folder, runtime["id"], ctx.security, allow_user=not _has_elevated_steps(runtime)):
            return untrusted
        data = _read_marker(folder)
        if isinstance(data, dict):
            files = data["files"]
        elif (record is not None and record.kind == "runtime"
              and os.path.normcase(os.path.abspath(record.folder)) == os.path.normcase(os.path.abspath(folder))):
            files = {f.path: f.sha256 for f in record.files.values()}
        else:
            files = _sealed_files(ctx, folder)
            if files is None:
                return untrusted
    except (OSError, ValueError):
        return untrusted
    return CopySource(runtime, folder, {d: s.lower() for d, s in files.items()
                                        if isinstance(d, str) and isinstance(s, str) and _loadable(d)})


def _sealed_files(ctx, folder: Path) -> dict[str, str] | None:
    """Each loadable file in a sealed runtime folder (one marker_present trusts) and its sha256, for another
    account's install, which left no record here. Every one must be a plain file with one name, owned by
    Administrators or SYSTEM: the elevated chain that sealed the folder copied it in and checked it against the
    download. None if anything in the folder is a link, or a loadable file isn't so."""
    files = {}
    pending = [folder]
    while pending:
        current = pending.pop()
        for item in os.scandir(current):
            path = Path(item.path)
            info = os.lstat(path)
            if is_link(path) or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
                return None
            if item.is_dir(follow_symlinks=False):
                pending.append(path)
                continue
            rel = path.relative_to(folder).as_posix()
            if not _loadable(rel):
                continue
            if (not item.is_file(follow_symlinks=False) or info.st_nlink > 1
                    or not _owner_is(ctx.security, path, allow_user=False)):
                return None
            files[rel] = hash_file(path)
    return files


def _copy_new(src: Path, dest: Path) -> None:
    """Copy `src` to `dest`, which mustn't exist: nothing there is ever replaced (FileExistsError)."""
    with open(src, "rb") as source, open(dest, "xb") as copy:
        shutil.copyfileobj(source, copy)


def _make_folders(folder: Path, made: list[Path]) -> None:
    missing = []
    while not os.path.lexists(folder):
        missing.append(folder)
        folder = folder.parent
    for path in reversed(missing):
        path.mkdir()
        made.append(path)


def _link_on_the_way(path: Path, root: Path) -> bool:
    """True if a folder between `root` and `path` is a link (checked lexically, as the planner does)."""
    current = Path(path).parent
    while current != root and root in current.parents:
        if is_link(current):
            return True
        current = current.parent
    return False


def place_copies(sources: list[CopySource], exe_dir: Path, root: Path, title: str,
                 ours=lambda path: None) -> tuple[dict[Path, tuple[str, str]], dict[str, str]]:
    """Copy each source's files into `exe_dir` (inside `root`), keeping their layout, and check each copy's
    sha256. A file already at that name is kept (the game's own), unless `ours(path)` names the runtime it
    belongs to (a private copy, or an earlier copy): then it counts as copied. Nothing is followed through a
    link, and nothing raises: a file that can't be copied or checked is a note for its runtime.

    Returns {copy: (runtime id, sha256)} and {runtime id: note}."""
    placed: dict[Path, tuple[str, str]] = {}
    notes = {s.runtime["id"]: s.note for s in sources if s.note}
    for source in sources:
        if source.folder is None:
            continue
        runtime_id = source.runtime["id"]
        note = messages.RUNTIME_FILES_NOT_COPIED.format(title=title, component=_title(source.runtime))
        for rel, sha in sorted(source.files.items()):
            try:
                src, src_linked = _resolve_game_path(source.folder, rel)
                dest, dest_linked = _resolve_game_path(exe_dir, rel)
                if src_linked or dest_linked or _link_on_the_way(dest, root):
                    raise UnsafePath(rel)
            except (UnsafePath, TypeError, ValueError, OSError):
                notes[runtime_id] = note
                continue
            if dest in placed:
                continue  # an earlier runtime's file of that name
            if os.path.lexists(dest):
                owner = ours(dest)
                if owner is not None:
                    try:
                        placed[dest] = (owner, hash_file(dest))
                    except OSError:
                        pass
                continue  # the game's own file is kept
            made: list[Path] = []
            try:
                _make_folders(dest.parent, made)
                _copy_new(src, dest)
                if hash_file(dest) != sha:
                    _unlink_quietly(dest)
                    raise OSError(f"{src} isn't the file that was installed")
            except FileExistsError:
                continue  # appeared meanwhile: it is the game's
            except OSError:
                for folder in reversed(made):
                    _prune_empty_dirs(folder, exe_dir)
                notes[runtime_id] = note
                continue
            placed[dest] = (runtime_id, sha)
    return placed, notes


def copies_in_tree(ctx, entry: dict, tree: Path, exe: Path, copies: list[PrivateCopy], title: str,
                   ids: list[str] | None = None, installed: tuple[InstallRecord, Path] | None = None
                   ) -> tuple[dict[str, str], list[str], list[str]]:
    """Once a staged game's exe is known (and its private copies are beside it): copy its runtimes' DLL and OCX
    files beside the exe in `tree`. A private copy's file already there counts as the copy.

    `installed` (the record and folder of the version being replaced) keeps, for a runtime there is nothing to
    copy from this time (no shared folder, or one that can't be trusted), the copies that version recorded,
    where they are unchanged: a Repair or Update never removes a working copy because its source is gone.

    Returns the owners to record (norm_key in `tree` -> runtime_owner(id)), the notes, and the runtime ids (for
    the record's `runtime_copies`)."""
    sources = copy_sources(ctx, entry, copies, title, ids)
    private = {os.path.normcase(str(p)): c.runtime["id"] for c in copies for p in c.files}
    placed, notes = place_copies(sources, exe.parent, tree, title, lambda p: private.get(os.path.normcase(str(p))))
    if installed is not None:
        rec, game_dir = installed
        for source in sources:
            runtime_id = source.runtime["id"]
            if source.folder is None or runtime_id in notes:
                if _keep_recorded(rec, game_dir, tree, runtime_id, placed) and source.folder is None:
                    notes.pop(runtime_id, None)
    owners = {norm_key(dest.relative_to(tree).as_posix()): runtime_owner(runtime_id)
              for dest, (runtime_id, _) in placed.items()}
    return owners, list(notes.values()), [s.runtime["id"] for s in sources]


def _keep_recorded(rec: InstallRecord, game_dir: Path, tree: Path, runtime_id: str,
                   placed: dict[Path, tuple[str, str]]) -> bool:
    """Copy into `tree` the runtime's recorded copies that `game_dir` still holds unchanged, where `tree` has
    nothing at that name. True if any was kept."""
    kept = False
    for recorded in rec.files.values():
        if recorded.owner != runtime_owner(runtime_id):
            continue
        try:
            path, linked = _resolve_game_path(game_dir, recorded.path)
            dest, dest_linked = _resolve_game_path(tree, recorded.path)
            if (linked or dest_linked or os.path.lexists(dest) or not path.is_file()
                    or hash_file(path) != recorded.sha256):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            _copy_new(path, dest)
            if hash_file(dest) != recorded.sha256:
                _unlink_quietly(dest)
                continue
        except (UnsafePath, TypeError, ValueError, OSError):
            continue
        placed[dest] = (runtime_id, recorded.sha256)
        kept = True
    return kept


def copies_after_installer(ctx, entry: dict, rec: InstallRecord, game_dir: Path, copies: list[PrivateCopy],
                           title: str, ids: list[str] | None = None) -> list[str]:
    """For a tracked game whose installer has run: copy its runtimes' DLL and OCX files beside its launch exe in
    `game_dir`, record them with their runtime as owner, and save the record. Returns the notes.

    A copy already recorded and unchanged stays; a recorded copy no runtime copies any more is removed where
    unchanged (a changed one is the player's now), except a private copy's, and a runtime's that has nothing
    to copy from this time. With no launch exe recorded, nothing is copied and the notes say so."""
    sources = copy_sources(ctx, entry, copies, title, ids)
    rec.runtime_copies = [s.runtime["id"] for s in sources]
    if not rec.launch_path:
        ctx.records.save(rec)
        return [messages.RUNTIME_FILES_NO_PROGRAM.format(title=title, component=_title(s.runtime))
                for s in sources if s.files or s.note]
    recorded = {key: f for key, f in rec.files.items() if is_runtime_owner(f.owner)}

    def ours(path: Path) -> str | None:
        f = recorded.get(norm_key(path.relative_to(game_dir).as_posix()))
        try:
            return f.owner[len(RUNTIME_OWNER_PREFIX):] if f is not None and hash_file(path) == f.sha256 else None
        except OSError:
            return None

    placed, notes = place_copies(sources, (game_dir / rec.launch_path).parent, game_dir, title, ours)
    keep = {norm_key(dest.relative_to(game_dir).as_posix()) for dest in placed}
    held = {s.runtime["id"] for s in sources if s.folder is None or s.runtime["id"] in notes}
    held |= set(rec.private_runtimes or [])
    for key, f in recorded.items():
        if key not in keep and f.owner[len(RUNTIME_OWNER_PREFIX):] not in held:
            _drop_recorded(rec, key, game_dir)
    for dest, (runtime_id, sha) in placed.items():
        rel = dest.relative_to(game_dir).as_posix()
        rec.files[norm_key(rel)] = RecordedFile(rel, sha, runtime_owner(runtime_id))
    ctx.records.save(rec)
    return list(notes.values())


def copies_skipped_notes(ctx, entry: dict, copies: list[PrivateCopy], title: str, text: str, **values) -> list[str]:
    """The notes for a game whose folder gets no copies (untracked, or needing administrator permission): one
    for each runtime that has files to copy, except those its private copies already have a note for."""
    private = {c.runtime["id"] for c in copies}
    return [text.format(title=title, component=_title(s.runtime), **values)
            for s in copy_sources(ctx, entry, [], title) if (s.files or s.note) and s.runtime["id"] not in private]
