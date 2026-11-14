"""A runtime's DLL and OCX files, copied beside the game's launch exe so a load by name finds them."""

import json
import shutil
import time

import pytest

from engine import comfree, messages, run_installer, runtimes
from engine.records import RecordStore
from engine.runtimes import MARKER_NAME
from tests.engine.fakes import (ADMINISTRATORS, MIRROR, FakeElevator, FakeFetcher, FakeInstallerUI, FakeSecurity,
                                make_engine, served_source, sha, zip_bytes)
from tests.engine.test_runtimes import private_com  # noqa: F401  (a fixture, used by name)
from tests.engine.test_runtimes import (DX8VB, DX8VB_TITLE, dx8vb_catalog, game, plain_runtime, put_marker, run,
                                        run_mode_game, setup, vb6, writes_game)
from tests.engine.test_runtimes import dx8vb as dx8vb_entry

OWNER = "runtime:dx8vb"


def not_copied(title="Entombed", component=DX8VB_TITLE):
    return messages.RUNTIME_FILES_NOT_COPIED.format(title=title, component=component)


def layered_runtime(routes, id="dxlib", files=None):
    """A runtime with no elevated step whose download has a subfolder and files a game can't load."""
    files = files if files is not None else {"dxlib.dll": b"MZ dxlib", "dxlib.ocx": b"MZ ocx", "readme.txt": b"hi",
                                             "tool.exe": b"MZ tool", "lib/helper.dll": b"MZ helper"}
    data = zip_bytes(files)
    source = served_source(routes, f"{MIRROR}{sha(data)}/{id}.zip", data)
    return {"id": id, "kind": "runtime", "title": id.upper(), "version": "1", "sources": [source]}


# ---- shared runtimes -----------------------------------------------------------------------------------------


def test_shared_runtime_installed_now_is_copied_beside_the_exe_and_recorded(tmp_path):
    eng, lib, rt, g = dx8vb_catalog(tmp_path)
    r = run(eng, "install")
    folder = lib / "Entombed"
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB and r.notes == []
    rec = eng.ctx.records.load("entombed")
    assert rec.files["dx8vb.dll"].owner == OWNER and rec.files["dx8vb.dll"].sha256 == sha(DX8VB)
    assert rec.files["entombed.exe"].owner == "entombed" and rec.launch_path == "entombed.exe"
    assert not (folder / MARKER_NAME).exists()


def test_shared_runtime_already_present_is_still_copied(tmp_path):
    eng, lib, rt, g = dx8vb_catalog(tmp_path)
    folder = eng.ctx.runtimes_root / "dx8vb"
    put_marker(folder, {"dx8vb.dll": DX8VB})
    eng.ctx.security.set_owner(folder, ADMINISTRATORS)
    r = run(eng, "install")
    assert eng.ctx.fetcher.requests == [g["sources"][0]["url"]] and eng.ctx.elevator.calls == []
    assert (lib / "Entombed" / "dx8vb.dll").read_bytes() == DX8VB and r.notes == []
    assert eng.ctx.records.load("entombed").files["dx8vb.dll"].owner == OWNER


def test_sealed_folder_another_account_installed_is_copied_from(tmp_path):
    routes = {}
    rt, one = dx8vb_entry(routes), game(routes)
    two = game(routes, id="pawprints", title="Pawprints", files={"pawprints.exe": b"paw"})
    eng, lib = setup(tmp_path, [rt, one, two], routes)
    run(eng, "install")
    other = make_engine(tmp_path, [rt, one, two], fetcher=FakeFetcher(routes), records=RecordStore(tmp_path / "b"),
                        runtimes_root=eng.ctx.runtimes_root,
                        security=FakeSecurity(eng.ctx.elevator, user="S-1-5-21-2000-2000-2000-1002"))
    r = run(other, "install", id="pawprints")
    assert (lib / "Pawprints" / "dx8vb.dll").read_bytes() == DX8VB and r.notes == []
    assert other.ctx.records.load("pawprints").files["dx8vb.dll"].owner == OWNER


