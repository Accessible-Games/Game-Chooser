"""The three-checksum update planner.

For each file it compares what was installed (I), what is on disk now (D) and
what the new version ships (N), and decides whether to keep, write, back up and
write, or delete. Player-owned files (not in the record, or changed since
install) are never deleted or overwritten without a backup first. Only
.exe/.dll/.ocx files are replaced without a copy.
"""

import fnmatch
import os
import re
import posixpath
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Sequence

from . import messages
from .errors import EngineError, UnsafePath
from .paths import (EXECUTABLE_CODE_SUFFIXES, STAGING_DIR_NAME, backup_dir_name, norm_key, safe_join,
                    unique_path)
from .records import RecordedFile
from .sources import hash_file


class Op(str, Enum):
    KEEP = "keep"
    WRITE = "write"
    BACKUP_AND_WRITE = "backup_and_write"
    DELETE = "delete"


# Marker for "this file was not on disk when the plan was made".
ABSENT = "<absent>"


@dataclass
class FileOp:
    key: str
    path: str
    op: Op
    reason: str
    # What the file on disk looked like at planning time: its sha256, or ABSENT.
    # None means "not recorded"; apply_plan then skips its last-moment re-check.
    expected: str | None = None


@dataclass
class UpdatePlan:
    ops: list[FileOp] = field(default_factory=list)
    new_files: dict[str, RecordedFile] = field(default_factory=dict)


def matches_persist(path: str, persist: Sequence[str]) -> bool:
    key = norm_key(path)
    for pattern in persist:
        pat = norm_key(pattern)
        if pat.endswith("/"):
            if key.startswith(pat) or key + "/" == pat:
                return True
        elif fnmatch.fnmatchcase(key, pat) or key.startswith(pat + "/"):
            return True  # a pattern naming a folder, with or without the slash, protects everything under it
    return False


def _is_code(path: str) -> bool:
    return posixpath.splitext(path.replace("\\", "/"))[1].lower() in EXECUTABLE_CODE_SUFFIXES


def _rekey(items: dict, what: str) -> dict:
    out = {}
    for key, value in items.items():
        nk = norm_key(key)
        if nk in out:
            raise ValueError(f"Two {what} entries are the same file: {key!r}")
        out[nk] = value
    return out


def plan_update(
    installed: dict[str, RecordedFile],
    on_disk: dict[str, tuple[str, str]],
    incoming: dict[str, RecordedFile],
    persist: Sequence[str] = (),
    restore_changed_code: bool = False,
) -> UpdatePlan:
    installed = _rekey(installed, "installed")
    on_disk = _rekey(on_disk, "on-disk")
    incoming = _rekey(incoming, "incoming")
    plan = UpdatePlan()
    for key in sorted(set(installed) | set(on_disk) | set(incoming)):
        inst = installed.get(key)
        disk = on_disk.get(key)
        new = incoming.get(key)
        path = new.path if new else disk[0] if disk else inst.path
        op, reason = _decide(key, path, inst, disk, new, persist, restore_changed_code)
        if op is not None:
            plan.ops.append(FileOp(key, path, op, reason, disk[1] if disk else ABSENT))
        if new is not None and not matches_persist(key, persist):
            plan.new_files[key] = new
    return plan


def _decide(key, path, inst, disk, new, persist, restore_code):
    if matches_persist(key, persist):
        if disk is not None:
            return Op.KEEP, "persist path is player data"
        return (Op.WRITE, "persist default, nothing on disk") if new else (None, "")

    if disk is None:
        return (Op.WRITE, "missing on disk") if new else (None, "gone and not shipped")

    d = disk[1]
    if inst is not None and d == inst.sha256:
        if new is None:
            return Op.DELETE, "unchanged and removed by new version"
        if new.sha256 == d:
            return Op.KEEP, "unchanged and same"
        return Op.WRITE, "unchanged, new version differs"

    # Player-owned: changed since install, or never recorded.
    if new is None:
        return Op.KEEP, "player-owned and not shipped"
    if new.sha256 == d:
        return Op.KEEP, "already equals new version"
    if inst is not None and new.sha256 == inst.sha256 and not (restore_code and _is_code(path)):
        return Op.KEEP, "player changed, new version same as installed"
    if _is_code(path):
        return Op.WRITE, "code replaced without copy"
    return Op.BACKUP_AND_WRITE, "player-owned file replaced"


# ---- applying a plan -------------------------------------------------------


@dataclass
class ApplyResult:
    backups: list[dict] = field(default_factory=list)


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_link(path) -> bool:
    """True for symlinks and NTFS junctions. The engine never follows or removes these."""
    return os.path.islink(path) or os.path.isjunction(path)


def _resolve_game_path(game_dir: Path, rel: str) -> tuple[Path, bool]:
    """Path of `rel` under `game_dir` and whether it passes through a link.

    The link check is lexical and comes first: safe_join resolves links, so it would refuse
    (raise) for a file under a junction that points outside the folder.
    """
    parts = [p for p in re.split(r"[\/]", rel) if p]
    if ".." not in parts and not PureWindowsPath(rel).anchor and not rel.startswith(("/", "\\")):
        lexical = game_dir.joinpath(*parts)
        if _has_link_component(lexical, game_dir):
            return lexical, True
    return safe_join(game_dir, rel), False


