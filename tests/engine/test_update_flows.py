"""Update, repair, undo, extras and uninstall: the data-safety rule under every flow.

The fixture is Pawprints, installed at v1 with a required and an optional extra, then played:
the player edited pawprints.ini and has two save slots, a self-updater left update.tmp and
rewrote bass.dll. v2 changes the exe, the ini and the dll, removes sounds/a.wav and adds
sounds/b.wav.
"""

import hashlib
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest

import engine.safety
from engine import messages
from engine.entries import mirror_url_for
from engine.errors import InstallFailed
from engine.jobs import InstallRequest
from engine.records import InstalledSource, InstallRecord, RecordedFile
from tests.engine.fakes import FakeElevator, FakeFetcher, make_engine, served_source, zip_bytes

MIRROR = "https://files.accessiblegames.net/files/"
DEV_URL = "https://dev.example.com/pawprints.zip"
BK = "Game Chooser backup 2026-11-04"

V1 = {"pawprints.exe": b"exe v1", "sounds/a.wav": b"a", "pawprints.ini": b"volume=5", "bass.dll": b"bass v1"}
V2 = {"pawprints.exe": b"exe v2", "pawprints.ini": b"volume=7", "bass.dll": b"bass v2", "sounds/b.wav": b"b"}
EDITED_INI = b"volume=9 the player's choice"
PLAYER_FILES = {"Saves/slot1.sav": b"save one", "Saves/slot2.sav": b"save two", "update.tmp": b"half a download"}
SELF_UPDATED_DLL = b"bass from the self-updater"


def never():
    return False


