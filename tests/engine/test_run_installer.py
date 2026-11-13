"""Installers that must run: silent and interactive runs, finding where the game went, tracking it by
the install window, and the set-aside rule for run-mode updates and vendor uninstalls."""

import os
import sys
import time
from pathlib import Path

import pytest

from engine import messages, run_installer
from engine.errors import Cancelled
from engine.jobs import InstallRequest
from engine.records import InstalledSource, InstallRecord
from engine.run_installer import (UNINSTALL_KEYS, files_created_during, folder_from_uninstall_entry,
                                  installer_command, new_uninstall_entries, silent_args_for, snapshot_folder,
                                  snapshot_uninstall_keys)
from tests.engine.fakes import (MIRROR, FakeElevator, FakeFetcher, FakeInstallerUI, FakeUninstallKeys, fails,
                                make_engine, never, put, quiet, served_source, sha, snapshot, watch_with, zip_bytes)

SETUP = b"MZ fake setup program"


def run_game(routes, installer=None, setup=SETUP, id="lightcars", title="Light Cars", steps=None, **fields):
    """A game whose only source is an installer that must run."""
    installer = {"type": "other", "mode": "run", **(installer or {})}
    source = served_source(routes, f"{MIRROR}{sha(setup)}/setup.exe", setup, method="installer",
                           steps=steps, installer=installer)
    return {"id": id, "kind": "game", "title": title, "developer": ["Draconis"], "genres": ["Racing"],
            "year": 2007, "version": "1.0", "sources": [source], **fields}


def setup_engine(tmp_path, entries, routes, on_start=None, ui=None, **ctx):
    elevator = ctx.pop("elevator", None) or FakeElevator(on_start=on_start)
    eng = make_engine(tmp_path, entries, fetcher=FakeFetcher(routes), elevator=elevator, installer_ui=ui, **ctx)
    return eng, tmp_path / "Games"


def install(eng, id="lightcars", cancel=never):
    return eng.run(InstallRequest("install", id, "Games"), quiet, cancel)


def writes(root: Path, files: dict[str, bytes], events: list | None = None):
    """A fake installer: when started, it writes `files` under `root`."""
    def on_start(exe, args, elevated):
        if events is not None:
            events.append("start")
        time.sleep(0.05)  # file times follow the ~16 ms system tick; keep them clearly inside the window
        for rel, data in files.items():
            put(root, rel, data)
        time.sleep(0.05)
    return on_start


# ---- commands --------------------------------------------------------------------------------------------


def test_default_and_custom_silent_args(tmp_path):
    assert installer_command(Path("s.exe"), {"type": "inno", "mode": "run"}, Path("C:/G/X")) == (
        "s.exe", '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="C:\\G\\X"')
    assert silent_args_for({"type": "other", "mode": "run"}) is None
    assert installer_command(Path("g.msi"), {"type": "msi", "mode": "run"}, Path("C:/G"))[0] == "msiexec"
    assert installer_command(Path("g.msi"), {"type": "msi", "mode": "run"}, Path("C:/G"))[1] == \
        '/i "g.msi" /qn TARGETDIR="C:\\G"'
    assert installer_command(Path("n.exe"), {"type": "nsis", "mode": "run"}, Path("C:/G")) == ("n.exe", "/S /D=C:\\G")
    assert silent_args_for({"type": "inno", "mode": "run", "silent_args": "/SP- /silent"}) == "/SP- /silent"
    assert installer_command(Path("w.exe"), {"type": "other", "mode": "run", "silent_args": '/s /p="{dir}"'},
                             Path("C:/G")) == ("w.exe", '/s /p="C:\\G"')
    assert installer_command(Path("i.exe"), {"type": "installshield", "mode": "run"}, Path("C:/G")) == ("i.exe", "")


def test_folder_from_uninstall_entry():
    assert folder_from_uninstall_entry({"InstallLocation": "C:\\Games\\X\\"}) == Path("C:/Games/X")
    assert folder_from_uninstall_entry({"DisplayIcon": '"C:\\Games\\X\\x.exe",0'}) == Path("C:/Games/X")
    assert folder_from_uninstall_entry({"UninstallString": '"C:\\Games\\X\\unins000.exe" /SILENT'}) == Path("C:/Games/X")
    assert folder_from_uninstall_entry({"UninstallString": "MsiExec.exe /X{1234}"}) is None
    assert folder_from_uninstall_entry({"UninstallString": "C:\\Program Files\\Dev X\\uninst.exe /S"}) == \
        Path("C:/Program Files/Dev X")
    assert folder_from_uninstall_entry({"InstallLocation": "", "DisplayIcon": "C:\\Games\\Y\\y.ico"}) == Path("C:/Games/Y")
    windir = os.environ.get("SystemRoot", "C:\\Windows")
    assert folder_from_uninstall_entry({"DisplayIcon": f"{windir}\\Installer\\{{1234}}\\icon.ico"}) is None
    assert folder_from_uninstall_entry({}) is None


