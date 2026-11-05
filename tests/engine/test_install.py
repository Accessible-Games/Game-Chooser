import os
import stat
from pathlib import Path

import pytest

from engine import jobs, messages
from engine.errors import Cancelled, ExtractError, InstallFailed
from engine.folders import target_folder
from engine.jobs import InstallRequest, JobResult, blocked_reason
from engine.messages import DEFENDER_NOTE
from tests.engine.fakes import FakeElevator, FakeFetcher, ZipExtractor, make_engine, served_source, zip_bytes

MIRROR = "https://files.accessiblegames.net/files/"


def never():
    return False


def game(routes, id="entombed", title="Entombed", files=None, steps=None, developer=("Ghorse",), method="zip",
         install=None, url=None, **fields):
    files = files if files is not None else {"entombed.exe": b"exe", "sounds/a.wav": b"wav"}
    data = files if isinstance(files, bytes) else zip_bytes(files)
    source = served_source(routes, url or f"{MIRROR}{id}/{id}.zip", data, method=method, steps=steps,
                           **(install or {}))
    return {"id": id, "kind": "game", "title": title, "developer": list(developer), "genres": ["Arcade"],
            "year": 2008, "version": "1.0", "sources": [source], **fields}


def extra(routes, id, files, steps=None, parent="entombed"):
    source = served_source(routes, f"{MIRROR}{id}/{id}.zip", zip_bytes(files), steps=steps)
    return {"id": id, "kind": "extra", "title": id.replace("-", " ").title(), "install_into": parent,
            "version": "2", "sources": [source]}


def setup(tmp_path, entries, routes, **ctx):
    engine = make_engine(tmp_path, entries, fetcher=FakeFetcher(routes), **ctx)
    return engine, engine.ctx, tmp_path / "Games"


def leftovers(ctx, lib):
    """Anything the install left behind in the library's staging folder or the work folder."""
    staging = lib / ".gamechooser-staging"
    return [p for root in (staging, ctx.work_root) if root.exists() for p in root.rglob("*")]


def test_fresh_install(tmp_path):
    routes = {}
    e = game(routes)
    engine, ctx, lib = setup(tmp_path, [e], routes)
    records = ctx.records
    seen = []
    r = engine.run(InstallRequest("install", "entombed", "Games", lib), seen.append, never)
    assert (lib / "Entombed" / "entombed.exe").exists() and not (lib / ".gamechooser-staging" / "entombed").exists()
    rec = records.load("entombed")
    assert rec.files["entombed.exe"].owner == "entombed" and rec.folder == "Entombed"
    assert r.game["launch_path"] == "Entombed/entombed.exe" and r.game["catalog_id"] == "entombed"
    assert not any(ctx.work_root.rglob("*.zip"))            # download deleted
    assert isinstance(r, JobResult) and r.action == "install" and r.title == "Entombed" and r.notes == []
    assert r.game == {
        "title": "Entombed", "developer": "Ghorse", "genre": "Arcade", "year": "2008", "platforms": ["Windows"],
        "launch_path": "Entombed/entombed.exe", "library_name": "Games", "catalog_id": "entombed",
        "installed_source": {"url": e["sources"][0]["url"], "sha256": e["sources"][0]["sha256"],
                             "size": e["sources"][0]["size"]},
        "installed_extras": [],
    }
    assert rec.launch_path == "entombed.exe" and rec.library_name == "Games" and rec.method == "zip"
    assert rec.source.sha256 == e["sources"][0]["sha256"] and rec.source.catalog_sha256 == e["sources"][0]["sha256"]
    assert rec.source.position == 0 and rec.source.version == "1.0" and rec.extras == {} and rec.steps == []
    assert set(rec.files) == {"entombed.exe", "sounds/a.wav"}
    assert {p.phase for p in seen} >= {"downloading", "verifying", "extracting", "finishing"}
    assert leftovers(ctx, lib) == []