def _first_link(path: Path, root: Path) -> Path | None:
    """The outermost link between `root` and `path` (or `path` itself), checked lexically."""
    parts = Path(path).relative_to(root).parts
    current = Path(root)
    for part in parts:
        current = current / part
        if is_link(current):
            return current
    return None


def _has_link_component(path: Path, root: Path) -> bool:
    """True if `path`, or any folder between `root` and it, is a link."""
    current = Path(path)
    while current != root and root in current.parents:
        if is_link(current):
            return True
        current = current.parent
    return False


def _current_state(path: Path) -> str:
    if is_link(path):
        return "<link>"
    if path.is_file():
        return hash_file(path)
    if path.exists():
        return "<other>"
    return ABSENT


def _prune_empty_dirs(start: Path, stop: Path) -> None:
    """Remove `start` and its empty ancestors, never `stop` itself, a link, or anything above."""
    stop = Path(stop)
    current = Path(start)
    while current != stop and stop in current.parents:
        if is_link(current):
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def preflight(plan: UpdatePlan, game_dir: Path, incoming_root: Path, today: date) -> None:
    """Check every op before any file is touched. Never resolves a conflict by removing a folder.

    Changes nothing; apply_plan runs it too. Callers run it on its own to refuse before other work.
    """
    for op in plan.ops:
        target = safe_join(game_dir, op.path)
        if op.op not in (Op.WRITE, Op.BACKUP_AND_WRITE):
            continue
        if target.is_dir():
            raise EngineError(messages.UPDATE_PATH_CONFLICT.format(path=op.path, kind="folder"))
        parent = target.parent
        while parent != game_dir and game_dir in parent.parents:
            if parent.exists() and not parent.is_dir():
                raise EngineError(messages.UPDATE_PATH_CONFLICT.format(path=_rel(parent, game_dir), kind="file"))
            parent = parent.parent
        backup_target = safe_join(game_dir / backup_dir_name(today), op.path)
        parent = backup_target.parent
        while parent != game_dir and game_dir in parent.parents:
            if parent.exists() and not parent.is_dir():
                raise EngineError(messages.UPDATE_PATH_CONFLICT.format(path=_rel(parent, game_dir), kind="file"))
            parent = parent.parent
        if not safe_join(incoming_root, op.path).is_file():
            raise FileNotFoundError(f"Incoming file is missing: {op.path}")


def _write_file(source: Path, target: Path, backup_to: Path | None, game_dir: Path) -> None:
    """Copy `source` to a temp sibling, optionally move the player's file to `backup_to`, then swap in."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".gc-", suffix=".tmp", dir=target.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    moved = False
    try:
        shutil.copyfile(source, tmp)
        if backup_to is not None:
            backup_to.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(backup_to))
            moved = True
        os.replace(tmp, target)
    except BaseException:
        if moved and not target.exists():
            shutil.move(str(backup_to), str(target))
            _prune_empty_dirs(backup_to.parent, game_dir)
        raise
    finally:
        tmp.unlink(missing_ok=True)


def apply_plan(plan: UpdatePlan, game_dir: Path, incoming_root: Path, today: date) -> ApplyResult:
    """Apply `plan`. If it fails partway, the exception carries `.partial`: the ApplyResult for the
    backups already made, so the caller can still record them."""
    game_dir, incoming_root = Path(game_dir), Path(incoming_root)
    result = ApplyResult()
    try:
        preflight(plan, game_dir, incoming_root, today)
        for op in plan.ops:
            _apply_op(op, plan, game_dir, incoming_root, today, result)
    except BaseException as exc:
        exc.partial = result
        raise
    return result


def _is_code_path(path: Path) -> bool:
    return path.suffix.lower() in EXECUTABLE_CODE_SUFFIXES


def _apply_op(op: FileOp, plan: UpdatePlan, game_dir: Path, incoming_root: Path, today: date,
              result: ApplyResult) -> None:
    if op.op is Op.KEEP:
        return
    target = safe_join(game_dir, op.path)
    # Re-check the disk just before acting: the game may have written something since planning.
    state = _current_state(target) if op.expected is not None else None
    if state in ("<link>", "<other>"):
        if op.op is Op.DELETE:
            return
        raise EngineError(messages.UPDATE_PATH_CONFLICT.format(path=op.path, kind="folder"))
    changed = state is not None and state != op.expected

    if op.op is Op.DELETE:
        if not changed:
            target.unlink(missing_ok=True)
            _prune_empty_dirs(target.parent, game_dir)
        return  # a changed file is the player's now

    source = safe_join(incoming_root, op.path)
    needs_backup = op.op is Op.BACKUP_AND_WRITE
    if op.op is Op.WRITE and changed and state != ABSENT:
        if op.key not in plan.new_files:
            return  # a persist default that appeared meanwhile: keep the player's copy
        needs_backup = not _is_code_path(target)
    if not needs_backup or not target.exists():
        _write_file(source, target, None, game_dir)
        return
    backup = unique_path(safe_join(game_dir / backup_dir_name(today), op.path))
    _write_file(source, target, backup, game_dir)
    result.backups.append({
        "original": op.path.replace("\\", "/"),
        "backup": _rel(backup, game_dir),
        "installed_sha256": plan.new_files[op.key].sha256,
    })


def restore_backups(game_dir: Path, backups: list[dict]) -> list[str]:
    """Put backed-up files back, but only where the player hasn't touched what the update wrote."""
    game_dir = Path(game_dir)
    restored = []
    for b in backups:
        original = safe_join(game_dir, b["original"])
        backup = safe_join(game_dir, b["backup"])
        if not backup.is_file() or original.is_dir():
            continue
        if original.exists() and hash_file(original) != b["installed_sha256"]:
            continue
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, original)
        except OSError:
            continue
        restored.append(b["original"])
        _prune_empty_dirs(backup.parent, game_dir)
    return restored


