"""Build and serve a small local test catalog for trying Game Chooser's installs by hand.

Run: uv run python scripts/serve_test_catalog.py
It prints "Serving test catalog at http://127.0.0.1:8765/". Point Preferences > Installing > catalog address at it.

  --version 2          republish with version 2 of Test Game (a changed data file and settings file, and a new level)
  --add-launch-path    republish Test Game Without Launch Path with its launch path (launcher/start.bat)
  --throttle KBPS      send the game files slowly (kilobytes a second), to leave time to cancel a download
  --port N             listen on another port (the default is 8765)
  --build-only         build the catalog and stop, without serving

The entries (catalog schema v2) come from tests/catalog_fixtures/entries/*.json, the files they install from
tests/catalog_fixtures/games/. Test Shared Extra Level is an optional extra of two games (Test Game With Extra and
Test Game Tar), each getting its own copy. Test Story is a two-page web page game (it opens in the browser). Test HTA
Game is one harmless .hta file with no script (Windows' mshta.exe runs it). The zips,
the 7z, the tar.gz, TinyGame.exe (a copy of where.exe) and the index are built into
tests/catalog_fixtures/_build/ (git-ignored). Only the source files are committed. Every download address points at this
server; nothing here contacts a real host. (The entries' web page links point at example.com and example.org, which only
open when chosen.)
"""

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "catalog_fixtures"
ENTRIES_DIR = FIXTURES / "entries"
GAMES_DIR = FIXTURES / "games"
DEFAULT_BUILD_DIR = FIXTURES / "_build"
DEFAULT_PORT = 8765
TINY_GAME_SOURCE = Path(r"C:\Windows\System32\where.exe")
_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
_LAUNCHER = "launcher/start.bat"
SCHEMA_VERSION = 2


def base_url(port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}/"


def _zip_dir(source: Path, out: Path) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            info = zipfile.ZipInfo(path.relative_to(source).as_posix(), _ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())