def test_required_extra_always_installed_and_tagged(tmp_path):
    routes = {}
    e = game(routes, files={"entombed.exe": b"exe", "data/patch.dat": b"old", "sounds/a.wav": b"wav"},
             extras=[{"id": "entombed-patch", "required": True}])
    x = extra(routes, "entombed-patch", {"data/patch.dat": b"new", "data/more.dat": b"more",
                                         "sounds/a.wav": b"wav"})
    engine, ctx, lib = setup(tmp_path, [e, x], routes)
    r = engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert (lib / "Entombed" / "data" / "patch.dat").read_bytes() == b"new"
    rec = ctx.records.load("entombed")
    assert rec.files["data/patch.dat"].owner == "entombed-patch"
    assert rec.files["data/more.dat"].owner == "entombed-patch"
    assert rec.files["sounds/a.wav"].owner == "entombed-patch"   # same bytes, but the extra wrote it last
    assert rec.files["entombed.exe"].owner == "entombed"
    assert rec.extras["entombed-patch"].sha256 == x["sources"][0]["sha256"]
    assert rec.extras["entombed-patch"].version == "2"
    assert r.game["installed_extras"] == ["entombed-patch"]
    assert leftovers(ctx, lib) == []


def test_optional_extra_only_when_chosen(tmp_path):
    routes = {}
    e = game(routes, extras=[{"id": "music-pack", "required": False}, {"id": "voice-pack", "required": False}])
    music = extra(routes, "music-pack", {"music/song.ogg": b"song"})
    voice = extra(routes, "voice-pack", {"voice/hello.ogg": b"hi"})
    engine, ctx, lib = setup(tmp_path, [e, music, voice], routes)
    r = engine.run(InstallRequest("install", "entombed", "Games", lib, extras=("voice-pack",)), lambda p: None, never)
    assert (lib / "Entombed" / "voice" / "hello.ogg").exists()
    assert not (lib / "Entombed" / "music").exists()
    assert music["sources"][0]["url"] not in ctx.fetcher.requests
    assert r.game["installed_extras"] == ["voice-pack"]
    assert set(ctx.records.load("entombed").extras) == {"voice-pack"}


def test_extract_dir_and_method_none(tmp_path):
    routes = {}
    e = game(routes, files={"Entombed v1/entombed.exe": b"exe", "readme.txt": b"r"},
             install={"extract_dir": "Entombed v1"})
    pong = game(routes, id="pong", title="Pong", files=b"MZ pong", method="none", url=f"{MIRROR}pong/pong.exe")
    engine, ctx, lib = setup(tmp_path, [e, pong], routes)
    engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert (lib / "Entombed" / "entombed.exe").exists() and not (lib / "Entombed" / "readme.txt").exists()
    r = engine.run(InstallRequest("install", "pong", "Games", lib), lambda p: None, never)
    assert (lib / "Pong" / "pong.exe").read_bytes() == b"MZ pong"
    assert r.game["launch_path"] == "Pong/pong.exe" and ctx.records.load("pong").method == "none"
    assert leftovers(ctx, lib) == []


def test_file_steps_run_in_staging_before_detection(tmp_path):
    routes = {}
    e = game(routes, files={"bin/run.exe": b"exe", "bin/old.dat": b"old"},
             steps=[{"action": "move", "from": "bin/run.exe", "to": "run.exe"},
                    {"action": "delete", "path": "bin/old.dat"}],
             launch={"windows": {"path": "run.exe"}})
    engine, ctx, lib = setup(tmp_path, [e], routes)
    r = engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert r.game["launch_path"] == "Entombed/run.exe"
    assert (lib / "Entombed" / "run.exe").exists() and not (lib / "Entombed" / "bin" / "run.exe").exists()
    assert set(ctx.records.load("entombed").files) == {"run.exe"}


