"""Shared test fakes for the engine tests."""

import gzip
import hashlib
import io
import json
import shutil
import zipfile
from datetime import date
from pathlib import Path

from engine.errors import Cancelled, DownloadError
from engine.extract import content_root
from engine.http import check_url_allowed


class FakeFetcher:
    """Serves canned bytes by URL; counts calls per URL."""

    def __init__(self, routes: dict[str, bytes] | None = None):
        self.routes: dict[str, bytes] = dict(routes or {})
        self.calls: dict[str, int] = {}
        self.requests: list[str] = []

    def get_bytes(self, url: str, max_bytes: int = 64 * 2**20) -> bytes:
        self.calls[url] = self.calls.get(url, 0) + 1
        if url not in self.routes:
            raise DownloadError(f"404 Not Found: {url}")
        return self.routes[url]

    def download(self, url: str, dest: Path, progress=None, cancel=None) -> None:
        """Like Fetcher.download: blocked hosts refused, bytes written, progress called, cancel honored."""
        check_url_allowed(url)
        self.requests.append(url)
        if url not in self.routes:
            raise DownloadError(f"404 Not Found: {url}")
        data = self.routes[url]
        half = len(data) // 2
        dest.write_bytes(data[:half])
        if progress:
            progress(half, len(data))
        if cancel and cancel():
            dest.unlink(missing_ok=True)
            raise Cancelled()
        dest.write_bytes(data)
        if progress:
            progress(len(data), len(data))


def publish(fetcher: FakeFetcher, base_url: str, entries: list[dict], schema_version: int = 1) -> bytes:
    """Publish index.json.gz and index-meta.json the way catalog-index does; returns the gz bytes."""
    doc = {"schema_version": schema_version, "generated_at": "2026-11-04T00:00:00Z", "entries": entries}
    gz = gzip.compress(json.dumps(doc).encode("utf-8"), mtime=0)
    meta = {
        "schema_version": schema_version,
        "generated_at": doc["generated_at"],
        "entry_count": len(entries),
        "sha256": hashlib.sha256(gz).hexdigest(),
        "index_url": "index.json.gz",
    }
    fetcher.routes[base_url + "index.json.gz"] = gz
    fetcher.routes[base_url + "index-meta.json"] = json.dumps(meta).encode("utf-8")
    return gz


class FakeProcess:
    """A finished process as returned by FakeElevator.start."""

    def __init__(self, code: int, pid: int):
        self.code = code
        self.pid = pid
        self.handle = pid

    def wait(self, timeout: float | None = None) -> int | None:
        return self.code

    def close(self) -> None:
        pass


class FakeElevator:
    """Records (exe, args, elevated) calls; never runs anything.

    `exit_codes` maps an exe name to its exit code (default 0). With `decline=True`
    every elevated call raises ElevationDeclined, as if the UAC prompt was refused.
    """

    def __init__(self, exit_codes: dict[str, int] | None = None, decline: bool = False):
        self.exit_codes = dict(exit_codes or {})
        self.decline = decline
        self.calls: list[tuple[str, str, bool]] = []
        self.cwds: list[Path | None] = []

    def start(self, exe: str, args: str, elevated: bool, cwd: Path | None = None) -> FakeProcess:
        from engine.errors import ElevationDeclined
        self.calls.append((exe, args, elevated))
        self.cwds.append(cwd)
        if elevated and self.decline:
            raise ElevationDeclined("The administrator prompt was declined.")
        return FakeProcess(self.exit_codes.get(exe, 0), 1000 + len(self.calls))

    def run(self, exe: str, args: str, elevated: bool, cwd: Path | None = None) -> int:
        return self.start(exe, args, elevated, cwd).wait(None)


class _FakeKey:
    def __init__(self, hive: int, path: str, access: int):
        self.hive, self.path, self.access = hive, path, access

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def Close(self):
        pass


