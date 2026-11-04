import re
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent.parent / "engine"


def test_engine_never_imports_wx():
    files = list(ENGINE_DIR.rglob("*.py"))
    assert files
    for py in files:
        assert not re.search(r"^\s*(import wx|from wx)", py.read_text(encoding="utf-8"), re.M), py
