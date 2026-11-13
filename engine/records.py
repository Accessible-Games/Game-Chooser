"""Install records: what Game Chooser put on disk, kept as one JSON file per catalog id.

`InstallRecord.previous` stays a plain JSON dict (its sources are `asdict` output),
so it round-trips untouched; every other nested value is rebuilt into its dataclass.
"""

import json
import logging
import os
import typing
from dataclasses import MISSING, asdict, dataclass, fields
from pathlib import Path

from .paths import is_engine_dir, norm_key, same_folder
from .sources import hash_file

log = logging.getLogger(__name__)


@dataclass
class RecordedFile:
    path: str
    sha256: str
    owner: str  # the catalog id that shipped it (the game or an extra), or runtime_owner(id) for a runtime's copy


RUNTIME_OWNER_PREFIX = "runtime:"


def runtime_owner(runtime_id: str) -> str:
    """The owner recorded for a file a runtime put in a game's folder (a private copy, or a copy beside its exe),
    not the game's or its installer's."""
    return RUNTIME_OWNER_PREFIX + runtime_id


def is_runtime_owner(owner: str) -> bool:
    return owner.startswith(RUNTIME_OWNER_PREFIX)


@dataclass
class InstalledSource:
    url: str
    sha256: str
    size: int
    catalog_sha256: str | None
    catalog_size: int | None
    position: int
    version: str | None
    # How this source was unpacked and set up, as the catalog said when it was installed, so repair
    # and undo rebuild exactly that tree. None in records written before these were kept.
    install: dict | None = None
    steps: list[dict] | None = None
    launch: str | None = None  # the entry's launch path hint for this platform (game sources)


@dataclass
class InstallRecord:
    catalog_id: str
    kind: str
    library_id: str
    folder: str
    launch_path: str
    source: InstalledSource
    extras: dict[str, InstalledSource]
    files: dict[str, RecordedFile]
    steps: list[dict]
    method: str
    tracked: bool = True
    vendor_uninstaller: str | None = None
    previous: dict | None = None
    previous_unavailable: str | None = None
    uninstalled: bool = False
    format: int = 1
    # Runtime ids the install made private COM copies of (engine.runtimes); None in older records.
    private_runtimes: list[str] | None = None
    # True when launch_path is the catalog's launch path for the entry, False when the engine guessed it.
    launch_from_catalog: bool = False
    # Runtime ids whose DLL and OCX files the install copies beside the launch exe (engine.runtimes), whether or
    # not each copy could be made; Repair and Undo copy those again. None in older records.
    runtime_copies: list[str] | None = None


def record_folder(record: InstallRecord, libraries: list[dict]) -> Path | None:
    if not record.library_id:
        return Path(record.folder)
    for lib in libraries:
        if lib.get("id") == record.library_id:
            return Path(lib["path"]) / record.folder
    return None