def test_uninstall_key_snapshot_and_diff():
    keys = FakeUninstallKeys()
    keys.add("Old", {"DisplayName": "Old"})
    before = snapshot_uninstall_keys(read=keys)
    keys.add("New", {"DisplayName": "New", "InstallLocation": "C:\\New"}, hive="HKCU", view="")
    after = snapshot_uninstall_keys(read=keys)
    assert new_uninstall_entries(before, after) == [{"DisplayName": "New", "InstallLocation": "C:\\New"}]
    assert {(hive, view) for hive, _, view in UNINSTALL_KEYS} == {("HKLM", "64"), ("HKLM", "32"), ("HKCU", "")}
    assert all(key == r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall" for _, key, _ in UNINSTALL_KEYS)

    def broken(hive, key, view):
        raise OSError("no access")
    assert snapshot_uninstall_keys(read=broken) == {}


# ---- tracking by the install window ---------------------------------------------------------------------


def test_snapshot_folder_skips_engine_folders_and_tolerates_missing(tmp_path):
    assert snapshot_folder(tmp_path / "missing") == set()
    put(tmp_path, "A/Game.EXE", b"x")
    put(tmp_path, "Game Chooser backup 2026-11-04/save.dat", b"s")
    put(tmp_path, ".gamechooser-staging/x/y", b"y")
    assert snapshot_folder(tmp_path) == {"a/game.exe"}


def test_shared_vendor_folder_claims_only_window_files(tmp_path):
    dev = tmp_path / "Program Files" / "Dev"
    put(dev, "OldGame/old.exe", b"old")
    put(dev, "OldGame/data/a.dat", b"a")
    time.sleep(0.1)
    start = time.time()
    time.sleep(0.03)
    new_game = {"NewGame/new.exe": b"new", "NewGame/sounds/b.wav": b"b"}
    for rel, data in new_game.items():
        put(dev, rel, data)
    time.sleep(0.03)
    end = time.time()
    new_game_keys = [rel.lower() for rel in new_game]
    assert files_created_during(dev, start, end, None, slack=0) == sorted(new_game_keys)


def test_preserved_mtime_still_claimed_by_birthtime(tmp_path):
    start = time.time()
    time.sleep(0.03)
    old_looking = put(tmp_path, "Data/Music.ogg", b"m")
    os.utime(old_looking, (0, 0))  # installers often keep the original file dates
    time.sleep(0.03)
    end = time.time()
    assert old_looking.stat().st_mtime == 0
    assert files_created_during(tmp_path, start, end, None, slack=0) == ["data/music.ogg"]


def test_before_snapshot_claims_new_files_whatever_their_times(tmp_path):
    put(tmp_path, "old.txt", b"o")
    before = snapshot_folder(tmp_path)
    late = put(tmp_path, "new.dat", b"n")
    os.utime(late, (0, 0))
    now = time.time()
    found = files_created_during(tmp_path, now + 100, now + 200, before, slack=0)
    assert found == ["new.dat"]


# ---- installing ------------------------------------------------------------------------------------------


def test_silent_install_into_target_is_tracked(tmp_path):
    routes = {}
    e = run_game(routes, installer={"type": "inno"})
    target = tmp_path / "Games" / "Light Cars"
    eng, lib = setup_engine(tmp_path, [e], routes, on_start=writes(target, {"lc.exe": b"exe", "sfx/a.wav": b"a"}))
    r = install(eng)
    exe, args, elevated = eng.ctx.elevator.calls[0]
    assert Path(exe).name == "setup.exe" and args.endswith(f'/DIR="{target}"') and elevated is False
    rec = eng.ctx.records.load("lightcars")
    assert rec.method == "run" and rec.tracked and rec.library_id == "Games" and rec.folder == "Light Cars"
    assert set(rec.files) == {"lc.exe", "sfx/a.wav"} and rec.files["lc.exe"].sha256 == sha(b"exe")
    assert rec.launch_path == "lc.exe" and rec.vendor_uninstaller is None
    assert rec.source.install["installer"]["type"] == "inno"
    assert r.game["launch_path"] == "Light Cars/lc.exe" and r.game["library_id"] == "Games"
    assert r.notes == []
    assert not any(eng.ctx.work_root.rglob("setup.exe"))  # the downloaded installer is not kept


def test_interactive_announces_before_launch(tmp_path):
    routes = {}
    e = run_game(routes)  # type "other", no silent_args: the player drives the installer
    target = tmp_path / "Games" / "Light Cars"
    ui = FakeInstallerUI()
    eng, lib = setup_engine(tmp_path, [e], routes, on_start=writes(target, {"lc.exe": b"exe"}, ui.events), ui=ui)
    install(eng)
    assert ui.events == ["announce", "start", "wait"]
    assert ui.announced == [("Light Cars", target)]
    assert eng.ctx.elevator.calls[0][1] == ""
    assert eng.ctx.records.load("lightcars").tracked


def test_interactive_without_a_ui_fails_plainly_before_running(tmp_path):
    routes = {}
    eng, lib = setup_engine(tmp_path, [run_game(routes)], routes)
    assert fails(install, eng).message == messages.INSTALLER_NEEDS_PLAYER.format(title="Light Cars")
    assert eng.ctx.elevator.calls == [] and eng.ctx.records.load("lightcars") is None


def test_located_in_target_claims_only_new_files(tmp_path, monkeypatch):
    monkeypatch.setattr(run_installer, "CLOCK_SLACK", 0.0)
    routes = {}
    e = run_game(routes, installer={"type": "nsis"})
    target = tmp_path / "Games" / "Light Cars"
    eng, lib = setup_engine(tmp_path, [e], routes, on_start=writes(target, {"lc.exe": b"exe", "data/x.dat": b"x"}))
    put(target, "readme.txt", b"the player's notes")
    time.sleep(0.05)
    file = tmp_path / "setup.exe"
    file.write_bytes(SETUP)
    item = run_installer.RunItem("lightcars", "game", "Light Cars", file, {"type": "nsis", "mode": "run"}, [],
                                 InstalledSource("u", sha(SETUP), len(SETUP), None, None, 0, "1.0"), None)
    rec, notes = run_installer.install(eng.ctx, item, target, "Games", lib, quiet, never)
    assert set(rec.files) == {"lc.exe", "data/x.dat"} and rec.tracked
    assert (target / "readme.txt").read_bytes() == b"the player's notes"


def test_registry_diff_locates_and_records_vendor_uninstaller(tmp_path):
    routes = {}
    e = run_game(routes, installer={"type": "inno"})
    program = tmp_path / "Program Files" / "Draconis" / "Light Cars"
    keys = FakeUninstallKeys()
    keys.add("Someone Else", {"DisplayName": "Other", "InstallLocation": str(tmp_path / "Other")})
    place = writes(program, {"lightcars.exe": b"exe", "unins000.exe": b"u"})

    def on_start(exe, args, elevated):  # the installer ignores /DIR and registers itself
        place(exe, args, elevated)
        keys.add("Light Cars_is1", {"DisplayName": "Light Cars", "InstallLocation": str(program) + "\\",
                                     "UninstallString": f'"{program}\\unins000.exe"',
                                     "QuietUninstallString": f'"{program}\\unins000.exe" /SILENT'})
    eng, lib = setup_engine(tmp_path, [e], routes, on_start=on_start, uninstall_reader=keys)
    r = install(eng)
    rec = eng.ctx.records.load("lightcars")
    assert rec.vendor_uninstaller == f'"{program}\\unins000.exe" /SILENT'
    assert Path(rec.folder) == program and rec.tracked and set(rec.files) == {"lightcars.exe", "unins000.exe"}
    assert not (lib / "Light Cars").exists()
    assert r.game["launch_path"] == str(program / "lightcars.exe")


def test_out_of_library_install_uses_absolute_paths(tmp_path):
    routes = {}
    e = run_game(routes, installer={"type": "inno"})
    elsewhere = tmp_path / "Old Games" / "Light Cars"
    keys = FakeUninstallKeys()
    place = writes(elsewhere, {"bin/lc.exe": b"exe"})

    def on_start(exe, args, elevated):
        place(exe, args, elevated)
        keys.add("LC", {"DisplayIcon": f'"{elsewhere}\\bin\\lc.exe",0', "UninstallString": "MsiExec.exe /X{1}"})
    eng, lib = setup_engine(tmp_path, [e], routes, on_start=on_start, uninstall_reader=keys)
    r = install(eng)
    rec = eng.ctx.records.load("lightcars")
    assert rec.library_id == "" and Path(rec.folder) == elsewhere / "bin" and Path(rec.folder).is_absolute()
    assert rec.launch_path == "lc.exe" and rec.vendor_uninstaller == "MsiExec.exe /X{1}"
    assert r.game["library_id"] == "" and Path(r.game["launch_path"]).is_absolute() and "manual" not in r.game
    assert Path(r.game["launch_path"]) == elsewhere / "bin" / "lc.exe"


def test_registry_folder_in_a_library_is_stored_relative(tmp_path):
    routes = {}
    e = run_game(routes, installer={"type": "inno"})
    other = tmp_path / "Games" / "LightCars2007"
    keys = FakeUninstallKeys()
    place = writes(other, {"lc.exe": b"exe"})

    def on_start(exe, args, elevated):
        place(exe, args, elevated)
        keys.add("LC", {"InstallLocation": str(other)})
    eng, lib = setup_engine(tmp_path, [e], routes, on_start=on_start, uninstall_reader=keys)
    r = install(eng)
    rec = eng.ctx.records.load("lightcars")
    assert rec.library_id == "Games" and rec.folder == "LightCars2007"
    assert r.game["launch_path"] == "LightCars2007/lc.exe" and r.game["library_id"] == "Games"


def test_nothing_found_asks_player_then_untracked(tmp_path, monkeypatch):
    monkeypatch.setattr(run_installer, "CLOCK_SLACK", 0.0)
    routes = {}
    e = run_game(routes)
    chosen = tmp_path / "Somewhere" / "LC"
    put(chosen, "lc.exe", b"exe")  # an old copy: nothing in it was written during the install
    time.sleep(0.05)
    ui = FakeInstallerUI(locate=chosen)
    eng, lib = setup_engine(tmp_path, [e], routes, ui=ui)
    r = install(eng)
    assert ui.events == ["announce", "wait", "locate"]
    rec = eng.ctx.records.load("lightcars")
    assert rec.tracked is False and rec.files == {} and Path(rec.folder) == chosen and rec.launch_path == "lc.exe"
    assert r.notes == [messages.UNTRACKED_NOTE.format(title="Light Cars")]
    assert (chosen / "lc.exe").read_bytes() == b"exe"


def test_nothing_found_and_player_gives_up_fails_plainly(tmp_path):
    routes = {}
    ui = FakeInstallerUI(locate=None)
    eng, lib = setup_engine(tmp_path, [run_game(routes)], routes, ui=ui)
    assert fails(install, eng).message == messages.INSTALL_FOLDER_NOT_FOUND.format(title="Light Cars")
    assert eng.ctx.records.load("lightcars") is None and not (lib / "Light Cars").exists()


def test_player_located_drive_root_is_not_accepted(tmp_path):
    routes = {}
    ui = FakeInstallerUI(locate=Path(tmp_path.anchor))
    eng, lib = setup_engine(tmp_path, [run_game(routes)], routes, ui=ui)
    assert fails(install, eng).message == messages.INSTALL_FOLDER_NOT_FOUND.format(title="Light Cars")


def test_silent_installer_error_code_fails_and_removes_its_files(tmp_path):
    routes = {}
    e = run_game(routes, installer={"type": "inno"})
    target = tmp_path / "Games" / "Light Cars"
    elevator = FakeElevator()
    place = writes(target, {"lc.exe": b"half"})

    def on_start(exe, args, elevated):  # the downloaded file's path is only known once it runs
        elevator.exit_codes[exe] = 5
        place(exe, args, elevated)
    elevator.on_start = on_start
    eng, lib = setup_engine(tmp_path, [e], routes, elevator=elevator)
    assert fails(install, eng).message == messages.INSTALLER_FAILED.format(title="Light Cars", code=5)
    assert not target.exists() and eng.ctx.records.load("lightcars") is None
    assert elevator.shown == [True]  # an installer keeps its window: the player may need to see it (I2)


def test_interactive_error_code_after_done_is_a_note(tmp_path):
    routes = {}
    target = tmp_path / "Games" / "Light Cars"
    elevator = FakeElevator()
    place = writes(target, {"lc.exe": b"exe"})

    def on_start(exe, args, elevated):
        elevator.exit_codes[exe] = 2
        place(exe, args, elevated)
    elevator.on_start = on_start
    eng, lib = setup_engine(tmp_path, [run_game(routes)], routes, elevator=elevator, ui=FakeInstallerUI())
    r = install(eng)
    assert r.notes == [messages.INSTALLER_CODE_NOTE.format(title="Light Cars", code=2)]
    assert eng.ctx.records.load("lightcars").tracked


def test_cancel_while_waiting_removes_only_new_target_files(tmp_path):
    routes = {}
    e = run_game(routes, installer={"type": "inno"})
    target = tmp_path / "Games" / "Light Cars"
    started = []
    place = writes(target, {"lc.exe": b"exe", "data/a.dat": b"a"})

    def on_start(exe, args, elevated):
        started.append(exe)
        elevator.hang.add(exe)
        place(exe, args, elevated)
    elevator = FakeElevator(on_start=on_start)
    eng, lib = setup_engine(tmp_path, [e], routes, elevator=elevator)
    watches = []
    eng.ctx.process_watch = lambda p, elevated: watches.append(run_watch(p, elevated)) or watches[-1]
    with pytest.raises(Cancelled):
        install(eng, cancel=lambda: bool(started))
    assert watches[0].terminated and watches[0].closed
    assert not target.exists() and eng.ctx.records.load("lightcars") is None


def run_watch(process, elevated):
    from tests.engine.fakes import FakeWatch
    return FakeWatch(process, elevated)


def test_cancel_by_the_player_in_the_installer_ui(tmp_path):
    routes = {}
    target = tmp_path / "Games" / "Light Cars"
    elevator = FakeElevator()

    def on_start(exe, args, elevated):
        elevator.hang.add(exe)
        writes(target, {"lc.exe": b"exe"})(exe, args, elevated)
    elevator.on_start = on_start
    eng, lib = setup_engine(tmp_path, [run_game(routes)], routes, elevator=elevator, ui=FakeInstallerUI(done=False))
    with pytest.raises(Cancelled):
        install(eng)
    assert not target.exists() and eng.ctx.records.load("lightcars") is None


def test_elevated_installer_requested_when_flagged(tmp_path):
    routes = {}
    e = run_game(routes, installer={"type": "inno", "elevation": True})
    target = tmp_path / "Games" / "Light Cars"
    watches = []
    eng, lib = setup_engine(tmp_path, [e], routes, on_start=writes(target, {"lc.exe": b"exe"}))
    eng.ctx.process_watch = lambda p, elevated: watches.append(run_watch(p, elevated)) or watches[-1]
    install(eng)
    assert eng.ctx.elevator.calls[0][2] is True and watches[0].elevated is True


def test_declined_elevation_fails_plainly(tmp_path):
    routes = {}
    e = run_game(routes, installer={"type": "inno", "elevation": True})
    eng, lib = setup_engine(tmp_path, [e], routes, elevator=FakeElevator(decline=True))
    assert fails(install, eng).message == messages.ELEVATION_DECLINED.format(what="Light Cars")


def test_system_steps_of_a_run_source_are_applied_and_recorded(tmp_path):
    routes = {}
    step = {"action": "registry", "hive": "HKCU", "key": r"Software\LightCars", "name": "Path", "type": "REG_SZ",
            "value": "{dir}"}
    e = run_game(routes, installer={"type": "inno"}, steps=[step])
    target = tmp_path / "Games" / "Light Cars"
    eng, lib = setup_engine(tmp_path, [e], routes, on_start=writes(target, {"lc.exe": b"exe"}))
    install(eng)
    rec = eng.ctx.records.load("lightcars")
    assert [s["action"] for s in rec.steps] == ["registry"] and rec.steps[0]["undo"]["existed"] is False
    assert eng.ctx.registry.get("HKCU", r"Software\LightCars", "Path")[0] is True


# ---- routing ---------------------------------------------------------------------------------------------


def test_routes_on_the_source_that_was_fetched(tmp_path):
    routes = {}
    zip_data = zip_bytes({"lc.exe": b"zip exe"})
    zip_source = served_source(routes, f"{MIRROR}{sha(zip_data)}/lc.zip", zip_data)
    e = run_game(routes, installer={"type": "inno"})
    e["sources"] = [zip_source, *e["sources"]]  # a run-mode sibling doesn't stop the zip install
    eng, lib = setup_engine(tmp_path, [e], routes)
    install(eng)
    rec = eng.ctx.records.load("lightcars")
    assert rec.method == "zip" and (lib / "Light Cars" / "lc.exe").read_bytes() == b"zip exe"
    assert eng.ctx.elevator.calls == []


def test_web_game_is_not_installed_by_the_engine(tmp_path):
    e = {"id": "webgame", "kind": "game", "title": "Web Game", "sources": [
        {"label": "Site", "url": "https://example.com/play", "platform": "web", "install": {"method": "url"}}]}
    eng, lib = setup_engine(tmp_path, [e], {})
    assert fails(install, eng, "webgame").message == messages.WEB_GAME_NOT_INSTALLED.format(title="Web Game")
    assert eng.ctx.fetcher.requests == []


def run_game_with_patch(routes, patch_route=True):
    """Light Cars (run-mode) with a required archive extra that patches its exe and sets a registry value."""
    e = run_game(routes, installer={"type": "inno"}, extras=[{"id": "lc-patch", "required": True}])
    data = zip_bytes({"patch.dat": b"p", "lc.exe": b"exe patched"})
    step = {"action": "registry", "hive": "HKCU", "key": r"Software\LightCars", "name": "Patch", "type": "REG_SZ",
            "value": "on"}
    source = served_source(routes, f"{MIRROR}{sha(data)}/p.zip", data, steps=[step])
    if not patch_route:
        del routes[source["url"]]
    x = {"id": "lc-patch", "kind": "extra", "title": "Patch", "extends": ["lightcars"], "version": "1",
         "sources": [source]}
    return e, x


def test_run_mode_game_gets_its_required_archive_extra_after_the_installer(tmp_path):
    routes = {}
    e, x = run_game_with_patch(routes)
    target = tmp_path / "Games" / "Light Cars"
    eng, lib = setup_engine(tmp_path, [e, x], routes, on_start=writes(target, {"lc.exe": b"exe", "data.dat": b"d"}))
    r = install(eng)
    assert (target / "lc.exe").read_bytes() == b"exe patched" and (target / "patch.dat").read_bytes() == b"p"
    rec = eng.ctx.records.load("lightcars")
    assert set(rec.extras) == {"lc-patch"} and rec.method == "run" and rec.tracked
    assert rec.files["patch.dat"].owner == "lc-patch" and rec.files["lc.exe"].owner == "lc-patch"
    assert rec.files["data.dat"].owner == "lightcars"
    assert [s["name"] for s in rec.steps] == ["Patch"]
    assert eng.ctx.registry.get("HKCU", r"Software\LightCars", "Patch") == (True, "on", "REG_SZ")
    assert r.game["installed_extras"] == ["lc-patch"]
    eng.run(InstallRequest("uninstall", "lightcars"), quiet, never)
    assert not target.exists() and eng.ctx.registry.get("HKCU", r"Software\LightCars", "Patch")[0] is False


def test_a_bad_extra_download_fails_before_the_installer_starts(tmp_path):
    routes = {}
    e, x = run_game_with_patch(routes, patch_route=False)
    target = tmp_path / "Games" / "Light Cars"
    eng, lib = setup_engine(tmp_path, [e, x], routes, on_start=writes(target, {"lc.exe": b"exe"}))
    assert fails(install, eng).message.startswith(messages.NO_SOURCE.format(title="Patch", reasons="").strip())
    assert eng.ctx.elevator.calls == [] and not target.exists() and eng.ctx.records.load("lightcars") is None


def test_an_untracked_run_install_skips_its_extras_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(run_installer, "CLOCK_SLACK", 0.0)
    routes = {}
    e, x = run_game_with_patch(routes)
    e["sources"][0]["install"]["installer"]["type"] = "other"  # interactive, so the player can locate it
    chosen = tmp_path / "Somewhere" / "LC"
    put(chosen, "lc.exe", b"exe")
    time.sleep(0.05)
    eng, lib = setup_engine(tmp_path, [e, x], routes, ui=FakeInstallerUI(locate=chosen))
    r = install(eng)
    rec = eng.ctx.records.load("lightcars")
    assert rec.tracked is False and rec.extras == {} and not (chosen / "patch.dat").exists()
    assert messages.EXTRAS_SKIPPED_UNTRACKED.format(title="Light Cars", extras="Patch") in r.notes
    assert messages.EXTRAS_REQUIRED_MISSING.format(title="Light Cars", extras="Patch") in r.notes


def test_run_mode_extra_is_refused_plainly(tmp_path):
    routes = {}
    data = zip_bytes({"lc.exe": b"exe"})
    e = {"id": "lightcars", "kind": "game", "title": "Light Cars", "version": "1",
         "sources": [served_source(routes, f"{MIRROR}{sha(data)}/lc.zip", data)],
         "extras": [{"id": "lc-patch", "required": True}]}
    x = run_game(routes, id="lc-patch", title="Patch", setup=b"patch setup", installer={"type": "inno"},
                 kind="extra", extends=["lightcars"])
    eng, lib = setup_engine(tmp_path, [e, x], routes)
    assert fails(install, eng).message == messages.EXTRA_NOT_SUPPORTED.format(extra="Patch")
    assert not (lib / "Light Cars").exists()


# ---- run-mode update -------------------------------------------------------------------------------------


def installed_run_game(tmp_path, files=None, vendor=None, elevation=False):
    """Light Cars installed by a run-mode installer and then played."""
    routes = {}
    e = run_game(routes, installer={"type": "inno", "elevation": elevation})
    target = tmp_path / "Games" / "Light Cars"
    files = files or {"lc.exe": b"exe v1", "cfg.ini": b"volume=5", "sfx/a.wav": b"a"}
    if vendor is True:  # the installer also wrote its uninstaller and registered it
        files = {**files, "unins000.exe": b"uninstaller"}
        vendor = f'"{target / "unins000.exe"}" /SILENT'
    eng, lib = setup_engine(tmp_path, [e], routes, on_start=writes(target, files))
    install(eng)
    rec = eng.ctx.records.load("lightcars")
    if vendor:
        rec.vendor_uninstaller = vendor
        eng.ctx.records.save(rec)
    put(target, "cfg.ini", b"volume=9 mine")
    put(target, "saves/slot1.sav", b"save")
    return eng, routes, target


def publish_v2(eng, routes, setup=b"MZ setup v2", steps=None, **installer):
    e = run_game(routes, setup=setup, installer={"type": "inno", **installer}, steps=steps)
    e["version"] = "2.0"
    eng.ctx.entries["lightcars"] = e
    eng.ctx.fetcher.routes.update(routes)
    return e


def test_run_mode_update_restores_player_files_keeps_new(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes)
    v2 = {"lc.exe": b"exe v2", "cfg.ini": b"volume=7", "sfx/b.wav": b"b"}

    def installer(exe, args, elevated):
        time.sleep(0.02)
        for rel, data in v2.items():
            put(target, rel, data)
        (target / "saves" / "slot1.sav").unlink()  # a careless installer
        time.sleep(0.02)
    eng.ctx.elevator.on_start = installer
    r = eng.run(InstallRequest("update", "lightcars"), quiet, never)
    assert (target / "cfg.ini").read_bytes() == b"volume=9 mine"
    assert (target / "cfg.ini.new").read_bytes() == b"volume=7"
    assert (target / "saves" / "slot1.sav").read_bytes() == b"save"
    assert (target / "lc.exe").read_bytes() == b"exe v2"
    rec = eng.ctx.records.load("lightcars")
    assert set(rec.files) == {"lc.exe", "cfg.ini.new", "sfx/a.wav", "sfx/b.wav"}
    assert rec.files["lc.exe"].sha256 == sha(b"exe v2") and rec.source.version == "2.0" and rec.method == "run"
    assert r.action == "update" and r.game["launch_path"] == "Light Cars/lc.exe"
    assert not (eng.ctx.work_root / ".setaside" / "lightcars").exists()


def test_run_mode_update_records_shipped_file_rewritten_with_old_dates(tmp_path, monkeypatch):
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes)
    monkeypatch.setattr(run_installer, "CLOCK_SLACK", 0.0)
    time.sleep(0.05)

    def installer(exe, args, elevated):
        wav = target / "sfx" / "a.wav"
        with open(wav, "r+b") as f:  # rewritten in place: its creation time stays the old one
            f.write(b"A")
        os.utime(wav, (0, 0))
    eng.ctx.elevator.on_start = installer
    eng.run(InstallRequest("update", "lightcars"), quiet, never)
    rec = eng.ctx.records.load("lightcars")
    assert rec.files["sfx/a.wav"].sha256 == sha(b"A") and rec.files["lc.exe"].sha256 == sha(b"exe v1")
    assert "cfg.ini" not in rec.files and (target / "cfg.ini").read_bytes() == b"volume=9 mine"