def quiet(p):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(root: Path, rel: str, data: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def pawprints(routes, files, version, url=None, steps=None, install=None, **fields):
    data = zip_bytes(files)
    source = served_source(routes, url or f"{MIRROR}{sha(data)}/pawprints.zip", data, steps=steps,
                           **(install or {}))
    return {"id": "pawprints", "kind": "game", "title": "Pawprints", "developer": ["Kitty Games"],
            "genres": ["Adventure"], "year": 2010, "version": version, "sources": [source],
            "extras": [{"id": "pawprints-patch", "required": True},
                       {"id": "pawprints-music", "required": False},
                       {"id": "pawprints-voices", "required": False}], **fields}


def extra(routes, id, files, steps=None):
    data = zip_bytes(files)
    source = served_source(routes, f"{MIRROR}{sha(data)}/{id}.zip", data, steps=steps)
    return {"id": id, "kind": "extra", "title": id.replace("-", " ").title(), "install_into": "pawprints",
            "version": "1", "sources": [source]}


def install_v1(tmp_path, url=None, steps=None, optional=("pawprints-music",), play=True, **ctx):
    """Pawprints v1 installed with its required extra and `optional`, then played (see the module doc)."""
    routes = {}
    entries = [
        pawprints(routes, V1, "1.0", url=url, steps=steps),
        extra(routes, "pawprints-patch", {"data/patch.dat": b"patch v1"}),
        extra(routes, "pawprints-music", {"music/theme.ogg": b"theme"}),
        extra(routes, "pawprints-voices", {"voices/hi.ogg": b"hi", "sounds/a.wav": b"a"}),
    ]
    eng = make_engine(tmp_path, entries, fetcher=FakeFetcher(routes), **ctx)
    eng.run(InstallRequest("install", "pawprints", "Games", extras=tuple(optional)), quiet, never)
    game = tmp_path / "Games" / "Pawprints"
    if play:
        put(game, "pawprints.ini", EDITED_INI)
        put(game, "bass.dll", SELF_UPDATED_DLL)
        for rel, data in PLAYER_FILES.items():
            put(game, rel, data)
    return eng, game


def publish(eng, entry_or_files, version="2.0", url=None, steps=None, **fields) -> dict:
    """Put a new version of Pawprints (or a whole new entry) in the catalog and serve its download."""
    routes = eng.ctx.fetcher.routes
    if isinstance(entry_or_files, dict) and "id" in entry_or_files:
        entry = entry_or_files
    else:
        entry = pawprints(routes, entry_or_files, version, url=url, steps=steps, **fields)
    eng.ctx.entries[entry["id"]] = entry
    return entry


def run(eng, action, **kw):
    return eng.run(InstallRequest(action, kw.pop("id", "pawprints"), "Games", **kw), quiet, never)


def record(eng, id="pawprints") -> InstallRecord:
    return eng.ctx.records.load(id)


# ---- update ------------------------------------------------------------------


def test_update_keeps_every_save_byte_for_byte(tmp_path):
    eng, game = install_v1(tmp_path)
    publish(eng, V2)
    run(eng, "update")
    for rel, data in PLAYER_FILES.items():
        assert (game / rel).read_bytes() == data


def test_update_backs_up_edited_config_and_counts_it(tmp_path):
    eng, game = install_v1(tmp_path)
    publish(eng, V2)
    r = run(eng, "update")
    assert r.action == "update" and r.backups == 1
    assert messages.update_summary(r.title, r.backups) == "Updated Pawprints. Kept a backup of 1 file you'd changed."
    assert (game / BK / "pawprints.ini").read_bytes() == EDITED_INI
    assert (game / "pawprints.ini").read_bytes() == V2["pawprints.ini"]
    assert record(eng).previous["backups"] == [
        {"original": "pawprints.ini", "backup": f"{BK}/pawprints.ini", "installed_sha256": sha(V2["pawprints.ini"])}]


def test_update_replaces_changed_dll_and_exe_without_copy(tmp_path):
    eng, game = install_v1(tmp_path)
    publish(eng, V2)
    run(eng, "update")
    assert (game / "bass.dll").read_bytes() == V2["bass.dll"]
    assert (game / "pawprints.exe").read_bytes() == V2["pawprints.exe"]
    assert sorted(snapshot(game / BK)) == ["pawprints.ini"]


def test_update_deletes_removed_unchanged_file_and_adds_new(tmp_path):
    eng, game = install_v1(tmp_path)
    publish(eng, V2)
    r = run(eng, "update")
    assert not (game / "sounds" / "a.wav").exists()
    assert (game / "sounds" / "b.wav").read_bytes() == b"b"
    rec = record(eng)
    assert set(rec.files) == {"pawprints.exe", "pawprints.ini", "bass.dll", "sounds/b.wav", "data/patch.dat",
                              "music/theme.ogg"}
    assert rec.files["sounds/b.wav"] == RecordedFile("sounds/b.wav", sha(b"b"), "pawprints")
    assert r.game["launch_path"] == "Pawprints/pawprints.exe" and rec.launch_path == "pawprints.exe"
    assert not (tmp_path / "Games" / ".gamechooser-staging" / "pawprints").exists()
    assert not (eng.ctx.work_root / "pawprints").exists()


def test_update_restores_quarantined_missing_file(tmp_path):
    eng, game = install_v1(tmp_path)
    (game / "data" / "patch.dat").unlink()      # quarantined; the required extra still ships it
    (game / "pawprints.exe").unlink()
    publish(eng, V2)
    r = run(eng, "update")
    assert (game / "data" / "patch.dat").read_bytes() == b"patch v1"
    assert (game / "pawprints.exe").read_bytes() == V2["pawprints.exe"]
    assert r.backups == 1


def test_update_never_touches_persist_paths(tmp_path):
    eng, game = install_v1(tmp_path)
    publish(eng, {**V2, "Saves/slot1.sav": b"", "Saves/slot3.sav": b"empty slot"}, persist=["Saves/"])
    r = run(eng, "update")
    assert (game / "Saves" / "slot1.sav").read_bytes() == PLAYER_FILES["Saves/slot1.sav"]
    assert (game / "Saves" / "slot2.sav").read_bytes() == PLAYER_FILES["Saves/slot2.sav"]
    assert (game / "Saves" / "slot3.sav").read_bytes() == b"empty slot"   # a default where nothing was
    assert r.backups == 1 and not (game / BK / "Saves").exists()
    assert not any(key.startswith("saves/") for key in record(eng).files)


def test_update_reapplies_required_and_installed_optional_extras(tmp_path):
    eng, game = install_v1(tmp_path)
    routes = eng.ctx.fetcher.routes
    new_patch = publish(eng, extra(routes, "pawprints-patch", {"data/patch.dat": b"patch v2"}))
    publish(eng, V2)
    eng.ctx.fetcher.requests.clear()
    r = run(eng, "update")
    assert (game / "data" / "patch.dat").read_bytes() == b"patch v2"
    assert (game / "music" / "theme.ogg").read_bytes() == b"theme"
    assert not (game / "voices").exists()
    assert not any("pawprints-voices" in url for url in eng.ctx.fetcher.requests)
    rec = record(eng)
    assert set(rec.extras) == {"pawprints-patch", "pawprints-music"}
    assert rec.extras["pawprints-patch"].sha256 == new_patch["sources"][0]["sha256"]
    assert rec.files["data/patch.dat"].owner == "pawprints-patch"
    assert rec.files["music/theme.ogg"].owner == "pawprints-music"
    assert sorted(r.game["installed_extras"]) == ["pawprints-music", "pawprints-patch"]


def test_update_records_previous_and_new_source(tmp_path):
    eng, game = install_v1(tmp_path)
    v1 = asdict(record(eng).source)
    rec = record(eng)
    rec.previous_unavailable = "stale"
    eng.ctx.records.save(rec)
    v2 = publish(eng, V2)
    r = run(eng, "update")
    rec = record(eng)
    assert rec.previous["source"]["sha256"] == v1["sha256"] and rec.previous["source"] == v1
    assert set(rec.previous["extras"]) == {"pawprints-patch", "pawprints-music"}
    assert rec.source.sha256 == v2["sources"][0]["sha256"] and rec.source.version == "2.0"
    assert rec.previous_unavailable is None
    assert rec.source.install == {"method": "zip"} and rec.source.steps == []   # R17: the spec is recorded
    assert rec.previous["source"]["install"] == {"method": "zip"}
    assert r.game["installed_source"]["sha256"] == v2["sources"][0]["sha256"]


def test_update_swaps_changed_system_steps_and_keeps_unchanged_ones(tmp_path):
    def reg(name, value):
        return {"action": "registry", "hive": "HKCU", "key": r"Software\Pawprints", "name": name,
                "type": "REG_SZ", "value": value}

    eng, game = install_v1(tmp_path, steps=[reg("Path", "here"), reg("Mode", "old")])
    fake_reg = eng.ctx.registry
    publish(eng, V2, steps=[reg("Path", "here"), reg("Mode", "new")])
    run(eng, "update")
    assert fake_reg.get("HKCU", r"Software\Pawprints", "Mode") == (True, "new", "REG_SZ")
    assert fake_reg.get("HKCU", r"Software\Pawprints", "Path") == (True, "here", "REG_SZ")
    steps = record(eng).steps
    assert [(s["name"], s["value"]) for s in steps] == [("Path", "here"), ("Mode", "new")]
    # Neither undo captured a value Game Chooser itself had set.
    assert all(s["undo"] == {"existed": False, "previous": None, "previous_type": None} for s in steps)
    run(eng, "uninstall")
    assert fake_reg.values == {}


def test_failed_apply_keeps_old_version_and_records_backups(tmp_path, monkeypatch):
    eng, game = install_v1(tmp_path)
    v2 = publish(eng, V2)
    real_write = engine.safety._write_file

    def write_file(source, target, backup_to, game_dir):
        if Path(target).name == "b.wav":
            raise PermissionError("in use")
        return real_write(source, target, backup_to, game_dir)

    monkeypatch.setattr(engine.safety, "_write_file", write_file)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "update")
    assert ei.value.message == messages.FILES_NOT_CHANGED.format(title="Pawprints")
    rec = record(eng)
    assert rec.source.version == "1.0"                                       # not claimed as v2
    assert rec.files["pawprints.ini"].sha256 == sha(V2["pawprints.ini"])     # what is on disk now
    assert rec.previous["backups"] == [
        {"original": "pawprints.ini", "backup": f"{BK}/pawprints.ini", "installed_sha256": sha(V2["pawprints.ini"])}]
    for rel, data in PLAYER_FILES.items():
        assert (game / rel).read_bytes() == data

    monkeypatch.setattr(engine.safety, "_write_file", real_write)
    r = run(eng, "update")
    assert r.backups == 0 and record(eng).source.sha256 == v2["sources"][0]["sha256"]
    run(eng, "undo")
    assert (game / "pawprints.ini").read_bytes() == EDITED_INI   # the first attempt's backup still comes back


