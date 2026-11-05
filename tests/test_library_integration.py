import json

from engine.index import DEFAULT_CATALOG_URL
from engine.records import InstalledSource, InstallRecord, RecordStore
from models import Game


def _record(catalog_id="entombed", library_id="main", folder="Entombed", **kw):
    src = InstalledSource("https://x/e.zip", "a" * 64, 50, None, None, 0, None)
    base = dict(catalog_id=catalog_id, kind="game", library_id=library_id, folder=folder,
                launch_path="e.exe", source=src, extras={"patch": src}, files={}, steps=[], method="extract")
    base.update(kw)
    return InstallRecord(**base)


def _seed_records(factory, *records):
    store = RecordStore(factory.config_dir / "installs")
    for rec in records:
        store.save(rec)


def _games_json(owner):
    """games.json as saved; `owner` is a manager or the factory fixture"""
    path = owner.games_file if hasattr(owner, "games_file") else owner.app_dir / "games.json"
    return json.loads(path.read_text())


def test_config_defaults_merge(isolated_manager_factory):
    m = isolated_manager_factory()
    assert m.config["install"] == {"catalog_url": DEFAULT_CATALOG_URL, "register_shared_components": True,
                                   "last_library": None}
    state = m.config["SavedState"]
    assert state["last_tab"] is None
    assert state["catalog_last_selected"] is None
    assert state["catalog_last_search"] == ""
    assert state["catalog_tree_selections"] == []


def test_config_defaults_merge_into_partial_old_config(isolated_manager_factory):
    m = isolated_manager_factory(config={"libraries": [], "install": {"catalog_url": "http://x/"},
                                         "SavedState": {"sort_column": 2}})
    assert m.config["install"]["catalog_url"] == "http://x/"
    assert m.config["install"]["register_shared_components"] is True
    assert m.config["SavedState"]["sort_column"] == 2
    assert m.config["SavedState"]["catalog_last_search"] == ""


def test_plain_games_round_trip_without_catalog_keys(isolated_manager):
    game = Game(title="Plain", launch_path="C:/p.exe")
    assert "catalog_id" not in game.to_dict()
    assert "installed_source" not in game.to_dict()
    assert "installed_extras" not in game.to_dict()
    assert not game.is_catalog
    isolated_manager.games = [game]
    isolated_manager.save_games()
    assert set(_games_json(isolated_manager)[0]) == {
        "title", "genre", "developer", "year", "platforms", "launch_path", "library_id"}
    full = Game(title="C", catalog_id="c", installed_source={"url": "u"}, installed_extras=["x"])
    again = Game.from_dict(full.to_dict())
    assert again.is_catalog and again.installed_source == {"url": "u"} and again.installed_extras == ["x"]


def test_catalog_game_with_missing_exe_is_kept(isolated_manager_factory, tmp_path):
    config = {"libraries": [{"id": "main", "path": str(tmp_path / "unplugged")}]}
    games = [{"title": "Entombed", "launch_path": "Entombed/e.exe", "library_id": "main",
              "catalog_id": "entombed", "installed_source": {"url": "u", "sha256": "s", "size": 1},
              "installed_extras": []}]
    _seed_records(isolated_manager_factory, _record())
    m = isolated_manager_factory(games=games, config=config)
    assert not m.library_reachable("main")
    assert [g.catalog_id for g in m.games] == ["entombed"]
    assert m.catalog_game("entombed") is m.games[0]
    assert m.catalog_game("other") is None


def test_catalog_games_in_library(isolated_manager):
    isolated_manager.games = [
        Game(title="A", library_id="one", catalog_id="a"),
        Game(title="B", library_id="two", catalog_id="b"),
        Game(title="C", library_id="one"),
        Game(title="D", library_id="", catalog_id="d"),
    ]
    assert [g.title for g in isolated_manager.catalog_games_in("one")] == ["A"]
    assert isolated_manager.catalog_games_in("none") == []


def test_add_catalog_game_replaces_and_saves_then_remove(isolated_manager):
    m = isolated_manager
    first = m.add_catalog_game({"title": "Old", "catalog_id": "e", "launch_path": "x/e.exe", "library_id": "l"})
    second = m.add_catalog_game({"title": "New", "catalog_id": "e", "launch_path": "x/e.exe", "library_id": "l",
                                 "installed_extras": ["p"]})
    assert first is not second
    assert [g.title for g in m.games] == ["New"]
    assert _games_json(m)[0]["installed_extras"] == ["p"]
    m.remove_catalog_game("e")
    assert m.games == [] and _games_json(m) == []


def test_reconcile_adds_game_for_orphan_record(isolated_manager_factory, tmp_path):
    config = {"libraries": [{"id": "main", "path": str(tmp_path)}]}
    isolated_manager_factory(config=config)
    _seed_records(isolated_manager_factory, _record())
    m = isolated_manager_factory()
    game = m.catalog_game("entombed")
    assert game is not None
    assert game.title == "entombed"  # the entry isn't cached: the id stands in for the title
    assert game.launch_path == "Entombed/e.exe" and game.library_id == "main"
    assert game.installed_extras == ["patch"]
    assert [g["catalog_id"] for g in _games_json(isolated_manager_factory)] == ["entombed"]