def test_file_that_doesnt_match_the_marker_isnt_copied_and_the_install_succeeds(tmp_path):
    eng, lib, rt, g = dx8vb_catalog(tmp_path)
    folder = eng.ctx.runtimes_root / "dx8vb"
    put_marker(folder, {"dx8vb.dll": DX8VB})
    eng.ctx.security.set_owner(folder, ADMINISTRATORS)
    (folder / "dx8vb.dll").write_bytes(b"MZ something else")
    r = run(eng, "install")
    assert r.notes == [not_copied()]
    assert (lib / "Entombed" / "entombed.exe").exists() and not (lib / "Entombed" / "dx8vb.dll").exists()
    assert "dx8vb.dll" not in eng.ctx.records.load("entombed").files


def test_untrusted_runtime_folder_isnt_copied_from(tmp_path):
    eng, lib, rt, g = dx8vb_catalog(tmp_path)
    folder = eng.ctx.runtimes_root / "dx8vb"
    put_marker(folder, {"dx8vb.dll": DX8VB})
    eng.ctx.security.set_owner(folder, "S-1-5-21-3000-3000-3000-1003")  # another account's folder
    eng.ctx.registry.set("HKLM", r"SOFTWARE\Classes\DxVBLibA.DirectX8", "", "REG_SZ", "DirectX8")  # present
    r = run(eng, "install")
    assert r.notes == [not_copied()] and not (lib / "Entombed" / "dx8vb.dll").exists()


def test_marker_naming_a_path_outside_its_folder_copies_nothing_and_raises_nothing(tmp_path):
    eng, lib, rt, g = dx8vb_catalog(tmp_path)
    folder = eng.ctx.runtimes_root / "dx8vb"
    folder.mkdir(parents=True)
    (folder.parent / "evil.dll").write_bytes(b"MZ evil")
    (folder / MARKER_NAME).write_text(json.dumps({"id": "dx8vb", "files": {"../evil.dll": sha(b"MZ evil")}}))
    eng.ctx.security.set_owner(folder, ADMINISTRATORS)
    eng.ctx.registry.set("HKLM", r"SOFTWARE\Classes\DxVBLibA.DirectX8", "", "REG_SZ", "DirectX8")  # present
    r = run(eng, "install")
    assert r.notes == [not_copied()] and sorted(p.name for p in (lib / "Entombed").iterdir()) == ["entombed.exe",
                                                                                                 "sounds"]


def test_runtime_present_only_by_its_detect_hint_has_nothing_to_copy(tmp_path):
    eng, lib, rt, g = dx8vb_catalog(tmp_path)
    eng.ctx.registry.set("HKLM", r"SOFTWARE\Classes\DxVBLibA.DirectX8", "", "REG_SZ", "DirectX8")
    r = run(eng, "install")
    assert r.notes == [] and not (lib / "Entombed" / "dx8vb.dll").exists()


def test_the_games_own_file_of_that_name_is_kept(tmp_path):
    routes = {}
    rt = dx8vb_entry(routes)
    g = game(routes, files={"entombed.exe": b"exe", "dx8vb.dll": b"the game's own copy"})
    eng, lib = setup(tmp_path, [rt, g], routes)
    r = run(eng, "install")
    assert (lib / "Entombed" / "dx8vb.dll").read_bytes() == b"the game's own copy" and r.notes == []
    assert eng.ctx.records.load("entombed").files["dx8vb.dll"].owner == "entombed"


def test_copy_goes_beside_an_exe_in_a_subfolder(tmp_path):
    routes = {}
    rt = dx8vb_entry(routes)
    g = game(routes, files={"bin/entombed.exe": b"exe", "sounds/a.wav": b"wav"})
    eng, lib = setup(tmp_path, [rt, g], routes)
    run(eng, "install")
    folder = lib / "Entombed"
    assert (folder / "bin" / "dx8vb.dll").read_bytes() == DX8VB and not (folder / "dx8vb.dll").exists()
    assert eng.ctx.records.load("entombed").files["bin/dx8vb.dll"].owner == OWNER