def test_run_mode_update_refuses_while_earlier_set_aside_files_remain(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes)
    left = put(eng.ctx.work_root / ".setaside" / "lightcars", "cfg.ini", b"older copy")
    error = fails(eng.run, InstallRequest("update", "lightcars"), quiet, never)
    assert error.message == messages.SETASIDE_LEFT_OVER.format(title="Light Cars", path=left.parent)
    assert left.read_bytes() == b"older copy" and len(eng.ctx.elevator.calls) == 1


def test_cancelled_update_restores_player_files_and_removes_only_new_ones(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes)
    before = snapshot(target)
    started = []

    def installer(exe, args, elevated):
        started.append(exe)
        eng.ctx.elevator.hang.add(exe)
        put(target, "cfg.ini", b"installer's half-written ini")
        put(target, "new.dll", b"new")
        (target / "saves" / "slot1.sav").unlink()
    eng.ctx.elevator.on_start = installer
    error = fails(eng.run, InstallRequest("update", "lightcars"), quiet, lambda: bool(started))
    after = snapshot(target)
    assert after.pop("cfg.ini.new") == b"installer's half-written ini"
    assert after == before
    # M5: a file that appeared during the window isn't deleted (it may be the player's, written by the running
    # game); it is moved beside the set-aside copies, and the player is told where.
    aside = eng.ctx.work_root / ".setaside" / "lightcars.new"
    assert snapshot(aside) == {"new.dll": b"new"}
    assert not (eng.ctx.work_root / ".setaside" / "lightcars").exists()  # the player's copies: back, discarded
    assert error.message == "\n\n".join([messages.INSTALLER_CANCELLED.format(title="Light Cars"),
                                             messages.NEW_FILES_SET_ASIDE.format(title="Light Cars", path=aside)])
    assert eng.ctx.records.load("lightcars").source.version == "1.0"