def test_failed_new_step_keeps_old_version_recorded(tmp_path):
    lib = tmp_path / "Games"
    eng, game = install_v1(tmp_path, elevator=FakeElevator(exit_codes={str(lib / "Pawprints" / "fix.exe"): 2}))
    publish(eng, {**V2, "fix.exe": b"fix"}, steps=[{"action": "run", "path": "fix.exe"}])
    with pytest.raises(InstallFailed) as ei:
        run(eng, "update")
    assert ei.value.message == messages.STEP_RUN_FAILED.format(name="fix.exe", code=2)
    rec = record(eng)
    assert rec.source.version == "1.0" and rec.files["pawprints.exe"].sha256 == sha(V2["pawprints.exe"])
    assert rec.previous["backups"][0]["original"] == "pawprints.ini"
    for rel, data in PLAYER_FILES.items():
        assert (game / rel).read_bytes() == data


def test_update_refuses_records_it_does_not_manage(tmp_path):
    eng, game = install_v1(tmp_path)
    rec = record(eng)
    rec.method = "run"
    rec.tracked = False  # an installer put it in place, and its files weren't found
    eng.ctx.records.save(rec)
    publish(eng, V2)
    before = snapshot(game)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "update")
    assert ei.value.message == messages.INSTALL_NOT_CHANGEABLE.format(title="Pawprints")
    assert snapshot(game) == before


# ---- install over a folder the game's own record owns ------------------------


def test_reinstall_over_own_folder_uses_update_rules(tmp_path):
    eng, game = install_v1(tmp_path)
    run(eng, "uninstall")
    assert record(eng).uninstalled
    r = run(eng, "install", extras=("pawprints-music",))
    assert r.action == "install" and r.backups == 1
    for rel, data in PLAYER_FILES.items():
        assert (game / rel).read_bytes() == data
    assert (game / BK / "pawprints.ini").read_bytes() == EDITED_INI
    assert snapshot(game)["pawprints.ini"] == V1["pawprints.ini"]
    assert (game / "bass.dll").read_bytes() == V1["bass.dll"]
    rec = record(eng)
    assert not rec.uninstalled and set(rec.extras) == {"pawprints-patch", "pawprints-music"}
    assert set(rec.files) == {"pawprints.exe", "pawprints.ini", "bass.dll", "sounds/a.wav", "data/patch.dat",
                              "music/theme.ogg"}


def test_reinstall_when_recorded_folder_is_gone_installs_fresh(tmp_path):
    eng, game = install_v1(tmp_path, play=False)
    shutil.rmtree(game)
    r = run(eng, "install")
    assert r.action == "install" and snapshot(game)["pawprints.exe"] == V1["pawprints.exe"]
    rec = record(eng)
    assert not rec.uninstalled and set(rec.extras) == {"pawprints-patch"}


# ---- undo --------------------------------------------------------------------


