from datetime import date

import pytest

from engine.errors import UnsafePath
from engine.paths import (backup_dir_name, is_engine_dir, norm_key,
                          safe_join, sanitize_title, unique_path)


def test_sanitize_title():
    assert sanitize_title('Top Speed 2: "Turbo"?') == "Top Speed 2 Turbo"
    assert sanitize_title("con") == "con_"
    assert sanitize_title("  Game.  ") == "Game"
    assert sanitize_title("///") == "Game"


def test_safe_join_rejects_escapes(tmp_path):
    for bad in ["..\\x", "../x", "a/../../x", "C:\\Windows\\x", "\\\\server\\share\\x", "/etc/x", "\\x"]:
        with pytest.raises(UnsafePath):
            safe_join(tmp_path, bad)
    assert safe_join(tmp_path, "data/a.txt") == tmp_path / "data" / "a.txt"


def test_engine_dirs():
    assert backup_dir_name(date(2026, 11, 4)) == "Game Chooser backup 2026-11-04"
    assert is_engine_dir(".gamechooser-staging") and is_engine_dir("Game Chooser backup 2026-11-04")
    assert not is_engine_dir("Saves")


def test_norm_key():
    assert norm_key("Data\\Config.INI") == norm_key("data/config.ini") == "data/config.ini"


def test_unique_path(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    assert unique_path(tmp_path / "a.txt") == tmp_path / "a (2).txt"
    (tmp_path / "a (2).txt").write_text("x")
    assert unique_path(tmp_path / "a.txt") == tmp_path / "a (3).txt"
    assert unique_path(tmp_path / "new.txt") == tmp_path / "new.txt"
