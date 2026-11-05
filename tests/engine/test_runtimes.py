"""Dependencies: runtimes installed before the game, shared machine-wide or as a private COM copy."""

import hashlib
import sys
import time
from pathlib import Path
from xml.dom import minidom

import pytest

from engine import comfree, messages
from engine.comfree import PrivateComUnavailable, com_manifest_xml, has_embedded_manifest, write_private_com
from engine.errors import InstallFailed
from engine.jobs import InstallRequest
from engine.messages import Progress
from engine.runtimes import (default_runtimes_root, detect_present, is_com_runtime, missing_runtimes,
                             runtime_present)
from tests.engine.fakes import FakeElevator, FakeFetcher, FakeRegistry, make_engine, served_source, zip_bytes

MIRROR = "https://files.accessiblegames.net/files/"
DX8VB = b"MZ fake dx8vb.dll"
DX8VB_TITLE = "DirectX 8 for Visual Basic"
DX8VB_CLSID = "{E7FF1300-96A5-11D3-AC85-00C04FC2C602}"
DX8VB_TLBID = "{E1211242-8E94-11D1-8808-00C04FC2C603}"
DX8VB_KEY = r"SOFTWARE\Classes\DxVBLibA.DirectX8"
VB6_TITLE = "Visual Basic 6 runtime"
SETUP = b"MZ fake vbrun60 setup"


def never():
    return False


def quiet(p):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dx8vb(routes, **fields):
    """The shared COM component: the loose DLL plus a register step."""
    source = served_source(routes, f"{MIRROR}{sha(DX8VB)}/dx8vb.dll", DX8VB, method="none",
                           steps=[{"action": "register", "path": "dx8vb.dll"}])
    return {"id": "dx8vb", "kind": "runtime", "title": DX8VB_TITLE, "version": "1", "sources": [source],
            "detect": {"registry": {"hive": "HKCR", "key": "DxVBLibA.DirectX8"}}, **fields}


def vb6(routes, **fields):
    """A runtime that only exists as an installer that must run."""
    source = served_source(routes, f"{MIRROR}{sha(SETUP)}/vbrun60.exe", SETUP, method="installer",
                           installer={"type": "other", "mode": "run", "silent_args": "/Q"})
    return {"id": "vb6-runtime", "kind": "runtime", "title": VB6_TITLE, "version": "6", "sources": [source],
            "detect": {"file": "%GC_VB6%\\msvbvm60.dll"}, **fields}


def plain_runtime(routes, id, dependencies=()):
    data = zip_bytes({f"{id}.dll": id.encode()})
    source = served_source(routes, f"{MIRROR}{sha(data)}/{id}.zip", data)
    return {"id": id, "kind": "runtime", "title": id.upper(), "version": "1", "sources": [source],
            "dependencies": list(dependencies)}


def game(routes, id="entombed", title="Entombed", files=None, dependencies=("dx8vb",), version="1.0", **fields):
    files = files if files is not None else {"entombed.exe": b"exe", "sounds/a.wav": b"wav"}
    data = zip_bytes(files)
    source = served_source(routes, f"{MIRROR}{sha(data)}/{id}.zip", data)
    return {"id": id, "kind": "game", "title": title, "developer": ["Ghorse"], "genres": ["Arcade"],
            "year": 2008, "version": version, "sources": [source], "dependencies": list(dependencies), **fields}


def setup(tmp_path, entries, routes, **ctx):
    eng = make_engine(tmp_path, entries, fetcher=FakeFetcher(routes), **ctx)
    return eng, tmp_path / "Games"


def run(eng, action, id="entombed", seen=None, **req):
    return eng.run(InstallRequest(action, id, "Games", **req), seen.append if seen is not None else quiet, never)


def regsvr32(path: Path) -> tuple[str, str, bool]:
    return "regsvr32.exe", f'/s "{path}"', True


def fake_reader(dll):
    return [{"clsid": DX8VB_CLSID, "progid": "DxVBLibA.DirectX8"}], {"tlbid": DX8VB_TLBID, "version": "1.0"}


@pytest.fixture
def private_com(monkeypatch):
    """Preference off: the type library reader is faked (the test DLL isn't a real one)."""
    monkeypatch.setattr(comfree, "read_com_info", fake_reader)


# ---- detecting what's present --------------------------------------------------------------------------