def test_undo_restores_v1_and_puts_player_copies_back(tmp_path):
    eng, game = install_v1(tmp_path)
    v1_sha = record(eng).source.sha256
    publish(eng, V2)
    run(eng, "update")
    r = run(eng, "undo")
    assert r.action == "undo"
    assert (game / "pawprints.ini").read_bytes() == EDITED_INI
    assert (game / "pawprints.exe").read_bytes() == V1["pawprints.exe"]
    assert (game / "bass.dll").read_bytes() == V1["bass.dll"]
    assert (game / "sounds" / "a.wav").read_bytes() == b"a" and not (game / "sounds" / "b.wav").exists()
    for rel, data in PLAYER_FILES.items():
        assert (game / rel).read_bytes() == data
    assert not (game / BK).exists() and r.notes == []
    rec = record(eng)
    assert rec.previous is None and rec.source.sha256 == v1_sha and rec.source.version == "1.0"
    assert rec.files["pawprints.ini"].sha256 == sha(V1["pawprints.ini"])   # the player's copy is player-owned again


def test_undo_leaves_a_backup_whose_file_changed_after_the_update(tmp_path):
    eng, game = install_v1(tmp_path)
    publish(eng, V2)
    run(eng, "update")
    put(game, "pawprints.ini", b"edited again after the update")
    r = run(eng, "undo")
    assert (game / "pawprints.ini").read_bytes() == V1["pawprints.ini"]
    assert (game / BK / "pawprints.ini").read_bytes() == EDITED_INI
    assert (game / BK / "pawprints (2).ini").read_bytes() == b"edited again after the update"
    assert r.backups == 1
    assert r.notes == [messages.UNDO_BACKUPS_KEPT.format(title="Pawprints", names="pawprints.ini")]


def test_undo_tries_recorded_url_then_mirror_path(tmp_path):
    eng, game = install_v1(tmp_path, url=DEV_URL)
    v1_sha = record(eng).source.sha256
    routes = eng.ctx.fetcher.routes
    mirror = mirror_url_for(v1_sha, "pawprints.zip")
    routes[mirror] = routes[DEV_URL]                       # the mirror kept v1
    publish(eng, V2, url=DEV_URL)                          # the developer replaced the file at the same URL
    run(eng, "update")
    eng.ctx.fetcher.requests.clear()
    run(eng, "undo")
    assert eng.ctx.fetcher.requests[:2] == [DEV_URL, mirror]
    assert (game / "pawprints.exe").read_bytes() == V1["pawprints.exe"]
    assert record(eng).source.sha256 == v1_sha


def test_undo_unavailable_when_no_exact_file(tmp_path):
    eng, game = install_v1(tmp_path, url=DEV_URL)
    publish(eng, V2, url=DEV_URL)
    run(eng, "update")
    before = snapshot(game)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "undo")
    expected = messages.UNDO_UNAVAILABLE.format(title="Pawprints")
    assert ei.value.message == expected
    rec = record(eng)
    assert rec.previous_unavailable == expected and rec.previous is not None
    assert snapshot(game) == before


def test_undo_with_nothing_to_undo_refuses(tmp_path):
    eng, game = install_v1(tmp_path)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "undo")
    assert ei.value.message == messages.NOTHING_TO_UNDO.format(title="Pawprints")


# ---- repair ------------------------------------------------------------------


def test_repair_restores_missing_and_changed_code_leaves_config(tmp_path):
    eng, game = install_v1(tmp_path)
    (game / "sounds" / "a.wav").unlink()
    put(game, "pawprints.exe", b"damaged")
    before = record(eng)
    r = run(eng, "repair")
    assert r.action == "repair" and r.backups == 0
    assert (game / "sounds" / "a.wav").read_bytes() == b"a"
    assert (game / "pawprints.exe").read_bytes() == V1["pawprints.exe"]
    assert (game / "bass.dll").read_bytes() == V1["bass.dll"]       # changed code is restored too
    assert (game / "pawprints.ini").read_bytes() == EDITED_INI
    for rel, data in PLAYER_FILES.items():
        assert (game / rel).read_bytes() == data
    after = record(eng)
    assert after.source == before.source and after.files == before.files and after.previous is None


def test_repair_reproduces_file_steps(tmp_path):
    routes = {}
    entry = pawprints(routes, {"bin/pawprints.exe": b"exe", "bin/old.dat": b"old"}, "1.0",
                      steps=[{"action": "move", "from": "bin/pawprints.exe", "to": "pawprints.exe"},
                             {"action": "delete", "path": "bin/old.dat"}])
    entry["extras"] = []
    eng = make_engine(tmp_path, [entry], fetcher=FakeFetcher(routes))
    run(eng, "install")
    game = tmp_path / "Games" / "Pawprints"
    (game / "pawprints.exe").unlink()
    run(eng, "repair")
    assert snapshot(game) == {"pawprints.exe": b"exe"}


def test_repair_refuses_when_download_changed(tmp_path):
    eng, game = install_v1(tmp_path)
    url = record(eng).source.url
    eng.ctx.fetcher.routes[url] = zip_bytes(V2)
    eng.ctx.fetcher.requests.clear()
    (game / "pawprints.exe").unlink()
    before = snapshot(game)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "repair")
    assert ei.value.message == messages.REPAIR_FILE_CHANGED.format(title="Pawprints")
    assert snapshot(game) == before
    assert eng.ctx.fetcher.requests == [url]   # the recorded URL is the mirror's copy itself