def test_failed_update_sets_new_files_aside_and_a_retry_neither_clears_nor_refuses_them(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes)

    def failing(exe, args, elevated):
        eng.ctx.elevator.exit_codes[exe] = 7
        put(target, "saves" / Path("slot2.sav"), b"written by the game during the window")
    eng.ctx.elevator.on_start = failing
    error = fails(eng.run, InstallRequest("update", "lightcars"), quiet, never)
    aside = eng.ctx.work_root / ".setaside" / "lightcars.new"
    assert error.message == "\n\n".join([messages.INSTALLER_FAILED.format(title="Light Cars", code=7),
                                             messages.NEW_FILES_SET_ASIDE.format(title="Light Cars", path=aside)])
    assert (aside / "saves" / "slot2.sav").read_bytes() == b"written by the game during the window"
    assert not (target / "saves" / "slot2.sav").exists() and (target / "saves" / "slot1.sav").read_bytes() == b"save"
    # A second failure keeps the earlier file too; a retry that works leaves the folder for the player.
    eng.ctx.elevator.exit_codes.clear()
    fails(eng.run, InstallRequest("update", "lightcars"), quiet, never)
    assert snapshot(aside) == {"saves/slot2.sav": b"written by the game during the window",
                           "saves/slot2 (2).sav": b"written by the game during the window"}
    eng.ctx.elevator.exit_codes.clear()
    eng.ctx.elevator.on_start = writes(target, {"lc.exe": b"exe v2"})
    r = eng.run(InstallRequest("update", "lightcars"), quiet, never)
    assert r.game["launch_path"] == "Light Cars/lc.exe" and len(snapshot(aside)) == 2


def test_failed_restore_keeps_set_aside_copies(tmp_path, monkeypatch):
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes)

    def installer(exe, args, elevated):
        put(target, "cfg.ini", b"volume=7")
    eng.ctx.elevator.on_start = installer
    real_copy = run_installer.shutil.copy2

    def copy2(src, dst, **kw):
        if "setaside" in str(src):
            raise OSError("disk full")
        return real_copy(src, dst, **kw)
    monkeypatch.setattr(run_installer.shutil, "copy2", copy2)
    r = eng.run(InstallRequest("update", "lightcars"), quiet, never)
    kept = eng.ctx.work_root / ".setaside" / "lightcars"
    assert (kept / "cfg.ini").read_bytes() == b"volume=9 mine"
    assert messages.SETASIDE_KEPT.format(title="Light Cars", path=kept) in r.notes