def hash_tree(root: Path) -> dict[str, tuple[str, str]]:
    """Map norm_key -> (display path with forward slashes, sha256) for every file under root."""
    root = Path(root)
    result: dict[str, tuple[str, str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        # Never follow a symlink or junction out of the folder.
        dirnames[:] = [d for d in dirnames if not is_engine_dir(d)
                       and not (os.path.islink(Path(dirpath) / d) or os.path.isjunction(Path(dirpath) / d))]
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            result[norm_key(rel)] = (rel, hash_file(full))
    return result


def _build(cls, data):
    """Build dataclass `cls` from a dict: unknown keys dropped, absent nullable/defaulted keys filled."""
    if not isinstance(data, dict):
        raise TypeError(f"{cls.__name__} must be an object")
    hints = typing.get_type_hints(cls)
    kwargs = {}
    for f in fields(cls):
        if f.name in data:
            kwargs[f.name] = data[f.name]
        elif f.default is MISSING and f.default_factory is MISSING:
            if type(None) in typing.get_args(hints[f.name]):
                kwargs[f.name] = None
            else:
                raise KeyError(f.name)
    return kwargs


def _record_from_dict(data) -> InstallRecord:
    kwargs = _build(InstallRecord, data)
    kwargs["source"] = InstalledSource(**_build(InstalledSource, kwargs["source"]))
    if not isinstance(kwargs["extras"], dict) or not isinstance(kwargs["files"], dict):
        raise TypeError("extras and files must be objects")
    kwargs["extras"] = {k: InstalledSource(**_build(InstalledSource, v))
                        for k, v in kwargs["extras"].items()}
    kwargs["files"] = {k: RecordedFile(**_build(RecordedFile, v))
                       for k, v in kwargs["files"].items()}
    if not isinstance(kwargs["steps"], list) or not all(isinstance(s, dict) for s in kwargs["steps"]):
        raise TypeError("steps must be a list of objects")
    if kwargs["previous"] is not None and not isinstance(kwargs["previous"], dict):
        raise TypeError("previous must be an object")
    return InstallRecord(**kwargs)


def _check_id(catalog_id: str) -> str:
    if (not isinstance(catalog_id, str) or not catalog_id
            or catalog_id in (".", "..") or "/" in catalog_id or "\\" in catalog_id
            or "\x00" in catalog_id or ":" in catalog_id):
        raise ValueError(f"Not a safe catalog id: {catalog_id!r}")
    return catalog_id


class RecordStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _file(self, catalog_id: str) -> Path:
        return self.root / f"{_check_id(catalog_id)}.json"

    def load(self, catalog_id: str) -> InstallRecord | None:
        path = self._file(catalog_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return _record_from_dict(data)
        except FileNotFoundError:
            return None  # not installed: the normal case
        except (OSError, ValueError, TypeError, KeyError) as e:
            # Never silent: an unreadable record makes an installed game look uninstalled.
            log.warning("The install record %s couldn't be read: %s", path, e)
            return None

    def exists(self, catalog_id: str) -> bool:
        """True when a record file is there, even if it can't be read. ValueError for an unsafe id."""
        return self._file(catalog_id).exists()

    def save(self, record: InstallRecord) -> None:
        path = self._file(record.catalog_id)
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(asdict(record), indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def delete(self, catalog_id: str) -> None:
        self._file(catalog_id).unlink(missing_ok=True)

    def all(self) -> dict[str, InstallRecord]:
        result = {}
        if not self.root.is_dir():
            return result
        for path in sorted(self.root.glob("*.json")):
            record = self.load(path.stem)
            if record is not None:
                result[path.stem] = record
        return result

    # The library's view of the store. Shared runtimes keep `kind="runtime"` records here too; they are never
    # games, so the library never lists, changes or deletes them. A catalog id from a hand-edited games.json may
    # be unsafe as a file name: for the library that is simply "no record".

    def game_records(self) -> dict[str, InstallRecord]:
        """The readable records of installed games (runtimes left out)."""
        return {cid: rec for cid, rec in self.all().items() if rec.kind == "game"}

    def load_game(self, catalog_id: str) -> InstallRecord | None:
        """The game's record, or None: none, unreadable, an unsafe id, or a record that isn't a game's."""
        try:
            record = self.load(catalog_id)
        except ValueError:
            log.warning("Not a safe catalog id, so it has no install record: %r", catalog_id)
            return None
        return record if record is not None and record.kind == "game" else None

    def delete_game(self, catalog_id: str) -> None:
        """Delete a game's record (an unreadable one too). Never one that isn't a game's (a runtime's), and an
        unsafe id has nothing to delete. OSError if the file can't be deleted."""
        try:
            record = self.load(catalog_id)
        except ValueError:
            log.warning("Not a safe catalog id, so it has no install record: %r", catalog_id)
            return
        if record is not None and record.kind != "game":
            log.warning("The install record %s is a %s's, not a game's, so it was kept", catalog_id, record.kind)
            return
        self.delete(catalog_id)

    def owner_of_folder(self, folder: Path, libraries: list[dict]) -> InstallRecord | None:
        for record in self.all().values():
            candidate = record_folder(record, libraries)
            if candidate is not None and same_folder(candidate, folder):
                return record
        return None
