import pytest

import engine.sources
from engine.records import InstalledSource, InstallRecord
from engine.status import EXE_NOT_FOUND, READY, UPDATE_AVAILABLE, game_status, update_available


def record(url="https://dev/x.zip", sha256="a" * 64, size=10, catalog_sha256=None, catalog_size=None,
           version=None, position=0, kind="game", tracked=True, uninstalled=False, extras=None):
    return InstallRecord(
        catalog_id="g", kind=kind, library_name="", folder="g", launch_path="g.exe",
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


def test_hashed_sources_compare_installed_reference():
    rec = record(sha256="d" * 64, catalog_sha256="c" * 64)
    assert not update_available(rec, entry([src(sha="c" * 64)]), "windows")
    assert update_available(rec, entry([src(sha="e" * 64)]), "windows")


def test_any_matching_hashed_source_means_no_update():
    rec = record(catalog_sha256="c" * 64)
    assert not update_available(rec, entry([src(sha="e" * 64), src(sha="c" * 64)]), "windows")


def test_installed_developer_file_later_accepted_by_the_catalog_is_not_an_update():
    """M3: installed with catalog X and the developer's Z (noted); when the catalog accepts Z, nothing is new."""
    rec = record(sha256="z" * 64, catalog_sha256="x" * 64)
    assert not update_available(rec, entry([src(sha="z" * 64)]), "windows")
    assert not update_available(rec, entry([src(sha="x" * 64)]), "windows")
    assert update_available(rec, entry([src(sha="y" * 64)]), "windows")


def test_hashes_added_later_compare_actual():
    assert not update_available(record(sha256="a" * 64, catalog_sha256=None), entry([src(sha="a" * 64)]), "windows")


def test_hashless_url_size_version_changes():
    rec = record(url="https://dev/x1.zip", catalog_size=10, version="1.0", position=0)
    assert not update_available(rec, entry([src(url="https://dev/x1.zip", size=10)], version="1.0"), "windows")
    assert update_available(rec, entry([src(url="https://dev/x2.zip")], version="1.0"), "windows")
    assert update_available(rec, entry([src(url="https://dev/x1.zip", size=10)], version="1.1"), "windows")
    assert update_available(rec, entry([src(url="https://dev/x1.zip", size=11)], version="1.0"), "windows")


def test_size_compared_only_when_both_sides_have_one():
    rec = record(url="https://dev/x.zip", catalog_size=None, version="1.0")
    assert not update_available(rec, entry([src(size=99)], version="1.0"), "windows")
    rec = record(url="https://dev/x.zip", catalog_size=10, version="1.0")
    assert not update_available(rec, entry([src()], version="1.0"), "windows")


def test_hashless_uses_recorded_position_with_fallback_to_first():
    rec = record(url="https://dev/b.zip", version="1.0", position=1)
    sources = [src(url="https://dev/a.zip"), src(url="https://dev/b.zip")]
    assert not update_available(rec, entry(sources, version="1.0"), "windows")
    assert update_available(rec, entry([src(url="https://dev/a.zip")], version="1.0"), "windows")  # falls back to 0
    rec = record(url="https://dev/a.zip", version="1.0", position=5)
    assert not update_available(rec, entry([src(url="https://dev/a.zip")], version="1.0"), "windows")


def test_position_indexes_same_platform_sources_only():
    rec = record(url="https://dev/w.zip", version="1.0", position=0)
    sources = [src(url="https://dev/m.zip", platform="macos"), src(url="https://dev/w.zip")]
    assert not update_available(rec, entry(sources, version="1.0"), "windows")


def test_entry_gone_or_no_platform_source_is_not_an_update():
    rec = record(version="1.0")
    assert not update_available(rec, None, "windows")
    assert not update_available(rec, entry([]), "windows")
    assert not update_available(rec, entry([src(sha="e" * 64, platform="macos")], version="2.0"), "windows")
    assert not update_available(None, entry([src(sha="e" * 64)]), "windows")


def test_untracked_record_can_still_have_an_update():
    assert update_available(record(tracked=False), entry([src(sha="e" * 64)]), "windows")


@pytest.mark.parametrize("kw", [{"uninstalled": True}, {"kind": "runtime"}])
def test_uninstalled_or_runtime_never_updates(kw):
    assert not update_available(record(**kw), entry([src(sha="e" * 64)]), "windows")


def test_extras_do_not_count():
    extra = InstalledSource("https://dev/e.zip", "1" * 64, 1, None, None, 0, None)
    rec = record(sha256="a" * 64, extras={"e": extra})
    assert not update_available(rec, entry([src(sha="a" * 64)]), "windows")


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
