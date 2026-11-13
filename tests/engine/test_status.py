import pytest

import engine.sources
from engine.records import InstalledSource, InstallRecord
from engine.status import EXE_NOT_FOUND, READY, UPDATE_AVAILABLE, game_status, update_available


def record(url="https://dev/x.zip", sha256="a" * 64, size=10, catalog_sha256=None, catalog_size=None,
           version=None, position=0, kind="game", tracked=True, uninstalled=False, extras=None):
    return InstallRecord(
        catalog_id="g", kind=kind, library_id="", folder="g", launch_path="g.exe",
        source=InstalledSource(url, sha256, size, catalog_sha256, catalog_size, position, version),
        extras=extras or {}, files={}, steps=[], method="extract", tracked=tracked, uninstalled=uninstalled)


def src(url="https://dev/x.zip", sha=None, size=None, platform="windows"):
    s = {"platform": platform, "url": url}
    if sha:
        s["sha256"] = sha
    if size is not None:
        s["size"] = size
    return s


def entry(sources, version=None):
    e = {"id": "g", "sources": sources}
    if version is not None:
        e["version"] = version
    return e


HASHED = record(sha256="d" * 64, catalog_sha256="c" * 64)
# M3: installed with catalog X and the developer's Z (noted); when the catalog accepts Z, nothing is new.
DEVELOPER_Z = record(sha256="z" * 64, catalog_sha256="x" * 64)
HASHLESS = record(url="https://dev/x1.zip", catalog_size=10, version="1.0", position=0)
SECOND = record(url="https://dev/b.zip", version="1.0", position=1)
SELF_UPDATING = record(catalog_sha256="c" * 64, version="1.0")
CHANGED = entry([src(sha="e" * 64)], version="2.0")
EXTRA = InstalledSource("https://dev/e.zip", "1" * 64, 1, None, None, 0, None)


@pytest.mark.parametrize("rec, ent, expected", [
    pytest.param(HASHED, entry([src(sha="c" * 64)]), False, id="hashed: the installed catalog checksum"),
    pytest.param(HASHED, entry([src(sha="e" * 64)]), True, id="hashed: a new checksum"),
    pytest.param(record(catalog_sha256="c" * 64), entry([src(sha="e" * 64), src(sha="c" * 64)]), False,
                 id="hashed: any matching source"),
    pytest.param(DEVELOPER_Z, entry([src(sha="z" * 64)]), False, id="M3: the developer's file, now accepted"),
    pytest.param(DEVELOPER_Z, entry([src(sha="x" * 64)]), False, id="M3: the catalog's file"),
    pytest.param(DEVELOPER_Z, entry([src(sha="y" * 64)]), True, id="M3: a third file"),
    pytest.param(record(sha256="a" * 64, catalog_sha256=None), entry([src(sha="a" * 64)]), False,
                 id="hashes added later compare the actual file"),
    pytest.param(HASHLESS, entry([src(url="https://dev/x1.zip", size=10)], version="1.0"), False,
                 id="hashless: nothing changed"),
    pytest.param(HASHLESS, entry([src(url="https://dev/x2.zip")], version="1.0"), True, id="hashless: url changed"),
    pytest.param(HASHLESS, entry([src(url="https://dev/x1.zip", size=10)], version="1.1"), True,
                 id="hashless: version changed"),
    pytest.param(HASHLESS, entry([src(url="https://dev/x1.zip", size=11)], version="1.0"), True,
                 id="hashless: size changed"),
    pytest.param(record(url="https://dev/x.zip", catalog_size=None, version="1.0"),
                 entry([src(size=99)], version="1.0"), False, id="size not compared: none recorded"),
    pytest.param(record(url="https://dev/x.zip", catalog_size=10, version="1.0"), entry([src()], version="1.0"), False,
                 id="size not compared: none in the catalog"),
    pytest.param(SECOND, entry([src(url="https://dev/a.zip"), src(url="https://dev/b.zip")], version="1.0"), False,
                 id="hashless: the recorded position"),
    pytest.param(SECOND, entry([src(url="https://dev/a.zip")], version="1.0"), True,
                 id="hashless: a position that is gone falls back to the first"),
    pytest.param(record(url="https://dev/a.zip", version="1.0", position=5),
                 entry([src(url="https://dev/a.zip")], version="1.0"), False,
                 id="hashless: the fallback to the first matches"),
    pytest.param(record(url="https://dev/w.zip", version="1.0", position=0),
                 entry([src(url="https://dev/m.zip", platform="macos"), src(url="https://dev/w.zip")], version="1.0"),
                 False, id="position counts this platform's sources only"),
    pytest.param(record(version="1.0"), None, False, id="entry gone"),
    pytest.param(record(version="1.0"), entry([]), False, id="no source"),
    pytest.param(record(version="1.0"), entry([src(sha="e" * 64, platform="macos")], version="2.0"), False,
                 id="no source for this platform"),
    pytest.param(None, entry([src(sha="e" * 64)]), False, id="no record"),
    pytest.param(record(tracked=False), entry([src(sha="e" * 64)]), True, id="untracked can still update"),
    pytest.param(record(uninstalled=True), entry([src(sha="e" * 64)]), False, id="uninstalled"),
    pytest.param(record(kind="runtime"), entry([src(sha="e" * 64)]), False, id="runtime"),
    pytest.param(record(sha256="a" * 64, extras={"e": EXTRA}), entry([src(sha="a" * 64)]), False,
                 id="extras do not count"),
    pytest.param(SELF_UPDATING, CHANGED, True, id="changed"),
    pytest.param(SELF_UPDATING, {**CHANGED, "self_updating": True}, False, id="self-updating: changed"),
    pytest.param(record(version="1.0"), {**entry([src()], version="2.0"), "self_updating": True}, False,
                 id="self-updating: hashless, new version"),
    pytest.param(SELF_UPDATING, {**CHANGED, "self_updating": "yes"}, True, id="self-updating: only true counts"),
])
def test_update_available(rec, ent, expected):
    assert update_available(rec, ent, "windows") is expected


