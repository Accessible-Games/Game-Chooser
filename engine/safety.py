"""The three-checksum update planner.

For each file it compares what was installed (I), what is on disk now (D) and
what the new version ships (N), and decides whether to keep, write, back up and
write, or delete. Player-owned files (not in the record, or changed since
install) are never deleted or overwritten without a backup first. Only
.exe/.dll/.ocx files are replaced without a copy.
"""

import fnmatch
import posixpath
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from .paths import EXECUTABLE_CODE_SUFFIXES, norm_key
from .records import RecordedFile


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