def test_only_loadable_files_are_copied_and_a_subfolder_keeps_its_layout(tmp_path):
    routes = {}
    rt = layered_runtime(routes)
    g = game(routes, dependencies=["dxlib"])
    eng, lib = setup(tmp_path, [rt, g], routes)
    run(eng, "install")
    folder = lib / "Entombed"
    assert (folder / "dxlib.dll").read_bytes() == b"MZ dxlib" and (folder / "dxlib.ocx").exists()
    assert (folder / "lib" / "helper.dll").read_bytes() == b"MZ helper" and not (folder / "helper.dll").exists()
    assert not (folder / "readme.txt").exists() and not (folder / "tool.exe").exists()
    assert not (folder / MARKER_NAME).exists()
    rec = eng.ctx.records.load("entombed")
    assert {k for k, f in rec.files.items() if f.owner == "runtime:dxlib"} == {"dxlib.dll", "dxlib.ocx",
                                                                                 "lib/helper.dll"}
    assert rec.launch_path == "entombed.exe"


def test_dependencies_of_dependencies_are_copied_too(tmp_path):
    routes = {}
    base = plain_runtime(routes, "base")
    top = plain_runtime(routes, "top", dependencies=["base"])
    eng, lib = setup(tmp_path, [base, top, game(routes, dependencies=["top"])], routes)
    run(eng, "install")
    assert (lib / "Entombed" / "base.dll").exists() and (lib / "Entombed" / "top.dll").exists()


def test_installer_runtime_gets_no_copies(tmp_path, monkeypatch):
    monkeypatch.setenv("GC_VB6", str(tmp_path / "system"))
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "msvbvm60.dll").write_bytes(b"MZ vb")  # present by its detect hint
    routes = {}
    rt, g = vb6(routes), game(routes, dependencies=["vb6-runtime"])
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "vb6-runtime"  # whatever its installer left in its folder
    put_marker(folder, {"msvbvm60.dll": b"MZ vb"}, runtime_id="vb6-runtime")
    eng.ctx.security.set_owner(folder, ADMINISTRATORS)
    r = run(eng, "install")
    assert r.notes == [] and not (lib / "Entombed" / "msvbvm60.dll").exists()
    assert not any(f.owner.startswith("runtime:") for f in eng.ctx.records.load("entombed").files.values())


def test_a_locked_file_in_the_runtime_folder_is_a_note_not_a_failure(tmp_path, monkeypatch):
    eng, lib, rt, g = dx8vb_catalog(tmp_path)
    folder = eng.ctx.runtimes_root / "dx8vb"
    put_marker(folder, {"dx8vb.dll": DX8VB})
    eng.ctx.security.set_owner(folder, ADMINISTRATORS)
    real = runtimes._copy_new

    def locked(src, dest):
        if src.parent == folder:
            raise PermissionError(13, "locked", str(src))
        return real(src, dest)
    monkeypatch.setattr(runtimes, "_copy_new", locked)
    r = run(eng, "install")
    assert r.notes == [not_copied()] and not (lib / "Entombed" / "dx8vb.dll").exists()


# ---- uninstall, repair, update, undo ---------------------------------------------------------------------


def test_uninstall_removes_an_unchanged_copy(tmp_path):
    eng, lib, rt, g = dx8vb_catalog(tmp_path)
    run(eng, "install")
    run(eng, "uninstall")
    assert not (lib / "Entombed").exists()
    assert (eng.ctx.runtimes_root / "dx8vb" / "dx8vb.dll").read_bytes() == DX8VB


def test_uninstall_keeps_a_changed_copy(tmp_path):
    eng, lib, rt, g = dx8vb_catalog(tmp_path)
    run(eng, "install")
    (lib / "Entombed" / "dx8vb.dll").write_bytes(b"the player's own dx8vb")
    run(eng, "uninstall")
    assert (lib / "Entombed" / "dx8vb.dll").read_bytes() == b"the player's own dx8vb"
    assert not (lib / "Entombed" / "entombed.exe").exists()


def test_repair_recreates_a_missing_copy(tmp_path):
    eng, lib, rt, g = dx8vb_catalog(tmp_path)
    run(eng, "install")
    (lib / "Entombed" / "dx8vb.dll").unlink()
    r = run(eng, "repair")
    assert (lib / "Entombed" / "dx8vb.dll").read_bytes() == DX8VB and r.notes == []
    assert eng.ctx.records.load("entombed").files["dx8vb.dll"].owner == OWNER