def test_run_mode_install_updated_by_an_archive_uses_the_three_checksum_rule(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    data = zip_bytes({"lc.exe": b"exe v2", "cfg.ini": b"volume=7"})
    e = dict(eng.ctx.entries["lightcars"], version="2.0",
             sources=[served_source(routes, f"{MIRROR}{sha(data)}/lc.zip", data)])
    eng.ctx.entries["lightcars"] = e
    eng.ctx.fetcher.routes.update(routes)
    r = eng.run(InstallRequest("update", "lightcars"), quiet, never)
    assert (target / "lc.exe").read_bytes() == b"exe v2" and not (target / "sfx" / "a.wav").exists()
    assert (target / "cfg.ini").read_bytes() == b"volume=7" and r.backups == 1
    assert (target / "Game Chooser backup 2026-11-04" / "cfg.ini").read_bytes() == b"volume=9 mine"
    assert (target / "saves" / "slot1.sav").read_bytes() == b"save"
    assert eng.ctx.records.load("lightcars").method == "zip" and len(eng.ctx.elevator.calls) == 1


def test_untracked_run_install_cannot_be_updated(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    rec = eng.ctx.records.load("lightcars")
    rec.tracked = False
    eng.ctx.records.save(rec)
    publish_v2(eng, routes)
    error = fails(eng.run, InstallRequest("update", "lightcars"), quiet, never)
    assert error.message == messages.INSTALL_NOT_CHANGEABLE.format(title="Light Cars")


# ---- uninstall -------------------------------------------------------------------------------------------


def test_own_record_uninstall_of_a_run_install(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    r = eng.run(InstallRequest("uninstall", "lightcars"), quiet, never)
    assert snapshot(target) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save"}
    assert r.kept_player_files == 2 and len(eng.ctx.elevator.calls) == 1


def test_vendor_uninstall_sets_aside_player_files(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, vendor=True, elevation=True)

    def uninstaller(exe, args, elevated):
        import shutil
        shutil.rmtree(target)  # takes the player's saves with it
    eng.ctx.elevator.on_start = uninstaller
    r = eng.run(InstallRequest("uninstall", "lightcars"), quiet, never)
    assert eng.ctx.elevator.calls[-1] == (str(target / "unins000.exe"), "/SILENT", True)
    assert snapshot(target) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save"}
    assert r.kept_player_files == 2
    rec = eng.ctx.records.load("lightcars")
    assert rec.uninstalled and rec.files == {}
    assert not (eng.ctx.work_root / ".setaside" / "lightcars").exists()


def test_vendor_uninstall_deletes_leftovers_and_recycles_player_files_when_asked(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, vendor=True)

    def uninstaller(exe, args, elevated):
        pass  # leaves its own unins000.exe behind
    eng.ctx.elevator.on_start = uninstaller
    eng.run(InstallRequest("uninstall", "lightcars", delete_player_data=True), quiet, never)
    assert not target.exists() and eng.ctx.records.load("lightcars") is None
    recycled = sorted(p.relative_to(target).as_posix() for p in eng.ctx.recycle.calls[0])
    assert recycled == ["cfg.ini", "saves/slot1.sav"]


def test_vendor_uninstall_refuses_before_undoing_anything_while_set_aside_files_remain(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, vendor=True)
    rec = eng.ctx.records.load("lightcars")
    rec.steps = [{"action": "registry", "hive": "HKCU", "key": r"Software\LC", "name": "P", "type": "REG_SZ",
                  "value": "v", "undo": {"existed": False, "previous": None, "previous_type": None}}]
    eng.ctx.records.save(rec)
    eng.ctx.registry.set("HKCU", r"Software\LC", "P", "REG_SZ", "v")
    left = put(eng.ctx.work_root / ".setaside" / "lightcars", "cfg.ini", b"older copy")
    error = fails(eng.run, InstallRequest("uninstall", "lightcars"), quiet, never)
    assert error.message == messages.SETASIDE_LEFT_OVER.format(title="Light Cars", path=left.parent)
    assert eng.ctx.registry.get("HKCU", r"Software\LC", "P")[0] is True
    assert eng.ctx.records.load("lightcars") == rec and len(eng.ctx.elevator.calls) == 1


def test_run_install_updated_by_an_archive_then_uninstalled_by_its_record(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, vendor=True)
    data = zip_bytes({"lc.exe": b"exe v2"})
    eng.ctx.entries["lightcars"] = dict(eng.ctx.entries["lightcars"], version="2.0",
                                        sources=[served_source(routes, f"{MIRROR}{sha(data)}/lc.zip", data)])
    eng.ctx.fetcher.routes.update(routes)
    eng.run(InstallRequest("update", "lightcars"), quiet, never)
    assert eng.ctx.records.load("lightcars").vendor_uninstaller is None
    r = eng.run(InstallRequest("uninstall", "lightcars"), quiet, never)
    assert not (target / "lc.exe").exists() and not (target / "unins000.exe").exists()
    assert r.kept_player_files == 2 and len(eng.ctx.elevator.calls) == 1


def test_missing_vendor_uninstaller_falls_back_to_the_record(tmp_path):
    gone = tmp_path / "Games" / "Light Cars" / "unins000.exe"
    eng, routes, target = installed_run_game(tmp_path, vendor=f'"{gone}" /SILENT')
    r = eng.run(InstallRequest("uninstall", "lightcars"), quiet, never)
    assert messages.VENDOR_UNINSTALLER_MISSING.format(title="Light Cars") in r.notes
    assert snapshot(target) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save"}
    assert len(eng.ctx.elevator.calls) == 1


def test_vendor_uninstaller_error_is_a_note_and_files_are_put_back(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, vendor=True)
    elevator = eng.ctx.elevator

    def uninstaller(exe, args, elevated):
        elevator.exit_codes[exe] = 3
        (target / "cfg.ini").unlink()
    elevator.on_start = uninstaller
    r = eng.run(InstallRequest("uninstall", "lightcars"), quiet, never)
    assert messages.UNINSTALLER_FAILED.format(title="Light Cars", code=3) in r.notes
    assert snapshot(target) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save"}
    assert eng.ctx.records.load("lightcars").uninstalled


# ---- the real Windows pieces (read-only, no installer, no UAC) ------------------------------------------


@pytest.mark.windows
def test_job_watch_waits_for_children_of_an_early_exit(tmp_path):
    from engine.elevation import WindowsElevator
    script = tmp_path / "parent.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "time.sleep(0.3)\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1.5)'],\n"
        "                 creationflags=subprocess.CREATE_NO_WINDOW)\n", encoding="utf-8")
    process = WindowsElevator().start(sys.executable, f'"{script}"', False)
    watch = run_installer.watch_process(process, False)
    started = time.monotonic()
    try:
        while not watch.poll(0.1):
            assert time.monotonic() - started < 20
        assert time.monotonic() - started >= 1.2  # the child, not the parent, decided when it was over
        assert watch.exit_code() == 0
    finally:
        watch.close()


@pytest.mark.windows
def test_job_watch_terminate_ends_the_whole_tree(tmp_path):
    from engine.elevation import WindowsElevator
    script = tmp_path / "parent.py"
    script.write_text(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],\n"
        "                 creationflags=subprocess.CREATE_NO_WINDOW)\n"
        "import time; time.sleep(30)\n", encoding="utf-8")
    process = WindowsElevator().start(sys.executable, f'"{script}"', False)
    watch = run_installer.watch_process(process, False)
    try:
        time.sleep(0.5)
        assert not watch.poll(0.05)
        watch.terminate()
        started = time.monotonic()
        while not watch.poll(0.1):
            assert time.monotonic() - started < 10
    finally:
        watch.close()


@pytest.mark.windows
def test_real_uninstall_keys_can_be_read():
    snapshot = snapshot_uninstall_keys()  # read-only
    assert isinstance(snapshot, dict) and all(isinstance(v, dict) for v in snapshot.values())


# ---- fix round 1 -------------------------------------------------------------------------------------------


def setaside(eng):
    return eng.ctx.work_root / ".setaside" / "lightcars"


def overwrites_cfg(target):
    def installer(exe, args, elevated):
        put(target, "cfg.ini", b"volume=7")
        put(target, "lc.exe", b"exe v2")
    return installer


# Finding 1 (R21): copies are deleted only when the whole process tree was seen to finish.

def test_update_keeps_copies_when_the_tree_wasnt_seen_to_finish(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes)
    eng.ctx.elevator.on_start = overwrites_cfg(target)
    eng.ctx.process_watch = watch_with(observed=False)  # e.g. an elevated bootstrapper's child couldn't be followed
    r = eng.run(InstallRequest("update", "lightcars"), quiet, never)
    assert (target / "cfg.ini").read_bytes() == b"volume=9 mine"
    assert messages.SETASIDE_KEPT_TO_BE_SAFE.format(title="Light Cars", path=setaside(eng)) in r.notes
    # The child that was still running now deletes the player's files: their copies are still safe.
    (target / "cfg.ini").unlink()
    (target / "saves" / "slot1.sav").unlink()
    assert snapshot(setaside(eng)) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save"}