def test_extra_file_steps_run_on_the_game_tree_and_tag_their_output(tmp_path):
    routes = {}
    e = game(routes, extras=[{"id": "hd-sounds", "required": True}])
    x = extra(routes, "hd-sounds", {"hd/a.wav": b"hd"},
              steps=[{"action": "copy", "from": "hd/a.wav", "to": "sounds/a.wav"}])
    engine, ctx, lib = setup(tmp_path, [e, x], routes)
    engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert (lib / "Entombed" / "sounds" / "a.wav").read_bytes() == b"hd"
    files = ctx.records.load("entombed").files
    assert files["sounds/a.wav"].owner == "hd-sounds" and files["hd/a.wav"].owner == "hd-sounds"


def test_system_steps_run_after_rename_with_final_paths(tmp_path):
    routes = {}
    reg_step = {"action": "registry", "hive": "HKCU", "key": r"Software\Entombed", "name": "Path",
                "type": "REG_SZ", "value": "x"}
    e = game(routes, files={"entombed.exe": b"exe", "dx.dll": b"dll"},
             steps=[{"action": "register", "path": "dx.dll"}, reg_step])
    lib = tmp_path / "Games"
    placed_when_run = []

    class WatchingElevator(FakeElevator):
        def start(self, exe, args, elevated, cwd=None):
            placed_when_run.append((lib / "Entombed" / "dx.dll").exists())
            return super().start(exe, args, elevated, cwd)

    fake_elev = WatchingElevator()
    engine, ctx, lib = setup(tmp_path, [e], routes, elevator=fake_elev)
    engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert fake_elev.calls[0][1] == f'/s "{lib / "Entombed" / "dx.dll"}"'
    assert fake_elev.calls[0] == ("regsvr32.exe", f'/s "{lib / "Entombed" / "dx.dll"}"', True)
    assert placed_when_run == [True]
    assert ctx.registry.get("HKCU", r"Software\Entombed", "Path") == (True, "x", "REG_SZ")
    steps = ctx.records.load("entombed").steps
    assert [s["action"] for s in steps] == ["register", "registry"]
    assert steps[1]["undo"] == {"existed": False, "previous": None, "previous_type": None}


def test_failing_system_step_rolls_back(tmp_path):
    routes = {}
    lib = tmp_path / "Games"
    e = game(routes, files={"entombed.exe": b"exe", "fix.exe": b"fix"},
             steps=[{"action": "registry", "hive": "HKCU", "key": r"Software\Entombed", "name": "Path",
                     "type": "REG_SZ", "value": "x"},
                    {"action": "run", "path": "fix.exe"}])
    fake_elev = FakeElevator(exit_codes={str(lib / "Entombed" / "fix.exe"): 1})
    engine, ctx, lib = setup(tmp_path, [e], routes, elevator=fake_elev)
    fake_reg, records = ctx.registry, ctx.records
    with pytest.raises(InstallFailed) as ei:
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert ei.value.message == messages.STEP_RUN_FAILED.format(name="fix.exe", code=1)
    assert not (lib / "Entombed").exists() and fake_reg.values == {} and records.load("entombed") is None
    assert leftovers(ctx, lib) == []


def test_failing_extra_system_step_undoes_the_games_steps(tmp_path):
    routes = {}
    lib = tmp_path / "Games"
    e = game(routes, steps=[{"action": "registry", "hive": "HKCU", "key": r"Software\Entombed", "name": "Path",
                             "type": "REG_SZ", "value": "x"}],
             extras=[{"id": "entombed-patch", "required": True}])
    x = extra(routes, "entombed-patch", {"patch.exe": b"p"}, steps=[{"action": "run", "path": "patch.exe"}])
    fake_elev = FakeElevator(exit_codes={str(lib / "Entombed" / "patch.exe"): 3})
    engine, ctx, lib = setup(tmp_path, [e, x], routes, elevator=fake_elev)
    with pytest.raises(InstallFailed) as ei:
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert ei.value.message == messages.STEP_RUN_FAILED.format(name="patch.exe", code=3)
    assert ctx.registry.values == {} and not (lib / "Entombed").exists()
    assert ctx.records.load("entombed") is None and leftovers(ctx, lib) == []


