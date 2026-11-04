"""The three-checksum update planner.

For each file it compares what was installed (I), what is on disk now (D) and
what the new version ships (N), and decides whether to keep, write, back up and
write, or delete. Player-owned files (not in the record, or changed since
install) are never deleted or overwritten without a backup first. Only
.exe/.dll/.ocx files are replaced without a copy.
"""

import fnmatch
import os
import posixpath
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Sequence

from . import messages
from .errors import EngineError
from .paths import (EXECUTABLE_CODE_SUFFIXES, STAGING_DIR_NAME, backup_dir_name, norm_key, safe_join,
                    unique_path)
from .records import RecordedFile
from .sources import hash_file


class Op(str, Enum):
    KEEP = "keep"
    WRITE = "write"
    BACKUP_AND_WRITE = "backup_and_write"
    DELETE = "delete"


@dataclass
class FileOp:
    key: str
    path: str
    op: Op
    reason: str


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
        elif fnmatch.fnmatchcase(key, pat):
            return True
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
            plan.ops.append(FileOp(key, path, op, reason))
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


def _prune_empty_dirs(start: Path, stop: Path) -> None:
    """Remove `start` and its empty ancestors, never `stop` itself or anything above it."""
    stop = Path(stop)
    current = Path(start)
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _preflight(plan: UpdatePlan, game_dir: Path, incoming_root: Path) -> None:
    """Check every op before any file is touched. Never resolves a conflict by removing a folder."""
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
    game_dir, incoming_root = Path(game_dir), Path(incoming_root)
    _preflight(plan, game_dir, incoming_root)
    backup_root = game_dir / backup_dir_name(today)
    result = ApplyResult()
    for op in plan.ops:
        if op.op is Op.KEEP:
            continue
        target = safe_join(game_dir, op.path)
        if op.op is Op.DELETE:
            target.unlink(missing_ok=True)
            _prune_empty_dirs(target.parent, game_dir)
            continue
        source = safe_join(incoming_root, op.path)
        if op.op is Op.WRITE or not target.exists():
            _write_file(source, target, None, game_dir)
            continue
        backup = unique_path(safe_join(backup_root, op.path))
        _write_file(source, target, backup, game_dir)
        result.backups.append({
            "original": op.path.replace("\\", "/"),
            "backup": _rel(backup, game_dir),
            "installed_sha256": plan.new_files[op.key].sha256,
        })
    return result


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


def plan_uninstall(record, game_dir: Path) -> UninstallPlan:
    game_dir = Path(game_dir)
    delete = []
    for recorded in record.files.values():
        path = safe_join(game_dir, recorded.path)
        if path.is_file() and hash_file(path) == recorded.sha256:
            delete.append(recorded.path.replace("\\", "/"))
    deleting = {norm_key(p) for p in delete}
    player = []
    for dirpath, dirnames, filenames in os.walk(game_dir):
        dirnames[:] = [d for d in dirnames if d != STAGING_DIR_NAME]
        for name in filenames:
            rel = _rel(Path(dirpath) / name, game_dir)
            if norm_key(rel) not in deleting:
                player.append(rel)
    return UninstallPlan(sorted(delete), sorted(player))


def count_player_files(record, game_dir: Path) -> int:
    return len(plan_uninstall(record, game_dir).player_files)


def _prune_all_empty(root: Path) -> None:
    for dirpath, _dirnames, _files in os.walk(root, topdown=False):
        if Path(dirpath) != root:
            try:
                Path(dirpath).rmdir()
            except OSError:
                pass


def apply_uninstall(plan: UninstallPlan, game_dir: Path, delete_player_data: bool,
                    recycle: Callable[[list[Path]], None]) -> int:
    game_dir = Path(game_dir)
    for rel in plan.delete:
        path = safe_join(game_dir, rel)
        path.unlink(missing_ok=True)
        _prune_empty_dirs(path.parent, game_dir)
    kept = len(plan.player_files)
    if delete_player_data:
        if plan.player_files:
            recycle([safe_join(game_dir, rel) for rel in plan.player_files])
        _prune_all_empty(game_dir)
        kept = 0
    try:
        game_dir.rmdir()
    except OSError:
        pass
    return kept