# ---- uninstall -------------------------------------------------------------


@dataclass
class UninstallPlan:
    delete: list[str] = field(default_factory=list)
    player_files: list[str] = field(default_factory=list)
    # sha256 of each `delete` file at planning time (keyed by its path in `delete`); re-checked before unlinking.
    hashes: dict[str, str] = field(default_factory=dict)


def plan_uninstall(record, game_dir: Path) -> UninstallPlan:
    game_dir = Path(game_dir)
    delete = []
    hashes = {}
    for recorded in record.files.values():
        path, linked = _resolve_game_path(game_dir, recorded.path)
        if linked:
            continue
        if path.is_file() and hash_file(path) == recorded.sha256:
            rel = recorded.path.replace("\\", "/")
            delete.append(rel)
            hashes[rel] = recorded.sha256
    deleting = {norm_key(p) for p in delete}
    player = []
    for dirpath, dirnames, filenames in os.walk(game_dir):
        kept_dirs = []
        for d in dirnames:
            if d == STAGING_DIR_NAME:
                continue
            if is_link(Path(dirpath) / d):
                player.append(_rel(Path(dirpath) / d, game_dir))  # listed, never descended into
            else:
                kept_dirs.append(d)
        dirnames[:] = kept_dirs
        for name in filenames:
            rel = _rel(Path(dirpath) / name, game_dir)
            if norm_key(rel) not in deleting:
                player.append(rel)
    return UninstallPlan(sorted(delete), sorted(player), hashes)


def count_player_files(record, game_dir: Path) -> int:
    return len(plan_uninstall(record, game_dir).player_files)


def _entry_path(game_dir: Path, rel: str) -> Path:
    """Path of a listed entry that may itself be a link: validate its parent, not where it points."""
    parent, _, name = rel.rpartition("/")
    base = safe_join(game_dir, parent) if parent else game_dir
    if not name or name in (".", "..") or "\\" in name:
        raise UnsafePath(f"Not a plain file name: {rel}")
    return base / name


def _prune_all_empty(root: Path) -> None:
    """Remove every empty folder under `root`, never entering or removing links."""
    for entry in list(os.scandir(root)):
        path = Path(entry.path)
        if entry.is_dir(follow_symlinks=False) and not is_link(path):
            _prune_all_empty(path)
            try:
                path.rmdir()
            except OSError:
                pass


def apply_uninstall(plan: UninstallPlan, game_dir: Path, delete_player_data: bool,
                    recycle: Callable[[list[Path]], None]) -> int:
    """Returns how many player files (a link counts as one) were left in the game folder."""
    game_dir = Path(game_dir)
    # Resolve every path first, so a refusal happens before the first deletion.
    delete_paths = [(rel, *_resolve_game_path(game_dir, rel)) for rel in plan.delete]
    player = [_entry_path(game_dir, rel) for rel in plan.player_files]
    for rel, path, linked in delete_paths:
        expected = plan.hashes.get(rel)
        if linked:
            pass  # never touched; its link entry is kept below
        elif expected is not None and path.exists() and (not path.is_file() or hash_file(path) != expected):
            player.append(path)  # changed since planning: now the player's
        else:
            path.unlink(missing_ok=True)
            _prune_empty_dirs(path.parent, game_dir)
    # A path that goes through a link is represented by the link entry itself, counted once.
    kept_entries = []
    for p in player + [path for _rel_, path, linked in delete_paths if linked]:
        entry = _first_link(p, game_dir) or p
        if entry not in kept_entries:
            kept_entries.append(entry)
    player = kept_entries
    kept = len(player)
    if delete_player_data:
        to_recycle = [p for p in player if not is_link(p) and not _has_link_component(p, game_dir)]
        if to_recycle:
            recycle(to_recycle)
        _prune_all_empty(game_dir)
        kept = len(player) - len(to_recycle)
    try:
        game_dir.rmdir()
    except OSError:
        pass
    return kept
