import json
import os

import pytest

from engine.records import (
    InstallRecord, InstalledSource, RecordedFile, RecordStore, hash_tree, record_folder,
)
from engine.sources import hash_file


def make_source(n=1):
    return InstalledSource(url=f"https://files.accessiblegames.net/files/{n}/a.zip",
                           sha256="a" * 64, size=10, catalog_sha256="b" * 64,
                           catalog_size=10, position=0, version="1.0")


def make_record(**kw):
    base = dict(
        catalog_id="entombed", kind="game", library_id="Main", folder="Entombed",
        launch_path="Entombed/entombed.exe", source=make_source(),
        extras={"patch": make_source(2)},
        files={"entombed.exe": RecordedFile("Entombed.exe", "c" * 64, "entombed")},
        steps=[{"op": "extract"}], method="extract",
    )
    base.update(kw)
    return InstallRecord(**base)


def test_round_trip(tmp_path):
    store = RecordStore(tmp_path)
    rec = make_record(previous={"source": {"url": "u"}, "extras": {}, "backups": []},
                      vendor_uninstaller="unins000.exe")
    store.save(make_record(tracked=False))
    store.save(rec)  # replaces the first, atomically
    assert [p.name for p in tmp_path.iterdir()] == ["entombed.json"]  # no temp file left
    assert store.load("entombed") == rec
    assert isinstance(store.load("entombed").files["entombed.exe"], RecordedFile)
    assert isinstance(store.load("entombed").extras["patch"], InstalledSource)
    assert store.all() == {"entombed": rec}


def test_unknown_fields_ignored_and_corrupt_kept(tmp_path, caplog):
    (tmp_path / "x.json").write_text("{not json")
    assert RecordStore(tmp_path).load("x") is None
    assert (tmp_path / "x.json").exists()
    [warning] = [r for r in caplog.records if r.levelname == "WARNING"]  # M7: never silent
    assert str(tmp_path / "x.json") in warning.getMessage() and "Expecting" in warning.getMessage()
    caplog.clear()
    assert RecordStore(tmp_path).load("absent") is None and caplog.records == []  # a missing record is normal

    store = RecordStore(tmp_path)
    store.save(make_record())
    data = json.loads((tmp_path / "entombed.json").read_text())
    data["future"] = 1
    data["source"]["future"] = 2
    data["files"]["entombed.exe"]["future"] = 3
    data["extras"]["patch"]["future"] = 4
    del data["vendor_uninstaller"], data["uninstalled"]
    (tmp_path / "entombed.json").write_text(json.dumps(data))
    assert store.load("entombed") == make_record()

    (tmp_path / "w.json").write_text('{"catalog_id": 5}')
    (tmp_path / "l.json").write_text("[]")
    assert store.load("w") is None and store.load("l") is None
    assert (tmp_path / "w.json").exists() and (tmp_path / "l.json").exists()
    assert store.load("missing") is None
    assert set(store.all()) == {"entombed"}


@pytest.mark.parametrize("bad", ["", "..", "a/b", "a" + chr(92) + "b", ".", "x/../y"])
def test_unsafe_ids_refused(tmp_path, bad):
    store = RecordStore(tmp_path)
    with pytest.raises(ValueError):
        store.load(bad)
    with pytest.raises(ValueError):
        store.delete(bad)
    with pytest.raises(ValueError):
        store.save(make_record(catalog_id=bad))


def test_delete(tmp_path):
    store = RecordStore(tmp_path)
    store.save(make_record())
    store.delete("entombed")
    store.delete("entombed")
    assert store.load("entombed") is None


def test_hash_tree_skips_engine_dirs_and_folds_case(tmp_path):
    (tmp_path / "Game.exe").write_bytes(b"g")
    (tmp_path / "Saves").mkdir()
    (tmp_path / "Saves" / "1.sav").write_bytes(b"s")
    (tmp_path / "Game Chooser backup 2026-11-04").mkdir()
    (tmp_path / "Game Chooser backup 2026-11-04" / "cfg.ini").write_bytes(b"c")
    (tmp_path / "Saves" / ".gamechooser-staging").mkdir()
    (tmp_path / "Saves" / ".gamechooser-staging" / "x").write_bytes(b"x")
    result = hash_tree(tmp_path)
    assert set(result) == {"game.exe", "saves/1.sav"}
    assert result["game.exe"][0] == "Game.exe"
    assert result["saves/1.sav"][0] == "Saves/1.sav"
    assert result["game.exe"][1] == hash_file(tmp_path / "Game.exe")


def test_record_folder_and_owner(tmp_path):
    libs = [{"id": "Main", "path": str(tmp_path / "lib")}]
    rel = make_record()
    assert record_folder(rel, libs) == tmp_path / "lib" / "Entombed"
    assert record_folder(make_record(library_id="Gone"), libs) is None
    absolute = make_record(catalog_id="abs", library_id="", folder=str(tmp_path / "elsewhere"))
    assert record_folder(absolute, libs) == tmp_path / "elsewhere"

    store = RecordStore(tmp_path / "records")
    store.save(rel)
    store.save(absolute)
    found = store.owner_of_folder(tmp_path / "lib" / "ENTOMBED", libs)
    assert found is not None and found.catalog_id == "entombed"
    assert store.owner_of_folder(tmp_path / "ELSEWHERE", libs).catalog_id == "abs"
    assert store.owner_of_folder(tmp_path / "lib" / "Other", libs) is None


def test_record_folder_by_id(tmp_path):
    libs = [{"id": "a1", "path": str(tmp_path / "A")}, {"id": "b2", "path": str(tmp_path / "B")}]
    assert record_folder(make_record(library_id="b2"), libs) == tmp_path / "B" / "Entombed"
    assert record_folder(make_record(library_id="zz"), libs) is None


def test_library_view_leaves_runtime_records_alone(tmp_path):
    store = RecordStore(tmp_path)
    game, runtime = make_record(), make_record(catalog_id="dx8vb", kind="runtime", library_id="", folder="C:/rt")
    store.save(game)
    store.save(runtime)
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert set(store.game_records()) == {"entombed"}
    assert store.load_game("entombed") == game and store.load_game("dx8vb") is None
    assert store.load_game("broken") is None and store.load_game("../x") is None and store.load_game("a/b") is None
    store.delete_game("dx8vb")  # a runtime's record is never deleted as a game's
    store.delete_game("../x")  # an unsafe id has nothing to delete
    assert store.load("dx8vb") == runtime
    store.delete_game("broken")  # an unreadable record may be the game's: it goes
    store.delete_game("entombed")
    assert not (tmp_path / "broken.json").exists() and store.load("entombed") is None


def test_a_save_that_fails_keeps_the_old_record_and_leaves_no_temp_file(tmp_path, monkeypatch):
    store = RecordStore(tmp_path)
    store.save(make_record())

    def full_disk(*args):
        raise OSError("No space left on device")
    monkeypatch.setattr(os, "replace", full_disk)
    with pytest.raises(OSError):
        store.save(make_record(folder="Moved"))
    assert store.load("entombed") == make_record()
    assert [p.name for p in tmp_path.iterdir()] == ["entombed.json"]
