"""Dependencies: runtimes installed before the game, shared machine-wide or as a private COM copy."""

import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
from xml.dom import minidom

import pytest

from engine import comfree, messages, run_installer
from engine.comfree import PrivateComUnavailable, com_manifest_xml, has_embedded_manifest, write_private_com
from engine.errors import InstallFailed
from engine.jobs import InstallRequest
from engine.messages import Progress
from engine.records import RecordedFile, RecordStore
from engine.runtimes import (MARKER_NAME, default_runtimes_root, detect_present, is_com_runtime, lock_down_command,
                             missing_runtimes, recorded_private, runtime_present)
from tests.engine.fakes import (ADMINISTRATORS, FakeElevator, FakeFetcher, FakeInstallerUI, FakeRegistry,
                                FakeSecurity, make_engine, served_source, zip_bytes)

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
    assert order == [rt["sources"][0]["url"], "cmd.exe", "regsvr32.exe", g["sources"][0]["url"]]


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


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def put_marker(folder: Path, files: dict[str, bytes], runtime_id: str = "dx8vb") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        (folder / rel).write_bytes(data)
    (folder / MARKER_NAME).write_text(json.dumps(
        {"id": runtime_id, "source_sha256": "x", "files": {rel: sha(data) for rel, data in files.items()}}))


def test_installed_runtime_leaves_a_marker_other_accounts_read(tmp_path):
    routes = {}
    rt, one = dx8vb(routes), game(routes)
    two = game(routes, id="pawprints", title="Pawprints", files={"pawprints.exe": b"paw"})
    eng, lib = setup(tmp_path, [rt, one, two], routes)
    run(eng, "install")
    folder = eng.ctx.runtimes_root / "dx8vb"
    marker = json.loads((folder / MARKER_NAME).read_text())
    assert marker == {"id": "dx8vb", "source_sha256": sha(DX8VB), "files": {"dx8vb.dll": sha(DX8VB)}}
    assert set(eng.ctx.records.load("dx8vb").files) == {"dx8vb.dll"}  # the marker isn't one of its files

    # Another Windows account: its own records, the same machine-wide runtimes folder.
    other_user = FakeSecurity(eng.ctx.elevator, user="S-1-5-21-2000-2000-2000-1002")  # sees the same lock-down
    other = make_engine(tmp_path, [rt, one, two], fetcher=FakeFetcher(routes), records=RecordStore(tmp_path / "b"),
                        runtimes_root=eng.ctx.runtimes_root, security=other_user)
    run(other, "install", id="pawprints")
    assert other.ctx.fetcher.requests == [two["sources"][0]["url"]] and other.ctx.elevator.calls == []
    assert other.ctx.records.load("dx8vb") is None and (folder / "dx8vb.dll").read_bytes() == DX8VB


