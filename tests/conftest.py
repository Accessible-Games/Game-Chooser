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
