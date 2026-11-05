"""Shared test fakes for the engine tests."""

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import time
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
    """A process as returned by FakeElevator.start: finished at once unless started `running`."""

    def __init__(self, code: int, pid: int, running: bool = False):
        self.code = code
        self.pid = pid
        self.handle = pid
        self.running = running

    def wait(self, timeout: float | None = None) -> int | None:
        return None if self.running else self.code

    def close(self) -> None:
        pass


_CHAIN_PREFIX = '/d /v:off /s /c "'
_CHAIN_FORMS = [
    ("icacls", re.compile(r'^icacls "([^"]+)" .+ /L /Q$')),
    ("not_reparse", re.compile(r'^\(fsutil reparsepoint query "([^"]+)" >nul 2>&1 && exit 1 \|\| type nul\)$')),
    ("mkdir", re.compile(r'^mkdir "([^"]+)"$')),
    ("absent", re.compile(r'^\(if exist "([^"]+)" exit 1\)$')),
    ("copy", re.compile(r'^copy /b "([^"]+)" "([^"]+)" >nul$')),
    ("verify", re.compile(r'^\(\(certutil -hashfile "([^"]+)" SHA256 \| findstr /i /x /l "([0-9a-f]{64})" >nul\) '
                          r'\|\| \(del /f /q "([^"]+)" & exit 1\)\)$')),
    ("regsvr32", re.compile(r'^regsvr32\.exe /s "([^"]+)"$')),
    ("marker", re.compile(r'^\(echo ([0-9a-f]{64})\) > "([^"]+)"$')),
    ("marker_or_not", re.compile(r'^\(\(echo ([0-9a-f]{64})\) > "([^"]+)" \|\| type nul\)$')),
]


def split_chain(args: str) -> list[str]:
    """The commands of an elevated chain's arguments, `/d /v:off /s /c "a && (b && c || d) && e"`, split as
    cmd.exe reads them: on `&&` outside parentheses and quotes."""
    assert args.startswith(_CHAIN_PREFIX) and args.endswith('"'), args
    commands, current, depth, quoted = [], "", 0, False
    text = args[len(_CHAIN_PREFIX):-1]
    i = 0
    while i < len(text):
        if text[i] == '"':
            quoted = not quoted
        elif not quoted and text[i] in "()":
            depth += 1 if text[i] == "(" else -1
        if not quoted and depth == 0 and text.startswith(" && ", i):
            commands.append(current)
            current, i = "", i + 4
            continue
        current += text[i]
        i += 1
    return [*commands, current]


def run_elevated_chain(args: str, owned: set[str] | None = None, exit_codes: dict[str, int] | None = None) -> int:
    """Play one of the engine's elevated chains (`cmd.exe /d /v:off /s /c "a && b && …"`: the lock-down, or
    regsvr32 followed by the marker) on disk, as cmd.exe would, stopping at the first command that fails:
    mkdir of something that exists, a reparse point where a real folder must be, a destination that is
    already there, a copy that can't be made, a copy whose SHA256 isn't the one named (the copy is deleted
    first, as the chain does), regsvr32 with the exit code `exit_codes` gives it. icacls changes nothing
    here. Every path the chain reached with `/setowner`, made, copied to or wrote the marker at is added to
    `owned` (what Administrators own afterwards). Any other command is a test error."""
    owned = set() if owned is None else owned
    for command in split_chain(args):
        for kind, form in _CHAIN_FORMS:
            match = form.match(command)
            if match:
                break
        else:
            raise AssertionError(f"unexpected command in the elevated chain: {command}")
        path = Path(match.group(1))
        if kind == "icacls":
            if "/setowner" in command:
                owned.add(os.path.normcase(str(path)))
        elif kind == "not_reparse":
            if _is_reparse_point(path):
                return 1
        elif kind == "mkdir":
            try:
                os.mkdir(path)
            except OSError:
                return 1
            owned.add(os.path.normcase(str(path)))
        elif kind == "absent":
            if os.path.lexists(path):
                return 1
        elif kind == "regsvr32":
            code = (exit_codes or {}).get("regsvr32.exe", 0)
            if code:
                return code
        elif kind == "copy":
            dest = Path(match.group(2))
            if dest.is_dir():
                dest = dest / path.name  # as cmd's copy does: into the folder (or wherever a junction leads)
            try:
                shutil.copyfile(path, dest)
            except OSError:
                return 1
            owned.add(os.path.normcase(str(dest)))
        elif kind == "verify":
            assert match.group(3) == match.group(1), command  # the file deleted is the file checked
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                digest = None
            if digest != match.group(2):
                path.unlink(missing_ok=True)
                return 1
        elif kind in ("marker", "marker_or_not"):
            marker = Path(match.group(2))
            try:
                marker.write_bytes((match.group(1) + "\r\n").encode())  # as `echo` writes it
            except OSError:
                if kind == "marker":
                    return 1
                continue  # `|| type nul`: a marker that can't be written doesn't fail the command
            owned.add(os.path.normcase(str(marker)))
    return 0


