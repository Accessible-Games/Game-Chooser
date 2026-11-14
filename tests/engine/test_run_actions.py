"""Repair, Undo and Extras for tracked run-mode installs (Task 14b).

Every game folder here holds a player's save and an edited config, which must survive each action.
"""

import time
from pathlib import Path

import pytest

from engine import jobs, messages, run_actions, run_installer
from engine.errors import InstallFailed
from engine.jobs import InstallRequest
from library_actions import undo_item
from tests.engine import test_update_flows as archive
from tests.engine.fakes import (MIRROR, FakeUninstallKeys, fails, never, put, quiet, reg_step, served_source, sha,
                                snapshot, zip_bytes)
from tests.engine.test_run_installer import (SETUP, install, installed_run_game, publish_v2, run_game,
                                             run_game_with_patch, setaside, setup_engine, writes)

V1 = {"lc.exe": b"exe v1", "cfg.ini": b"volume=5", "sfx/a.wav": b"a"}  # what installed_run_game's installer writes
SETUP_V2 = b"MZ setup v2"
V2 = {"lc.exe": b"exe v2", "cfg.ini": b"volume=7", "sfx/b.wav": b"b", "v2.txt": b"new in v2"}


def run(eng, action, **kw):
    return eng.run(InstallRequest(action, "lightcars", "Games", **kw), quiet, never)


def record(eng):
    return eng.ctx.records.load("lightcars")


def versioned(target):
    """A fake installer that writes v1's or v2's files, depending on which setup program was started."""
    def installer(exe, args, elevated):
        if Path(exe).name != "setup.exe":
            return  # regsvr32 for a register step
        files = V2 if Path(exe).read_bytes() == SETUP_V2 else V1
        writes(target, files)(exe, args, elevated)
    return installer


def updated_run_game(tmp_path, steps=None):
    """Light Cars installed and played, then updated by v2's installer (with v2's `steps`)."""
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes, setup=SETUP_V2, steps=steps)
    eng.ctx.elevator.on_start = versioned(target)
    run(eng, "update")
    return eng, routes, target


def installed_outside(tmp_path):
    """Light Cars installed by its installer into a folder outside every library, then played."""
    routes = {}
    e = run_game(routes, installer={"type": "inno"}, extras=[{"id": "lc-music", "required": False}])
    folder = tmp_path / "Old Games" / "Light Cars"
    keys = FakeUninstallKeys()
    place = writes(folder, V1)

    def on_start(exe, args, elevated):
        place(exe, args, elevated)
        keys.add("Light Cars_is1", {"DisplayName": "Light Cars", "InstallLocation": str(folder)})
    eng, lib = setup_engine(tmp_path, [e, music_extra(routes)], routes, on_start=on_start, uninstall_reader=keys)
    install(eng)
    rec = record(eng)
    assert rec.library_id == "" and Path(rec.folder) == folder and rec.tracked
    put(folder, "cfg.ini", b"volume=9 mine")
    put(folder, "saves/slot1.sav", b"save")
    return eng, routes, folder


def publish_archive(eng, routes, files):
    data = zip_bytes(files)
    eng.ctx.entries["lightcars"] = dict(eng.ctx.entries["lightcars"], version="2.0",
                                        sources=[served_source(routes, f"{MIRROR}{sha(data)}/lc.zip", data)])
    eng.ctx.fetcher.routes.update(routes)


def music_extra(routes, steps=None):
    data = zip_bytes({"music/theme.ogg": b"theme"})
    return {"id": "lc-music", "kind": "extra", "title": "Music", "extends": ["lightcars"], "version": "1",
            "sources": [served_source(routes, f"{MIRROR}{sha(data)}/music.zip", data, steps=steps)]}


def setup_url(eng):
    return eng.ctx.entries["lightcars"]["sources"][0]["url"]


def assert_players_files_kept(folder):
    assert (folder / "cfg.ini").read_bytes() == b"volume=9 mine"
    assert (folder / "saves" / "slot1.sav").read_bytes() == b"save"


# ---- repair --------------------------------------------------------------------------------------------------