def test_declined_elevation_fails_plainly_and_rolls_back(tmp_path):
    routes = {}
    e = game(routes, files={"entombed.exe": b"exe", "dx.dll": b"dll"},
             steps=[{"action": "register", "path": "dx.dll"}])
    engine, ctx, lib = setup(tmp_path, [e], routes, elevator=FakeElevator(decline=True))
    with pytest.raises(InstallFailed) as ei:
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert ei.value.message == messages.ELEVATION_DECLINED.format(what="dx.dll")
    assert not (lib / "Entombed").exists() and ctx.records.load("entombed") is None


def test_no_exe_fails_with_defender_note(tmp_path):
    routes = {}
    e = game(routes, files={"readme.txt": b"r", "setup.exe": b"installer"})   # setup.exe is auto-excluded
    engine, ctx, lib = setup(tmp_path, [e], routes)
    with pytest.raises(InstallFailed) as ei:
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert ei.value.message == messages.NO_EXE.format(title="Entombed") + "\n\n" + DEFENDER_NOTE
    assert DEFENDER_NOTE in ei.value.message and list(lib.iterdir()) in ([], [lib / ".gamechooser-staging"]) and not any((lib / ".gamechooser-staging").iterdir())
    assert leftovers(ctx, lib) == [] and ctx.records.load("entombed") is None


def test_cancel_during_download_leaves_nothing(tmp_path):
    routes = {}
    e = game(routes)
    engine, ctx, lib = setup(tmp_path, [e], routes)
    with pytest.raises(Cancelled):
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, lambda: True)
    assert list(lib.iterdir()) in ([], [lib / ".gamechooser-staging"])
    assert leftovers(ctx, lib) == [] and ctx.records.load("entombed") is None


def test_cancel_after_download_leaves_nothing(tmp_path):
    routes = {}
    e = game(routes)
    engine, ctx, lib = setup(tmp_path, [e], routes)
    asked = []

    def cancel():
        asked.append(1)
        return len(asked) > 1   # the download itself finishes

    with pytest.raises(Cancelled):
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, cancel)
    assert list(lib.iterdir()) in ([], [lib / ".gamechooser-staging"])
    assert leftovers(ctx, lib) == [] and ctx.records.load("entombed") is None


def test_existing_unrecorded_folder_is_refused_before_download(tmp_path):
    routes = {}
    e = game(routes)
    engine, ctx, lib = setup(tmp_path, [e], routes)
    records, libs, fetcher = ctx.records, ctx.libraries, ctx.fetcher
    (lib / "Entombed").mkdir()
    (lib / "Entombed" / "mine.txt").write_text("player")
    assert blocked_reason(e, lib, records, libs).startswith('A folder named "Entombed"')
    assert blocked_reason(e, lib, records, libs) == messages.FOLDER_EXISTS.format(folder="Entombed", library="Games")
    with pytest.raises(InstallFailed) as ei:
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert ei.value.message == blocked_reason(e, lib, records, libs)
    assert fetcher.requests == []
    assert [p.name for p in (lib / "Entombed").iterdir()] == ["mine.txt"]


def test_existing_folder_differing_only_in_case_is_refused(tmp_path):
    routes = {}
    e = game(routes)
    engine, ctx, lib = setup(tmp_path, [e], routes)
    (lib / "ENTOMBED").mkdir()
    if not (lib / "Entombed").exists():
        pytest.skip("case-sensitive file system")
    with pytest.raises(InstallFailed):
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert ctx.fetcher.requests == []