class FakeWinreg:
    """An in-memory stand-in for the parts of `winreg` the engine uses. Keys and names are case-insensitive."""

    HKEY_CURRENT_USER = 0x80000001
    HKEY_LOCAL_MACHINE = 0x80000002
    KEY_READ = 0x20019
    KEY_SET_VALUE = 0x0002
    KEY_WOW64_32KEY = 0x0200
    KEY_WOW64_64KEY = 0x0100
    REG_NONE, REG_SZ, REG_EXPAND_SZ, REG_BINARY, REG_DWORD = 0, 1, 2, 3, 4
    REG_MULTI_SZ, REG_QWORD = 7, 11

    def __init__(self):
        # (hive, lowercased key) -> {lowercased name: (value, type)}
        self.keys: dict[tuple[int, str], dict[str, tuple[object, int]]] = {}
        self.opens: list[tuple[int, str, int]] = []

    @staticmethod
    def _norm(key: str) -> str:
        return key.strip("\\").lower()

    def OpenKey(self, hive, key, reserved=0, access=0x20019):
        self.opens.append((hive, key, access))
        if (hive, self._norm(key)) not in self.keys:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return _FakeKey(hive, self._norm(key), access)

    def CreateKeyEx(self, hive, key, reserved=0, access=0x20006):
        self.opens.append((hive, key, access))
        self.keys.setdefault((hive, self._norm(key)), {})
        return _FakeKey(hive, self._norm(key), access)

    def QueryValueEx(self, handle, name):
        values = self.keys[(handle.hive, handle.path)]
        if name.lower() not in values:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return values[name.lower()]

    def SetValueEx(self, handle, name, reserved, type_, value):
        self.keys[(handle.hive, handle.path)][name.lower()] = (value, type_)

    def DeleteValue(self, handle, name):
        values = self.keys[(handle.hive, handle.path)]
        if name.lower() not in values:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        del values[name.lower()]

    def CloseKey(self, handle):
        pass

    # Test helpers
    def put(self, hive, key, name, value, type_):
        self.keys.setdefault((hive, self._norm(key)), {})[name.lower()] = (value, type_)

    def value(self, hive, key, name):
        return self.keys.get((hive, self._norm(key)), {}).get(name.lower())


class FakeRegistry:
    """Stands in for RegistryAccess. `values` maps (hive, lowercased key, lowercased name) -> (value, type)."""

    def __init__(self):
        self.values: dict[tuple[str, str, str], tuple[object, str]] = {}

    @staticmethod
    def _id(hive: str, key: str, name: str) -> tuple[str, str, str]:
        return hive, key.strip("\\").lower(), name.lower()

    def get(self, hive: str, key: str, name: str) -> tuple[bool, object, str | None]:
        found = self.values.get(self._id(hive, key, name))
        return (False, None, None) if found is None else (True, found[0], found[1])

    def can_restore(self, hive: str, type_: str | None, value: object) -> bool:
        return True

    def set(self, hive: str, key: str, name: str, type_: str, value: object) -> None:
        self.values[self._id(hive, key, name)] = (value, type_)

    def delete(self, hive: str, key: str, name: str) -> None:
        self.values.pop(self._id(hive, key, name), None)


class FakeRecycle:
    """Records each call's paths, then removes them (as the Recycle Bin would, from the folder's view)."""

    def __init__(self):
        self.calls: list[list[Path]] = []

    def __call__(self, paths):
        self.calls.append(list(paths))
        for p in paths:
            p = Path(p)
            assert p.is_absolute()
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()


class ZipExtractor:
    """Extractor stand-in: unpacks with `zipfile` (every archive method), copies for `none`."""

    def __init__(self):
        self.calls: list[tuple[Path, Path, dict]] = []

    def extract(self, archive: Path, dest: Path, install: dict, cancel) -> Path:
        self.calls.append((Path(archive), Path(dest), install))
        method = install.get("method")
        if method == "installer" and (install.get("installer") or {}).get("mode") != "extract":
            raise ValueError("Only installers marked mode 'extract' can be extracted")
        if method == "url":
            raise ValueError("Nothing to extract for method 'url'")
        if cancel():
            raise Cancelled()
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        if method == "none":
            shutil.copy2(archive, dest / Path(archive).name)
        else:
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(dest)
        return content_root(dest, install.get("extract_dir"))


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def served_source(routes: dict[str, bytes], url: str, data: bytes, method: str = "zip",
                  steps: list[dict] | None = None, **install) -> dict:
    """A windows source whose sha256/size match `data`; also adds `data` to `routes` at `url`."""
    routes[url] = data
    source = {"label": "Mirror", "url": url, "platform": "windows",
              "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
              "install": {"method": method, **install}}
    if steps:
        source["steps"] = steps
    return source


def make_engine(tmp_path: Path, entries, **ctx_overrides):
    """An InstallEngine over fakes, with one library "Games" at tmp_path/Games.

    `entries` is a list of entry dicts or a dict by id. Returns the engine; its `ctx` holds the fakes.
    """
    from engine.jobs import EngineContext, InstallEngine
    from engine.records import RecordStore

    if not isinstance(entries, dict):
        entries = {e["id"]: e for e in entries}
    lib = tmp_path / "Games"
    lib.mkdir(exist_ok=True)
    values = dict(
        entries=entries,
        records=RecordStore(tmp_path / "installs"),
        fetcher=FakeFetcher(),
        extractor=ZipExtractor(),
        elevator=FakeElevator(),
        registry=FakeRegistry(),
        work_root=tmp_path / "work",
        runtimes_root=tmp_path / "runtimes",
        libraries=[{"name": "Games", "path": str(lib)}],
        today=lambda: date(2026, 11, 4),
        recycle=FakeRecycle(),
    )
    values.update(ctx_overrides)
    return InstallEngine(EngineContext(**values))
