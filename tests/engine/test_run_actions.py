"""Repair, Undo and Extras for tracked run-mode installs (Task 14b).

Every game folder here holds a player's save and an edited config, which must survive each action.
"""

from pathlib import Path

import pytest

from engine import jobs, messages
from engine.errors import InstallFailed
from engine.jobs import InstallRequest
from tests.engine.fakes import FakeUninstallKeys, served_source, zip_bytes
from tests.engine.test_run_installer import (MIRROR, SETUP, install, installed_run_game, never, publish_v2, put,
                                             quiet, run_game, setaside, setup_engine, sha, tree, writes)

V1 = {"lc.exe": b"exe v1", "cfg.ini": b"volume=5", "sfx/a.wav": b"a"}  # what installed_run_game's installer writes
SETUP_V2 = b"MZ setup v2"
V2 = {"lc.exe": b"exe v2", "cfg.ini": b"volume=7", "sfx/b.wav": b"b"}


def run(eng, action, **kw):
    return eng.run(InstallRequest(action, "lightcars", "Games", **kw), quiet, never)


def record(eng):
    return eng.ctx.records.load("lightcars")


def versioned(target):
    """A fake installer that writes v1's or v2's files, depending on which setup program was started."""
    def installer(exe, args, elevated):
        files = V2 if Path(exe).read_bytes() == SETUP_V2 else V1
        writes(target, files)(exe, args, elevated)
    return installer


def updated_run_game(tmp_path):
    """Light Cars installed and played, then updated by v2's installer."""
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes, setup=SETUP_V2)
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
    assert rec.library_name == "" and Path(rec.folder) == folder and rec.tracked
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
    return {"id": "lc-music", "kind": "extra", "title": "Music", "install_into": "lightcars", "version": "1",
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


def test_repair_when_the_installers_download_changed_offers_update_and_changes_nothing(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    eng.ctx.fetcher.routes[setup_url(eng)] = b"MZ a different setup"
    before, files = record(eng), tree(target)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "repair")
    assert ei.value.message == messages.REPAIR_FILE_CHANGED.format(title="Light Cars")
    assert tree(target) == files and record(eng) == before and len(eng.ctx.elevator.calls) == 1


def test_repair_runs_the_recorded_system_steps_under_the_update_rules(tmp_path):
    step = {"action": "registry", "hive": "HKCU", "key": r"Software\LightCars", "name": "Path", "type": "REG_SZ",
            "value": "{dir}"}
    routes = {}
    e = run_game(routes, installer={"type": "inno"}, steps=[step])
    target = tmp_path / "Games" / "Light Cars"
    eng, lib = setup_engine(tmp_path, [e], routes, on_start=writes(target, V1))
    install(eng)
    applied = record(eng).steps
    eng.ctx.entries["lightcars"] = dict(e, sources=[dict(e["sources"][0], steps=[])])  # today's catalog differs
    run(eng, "repair")
    assert record(eng).steps == applied  # kept with its undo data, not run again


# ---- undo ----------------------------------------------------------------------------------------------------


def test_run_update_is_an_undo_point(tmp_path):
    eng, routes, target = updated_run_game(tmp_path)
    rec = record(eng)
    assert rec.previous["source"]["sha256"] == sha(SETUP) and rec.previous["backups"] == []
    assert rec.source.sha256 == sha(SETUP_V2)


def test_undo_after_a_run_update_reinstalls_the_previous_installer(tmp_path):
    eng, routes, target = updated_run_game(tmp_path)
    previous = record(eng).previous
    r = run(eng, "undo")
    assert r.action == "undo" and (target / "lc.exe").read_bytes() == b"exe v1"
    assert_players_files_kept(target)
    rec = record(eng)
    assert rec.source.sha256 == sha(SETUP) and rec.source.version == "1.0" and rec.method == "run"
    assert rec.previous is None and rec.previous_unavailable is None
    assert rec.extras == {} and rec.steps == previous["steps"]
    assert rec.files["lc.exe"].sha256 == sha(b"exe v1")


def test_undo_when_the_previous_installer_changed_marks_it_unavailable(tmp_path):
    eng, routes, target = updated_run_game(tmp_path)
    eng.ctx.fetcher.routes[f"{MIRROR}{sha(SETUP)}/setup.exe"] = b"MZ not the old setup"
    files = tree(target)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "undo")
    expected = messages.UNDO_UNAVAILABLE.format(title="Light Cars")
    assert ei.value.message == expected
    rec = record(eng)
    assert rec.previous_unavailable == expected and rec.previous is not None
    assert tree(target) == files and len(eng.ctx.elevator.calls) == 2