def _is_reparse_point(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return os.path.islink(path) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


class FakeElevator:
    """Records (exe, args, elevated) calls; runs nothing, except that an elevated `cmd.exe` lock-down
    chain is played on disk by `run_elevated_chain` (after `on_start`, which stands for what happens
    while the administrator prompt waits).

    `exit_codes` maps an exe name to its exit code (default 0, or the chain's). With `decline=True`
    every elevated call raises ElevationDeclined, as if the UAC prompt was refused.
    """

    def __init__(self, exit_codes: dict[str, int] | None = None, decline: bool = False,
                 on_start=None, hang: set[str] | None = None):
        self.exit_codes = dict(exit_codes or {})
        self.decline = decline
        # on_start(exe, args, elevated) stands in for what the program does (a fake installer writing
        # files); exes in `hang` keep running until their watch is terminated.
        self.on_start = on_start
        self.hang = set(hang or ())
        self.calls: list[tuple[str, str, bool]] = []
        self.cwds: list[Path | None] = []
        self.shown: list[bool] = []  # whether each call asked for the program's window to be shown
        self.owned: set[str] = set()  # what the elevated chains re-owned, made or copied (FakeSecurity reads it)

    def start(self, exe: str, args: str, elevated: bool, cwd: Path | None = None, show: bool = True) -> FakeProcess:
        from engine.errors import ElevationDeclined
        self.calls.append((exe, args, elevated))
        self.cwds.append(cwd)
        self.shown.append(show)
        if elevated and self.decline:
            raise ElevationDeclined("The administrator prompt was declined.")
        if self.on_start is not None:
            self.on_start(exe, args, elevated)
        code = self.exit_codes.get(exe)
        if code is None:
            code = run_elevated_chain(args, self.owned, self.exit_codes) if exe == "cmd.exe" and elevated else 0
        return FakeProcess(code, 1000 + len(self.calls), running=exe in self.hang)

    def run(self, exe: str, args: str, elevated: bool, cwd: Path | None = None, show: bool = True) -> int:
        return self.start(exe, args, elevated, cwd, show).wait(None)


class FakeWatch:
    """Stands in for the installer watch: done once the fake process (and, if `children_running`, the
    children it started) have stopped.

    `observed=False` is a watch that couldn't follow the whole tree; `unstoppable=True` ignores
    terminate, like an elevated installer refusing to be stopped.
    """

    def __init__(self, process: FakeProcess, elevated: bool, observed: bool = True,
                 children_running: bool = False, unstoppable: bool = False):
        self.process = process
        self.elevated = elevated
        self.observed = observed
        self.children_running = children_running
        self.unstoppable = unstoppable
        self.terminated = False
        self.closed = False

    def poll(self, timeout: float) -> bool:
        if self.process.running or self.children_running:
            time.sleep(min(timeout, 0.01))
            return False
        return True

    def exit_code(self) -> int | None:
        return self.process.wait(0)

    def tree_observed(self) -> bool:
        return self.observed

    def terminate(self) -> None:
        self.terminated = True
        if self.unstoppable:
            return
        self.process.running = False
        self.children_running = False
        self.process.code = 1

    def close(self) -> None:
        self.closed = True


def watch_with(**options):
    """A process_watch factory making FakeWatch objects with `options`; the made watches are in `.made`."""
    def make(process, elevated):
        watch = FakeWatch(process, elevated, **options)
        make.made.append(watch)
        return watch
    make.made = []
    return make


class FakeUninstallKeys:
    """The Windows installed-programs list, in memory: a reader for snapshot_uninstall_keys.

    `entries` maps (hive, view, subkey name) -> values; tests add to it from a fake installer.
    """

    def __init__(self):
        self.entries: dict[tuple[str, str, str], dict] = {}

    def add(self, name: str, values: dict, hive: str = "HKLM", view: str = "32") -> None:
        self.entries[(hive, view, name)] = dict(values)

    def __call__(self, hive: str, key: str, view: str) -> dict[str, dict]:
        return {name: dict(values) for (h, v, name), values in self.entries.items() if (h, v) == (hive, view)}


class FakeInstallerUI:
    """Records what the engine asked the player, in `events` (shared with a fake installer if given)."""

    def __init__(self, events: list | None = None, done: bool = True, locate: Path | None = None,
                 early: bool = False):
        self.events = events if events is not None else []
        self.done = done
        self.locate = locate
        self.early = early  # Done pressed at once, whether or not the installer has finished
        self.announced: list[tuple[str, Path]] = []

    def announce_target(self, title: str, target: Path) -> None:
        self.events.append("announce")
        self.announced.append((title, target))

    def wait_for_installer(self, title: str, finished) -> bool:
        self.events.append("wait")
        if self.done and not self.early:
            finished.wait(2)  # The player presses Done once the installer has finished (or after a while).
        return self.done

    def locate_folder(self, title: str) -> Path | None:
        self.events.append("locate")
        return self.locate


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


ADMINISTRATORS = "S-1-5-32-544"


class FakeSecurity:
    """Stands in for engine.runtimes.WindowsSecurity.

    A path's owner is the one set with `set_owner`; else Administrators once an elevated chain the elevator
    played has re-owned, made or copied it; else, if it exists, the current user (`user`).
    """

    def __init__(self, elevator=None, user: str = "S-1-5-21-1000-1000-1000-1001"):
        self.elevator = elevator
        self.user = user
        self.owners: dict[str, str] = {}

    def set_owner(self, path, sid: str) -> None:
        self.owners[os.path.normcase(str(path))] = sid

    def current_user(self) -> str:
        return self.user

    def owner(self, path) -> str | None:
        found = self.owners.get(os.path.normcase(str(path)))
        if found:
            return found
        if self.elevator is not None and os.path.normcase(str(path)) in self.elevator.owned:
            return ADMINISTRATORS
        return self.user if os.path.lexists(path) else None


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
        process_watch=FakeWatch,
        uninstall_reader=FakeUninstallKeys(),
    )
    values.update(ctx_overrides)
    values.setdefault("security", FakeSecurity(values["elevator"]))
    return InstallEngine(EngineContext(**values))
