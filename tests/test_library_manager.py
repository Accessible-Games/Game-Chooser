import json

import pytest

from engine import messages
from engine.folders import library_label
from library_manager import LibraryError
from path_manager import PathManager


def _seed(tmp_path):
    missing_abs = str(tmp_path / "gone" / "manual.exe")
    return [
        {"title": "Lib Game", "launch_path": "Lib Game/play.exe", "library_id": "main",
         "platforms": ["Windows"]},
        {"title": "Manual Game", "launch_path": missing_abs, "library_id": "",
         "platforms": ["Windows"]},
    ]


def test_games_with_missing_exes_are_kept(isolated_manager_factory, tmp_path):
    config = {"libraries": [{"id": "main", "path": str(tmp_path / "nolib")}]}
    m = isolated_manager_factory(games=_seed(tmp_path), config=config)
    assert len(m.games) == 2
    m2 = isolated_manager_factory()
    assert len(m2.games) == 2
    assert len(json.loads((isolated_manager_factory.app_dir / "games.json").read_text())) == 2


def test_no_scanning_api():
    from library_manager import GameLibraryManager

    for name in ("validate_and_scan", "scan_library", "scan_with_dialog", "cleanConfigs", "add_to_exceptions"):
        assert not hasattr(GameLibraryManager, name)


def test_new_config_has_no_exceptions_key(isolated_manager):
    assert "exceptions" not in isolated_manager.config


def _folder(tmp_path, *parts):
    path = tmp_path.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def test_add_library_gets_stable_id(isolated_manager, tmp_path):
    lib = isolated_manager.add_library(_folder(tmp_path, "Games"))
    assert set(lib) == {"id", "path"} and len(lib["id"]) == 12
    assert isolated_manager.library_by_id(lib["id"]) is lib
    assert isolated_manager.library_by_id("nope") is None


def test_same_basename_libraries_dont_collide(isolated_manager, tmp_path):
    from models import Game

    d = isolated_manager.add_library(_folder(tmp_path, "D", "Games"))
    e = isolated_manager.add_library(_folder(tmp_path, "E", "Games"))
    assert d["id"] != e["id"] and library_label(d) == library_label(e) == "Games"
    for lib, title in ((d, "One"), (e, "Two")):
        isolated_manager.games.append(Game(title=title, launch_path=f"{title}/play.exe", library_id=lib["id"]))
    one, two = isolated_manager.games
    assert isolated_manager.get_full_path(one) == str(tmp_path / "D" / "Games" / "One" / "play.exe")
    assert isolated_manager.get_full_path(two) == str(tmp_path / "E" / "Games" / "Two" / "play.exe")


def test_add_library_refuses_duplicate_and_nested(isolated_manager, tmp_path):
    games = _folder(tmp_path, "Games")
    isolated_manager.add_library(games)
    inner = _folder(tmp_path, "Games", "Inner")
    outer = _folder(tmp_path, "Outer")
    isolated_manager.add_library(_folder(tmp_path, "Outer", "Deep"))

    with pytest.raises(LibraryError) as e:
        isolated_manager.add_library(games.upper())
    assert str(e.value) == messages.LIBRARY_ALREADY_ADDED
    with pytest.raises(LibraryError) as e:
        isolated_manager.add_library(inner)
    assert str(e.value) == messages.LIBRARY_INSIDE.format(label="Games")
    with pytest.raises(LibraryError) as e:
        isolated_manager.add_library(outer)
    assert str(e.value) == messages.LIBRARY_CONTAINS.format(label="Deep")
    with pytest.raises(LibraryError) as e:
        isolated_manager.add_library(str(tmp_path / "missing"))
    assert str(e.value) == messages.LIBRARY_FOLDER_MISSING
    assert len(isolated_manager.config["libraries"]) == 2


def test_change_folder_keeps_games(isolated_manager, tmp_path):
    from models import Game

    lib = isolated_manager.add_library(_folder(tmp_path, "Old"))
    game = Game(title="G", launch_path="G/play.exe", library_id=lib["id"])
    isolated_manager.games.append(game)
    new = _folder(tmp_path, "New")
    isolated_manager.change_library_folder(lib["id"], new)
    assert isolated_manager.get_full_path(game) == str(tmp_path / "New" / "G" / "play.exe")
    assert isolated_manager.library_by_id(lib["id"])["id"] == lib["id"]
    # moving a library onto itself (or a part of itself) is not a conflict with itself
    isolated_manager.change_library_folder(lib["id"], _folder(tmp_path, "New", "Sub"))


def test_remove_library_changes_config_only(isolated_manager_factory, tmp_path):
    m = isolated_manager_factory(games=_seed(tmp_path), config={"libraries": [{"id": "main", "path": _folder(tmp_path, "L")}]})
    m.remove_library("main")
    m2 = isolated_manager_factory()
    assert m2.config["libraries"] == [] and len(m2.games) == 2 and (tmp_path / "L").is_dir()