def test_undo_when_the_previous_installer_cant_be_reached_stays_offered(tmp_path):
    eng, routes, target = updated_run_game(tmp_path)
    del eng.ctx.fetcher.routes[f"{MIRROR}{sha(SETUP)}/setup.exe"]
    with pytest.raises(InstallFailed) as ei:
        run(eng, "undo")
    assert ei.value.message == messages.DOWNLOAD_UNREACHABLE.format(title="Light Cars")
    rec = record(eng)
    assert rec.previous_unavailable is None and rec.previous is not None


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
    with pytest.raises(InstallFailed) as ei:
        run(eng, "undo")
    assert ei.value.message == messages.INSTALLER_FAILED.format(title="Light Cars", code=5)
    assert record(eng).previous is not None and record(eng).method == "zip"
    assert (folder / "Game Chooser backup 2026-11-04" / "cfg.ini").read_bytes() == b"volume=9 mine"
    elevator.exit_codes.clear()
    elevator.on_start = place
    run(eng, "undo")
    assert_players_files_kept(folder)
    assert record(eng).previous is None


def test_undo_whose_step_failed_after_the_installer_still_puts_the_backups_back_on_retry(tmp_path):
    eng, routes, folder = installed_outside(tmp_path)
    step = {"action": "registry", "hive": "HKCU", "key": r"Software\LightCars", "name": "Path", "type": "REG_SZ",
            "value": "{dir}"}
    rec = record(eng)
    rec.source.steps = [step]  # v1's installer came with a registry step
    eng.ctx.records.save(rec)
    publish_archive(eng, routes, {"lc.exe": b"exe v2", "cfg.ini": b"volume=7"})
    run(eng, "update")
    registry = eng.ctx.registry
    real_set = registry.set

    def broken(*args):
        raise OSError("registry unavailable")
    registry.set = broken
    with pytest.raises(InstallFailed) as ei:
        run(eng, "undo")
    assert ei.value.message.startswith(messages.REGISTRY_WRITE_FAILED.format(key=r"HKCU\Software\LightCars"))
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
    before, files = record(eng), tree(folder)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "update")
    assert ei.value.message == messages.FOLDER_NEEDS_ADMIN.format(title="Light Cars", path=folder)
    assert tree(folder) == files and record(eng) == before


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
    assert tree(target) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save"}


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
                 kind="extra", install_into="lightcars")
    target = tmp_path / "Games" / "Light Cars"
    eng, lib = setup_engine(tmp_path, [e, x], routes, on_start=writes(target, V1))
    install(eng)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "extras", extras=("lc-patch",))
    assert ei.value.message == messages.EXTRA_NOT_SUPPORTED.format(extra="Patch")
    assert record(eng).extras == {} and len(eng.ctx.elevator.calls) == 1


def test_repair_and_undo_of_a_run_record_with_extras_are_refused_plainly(tmp_path):
    eng, target = run_game_with_music(tmp_path)
    run(eng, "extras", extras=("lc-music",))
    rec = record(eng)
    rec.previous = {**jobs._snapshot(rec), "backups": []}
    eng.ctx.records.save(rec)
    files = tree(target)
    for action in ("repair", "undo"):
        with pytest.raises(InstallFailed) as ei:
            run(eng, action)
        assert ei.value.message == messages.RUN_INSTALLER_EXTRAS.format(title="Light Cars")
    assert tree(target) == files and len(eng.ctx.elevator.calls) == 1


def test_undo_of_a_run_update_after_extras_were_added_is_refused_plainly(tmp_path):
    eng, target = run_game_with_music(tmp_path)
    routes = dict(eng.ctx.fetcher.routes)
    publish_v2(eng, routes, setup=SETUP_V2)["extras"] = [{"id": "lc-music", "required": False}]
    eng.ctx.elevator.on_start = versioned(target)
    run(eng, "update")
    run(eng, "extras", extras=("lc-music",))
    assert record(eng).previous["extras"] == {}
    files, calls = tree(target), len(eng.ctx.elevator.calls)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "undo")
    assert ei.value.message == messages.RUN_INSTALLER_EXTRAS.format(title="Light Cars")
    assert tree(target) == files and len(eng.ctx.elevator.calls) == calls


# ---- what stays refused --------------------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["repair", "undo", "extras"])
def test_an_untracked_run_record_still_refuses(tmp_path, action):
    eng, target = run_game_with_music(tmp_path)
    rec = record(eng)
    rec.tracked = False
    rec.previous = {**jobs._snapshot(rec), "backups": []}
    eng.ctx.records.save(rec)
    with pytest.raises(InstallFailed) as ei:
        run(eng, action, extras=("lc-music",) if action == "extras" else ())
    assert ei.value.message == messages.INSTALL_NOT_CHANGEABLE.format(title="Light Cars")
    assert len(eng.ctx.elevator.calls) == 1