def test_update_drops_the_copy_when_the_game_no_longer_needs_the_runtime(tmp_path):
    eng, lib, rt, g = dx8vb_catalog(tmp_path)
    run(eng, "install")
    eng.ctx.entries["entombed"] = game(eng.ctx.fetcher.routes, files={"entombed.exe": b"exe 2"}, version="2.0",
                                       dependencies=())
    run(eng, "update")
    folder = lib / "Entombed"
    assert (folder / "entombed.exe").read_bytes() == b"exe 2" and not (folder / "dx8vb.dll").exists()
    assert "dx8vb.dll" not in eng.ctx.records.load("entombed").files

    run(eng, "undo")  # the previous version needed it: back it comes
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB
    assert eng.ctx.records.load("entombed").files["dx8vb.dll"].owner == OWNER


def test_update_keeps_a_changed_copy_the_game_no_longer_needs(tmp_path):
    eng, lib, rt, g = dx8vb_catalog(tmp_path)
    run(eng, "install")
    (lib / "Entombed" / "dx8vb.dll").write_bytes(b"the player's own dx8vb")
    eng.ctx.entries["entombed"] = game(eng.ctx.fetcher.routes, files={"entombed.exe": b"exe 2"}, version="2.0",
                                       dependencies=())
    run(eng, "update")
    assert (lib / "Entombed" / "dx8vb.dll").read_bytes() == b"the player's own dx8vb"


def test_update_adds_the_copies_of_a_new_dependency(tmp_path):
    routes = {}
    eng, lib = setup(tmp_path, [dx8vb_entry(routes), game(routes, dependencies=())], routes)
    run(eng, "install")
    assert not (lib / "Entombed" / "dx8vb.dll").exists()
    eng.ctx.entries["entombed"] = game(eng.ctx.fetcher.routes, files={"entombed.exe": b"exe 2"}, version="2.0")
    run(eng, "update")
    assert (lib / "Entombed" / "dx8vb.dll").read_bytes() == DX8VB


def test_extras_never_claim_the_copy(tmp_path):
    routes = {}
    x_data = zip_bytes({"maps/extra.map": b"x"})
    x = {"id": "ent-maps", "kind": "extra", "title": "Maps", "extends": ["entombed"], "version": "1",
         "sources": [served_source(routes, f"{MIRROR}{sha(x_data)}/maps.zip", x_data)]}
    eng, lib = setup(tmp_path, [dx8vb_entry(routes), x, game(routes, extras=[{"id": "ent-maps"}])], routes)
    run(eng, "install")
    run(eng, "extras", extras=("ent-maps",))
    rec = eng.ctx.records.load("entombed")
    assert rec.files["dx8vb.dll"].owner == OWNER and rec.files["maps/extra.map"].owner == "ent-maps"
    assert (lib / "Entombed" / "dx8vb.dll").read_bytes() == DX8VB


# ---- the preference off ----------------------------------------------------------------------------------


def test_private_copy_is_recorded_as_the_runtimes(tmp_path, private_com):
    eng, lib, rt, g = dx8vb_catalog(tmp_path, register_shared_components=False)
    r = run(eng, "install")
    folder = lib / "Entombed"
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB and (folder / "entombed.exe.manifest").exists()
    assert r.notes == [] and eng.ctx.records.load("entombed").files["dx8vb.dll"].owner == OWNER


def test_private_copy_that_cant_be_used_still_leaves_the_dll_beside_the_exe(tmp_path, private_com, monkeypatch):
    monkeypatch.setattr(comfree, "has_embedded_manifest", lambda exe: True)
    eng, lib, rt, g = dx8vb_catalog(tmp_path, register_shared_components=False)
    r = run(eng, "install")
    folder = lib / "Entombed"
    assert r.notes == [messages.NEEDS_SHARED_COMPONENT.format(title="Entombed", component=DX8VB_TITLE)]
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB and not (folder / "entombed.exe.manifest").exists()
    rec = eng.ctx.records.load("entombed")
    assert rec.files["dx8vb.dll"].owner == OWNER and rec.private_runtimes == []

    r = run(eng, "repair")  # nothing to copy it from now: the unchanged copy stays
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB and r.notes == []
    assert eng.ctx.records.load("entombed").files["dx8vb.dll"].owner == OWNER


