"""Path helpers: safe joining, naming, and engine-owned directory names."""

import os
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath

from .errors import UnsafePath

STAGING_DIR_NAME = ".gamechooser-staging"
BACKUP_PREFIX = "Game Chooser backup "
EXECUTABLE_CODE_SUFFIXES = frozenset({".exe", ".dll", ".ocx"})

_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_title(title: str) -> str:
    name = _ILLEGAL.sub("", title)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if not name:
        return "Game"
    if name.upper() in _RESERVED:
        name += "_"
    return name


def safe_join(root: Path, rel: str) -> Path:
    """Join `rel` under `root`, raising UnsafePath if it could land anywhere else."""
    win = PureWindowsPath(rel)
    posix = PurePosixPath(rel)
    if (win.anchor or win.drive or win.root or posix.is_absolute()
            or rel.startswith(("/", "\\"))):
        raise UnsafePath(f"Path is not relative: {rel}")
    if ".." in re.split(r"[\\/]", rel):
        raise UnsafePath(f"Path climbs out of its folder: {rel}")
    joined = Path(root).joinpath(*[p for p in re.split(r"[\\/]", rel) if p])
    if not joined.resolve().is_relative_to(Path(root).resolve()):
        raise UnsafePath(f"Path escapes its folder: {rel}")
    return joined


def backup_dir_name(day: date) -> str:
    return f"{BACKUP_PREFIX}{day.isoformat()}"


def is_engine_dir(name: str) -> bool:
    return name == STAGING_DIR_NAME or name.startswith(BACKUP_PREFIX)


def is_link(path) -> bool:
    """True for symlinks and NTFS junctions. The engine never follows or removes these."""
    return os.path.islink(path) or os.path.isjunction(path)


def walk_files(root: Path, skip=lambda name: False) -> Iterator[Path]:
    """Every file under `root` (a linked file too), never entering a linked folder or a folder `skip` names."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not skip(d) and not is_link(Path(dirpath) / d)]
        yield from (Path(dirpath) / name for name in filenames)


def is_code(path) -> bool:
    return PurePosixPath(str(path).replace("\\", "/")).suffix.lower() in EXECUTABLE_CODE_SUFFIXES


def same_folder(a, b) -> bool:
    return os.path.normcase(os.path.abspath(a)).lower() == os.path.normcase(os.path.abspath(b)).lower()


def norm_key(rel: str) -> str:
    return rel.replace("\\", "/").lower()


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1
