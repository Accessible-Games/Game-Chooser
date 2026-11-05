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
def tmp_library(tmp_path) -> Path:
    library = tmp_path / "Games"
    library.mkdir()
    return library


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
    """Build GameLibraryManagers against tmp APPDATA and sys.argv[0].

    Callable many times against the same files (a restart). Seed games.json or
    config.json with the ``games`` / ``config`` args (raw JSON values, written
    before construction), or write the files in ``factory.app_dir`` /
    ``factory.config_dir`` yourself.
    """
    import json

    appdata = tmp_path / "appdata"
    app_dir = tmp_path / "app"
    appdata.mkdir()
    app_dir.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(sys, "argv", [str(app_dir / "game-chooser.py")])

    def factory(games=None, config=None):
        from library_manager import GameLibraryManager

        config_dir = appdata / "GameChooser"
        config_dir.mkdir(exist_ok=True)
        if games is not None:
            (app_dir / "games.json").write_text(json.dumps(games), encoding="utf-8")
        if config is not None:
            (config_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        return GameLibraryManager()

    factory.app_dir = app_dir
    factory.config_dir = appdata / "GameChooser"
    return factory


@pytest.fixture
def isolated_manager(isolated_manager_factory):
    return isolated_manager_factory()