def test_own_recorded_folder_is_not_blocked_and_is_installed_over_as_an_update(tmp_path):
    routes = {}
    e = game(routes)
    engine, ctx, lib = setup(tmp_path, [e], routes)
    engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert blocked_reason(e, lib, ctx.records, ctx.libraries) is None
    (lib / "Entombed" / "save.dat").write_bytes(b"player")
    r = engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert r.action == "install" and r.backups == 0
    assert (lib / "Entombed" / "save.dat").read_bytes() == b"player"
    assert set(ctx.records.load("entombed").files) == {"entombed.exe", "sounds/a.wav"}


def test_duplicate_title_gets_developer_suffix(tmp_path):
    routes = {}
    first = game(routes, id="pong", title="Pong", developer=("Atari Fan",), files={"pong.exe": b"1"})
    second = game(routes, id="pong-other", title="Pong", developer=("Other Dev",), files={"pong.exe": b"2"})
    third = game(routes, id="pong-third", title="Pong", developer=("Other Dev",), files={"pong.exe": b"3"})
    engine, ctx, lib = setup(tmp_path, [first, second, third], routes)
    engine.run(InstallRequest("install", "pong", "Games", lib), lambda p: None, never)
    assert blocked_reason(second, lib, ctx.records, ctx.libraries) is None
    r = engine.run(InstallRequest("install", "pong-other", "Games", lib), lambda p: None, never)
    assert (lib / "Pong (Other Dev)" / "pong.exe").read_bytes() == b"2"
    assert r.game["launch_path"] == "Pong (Other Dev)/pong.exe"
    assert ctx.records.load("pong-other").folder == "Pong (Other Dev)"
    assert target_folder(third, lib, ctx.records, ctx.libraries) == lib / "Pong (pong-third)"
    assert target_folder(second, lib, ctx.records, ctx.libraries) == lib / "Pong (Other Dev)"   # its own folder


def test_leftover_staging_is_cleared(tmp_path):
    routes = {}
    e = game(routes)
    engine, ctx, lib = setup(tmp_path, [e], routes)
    old = lib / ".gamechooser-staging" / "entombed" / "raw"
    old.mkdir(parents=True)
    (old / "junk.exe").write_bytes(b"junk")
    stale = ctx.work_root / "entombed" / "game" / "source-0"
    stale.mkdir(parents=True)
    (stale / "old.zip").write_bytes(b"old")
    engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert not (lib / "Entombed" / "junk.exe").exists()
    assert set(ctx.records.load("entombed").files) == {"entombed.exe", "sounds/a.wav"}
    assert leftovers(ctx, lib) == []


def test_no_verified_source_fails_plainly(tmp_path):
    routes = {}
    e = game(routes)
    del routes[e["sources"][0]["url"]]
    engine, ctx, lib = setup(tmp_path, [e], routes)
    with pytest.raises(InstallFailed) as ei:
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert ei.value.message == messages.NO_SOURCE.format(title="Entombed", reasons=messages.SOURCE_UNREACHABLE)
    assert list(lib.iterdir()) in ([], [lib / ".gamechooser-staging"]) and leftovers(ctx, lib) == []


def test_changed_developer_files_note_once_and_record_the_real_file(tmp_path):
    routes = {}
    e = game(routes, extras=[{"id": "entombed-patch", "required": True}])
    x = extra(routes, "entombed-patch", {"patch.dat": b"p"})
    for entry, name in ((e, "entombed.zip"), (x, "patch.zip")):
        dev = dict(entry["sources"][0], label="Developer", url=f"https://dev.example.com/{name}")
        routes[dev["url"]] = b"the developer re-uploaded this"
        entry["sources"].insert(0, dev)
    engine, ctx, lib = setup(tmp_path, [e, x], routes)
    r = engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert r.notes == [messages.DEVELOPER_CHANGED_VERIFIED]
    assert r.game["installed_source"]["url"] == e["sources"][1]["url"]
    assert ctx.records.load("entombed").source.position == 1