# ---- extras ------------------------------------------------------------------


def test_add_extras_later_touches_only_extra_paths(tmp_path):
    eng, game = install_v1(tmp_path, optional=())
    before_files = snapshot(game)
    before = record(eng)
    eng.ctx.fetcher.requests.clear()
    r = run(eng, "extras", extras=("pawprints-voices",))
    assert r.action == "extras" and r.backups == 0
    # The installed version is rebuilt exactly (game and installed extras), then the new extra.
    assert eng.ctx.fetcher.requests == [before.source.url, before.extras["pawprints-patch"].url,
                                        eng.ctx.entries["pawprints-voices"]["sources"][0]["url"]]
    after_files = snapshot(game)
    assert after_files == {**before_files, "voices/hi.ogg": b"hi"}
    rec = record(eng)
    assert set(rec.extras) == {"pawprints-patch", "pawprints-voices"}
    assert rec.files["voices/hi.ogg"].owner == "pawprints-voices"
    assert rec.files["sounds/a.wav"].owner == "pawprints-voices"   # same bytes as the game's: the extra owns it
    assert {k: v for k, v in rec.files.items() if k not in ("voices/hi.ogg", "sounds/a.wav")} == \
        {k: v for k, v in before.files.items() if k != "sounds/a.wav"}
    assert rec.source == before.source


def test_added_extra_runs_only_its_own_steps_and_records_them(tmp_path):
    reg = {"action": "registry", "hive": "HKCU", "key": r"Software\Pawprints", "name": "Path", "type": "REG_SZ",
           "value": "x"}
    voice_reg = {**reg, "name": "Voices", "value": "on"}
    eng, game = install_v1(tmp_path, optional=(), steps=[reg])
    routes = eng.ctx.fetcher.routes
    publish(eng, extra(routes, "pawprints-voices", {"voices/hi.ogg": b"hi"}, steps=[voice_reg]))
    eng.ctx.registry.set("HKCU", r"Software\Pawprints", "Path", "REG_SZ", "changed by the game")
    run(eng, "extras", extras=("pawprints-voices",))
    assert eng.ctx.registry.get("HKCU", r"Software\Pawprints", "Voices") == (True, "on", "REG_SZ")
    assert eng.ctx.registry.get("HKCU", r"Software\Pawprints", "Path") == (True, "changed by the game", "REG_SZ")
    assert [s["name"] for s in record(eng).steps] == ["Path", "Voices"]


# ---- uninstall ---------------------------------------------------------------


def test_uninstall_keeps_player_data_and_marks_record(tmp_path):
    eng, game = install_v1(tmp_path)
    r = run(eng, "uninstall")
    assert r.action == "uninstall" and r.kept_player_files == 5 and r.game is None
    rec = record(eng)
    assert rec.uninstalled and rec.files == {} and rec.steps == []
    assert snapshot(game) == {**PLAYER_FILES, "pawprints.ini": EDITED_INI, "bass.dll": SELF_UPDATED_DLL}
    assert eng.ctx.recycle.calls == []


def test_uninstall_with_box_ticked_recycles_and_deletes_record(tmp_path):
    eng, game = install_v1(tmp_path)
    r = run(eng, "uninstall", delete_player_data=True)
    assert r.kept_player_files == 0
    assert not game.exists() and record(eng) is None
    recycled = sorted(p.relative_to(game).as_posix() for call in eng.ctx.recycle.calls for p in call)
    assert recycled == sorted([*PLAYER_FILES, "pawprints.ini", "bass.dll"])


def test_uninstall_undoes_system_steps_but_not_runtimes(tmp_path):
    lib = tmp_path / "Games"
    dll = lib / "Pawprints" / "bass.dll"
    present_at_unregister = []

    class WatchingElevator(FakeElevator):
        def start(self, exe, args, elevated, cwd=None, show=True):
            if args.startswith("/u"):
                present_at_unregister.append(dll.exists())
            return super().start(exe, args, elevated, cwd, show)

    elevator = WatchingElevator()
    eng, game = install_v1(tmp_path, play=False, elevator=elevator, steps=[
        {"action": "register", "path": "bass.dll"},
        {"action": "registry", "hive": "HKCU", "key": r"Software\Pawprints", "name": "Path", "type": "REG_SZ",
         "value": "x"}])
    runtime_dir = eng.ctx.runtimes_root / "dx8vb"
    put(runtime_dir, "dx8vb.dll", b"runtime")
    runtime = InstallRecord(
        catalog_id="dx8vb", kind="runtime", library_id="", folder=str(runtime_dir), launch_path="",
        source=InstalledSource("https://files.accessiblegames.net/files/x/dx8vb.zip", "x", 1, "x", 1, 0, "1"),
        extras={}, files={"dx8vb.dll": RecordedFile("dx8vb.dll", sha(b"runtime"), "dx8vb")},
        steps=[{"action": "register", "path": "dx8vb.dll"}], method="zip")
    eng.ctx.records.save(runtime)
    eng.ctx.registry.set("HKCU", r"Software\DirectX", "Version", "REG_SZ", "8")

    r = run(eng, "uninstall")
    assert ("regsvr32.exe", f'/u /s "{dll}"', True) in elevator.calls and present_at_unregister == [True]
    assert eng.ctx.registry.get("HKCU", r"Software\Pawprints", "Path") == (False, None, None)
    assert eng.ctx.registry.get("HKCU", r"Software\DirectX", "Version") == (True, "8", "REG_SZ")
    assert (runtime_dir / "dx8vb.dll").read_bytes() == b"runtime"
    assert record(eng, "dx8vb") == runtime
    assert not game.exists() and record(eng) is None and r.kept_player_files == 0
    assert not any("dx8vb" in args for _, args, _ in elevator.calls)

    eng.ctx.entries["dx8vb"] = {"id": "dx8vb", "kind": "runtime", "title": "DirectX 8", "sources": []}
    with pytest.raises(InstallFailed) as ei:
        run(eng, "uninstall", id="dx8vb")
    assert ei.value.message == messages.INSTALL_NOT_CHANGEABLE.format(title="DirectX 8")
    assert (runtime_dir / "dx8vb.dll").exists() and record(eng, "dx8vb") == runtime