def test_default_runtimes_root(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    assert default_runtimes_root() == tmp_path / "GameChooser" / "runtimes"


def test_detect_file_with_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("GC_TEST", str(tmp_path))
    assert not detect_present({"file": "%GC_TEST%\\x.dll"}, FakeRegistry())
    (tmp_path / "x.dll").write_bytes(b"dll")
    assert detect_present({"file": "%GC_TEST%\\x.dll"}, FakeRegistry())


def test_detect_registry_key_value_and_classes_root():
    registry = FakeRegistry()
    assert not detect_present({"registry": {"hive": "HKCR", "key": "DxVBLibA.DirectX8"}}, registry)
    registry.set("HKLM", DX8VB_KEY, "", "REG_SZ", "DirectX8")
    assert detect_present({"registry": {"hive": "HKCR", "key": "DxVBLibA.DirectX8"}}, registry)
    per_user = FakeRegistry()
    per_user.set("HKCU", DX8VB_KEY, "", "REG_SZ", "DirectX8")
    assert detect_present({"registry": {"hive": "HKCR", "key": "DxVBLibA.DirectX8"}}, per_user)
    registry.set("HKLM", r"SOFTWARE\VB6", "Installed", "REG_DWORD", 1)
    assert detect_present({"registry": {"hive": "HKLM", "key": r"SOFTWARE\VB6", "name": "Installed"}}, registry)
    assert not detect_present({"registry": {"hive": "HKLM", "key": r"SOFTWARE\VB6", "name": "Other"}}, registry)


def test_detect_unknown_shape_is_absent():
    assert not detect_present({"wmi": "x"}, FakeRegistry())
    assert not detect_present(None, FakeRegistry())
    assert not detect_present({"file": 3}, FakeRegistry())
    assert not detect_present({"registry": {"hive": "HKXX", "key": "x"}}, _RaisingRegistry())
    assert not detect_present({"registry": "HKLM\\x"}, FakeRegistry())


class _RaisingRegistry(FakeRegistry):
    def get(self, hive, key, name):
        from engine.errors import StepError
        raise StepError(messages.REGISTRY_BAD_SETTING)


def test_is_com_runtime():
    routes = {}
    assert is_com_runtime(dx8vb(routes))
    assert not is_com_runtime(vb6(routes))
    assert not is_com_runtime(plain_runtime(routes, "a"))


def test_runtime_present_by_record_or_detect(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    assert not runtime_present(rt, eng.ctx.records, eng.ctx.registry)
    eng.ctx.registry.set("HKLM", DX8VB_KEY, "", "REG_SZ", "DirectX8")
    assert runtime_present(rt, eng.ctx.records, eng.ctx.registry)


def test_missing_runtimes_deepest_first(tmp_path):
    routes = {}
    a, b, c = plain_runtime(routes, "a", ["b"]), plain_runtime(routes, "b", ["c"]), plain_runtime(routes, "c")
    g = game(routes, dependencies=["a", "c"])
    eng, lib = setup(tmp_path, [a, b, c, g], routes)
    entries = eng.ctx.entries
    assert [r["id"] for r in missing_runtimes(g, entries, eng.ctx.records, eng.ctx.registry)] == ["c", "b", "a"]

    run(eng, "install")
    assert eng.ctx.fetcher.requests == [x["sources"][0]["url"] for x in (c, b, a, g)]
    assert {r.catalog_id for r in eng.ctx.records.all().values() if r.kind == "runtime"} == {"a", "b", "c"}
    assert missing_runtimes(g, entries, eng.ctx.records, eng.ctx.registry) == []


def test_dependency_cycle_refused_plainly(tmp_path):
    routes = {}
    a, b = plain_runtime(routes, "a", ["b"]), plain_runtime(routes, "b", ["a"])
    g = game(routes, dependencies=["a"])
    eng, lib = setup(tmp_path, [a, b, g], routes)
    with pytest.raises(InstallFailed) as ei:
        missing_runtimes(g, eng.ctx.entries, eng.ctx.records, eng.ctx.registry)
    assert ei.value.message == messages.DEPENDENCY_CYCLE.format(title="Entombed")
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.DEPENDENCY_CYCLE.format(title="Entombed")
    assert eng.ctx.fetcher.requests == [] and not (lib / "Entombed").exists()


def test_missing_or_non_runtime_dependency_refused_plainly(tmp_path):
    routes = {}
    other = game(routes, id="other", title="Other", dependencies=())
    g = game(routes, dependencies=["nope"])
    eng, lib = setup(tmp_path, [g, other], routes)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.DEPENDENCY_UNAVAILABLE.format(title="Entombed")
    eng.ctx.entries["entombed"] = game(routes, dependencies=["other"])
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.DEPENDENCY_UNAVAILABLE.format(title="Entombed")
    assert eng.ctx.fetcher.requests == []


# ---- shared runtimes (the preference on) ---------------------------------------------------------------


def test_runtime_installed_once_into_programdata_folder(tmp_path):
    routes = {}
    rt = dx8vb(routes)
    one = game(routes)
    two = game(routes, id="pawprints", title="Pawprints", files={"pawprints.exe": b"paw"})
    eng, lib = setup(tmp_path, [rt, one, two], routes)
    runtimes_root = eng.ctx.runtimes_root
    seen = []
    r = run(eng, "install", seen=seen)
    run(eng, "install", id="pawprints")

    assert (runtimes_root / "dx8vb" / "dx8vb.dll").exists()
    fake_elev = eng.ctx.elevator
    assert fake_elev.calls.count(("regsvr32.exe", f'/s "{runtimes_root / "dx8vb" / "dx8vb.dll"}"', True)) == 1
    assert eng.ctx.fetcher.requests.count(rt["sources"][0]["url"]) == 1
    assert Progress("installing_dependency", detail=DX8VB_TITLE) in seen
    rec = eng.ctx.records.load("dx8vb")
    assert rec.kind == "runtime" and rec.library_name == "" and Path(rec.folder) == runtimes_root / "dx8vb"
    assert set(rec.files) == {"dx8vb.dll"} and rec.files["dx8vb.dll"].owner == "dx8vb"
    assert rec.steps == [{"action": "register", "path": "dx8vb.dll"}] and rec.method == "none"
    assert (lib / "Entombed" / "entombed.exe").exists() and (lib / "Pawprints" / "pawprints.exe").exists()
    assert not (lib / "Entombed" / "dx8vb.dll").exists() and r.notes == []
    assert not (eng.ctx.work_root / "entombed").exists()


def test_runtime_steps_run_before_the_game_is_downloaded(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    order = []

    class Recording(FakeFetcher):
        def download(self, url, dest, progress=None, cancel=None):
            order.append(url)
            super().download(url, dest, progress, cancel)

    elevator = FakeElevator(on_start=lambda exe, args, elevated: order.append(exe))
    eng, lib = setup(tmp_path, [rt, g], routes, elevator=elevator)
    eng.ctx.fetcher = Recording(routes)
    run(eng, "install")
    assert order == [rt["sources"][0]["url"], "regsvr32.exe", g["sources"][0]["url"]]


def test_present_runtime_is_skipped(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    eng.ctx.registry.set("HKLM", DX8VB_KEY, "", "REG_SZ", "DirectX8")
    r = run(eng, "install")
    assert eng.ctx.fetcher.requests == [g["sources"][0]["url"]]
    assert eng.ctx.elevator.calls == [] and eng.ctx.records.load("dx8vb") is None
    assert (lib / "Entombed" / "entombed.exe").exists() and r.notes == []


def test_uninstall_never_removes_runtimes(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    run(eng, "install")
    runtime = eng.ctx.records.load("dx8vb")
    run(eng, "uninstall")
    assert not (lib / "Entombed").exists() and eng.ctx.records.load("entombed") is None
    assert (eng.ctx.runtimes_root / "dx8vb" / "dx8vb.dll").read_bytes() == DX8VB
    assert eng.ctx.records.load("dx8vb") == runtime
    assert not any(args.startswith("/u") for _, args, _ in eng.ctx.elevator.calls)


def test_declined_elevation_fails_install_plainly(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(decline=True))
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.ELEVATION_DECLINED.format(what=DX8VB_TITLE)
    assert eng.ctx.records.load("dx8vb") is None and eng.ctx.records.load("entombed") is None
    assert not (eng.ctx.runtimes_root / "dx8vb").exists() and not (lib / "Entombed").exists()
    assert g["sources"][0]["url"] not in eng.ctx.fetcher.requests


def test_failed_register_fails_install_and_leaves_no_runtime(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(exit_codes={"regsvr32.exe": 3}))
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.STEP_REGISTER_FAILED.format(name="dx8vb.dll")
    assert eng.ctx.records.load("dx8vb") is None and not (eng.ctx.runtimes_root / "dx8vb").exists()


def test_leftover_runtime_folder_without_record_is_replaced(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    stale = eng.ctx.runtimes_root / "dx8vb" / "stale.dll"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"from a failed try")
    run(eng, "install")
    assert not stale.exists() and set(eng.ctx.records.load("dx8vb").files) == {"dx8vb.dll"}


def test_installer_runtime_runs_untracked_then_is_checked(tmp_path, monkeypatch):
    routes = {}
    rt, g = vb6(routes), game(routes, dependencies=["vb6-runtime"])
    system = tmp_path / "system"
    system.mkdir()
    monkeypatch.setenv("GC_VB6", str(system))

    def installs(exe, args, elevated):
        if exe.endswith("vbrun60.exe"):
            (system / "msvbvm60.dll").write_bytes(b"vb")

    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(on_start=installs))
    run(eng, "install")
    calls = [c for c in eng.ctx.elevator.calls if c[0].endswith("vbrun60.exe")]
    assert len(calls) == 1 and calls[0][1:] == ("/Q", False)
    rec = eng.ctx.records.load("vb6-runtime")
    assert rec.kind == "runtime" and rec.method == "run" and not rec.tracked and rec.files == {}
    assert (lib / "Entombed" / "entombed.exe").exists()


def test_installer_runtime_still_missing_fails(tmp_path, monkeypatch):
    routes = {}
    rt, g = vb6(routes), game(routes, dependencies=["vb6-runtime"])
    monkeypatch.setenv("GC_VB6", str(tmp_path / "system"))
    eng, lib = setup(tmp_path, [rt, g], routes)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.FAILED.format(title=VB6_TITLE)
    assert eng.ctx.records.load("vb6-runtime") is None and not (lib / "Entombed").exists()


def test_run_mode_game_installs_runtime_first(tmp_path):
    routes = {}
    rt = dx8vb(routes)
    setup_exe = b"MZ fake game setup"
    source = served_source(routes, f"{MIRROR}{sha(setup_exe)}/setup.exe", setup_exe, method="installer",
                           installer={"type": "other", "mode": "run", "silent_args": "/S"})
    g = {"id": "lightcars", "kind": "game", "title": "Light Cars", "developer": ["Draconis"], "genres": ["Racing"],
         "year": 2007, "version": "1.0", "sources": [source], "dependencies": ["dx8vb"]}
    lib = tmp_path / "Games"

    def installs(exe, args, elevated):
        if exe.endswith("setup.exe"):
            time.sleep(0.05)
            (lib / "Light Cars").mkdir(parents=True, exist_ok=True)
            (lib / "Light Cars" / "lightcars.exe").write_bytes(b"lc")
            time.sleep(0.05)

    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(on_start=installs))
    run(eng, "install", id="lightcars")
    exes = [c[0] for c in eng.ctx.elevator.calls]
    assert exes[0] == "regsvr32.exe" and exes[1].endswith("setup.exe")
    assert eng.ctx.records.load("dx8vb").kind == "runtime" and eng.ctx.records.load("lightcars").tracked


def test_update_installs_missing_runtime_first(tmp_path):
    routes = {}
    rt = dx8vb(routes)
    eng, lib = setup(tmp_path, [rt, game(routes, dependencies=())], routes)
    run(eng, "install")
    assert eng.ctx.elevator.calls == []
    eng.ctx.entries["entombed"] = game(eng.ctx.fetcher.routes, files={"entombed.exe": b"exe 2"}, version="2.0")
    r = run(eng, "update")
    assert eng.ctx.elevator.calls == [regsvr32(eng.ctx.runtimes_root / "dx8vb" / "dx8vb.dll")]
    assert (lib / "Entombed" / "entombed.exe").read_bytes() == b"exe 2" and r.notes == []


# ---- the preference off --------------------------------------------------------------------------------


def test_preference_off_private_copy_and_manifest(tmp_path, private_com):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    r = run(eng, "install")
    folder = lib / "Entombed"
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB
    manifest = (folder / "entombed.exe.manifest").read_text(encoding="utf-8")
    assert DX8VB_CLSID in manifest and '<file name="dx8vb.dll">' in manifest
    assert eng.ctx.elevator.calls == [] and r.notes == []
    assert eng.ctx.records.load("dx8vb") is None and not (eng.ctx.runtimes_root / "dx8vb").exists()
    rec = eng.ctx.records.load("entombed")
    assert {"dx8vb.dll", "entombed.exe.manifest"} <= set(rec.files) and rec.launch_path == "entombed.exe"
    assert not (eng.ctx.work_root / "entombed").exists()


def test_preference_off_private_copy_goes_beside_an_exe_in_a_subfolder(tmp_path, private_com):
    routes = {}
    rt = dx8vb(routes)
    g = game(routes, files={"bin/entombed.exe": b"exe", "sounds/a.wav": b"wav"})
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    run(eng, "install")
    folder = lib / "Entombed"
    assert (folder / "bin" / "dx8vb.dll").exists() and (folder / "bin" / "entombed.exe.manifest").exists()
    assert not (folder / "dx8vb.dll").exists()
    assert eng.ctx.records.load("entombed").launch_path == "bin/entombed.exe"


def test_preference_off_embedded_manifest_notes_and_installs(tmp_path, private_com, monkeypatch):
    monkeypatch.setattr(comfree, "has_embedded_manifest", lambda exe: True)
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    r = run(eng, "install")
    folder = lib / "Entombed"
    assert r.notes == [messages.NEEDS_SHARED_COMPONENT.format(title="Entombed", component=DX8VB_TITLE)]
    assert (folder / "entombed.exe").exists() and not (folder / "dx8vb.dll").exists()
    assert not (folder / "entombed.exe.manifest").exists() and eng.ctx.elevator.calls == []
    assert set(eng.ctx.records.load("entombed").files) == {"entombed.exe", "sounds/a.wav"}


def test_preference_off_private_com_not_supported_notes_and_installs(tmp_path, private_com, monkeypatch):
    monkeypatch.setattr(comfree, "PRIVATE_COM_SUPPORTED", False)
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    r = run(eng, "install")
    assert r.notes == [messages.NEEDS_SHARED_COMPONENT.format(title="Entombed", component=DX8VB_TITLE)]
    assert not (lib / "Entombed" / "dx8vb.dll").exists() and eng.ctx.elevator.calls == []


def test_preference_off_game_shipping_its_own_copy_keeps_it(tmp_path, private_com):
    routes = {}
    rt = dx8vb(routes)
    g = game(routes, files={"entombed.exe": b"exe", "dx8vb.dll": b"the game's own copy"})
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    run(eng, "install")
    assert (lib / "Entombed" / "dx8vb.dll").read_bytes() == b"the game's own copy"
    assert (lib / "Entombed" / "entombed.exe.manifest").exists()


def test_preference_off_installer_runtime_is_skipped_with_note(tmp_path, monkeypatch):
    monkeypatch.setenv("GC_VB6", str(tmp_path / "system"))
    routes = {}
    rt, g = vb6(routes), game(routes, dependencies=["vb6-runtime"])
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    r = run(eng, "install")
    assert r.notes == [messages.NEEDS_SHARED_COMPONENT.format(title="Entombed", component=VB6_TITLE)]
    assert eng.ctx.fetcher.requests == [g["sources"][0]["url"]] and eng.ctx.elevator.calls == []
    assert (lib / "Entombed" / "entombed.exe").exists()


def test_preference_off_non_com_runtime_still_installs_shared(tmp_path):
    routes = {}
    rt = plain_runtime(routes, "bass")
    g = game(routes, dependencies=["bass"])
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    run(eng, "install")
    assert (eng.ctx.runtimes_root / "bass" / "bass.dll").exists() and eng.ctx.records.load("bass") is not None


def test_preference_off_run_mode_game_notes_com_runtime(tmp_path, private_com):
    routes = {}
    rt = dx8vb(routes)
    setup_exe = b"MZ fake game setup"
    source = served_source(routes, f"{MIRROR}{sha(setup_exe)}/setup.exe", setup_exe, method="installer",
                           installer={"type": "other", "mode": "run", "silent_args": "/S"})
    g = {"id": "lightcars", "kind": "game", "title": "Light Cars", "developer": ["Draconis"], "genres": ["Racing"],
         "year": 2007, "version": "1.0", "sources": [source], "dependencies": ["dx8vb"]}
    lib = tmp_path / "Games"

    def installs(exe, args, elevated):
        time.sleep(0.05)
        (lib / "Light Cars").mkdir(parents=True, exist_ok=True)
        (lib / "Light Cars" / "lightcars.exe").write_bytes(b"lc")
        time.sleep(0.05)

    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False,
                     elevator=FakeElevator(on_start=installs))
    r = run(eng, "install", id="lightcars")
    assert messages.NEEDS_SHARED_COMPONENT.format(title="Light Cars", component=DX8VB_TITLE) in r.notes
    assert [c[0] for c in eng.ctx.elevator.calls if c[0] == "regsvr32.exe"] == []
    assert rt["sources"][0]["url"] not in eng.ctx.fetcher.requests


def test_preference_off_repair_and_update_keep_the_private_copy(tmp_path, private_com):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    run(eng, "install")
    folder = lib / "Entombed"
    run(eng, "repair")
    assert (folder / "dx8vb.dll").exists() and (folder / "entombed.exe.manifest").exists()
    v2 = {"entombed.exe": b"exe 2", "sounds/a.wav": b"wav"}
    eng.ctx.entries["entombed"] = game(eng.ctx.fetcher.routes, files=v2, version="2.0")
    run(eng, "update")
    assert (folder / "dx8vb.dll").exists() and (folder / "entombed.exe.manifest").exists()
    run(eng, "undo")
    assert (folder / "dx8vb.dll").exists() and (folder / "entombed.exe.manifest").exists()
    assert {"dx8vb.dll", "entombed.exe.manifest"} <= set(eng.ctx.records.load("entombed").files)
    assert eng.ctx.elevator.calls == []


def test_repair_keeps_a_private_copy_after_the_preference_is_turned_on(tmp_path, private_com):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    run(eng, "install")
    eng.ctx.register_shared_components = True
    run(eng, "repair")
    folder = lib / "Entombed"
    assert (folder / "dx8vb.dll").exists() and (folder / "entombed.exe.manifest").exists()
    assert eng.ctx.elevator.calls == [] and eng.ctx.records.load("dx8vb") is None


# ---- the manifest --------------------------------------------------------------------------------------


def test_com_manifest_xml_shape():
    xml = com_manifest_xml([("dx8vb.dll", [{"clsid": "{E1211242-8E94-11D1-8808-00C04FC2C602}",
                                            "progid": "DxVBLibA.DirectX8"}],
                             {"tlbid": "{E1211242-8E94-11D1-8808-00C04FC2C603}", "version": "1.0"})])
    assert '<file name="dx8vb.dll">' in xml and 'threadingModel="Apartment"' in xml
    assert 'tlbid="{E1211242-8E94-11D1-8808-00C04FC2C603}"' in xml
    assert xml.startswith('<?xml version="1.0"') and 'manifestVersion="1.0"' in xml
    assert minidom.parseString(xml).documentElement.tagName == "assembly"  # well formed


def test_write_private_com_refuses_and_writes_nothing(tmp_path):
    exe = tmp_path / "game.exe"
    exe.write_bytes(b"exe")
    dll = tmp_path / "dx8vb.dll"
    dll.write_bytes(DX8VB)
    elsewhere = tmp_path / "sub" / "x.dll"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"x")

    def broken(path):
        raise OSError("no type library")

    for dlls, reader in (([elsewhere], fake_reader), ([dll], broken), ([dll], lambda p: ([], {}))):
        with pytest.raises(PrivateComUnavailable):
            write_private_com(tmp_path, exe, dlls, reader=reader)
        assert not (tmp_path / "game.exe.manifest").exists()
    (tmp_path / "game.exe.manifest").write_text("the game's own")
    with pytest.raises(PrivateComUnavailable):
        write_private_com(tmp_path, exe, [dll], reader=fake_reader)
    assert (tmp_path / "game.exe.manifest").read_text() == "the game's own"


def test_has_embedded_manifest_is_false_for_a_file_that_isnt_a_program(tmp_path):
    exe = tmp_path / "game.exe"
    exe.write_bytes(b"not a program")
    assert has_embedded_manifest(exe) is False


@pytest.mark.windows
def test_real_windows_type_library_and_manifest_resources():
    assert has_embedded_manifest(Path(sys.executable)) is True
    dll = Path(r"C:\Windows\SysWOW64\dx8vb.dll")
    if not dll.exists():
        pytest.skip("dx8vb.dll isn't on this machine")
    classes, typelib = comfree.read_com_info(dll)
    assert {"clsid": DX8VB_CLSID, "progid": "DxVBLibA.DirectX8"} in classes
    assert typelib == {"tlbid": DX8VB_TLBID, "version": "1.0"}
