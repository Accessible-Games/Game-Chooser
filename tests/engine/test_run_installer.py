"""Installers that must run: silent and interactive runs, finding where the game went, tracking it by
the install window, and the set-aside rule for run-mode updates and vendor uninstalls."""

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from engine import messages, run_installer
from engine.errors import Cancelled, InstallFailed
from engine.jobs import InstallRequest
from engine.records import InstalledSource, InstallRecord, RecordedFile
from engine.run_installer import (UNINSTALL_KEYS, files_created_during, folder_from_uninstall_entry,
                                  installer_command, new_uninstall_entries, silent_args_for, snapshot_folder,
                                  snapshot_uninstall_keys)
from tests.engine.fakes import (FakeElevator, FakeFetcher, FakeInstallerUI, FakeUninstallKeys, make_engine,
                                served_source, zip_bytes)

MIRROR = "https://files.accessiblegames.net/files/"
SETUP = b"MZ fake setup program"


def never():
    return False


def quiet(p):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(root: Path, rel: str, data: bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


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
        time.sleep(0.02)  # file times are coarser than time.time(); keep them clearly inside the window
        for rel, data in files.items():
            put(root, rel, data)
        time.sleep(0.02)
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
    assert rec.method == "run" and rec.tracked and rec.library_name == "Games" and rec.folder == "Light Cars"
    assert set(rec.files) == {"lc.exe", "sfx/a.wav"} and rec.files["lc.exe"].sha256 == sha(b"exe")
    assert rec.launch_path == "lc.exe" and rec.vendor_uninstaller is None
    assert rec.source.install["installer"]["type"] == "inno"
    assert r.game["launch_path"] == "Light Cars/lc.exe" and r.game["library_name"] == "Games"
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
    with pytest.raises(InstallFailed) as ei:
        install(eng)
    assert ei.value.message == messages.INSTALLER_NEEDS_PLAYER.format(title="Light Cars")
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
    assert rec.library_name == "" and Path(rec.folder) == elsewhere / "bin" and Path(rec.folder).is_absolute()
    assert rec.launch_path == "lc.exe" and rec.vendor_uninstaller == "MsiExec.exe /X{1}"
    assert r.game["library_name"] == "manual"
    assert Path(r.game["launch_path"]) == elsewhere / "bin" / "lc.exe" and Path(r.game["launch_path"]).is_absolute()


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
    assert rec.library_name == "Games" and rec.folder == "LightCars2007"
    assert r.game["launch_path"] == "LightCars2007/lc.exe" and r.game["library_name"] == "Games"


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
    with pytest.raises(InstallFailed) as ei:
        install(eng)
    assert ei.value.message == messages.INSTALL_FOLDER_NOT_FOUND.format(title="Light Cars")
    assert eng.ctx.records.load("lightcars") is None and not (lib / "Light Cars").exists()


def test_player_located_drive_root_is_not_accepted(tmp_path):
    routes = {}
    ui = FakeInstallerUI(locate=Path(tmp_path.anchor))
    eng, lib = setup_engine(tmp_path, [run_game(routes)], routes, ui=ui)
    with pytest.raises(InstallFailed) as ei:
        install(eng)
    assert ei.value.message == messages.INSTALL_FOLDER_NOT_FOUND.format(title="Light Cars")


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
    with pytest.raises(InstallFailed) as ei:
        install(eng)
    assert ei.value.message == messages.INSTALLER_FAILED.format(title="Light Cars", code=5)
    assert not target.exists() and eng.ctx.records.load("lightcars") is None


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
    with pytest.raises(InstallFailed) as ei:
        install(eng)
    assert ei.value.message == messages.ELEVATION_DECLINED.format(what="Light Cars")


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
    with pytest.raises(InstallFailed) as ei:
        install(eng, "webgame")
    assert ei.value.message == messages.WEB_GAME_NOT_INSTALLED.format(title="Web Game")
    assert eng.ctx.fetcher.requests == []


def test_extras_on_a_run_mode_game_are_refused_plainly(tmp_path):
    routes = {}
    e = run_game(routes, installer={"type": "inno"}, extras=[{"id": "lc-patch", "required": True}])
    data = zip_bytes({"patch.dat": b"p"})
    x = {"id": "lc-patch", "kind": "extra", "title": "Patch", "install_into": "lightcars", "version": "1",
         "sources": [served_source(routes, f"{MIRROR}{sha(data)}/p.zip", data)]}
    eng, lib = setup_engine(tmp_path, [e, x], routes)
    with pytest.raises(InstallFailed) as ei:
        install(eng)
    assert ei.value.message == messages.RUN_INSTALLER_EXTRAS.format(title="Light Cars")
    assert eng.ctx.elevator.calls == []


def test_run_mode_extra_is_refused_plainly(tmp_path):
    routes = {}
    data = zip_bytes({"lc.exe": b"exe"})
    e = {"id": "lightcars", "kind": "game", "title": "Light Cars", "version": "1",
         "sources": [served_source(routes, f"{MIRROR}{sha(data)}/lc.zip", data)],
         "extras": [{"id": "lc-patch", "required": True}]}
    x = run_game(routes, id="lc-patch", title="Patch", setup=b"patch setup", installer={"type": "inno"},
                 kind="extra", install_into="lightcars")
    eng, lib = setup_engine(tmp_path, [e, x], routes)
    with pytest.raises(InstallFailed) as ei:
        install(eng)
    assert ei.value.message == messages.EXTRA_NOT_SUPPORTED.format(extra="Patch")
    assert not (lib / "Light Cars").exists()


# ---- run-mode update -------------------------------------------------------------------------------------


def installed_run_game(tmp_path, files=None, vendor=None, elevation=False):
    """Light Cars installed by a run-mode installer and then played."""
    routes = {}
    e = run_game(routes, installer={"type": "inno", "elevation": elevation})
    target = tmp_path / "Games" / "Light Cars"
    files = files or {"lc.exe": b"exe v1", "cfg.ini": b"volume=5", "sfx/a.wav": b"a"}
    eng, lib = setup_engine(tmp_path, [e], routes, on_start=writes(target, files))
    install(eng)
    rec = eng.ctx.records.load("lightcars")
    if vendor:
        rec.vendor_uninstaller = vendor
        eng.ctx.records.save(rec)
    put(target, "cfg.ini", b"volume=9 mine")
    put(target, "saves/slot1.sav", b"save")
    return eng, routes, target


def publish_v2(eng, routes, setup=b"MZ setup v2", **installer):
    e = run_game(routes, setup=setup, installer={"type": "inno", **installer})
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
    assert not (eng.ctx.work_root / "setaside" / "lightcars").exists()


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
    left = put(eng.ctx.work_root / "setaside" / "lightcars", "cfg.ini", b"older copy")
    with pytest.raises(InstallFailed) as ei:
        eng.run(InstallRequest("update", "lightcars"), quiet, never)
    assert ei.value.message == messages.SETASIDE_LEFT_OVER.format(title="Light Cars", path=left.parent)
    assert left.read_bytes() == b"older copy" and len(eng.ctx.elevator.calls) == 1


def test_cancelled_update_restores_player_files_and_removes_only_new_ones(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    publish_v2(eng, routes)
    before = tree(target)
    started = []

    def installer(exe, args, elevated):
        started.append(exe)
        eng.ctx.elevator.hang.add(exe)
        put(target, "cfg.ini", b"installer's half-written ini")
        put(target, "new.dll", b"new")
        (target / "saves" / "slot1.sav").unlink()
    eng.ctx.elevator.on_start = installer
    with pytest.raises(Cancelled):
        eng.run(InstallRequest("update", "lightcars"), quiet, lambda: bool(started))
    after = tree(target)
    assert after.pop("cfg.ini.new") == b"installer's half-written ini"
    assert after == before
    assert not (eng.ctx.work_root / "setaside" / "lightcars").exists()
    assert eng.ctx.records.load("lightcars").source.version == "1.0"


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
    kept = eng.ctx.work_root / "setaside" / "lightcars"
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
    with pytest.raises(InstallFailed) as ei:
        eng.run(InstallRequest("update", "lightcars"), quiet, never)
    assert ei.value.message == messages.INSTALL_NOT_CHANGEABLE.format(title="Light Cars")


def test_repair_of_a_run_mode_install_is_refused_plainly(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    with pytest.raises(InstallFailed) as ei:
        eng.run(InstallRequest("repair", "lightcars"), quiet, never)
    assert ei.value.message == messages.INSTALL_NOT_CHANGEABLE.format(title="Light Cars")


# ---- uninstall -------------------------------------------------------------------------------------------


def test_own_record_uninstall_of_a_run_install(tmp_path):
    eng, routes, target = installed_run_game(tmp_path)
    r = eng.run(InstallRequest("uninstall", "lightcars"), quiet, never)
    assert tree(target) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save"}
    assert r.kept_player_files == 2 and len(eng.ctx.elevator.calls) == 1


def test_vendor_uninstall_sets_aside_player_files(tmp_path):
    unins = '"C:\\Games\\Light Cars\\unins000.exe" /SILENT'
    eng, routes, target = installed_run_game(tmp_path, vendor=unins, elevation=True)

    def uninstaller(exe, args, elevated):
        import shutil
        shutil.rmtree(target)  # takes the player's saves with it
    eng.ctx.elevator.on_start = uninstaller
    r = eng.run(InstallRequest("uninstall", "lightcars"), quiet, never)
    assert eng.ctx.elevator.calls[-1] == ("C:\\Games\\Light Cars\\unins000.exe", "/SILENT", True)
    assert tree(target) == {"cfg.ini": b"volume=9 mine", "saves/slot1.sav": b"save"}
    assert r.kept_player_files == 2
    rec = eng.ctx.records.load("lightcars")
    assert rec.uninstalled and rec.files == {}
    assert not (eng.ctx.work_root / "setaside" / "lightcars").exists()


def test_vendor_uninstall_deletes_leftovers_and_recycles_player_files_when_asked(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, vendor='"X:\\unins000.exe" /SILENT')

    def uninstaller(exe, args, elevated):
        (target / "lc.exe").unlink()  # leaves sfx/a.wav behind, and the player's files
    eng.ctx.elevator.on_start = uninstaller
    r = eng.run(InstallRequest("uninstall", "lightcars", delete_player_data=True), quiet, never)
    assert not target.exists() and eng.ctx.records.load("lightcars") is None
    recycled = sorted(p.relative_to(target).as_posix() for p in eng.ctx.recycle.calls[0])
    assert recycled == ["cfg.ini", "saves/slot1.sav"]


def test_vendor_uninstall_refuses_before_undoing_anything_while_set_aside_files_remain(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, vendor='"X:\\unins000.exe" /SILENT')
    rec = eng.ctx.records.load("lightcars")
    rec.steps = [{"action": "registry", "hive": "HKCU", "key": r"Software\LC", "name": "P", "type": "REG_SZ",
                  "value": "v", "undo": {"existed": False, "previous": None, "previous_type": None}}]
    eng.ctx.records.save(rec)
    eng.ctx.registry.set("HKCU", r"Software\LC", "P", "REG_SZ", "v")
    left = put(eng.ctx.work_root / "setaside" / "lightcars", "cfg.ini", b"older copy")
    with pytest.raises(InstallFailed) as ei:
        eng.run(InstallRequest("uninstall", "lightcars"), quiet, never)
    assert ei.value.message == messages.SETASIDE_LEFT_OVER.format(title="Light Cars", path=left.parent)
    assert eng.ctx.registry.get("HKCU", r"Software\LC", "P")[0] is True
    assert eng.ctx.records.load("lightcars") == rec and len(eng.ctx.elevator.calls) == 1


def test_vendor_uninstaller_error_puts_files_back_and_keeps_record(tmp_path):
    eng, routes, target = installed_run_game(tmp_path, vendor='"X:\\unins000.exe" /SILENT')
    elevator = eng.ctx.elevator

    def uninstaller(exe, args, elevated):
        elevator.exit_codes[exe] = 3
        (target / "cfg.ini").unlink()
    elevator.on_start = uninstaller
    with pytest.raises(InstallFailed) as ei:
        eng.run(InstallRequest("uninstall", "lightcars"), quiet, never)
    assert ei.value.message == messages.UNINSTALLER_FAILED.format(title="Light Cars", code=3)
    assert (target / "cfg.ini").read_bytes() == b"volume=9 mine"
    rec = eng.ctx.records.load("lightcars")
    assert not rec.uninstalled and set(rec.files) == {"lc.exe", "cfg.ini", "sfx/a.wav"}


# ---- runtimes --------------------------------------------------------------------------------------------


def test_run_installer_untracked_for_runtimes(tmp_path):
    eng, lib = setup_engine(tmp_path, [], {})
    target = tmp_path / "runtimes" / "vb6"
    file = put(tmp_path, "dl/vbrun60.exe", b"vb")
    run_installer.run_installer_untracked(file, {"type": "other", "mode": "run", "silent_args": "/Q"}, target,
                                          eng.ctx, quiet, never)
    assert eng.ctx.elevator.calls == [(str(file), "/Q", False)]
    assert eng.ctx.records.all() == {}


def test_run_installer_untracked_error_code_fails(tmp_path):
    file = Path("vbrun60.exe")
    eng, lib = setup_engine(tmp_path, [], {}, elevator=FakeElevator(exit_codes={"vbrun60.exe": 4}))
    with pytest.raises(InstallFailed) as ei:
        run_installer.run_installer_untracked(file, {"type": "other", "mode": "run", "silent_args": "/Q"},
                                              tmp_path / "rt", eng.ctx, quiet, never, title="VB6 runtime")
    assert ei.value.message == messages.INSTALLER_FAILED.format(title="VB6 runtime", code=4)


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