def test_unreachable_library_is_kept(isolated_manager_factory, tmp_path):
    from models import Game

    m = isolated_manager_factory()
    lib = m.add_library(_folder(tmp_path, "Usb"))
    m.games.append(Game(title="G", launch_path="G/play.exe", library_id=lib["id"]))
    m.save_games()
    assert m.library_reachable(lib["id"])
    (tmp_path / "Usb").rmdir()
    m2 = isolated_manager_factory()
    assert [x["id"] for x in m2.config["libraries"]] == [lib["id"]] and len(m2.games) == 1
    assert m2.library_reachable(lib["id"]) is False
    assert m2.library_reachable("unknown") is False


def test_old_alpha_config_loads(isolated_manager_factory, tmp_path):
    games = [{"title": "Old", "launch_path": "Old/play.exe", "library_name": "Games", "platforms": ["Windows"]}]
    config = {"libraries": [{"name": "Games", "path": _folder(tmp_path, "Games")}], "exceptions": ["x/"]}
    m = isolated_manager_factory(games=games, config=config)
    assert set(m.config["libraries"][0]) == {"id", "path"} and "exceptions" not in m.config and len(m.games) == 1
    # the row is kept, but it no longer belongs to a library: unlaunchable until relocated
    assert m.games[0].library_id == "" and not hasattr(m.games[0], "library_name")


def test_get_full_path_old_name_rows_resolve_to_launch_path():
    assert PathManager.get_full_path("Old/play.exe", [{"id": "a", "path": "C:/x"}], "") == "Old/play.exe"


def test_ids_are_assigned_once(isolated_manager_factory, tmp_path):
    config = {"libraries": [{"name": "Games", "path": _folder(tmp_path, "Games")}]}
    first = isolated_manager_factory(config=config).config["libraries"][0]["id"]
    # the id given while loading the old file was saved, so a restart sees the same one
    assert isolated_manager_factory().config["libraries"][0]["id"] == first
    saved = json.loads((isolated_manager_factory.config_dir / "config.json").read_text())
    assert saved["libraries"] == [{"id": first, "path": str(tmp_path / "Games")}]


def test_libraries_on_other_drives_are_not_nested(isolated_manager_factory, tmp_path):
    from library_manager import _is_inside

    assert _is_inside(r"D:\a\b", r"D:\a") and not _is_inside(r"D:\a", r"E:\b") and not _is_inside(r"E:\b", r"D:\a")
    # an existing library on another drive letter must not make the check raise ValueError
    m = isolated_manager_factory(config={"libraries": [{"id": "e1", "path": r"E:\Games"}]})
    lib = m.add_library(_folder(tmp_path, "Games"))
    m.change_library_folder(lib["id"], _folder(tmp_path, "Games2"))
    assert len(m.config["libraries"]) == 2


def test_failed_upgrade_save_keeps_loaded_config(isolated_manager_factory, tmp_path, monkeypatch):
    from library_manager import GameLibraryManager

    old = {"libraries": [{"name": "Games", "path": _folder(tmp_path, "Games")}]}
    (isolated_manager_factory.config_dir).mkdir(exist_ok=True)
    (isolated_manager_factory.config_dir / "config.json").write_text(json.dumps(old), encoding="utf-8")
    attempts = []

    def fail(self):
        attempts.append(1)
        raise OSError("read-only")

    monkeypatch.setattr(GameLibraryManager, "save_config", fail)
    m = GameLibraryManager()
    assert attempts, "the upgrade save was never attempted"
    assert len(m.config["libraries"]) == 1 and len(m.config["libraries"][0]["id"]) == 12
    assert m.config["libraries"][0]["path"] == old["libraries"][0]["path"]


# ---- a damaged games.json or config.json is copied, never lost ------------------------------------------------

TRUNCATED_GAMES = b'[{"title": "Hand added", "launch_path": "C:/Games/hand.exe", "libr'
TRUNCATED_CONFIG = b'{"libraries": [{"id": "abc123abc123", "path": "D:/Games"}], "SavedSt'


def test_truncated_games_json_is_copied_and_the_app_keeps_working(isolated_manager_factory):
    from models import Game
    games = isolated_manager_factory.app_dir / "games.json"
    games.write_bytes(TRUNCATED_GAMES)
    m = isolated_manager_factory()
    bad = games.with_name("games.json.bad")
    assert m.games == [] and m.games_load_failed
    assert bad.read_bytes() == TRUNCATED_GAMES
    assert games.read_bytes() == TRUNCATED_GAMES  # nothing changed, so nothing was written yet
    assert m.load_problems == [messages.GAMES_FILE_DAMAGED.format(path=bad)]
    m.games.append(Game(title="New", launch_path="C:/Games/new.exe"))
    m.save_games()  # normal saves work once the copy is kept
    assert [g["title"] for g in json.loads(games.read_text(encoding="utf-8"))] == ["New"]
    assert bad.read_bytes() == TRUNCATED_GAMES
    restarted = isolated_manager_factory()
    assert [g.title for g in restarted.games] == ["New"] and restarted.load_problems == []


