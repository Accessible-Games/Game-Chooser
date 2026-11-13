import re
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parent.parent.parent
ENGINE_DIR = REPO_DIR / "engine"

# Top-level modules that must stay wx-free; append as they are added.
WX_FREE_MODULES = ["library_manager.py", "install_service.py", "catalog_view.py", "library_actions.py", "install_prefs.py",
                   "install_choices.py", "language_names.py"]

_WX_IMPORT = re.compile(r"^\s*(import wx|from wx)", re.M)


@pytest.mark.parametrize("files", [
    pytest.param(list(ENGINE_DIR.rglob("*.py")), id="engine"),
    pytest.param([p for p in (REPO_DIR / "tracing").rglob("*.py") if p.name != "app_glue.py"],
                 id="tracing except app_glue"),
])
def test_never_imports_wx(files):
    assert files
    for py in files:
        assert not _WX_IMPORT.search(py.read_text(encoding="utf-8")), py


def test_wx_free_modules_never_import_wx():
    for name in WX_FREE_MODULES:
        path = REPO_DIR / name
        assert path.exists(), path
        assert not _WX_IMPORT.search(path.read_text(encoding="utf-8")), path


_TRACING_IMPORT = re.compile(r"^\s*(import tracing|from tracing)", re.M)
_SKIP_DIRS = {"tracing", "tests", "scripts", ".venv", "smart_list", "resource_finder", "build"}


def test_nothing_outside_tracing_imports_it():
    for py in REPO_DIR.rglob("*.py"):
        rel = py.relative_to(REPO_DIR)
        if rel.parts[0] in _SKIP_DIRS or any(part.startswith(".") for part in rel.parts):
            continue
        text = py.read_text(encoding="utf-8")
        if rel.as_posix() == "game-chooser.py":
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if _TRACING_IMPORT.match(line):
                    assert i > 0 and lines[i - 1].strip() == "try:", (rel, i + 1)
            continue
        assert not _TRACING_IMPORT.search(text), rel