def test_private_staging_copy_changed_after_unpacking_isnt_copied(tmp_path, private_com, monkeypatch):
    monkeypatch.setattr(comfree, "has_embedded_manifest", lambda exe: True)
    real = runtimes.finish_private

    def swap_after(tree, exe, copies, title):
        result = real(tree, exe, copies, title)
        for copy in copies:
            (copy.root / "dx8vb.dll").write_bytes(b"MZ swapped in staging")
        return result
    monkeypatch.setattr(runtimes, "finish_private", swap_after)
    eng, lib, rt, g = dx8vb_catalog(tmp_path, register_shared_components=False)
    r = run(eng, "install")
    assert not_copied() in r.notes and not (lib / "Entombed" / "dx8vb.dll").exists()


def test_private_mode_copies_a_runtime_installed_shared_earlier(tmp_path, private_com):
    routes = {}
    rt, g = dx8vb_entry(routes), game(routes)
    two = game(routes, id="pawprints", title="Pawprints", files={"pawprints.exe": b"paw"})
    eng, lib = setup(tmp_path, [rt, g, two], routes)
    run(eng, "install")
    eng.ctx.register_shared_components = False
    r = run(eng, "install", id="pawprints")
    assert (lib / "Pawprints" / "dx8vb.dll").read_bytes() == DX8VB and r.notes == []
    assert not (lib / "Pawprints" / "pawprints.exe.manifest").exists()


# ---- games whose installer runs ----------------------------------------------------------------------------


def light_cars(tmp_path, files=None, **ctx):
    """dx8vb and Light Cars, whose installer writes `files` (writes_game's by default) into its folder."""
    routes = {}
    folder = tmp_path / "Games" / "Light Cars"
    eng, lib = setup(tmp_path, [dx8vb_entry(routes), run_mode_game(routes)], routes,
                     elevator=FakeElevator(on_start=writes_game(folder, files)), **ctx)
    return eng, folder


def test_installer_run_game_gets_the_copy_once_its_exe_is_known(tmp_path):
    eng, folder = light_cars(tmp_path, {"bin/lightcars.exe": b"lc"})
    r = run(eng, "install", id="lightcars")
    assert r.notes == [] and (folder / "bin" / "dx8vb.dll").read_bytes() == DX8VB
    rec = eng.ctx.records.load("lightcars")
    assert rec.launch_path == "bin/lightcars.exe" and rec.files["bin/dx8vb.dll"].owner == OWNER

    (folder / "bin" / "dx8vb.dll").unlink()
    eng.ctx.elevator.on_start = writes_game(folder, {"bin/lightcars.exe": b"lc"})
    run(eng, "repair", id="lightcars")
    assert (folder / "bin" / "dx8vb.dll").read_bytes() == DX8VB
    assert eng.ctx.records.load("lightcars").files["bin/dx8vb.dll"].owner == OWNER

    run(eng, "uninstall", id="lightcars")
    assert not folder.exists()


def test_installer_run_game_update_drops_the_copy_when_it_no_longer_needs_the_runtime(tmp_path):
    eng, folder = light_cars(tmp_path)
    run(eng, "install", id="lightcars")
    assert (folder / "dx8vb.dll").exists()
    eng.ctx.entries["lightcars"] = run_mode_game(eng.ctx.fetcher.routes, setup_exe=b"MZ setup v2", version="2.0",
                                                 dependencies=[])
    eng.ctx.elevator.on_start = writes_game(folder, {"lightcars.exe": b"lc v2", "data/track.dat": b"t2"})
    run(eng, "update", id="lightcars")
    assert not (folder / "dx8vb.dll").exists()
    assert not any(f.owner.startswith("runtime:") for f in eng.ctx.records.load("lightcars").files.values())


def test_installer_run_game_ships_its_own_copy(tmp_path):
    eng, folder = light_cars(tmp_path, {"lightcars.exe": b"lc", "dx8vb.dll": b"its own"})
    run(eng, "install", id="lightcars")
    assert (folder / "dx8vb.dll").read_bytes() == b"its own"
    assert eng.ctx.records.load("lightcars").files["dx8vb.dll"].owner == "lightcars"