def test_folder_appearing_during_install_is_not_merged(tmp_path):
    routes = {}
    e = game(routes)
    lib = tmp_path / "Games"

    class RacingExtractor(ZipExtractor):
        def extract(self, archive, dest, install, cancel):
            (lib / "Entombed").mkdir()
            (lib / "Entombed" / "mine.txt").write_text("player")
            return super().extract(archive, dest, install, cancel)

    engine, ctx, lib = setup(tmp_path, [e], routes, extractor=RacingExtractor())
    with pytest.raises(InstallFailed) as ei:
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert ei.value.message == messages.FOLDER_EXISTS.format(folder="Entombed", library="Games")
    assert [p.name for p in (lib / "Entombed").iterdir()] == ["mine.txt"]
    assert leftovers(ctx, lib) == [] and ctx.records.load("entombed") is None


def test_extract_error_cleans_staging(tmp_path):
    routes = {}
    e = game(routes)

    class BrokenExtractor(ZipExtractor):
        def extract(self, archive, dest, install, cancel):
            Path(dest).mkdir(parents=True, exist_ok=True)
            (Path(dest) / "half.dat").write_bytes(b"half")
            raise ExtractError(messages.EXTRACT_FAILED)

    engine, ctx, lib = setup(tmp_path, [e], routes, extractor=BrokenExtractor())
    with pytest.raises(InstallFailed) as ei:
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert ei.value.message == messages.EXTRACT_FAILED
    assert list(lib.iterdir()) in ([], [lib / ".gamechooser-staging"]) and leftovers(ctx, lib) == []


def test_unsafe_file_step_fails_before_anything_is_placed(tmp_path):
    routes = {}
    e = game(routes, steps=[{"action": "delete", "path": "../outside.txt"}])
    engine, ctx, lib = setup(tmp_path, [e], routes)
    with pytest.raises(InstallFailed) as ei:
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert ei.value.message == messages.STEP_UNSAFE_PATH
    assert list(lib.iterdir()) in ([], [lib / ".gamechooser-staging"]) and leftovers(ctx, lib) == []


def test_unknown_catalog_id_fails_plainly(tmp_path):
    engine, ctx, lib = setup(tmp_path, [], {})
    with pytest.raises(InstallFailed) as ei:
        engine.run(InstallRequest("install", "nope", "Games", lib), lambda p: None, never)
    assert ei.value.message == messages.NOT_IN_CATALOG


def test_library_found_by_name_alone(tmp_path):
    routes = {}
    engine, ctx, lib = setup(tmp_path, [game(routes)], routes)
    r = engine.run(InstallRequest("install", "entombed", "Games"), lambda p: None, never)
    assert (lib / "Entombed" / "entombed.exe").exists() and r.game["library_name"] == "Games"


def test_default_exclude_is_the_scanners_rules(tmp_path):
    engine, ctx, lib = setup(tmp_path, [], {})
    assert ctx.exclude(Path("setup.exe")) and not ctx.exclude(Path("entombed.exe"))


def test_record_save_failure_undoes_steps_and_removes_folder(tmp_path):
    routes = {}
    e = game(routes, steps=[{"action": "registry", "hive": "HKCU", "key": r"Software\Entombed", "name": "Path",
                             "type": "REG_SZ", "value": "x"}])
    engine, ctx, lib = setup(tmp_path, [e], routes)

    def broken_save(record):
        raise OSError("disk full")

    ctx.records.save = broken_save
    with pytest.raises(OSError):
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert ctx.registry.values == {} and not (lib / "Entombed").exists() and leftovers(ctx, lib) == []