def test_repair_puts_back_a_quarantined_exe_and_keeps_the_players_files(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    (target / "lc.exe").unlink()  # Defender quarantined it
    before = record(eng)
    r = run(eng, "repair")
    assert r.action == "repair" and (target / "lc.exe").read_bytes() == b"exe v1"
    assert_players_files_kept(target)
    rec = record(eng)
    assert rec.method == "run" and rec.source == before.source and rec.previous is None
    assert rec.files["lc.exe"].sha256 == sha(b"exe v1") and "cfg.ini" not in rec.files
    assert len(eng.ctx.elevator.calls) == 2 and not setaside(eng).exists()


def test_repair_runs_the_recorded_installer_and_keeps_the_undo_point(tmp_path):
    eng, routes, target = updated_run_game(tmp_path)
    previous = record(eng).previous
    (target / "lc.exe").unlink()
    run(eng, "repair")
    assert (target / "lc.exe").read_bytes() == b"exe v2"  # v2's installer ran, not today's catalog's
    assert Path(eng.ctx.elevator.calls[-1][0]).name == "setup.exe"
    rec = record(eng)
    assert rec.previous == previous and rec.source.sha256 == sha(SETUP_V2)
    assert_players_files_kept(target)


def any_game(tmp_path, kind, updated, v2_steps=None):
    """An archive game (Pawprints) or one whose installer runs (Light Cars), installed, played and, with `updated`,
    updated once to v2 (with `v2_steps`): (engine, its id, its title, its folder)."""
    if kind == "archive":
        eng, folder = archive.install_v1(tmp_path)
        if updated:
            archive.publish(eng, archive.V2, steps=v2_steps)
            archive.run(eng, "update")
        return eng, "pawprints", "Pawprints", folder
    eng, routes, folder = updated_run_game(tmp_path, v2_steps) if updated else installed_run_game(tmp_path)
    return eng, "lightcars", "Light Cars", folder


@pytest.mark.parametrize("kind", ["archive", "installer-run"])
def test_repair_whose_download_changed_changes_nothing(tmp_path, kind):
    eng, game_id, title, folder = any_game(tmp_path, kind, updated=False)
    before = eng.ctx.records.load(game_id)
    eng.ctx.fetcher.routes[before.source.url] = b"MZ a different file"
    eng.ctx.fetcher.requests.clear()
    files = snapshot(folder)
    error = fails(eng.run, InstallRequest("repair", game_id, "Games"), quiet, never)
    assert error.message == messages.REPAIR_FILE_CHANGED.format(title=title)
    assert snapshot(folder) == files and eng.ctx.records.load(game_id) == before
    assert eng.ctx.fetcher.requests == [before.source.url]  # the recorded URL is the mirror's copy itself


@pytest.mark.parametrize("kind", ["archive", "installer-run"])
@pytest.mark.parametrize("served", [b"MZ not that file", "gone", None], ids=["changed", "gone", "unreachable"])
def test_undo_whose_previous_download_is_gone_changes_nothing(tmp_path, kind, served):
    """Changed or gone (HTTP 404) for good, Undo is no longer offered; unreachable (maybe only for now), it stays
    offered."""
    eng, game_id, title, folder = any_game(tmp_path, kind, updated=True)
    url = eng.ctx.records.load(game_id).previous["source"]["url"]
    if served is None:
        del eng.ctx.fetcher.routes[url]
    elif served == "gone":
        eng.ctx.fetcher.gone.add(url)
    else:
        eng.ctx.fetcher.routes[url] = served
    files = snapshot(folder)
    error = fails(eng.run, InstallRequest("undo", game_id, "Games"), quiet, never)
    expected = (messages.UNDO_NOT_POSSIBLE if served else messages.DOWNLOAD_UNREACHABLE).format(title=title)
    rec = eng.ctx.records.load(game_id)
    assert error.message == expected and rec.previous is not None
    assert rec.previous_unavailable == (messages.UNDO_UNAVAILABLE.format(title=title) if served else None)
    assert undo_item(rec).enabled is (served is None) and snapshot(folder) == files


def test_repair_runs_the_recorded_system_steps_under_the_update_rules(tmp_path):
    routes = {}
    e = run_game(routes, installer={"type": "inno"}, steps=[reg_step("Path", "{dir}", r"Software\LightCars")])
    target = tmp_path / "Games" / "Light Cars"
    eng, lib = setup_engine(tmp_path, [e], routes, on_start=writes(target, V1))
    install(eng)
    applied = record(eng).steps
    eng.ctx.entries["lightcars"] = dict(e, sources=[dict(e["sources"][0], steps=[])])  # today's catalog differs
    run(eng, "repair")
    assert record(eng).steps == applied  # kept with its undo data, not run again


# ---- undo ----------------------------------------------------------------------------------------------------


def test_undo_after_a_run_update_reinstalls_the_previous_installer(tmp_path):
    eng, routes, target = updated_run_game(tmp_path)
    previous = record(eng).previous
    assert previous["backups"] == []  # a run update backs nothing up
    r = run(eng, "undo")
    assert r.action == "undo" and (target / "lc.exe").read_bytes() == b"exe v1"
    assert_players_files_kept(target)
    rec = record(eng)
    assert rec.source.sha256 == sha(SETUP) and rec.source.version == "1.0" and rec.method == "run"
    assert rec.previous is None and rec.previous_unavailable is None
    assert rec.extras == {} and rec.steps == previous["steps"]
    assert rec.files["lc.exe"].sha256 == sha(b"exe v1")


@pytest.mark.parametrize("kind", ["archive", "installer-run"])
def test_undo_unregisters_the_newer_versions_component_while_its_file_is_there(tmp_path, monkeypatch, kind):
    only_v2 = {"archive": "sounds/b.wav", "installer-run": "v2.txt"}[kind]
    eng, game_id, title, folder = any_game(tmp_path, kind, updated=True,
                                           v2_steps=[{"action": "register", "path": only_v2}])
    elevator = eng.ctx.elevator
    place = elevator.on_start
    there = []

    def on_start(exe, args, elevated):
        if exe == "regsvr32.exe" and args.startswith("/u"):
            there.append((folder / only_v2).is_file())
        elif place is not None:
            place(exe, args, elevated)
    elevator.on_start = on_start
    settle(monkeypatch)
    eng.run(InstallRequest("undo", game_id, "Games"), quiet, never)
    assert there == [True] and not (folder / only_v2).exists()
    assert eng.ctx.records.load(game_id).steps == []


def test_undo_whose_unregister_and_installer_both_fail_keeps_the_step_recorded(tmp_path, monkeypatch):
    eng, routes, folder = updated_run_game(tmp_path, [{"action": "register", "path": "v2.txt"}])
    steps = record(eng).steps
    assert steps
    elevator = eng.ctx.elevator
    place = elevator.on_start

    def failing(exe, args, elevated):
        if exe == "regsvr32.exe":
            elevator.exit_codes[exe] = 1
        else:
            elevator.exit_codes[exe] = 3
            place(exe, args, elevated)
    elevator.on_start = failing
    settle(monkeypatch)
    fails(run, eng, "undo")
    assert record(eng).steps == steps and (folder / "v2.txt").exists()

    elevator.exit_codes.clear()
    elevator.on_start = lambda exe, args, elevated: (elevator.exit_codes.update({exe: 1}) if exe == "regsvr32.exe"
                                                     else place(exe, args, elevated))
    r = run(eng, "undo")  # the unregister still fails, but the Undo works: the note says so
    assert messages.UNDO_UNREGISTER_FAILED.format(name="v2.txt") in r.notes


def test_undo_of_an_archive_update_outside_the_libraries_puts_the_backups_back(tmp_path):
    eng, routes, folder = installed_outside(tmp_path)
    publish_archive(eng, routes, {"lc.exe": b"exe v2", "cfg.ini": b"volume=7"})
    r = run(eng, "update")
    assert r.backups == 1 and (folder / "cfg.ini").read_bytes() == b"volume=7"
    assert record(eng).method == "zip"
    r = run(eng, "undo")
    assert (folder / "lc.exe").read_bytes() == b"exe v1" and (folder / "sfx" / "a.wav").read_bytes() == b"a"
    assert_players_files_kept(folder)
    assert not (folder / "Game Chooser backup 2026-11-04").exists()
    rec = record(eng)
    assert rec.method == "run" and rec.source.sha256 == sha(SETUP) and rec.previous is None


def test_undo_whose_installer_failed_keeps_the_undo_point_and_can_be_retried(tmp_path):
    eng, routes, folder = installed_outside(tmp_path)
    publish_archive(eng, routes, {"lc.exe": b"exe v2", "cfg.ini": b"volume=7"})
    run(eng, "update")
    elevator = eng.ctx.elevator
    place = elevator.on_start

    def failing(exe, args, elevated):
        elevator.exit_codes[exe] = 5
        place(exe, args, elevated)
    elevator.on_start = failing
    error = fails(run, eng, "undo")
    assert error.message == "\n\n".join([  # the files the failed installer added were moved aside (M5)
        messages.INSTALLER_FAILED.format(title="Light Cars", code=5),
        messages.NEW_FILES_SET_ASIDE.format(title="Light Cars", path=eng.ctx.work_root / ".setaside" / "lightcars.new")])
    assert record(eng).previous is not None and record(eng).method == "zip"
    assert (folder / "Game Chooser backup 2026-11-04" / "cfg.ini").read_bytes() == b"volume=9 mine"
    elevator.exit_codes.clear()
    elevator.on_start = place
    run(eng, "undo")
    assert_players_files_kept(folder)
    assert record(eng).previous is None


def test_undo_whose_step_failed_after_the_installer_still_puts_the_backups_back_on_retry(tmp_path):
    eng, routes, folder = installed_outside(tmp_path)
    rec = record(eng)
    rec.source.steps = [reg_step("Path", "{dir}", r"Software\LightCars")]  # v1's installer came with a registry step
    eng.ctx.records.save(rec)
    publish_archive(eng, routes, {"lc.exe": b"exe v2", "cfg.ini": b"volume=7"})
    run(eng, "update")
    registry = eng.ctx.registry
    real_set = registry.set

    def broken(*args):
        raise OSError("registry unavailable")
    registry.set = broken
    error = fails(run, eng, "undo")
    assert error.message.startswith(messages.REGISTRY_WRITE_FAILED.format(key=r"HKCU\Software\LightCars"))
    rec = record(eng)
    assert rec.previous is not None and rec.method == "run"  # v1's files are in place and recorded
    assert (folder / "cfg.ini").read_bytes() == b"volume=5"
    registry.set = real_set
    run(eng, "undo")
    assert_players_files_kept(folder)
    assert record(eng).previous is None and [s["name"] for s in record(eng).steps] == ["Path"]


def test_undo_of_a_run_update_over_an_archive_install_rebuilds_the_archive(tmp_path):
    routes = {}
    data = zip_bytes(V1)
    e = run_game(routes)
    e["sources"] = [served_source(routes, f"{MIRROR}{sha(data)}/lc.zip", data)]
    target = tmp_path / "Games" / "Light Cars"
    eng, lib = setup_engine(tmp_path, [e], routes)
    install(eng)
    put(target, "cfg.ini", b"volume=9 mine")
    put(target, "saves/slot1.sav", b"save")
    publish_v2(eng, routes, setup=SETUP_V2)
    eng.ctx.elevator.on_start = versioned(target)
    run(eng, "update")
    assert record(eng).method == "run"
    r = run(eng, "undo")
    rec = record(eng)
    assert rec.method == "zip" and rec.source.sha256 == sha(data) and rec.previous is None
    assert (target / "lc.exe").read_bytes() == b"exe v1" and len(eng.ctx.elevator.calls) == 1
    assert (target / "saves" / "slot1.sav").read_bytes() == b"save"
    # The run update left the player's config unrecorded, so v1's copy goes in with theirs backed up.
    assert (target / "cfg.ini").read_bytes() == b"volume=5" and r.backups == 1
    assert (target / "Game Chooser backup 2026-11-04" / "cfg.ini").read_bytes() == b"volume=9 mine"


# ---- archive changes outside the libraries -------------------------------------------------------------------


def test_archive_update_outside_the_libraries_stages_under_the_work_folder(tmp_path):
    eng, routes, folder = installed_outside(tmp_path)
    publish_archive(eng, routes, {"lc.exe": b"exe v2", "cfg.ini": b"volume=7"})
    run(eng, "update")
    staged_into = eng.ctx.extractor.calls[-1][1]
    assert eng.ctx.work_root in staged_into.parents
    assert not any(p.name.startswith(".gamechooser") for p in folder.parent.iterdir())
    assert (folder / "lc.exe").read_bytes() == b"exe v2"
    assert (folder / "Game Chooser backup 2026-11-04" / "cfg.ini").read_bytes() == b"volume=9 mine"
    assert (folder / "saves" / "slot1.sav").read_bytes() == b"save"


def unwritable(monkeypatch, folder):
    real = jobs.tempfile.mkstemp

    def mkstemp(*args, **kw):
        if Path(kw.get("dir", "")) == folder:
            raise PermissionError(13, "Access is denied")
        return real(*args, **kw)
    monkeypatch.setattr(jobs.tempfile, "mkstemp", mkstemp)


def test_archive_update_of_an_unwritable_folder_outside_the_libraries_refuses_plainly(tmp_path, monkeypatch):
    eng, routes, folder = installed_outside(tmp_path)
    publish_archive(eng, routes, {"lc.exe": b"exe v2", "cfg.ini": b"volume=7"})
    unwritable(monkeypatch, folder)
    before, files = record(eng), snapshot(folder)
    assert fails(run, eng, "update").message == messages.FOLDER_NEEDS_ADMIN.format(title="Light Cars", path=folder)
    assert snapshot(folder) == files and record(eng) == before


# ---- extras --------------------------------------------------------------------------------------------------


def run_game_with_music(tmp_path, steps=None):
    routes = {}
    e = run_game(routes, installer={"type": "inno"}, extras=[{"id": "lc-music", "required": False}])
    target = tmp_path / "Games" / "Light Cars"
    eng, lib = setup_engine(tmp_path, [e, music_extra(routes, steps)], routes, on_start=writes(target, V1))
    install(eng)
    put(target, "cfg.ini", b"volume=9 mine")
    put(target, "saves/slot1.sav", b"save")
    return eng, target


def test_extras_on_a_run_record_are_tagged_and_removed_by_uninstall(tmp_path):
    eng, target = run_game_with_music(tmp_path, steps=[{"action": "copy", "from": "sfx/a.wav", "to": "music/a.wav"}])
    r = run(eng, "extras", extras=("lc-music",))
    assert r.action == "extras" and (target / "music" / "theme.ogg").read_bytes() == b"theme"
    assert (target / "music" / "a.wav").read_bytes() == b"a"  # its step copied an installed file
    assert_players_files_kept(target)
    rec = record(eng)
    assert set(rec.extras) == {"lc-music"} and rec.method == "run" and len(eng.ctx.elevator.calls) == 1
    assert rec.files["music/theme.ogg"].owner == "lc-music" and rec.files["music/a.wav"].owner == "lc-music"
    assert rec.files["lc.exe"].owner == "lightcars"
    run(eng, "uninstall")
    assert snapshot(target) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save"}


def test_extras_on_a_run_record_outside_the_libraries(tmp_path):
    eng, routes, folder = installed_outside(tmp_path)
    run(eng, "extras", extras=("lc-music",))
    assert eng.ctx.work_root in eng.ctx.extractor.calls[-1][1].parents
    assert (folder / "music" / "theme.ogg").read_bytes() == b"theme"
    assert record(eng).files["music/theme.ogg"].owner == "lc-music"
    assert_players_files_kept(folder)


def test_extra_with_its_own_installer_is_refused_on_a_run_record_as_on_any_game(tmp_path):
    routes = {}
    e = run_game(routes, installer={"type": "inno"}, extras=[{"id": "lc-patch", "required": False}])
    x = run_game(routes, id="lc-patch", title="Patch", setup=b"patch setup", installer={"type": "inno"},
                 kind="extra", extends=["lightcars"])
    target = tmp_path / "Games" / "Light Cars"
    eng, lib = setup_engine(tmp_path, [e, x], routes, on_start=writes(target, V1))
    install(eng)
    assert fails(run, eng, "extras", extras=("lc-patch",)).message == messages.EXTRA_NOT_SUPPORTED.format(extra="Patch")
    assert record(eng).extras == {} and len(eng.ctx.elevator.calls) == 1


def music_v2(eng, target, clobbers_music=False):
    """v2 of Light Cars, still listing the optional music extra; its installer may overwrite the music."""
    routes = dict(eng.ctx.fetcher.routes)
    publish_v2(eng, routes, setup=SETUP_V2)["extras"] = [{"id": "lc-music", "required": False}]
    install_version = versioned(target)

    def installer(exe, args, elevated):
        install_version(exe, args, elevated)
        if clobbers_music and Path(exe).read_bytes() == SETUP_V2:
            put(target, "music/theme.ogg", b"the installer's own theme")
    eng.ctx.elevator.on_start = installer


def test_run_update_reapplies_an_installed_optional_extra(tmp_path):
    eng, target = run_game_with_music(tmp_path)
    run(eng, "extras", extras=("lc-music",))
    music_v2(eng, target, clobbers_music=True)
    r = run(eng, "update")
    assert (target / "music" / "theme.ogg").read_bytes() == b"theme" and (target / "lc.exe").read_bytes() == b"exe v2"
    assert_players_files_kept(target)
    rec = record(eng)
    assert set(rec.extras) == {"lc-music"} and rec.files["music/theme.ogg"].owner == "lc-music"
    assert set(rec.previous["extras"]) == {"lc-music"} and r.game["installed_extras"] == ["lc-music"]


def test_repair_of_a_run_install_with_an_extra_puts_both_back(tmp_path):
    eng, target = run_game_with_music(tmp_path)
    run(eng, "extras", extras=("lc-music",))
    before = record(eng)
    (target / "lc.exe").unlink()
    (target / "music" / "theme.ogg").unlink()
    run(eng, "repair")
    assert (target / "lc.exe").read_bytes() == b"exe v1" and (target / "music" / "theme.ogg").read_bytes() == b"theme"
    assert_players_files_kept(target)
    rec = record(eng)
    assert rec.extras == before.extras and rec.source == before.source and rec.previous is None
    assert rec.files["music/theme.ogg"].owner == "lc-music"


def test_repair_when_an_extras_download_changed_fails_before_the_installer_starts(tmp_path):
    eng, target = run_game_with_music(tmp_path)
    run(eng, "extras", extras=("lc-music",))
    eng.ctx.fetcher.routes[eng.ctx.entries["lc-music"]["sources"][0]["url"]] = b"not the music"
    files = snapshot(target)
    assert fails(run, eng, "repair").message == messages.REPAIR_EXTRA_CHANGED.format(extra="Music", title="Light Cars")
    assert snapshot(target) == files and len(eng.ctx.elevator.calls) == 1


def test_undo_of_a_run_update_puts_back_the_previous_extras(tmp_path):
    eng, target = run_game_with_music(tmp_path)
    run(eng, "extras", extras=("lc-music",))
    music = record(eng).extras["lc-music"]
    music_v2(eng, target, clobbers_music=True)
    run(eng, "update")
    (target / "music" / "theme.ogg").unlink()
    run(eng, "undo")
    assert (target / "music" / "theme.ogg").read_bytes() == b"theme" and (target / "lc.exe").read_bytes() == b"exe v1"
    assert_players_files_kept(target)
    rec = record(eng)
    assert rec.extras == {"lc-music": music} and rec.previous is None and rec.source.sha256 == sha(SETUP)


def test_undo_after_extras_were_added_removes_them_with_the_newer_version(tmp_path, monkeypatch):
    eng, target = run_game_with_music(tmp_path)
    music_v2(eng, target)
    run(eng, "update")
    run(eng, "extras", extras=("lc-music",))
    assert record(eng).previous["extras"] == {}
    settle(monkeypatch)
    run(eng, "undo")
    rec = record(eng)
    assert rec.extras == {} and not (target / "music").exists()
    assert not any(f.owner == "lc-music" for f in rec.files.values())
    assert_players_files_kept(target)


def settle(monkeypatch):
    """Let the files written so far fall outside the next install window."""
    monkeypatch.setattr(run_installer, "CLOCK_SLACK", 0.0)
    time.sleep(0.1)


# ---- Undo reverses the newer installer (R26) -----------------------------------------------------------------


def test_undo_removes_the_newer_versions_unchanged_files_and_keeps_the_rest(tmp_path, monkeypatch):
    eng, routes, target = updated_run_game(tmp_path)
    put(target, "sfx/b.wav", b"b, edited by the player")

    def old_installer(exe, args, elevated):  # it skips sfx/a.wav, which it finds already there
        writes(target, {"lc.exe": b"exe v1", "cfg.ini": b"volume=5"})(exe, args, elevated)
    eng.ctx.elevator.on_start = old_installer
    settle(monkeypatch)
    run(eng, "undo")
    assert not (target / "v2.txt").exists()  # v2's, unchanged, and not v1's
    assert (target / "sfx" / "b.wav").read_bytes() == b"b, edited by the player"
    assert (target / "sfx" / "a.wav").read_bytes() == b"a"  # v1 had it too
    assert_players_files_kept(target)
    rec = record(eng)
    assert "v2.txt" not in rec.files and "sfx/b.wav" not in rec.files
    assert rec.files["sfx/a.wav"].sha256 == sha(b"a") and rec.files["lc.exe"].sha256 == sha(b"exe v1")


def test_undo_puts_the_previous_vendor_uninstaller_back(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, vendor=True)
    vendor = record(eng).vendor_uninstaller
    publish_archive(eng, routes, {"lc.exe": b"exe v2", "cfg.ini": b"volume=7"})
    run(eng, "update")
    assert record(eng).vendor_uninstaller is None and record(eng).previous["vendor_uninstaller"] == vendor
    run(eng, "undo")
    rec = record(eng)
    assert rec.vendor_uninstaller == vendor and (target / "unins000.exe").exists()


def test_a_work_folder_that_cant_be_created_is_named_as_such(tmp_path):
    eng, routes, folder = installed_outside(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")
    eng.ctx.work_root = blocker / "work"
    with pytest.raises(InstallFailed) as ei:
        with eng._staging_folder(eng.ctx.work_root, "lightcars"):
            pass
    assert ei.value.message == messages.WORK_FOLDER_NOT_CREATED.format(path=eng.ctx.work_root)


# ---- what stays refused --------------------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["repair", "undo", "extras"])
def test_an_untracked_run_record_still_refuses(tmp_path, action):
    eng, target = run_game_with_music(tmp_path)
    rec = record(eng)
    rec.tracked = False
    rec.previous = {**jobs._snapshot(rec), "backups": []}
    eng.ctx.records.save(rec)
    error = fails(run, eng, action, extras=("lc-music",) if action == "extras" else ())
    assert error.message == messages.INSTALL_NOT_CHANGEABLE.format(title="Light Cars")
    assert len(eng.ctx.elevator.calls) == 1


# ---- a phase that fails partway ------------------------------------------------------------------------------


def fails_once(monkeypatch, module, name):
    """Make module.name raise OSError the first time it is called."""
    real = getattr(module, name)
    calls = []

    def once(*args, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("the disk was busy")
        return real(*args, **kw)
    monkeypatch.setattr(module, name, once)


# Finding 1: a failed extras phase keeps the extras in the record, so a retry still applies them.

def test_a_failed_extras_phase_keeps_the_extras_for_a_retried_update(tmp_path, monkeypatch):
    eng, target = run_game_with_music(tmp_path)
    run(eng, "extras", extras=("lc-music",))
    music_v2(eng, target, clobbers_music=True)
    fails_once(monkeypatch, jobs, "apply_plan")
    assert fails(run, eng, "update").message == messages.FILES_NOT_CHANGED.format(title="Light Cars")
    assert set(record(eng).extras) == {"lc-music"}
    run(eng, "update")
    assert (target / "music" / "theme.ogg").read_bytes() == b"theme" and set(record(eng).extras) == {"lc-music"}
    assert_players_files_kept(target)


def test_a_failed_extras_phase_keeps_the_extras_for_a_retried_repair(tmp_path, monkeypatch):
    eng, target = run_game_with_music(tmp_path)
    run(eng, "extras", extras=("lc-music",))
    music = record(eng).extras["lc-music"]
    (target / "music" / "theme.ogg").unlink()
    fails_once(monkeypatch, jobs, "apply_plan")
    fails(run, eng, "repair")
    assert record(eng).extras == {"lc-music": music}
    run(eng, "repair")
    assert (target / "music" / "theme.ogg").read_bytes() == b"theme" and record(eng).extras == {"lc-music": music}
    assert_players_files_kept(target)


# Finding 2: a file that can't be copied for the extras' base fails in plain words.

def copy_to_staging_fails(monkeypatch):
    real = run_actions.shutil.copy2

    def copy2(src, dst, *args, **kw):
        if ".gamechooser-staging" in str(dst):
            raise OSError(28, "No space left on device")
        return real(src, dst, *args, **kw)
    monkeypatch.setattr(run_actions.shutil, "copy2", copy2)


def test_extras_whose_installed_files_cant_be_copied_fail_plainly(tmp_path, monkeypatch):
    eng, target = run_game_with_music(tmp_path)
    before, files = record(eng), snapshot(target)
    copy_to_staging_fails(monkeypatch)
    error = fails(run, eng, "extras", extras=("lc-music",))
    assert error.message == messages.EXTRAS_FILES_NOT_COPIED.format(title="Light Cars")
    assert snapshot(target) == files and record(eng) == before


def test_a_run_update_whose_installed_files_cant_be_copied_fails_plainly(tmp_path, monkeypatch):
    eng, target = run_game_with_music(tmp_path)
    run(eng, "extras", extras=("lc-music",))
    music_v2(eng, target)
    copy_to_staging_fails(monkeypatch)
    assert fails(run, eng, "update").message == messages.EXTRAS_FILES_NOT_COPIED.format(title="Light Cars")
    assert set(record(eng).extras) == {"lc-music"}


# R27: a fresh install into a folder that needs administrator permission skips its required extra, plainly.

def test_a_fresh_install_into_a_folder_needing_admin_records_its_required_extra_as_not_added(tmp_path, monkeypatch):
    routes = {}
    e, x = run_game_with_patch(routes)
    folder = tmp_path / "Program Files" / "Light Cars"
    keys = FakeUninstallKeys()
    place = writes(folder, {"lc.exe": b"exe"})

    def on_start(exe, args, elevated):
        place(exe, args, elevated)
        keys.add("Light Cars_is1", {"DisplayName": "Light Cars", "InstallLocation": str(folder)})
    eng, lib = setup_engine(tmp_path, [e, x], routes, on_start=on_start, uninstall_reader=keys)
    unwritable(monkeypatch, folder)
    r = install(eng)
    rec = record(eng)
    assert rec.tracked and rec.extras == {} and not (folder / "patch.dat").exists()
    assert (folder / "lc.exe").read_bytes() == b"exe" and r.game["installed_extras"] == []
    assert messages.EXTRAS_SKIPPED_NEEDS_ADMIN.format(title="Light Cars", path=folder, extras="Patch") in r.notes
    assert messages.EXTRAS_REQUIRED_MISSING.format(title="Light Cars", extras="Patch") in r.notes
    assert eng.ctx.registry.get("HKCU", r"Software\LightCars", "Patch")[0] is False


# R28: a path the older version recorded too is never removed by Undo, whatever it holds now.

def test_undo_keeps_a_path_the_older_version_also_had_and_records_what_it_holds(tmp_path, monkeypatch):
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes, setup=SETUP_V2)
    eng.ctx.elevator.on_start = writes(target, {"lc.exe": b"exe v2", "sfx/a.wav": b"a, v2"})
    run(eng, "update")
    eng.ctx.elevator.on_start = writes(target, {"lc.exe": b"exe v1", "cfg.ini": b"volume=5"})  # skips sfx/a.wav
    settle(monkeypatch)
    run(eng, "undo")
    assert (target / "sfx" / "a.wav").read_bytes() == b"a, v2"
    assert record(eng).files["sfx/a.wav"].sha256 == sha(b"a, v2")


# R29 (a): a fresh install is never an undo point, even when its extras made a backup.

def test_a_fresh_install_whose_extras_made_a_backup_has_no_undo_point(tmp_path, monkeypatch):
    routes = {}
    e, x = run_game_with_patch(routes)
    target = tmp_path / "Games" / "Light Cars"
    eng, lib = setup_engine(tmp_path, [e, x], routes, on_start=writes(target, {"lc.exe": b"exe"}))
    real = run_actions.copy_installed_files

    def game_wrote_a_file_meanwhile(rec, game_dir, *args):
        put(game_dir, "patch.dat", b"the game's own patch.dat")
        return real(rec, game_dir, *args)
    monkeypatch.setattr(run_actions, "copy_installed_files", game_wrote_a_file_meanwhile)
    r = install(eng)
    assert r.backups == 1 and record(eng).previous is None
    assert (target / "Game Chooser backup 2026-11-04" / "patch.dat").read_bytes() == b"the game's own patch.dat"


# R29 (b): a newer file Undo couldn't remove gets a plain note, and stays recorded.

def test_undo_says_so_when_a_newer_file_cant_be_removed(tmp_path, monkeypatch):
    eng, routes, target = updated_run_game(tmp_path)
    eng.ctx.elevator.on_start = writes(target, V1)
    settle(monkeypatch)
    real_unlink = Path.unlink

    def unlink(self, *args, **kw):
        if self.name == "v2.txt":
            raise PermissionError(13, "The file is in use")
        return real_unlink(self, *args, **kw)
    monkeypatch.setattr(Path, "unlink", unlink)
    r = run(eng, "undo")
    assert messages.UNDO_FILES_NOT_REMOVED.format(title="Light Cars") in r.notes
    assert (target / "v2.txt").exists() and "v2.txt" in record(eng).files


# R29 (c): the folder-needs-administrator refusal reads right for any change.

def test_extras_on_a_folder_needing_admin_refuse_with_words_that_fit_any_change(tmp_path, monkeypatch):
    eng, routes, folder = installed_outside(tmp_path)
    unwritable(monkeypatch, folder)
    error = fails(run, eng, "extras", extras=("lc-music",))
    assert error.message == messages.FOLDER_NEEDS_ADMIN.format(title="Light Cars", path=folder)
    assert "update" not in messages.FOLDER_NEEDS_ADMIN.lower()


def test_failed_repair_leaves_the_undo_point_as_it_was(tmp_path):
    eng, routes, target = updated_run_game(tmp_path)
    rec = record(eng)
    put(target, "Game Chooser backup 2026-11-04/v2.txt", b"the player's v2.txt")
    rec.previous = {**rec.previous, "backups": [{"original": "v2.txt", "backup": "Game Chooser backup 2026-11-04/v2.txt",
                                                 "installed_sha256": sha(b"new in v2")}]}
    eng.ctx.records.save(rec)
    before = record(eng).previous
    elevator, place = eng.ctx.elevator, eng.ctx.elevator.on_start

    def failing(exe, args, elevated):
        place(exe, args, elevated)
        put(target, "v2.txt", b"rewritten by the repair's installer")
        elevator.exit_codes[exe] = 5
    elevator.on_start = failing
    fails(run, eng, "repair")
    assert record(eng).previous == before