def test_vendor_uninstall_doesnt_put_back_or_delete_copies_when_the_tree_wasnt_seen_to_finish(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, vendor=True, elevation=True)
    eng.ctx.process_watch = watch_with(observed=False)  # an NSIS uninstaller that relaunched itself from %TEMP%
    r = eng.run(InstallRequest("uninstall", "lightcars"), quiet, never)
    assert messages.SETASIDE_NOT_PUT_BACK.format(title="Light Cars", path=setaside(eng)) in r.notes
    import shutil
    shutil.rmtree(target)  # ... and that relaunched copy then removes the whole folder
    assert snapshot(setaside(eng)) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save"}


def test_done_pressed_early_keeps_the_copies(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes, type="other")  # interactive
    eng.ctx.installer_ui = FakeInstallerUI(early=True)

    def installer(exe, args, elevated):
        eng.ctx.elevator.hang.add(exe)  # still running when the player presses Done
        put(target, "cfg.ini", b"volume=7")
    eng.ctx.elevator.on_start = installer
    r = eng.run(InstallRequest("update", "lightcars"), quiet, never)
    assert messages.SETASIDE_KEPT_TO_BE_SAFE.format(title="Light Cars", path=setaside(eng)) in r.notes
    assert (setaside(eng) / "cfg.ini").read_bytes() == b"volume=9 mine"


def test_copies_go_once_the_tree_was_seen_to_finish(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes)
    eng.ctx.elevator.on_start = overwrites_cfg(target)
    r = eng.run(InstallRequest("update", "lightcars"), quiet, never)
    assert not setaside(eng).exists() and r.notes == []


# Finding 2: a folder shared with other games is never recorded as this game's.

def test_located_folder_inside_another_games_folder_is_refused(tmp_path):
    routes = {}
    other = tmp_path / "Games" / "Other Game"
    put(other, "other.exe", b"o")
    ui = FakeInstallerUI(locate=other / "sub")
    put(other, "sub/x.exe", b"x")
    eng, lib = setup_engine(tmp_path, [run_game(routes)], routes, ui=ui)
    eng.ctx.records.save(InstallRecord("othergame", "game", "Games", "Other Game", "other.exe",
                                       InstalledSource("u", "s", 1, None, None, 0, "1"), {}, {}, [], "zip"))
    assert fails(install, eng).message == messages.INSTALL_FOLDER_NOT_FOUND.format(title="Light Cars")


def test_registry_folder_holding_another_games_folder_is_skipped(tmp_path):
    routes = {}
    dev = tmp_path / "Program Files" / "Dev"
    keys = FakeUninstallKeys()
    place = writes(dev, {"NewGame/lc.exe": b"exe"})

    def on_start(exe, args, elevated):
        place(exe, args, elevated)
        keys.add("LC", {"DisplayName": "Light Cars", "InstallLocation": str(dev)})
    eng, lib = setup_engine(tmp_path, [run_game(routes, installer={"type": "inno"})], routes,
                            on_start=on_start, uninstall_reader=keys)
    eng.ctx.records.save(InstallRecord("oldgame", "game", "", str(dev / "OldGame"), "old.exe",
                                       InstalledSource("u", "s", 1, None, None, 0, "1"), {}, {}, [], "run"))
    assert fails(install, eng).message == messages.INSTALL_FOLDER_NOT_FOUND.format(title="Light Cars")


def test_file_written_to_but_created_before_the_window_is_not_claimed(tmp_path):
    settings = put(tmp_path, "OldGame/settings.ini", b"s")
    time.sleep(0.1)
    start = time.time()
    time.sleep(0.03)
    settings.write_bytes(b"the other game saved its settings meanwhile")
    put(tmp_path, "NewGame/new.exe", b"n")
    time.sleep(0.03)
    assert files_created_during(tmp_path, start, time.time(), None, slack=0) == ["newgame/new.exe"]


def test_shared_folder_with_other_content_is_untracked(tmp_path, monkeypatch):
    monkeypatch.setattr(run_installer, "CLOCK_SLACK", 0.0)
    routes = {}
    dev = tmp_path / "Program Files" / "Dev"
    put(dev, "OldGame/old.dat", b"o")  # another game, already there
    time.sleep(0.05)
    keys = FakeUninstallKeys()
    place = writes(dev, {"NewGame/lc.exe": b"exe", "common.dat": b"c"})

    def on_start(exe, args, elevated):
        place(exe, args, elevated)
        keys.add("LC", {"DisplayName": "Light Cars", "InstallLocation": str(dev)})
    eng, lib = setup_engine(tmp_path, [run_game(routes, installer={"type": "inno"})], routes,
                            on_start=on_start, uninstall_reader=keys)
    r = install(eng)
    rec = eng.ctx.records.load("lightcars")
    assert rec.tracked is False and rec.files == {}
    assert messages.UNTRACKED_NOTE.format(title="Light Cars") in r.notes


# Finding 3: a chained redistributable is neither the game's folder nor its uninstaller.

def test_registry_prefers_the_games_entry_over_a_chained_redistributable(tmp_path):
    routes = {}
    redist = tmp_path / "Program Files" / "OpenAL"
    game = tmp_path / "Program Files" / "Draconis" / "Light Cars"
    keys = FakeUninstallKeys()
    place_redist = writes(redist, {"oalinst.exe": b"r", "OpenAL32.dll": b"d"})
    place_game = writes(game, {"lc.exe": b"exe"})

    def on_start(exe, args, elevated):
        place_redist(exe, args, elevated)
        place_game(exe, args, elevated)
        keys.add("AAA OpenAL", {"DisplayName": "OpenAL", "InstallLocation": str(redist),
                                "UninstallString": f'"{redist}\\oalinst.exe" /U'})
        keys.add("Light Cars_is1", {"DisplayName": "Light Cars 1.0", "InstallLocation": str(game),
                                    "QuietUninstallString": f'"{game}\\unins000.exe" /SILENT'})
    eng, lib = setup_engine(tmp_path, [run_game(routes, installer={"type": "inno"})], routes,
                            on_start=on_start, uninstall_reader=keys)
    install(eng)
    rec = eng.ctx.records.load("lightcars")
    assert Path(rec.folder) == game and rec.vendor_uninstaller == f'"{game}\\unins000.exe" /SILENT'


def test_a_lone_redistributable_entry_is_not_the_games_uninstaller(tmp_path):
    routes = {}
    target = tmp_path / "Games" / "Light Cars"
    keys = FakeUninstallKeys()
    place = writes(target, {"lc.exe": b"exe"})

    def on_start(exe, args, elevated):
        place(exe, args, elevated)
        keys.add("{VC}", {"DisplayName": "Microsoft Visual C++ 2005 Redistributable",
                          "UninstallString": "MsiExec.exe /X{VC}"})
    eng, lib = setup_engine(tmp_path, [run_game(routes, installer={"type": "inno"})], routes,
                            on_start=on_start, uninstall_reader=keys)
    install(eng)
    assert eng.ctx.records.load("lightcars").vendor_uninstaller is None


# Finding 4 (R22): the record goes first; the vendor uninstaller only for what it can't cover.

def test_vendor_uninstaller_runs_after_the_record_and_is_announced(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, vendor=True)
    seen = []

    def uninstaller(exe, args, elevated):
        seen.append(sorted(snapshot(target)))
    eng.ctx.elevator.on_start = uninstaller
    phases = []
    r = eng.run(InstallRequest("uninstall", "lightcars"), lambda p: phases.append(p.phase), never)
    assert seen == [["cfg.ini", "saves/slot1.sav", "unins000.exe"]]  # the record's files were already gone
    assert messages.VENDOR_UNINSTALLER_RAN.format(title="Light Cars") in r.notes and "vendor_uninstaller" in phases
    assert snapshot(target) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save"}
    assert eng.ctx.records.load("lightcars").vendor_uninstaller is None


