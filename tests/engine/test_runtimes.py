"""Dependencies: runtimes installed before the game, shared machine-wide or as a private COM copy."""

import errno
import hashlib
import json
import os
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
from engine.runtimes import (MARKER_NAME, default_runtimes_root, detect_present, is_com_runtime, lock_down_commands,
                             marker_present, missing_runtimes, recorded_private, register_and_mark_args,
                             runtime_present, write_marker_args)
from tests.engine.fakes import (ADMINISTRATORS, FakeElevator, FakeFetcher, FakeInstallerUI, FakeRegistry,
                                FakeSecurity, ZipExtractor, make_engine, served_source, split_chain, zip_bytes)

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


def regsvr32(eng, folder: Path, dll: str = "dx8vb.dll", game: str = "entombed",
             source_sha: str = sha(DX8VB)) -> tuple[str, str, bool]:
    """The runtime's last register step: one elevated command running regsvr32, then writing the marker (the
    download's checksum, nothing from the player) once it has succeeded."""
    return "cmd.exe", register_and_mark_args(folder / dll, folder / MARKER_NAME, source_sha), True


def marker_bytes(source_sha: str) -> bytes:
    """What `echo` leaves in the marker of a sealed runtime folder."""
    return (source_sha + "\r\n").encode()


# The bytes behind the file names the chain tests use (the lock-down chain carries each file's checksum).
KNOWN_FILES = {"dx8vb.dll": DX8VB, "vbx.ocx": b"MZ vbx", "tool.exe": b"MZ tool", "lib/helper.dll": b"MZ helper",
               "lib/deep/more.dll": b"MZ more"}


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
    assert fake_elev.calls.count(regsvr32(eng, runtimes_root / "dx8vb")) == 1  # (R42: regsvr32 then the marker)
    assert sum('regsvr32.exe /s "' in args for _, args, _ in fake_elev.calls) == 1
    assert eng.ctx.fetcher.requests.count(rt["sources"][0]["url"]) == 1
    assert Progress("installing_dependency", detail=DX8VB_TITLE) in seen
    rec = eng.ctx.records.load("dx8vb")
    assert rec.kind == "runtime" and rec.library_id == "" and Path(rec.folder) == runtimes_root / "dx8vb"
    assert set(rec.files) == {"dx8vb.dll"} and rec.files["dx8vb.dll"].owner == "dx8vb"
    assert rec.steps == [{"action": "register", "path": "dx8vb.dll"}] and rec.method == "none"
    assert (lib / "Entombed" / "entombed.exe").exists() and (lib / "Pawprints" / "pawprints.exe").exists()
    # The runtime lives in its shared folder; the game gets a copy of its DLL beside its exe (engine.runtimes).
    assert (lib / "Entombed" / "dx8vb.dll").read_bytes() == DX8VB and r.notes == []
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
    assert order == [rt["sources"][0]["url"], "cmd.exe", "cmd.exe", g["sources"][0]["url"]]  # lock-down, regsvr32


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


def test_failed_register_fails_install_and_leaves_no_record_and_the_locked_folder(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(exit_codes={"regsvr32.exe": 3}))
    folder = eng.ctx.runtimes_root / "dx8vb"
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.STEP_REGISTER_FAILED.format(name="dx8vb.dll")
    assert eng.ctx.records.load("dx8vb") is None and not (lib / "Entombed").exists()
    # The folder was made and sealed by the elevated command, so the player can't remove it (R39). Without a
    # marker it counts as absent, and a retry adopts it.
    assert snapshot(folder) == {"dx8vb.dll": DX8VB} and not (folder / MARKER_NAME).exists()


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
    # A sealed folder's marker: the download's checksum, written by the elevated command, nothing else.
    assert (folder / MARKER_NAME).read_bytes() == marker_bytes(sha(DX8VB))
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


def test_sealed_folder_marker_is_a_checksum_line_and_counts_only_under_administrators(tmp_path):
    """The marker an elevated chain writes (R44): one line of hex, no file list. Only an administrator's
    process can write into the sealed folder, so in an Administrators-owned folder its presence is the
    evidence; anywhere else it counts for nothing, and nor does anything that isn't a checksum line."""
    sec = FakeSecurity()
    folder = tmp_path / "runtimes" / "dx8vb"
    folder.mkdir(parents=True)
    (folder / "dx8vb.dll").write_bytes(DX8VB)
    (folder / MARKER_NAME).write_bytes(marker_bytes(sha(DX8VB)))
    assert not marker_present(folder, "dx8vb", sec)  # the player's folder (FakeSecurity's default)
    assert not marker_present(folder, "dx8vb", sec, allow_user=True)  # even for a runtime with no elevated step
    sec.set_owner(folder, ADMINISTRATORS)
    assert marker_present(folder, "dx8vb", sec)
    (folder / MARKER_NAME).write_bytes(sha(DX8VB).upper().encode() + b"\n")  # case and line ending don't matter
    assert marker_present(folder, "dx8vb", sec)
    for junk in (b"", b"not a checksum\r\n", sha(DX8VB).encode()[:-1] + b"\r\n", b'{"files": 1}'):
        (folder / MARKER_NAME).write_bytes(junk)
        assert not marker_present(folder, "dx8vb", sec)