def test_existing_marker_counts_as_present_only_while_its_files_are_there(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "dx8vb"
    put_marker(folder, {"dx8vb.dll": DX8VB})
    sec = eng.ctx.security
    sec.set_owner(folder, ADMINISTRATORS)  # locked down by an earlier install
    assert runtime_present(rt, eng.ctx.records, eng.ctx.registry, eng.ctx.runtimes_root, sec)
    assert not runtime_present(rt, eng.ctx.records, eng.ctx.registry)  # no root given: the marker isn't read
    run(eng, "install")
    assert eng.ctx.fetcher.requests == [g["sources"][0]["url"]] and eng.ctx.elevator.calls == []
    (folder / "dx8vb.dll").unlink()
    assert not runtime_present(rt, eng.ctx.records, eng.ctx.registry, eng.ctx.runtimes_root, sec)
    put_marker(folder, {"dx8vb.dll": DX8VB}, runtime_id="other")
    assert not runtime_present(rt, eng.ctx.records, eng.ctx.registry, eng.ctx.runtimes_root, sec)


def test_marker_counts_only_in_a_folder_owned_by_administrators_or_for_a_plain_runtime_the_user(tmp_path):
    routes = {}
    rt, bass = dx8vb(routes), plain_runtime(routes, "bass")
    eng, lib = setup(tmp_path, [rt, bass, game(routes)], routes)
    sec, root = eng.ctx.security, eng.ctx.runtimes_root
    put_marker(root / "dx8vb", {"dx8vb.dll": DX8VB})
    put_marker(root / "bass", {"bass.dll": b"bass"}, runtime_id="bass")
    # Owned by the current user: enough for a runtime with no elevated step, not for one that registers.
    assert not runtime_present(rt, eng.ctx.records, eng.ctx.registry, root, sec)
    assert runtime_present(bass, eng.ctx.records, eng.ctx.registry, root, sec)
    # Owned by another account: never.
    for folder in (root / "dx8vb", root / "bass"):
        sec.set_owner(folder, "S-1-5-21-2000-2000-2000-1002")
    assert not runtime_present(rt, eng.ctx.records, eng.ctx.registry, root, sec)
    assert not runtime_present(bass, eng.ctx.records, eng.ctx.registry, root, sec)


@pytest.mark.windows
def test_marker_reached_through_a_junction_doesnt_count(tmp_path):
    import _winapi
    routes = {}
    rt = dx8vb(routes)
    eng, lib = setup(tmp_path, [rt, game(routes)], routes)
    real = tmp_path / "elsewhere"
    put_marker(real, {"dx8vb.dll": DX8VB})
    eng.ctx.runtimes_root.mkdir(parents=True)
    _winapi.CreateJunction(str(real), str(eng.ctx.runtimes_root / "dx8vb"))
    eng.ctx.security.set_owner(eng.ctx.runtimes_root / "dx8vb", ADMINISTRATORS)
    assert not runtime_present(rt, eng.ctx.records, eng.ctx.registry, eng.ctx.runtimes_root, eng.ctx.security)


def test_matching_folder_without_marker_is_adopted(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "dx8vb"
    folder.mkdir(parents=True)
    (folder / "dx8vb.dll").write_bytes(DX8VB)
    (folder / "readme.txt").write_bytes(b"someone else's note")
    run(eng, "install")
    assert eng.ctx.elevator.calls == [lock_down(eng, folder), regsvr32(folder / "dx8vb.dll")]
    assert snapshot(folder) == {"dx8vb.dll": DX8VB, "readme.txt": b"someone else's note",
                                MARKER_NAME: (folder / MARKER_NAME).read_bytes()}
    assert json.loads((folder / MARKER_NAME).read_text())["files"] == {"dx8vb.dll": sha(DX8VB)}
    assert set(eng.ctx.records.load("dx8vb").files) == {"dx8vb.dll"}
    assert (lib / "Entombed" / "entombed.exe").exists()


def test_empty_folder_without_marker_is_adopted_and_filled(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "dx8vb"
    folder.mkdir(parents=True)
    run(eng, "install")
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB and (folder / MARKER_NAME).exists()


def test_folder_that_doesnt_match_is_refused_and_left_alone(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "dx8vb"
    folder.mkdir(parents=True)
    (folder / "dx8vb.dll").write_bytes(b"a different dx8vb")
    (folder / "other.dll").write_bytes(b"other")
    before = snapshot(folder)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_NOT_OURS.format(title=DX8VB_TITLE, path=folder)
    assert "temporary" not in ei.value.message
    assert snapshot(folder) == before and eng.ctx.elevator.calls == []
    assert eng.ctx.records.load("dx8vb") is None and not (lib / "Entombed").exists()


def test_failed_install_removes_only_what_it_added_to_a_folder_it_found(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(exit_codes={"regsvr32.exe": 3}))
    folder = eng.ctx.runtimes_root / "dx8vb"
    folder.mkdir(parents=True)
    (folder / "readme.txt").write_bytes(b"someone else's note")
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.STEP_REGISTER_FAILED.format(name="dx8vb.dll")
    assert snapshot(folder) == {"readme.txt": b"someone else's note"}
    assert eng.ctx.records.load("dx8vb") is None


def lock_down(eng, folder: Path) -> tuple[str, str, bool]:
    return ("cmd.exe", *lock_down_command(folder, eng.ctx.security.current_user())[1:], True)


def test_lock_down_command_is_exact():
    folder = Path(r"C:\ProgramData\GameChooser\runtimes\dx8vb")
    exe, args = lock_down_command(folder, "S-1-5-21-1-2-3-1001")
    marker = folder / MARKER_NAME
    assert exe == "cmd.exe"
    assert args == (
        '/d /v:off /s /c "'
        r'icacls "C:\ProgramData\GameChooser" /setowner *S-1-5-32-544 /Q && '
        r'icacls "C:\ProgramData\GameChooser" /inheritance:r /grant:r *S-1-5-32-544:(OI)(CI)F '
        r'*S-1-5-18:(OI)(CI)F *S-1-5-32-545:(OI)(CI)RX /Q && '
        r'icacls "C:\ProgramData\GameChooser\runtimes" /setowner *S-1-5-32-544 /Q && '
        r'icacls "C:\ProgramData\GameChooser\runtimes" /inheritance:r /grant:r *S-1-5-32-544:(OI)(CI)F '
        r'*S-1-5-18:(OI)(CI)F *S-1-5-32-545:(OI)(CI)RX *S-1-3-0:(OI)(CI)(IO)F /Q && '
        r'icacls "C:\ProgramData\GameChooser\runtimes" /grant *S-1-5-32-545:(AD) /Q && '
        f'type nul > "{marker}" && '
        f'icacls "{folder}" /setowner *S-1-5-32-544 /T /Q && '
        f'icacls "{folder}" /inheritance:r /grant:r *S-1-5-32-544:(OI)(CI)F *S-1-5-18:(OI)(CI)F '
        f'*S-1-5-32-545:(OI)(CI)RX /Q && '
        f'icacls "{folder}\\*" /reset /T /Q && '
        f'icacls "{marker}" /grant *S-1-5-21-1-2-3-1001:M /Q"')


def test_folder_is_locked_down_then_hashed_again_before_regsvr32(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    seen = []

    def watch(exe, args, elevated):
        dll = tmp_path / "runtimes" / "dx8vb" / "dx8vb.dll"
        seen.append((exe, dll.read_bytes() if dll.exists() else None))

    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(on_start=watch))
    run(eng, "install")
    folder = eng.ctx.runtimes_root / "dx8vb"
    assert eng.ctx.elevator.calls == [lock_down(eng, folder), regsvr32(folder / "dx8vb.dll")]
    assert seen == [("cmd.exe", DX8VB), ("regsvr32.exe", DX8VB)]  # copied before the lock-down


def test_file_swapped_before_the_lock_down_is_caught_and_nothing_is_registered(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)

    def swap(exe, args, elevated):
        if exe == "cmd.exe":  # another account replaces the DLL just before the folder is locked
            (tmp_path / "runtimes" / "dx8vb" / "dx8vb.dll").write_bytes(b"MZ planted")

    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(on_start=swap))
    folder = eng.ctx.runtimes_root / "dx8vb"
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_CHANGED.format(title=DX8VB_TITLE, path=folder)
    assert [c[0] for c in eng.ctx.elevator.calls] == ["cmd.exe"]  # regsvr32 never ran
    assert eng.ctx.records.load("dx8vb") is None and not (lib / "Entombed").exists()


@pytest.mark.windows
@pytest.mark.parametrize("swapped", ["id", "runtimes"])
def test_junction_swapped_in_during_the_lock_down_is_caught_and_nothing_is_registered(tmp_path, swapped):
    import _winapi
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    root = tmp_path / "GameChooser" / "runtimes"
    decoy = tmp_path / "decoy"

    def swap(exe, args, elevated):
        if exe != "cmd.exe":
            return
        # Another account moves the folder away and puts a junction to a look-alike in its place.
        path = root / "dx8vb" if swapped == "id" else root
        shutil.copytree(path, decoy / path.name)
        path.rename(path.with_name(path.name + "-moved"))
        _winapi.CreateJunction(str(decoy / path.name), str(path))

    eng, lib = setup(tmp_path, [rt, g], routes, runtimes_root=root, elevator=FakeElevator(on_start=swap))
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_CHANGED.format(title=DX8VB_TITLE, path=root / "dx8vb")
    assert [c[0] for c in eng.ctx.elevator.calls] == ["cmd.exe"]  # regsvr32 never ran
    assert eng.ctx.records.load("dx8vb") is None


def test_a_new_runtime_folder_still_installs_after_the_parents_are_locked(tmp_path):
    routes = {}
    rt = dx8vb(routes)
    vbx_data = b"MZ fake vbx.ocx"
    vbx = {"id": "vbx", "kind": "runtime", "title": "VBX", "version": "1",
           "sources": [served_source(routes, f"{MIRROR}{sha(vbx_data)}/vbx.ocx", vbx_data, method="none",
                                     steps=[{"action": "register", "path": "vbx.ocx"}])]}
    one, two = game(routes), game(routes, id="pawprints", title="Pawprints", files={"pawprints.exe": b"paw"},
                                  dependencies=["vbx"])
    eng, lib = setup(tmp_path, [rt, vbx, one, two], routes)
    run(eng, "install")  # dx8vb: locks GameChooser, runtimes and runtimes/dx8vb
    run(eng, "install", id="pawprints")  # vbx: a new folder in the locked runtimes folder
    root = eng.ctx.runtimes_root
    assert (root / "vbx" / "vbx.ocx").read_bytes() == vbx_data
    assert eng.ctx.elevator.calls == [lock_down(eng, root / "dx8vb"), regsvr32(root / "dx8vb" / "dx8vb.dll"),
                                      lock_down(eng, root / "vbx"), regsvr32(root / "vbx" / "vbx.ocx")]
    assert all('icacls "%s" /grant *S-1-5-32-545:(AD) /Q' % root in c[1] for c in eng.ctx.elevator.calls[::2])


def test_declined_lock_down_fails_plainly(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(decline=True))
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.ELEVATION_DECLINED.format(what=DX8VB_TITLE)
    assert [c[0] for c in eng.ctx.elevator.calls] == ["cmd.exe"]


def test_failed_lock_down_registers_nothing(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(exit_codes={"cmd.exe": 5}))
    folder = eng.ctx.runtimes_root / "dx8vb"
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_NOT_SECURED.format(title=DX8VB_TITLE, path=folder)
    assert [c[0] for c in eng.ctx.elevator.calls] == ["cmd.exe"]


def test_runtime_with_no_elevated_step_isnt_locked_down(tmp_path):
    routes = {}
    rt = plain_runtime(routes, "bass")
    eng, lib = setup(tmp_path, [rt, game(routes, dependencies=["bass"])], routes)
    run(eng, "install")
    assert eng.ctx.elevator.calls == [] and (eng.ctx.runtimes_root / "bass" / MARKER_NAME).exists()


@pytest.mark.windows
def test_junction_at_the_runtime_folder_is_refused(tmp_path):
    import _winapi
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    real = tmp_path / "attacker"
    real.mkdir()
    eng.ctx.runtimes_root.mkdir(parents=True)
    link = eng.ctx.runtimes_root / "dx8vb"
    _winapi.CreateJunction(str(real), str(link))
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_LINKED.format(title=DX8VB_TITLE, path=link)
    assert list(real.iterdir()) == [] and eng.ctx.elevator.calls == []


@pytest.mark.windows
def test_junction_at_a_parent_folder_is_refused(tmp_path):
    import _winapi
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    real = tmp_path / "attacker"
    real.mkdir()
    root = tmp_path / "GameChooser" / "runtimes"
    root.parent.mkdir()
    _winapi.CreateJunction(str(real), str(root))
    eng, lib = setup(tmp_path, [rt, g], routes, runtimes_root=root)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_LINKED.format(title=DX8VB_TITLE, path=root)
    assert list(real.iterdir()) == [] and eng.ctx.elevator.calls == []


def test_folder_owned_by_another_account_is_refused_and_left_alone(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "dx8vb"
    folder.mkdir(parents=True)  # empty, as another account could leave it
    (folder / "readme.txt").write_bytes(b"theirs")
    eng.ctx.security.set_owner(folder, "S-1-5-21-2000-2000-2000-1002")
    before = snapshot(folder)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_NOT_OURS.format(title=DX8VB_TITLE, path=folder)
    assert snapshot(folder) == before and eng.ctx.elevator.calls == []


def failing_after_register(routes):
    """dx8vb with a second step that fails once regsvr32 has run (its program isn't in the folder)."""
    rt = dx8vb(routes)
    rt["sources"][0]["steps"] = [{"action": "register", "path": "dx8vb.dll"}, {"action": "run", "path": "setup.exe"}]
    return rt


def test_failure_after_registering_in_a_found_folder_leaves_the_registration(tmp_path):
    routes = {}
    rt, g = failing_after_register(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "dx8vb"
    folder.mkdir(parents=True)
    (folder / "dx8vb.dll").write_bytes(DX8VB)  # it may already have been registered by another account
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message.startswith(messages.STEP_FILE_MISSING.format(path="setup.exe"))
    assert eng.ctx.elevator.calls == [lock_down(eng, folder), regsvr32(folder / "dx8vb.dll")]  # no /u
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB


def test_failure_after_registering_in_a_folder_it_made_unregisters(tmp_path):
    routes = {}
    rt, g = failing_after_register(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "dx8vb"
    with pytest.raises(InstallFailed):
        run(eng, "install")
    assert eng.ctx.elevator.calls[-1] == ("regsvr32.exe", f'/u /s "{folder / "dx8vb.dll"}"', True)
    assert not folder.exists()


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
    assert not (eng.ctx.runtimes_root / "vb6-runtime" / MARKER_NAME).exists()  # presence: record or detect
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
    assert exes[:2] == ["cmd.exe", "regsvr32.exe"] and exes[2].endswith("setup.exe")
    assert eng.ctx.records.load("dx8vb").kind == "runtime" and eng.ctx.records.load("lightcars").tracked


def test_update_installs_missing_runtime_first(tmp_path):
    routes = {}
    rt = dx8vb(routes)
    eng, lib = setup(tmp_path, [rt, game(routes, dependencies=())], routes)
    run(eng, "install")
    assert eng.ctx.elevator.calls == []
    eng.ctx.entries["entombed"] = game(eng.ctx.fetcher.routes, files={"entombed.exe": b"exe 2"}, version="2.0")
    r = run(eng, "update")
    folder = eng.ctx.runtimes_root / "dx8vb"
    assert eng.ctx.elevator.calls == [lock_down(eng, folder), regsvr32(folder / "dx8vb.dll")]
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
    assert rec.private_runtimes == ["dx8vb"]
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
    assert eng.ctx.records.load("entombed").private_runtimes == []


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


def run_mode_game(routes, setup_exe=b"MZ fake game setup", silent=True, version="1.0", **fields):
    installer = {"type": "other", "mode": "run", **({"silent_args": "/S"} if silent else {})}
    source = served_source(routes, f"{MIRROR}{sha(setup_exe)}/setup.exe", setup_exe, method="installer",
                           installer=installer)
    return {"id": "lightcars", "kind": "game", "title": "Light Cars", "developer": ["Draconis"],
            "genres": ["Racing"], "year": 2007, "version": version, "sources": [source],
            "dependencies": ["dx8vb"], **fields}


def writes_game(folder: Path, files: dict[str, bytes] | None = None):
    """A fake game installer: when started, it writes the game into `folder`."""
    def on_start(exe, args, elevated):
        if exe.endswith("setup.exe"):
            time.sleep(0.05)
            for rel, data in (files or {"lightcars.exe": b"lc", "data/track.dat": b"t"}).items():
                (folder / rel).parent.mkdir(parents=True, exist_ok=True)
                (folder / rel).write_bytes(data)
            time.sleep(0.05)
    return on_start


def test_preference_off_tracked_run_install_gets_private_copy_and_uninstall_removes_it(tmp_path, private_com):
    routes = {}
    rt, g = dx8vb(routes), run_mode_game(routes)
    folder = tmp_path / "Games" / "Light Cars"
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False,
                     elevator=FakeElevator(on_start=writes_game(folder)))
    r = run(eng, "install", id="lightcars")
    assert r.notes == [] and eng.ctx.elevator.calls[0][0].endswith("setup.exe") and len(eng.ctx.elevator.calls) == 1
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB
    assert DX8VB_CLSID in (folder / "lightcars.exe.manifest").read_text(encoding="utf-8")
    rec = eng.ctx.records.load("lightcars")
    assert rec.tracked and rec.files["dx8vb.dll"].owner == "runtime:dx8vb"
    assert rec.files["lightcars.exe.manifest"].owner == "runtime:dx8vb"
    assert rec.files["lightcars.exe"].owner == "lightcars" and rec.private_runtimes == ["dx8vb"]
    assert eng.ctx.records.load("dx8vb") is None and not (eng.ctx.runtimes_root / "dx8vb").exists()

    run(eng, "uninstall", id="lightcars")
    assert not folder.exists() and eng.ctx.records.load("lightcars") is None


def test_preference_off_run_update_replaces_the_private_copy_and_extras_leave_it(tmp_path, private_com):
    routes = {}
    rt = dx8vb(routes)
    x_data = zip_bytes({"tracks/extra.dat": b"x"})
    x = {"id": "lc-tracks", "kind": "extra", "title": "Tracks", "install_into": "lightcars", "version": "1",
         "sources": [served_source(routes, f"{MIRROR}{sha(x_data)}/tracks.zip", x_data)]}
    g = run_mode_game(routes, extras=[{"id": "lc-tracks", "required": True}])
    folder = tmp_path / "Games" / "Light Cars"
    eng, lib = setup(tmp_path, [rt, x, g], routes, register_shared_components=False,
                     elevator=FakeElevator(on_start=writes_game(folder)))
    run(eng, "install", id="lightcars")
    assert (folder / "dx8vb.dll").exists() and (folder / "tracks" / "extra.dat").exists()

    eng.ctx.entries["lightcars"] = run_mode_game(eng.ctx.fetcher.routes, setup_exe=b"MZ setup v2", version="2.0",
                                                 extras=[{"id": "lc-tracks", "required": True}])
    eng.ctx.elevator.on_start = writes_game(folder, {"lightcars.exe": b"lc v2", "data/track.dat": b"t2"})
    r = run(eng, "update", id="lightcars")
    assert r.notes == [] and (folder / "lightcars.exe").read_bytes() == b"lc v2"
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB and (folder / "lightcars.exe.manifest").exists()
    rec = eng.ctx.records.load("lightcars")
    assert rec.files["dx8vb.dll"].owner == "runtime:dx8vb" and rec.files["tracks/extra.dat"].owner == "lc-tracks"
    run(eng, "repair", id="lightcars")  # runs the installer again, puts the extras back: the copy stays
    assert (folder / "dx8vb.dll").exists() and (folder / "lightcars.exe.manifest").exists()
    assert eng.ctx.records.load("lightcars").files["dx8vb.dll"].owner == "runtime:dx8vb"

    eng.ctx.register_shared_components = True  # now shared: the private copy goes, the runtime is registered
    eng.ctx.elevator.on_start = writes_game(folder, {"lightcars.exe": b"lc v2", "data/track.dat": b"t2"})
    run(eng, "update", id="lightcars")
    assert not (folder / "dx8vb.dll").exists() and not (folder / "lightcars.exe.manifest").exists()
    assert eng.ctx.records.load("dx8vb").kind == "runtime"
    assert not any(runtimes_owner.startswith("runtime:")
                   for runtimes_owner in (f.owner for f in eng.ctx.records.load("lightcars").files.values()))


def test_preference_off_run_install_with_embedded_manifest_notes_and_copies_nothing(tmp_path, private_com,
                                                                                    monkeypatch):
    monkeypatch.setattr(comfree, "has_embedded_manifest", lambda exe: True)
    routes = {}
    rt, g = dx8vb(routes), run_mode_game(routes)
    folder = tmp_path / "Games" / "Light Cars"
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False,
                     elevator=FakeElevator(on_start=writes_game(folder)))
    r = run(eng, "install", id="lightcars")
    assert r.notes == [messages.NEEDS_SHARED_COMPONENT.format(title="Light Cars", component=DX8VB_TITLE)]
    assert not (folder / "dx8vb.dll").exists() and not (folder / "lightcars.exe.manifest").exists()
    assert set(eng.ctx.records.load("lightcars").files) == {"lightcars.exe", "data/track.dat"}
    assert eng.ctx.records.load("lightcars").private_runtimes == []
    assert [c for c in eng.ctx.elevator.calls if c[0] == "regsvr32.exe"] == []


def test_preference_off_untracked_run_install_notes(tmp_path, private_com, monkeypatch):
    monkeypatch.setattr(run_installer, "CLOCK_SLACK", 0.0)
    routes = {}
    rt, g = dx8vb(routes), run_mode_game(routes, silent=False)
    chosen = tmp_path / "Somewhere" / "LC"
    chosen.mkdir(parents=True)
    (chosen / "lc.exe").write_bytes(b"exe")  # an old copy: nothing in it was written during the install
    time.sleep(0.05)
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False,
                     installer_ui=FakeInstallerUI(locate=chosen))
    r = run(eng, "install", id="lightcars")
    assert not eng.ctx.records.load("lightcars").tracked
    assert messages.NEEDS_SHARED_COMPONENT.format(title="Light Cars", component=DX8VB_TITLE) in r.notes
    assert sorted(p.name for p in chosen.iterdir()) == ["lc.exe"]


def test_preference_off_bad_runtime_download_leaves_the_installer_unstarted(tmp_path, private_com):
    routes = {}
    rt, g = dx8vb(routes), run_mode_game(routes)
    del routes[rt["sources"][0]["url"]]
    folder = tmp_path / "Games" / "Light Cars"
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False,
                     elevator=FakeElevator(on_start=writes_game(folder)))
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install", id="lightcars")
    assert ei.value.message.startswith(f"{DX8VB_TITLE} couldn't be downloaded.")
    assert eng.ctx.elevator.calls == [] and not folder.exists() and eng.ctx.records.load("lightcars") is None


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
    assert eng.ctx.records.load("entombed").private_runtimes == ["dx8vb"]
    eng.ctx.register_shared_components = True
    run(eng, "repair")
    folder = lib / "Entombed"
    assert (folder / "dx8vb.dll").exists() and (folder / "entombed.exe.manifest").exists()
    assert eng.ctx.elevator.calls == [] and eng.ctx.records.load("dx8vb") is None


def test_repair_restores_a_missing_private_copy(tmp_path, private_com):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    run(eng, "install")
    folder = lib / "Entombed"
    (folder / "dx8vb.dll").unlink()
    run(eng, "repair")
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB and (folder / "entombed.exe.manifest").exists()
    assert eng.ctx.records.load("entombed").private_runtimes == ["dx8vb"]


def test_repair_of_a_game_installed_shared_never_adds_a_private_copy(tmp_path, private_com):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    run(eng, "install")
    assert eng.ctx.records.load("entombed").private_runtimes == []
    # The runtime has gone missing since (its record and folder lost), and the preference is now off.
    eng.ctx.records.delete("dx8vb")
    shutil.rmtree(eng.ctx.runtimes_root / "dx8vb")
    eng.ctx.register_shared_components = False
    run(eng, "repair")
    folder = lib / "Entombed"
    assert not (folder / "dx8vb.dll").exists() and not (folder / "entombed.exe.manifest").exists()
    assert rt["sources"][0]["url"] not in eng.ctx.fetcher.requests[1:]


def test_undo_makes_the_previous_versions_private_copies(tmp_path, private_com):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    run(eng, "install")
    eng.ctx.register_shared_components = True  # v2 is installed with the runtime shared
    eng.ctx.entries["entombed"] = game(eng.ctx.fetcher.routes, files={"entombed.exe": b"exe 2"}, version="2.0")
    run(eng, "update")
    folder = lib / "Entombed"
    assert eng.ctx.records.load("entombed").private_runtimes == [] and not (folder / "dx8vb.dll").exists()
    run(eng, "undo")
    assert (folder / "dx8vb.dll").exists() and (folder / "entombed.exe.manifest").exists()
    assert eng.ctx.records.load("entombed").private_runtimes == ["dx8vb"]


def test_older_record_without_the_field_is_read_from_its_files(tmp_path, private_com):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    run(eng, "install")
    rec = eng.ctx.records.load("entombed")
    rec.private_runtimes = None
    assert recorded_private(rec, g, eng.ctx.entries) == ["dx8vb"]  # its DLL beside a recorded manifest
    eng.ctx.records.save(rec)
    run(eng, "repair")
    assert (lib / "Entombed" / "dx8vb.dll").exists()

    shipped = eng.ctx.records.load("entombed")
    shipped.private_runtimes = None
    del shipped.files["entombed.exe.manifest"]  # a dx8vb.dll the game shipped itself, with no manifest
    assert recorded_private(shipped, g, eng.ctx.entries) == []
    run_owned = eng.ctx.records.load("entombed")
    run_owned.private_runtimes = None
    run_owned.files = {"x.dll": RecordedFile("x.dll", "0", "runtime:other-rt")}
    assert recorded_private(run_owned, g, eng.ctx.entries) == ["other-rt"]


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