@pytest.mark.parametrize("stuck", ["staging", "work"])
def test_temp_folder_that_cannot_be_emptied_stops_the_install(tmp_path, monkeypatch, stuck):
    routes = {}
    e = game(routes)
    engine, ctx, lib = setup(tmp_path, [e], routes)
    folder = lib / ".gamechooser-staging" / "entombed" if stuck == "staging" else ctx.work_root / "entombed"
    (folder / "raw").mkdir(parents=True)
    (folder / "raw" / "locked.exe").write_bytes(b"stale")
    real_clear = jobs._clear

    def clear(path):   # as if Defender held locked.exe open
        if Path(path) != folder:
            real_clear(path)

    monkeypatch.setattr(jobs, "_clear", clear)
    with pytest.raises(InstallFailed) as ei:
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert ei.value.message == messages.TEMP_FOLDER_NOT_CLEARED.format(path=folder)
    assert ctx.fetcher.requests == [] and ctx.records.load("entombed") is None
    assert not (lib / "Entombed").exists()
    assert [p.name for p in (folder / "raw").iterdir()] == ["locked.exe"]


def test_rollback_that_cannot_remove_the_folder_says_where_it_is(tmp_path, monkeypatch):
    routes = {}
    lib = tmp_path / "Games"
    target = lib / "Entombed"
    e = game(routes, files={"entombed.exe": b"exe", "fix.exe": b"fix"}, steps=[{"action": "run", "path": "fix.exe"}])
    engine, ctx, lib = setup(tmp_path, [e], routes,
                             elevator=FakeElevator(exit_codes={str(target / "fix.exe"): 1}))
    real_replace, real_clear = os.replace, jobs._clear

    def replace(src, dst):
        if Path(src) == target:
            raise PermissionError("a program started by the game still has fix.exe open")
        return real_replace(src, dst)

    def clear(path):   # removes everything but the file still in use
        if Path(path) == target:
            for p in Path(path).iterdir():
                if p.name != "fix.exe":
                    p.unlink() if p.is_file() else real_clear(p)
        else:
            real_clear(path)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(jobs, "_clear", clear)
    with pytest.raises(InstallFailed) as ei:
        engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    assert ei.value.message == (messages.STEP_RUN_FAILED.format(name="fix.exe", code=1) + "\n\n"
                                + messages.FOLDER_NOT_REMOVED.format(path=target))
    assert str(target) in ei.value.message
    assert [p.name for p in target.iterdir()] == ["fix.exe"] and ctx.records.load("entombed") is None


@pytest.mark.windows
def test_staging_folder_is_hidden(tmp_path):
    routes = {}
    engine, ctx, lib = setup(tmp_path, [game(routes)], routes)
    engine.run(InstallRequest("install", "entombed", "Games", lib), lambda p: None, never)
    staging = lib / ".gamechooser-staging"
    assert staging.is_dir()
    assert os.stat(staging).st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN


def test_install_into_a_library_folder_that_does_not_exist_yet(tmp_path):
    routes = {}
    new_lib = tmp_path / "Profile" / "Games"
    engine, ctx, _ = setup(tmp_path, [game(routes)], routes,
                           libraries=[{"name": "Games", "path": str(new_lib)}])
    r = engine.run(InstallRequest("install", "entombed", "Games", new_lib), lambda p: None, never)
    assert (new_lib / "Entombed" / "entombed.exe").exists() and r.game["library_name"] == "Games"
    assert ctx.records.load("entombed").folder == "Entombed"
    if hasattr(os.stat(new_lib), "st_file_attributes"):
        assert not os.stat(new_lib).st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN
        assert os.stat(new_lib / ".gamechooser-staging").st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN


def test_library_folder_that_cannot_be_created_fails_plainly(tmp_path):
    routes = {}
    (tmp_path / "blocker").write_text("a file where the library's parent folder should be")
    bad_lib = tmp_path / "blocker" / "Games"
    engine, ctx, _ = setup(tmp_path, [game(routes)], routes,
                           libraries=[{"name": "Games", "path": str(bad_lib)}])
    with pytest.raises(InstallFailed) as ei:
        engine.run(InstallRequest("install", "entombed", "Games", bad_lib), lambda p: None, never)
    assert ei.value.message == messages.LIBRARY_FOLDER_NOT_CREATED.format(path=bad_lib)
    assert ctx.fetcher.requests == []