@pytest.mark.parametrize("action", ["update", "repair", "undo"])
@pytest.mark.parametrize("shared_folder", ["gone", "untrusted"])
@pytest.mark.parametrize("shared_com", [True, False], ids=["shared-com", "private-com"])
def test_installer_run_game_keeps_the_copy_it_has_nothing_to_copy_from(tmp_path, private_com, monkeypatch, action,
                                                                         shared_folder, shared_com):
    monkeypatch.setenv("GC_DXLIB", str(tmp_path / "system"))
    routes = {}
    rt = {**plain_runtime(routes, "dxlib"), "detect": {"file": "%GC_DXLIB%\\dxlib.dll"}}
    folder = tmp_path / "Games" / "Light Cars"
    eng, lib = setup(tmp_path, [dx8vb_entry(routes), rt, run_mode_game(routes, dependencies=["dx8vb", "dxlib"])],
                     routes, register_shared_components=shared_com, elevator=FakeElevator(on_start=writes_game(folder)))
    def publish(version):
        eng.ctx.entries["lightcars"] = run_mode_game(eng.ctx.fetcher.routes, setup_exe=version.encode(),
                                                     version=version, dependencies=["dx8vb", "dxlib"])
    run(eng, "install", id="lightcars")
    if action == "undo":
        publish("2.0")
        run(eng, "update", id="lightcars")
    assert (folder / "dxlib.dll").read_bytes() == b"dxlib"
    runtime_folder = eng.ctx.runtimes_root / "dxlib"
    if shared_folder == "gone":
        shutil.rmtree(runtime_folder)
    else:
        eng.ctx.security.set_owner(runtime_folder, "S-1-5-21-3000-3000-3000-1003")  # another account's folder
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "dxlib.dll").write_bytes(b"dxlib")  # present by its detect hint
    if action == "update":
        publish("3.0")
    run(eng, action, id="lightcars")
    assert (folder / "dxlib.dll").read_bytes() == b"dxlib"
    assert eng.ctx.records.load("lightcars").files["dxlib.dll"].owner == "runtime:dxlib"


@pytest.mark.parametrize("action", ["repair", "update"])
@pytest.mark.parametrize("shared_folder", ["gone", "untrusted"])
def test_a_copy_with_nothing_to_copy_from_that_cant_be_kept_gets_a_note(tmp_path, monkeypatch, action, shared_folder):
    monkeypatch.setenv("GC_DXLIB", str(tmp_path / "system"))
    routes = {}
    rt = {**layered_runtime(routes), "detect": {"file": "%GC_DXLIB%\\dxlib.dll"}}
    eng, lib = setup(tmp_path, [rt, game(routes, dependencies=["dxlib"])], routes)
    run(eng, "install")
    if shared_folder == "gone":
        shutil.rmtree(eng.ctx.runtimes_root / "dxlib")
    else:
        eng.ctx.security.set_owner(eng.ctx.runtimes_root / "dxlib", "S-1-5-21-3000-3000-3000-1003")
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "dxlib.dll").write_bytes(b"MZ dxlib")  # present by its detect hint
    real = runtimes._copy_new

    def full_for_helper(src, dest):
        if src.name == "helper.dll":
            raise OSError(28, "No space left on device", str(dest))
        return real(src, dest)
    monkeypatch.setattr(runtimes, "_copy_new", full_for_helper)
    if action == "update":
        eng.ctx.entries["entombed"] = game(eng.ctx.fetcher.routes, files={"entombed.exe": b"exe 2"}, version="2.0",
                                           dependencies=["dxlib"])
    r = run(eng, action)
    folder = lib / "Entombed"
    assert (folder / "dxlib.dll").exists() and not (folder / "lib" / "helper.dll").exists()
    assert r.notes == [not_copied(component="DXLIB")]