def test_game_status(tmp_path):
    rec = record(sha256="a" * 64)
    ok = entry([src(sha="a" * 64)])
    newer = entry([src(sha="e" * 64)])
    exe = tmp_path / "game.exe"
    exe.write_bytes(b"x")
    assert game_status(None, True, None, None, "windows") == READY == "Ready"
    assert game_status(str(exe), False, rec, ok, "windows") == READY
    assert game_status(str(exe), False, rec, newer, "windows") == UPDATE_AVAILABLE == "Update available"
    assert game_status(None, True, rec, newer, "windows") == UPDATE_AVAILABLE
    assert game_status(str(tmp_path / "missing.exe"), False, rec, ok, "windows") == EXE_NOT_FOUND == "Executable not found"
    assert game_status(str(tmp_path / "missing.exe"), False, rec, newer, "windows") == EXE_NOT_FOUND
    assert game_status(None, False, rec, ok, "windows") == EXE_NOT_FOUND


def test_status_never_hashes_files(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("status must not hash files")
    monkeypatch.setattr(engine.sources, "hash_file", boom)
    exe = tmp_path / "game.exe"
    exe.write_bytes(b"x")
    rec = record()
    assert game_status(str(exe), False, rec, entry([src(sha="e" * 64)]), "windows") == UPDATE_AVAILABLE


def test_library_not_found_wins():
    from engine.status import LIBRARY_NOT_FOUND, game_status
    assert game_status("X:/nope.exe", False, None, None, "windows", library_reachable=False) == LIBRARY_NOT_FOUND


@pytest.mark.parametrize("name", ["index.html", "jumpgame.hta"])
def test_a_page_or_hta_game_is_ready(tmp_path, name):
    launch = tmp_path / name
    launch.write_text("<html></html>")
    rec = record()
    ok = entry([src(sha="a" * 64)])
    assert game_status(str(launch), False, rec, ok, "windows") == READY
    assert game_status(str(launch), False, rec, entry([src(sha="e" * 64)]), "windows") == UPDATE_AVAILABLE
    launch.unlink()
    assert game_status(str(launch), False, rec, ok, "windows") == EXE_NOT_FOUND