def test_truncated_config_json_is_copied_and_the_app_keeps_working(isolated_manager_factory, tmp_path):
    config = isolated_manager_factory.config_dir / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(TRUNCATED_CONFIG)
    m = isolated_manager_factory()
    bad = config.with_name("config.json.bad")
    assert m.config["libraries"] == [] and not m.is_first_run
    assert bad.read_bytes() == TRUNCATED_CONFIG and config.read_bytes() == TRUNCATED_CONFIG
    assert m.load_problems == [messages.CONFIG_FILE_DAMAGED.format(path=bad)]
    lib = m.add_library(_folder(tmp_path, "Games"))  # saves the config: allowed now
    assert json.loads(config.read_text(encoding="utf-8"))["libraries"] == [lib]
    assert bad.read_bytes() == TRUNCATED_CONFIG
    assert isolated_manager_factory().config["libraries"] == [lib]


def test_wrong_shape_config_is_treated_as_damaged(isolated_manager_factory):
    config = isolated_manager_factory.config_dir / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('{"libraries": ["D:/Games"]}', encoding="utf-8")  # valid JSON, a library that isn't an object
    m = isolated_manager_factory()
    assert m.config["libraries"] == []
    assert config.with_name("config.json.bad").read_text(encoding="utf-8") == '{"libraries": ["D:/Games"]}'


def test_an_earlier_bad_copy_is_never_overwritten(isolated_manager_factory):
    games = isolated_manager_factory.app_dir / "games.json"
    first = games.with_name("games.json.bad")
    first.write_bytes(b"an earlier damaged list")
    games.write_bytes(TRUNCATED_GAMES)
    m = isolated_manager_factory()
    assert first.read_bytes() == b"an earlier damaged list"
    copies = [p for p in games.parent.glob("games.json.*.bad")]
    assert len(copies) == 1 and copies[0].read_bytes() == TRUNCATED_GAMES
    assert m.load_problems == [messages.GAMES_FILE_DAMAGED.format(path=copies[0])]


def test_damaged_file_is_never_saved_over_while_no_copy_can_be_kept(isolated_manager_factory, monkeypatch):
    import library_manager
    from models import Game
    games = isolated_manager_factory.app_dir / "games.json"
    config = isolated_manager_factory.config_dir / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    games.write_bytes(TRUNCATED_GAMES)
    config.write_bytes(TRUNCATED_CONFIG)
    real_copy = library_manager.keep_damaged_copy

    def no_copy(path):
        raise PermissionError("read-only folder")
    monkeypatch.setattr(library_manager, "keep_damaged_copy", no_copy)
    m = isolated_manager_factory()
    assert m.load_problems == [messages.CONFIG_FILE_DAMAGED_NOT_COPIED.format(path=config),
                               messages.GAMES_FILE_DAMAGED_NOT_COPIED.format(path=games)]
    m.games.append(Game(title="New", launch_path="C:/Games/new.exe"))
    with pytest.raises(OSError):
        m.save_games()
    with pytest.raises(OSError):
        m.save_config()
    assert games.read_bytes() == TRUNCATED_GAMES and config.read_bytes() == TRUNCATED_CONFIG
    monkeypatch.setattr(library_manager, "keep_damaged_copy", real_copy)  # the folder is writable again
    m.save_games()
    m.save_config()
    assert games.with_name("games.json.bad").read_bytes() == TRUNCATED_GAMES
    assert config.with_name("config.json.bad").read_bytes() == TRUNCATED_CONFIG
    assert [g["title"] for g in json.loads(games.read_text(encoding="utf-8"))] == ["New"]


def test_a_failed_write_leaves_the_old_file_whole(isolated_manager_factory, monkeypatch):
    import library_manager
    from models import Game
    m = isolated_manager_factory()
    m.games.append(Game(title="Kept", launch_path="C:/Games/kept.exe"))
    m.save_games()
    m.save_config()
    games_before, config_before = m.games_file.read_bytes(), m.config_file.read_bytes()
    real_dump = json.dump

    def half_then_full_disk(data, f, **kw):
        f.write('[{"title": "Ha')
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(library_manager.json, "dump", half_then_full_disk)
    m.games.append(Game(title="Lost", launch_path="C:/Games/lost.exe"))
    with pytest.raises(OSError):
        m.save_games()
    with pytest.raises(OSError):
        m.save_config()
    monkeypatch.setattr(library_manager.json, "dump", real_dump)
    assert m.games_file.read_bytes() == games_before and m.config_file.read_bytes() == config_before
    assert not list(m.games_file.parent.glob("*.tmp")) and not list(m.config_file.parent.glob("*.tmp"))