def test_uninstall_works_after_the_game_left_the_catalog(tmp_path):
    eng, game = install_v1(tmp_path, play=False)
    del eng.ctx.entries["pawprints"]
    r = run(eng, "uninstall")
    assert r.title == "Pawprints" and not game.exists() and record(eng) is None


def test_uninstall_after_keeping_data_can_remove_it_later(tmp_path):
    eng, game = install_v1(tmp_path)
    run(eng, "uninstall")
    r = run(eng, "uninstall", delete_player_data=True)
    assert r.kept_player_files == 0 and not game.exists() and record(eng) is None


# ---- fix round 1 ---------------------------------------------------------------

V3 = {"pawprints.exe": b"exe v3", "pawprints.ini": b"volume=8", "bass.dll": b"bass v3", "sounds/b.wav": b"b",
      "sounds/c.wav": b"c"}


def failing_write(monkeypatch, name):
    """Make writing the file called `name` fail, as if it were in use; returns a function that stops it."""
    real_write = engine.safety._write_file

    def write_file(source, target, backup_to, game_dir):
        if Path(target).name == name:
            raise PermissionError("in use")
        return real_write(source, target, backup_to, game_dir)

    monkeypatch.setattr(engine.safety, "_write_file", write_file)
    return lambda: monkeypatch.setattr(engine.safety, "_write_file", real_write)


def reg_step(name, value):
    return {"action": "registry", "hive": "HKCU", "key": r"Software\Pawprints", "name": name, "type": "REG_SZ",
            "value": value}


def test_failed_update_backup_survives_a_retry_after_an_earlier_update(tmp_path, monkeypatch):
    eng, game = install_v1(tmp_path)
    publish(eng, V2)
    run(eng, "update")                                        # previous = v1
    put(game, "pawprints.ini", b"second edit")
    publish(eng, V3, version="3.0")
    stop = failing_write(monkeypatch, "c.wav")
    with pytest.raises(InstallFailed):
        run(eng, "update")                                    # the ini was moved aside and v3's written
    stop()
    assert run(eng, "update").backups == 0
    assert record(eng).source.version == "3.0"
    run(eng, "undo")
    assert record(eng).source.version == "2.0"
    assert (game / "pawprints.ini").read_bytes() == b"second edit"


def test_failed_undo_retried_still_puts_the_players_copy_back(tmp_path, monkeypatch):
    eng, game = install_v1(tmp_path)
    publish(eng, V2)
    run(eng, "update")
    stop = failing_write(monkeypatch, "a.wav")
    with pytest.raises(InstallFailed) as ei:
        run(eng, "undo")
    assert ei.value.message == messages.FILES_NOT_CHANGED.format(title="Pawprints")
    assert (game / "pawprints.ini").read_bytes() == V1["pawprints.ini"]   # the undo got this far
    assert record(eng).previous is not None
    stop()
    run(eng, "undo")
    assert (game / "pawprints.ini").read_bytes() == EDITED_INI
    assert (game / "sounds" / "a.wav").read_bytes() == b"a" and record(eng).previous is None


def test_undo_rebuilds_the_previous_version_with_its_recorded_spec(tmp_path):
    routes = {}
    v1 = pawprints(routes, {"Pawprints v1/pawprints.exe": b"exe v1", "Pawprints v1/bin/tool.dat": b"tool"},
                   "1.0", install={"extract_dir": "Pawprints v1"},
                   steps=[{"action": "move", "from": "bin/tool.dat", "to": "tool.dat"}])
    v1["extras"] = []
    eng = make_engine(tmp_path, [v1], fetcher=FakeFetcher(routes))
    run(eng, "install")
    game = tmp_path / "Games" / "Pawprints"
    assert snapshot(game) == {"pawprints.exe": b"exe v1", "tool.dat": b"tool"}
    v2 = publish(eng, {"Pawprints v2/pawprints.exe": b"exe v2", "Pawprints v2/tool.dat": b"tool 2"},
                 install={"extract_dir": "Pawprints v2"})
    v2["extras"] = []
    run(eng, "update")
    assert snapshot(game) == {"pawprints.exe": b"exe v2", "tool.dat": b"tool 2"}
    run(eng, "undo")
    assert snapshot(game) == {"pawprints.exe": b"exe v1", "tool.dat": b"tool"}
    assert record(eng).source.install == {"method": "zip", "extract_dir": "Pawprints v1"}