def _tar_gz_dir(source: Path, out: Path) -> None:
    """A .tar.gz of `source`'s contents (its top folder included), with fixed times so it builds the same."""
    def fixed(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.mtime, info.uid, info.gid, info.uname, info.gname = 1767225600, 0, 0, "", ""
        return info

    with tarfile.open(out, "w:gz") as tar:
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            tar.add(path, arcname=path.relative_to(source.parent).as_posix(), filter=fixed)


def _add_extras(entries: list[dict]) -> None:
    """Each entry's `extras`, as catalog-index computes them: every entry whose `extends` list names it, by id.
    An extra that extends several games is listed on each of them."""
    for entry in entries:
        extras = sorted((e["id"], bool(e.get("required"))) for e in entries
                        if entry["id"] in (e.get("extends") or []))
        if extras:
            entry["extras"] = [{"id": i, "required": required} for i, required in extras]


def _seven_zip_dir(source: Path, out: Path, seven_zip: Path) -> None:
    out.unlink(missing_ok=True)
    subprocess.run([str(seven_zip), "a", "-t7z", str(out), "*"],
                   cwd=source, check=True, capture_output=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_seven_zip() -> Path | None:
    path = ROOT / "bin" / "7z.exe"
    return path if path.is_file() else None


def build_catalog(out_dir: Path = DEFAULT_BUILD_DIR, port: int = DEFAULT_PORT, version: int = 1,
                  add_launch_path: bool = False, seven_zip: Path | None = None) -> dict:
    """Build the files and the index into `out_dir`; returns the index document.

    Without a 7-Zip (bin/7z.exe, from scripts/fetch_tools.py) the 7z game is left out, as is the
    single-exe game when where.exe isn't there."""
    out_dir = Path(out_dir)
    files = out_dir / "files"
    files.mkdir(parents=True, exist_ok=True)
    seven_zip = seven_zip or find_seven_zip()
    base = base_url(port)

    def served(name: str) -> tuple[Path, str]:
        return files / name, f"{base}files/{name}"

    # entry id -> (archive name, how to build it)
    builds: dict[str, tuple[str, object]] = {
        "test-game": (f"test-game-v{version}.zip", lambda p: _zip_dir(GAMES_DIR / "test-game" / f"v{version}", p)),
        "test-game-no-path": ("test-game-no-path.zip", lambda p: _zip_dir(GAMES_DIR / "test-game-no-path", p)),
        "test-game-extras": ("test-game-extras.zip", lambda p: _zip_dir(GAMES_DIR / "test-game-extras", p)),
        "trace-test": ("trace-test.zip", lambda p: _zip_dir(GAMES_DIR / "trace-test", p)),
        "test-extra": ("test-extra.zip", lambda p: _zip_dir(GAMES_DIR / "test-extra", p)),
        "test-shared-extra": ("test-shared-extra.zip", lambda p: _zip_dir(GAMES_DIR / "test-shared-extra", p)),
        "test-game-page": ("test-game-page.zip", lambda p: _zip_dir(GAMES_DIR / "test-game-page", p)),
        "test-game-hta": ("test-game-hta.zip", lambda p: _zip_dir(GAMES_DIR / "test-game-hta", p)),
        "test-game-tar": ("test-game-tar.tar.gz", lambda p: _tar_gz_dir(GAMES_DIR / "test-game-tar" / "TarGame", p)),
    }
    if seven_zip is not None:
        builds["test-game-7z"] = ("test-game-7z.7z", lambda p: _seven_zip_dir(GAMES_DIR / "test-game-7z", p, seven_zip))
    if TINY_GAME_SOURCE.is_file():
        builds["test-game-none"] = ("TinyGame.exe", lambda p: shutil.copyfile(TINY_GAME_SOURCE, p))

    entries = []
    for entry_file in sorted(ENTRIES_DIR.glob("*.json")):
        entry = json.loads(entry_file.read_text(encoding="utf-8"))
        if entry["id"] not in builds:
            continue
        name, build = builds[entry["id"]]
        path, url = served(name)
        build(path)
        entry["sources"][0].update(url=url, sha256=_sha256(path), size=path.stat().st_size)
        if entry["id"] == "test-game" and version == 2:
            entry["version"] = "2.0"
        if entry["id"] == "test-game-no-path" and add_launch_path:
            entry["launch"] = {"windows": {"path": _LAUNCHER}}
        entries.append(entry)
    entries.sort(key=lambda e: e["id"])
    _add_extras(entries)

    doc = {"schema_version": SCHEMA_VERSION, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "entries": entries}
    gz = gzip.compress(json.dumps(doc, indent=1).encode("utf-8"), mtime=0)
    (out_dir / "index.json.gz").write_bytes(gz)
    meta = {"schema_version": SCHEMA_VERSION, "generated_at": doc["generated_at"], "entry_count": len(entries),
            "sha256": hashlib.sha256(gz).hexdigest(), "index_url": "index.json.gz"}
    (out_dir / "index-meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return doc


class _Handler(SimpleHTTPRequestHandler):
    throttle_kbps = 0

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    def copyfile(self, source, outputfile):
        if not self.throttle_kbps:
            return super().copyfile(source, outputfile)
        chunk = max(1024, self.throttle_kbps * 1024 // 10)
        while data := source.read(chunk):
            outputfile.write(data)
            outputfile.flush()
            time.sleep(0.1)


class _Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError):  # a player cancelling a download is not an error
            super().handle_error(request, client_address)


def make_server(out_dir: Path, port: int = DEFAULT_PORT, throttle_kbps: int = 0) -> _Server:
    handler = type("Handler", (_Handler,), {"throttle_kbps": throttle_kbps})
    return _Server(("127.0.0.1", port), partial(handler, directory=str(out_dir)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", type=int, choices=(1, 2), default=1, help="the Test Game version to publish")
    parser.add_argument("--add-launch-path", action="store_true",
                        help="publish a launch path for Test Game Without Launch Path")
    parser.add_argument("--throttle", type=int, default=0, metavar="KBPS", help="slow the file downloads down")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    parser.add_argument("--build-only", action="store_true", help="build the catalog and stop")
    args = parser.parse_args(argv)

    if find_seven_zip() is None:
        print("bin/7z.exe is missing, so the 7z game is left out. "
              "Fetch the tools with: uv run python scripts/fetch_tools.py")
    doc = build_catalog(args.build_dir, args.port, args.version, args.add_launch_path)
    if args.build_only:
        print(f"Built {len(doc['entries'])} entries in {args.build_dir}")
        return 0
    server = make_server(args.build_dir, args.port, args.throttle)
    print(f"Serving test catalog at {base_url(args.port)}", flush=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
