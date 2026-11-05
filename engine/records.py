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

from .paths import is_engine_dir, norm_key
from .sources import hash_file

log = logging.getLogger(__name__)


@dataclass
class RecordedFile:
    path: str
    sha256: str
    owner: str  # the catalog id that shipped it (the game or an extra), or runtime_owner(id)


RUNTIME_OWNER_PREFIX = "runtime:"


def runtime_owner(runtime_id: str) -> str:
    """The owner recorded for a file a runtime's private copy put in a game's folder (not the installer's)."""
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
    library_name: str
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


def record_folder(record: InstallRecord, libraries: list[dict]) -> Path | None:
    if not record.library_name:
        return Path(record.folder)
    for lib in libraries:
        if lib.get("name") == record.library_name:
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


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))).lower() == \
        os.path.normcase(os.path.normpath(str(b))).lower()


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

    def owner_of_folder(self, folder: Path, libraries: list[dict]) -> InstallRecord | None:
        for record in self.all().values():
            candidate = record_folder(record, libraries)
            if candidate is not None and _same_path(candidate, folder):
                return record
        return None