def test_repair_keeps_the_recorded_steps_not_todays_catalog(tmp_path):
    eng, game = install_v1(tmp_path, steps=[reg_step("Path", "here")])
    eng.ctx.entries["pawprints"]["sources"][0]["steps"] = [reg_step("Mode", "new")]   # same file, new steps
    (game / "pawprints.exe").unlink()
    run(eng, "repair")
    assert eng.ctx.registry.get("HKCU", r"Software\Pawprints", "Path") == (True, "here", "REG_SZ")
    assert eng.ctx.registry.get("HKCU", r"Software\Pawprints", "Mode") == (False, None, None)
    assert [s["name"] for s in record(eng).steps] == ["Path"]
    assert (game / "pawprints.exe").read_bytes() == V1["pawprints.exe"]


def test_repair_works_after_an_optional_extra_left_the_catalog(tmp_path):
    eng, game = install_v1(tmp_path)
    del eng.ctx.entries["pawprints-music"]
    (game / "music" / "theme.ogg").unlink()
    run(eng, "repair")
    assert (game / "music" / "theme.ogg").read_bytes() == b"theme"


def test_repair_of_a_record_without_specs_falls_back_to_the_catalog(tmp_path):
    eng, game = install_v1(tmp_path)
    rec = record(eng)
    for source in [rec.source, *rec.extras.values()]:
        source.install = source.steps = source.launch = None   # as written before specs were recorded
    eng.ctx.records.save(rec)
    (game / "sounds" / "a.wav").unlink()
    run(eng, "repair")
    assert (game / "sounds" / "a.wav").read_bytes() == b"a"

    del eng.ctx.entries["pawprints-music"]
    before = snapshot(game)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "repair")
    assert ei.value.message == messages.REPAIR_NOT_POSSIBLE.format(title="Pawprints")
    assert snapshot(game) == before


def test_repair_prefers_the_catalogs_mirror_copy_of_the_recorded_file(tmp_path):
    routes = {}
    entry = pawprints(routes, V1, "1.0", url=DEV_URL)
    entry["extras"] = []
    data = routes[DEV_URL]
    mirror_url = f"{MIRROR}{sha(data)}/pawprints-1.0.zip"
    entry["sources"].append(served_source(routes, mirror_url, data))
    eng = make_engine(tmp_path, [entry], fetcher=FakeFetcher(routes))
    run(eng, "install")
    game = tmp_path / "Games" / "Pawprints"
    assert record(eng).source.url == DEV_URL
    eng.ctx.fetcher.routes[DEV_URL] = b"the developer re-uploaded this"
    (game / "pawprints.exe").unlink()
    eng.ctx.fetcher.requests.clear()
    run(eng, "repair")
    assert eng.ctx.fetcher.requests == [DEV_URL, mirror_url]
    assert (game / "pawprints.exe").read_bytes() == V1["pawprints.exe"]


def test_fresh_install_over_a_stale_record_undoes_its_old_steps(tmp_path):
    eng, game = install_v1(tmp_path, play=False, steps=[reg_step("Path", "here")])
    shutil.rmtree(game)
    run(eng, "install")
    assert record(eng).steps[0]["undo"] == {"existed": False, "previous": None, "previous_type": None}
    run(eng, "uninstall")
    assert eng.ctx.registry.values == {}


def test_fresh_install_when_the_records_library_is_gone_undoes_its_old_steps(tmp_path):
    eng, game = install_v1(tmp_path, play=False, steps=[reg_step("Path", "here")])
    other = tmp_path / "Other"
    eng.ctx.libraries = [{"id": "Other", "path": str(other)}]
    r = eng.run(InstallRequest("install", "pawprints", "Other"), quiet, never)
    assert r.game["library_id"] == "Other" and (other / "Pawprints" / "pawprints.exe").exists()
    rec = record(eng)
    assert rec.library_id == "Other"
    assert rec.steps[0]["undo"] == {"existed": False, "previous": None, "previous_type": None}
    assert game.exists()   # the old folder is left alone


def test_added_extra_can_copy_from_a_base_file(tmp_path):
    eng, game = install_v1(tmp_path, optional=())
    routes = eng.ctx.fetcher.routes
    publish(eng, extra(routes, "pawprints-voices", {"voices/hi.ogg": b"hi"},
                       steps=[{"action": "copy", "from": "sounds/a.wav", "to": "voices/a.wav"}]))
    before = snapshot(game)
    run(eng, "extras", extras=("pawprints-voices",))
    assert snapshot(game) == {**before, "voices/hi.ogg": b"hi", "voices/a.wav": b"a"}
    files = record(eng).files
    assert files["voices/a.wav"].owner == "pawprints-voices" and files["sounds/a.wav"].owner == "pawprints"


