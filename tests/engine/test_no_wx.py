import re
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent.parent
ENGINE_DIR = REPO_DIR / "engine"

# Top-level modules that must stay wx-free; append as they are added.
WX_FREE_MODULES = ["library_manager.py", "install_service.py", "catalog_view.py", "library_actions.py", "install_prefs.py",
                   "install_choices.py"]

_WX_IMPORT = re.compile(r"^\s*(import wx|from wx)", re.M)


def test_engine_never_imports_wx():
    files = list(ENGINE_DIR.rglob("*.py"))
    assert files
    for py in files:
        assert not _WX_IMPORT.search(py.read_text(encoding="utf-8")), py


def test_wx_free_modules_never_import_wx():
    for name in WX_FREE_MODULES:
        path = REPO_DIR / name
        assert path.exists(), path
        assert not _WX_IMPORT.search(path.read_text(encoding="utf-8")), path