def test_vendor_uninstaller_runs_when_the_record_cant_remove_the_files(tmp_path, monkeypatch):
    import engine.jobs
    eng, routes, target = installed_run_game(tmp_path, vendor=True)
    real = engine.jobs.apply_uninstall
    calls = []

    def apply_uninstall(*args, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("Program Files needs administrator rights")
        return real(*args, **kw)
    monkeypatch.setattr(engine.jobs, "apply_uninstall", apply_uninstall)

    def uninstaller(exe, args, elevated):  # elevated, it can remove what it installed
        for rel in ("lc.exe", "sfx/a.wav", "unins000.exe"):
            (target / rel).unlink()
    eng.ctx.elevator.on_start = uninstaller
    r = eng.run(InstallRequest("uninstall", "lightcars"), quiet, never)
    assert snapshot(target) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save"} and r.kept_player_files == 2


# Finding 5 (R22): the player's code files aren't set aside on update.

def test_update_replaces_a_players_changed_exe_without_a_copy(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    put(target, "lc.exe", b"exe patched by the player")
    put(target, "mod.dll", b"the player's mod")
    publish_v2(eng, routes)
    eng.ctx.elevator.on_start = lambda exe, args, elevated: put(target, "lc.exe", b"exe v2")
    eng.run(InstallRequest("update", "lightcars"), quiet, never)
    assert (target / "lc.exe").read_bytes() == b"exe v2" and not (target / "lc.exe.new").exists()
    rec = eng.ctx.records.load("lightcars")
    assert rec.files["lc.exe"].sha256 == sha(b"exe v2")
    assert "mod.dll" not in rec.files and (target / "mod.dll").read_bytes() == b"the player's mod"


# Finding 6 (R22): a silent installer whose programs run on doesn't block forever.

def test_silent_installer_whose_children_run_on_finishes_after_the_grace_period(tmp_path, monkeypatch):
    monkeypatch.setattr(run_installer, "GRACE_SECONDS", 0.2)
    routes = {}
    target = tmp_path / "Games" / "Light Cars"
    eng, lib = setup_engine(tmp_path, [run_game(routes, installer={"type": "inno"})], routes,
                            on_start=writes(target, {"lc.exe": b"exe"}),
                            process_watch=watch_with(children_running=True))  # it started the game
    started = time.monotonic()
    install(eng)
    assert time.monotonic() - started < 10 and eng.ctx.records.load("lightcars").tracked
    assert not eng.ctx.process_watch.made[0].terminated  # the game it started is left running


def test_silent_installer_offers_done_when_there_is_a_ui(tmp_path):
    routes = {}
    target = tmp_path / "Games" / "Light Cars"
    ui = FakeInstallerUI(early=True)
    eng, lib = setup_engine(tmp_path, [run_game(routes, installer={"type": "inno"})], routes,
                            on_start=writes(target, {"lc.exe": b"exe"}), ui=ui,
                            process_watch=watch_with(children_running=True))
    install(eng)
    assert ui.events == ["wait"] and eng.ctx.records.load("lightcars").tracked


# Finding 7: a cancelled installer that won't stop is left alone.

def test_cancel_of_an_installer_that_wont_stop_cleans_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(run_installer, "STOP_SECONDS", 0.2)
    routes = {}
    target = tmp_path / "Games" / "Light Cars"
    started = []
    place = writes(target, {"lc.exe": b"exe"})

    def on_start(exe, args, elevated):
        started.append(exe)
        eng.ctx.elevator.hang.add(exe)
        place(exe, args, elevated)
    eng, lib = setup_engine(tmp_path, [run_game(routes, installer={"type": "inno", "elevation": True})], routes,
                            on_start=on_start, process_watch=watch_with(unstoppable=True))
    error = fails(install, eng, cancel=lambda: bool(started))
    assert error.message == messages.INSTALLER_STILL_RUNNING.format(title="Light Cars")
    assert (target / "lc.exe").exists()  # not cleaned up under a running installer
    assert eng.ctx.process_watch.made[0].terminated


def test_cancelled_update_that_wont_stop_keeps_the_copies(tmp_path, monkeypatch):
    monkeypatch.setattr(run_installer, "STOP_SECONDS", 0.2)
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes)
    started = []

    def installer(exe, args, elevated):
        started.append(exe)
        eng.ctx.elevator.hang.add(exe)
        put(target, "cfg.ini", b"volume=7")
    eng.ctx.elevator.on_start = installer
    eng.ctx.process_watch = watch_with(unstoppable=True)
    error = fails(eng.run, InstallRequest("update", "lightcars"), quiet, lambda: bool(started))
    assert error.message == "\n\n".join([messages.INSTALLER_STILL_RUNNING.format(title="Light Cars"),
                                            messages.SETASIDE_KEPT.format(title="Light Cars", path=setaside(eng))])
    assert (setaside(eng) / "cfg.ini").read_bytes() == b"volume=9 mine"


# Finding 8: file steps only touch the install's own files.

def test_update_file_step_that_would_delete_a_players_file_is_refused(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes, steps=[{"action": "delete", "path": "saves"}])
    eng.ctx.elevator.on_start = lambda exe, args, elevated: put(target, "lc.exe", b"exe v2")
    error = fails(eng.run, InstallRequest("update", "lightcars"), quiet, never)
    assert error.message.startswith(messages.STEP_TOUCHES_OTHER_FILES.format(title="Light Cars"))
    assert (target / "saves" / "slot1.sav").read_bytes() == b"save"


def test_install_file_steps_run_on_the_installs_files(tmp_path):
    routes = {}
    target = tmp_path / "Games" / "Light Cars"
    e = run_game(routes, installer={"type": "inno"}, steps=[{"action": "move", "from": "lc.ex_", "to": "lc.exe"}])
    eng, lib = setup_engine(tmp_path, [e], routes, on_start=writes(target, {"lc.ex_": b"exe"}))
    install(eng)
    rec = eng.ctx.records.load("lightcars")
    assert set(rec.files) == {"lc.exe"} and rec.launch_path == "lc.exe"


def test_untracked_install_skips_its_file_steps_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(run_installer, "CLOCK_SLACK", 0.0)
    routes = {}
    chosen = tmp_path / "Somewhere" / "LC"
    put(chosen, "lc.exe", b"exe")
    put(chosen, "readme.txt", b"r")
    time.sleep(0.05)
    e = run_game(routes, steps=[{"action": "delete", "path": "readme.txt"}])
    eng, lib = setup_engine(tmp_path, [e], routes, ui=FakeInstallerUI(locate=chosen))
    r = install(eng)
    assert (chosen / "readme.txt").exists()
    assert messages.FILE_STEPS_SKIPPED.format(title="Light Cars") in r.notes


# Finding 9 (R22): the set-aside root can't be a game's work folder.

def test_set_aside_copies_survive_a_job_for_a_game_called_setaside(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    left = put(setaside(eng), "cfg.ini", b"kept copy")
    data = zip_bytes({"s.exe": b"s"})
    eng.ctx.entries["setaside"] = {"id": "setaside", "kind": "game", "title": "Set Aside", "version": "1",
                                   "sources": [served_source(routes, f"{MIRROR}{sha(data)}/s.zip", data)]}
    eng.ctx.fetcher.routes.update(routes)
    eng.run(InstallRequest("install", "setaside", "Games"), quiet, never)
    assert left.read_bytes() == b"kept copy" and setaside(eng) == eng.ctx.work_root / ".setaside" / "lightcars"


@pytest.mark.windows
def test_tree_watch_follows_children_outside_any_job(tmp_path):
    """The elevated path's watch (no job): children are followed through the process list."""
    import win32process
    from engine.elevation import _ShellProcess
    from engine.installer_watch import watch_process
    script = tmp_path / "parent.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "time.sleep(0.3)\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1.5)'],\n"
        "                 creationflags=subprocess.CREATE_NO_WINDOW)\n", encoding="utf-8")
    handle, thread, _, _ = win32process.CreateProcess(None, f'"{sys.executable}" "{script}"', None, None, False, 0,
                                                      None, None, win32process.STARTUPINFO())
    thread.Close()
    watch = watch_process(_ShellProcess(handle), True)
    started = time.monotonic()
    try:
        while not watch.poll(0.1):
            assert time.monotonic() - started < 20
        assert time.monotonic() - started >= 1.2 and watch.tree_observed()
    finally:
        watch.close()


# ---- fix round 2 -------------------------------------------------------------------------------------------


# Finding 2(c): claims spanning a folder that was already there can't be told from another program's files.

def _dev_install(tmp_path, before, during):
    """Light Cars installed into a shared Dev folder named by its Windows entry; `before` is already
    there, `during` is written while the installer runs (by it, or by something else)."""
    routes = {}
    dev = tmp_path / "Program Files" / "Dev"
    for rel, data in before.items():
        put(dev, rel, data)
    time.sleep(0.05)
    keys = FakeUninstallKeys()
    place = writes(dev, during)

    def on_start(exe, args, elevated):
        place(exe, args, elevated)
        keys.add("LC", {"DisplayName": "Light Cars", "InstallLocation": str(dev)})
    eng, lib = setup_engine(tmp_path, [run_game(routes, installer={"type": "inno"})], routes,
                            on_start=on_start, uninstall_reader=keys)
    return eng, dev