def test_added_extra_can_delete_a_base_file_but_not_a_players(tmp_path):
    eng, game = install_v1(tmp_path, optional=())
    routes = eng.ctx.fetcher.routes
    publish(eng, extra(routes, "pawprints-voices", {"voices/hi.ogg": b"hi"},
                       steps=[{"action": "delete", "path": "sounds/a.wav"},
                              {"action": "delete", "path": "pawprints.ini"}]))
    before = snapshot(game)
    run(eng, "extras", extras=("pawprints-voices",))
    expected = {**before, "voices/hi.ogg": b"hi"}
    del expected["sounds/a.wav"]
    assert snapshot(game) == expected                      # the player's edited ini is kept
    files = record(eng).files
    assert "sounds/a.wav" not in files and "pawprints.ini" not in files   # the kept ini is the player's now


def test_path_conflict_refuses_before_undoing_dropped_steps(tmp_path):
    eng, game = install_v1(tmp_path, steps=[reg_step("Mode", "old")])
    (game / "sounds" / "b.wav").mkdir()                     # a folder where v2 ships a file
    publish(eng, V2)                                        # v2 no longer sets Mode
    before = record(eng)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "update")
    assert ei.value.message == messages.UPDATE_PATH_CONFLICT.format(path="sounds/b.wav", kind="folder")
    assert eng.ctx.registry.get("HKCU", r"Software\Pawprints", "Mode") == (True, "old", "REG_SZ")
    assert record(eng) == before


# ---- fix round 2 ---------------------------------------------------------------


def test_repair_names_the_extra_whose_download_changed(tmp_path):
    eng, game = install_v1(tmp_path)
    music_url = record(eng).extras["pawprints-music"].url
    eng.ctx.fetcher.routes[music_url] = b"a different file"
    (game / "music" / "theme.ogg").unlink()
    before = snapshot(game)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "repair")
    assert ei.value.message == messages.REPAIR_EXTRA_CHANGED.format(extra="Pawprints Music", title="Pawprints")
    assert snapshot(game) == before


def test_repair_offline_says_try_again_later(tmp_path):
    eng, game = install_v1(tmp_path)
    eng.ctx.fetcher.routes.clear()                        # every address unreachable
    (game / "pawprints.exe").unlink()
    before = snapshot(game)
    with pytest.raises(InstallFailed) as ei:
        run(eng, "repair")
    assert ei.value.message == messages.DOWNLOAD_UNREACHABLE.format(title="Pawprints")
    assert snapshot(game) == before


def test_repair_when_the_games_download_changed_and_the_mirror_is_unreachable(tmp_path):
    eng, game = install_v1(tmp_path, url=DEV_URL)
    eng.ctx.fetcher.routes[DEV_URL] = zip_bytes(V2)       # changed; the mirror never had it
    (game / "pawprints.exe").unlink()
    with pytest.raises(InstallFailed) as ei:
        run(eng, "repair")
    assert ei.value.message == messages.REPAIR_FILE_CHANGED.format(title="Pawprints")


def test_undo_offline_says_try_again_later_and_stays_available(tmp_path):
    eng, game = install_v1(tmp_path)
    publish(eng, V2)
    run(eng, "update")
    eng.ctx.fetcher.routes.clear()
    with pytest.raises(InstallFailed) as ei:
        run(eng, "undo")
    assert ei.value.message == messages.DOWNLOAD_UNREACHABLE.format(title="Pawprints")
    rec = record(eng)
    assert rec.previous_unavailable is None and rec.previous is not None


def test_adding_extras_offline_says_try_again_later(tmp_path):
    eng, game = install_v1(tmp_path, optional=())
    eng.ctx.fetcher.routes.clear()
    with pytest.raises(InstallFailed) as ei:
        run(eng, "extras", extras=("pawprints-voices",))
    assert ei.value.message == messages.DOWNLOAD_UNREACHABLE.format(title="Pawprints")


def test_fresh_install_whose_library_cant_be_created_leaves_the_old_steps_alone(tmp_path):
    """M1: the record's folder is gone (an unplugged drive). Its registry settings are undone only once the
    new install's workspace exists; if the library folder can't be made, nothing of the old install changes."""
    eng, game = install_v1(tmp_path, play=False, steps=[reg_step("Path", "here")])
    blocker = tmp_path / "unplugged"
    blocker.write_bytes(b"not a folder")  # a library path under a file can't be created
    eng.ctx.libraries = [{"id": "Other", "path": str(blocker / "Games")}]
    with pytest.raises(InstallFailed) as ei:
        eng.run(InstallRequest("install", "pawprints", "Other"), quiet, never)
    assert ei.value.message == messages.LIBRARY_FOLDER_NOT_CREATED.format(path=blocker / "Games")
    assert eng.ctx.registry.values != {} and record(eng).steps == [{**reg_step("Path", "here"), "undo": {
        "existed": False, "previous": None, "previous_type": None}}]


def test_repair_prefers_current_catalog_hint(tmp_path):
    eng, game = install_v1(tmp_path)
    assert record(eng).launch_from_catalog is False
    eng.ctx.entries["pawprints"]["launch"] = {"windows": {"path": "pawprints.exe"}}
    run(eng, "repair")
    after = record(eng)
    assert after.launch_path == "pawprints.exe" and after.launch_from_catalog is True


def test_old_records_without_the_field_load_as_guessed(tmp_path):
    eng, game = install_v1(tmp_path)
    path = eng.ctx.records._file("pawprints")
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("launch_from_catalog")
    path.write_text(json.dumps(data), encoding="utf-8")
    assert record(eng).launch_from_catalog is False