@pytest.mark.parametrize("action", ["repair", "update", "undo"])
def test_a_copy_the_installer_removes_with_nothing_to_copy_from_gets_a_note(tmp_path, monkeypatch, action):
    monkeypatch.setenv("GC_DXLIB", str(tmp_path / "system"))
    routes = {}
    rt = {**layered_runtime(routes), "detect": {"file": "%GC_DXLIB%\\dxlib.dll"}}
    folder = tmp_path / "Games" / "Light Cars"
    eng, lib = setup(tmp_path, [rt, run_mode_game(routes, dependencies=["dxlib"])], routes,
                     elevator=FakeElevator(on_start=writes_game(folder)))
    run(eng, "install", id="lightcars")
    for version in {"repair": [], "update": ["2.0"], "undo": ["2.0"]}[action]:
        eng.ctx.entries["lightcars"] = run_mode_game(eng.ctx.fetcher.routes, setup_exe=version.encode(),
                                                     version=version, dependencies=["dxlib"])
    if action == "undo":
        run(eng, "update", id="lightcars")
    assert (folder / "lib" / "helper.dll").exists()
    shutil.rmtree(eng.ctx.runtimes_root / "dxlib")
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "dxlib.dll").write_bytes(b"MZ dxlib")  # present by its detect hint
    writes = writes_game(folder)

    def removes_the_copy(exe, args, elevated):
        writes(exe, args, elevated)
        (folder / "lib" / "helper.dll").unlink(missing_ok=True)
    eng.ctx.elevator.on_start = removes_the_copy
    r = run(eng, action, id="lightcars")
    assert not (folder / "lib" / "helper.dll").exists() and not_copied("Light Cars", "DXLIB") in r.notes


def test_untracked_installer_run_game_gets_a_note_instead(tmp_path, monkeypatch):
    monkeypatch.setattr(run_installer, "CLOCK_SLACK", 0.0)
    routes = {}
    rt, g = dx8vb_entry(routes), run_mode_game(routes, silent=False)
    chosen = tmp_path / "Somewhere" / "LC"
    chosen.mkdir(parents=True)
    (chosen / "lc.exe").write_bytes(b"exe")
    time.sleep(0.05)
    eng, lib = setup(tmp_path, [rt, g], routes, installer_ui=FakeInstallerUI(locate=chosen))
    r = run(eng, "install", id="lightcars")
    assert not eng.ctx.records.load("lightcars").tracked
    assert messages.RUNTIME_FILES_UNTRACKED.format(title="Light Cars", component=DX8VB_TITLE) in r.notes
    assert sorted(p.name for p in chosen.iterdir()) == ["lc.exe"]


def test_installer_run_game_in_private_mode_with_an_unusable_copy_still_gets_the_dll(tmp_path, private_com,
                                                                                      monkeypatch):
    monkeypatch.setattr(comfree, "has_embedded_manifest", lambda exe: True)
    eng, folder = light_cars(tmp_path, register_shared_components=False)
    r = run(eng, "install", id="lightcars")
    assert r.notes == [messages.NEEDS_SHARED_COMPONENT.format(title="Light Cars", component=DX8VB_TITLE)]
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB and not (folder / "lightcars.exe.manifest").exists()
    assert eng.ctx.records.load("lightcars").files["dx8vb.dll"].owner == OWNER


def test_installer_run_game_whose_private_runtime_changes_gets_the_new_ones_copies_and_manifest(tmp_path,
                                                                                                private_com):
    routes = {}
    data = b"MZ dx9vb"
    dx9vb = {"id": "dx9vb", "kind": "runtime", "title": "DX9", "version": "1",
             "sources": [served_source(routes, f"{MIRROR}{sha(data)}/dx9vb.dll", data, method="none",
                                       steps=[{"action": "register", "path": "dx9vb.dll"}])],
             "detect": {"registry": {"hive": "HKCR", "key": "DxVBLibA.DirectX9"}}}
    folder = tmp_path / "Games" / "Light Cars"
    eng, lib = setup(tmp_path, [dx8vb_entry(routes), dx9vb, run_mode_game(routes)], routes,
                     register_shared_components=False, elevator=FakeElevator(on_start=writes_game(folder)))
    run(eng, "install", id="lightcars")
    eng.ctx.entries["lightcars"] = run_mode_game(eng.ctx.fetcher.routes, setup_exe=b"MZ setup v2", version="2.0",
                                                 dependencies=["dx9vb"])
    run(eng, "update", id="lightcars")
    rec = eng.ctx.records.load("lightcars")
    assert not (folder / "dx8vb.dll").exists() and rec.private_runtimes == ["dx9vb"]
    assert rec.files["dx9vb.dll"].owner == rec.files["lightcars.exe.manifest"].owner == "runtime:dx9vb"