def test_claims_spanning_a_folder_that_was_already_there_are_untracked(tmp_path, monkeypatch):
    monkeypatch.setattr(run_installer, "CLOCK_SLACK", 0.0)
    eng, dev = _dev_install(tmp_path, {"OldGame/old.dat": b"o"},
                            {"NewGame/lc.exe": b"exe", "OldGame/newsave.sav": b"the other game saved"})
    r = install(eng)
    rec = eng.ctx.records.load("lightcars")
    assert rec.tracked is False and rec.files == {}
    assert messages.UNTRACKED_NOTE.format(title="Light Cars") in r.notes
    assert (dev / "OldGame" / "newsave.sav").exists()


def test_claims_spanning_only_new_folders_stay_tracked(tmp_path, monkeypatch):
    monkeypatch.setattr(run_installer, "CLOCK_SLACK", 0.0)
    eng, dev = _dev_install(tmp_path, {}, {"NewGame/lc.exe": b"exe", "Common/shared.dll": b"c"})
    install(eng)
    rec = eng.ctx.records.load("lightcars")
    assert rec.tracked and set(rec.files) == {"newgame/lc.exe", "common/shared.dll"}


# Finding A: a program that never started has nothing running; the copies go back and go.

def test_update_whose_installer_never_started_leaves_no_copies_and_can_be_retried(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, elevation=True)
    publish_v2(eng, routes, elevation=True)
    eng.ctx.elevator.decline = True
    error = fails(eng.run, InstallRequest("update", "lightcars"), quiet, never)
    assert error.message == messages.ELEVATION_DECLINED.format(what="Light Cars")
    assert not setaside(eng).exists()
    assert (target / "cfg.ini").read_bytes() == b"volume=9 mine"
    eng.ctx.elevator.decline = False
    eng.ctx.elevator.on_start = overwrites_cfg(target)
    eng.run(InstallRequest("update", "lightcars"), quiet, never)
    assert eng.ctx.records.load("lightcars").source.version == "2.0"


def test_vendor_uninstall_whose_uninstaller_never_started_puts_files_back_and_can_be_retried(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, vendor=True, elevation=True)
    eng.ctx.elevator.decline = True
    error = fails(eng.run, InstallRequest("uninstall", "lightcars"), quiet, never)
    assert error.message == messages.ELEVATION_DECLINED.format(what="Light Cars")
    assert not setaside(eng).exists()
    assert snapshot(target) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save", "unins000.exe": b"uninstaller"}
    rec = eng.ctx.records.load("lightcars")
    assert rec.uninstalled and rec.vendor_uninstaller and "unins000.exe" in rec.files
    eng.ctx.elevator.decline = False
    eng.ctx.elevator.on_start = None  # the uninstaller itself changes nothing here
    eng.run(InstallRequest("uninstall", "lightcars"), quiet, never)
    assert eng.ctx.elevator.calls[-1][0] == str(target / "unins000.exe")
    assert snapshot(target) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save"}
    rec = eng.ctx.records.load("lightcars")
    assert rec.uninstalled and rec.files == {} and rec.vendor_uninstaller is None


# Findings C and D: a cancel that keeps the copies says where they are, in words that fit what happened.

def hangs_once_started(eng, then):
    started = []

    def program(exe, args, elevated):
        started.append(exe)
        eng.ctx.elevator.hang.add(exe)
        then(exe, args, elevated)
    eng.ctx.elevator.on_start = program
    return lambda: bool(started)


def test_cancelled_update_whose_tree_wasnt_seen_to_finish_says_where_the_copies_are(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes)
    cancel = hangs_once_started(eng, lambda exe, args, elevated: put(target, "cfg.ini", b"volume=7"))
    eng.ctx.process_watch = watch_with(observed=False)
    assert fails(eng.run, InstallRequest("update", "lightcars"), quiet, cancel).message == "\n\n".join([
        messages.INSTALLER_CANCELLED.format(title="Light Cars"),
        messages.SETASIDE_KEPT_TO_BE_SAFE.format(title="Light Cars", path=setaside(eng))])
    assert (target / "cfg.ini").read_bytes() == b"volume=9 mine"  # put back
    assert (setaside(eng) / "cfg.ini").read_bytes() == b"volume=9 mine"  # and still kept


def test_cancelled_vendor_uninstall_whose_tree_wasnt_seen_to_finish_says_where_the_copies_are(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, vendor=True)
    cancel = hangs_once_started(eng, lambda exe, args, elevated: None)
    eng.ctx.process_watch = watch_with(observed=False)
    assert fails(eng.run, InstallRequest("uninstall", "lightcars"), quiet, cancel).message == "\n\n".join([
        messages.UNINSTALLER_CANCELLED.format(title="Light Cars"),
        messages.SETASIDE_NOT_PUT_BACK.format(title="Light Cars", path=setaside(eng))])
    assert snapshot(setaside(eng)) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save"}


def test_cancelled_vendor_uninstall_whose_files_couldnt_be_put_back_says_so(tmp_path, monkeypatch):
    eng, routes, target = installed_run_game(tmp_path, vendor=True)

    def removes_the_save(exe, args, elevated):
        (target / "saves" / "slot1.sav").unlink()
    cancel = hangs_once_started(eng, removes_the_save)
    real_copy = run_installer.shutil.copy2

    def copy2(src, dst, **kw):
        if ".setaside" in str(src):
            raise OSError("disk full")
        return real_copy(src, dst, **kw)
    monkeypatch.setattr(run_installer.shutil, "copy2", copy2)
    assert fails(eng.run, InstallRequest("uninstall", "lightcars"), quiet, cancel).message == "\n\n".join([
        messages.UNINSTALLER_CANCELLED.format(title="Light Cars"),
        messages.SETASIDE_KEPT.format(title="Light Cars", path=setaside(eng))])


def test_cancelled_vendor_uninstall_seen_to_stop_puts_files_back_quietly(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, vendor=True)

    def removes_the_save(exe, args, elevated):
        (target / "saves" / "slot1.sav").unlink()
    cancel = hangs_once_started(eng, removes_the_save)
    with pytest.raises(Cancelled):
        eng.run(InstallRequest("uninstall", "lightcars"), quiet, cancel)
    assert (target / "saves" / "slot1.sav").read_bytes() == b"save" and not setaside(eng).exists()


def test_game_fields_for_outside_libraries():
    from engine.jobs import game_fields_for
    entry = {"id": "lightcars", "title": "Light Cars", "platforms": ["Windows"]}
    elsewhere = Path(os.path.abspath("Old Games")) / "Light Cars"
    rec = InstallRecord(catalog_id="lightcars", kind="game", library_id="", folder=str(elsewhere),
                        launch_path="lc.exe", method="run", source=InstalledSource("u", "s" * 64, 1, "c" * 64, 1, 0, "1.0"),
                        files={}, extras={}, steps=[])
    game = game_fields_for(entry, rec, "windows")
    assert game["library_id"] == "" and Path(game["launch_path"]) == elsewhere / "lc.exe"
    assert Path(game["launch_path"]).is_absolute() and "library_name" not in game and "manual" not in game.values()


# ---- a cancel at the "where to install" prompt (quitting releases it) -----------------------------------------


class CancellingUI(FakeInstallerUI):
    """The player is told where to install, and the job is cancelled meanwhile (or the app quits and releases the
    prompt, which returns at once)."""

    def __init__(self):
        super().__init__()
        self.cancelled = False

    def announce_target(self, title, target):
        super().announce_target(title, target)
        self.cancelled = True

    def cancel(self):
        return self.cancelled


def test_cancel_at_the_announce_never_starts_the_installer(tmp_path):
    routes = {}
    ui = CancellingUI()
    eng, lib = setup_engine(tmp_path, [run_game(routes)], routes, ui=ui)  # interactive: the target is announced
    with pytest.raises(Cancelled):
        install(eng, cancel=ui.cancel)
    assert ui.events == ["announce"] and eng.ctx.elevator.calls == []
    assert eng.ctx.records.load("lightcars") is None and not (lib / "Light Cars").exists()


def test_cancel_at_the_announce_of_an_update_puts_the_players_files_back(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes, type="other")  # interactive
    ui = CancellingUI()
    eng.ctx.installer_ui = ui
    before = snapshot(target)
    starts = len(eng.ctx.elevator.calls)
    with pytest.raises(Cancelled):
        eng.run(InstallRequest("update", "lightcars"), quiet, ui.cancel)
    assert ui.events == ["announce"] and len(eng.ctx.elevator.calls) == starts
    assert snapshot(target) == before and eng.ctx.records.load("lightcars").source.version == "1.0"
    run_installer.require_no_setaside(eng.ctx, "lightcars", "Light Cars")  # no copies left set aside


def test_run_and_wait_checks_the_cancel_before_starting(tmp_path):
    eng, lib = setup_engine(tmp_path, [], {})
    with pytest.raises(Cancelled) as ei:
        run_installer._run_and_wait(eng.ctx, "Light Cars", "setup.exe", "", False, None, quiet, lambda: True)
    assert ei.value.tree_done and eng.ctx.elevator.calls == []
