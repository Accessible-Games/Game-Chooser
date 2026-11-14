import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def pytest_collection_modifyitems(config, items):
    skip_tools = pytest.mark.skip(reason="bin/7z.exe is absent")
    skip_windows = pytest.mark.skip(reason="Windows only")
    have_tools = (REPO_ROOT / "bin" / "7z.exe").exists()
    for item in items:
        if "tools" in item.keywords and not have_tools:
            item.add_marker(skip_tools)
        if "windows" in item.keywords and sys.platform != "win32":
            item.add_marker(skip_windows)


@pytest.fixture
def make_zip(tmp_path):
    counter = 0

    def _make(files: dict[str, bytes]) -> Path:
        nonlocal counter
        counter += 1
        path = tmp_path / f"archive{counter}.zip"
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in files.items():
                zf.writestr(name, data)
        return path

    return _make


@pytest.fixture
def isolated_manager_factory(tmp_path, monkeypatch):
    """Build GameLibraryManagers against tmp APPDATA, HOME and sys.argv[0].

    Callable many times against the same files (a restart). Seed games.json or
    config.json with the ``games`` / ``config`` args (raw JSON values, written
    before construction), or write ``factory.games_file`` and the files in
    ``factory.config_dir`` yourself.
    """
    import json
    import platform

    appdata = tmp_path / "appdata"
    home = tmp_path / "home"
    app_dir = tmp_path / "app"
    appdata.mkdir()
    home.mkdir()
    app_dir.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sys, "argv", [str(app_dir / "game-chooser.py")])
    if platform.system() == "Windows":
        config_dir = appdata / "GameChooser"
        games_file = app_dir / "games.json"
    else:
        config_dir = home / "Library" / "Application Support" / "GameChooser"
        games_file = config_dir / "games.json"
    config_dir.mkdir(parents=True)

    def factory(games=None, config=None):
        from library_manager import GameLibraryManager

        if games is not None:
            games_file.write_text(json.dumps(games), encoding="utf-8")
        if config is not None:
            (config_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        return GameLibraryManager()

    factory.app_dir = app_dir
    factory.config_dir = config_dir
    factory.games_file = games_file
    return factory


@pytest.fixture
def isolated_manager(isolated_manager_factory):
    return isolated_manager_factory()
