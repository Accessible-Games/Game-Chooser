import json


def _seed(tmp_path):
    missing_abs = str(tmp_path / "gone" / "manual.exe")
    return [
        {"title": "Lib Game", "launch_path": "Lib Game/play.exe", "library_name": "Main",
         "platforms": ["Windows"]},
        {"title": "Manual Game", "launch_path": missing_abs, "library_name": "",
         "platforms": ["Windows"]},
    ]


def test_games_with_missing_exes_are_kept(isolated_manager_factory, tmp_path):
    config = {"libraries": [{"name": "Main", "path": str(tmp_path / "nolib")}]}
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
