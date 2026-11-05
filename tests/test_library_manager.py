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