def test_player_owned_folder_is_refused_for_a_registering_runtime_and_left_alone(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "dx8vb"
    folder.mkdir(parents=True)  # owned by the player (FakeSecurity's default): matching files aren't enough
    (folder / "dx8vb.dll").write_bytes(DX8VB)
    before = snapshot(folder)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_NOT_OURS.format(title=DX8VB_TITLE, path=folder)
    assert snapshot(folder) == before and eng.ctx.elevator.calls == []
    assert eng.ctx.records.load("dx8vb") is None and not (lib / "Entombed").exists()


def test_administrators_owned_folder_is_adopted_and_only_missing_files_are_copied(tmp_path):
    routes = {}
    data = zip_bytes({"vbx.ocx": b"MZ vbx", "lib/helper.dll": b"MZ helper", "lib/deep/more.dll": b"MZ more"})
    vbx = {"id": "vbx", "kind": "runtime", "title": "VBX", "version": "1",
           "sources": [served_source(routes, f"{MIRROR}{sha(data)}/vbx.zip", data,
                                     steps=[{"action": "register", "path": "vbx.ocx"}])]}
    eng, lib = setup(tmp_path, [vbx, game(routes, dependencies=["vbx"])], routes)
    folder = eng.ctx.runtimes_root / "vbx"
    (folder / "lib").mkdir(parents=True)  # locked by an earlier install that lost its marker and record
    (folder / "vbx.ocx").write_bytes(b"MZ vbx")
    for made in (folder, folder / "lib", folder / "vbx.ocx"):
        eng.ctx.security.set_owner(made, ADMINISTRATORS)
    run(eng, "install")
    files = ["vbx.ocx", "lib/helper.dll", "lib/deep/more.dll"]
    assert eng.ctx.elevator.calls == [*lock_down(eng, folder, files, existing={"vbx.ocx", "lib"}),
                                      regsvr32(eng, folder, "vbx.ocx", source_sha=sha(data))]
    commands = icacls_commands(eng.ctx.elevator.calls[0][1])
    assert f'mkdir "{folder}"' not in commands and f'mkdir "{folder / "lib"}"' not in commands
    assert f'mkdir "{folder / "lib" / "deep"}"' in commands
    assert [c for c in commands if c.startswith("copy ")] == [
        f'copy /b "{staging_of(eng, folder) / "lib" / "helper.dll"}" "{folder / "lib" / "helper.dll"}" >nul',
        f'copy /b "{staging_of(eng, folder) / "lib" / "deep" / "more.dll"}" "{folder / "lib" / "deep" / "more.dll"}" >nul']
    assert snapshot(folder) == {"vbx.ocx": b"MZ vbx", "lib/helper.dll": b"MZ helper", "lib/deep/more.dll": b"MZ more",
                                MARKER_NAME: marker_bytes(sha(data))}
    assert set(eng.ctx.records.load("vbx").files) == set(files) and (lib / "Entombed" / "entombed.exe").exists()


def test_empty_folder_without_marker_is_adopted_and_filled_for_a_runtime_with_no_elevated_step(tmp_path):
    routes = {}
    rt = plain_runtime(routes, "bass")
    eng, lib = setup(tmp_path, [rt, game(routes, dependencies=["bass"])], routes)
    folder = eng.ctx.runtimes_root / "bass"
    folder.mkdir(parents=True)
    run(eng, "install")
    assert (folder / "bass.dll").read_bytes() == b"bass" and (folder / MARKER_NAME).exists()
    assert eng.ctx.elevator.calls == []


def test_folder_that_doesnt_match_is_refused_and_left_alone(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "dx8vb"
    folder.mkdir(parents=True)
    (folder / "dx8vb.dll").write_bytes(b"a different dx8vb")
    (folder / "other.dll").write_bytes(b"other")
    eng.ctx.security.set_owner(folder, ADMINISTRATORS)
    before = snapshot(folder)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_NOT_OURS.format(title=DX8VB_TITLE, path=folder)
    assert "temporary" not in ei.value.message
    assert snapshot(folder) == before and eng.ctx.elevator.calls == []
    assert eng.ctx.records.load("dx8vb") is None and not (lib / "Entombed").exists()


def test_failed_install_removes_only_what_it_added_to_a_folder_it_found(tmp_path):
    routes = {}
    bass = plain_runtime(routes, "bass")  # no elevated step: a found folder may hold other files
    bass["sources"][0]["steps"] = [{"action": "run", "path": "setup.exe"}]  # fails: not in the folder
    eng, lib = setup(tmp_path, [bass, game(routes, dependencies=["bass"])], routes)
    folder = eng.ctx.runtimes_root / "bass"
    folder.mkdir(parents=True)
    (folder / "readme.txt").write_bytes(b"someone else's note")
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message.startswith(messages.STEP_FILE_MISSING.format(path="setup.exe"))
    assert snapshot(folder) == {"readme.txt": b"someone else's note"}
    assert eng.ctx.records.load("bass") is None


def test_after_a_failed_register_the_locked_folder_stays_and_a_retry_adopts_it(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(exit_codes={"regsvr32.exe": 3}))
    folder = eng.ctx.runtimes_root / "dx8vb"
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.STEP_REGISTER_FAILED.format(name="dx8vb.dll")
    assert snapshot(folder) == {"dx8vb.dll": DX8VB}  # sealed: nothing the player could remove; no marker
    assert eng.ctx.records.load("dx8vb") is None

    eng.ctx.elevator.exit_codes.clear()  # regsvr32 works this time
    run(eng, "install")
    retry = eng.ctx.elevator.calls[2:]
    assert retry == [*lock_down(eng, folder, existing={"dx8vb.dll"}), regsvr32(eng, folder)]
    assert not any(c.startswith(("mkdir ", "copy ")) for c in icacls_commands(retry[0][1]))  # nothing was missing
    assert (folder / MARKER_NAME).read_bytes() == marker_bytes(sha(DX8VB))
    assert set(eng.ctx.records.load("dx8vb").files) == {"dx8vb.dll"} and (lib / "Entombed" / "entombed.exe").exists()


def test_found_folder_with_an_extra_file_isnt_adopted_for_a_lock_down(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "dx8vb"
    folder.mkdir(parents=True)
    (folder / "dx8vb.dll").write_bytes(DX8VB)
    (folder / "readme.txt").write_bytes(b"someone else's note")
    for made in (folder, folder / "dx8vb.dll"):
        eng.ctx.security.set_owner(made, ADMINISTRATORS)
    before = snapshot(folder)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_NOT_OURS.format(title=DX8VB_TITLE, path=folder)
    assert snapshot(folder) == before and eng.ctx.elevator.calls == []


def test_found_folder_holding_a_player_owned_matching_file_isnt_adopted_for_a_lock_down(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "dx8vb"
    folder.mkdir(parents=True)
    (folder / "dx8vb.dll").write_bytes(DX8VB)  # the right bytes, but the player's file (FakeSecurity's default)
    eng.ctx.security.set_owner(folder, ADMINISTRATORS)  # an administrator made the folder, not Game Chooser
    before = snapshot(folder)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_NOT_OURS.format(title=DX8VB_TITLE, path=folder)
    assert snapshot(folder) == before and eng.ctx.elevator.calls == []


def test_found_folder_with_a_stale_marker_isnt_adopted_for_a_lock_down(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "dx8vb"
    put_marker(folder, {"dx8vb.dll": DX8VB})
    (folder / "dx8vb.dll").unlink()  # the marker lists a file that is gone: it would say "installed" again
    eng.ctx.security.set_owner(folder, ADMINISTRATORS)  # as soon as the file is copied back, before regsvr32
    before = snapshot(folder)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_NOT_OURS.format(title=DX8VB_TITLE, path=folder)
    assert snapshot(folder) == before and eng.ctx.elevator.calls == []


def staging_of(eng, folder: Path, game: str = "entombed") -> Path:
    """Where the runtime was unpacked, as the player: the source of the elevated command's copies."""
    return eng.ctx.work_root / game / "runtimes" / folder.name / "unpacked"


def hashed(files) -> dict[str, str]:
    """`files` as lock_down_commands takes them: relative path -> sha256. Names are looked up in KNOWN_FILES."""
    if isinstance(files, dict):
        return {rel: sha(data) for rel, data in files.items()}
    return {rel: sha(KNOWN_FILES[rel]) for rel in files}


def lock_down(eng, folder: Path, files=("dx8vb.dll",), existing=None, game: str = "entombed"):
    return [(exe, args, True) for exe, args in lock_down_commands(
        folder, eng.ctx.security.current_user(), staging_of(eng, folder, game), hashed(files), existing)]


def icacls_commands(args: str) -> list[str]:
    """The elevated chain's commands, in order (icacls and the rest)."""
    return split_chain(args)


BASE_GRANTS = "*S-1-5-32-544:(OI)(CI)F *S-1-5-18:(OI)(CI)F *S-1-5-32-545:(OI)(CI)RX"
# The runtime's own folder, and the folders inside it, while they are being filled (R44).
ADMIN_ONLY = "*S-1-5-32-544:(OI)(CI)F *S-1-5-18:(OI)(CI)F"
USERS_RX = "*S-1-5-32-545:(OI)(CI)RX"
USER_SID = "S-1-5-21-1-2-3-1001"


def not_a_reparse_point(path) -> str:
    return f'(fsutil reparsepoint query "{path}" >nul 2>&1 && exit 1 || type nul)'


def verified(path, checksum: str) -> str:
    """The chain's check of a file it has just copied: its SHA256 must be the download's, else the copied bytes
    are deleted (still unreadable to anyone but administrators) and the chain stops."""
    return (f'((certutil -hashfile "{path}" SHA256 | findstr /i /x /l "{checksum}" >nul) || '
            f'(del /f /q "{path}" & exit 1))')


def opened(path) -> str:
    """The last thing the chain does: Users may read the folder, now that every file in it has been checked."""
    return f'icacls "{path}" /grant {USERS_RX} /L /Q'


def test_lock_down_command_is_exact():
    folder = Path(r"C:\ProgramData\GameChooser\runtimes\dx8vb")
    staging = Path(r"C:\Users\p\AppData\Local\Temp\GameChooser\entombed\runtimes\dx8vb\unpacked")
    [(exe, args)] = lock_down_commands(folder, USER_SID, staging, {"dx8vb.dll": sha(DX8VB)})
    assert exe == "cmd.exe"
    gc, rt = r"C:\ProgramData\GameChooser", r"C:\ProgramData\GameChooser\runtimes"
    assert icacls_commands(args) == [
        f'icacls "{gc}" /setowner *S-1-5-32-544 /L /Q',
        f'icacls "{gc}" /inheritance:r /grant:r {BASE_GRANTS} /L /Q',
        not_a_reparse_point(gc),
        f'icacls "{rt}" /setowner *S-1-5-32-544 /L /Q',
        f'icacls "{rt}" /inheritance:r /grant:r {BASE_GRANTS} *S-1-3-0:(OI)(CI)(IO)F /L /Q',
        f'icacls "{rt}" /grant *S-1-5-32-545:(AD) /L /Q',
        not_a_reparse_point(rt),
        f'mkdir "{folder}"',
        f'icacls "{folder}" /setowner *S-1-5-32-544 /L /Q',
        f'icacls "{folder}" /inheritance:r /grant:r {ADMIN_ONLY} /L /Q',  # nobody else reads it while it is filled
        f'copy /b "{staging / "dx8vb.dll"}" "{folder / "dx8vb.dll"}" >nul',
        verified(folder / "dx8vb.dll", sha(DX8VB)),
        opened(folder),
    ]
    assert USER_SID not in args  # the installing user gets no grant anywhere (R42)


def test_users_may_read_the_folder_only_as_the_chains_last_act_after_every_copy_is_checked():
    """R44: a redirect planted in staging during the prompt makes the elevated copy read a file the player
    can't; nothing it copied becomes readable until the checksum check has passed on every file."""
    folder = Path(r"C:\ProgramData\GameChooser\runtimes\vbx")
    files = {"vbx.ocx": "a" * 64, "lib/a.dll": "b" * 64, "lib/deep/b.dll": "c" * 64}
    for existing in (None, {"lib", "lib/a.dll"}):
        [(exe, args)] = lock_down_commands(folder, USER_SID, Path(r"C:\staging"), files, existing)
        commands = icacls_commands(args)
        inside = [folder, folder / "lib", folder / "lib" / "deep"]
        tail = commands[-len(inside):]
        assert sorted(tail) == sorted(opened(d) for d in inside)  # the runtime's folders, Users RX, last of all
        body = commands[:-len(inside)]
        assert not any("S-1-5-32-545" in c and str(folder) in c for c in body)  # no Users ACE before that
        for c in body:
            if c.startswith("icacls ") and str(folder) in c and "/inheritance:r" in c:
                assert c.endswith(f"/inheritance:r /grant:r {ADMIN_ONLY} /L /Q")
        copies = [c for c in commands if c.startswith("copy ")]
        for copy in copies:  # every copy is followed at once by its check
            dest = copy.split('"')[3]
            rel = Path(dest).relative_to(folder).as_posix()
            assert commands[commands.index(copy) + 1] == verified(dest, files[rel])
        assert {Path(c.split('"')[3]).relative_to(folder).as_posix() for c in copies} == set(files) - (existing or set())


def test_adoption_command_makes_nothing_that_exists_and_copies_only_missing_files():
    folder = Path(r"C:\ProgramData\GameChooser\runtimes\vbx")
    staging = Path(r"C:\Users\p\AppData\Local\Temp\GameChooser\entombed\runtimes\vbx\unpacked")
    files = {"vbx.ocx": "a" * 64, "lib/a.dll": "b" * 64, "lib/deep/b.dll": "c" * 64}
    [(exe, args)] = lock_down_commands(folder, USER_SID, staging, files, existing={"vbx.ocx", "lib"})
    commands = icacls_commands(args)
    lib, deep = folder / "lib", folder / "lib" / "deep"
    assert commands[7:] == [
        f'icacls "{folder}" /setowner *S-1-5-32-544 /L /Q',
        f'icacls "{folder}" /inheritance:r /grant:r {ADMIN_ONLY} /L /Q',  # Users lose read access while it is filled
        not_a_reparse_point(folder),
        f'(if exist "{folder / MARKER_NAME}" exit 1)',  # a stale marker must not say "installed" before regsvr32
        f'icacls "{lib}" /setowner *S-1-5-32-544 /L /Q',
        f'icacls "{lib}" /inheritance:r /grant:r {ADMIN_ONLY} /L /Q',
        not_a_reparse_point(lib),
        # Into a folder that was already there: never through a link, never over something planted.
        not_a_reparse_point(lib / "a.dll"),
        f'(if exist "{lib / "a.dll"}" exit 1)',
        f'copy /b "{staging / "lib" / "a.dll"}" "{lib / "a.dll"}" >nul',
        verified(lib / "a.dll", "b" * 64),
        f'mkdir "{deep}"',
        f'icacls "{deep}" /setowner *S-1-5-32-544 /L /Q',
        f'icacls "{deep}" /inheritance:r /grant:r {ADMIN_ONLY} /L /Q',
        f'copy /b "{staging / "lib" / "deep" / "b.dll"}" "{deep / "b.dll"}" >nul',  # a folder it just made and sealed
        verified(deep / "b.dll", "c" * 64),
        opened(folder), opened(lib), opened(deep),
    ]
    assert not any("vbx.ocx" in c for c in commands)  # already there: not copied, not touched


def test_lock_down_never_recurses_and_touches_no_file_with_icacls():
    folder = Path(r"C:\ProgramData\GameChooser\runtimes\vbx")
    staging = Path(r"C:\staging")
    files = {"vbx.ocx": "a" * 64, "lib/a.dll": "b" * 64, "lib/deep/b.dll": "c" * 64, "z.txt": "d" * 64}
    for existing in (None, {"lib", "lib/a.dll"}):
        [(exe, args)] = lock_down_commands(folder, USER_SID, staging, files, existing)
        commands = icacls_commands(args)
        assert all(" /T" not in c and "*\"" not in c and "\\*" not in c for c in commands)  # no recursion, no wildcard
        assert all(USER_SID not in c for c in commands)  # no grant to the installing user (R42)
        icacls = [c for c in commands if c.startswith("icacls ")]
        assert all(c.endswith(" /L /Q") for c in icacls)  # /L on every call
        folders = {folder.parent.parent, folder.parent, folder, folder / "lib", folder / "lib" / "deep"}
        assert {Path(c.split('"')[1]) for c in icacls} <= folders  # icacls names folders only, never a file
        copies = [Path(c.split('"')[3]) for c in commands if c.startswith("copy ")]
        assert {p.relative_to(folder).as_posix() for p in copies} == set(files) - (existing or set())
        for directory in folders - {folder.parent.parent, folder.parent}:
            sealed = commands.index(f'icacls "{directory}" /inheritance:r /grant:r {ADMIN_ONLY} /L /Q')
            assert commands.index(f'icacls "{directory}" /setowner *S-1-5-32-544 /L /Q') < sealed
            made = [i for i, c in enumerate(commands) if c == f'mkdir "{directory}"']
            assert (made == [] if existing and directory != folder / "lib" / "deep" else made[0] < sealed)
            children = [i for i, c in enumerate(commands) if f'"{directory}\\' in c]
            assert children and min(children) > sealed  # sealed before anything inside it is touched
        # The parents are sealed before the runtime's folder is made or sealed.
        assert commands.index(not_a_reparse_point(folder.parent)) < commands.index(
            f'icacls "{folder}" /setowner *S-1-5-32-544 /L /Q')


def test_long_lock_down_is_split_into_few_invocations_each_parent_first():
    folder = Path(r"C:\ProgramData\GameChooser\runtimes\big")
    files = {f"data/file-{i:04}.dat": f"{i:064x}" for i in range(120)}
    invocations = lock_down_commands(folder, USER_SID, Path(r"C:\staging\big"), files)
    assert 1 < len(invocations) <= 8 and all(len(args) <= 8000 for _, args in invocations)
    first = icacls_commands(invocations[0][1])
    assert f'icacls "{folder}" /inheritance:r /grant:r {ADMIN_ONLY} /L /Q' in first
    commands = [c for _, args in invocations for c in icacls_commands(args)]
    data = folder / "data"
    sealed = next(i for i, c in enumerate(commands) if f'icacls "{data}" /inheritance:r' in c)
    assert all(i > sealed for i, c in enumerate(commands) if f'"{data}\\' in c)
    # Users may read the folder only once the last invocation has checked the last file: a declined or failed
    # later prompt leaves a sealed folder nobody but administrators can read.
    assert all("S-1-5-32-545" not in c for _, args in invocations[:-1] for c in icacls_commands(args)[7:])
    assert icacls_commands(invocations[-1][1])[-2:] == [opened(folder), opened(data)]


def test_folder_is_made_and_filled_by_the_elevated_command_then_hashed_again_before_regsvr32(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    seen = []

    def watch(exe, args, elevated):
        folder = tmp_path / "runtimes" / "dx8vb"
        what = "lock-down" if "icacls" in args else "regsvr32"
        seen.append((what, folder.exists(), (folder / "dx8vb.dll").read_bytes() if (folder / "dx8vb.dll").exists() else None,
                     (folder / MARKER_NAME).exists()))

    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(on_start=watch))
    run(eng, "install")
    folder = eng.ctx.runtimes_root / "dx8vb"
    assert eng.ctx.elevator.calls == [*lock_down(eng, folder), regsvr32(eng, folder)]
    # Nothing there until the lock-down ran; no marker until regsvr32's command ran.
    assert seen == [("lock-down", False, None, False), ("regsvr32", True, DX8VB, False)]
    assert (folder / MARKER_NAME).read_bytes() == marker_bytes(sha(DX8VB))


def test_marker_is_written_by_the_registering_command_only_once_regsvr32_has_succeeded(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    run(eng, "install")
    folder = eng.ctx.runtimes_root / "dx8vb"
    [lock, (exe, args, elevated)] = eng.ctx.elevator.calls
    assert (exe, elevated) == ("cmd.exe", True)
    assert icacls_commands(args) == [f'regsvr32.exe /s "{folder / "dx8vb.dll"}"',
                                     f'((echo {sha(DX8VB)}) > "{folder / MARKER_NAME}" || type nul)']
    # The marker holds the download's checksum, which the engine computed: no bytes the player supplied are
    # read by the elevated command, and nothing from the work folder appears in it (R44).
    assert (folder / MARKER_NAME).read_bytes() == marker_bytes(sha(DX8VB))
    assert str(eng.ctx.work_root) not in args and "copy" not in args
    assert eng.ctx.security.owner(folder / MARKER_NAME) == ADMINISTRATORS  # made by the elevated command
    user = eng.ctx.security.current_user()
    assert all(user not in c_args for _, c_args, _ in eng.ctx.elevator.calls)  # no grant to the installing user


def test_failed_register_writes_no_marker(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(exit_codes={"regsvr32.exe": 5}))
    folder = eng.ctx.runtimes_root / "dx8vb"
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.STEP_REGISTER_FAILED.format(name="dx8vb.dll")
    assert eng.ctx.elevator.calls[1] == regsvr32(eng, folder) and not (folder / MARKER_NAME).exists()
    assert not runtime_present(rt, eng.ctx.records, eng.ctx.registry, eng.ctx.runtimes_root, eng.ctx.security)


def test_record_save_failure_after_the_marker_keeps_the_registration_and_installs(tmp_path, caplog):
    """R45: once the marker is in the sealed folder, every account sees the runtime as installed, so nothing
    is undone; the per-user record is only a cache, so losing it is logged, not a failure."""
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    real_save = eng.ctx.records.save

    def save(record):
        if record.kind == "runtime":
            raise OSError(28, "No space left on device")
        real_save(record)
    eng.ctx.records.save = save
    r = run(eng, "install")
    folder = eng.ctx.runtimes_root / "dx8vb"
    assert r.notes == [] and (lib / "Entombed" / "entombed.exe").exists()
    assert eng.ctx.elevator.calls == [*lock_down(eng, folder), regsvr32(eng, folder)]  # no regsvr32 /u
    assert (folder / MARKER_NAME).read_bytes() == marker_bytes(sha(DX8VB))
    assert eng.ctx.records.load("dx8vb") is None
    assert runtime_present(rt, eng.ctx.records, eng.ctx.registry, eng.ctx.runtimes_root, eng.ctx.security)
    assert any("record" in rec.message for rec in caplog.records if rec.levelname == "WARNING")
    # The next game needs no runtime work at all.
    eng.ctx.records.save = real_save
    two = game(routes, id="pawprints", title="Pawprints", files={"pawprints.exe": b"p"})
    eng.ctx.entries["pawprints"] = two
    eng.ctx.fetcher.routes.update(routes)
    run(eng, "install", id="pawprints")
    assert len(eng.ctx.elevator.calls) == 2


def test_runtime_whose_last_step_isnt_a_register_step_gets_its_marker_in_one_more_elevated_command(tmp_path):
    routes = {}
    data = zip_bytes({"vbx.ocx": b"MZ vbx", "tool.exe": b"MZ tool"})
    vbx = {"id": "vbx", "kind": "runtime", "title": "VBX", "version": "1",
           "sources": [served_source(routes, f"{MIRROR}{sha(data)}/vbx.zip", data,
                                     steps=[{"action": "register", "path": "vbx.ocx"}, {"action": "run", "path": "tool.exe"}])]}
    eng, lib = setup(tmp_path, [vbx, game(routes, dependencies=["vbx"])], routes)
    run(eng, "install")
    folder = eng.ctx.runtimes_root / "vbx"
    assert eng.ctx.elevator.calls == [
        *lock_down(eng, folder, ["vbx.ocx", "tool.exe"]),
        ("regsvr32.exe", f'/s "{folder / "vbx.ocx"}"', True),  # as steps.py runs it: not the last step
        (str(folder / "tool.exe"), "", False),
        ("cmd.exe", write_marker_args(folder / MARKER_NAME, sha(data)), True),  # the marker, once every step succeeded
    ]
    assert write_marker_args(folder / MARKER_NAME, sha(data)) == f'/d /v:off /s /c "(echo {sha(data)}) > "{folder / MARKER_NAME}""'
    assert (folder / MARKER_NAME).read_bytes() == marker_bytes(sha(data))


def test_staging_file_swapped_during_the_prompt_is_caught_and_nothing_is_registered(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)

    def swap(exe, args, elevated):
        if exe == "cmd.exe":  # code running as the player replaces the file the elevated command will copy
            next(p for p in tmp_path.rglob("dx8vb.dll") if "unpacked" in p.parts).write_bytes(b"MZ planted")

    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(on_start=swap))
    folder = eng.ctx.runtimes_root / "dx8vb"
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    # The elevated chain checks each copy against the download's checksum before anyone may read the folder
    # (R44): the planted bytes are deleted again, the chain stops, and the folder stays sealed with no marker.
    assert ei.value.message == messages.RUNTIME_FOLDER_NOT_SECURED.format(title=DX8VB_TITLE, path=folder)
    assert [c[0] for c in eng.ctx.elevator.calls] == ["cmd.exe"]  # regsvr32 never ran
    assert folder.is_dir() and not (folder / "dx8vb.dll").exists() and not (folder / MARKER_NAME).exists()
    assert eng.ctx.records.load("dx8vb") is None and not (lib / "Entombed").exists()


def test_swapped_staging_file_is_still_caught_unelevated_if_the_chains_check_let_it_through(tmp_path):
    """Defence in depth: the unelevated walk and re-hash after the lock-down still refuse a wrong file."""
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "dx8vb"
    real_start = eng.ctx.elevator.start

    def start(exe, args, elevated, cwd=None, show=True):
        process = real_start(exe, args, elevated, cwd, show)
        if "icacls" in args:  # as if certutil had been fooled: the file is wrong once the chain is done
            (folder / "dx8vb.dll").write_bytes(b"MZ planted")
        return process
    eng.ctx.elevator.start = start
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_CHANGED.format(title=DX8VB_TITLE, path=folder)
    assert [c[0] for c in eng.ctx.elevator.calls] == ["cmd.exe"]  # regsvr32 never ran
    assert not (folder / MARKER_NAME).exists() and eng.ctx.records.load("dx8vb") is None


def test_folder_planted_during_the_prompt_stops_the_elevated_command_and_registers_nothing(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)

    def plant(exe, args, elevated):
        if exe == "cmd.exe":  # the folder appears between the engine's check and the elevated mkdir
            (tmp_path / "runtimes" / "dx8vb").mkdir()

    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(on_start=plant))
    folder = eng.ctx.runtimes_root / "dx8vb"
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_NOT_SECURED.format(title=DX8VB_TITLE, path=folder)
    assert [c[0] for c in eng.ctx.elevator.calls] == ["cmd.exe"]
    assert snapshot(folder) == {} and eng.ctx.records.load("dx8vb") is None


@pytest.mark.windows
@pytest.mark.parametrize("swapped", ["id", "runtimes"])
def test_junction_planted_at_the_folder_or_a_parent_during_the_prompt_stops_the_elevated_command(tmp_path, swapped):
    import _winapi
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    root = tmp_path / "GameChooser" / "runtimes"
    victim = tmp_path / "victim"
    victim.mkdir()

    def swap(exe, args, elevated):
        if exe != "cmd.exe":
            return
        # Code running as the player: a junction where the runtime's folder will be made, or the `runtimes`
        # folder moved away and a junction to somewhere else put in its place.
        path = root / "dx8vb" if swapped == "id" else root
        if path.exists():
            path.rename(path.with_name(path.name + "-moved"))
        _winapi.CreateJunction(str(victim), str(path))

    eng, lib = setup(tmp_path, [rt, g], routes, runtimes_root=root, elevator=FakeElevator(on_start=swap))
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_NOT_SECURED.format(title=DX8VB_TITLE, path=root / "dx8vb")
    assert [c[0] for c in eng.ctx.elevator.calls] == ["cmd.exe"]  # regsvr32 never ran
    assert list(victim.iterdir()) == [] and eng.ctx.records.load("dx8vb") is None  # nothing made inside it


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
    assert eng.ctx.elevator.calls == [*lock_down(eng, root / "dx8vb"), regsvr32(eng, root / "dx8vb"),
                                      *lock_down(eng, root / "vbx", {"vbx.ocx": vbx_data}, game="pawprints"),
                                      regsvr32(eng, root / "vbx", "vbx.ocx", game="pawprints", source_sha=sha(vbx_data))]
    assert all('icacls "%s" /grant *S-1-5-32-545:(AD) /L /Q' % root in c[1] for c in eng.ctx.elevator.calls[::2])


def adoptable_folder(eng, files: dict[str, bytes] | None = None) -> Path:
    """A dx8vb folder locked by an earlier install (owned by Administrators) that lost its marker."""
    folder = eng.ctx.runtimes_root / "dx8vb"
    folder.mkdir(parents=True)
    eng.ctx.security.set_owner(folder, ADMINISTRATORS)
    for rel, data in ({"dx8vb.dll": DX8VB} if files is None else files).items():
        (folder / rel).write_bytes(data)
        eng.ctx.security.set_owner(folder / rel, ADMINISTRATORS)
    return folder


@pytest.mark.windows
@pytest.mark.parametrize("name, outcome", [("dx8vb.dll", "RUNTIME_FOLDER_NOT_SECURED"),
                                           ("planted", "RUNTIME_FOLDER_CHANGED")])
def test_junction_planted_inside_a_found_folder_during_the_prompt_is_refused(tmp_path, name, outcome):
    import _winapi
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "dx8vb.dll").write_bytes(b"the victim's own file")
    eng, lib = setup(tmp_path, [rt, g], routes)
    # Under the expected name, the file is missing and would be copied: the elevated command refuses to copy
    # onto something that appeared. Under another name, the post-lock walk refuses the folder.
    folder = adoptable_folder(eng, files={} if name == "dx8vb.dll" else None)
    eng.ctx.elevator.on_start = lambda exe, args, elevated: (
        _winapi.CreateJunction(str(victim), str(folder / name)) if exe == "cmd.exe" else None)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == getattr(messages, outcome).format(title=DX8VB_TITLE, path=folder)
    assert [c[0] for c in eng.ctx.elevator.calls] == ["cmd.exe"]  # regsvr32 never ran
    assert snapshot(victim) == {"dx8vb.dll": b"the victim's own file"}  # nothing written through the junction
    assert eng.ctx.records.load("dx8vb") is None


def test_unexpected_file_planted_in_a_found_folder_during_the_prompt_is_refused(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = adoptable_folder(eng)
    eng.ctx.elevator.on_start = lambda exe, args, elevated: (
        (folder / "dx8vbhelper.dll").write_bytes(b"MZ extra") if exe == "cmd.exe" else None)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_CHANGED.format(title=DX8VB_TITLE, path=folder)
    assert [c[0] for c in eng.ctx.elevator.calls] == ["cmd.exe"]


def test_hard_linked_file_in_a_found_folder_is_refused(tmp_path):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = adoptable_folder(eng)
    eng.ctx.elevator.on_start = lambda exe, args, elevated: (
        os.link(folder / "dx8vb.dll", tmp_path / "elsewhere.dll") if exe == "cmd.exe" else None)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.RUNTIME_FOLDER_CHANGED.format(title=DX8VB_TITLE, path=folder)
    assert [c[0] for c in eng.ctx.elevator.calls] == ["cmd.exe"]


def test_hard_link_in_staging_is_copied_and_its_other_name_is_left_alone(tmp_path):
    """R41's case: the player's staging file is a hard link to another file. The elevated command copies
    its bytes into a new file, so the other file is never touched, and the install goes through."""
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    victim = tmp_path / "victim.dll"
    victim.write_bytes(DX8VB)

    def link(exe, args, elevated):
        if exe == "cmd.exe":
            staged = next(p for p in tmp_path.rglob("dx8vb.dll") if "unpacked" in p.parts)
            staged.unlink()
            os.link(victim, staged)

    eng, lib = setup(tmp_path, [rt, g], routes, elevator=FakeElevator(on_start=link))
    run(eng, "install")
    folder = eng.ctx.runtimes_root / "dx8vb"
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB and os.stat(folder / "dx8vb.dll").st_nlink == 1
    assert os.stat(victim).st_nlink == 1  # the staging was cleared; the copy is its own file
    assert eng.ctx.elevator.calls[1] == regsvr32(eng, folder) and eng.ctx.records.load("dx8vb")


def test_runtime_with_a_subfolder_is_made_and_sealed_parent_first(tmp_path):
    routes = {}
    data = zip_bytes({"vbx.ocx": b"MZ vbx", "lib/helper.dll": b"MZ helper"})
    vbx = {"id": "vbx", "kind": "runtime", "title": "VBX", "version": "1",
           "sources": [served_source(routes, f"{MIRROR}{sha(data)}/vbx.zip", data,
                                     steps=[{"action": "register", "path": "vbx.ocx"}])]}
    eng, lib = setup(tmp_path, [vbx, game(routes, dependencies=["vbx"])], routes)
    run(eng, "install")
    folder = eng.ctx.runtimes_root / "vbx"
    [(exe, args, elevated), register] = eng.ctx.elevator.calls
    commands = icacls_commands(args)
    sub = folder / "lib"
    seal_sub = commands.index(f'icacls "{sub}" /inheritance:r /grant:r {ADMIN_ONLY} /L /Q')
    assert commands.index(f'mkdir "{sub}"') < commands.index(f'icacls "{sub}" /setowner *S-1-5-32-544 /L /Q') < seal_sub
    assert commands.index(f'copy /b "{staging_of(eng, folder) / "lib" / "helper.dll"}" "{sub / "helper.dll"}" >nul') > seal_sub
    assert commands[-2:] == [opened(folder), opened(sub)]
    assert register == regsvr32(eng, folder, "vbx.ocx", source_sha=sha(data))
    assert (folder / "lib" / "helper.dll").read_bytes() == b"MZ helper"
    assert set(eng.ctx.records.load("vbx").files) == {"vbx.ocx", "lib/helper.dll"}


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
    folder = adoptable_folder(eng)  # it may already have been registered by another account
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message.startswith(messages.STEP_FILE_MISSING.format(path="setup.exe"))
    assert eng.ctx.elevator.calls == [*lock_down(eng, folder, existing={"dx8vb.dll"}),
                                      ("regsvr32.exe", f'/s "{folder / "dx8vb.dll"}"', True)]  # not the last step; no /u
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB


def test_failure_after_registering_in_a_folder_it_made_unregisters(tmp_path):
    routes = {}
    rt, g = failing_after_register(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes)
    folder = eng.ctx.runtimes_root / "dx8vb"
    with pytest.raises(InstallFailed):
        run(eng, "install")
    assert eng.ctx.elevator.calls[-1] == ("regsvr32.exe", f'/u /s "{folder / "dx8vb.dll"}"', True)
    assert snapshot(folder) == {"dx8vb.dll": DX8VB}  # the sealed folder stays (R39), with no marker


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
    assert exes[:2] == ["cmd.exe", "cmd.exe"] and exes[2].endswith("setup.exe")  # lock-down, regsvr32, then the game
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
    assert eng.ctx.elevator.calls == [*lock_down(eng, folder), regsvr32(eng, folder)]
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
    # No private COM copy, but its DLL is still copied beside the exe, for a load by name.
    assert (folder / "entombed.exe").exists() and (folder / "dx8vb.dll").read_bytes() == DX8VB
    assert not (folder / "entombed.exe.manifest").exists() and eng.ctx.elevator.calls == []
    assert set(eng.ctx.records.load("entombed").files) == {"entombed.exe", "sounds/a.wav", "dx8vb.dll"}
    assert eng.ctx.records.load("entombed").private_runtimes == []


def test_preference_off_private_com_not_supported_notes_and_installs(tmp_path, private_com, monkeypatch):
    monkeypatch.setattr(comfree, "PRIVATE_COM_SUPPORTED", False)
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    r = run(eng, "install")
    assert r.notes == [messages.NEEDS_SHARED_COMPONENT.format(title="Entombed", component=DX8VB_TITLE)]
    assert not (lib / "Entombed" / "entombed.exe.manifest").exists() and eng.ctx.elevator.calls == []
    assert (lib / "Entombed" / "dx8vb.dll").read_bytes() == DX8VB  # copied beside the exe all the same


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


@pytest.mark.parametrize("shared", [True, False], ids=["shared", "private"])
def test_runtime_whose_developer_file_changed_is_installed_with_the_note(tmp_path, private_com, shared):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    rt["sources"][0].update(label="Developer", url="https://dev.example.com/dx8vb.dll")
    routes[rt["sources"][0]["url"]] = b"MZ the developer re-uploaded this"
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=shared)
    r = run(eng, "install")
    assert r.notes == [messages.DEVELOPER_CHANGED_UNVERIFIED]


@pytest.mark.parametrize("shared", [True, False], ids=["shared", "private"])
def test_full_disk_while_unpacking_a_runtime_gets_plain_words(tmp_path, private_com, shared):
    class FullDisk(ZipExtractor):
        def extract(self, archive, dest, install, cancel):
            if Path(archive).name == "dx8vb.dll":
                raise OSError(errno.ENOSPC, "No space left on device")
            return super().extract(archive, dest, install, cancel)

    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=shared, extractor=FullDisk())
    with pytest.raises(InstallFailed) as ei:
        run(eng, "install")
    assert ei.value.message == messages.DISK_FULL.format(title="Entombed")


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
    x = {"id": "lc-tracks", "kind": "extra", "title": "Tracks", "extends": ["lightcars"], "version": "1",
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
    assert not (folder / "lightcars.exe.manifest").exists() and eng.ctx.records.load("dx8vb").kind == "runtime"
    # The DLL beside the exe is now the copy from the shared folder.
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB
    assert {k for k, f in eng.ctx.records.load("lightcars").files.items() if f.owner.startswith("runtime:")} == \
        {"dx8vb.dll"}


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
    assert not (folder / "lightcars.exe.manifest").exists()
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB  # copied beside the exe all the same
    assert set(eng.ctx.records.load("lightcars").files) == {"lightcars.exe", "data/track.dat", "dx8vb.dll"}
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
    assert not (folder / "entombed.exe.manifest").exists()
    assert rt["sources"][0]["url"] not in eng.ctx.fetcher.requests[1:]
    # The copy of its DLL the shared install made stays: there is nothing to copy it from now, and it is unchanged.
    assert (folder / "dx8vb.dll").read_bytes() == DX8VB
    assert eng.ctx.records.load("entombed").files["dx8vb.dll"].owner == "runtime:dx8vb"


def test_undo_makes_the_previous_versions_private_copies(tmp_path, private_com):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    run(eng, "install")
    eng.ctx.register_shared_components = True  # v2 is installed with the runtime shared
    eng.ctx.entries["entombed"] = game(eng.ctx.fetcher.routes, files={"entombed.exe": b"exe 2"}, version="2.0")
    run(eng, "update")
    folder = lib / "Entombed"
    assert eng.ctx.records.load("entombed").private_runtimes == []
    assert not (folder / "entombed.exe.manifest").exists() and (folder / "dx8vb.dll").exists()  # the shared copy
    run(eng, "undo")
    assert (folder / "dx8vb.dll").exists() and (folder / "entombed.exe.manifest").exists()
    assert eng.ctx.records.load("entombed").private_runtimes == ["dx8vb"]


def test_older_record_without_the_field_is_read_from_its_files(tmp_path, private_com):
    routes = {}
    rt, g = dx8vb(routes), game(routes)
    eng, lib = setup(tmp_path, [rt, g], routes, register_shared_components=False)
    run(eng, "install")

    def older(rec):
        # Written before `private_runtimes` and `runtime_copies` were kept: a private copy was the game's.
        rec.private_runtimes = rec.runtime_copies = None
        rec.files["dx8vb.dll"].owner = "entombed"
        return rec

    rec = older(eng.ctx.records.load("entombed"))
    assert recorded_private(rec, g, eng.ctx.entries) == ["dx8vb"]  # its DLL beside a recorded manifest
    eng.ctx.records.save(rec)
    run(eng, "repair")
    assert (lib / "Entombed" / "dx8vb.dll").exists()

    shipped = older(eng.ctx.records.load("entombed"))
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