def test_reconcile_uses_cached_entry_for_title(isolated_manager_factory, tmp_path):
    import gzip
    config = {"libraries": [{"id": "main", "path": str(tmp_path)}]}
    isolated_manager_factory(config=config)
    cache = isolated_manager_factory.config_dir / "catalog"
    cache.mkdir()
    doc = {"schema_version": 1, "entries": [{"id": "entombed", "title": "Entombed!"}]}
    (cache / "index.json.gz").write_bytes(gzip.compress(json.dumps(doc).encode()))
    _seed_records(isolated_manager_factory, _record())
    m = isolated_manager_factory()
    assert m.catalog_game("entombed").title == "Entombed!"


def test_reconcile_skips_uninstalled_records(isolated_manager_factory, tmp_path):
    isolated_manager_factory(config={"libraries": [{"id": "main", "path": str(tmp_path)}]})
    _seed_records(isolated_manager_factory, _record(uninstalled=True))
    m = isolated_manager_factory()
    assert m.games == []


def test_reconcile_unlinks_game_whose_record_is_gone(isolated_manager_factory, tmp_path):
    config = {"libraries": [{"id": "main", "path": str(tmp_path)}]}
    games = [{"title": "Entombed", "launch_path": "Entombed/e.exe", "library_id": "main",
              "catalog_id": "entombed", "installed_source": {"url": "u", "sha256": "s", "size": 1},
              "installed_extras": ["patch"]}]
    m = isolated_manager_factory(games=games, config=config)
    assert len(m.games) == 1
    game = m.games[0]
    assert not game.is_catalog
    assert game.installed_source is None and game.installed_extras is None
    assert game.library_id == ""
    assert game.launch_path == str(tmp_path / "Entombed" / "e.exe") or \
        game.launch_path.replace("\\", "/") == (tmp_path / "Entombed" / "e.exe").as_posix()
    saved = _games_json(isolated_manager_factory)[0]
    assert "catalog_id" not in saved and saved["library_id"] == ""


def test_reconcile_saves_only_when_it_changed_something(isolated_manager_factory, tmp_path):
    games = [{"title": "Plain", "launch_path": "C:/p.exe", "library_id": ""}]
    isolated_manager_factory(games=games)
    path = isolated_manager_factory.app_dir / "games.json"
    path.write_text(json.dumps(games, indent=7), encoding="utf-8")
    isolated_manager_factory()
    assert json.loads(path.read_text()) == games
    assert path.read_text() == json.dumps(games, indent=7)


def test_failing_games_save_during_construction_leaves_manager_usable(isolated_manager_factory, tmp_path, monkeypatch):
    from library_manager import GameLibraryManager

    isolated_manager_factory(config={"libraries": [{"id": "main", "path": str(tmp_path)}]})
    _seed_records(isolated_manager_factory, _record())

    def boom(self):
        raise PermissionError("read-only")

    monkeypatch.setattr(GameLibraryManager, "save_games", boom)
    m = isolated_manager_factory()
    assert m.catalog_game("entombed") is not None


def test_corrupt_games_json_is_not_overwritten_by_reconcile(isolated_manager_factory, tmp_path):
    isolated_manager_factory(config={"libraries": [{"id": "main", "path": str(tmp_path)}]})
    _seed_records(isolated_manager_factory, _record())
    path = isolated_manager_factory.app_dir / "games.json"
    path.write_bytes(b'[{"title": "Hand added", ')
    m = isolated_manager_factory()
    assert path.read_bytes() == b'[{"title": "Hand added", '
    assert m.catalog_game("entombed") is not None  # still usable in memory


def test_unreadable_record_keeps_the_games_catalog_link(isolated_manager_factory, tmp_path):
    config = {"libraries": [{"id": "main", "path": str(tmp_path)}]}
    games = [{"title": "Entombed", "launch_path": "Entombed/e.exe", "library_id": "main",
              "catalog_id": "entombed", "installed_source": {"url": "u", "sha256": "s", "size": 1},
              "installed_extras": []}]
    installs = isolated_manager_factory.config_dir / "installs"
    installs.mkdir(parents=True)
    (installs / "entombed.json").write_text("{not json", encoding="utf-8")
    m = isolated_manager_factory(games=games, config=config)
    assert m.games[0].catalog_id == "entombed" and m.games[0].library_id == "main"
    assert _games_json(m)[0]["catalog_id"] == "entombed"


def test_web_catalog_game_without_record_keeps_its_link(isolated_manager_factory):
    games = [{"title": "Web", "launch_path": "https://example.com/play", "library_id": "",
              "catalog_id": "webgame", "platforms": ["Web"]}]
    m = isolated_manager_factory(games=games)
    assert m.catalog_game("webgame") is not None
    assert m.games[0].launch_path == "https://example.com/play"
