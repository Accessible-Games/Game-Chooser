import os
import subprocess
from pathlib import Path

import pytest

from engine.launch import find_launch_exe, select_best_executable
from engine.paths import STAGING_DIR_NAME
from exception_manager import ExceptionManager

EXCL = ExceptionManager().should_auto_exclude


def touch(root: Path, rel: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    return p


def test_launch_path_used_when_present(tmp_path):
    touch(tmp_path, "Other.exe")
    run = touch(tmp_path, "bin/Run.exe")
    assert find_launch_exe(tmp_path, "bin/Run.exe", EXCL) == run


def test_missing_launch_path_falls_back_to_detection(tmp_path):
    (tmp_path / "Copter Mission.exe").touch()
    assert find_launch_exe(tmp_path, "copter.exe", EXCL).name == "Copter Mission.exe"


def test_unsafe_launch_path_falls_back(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    touch(tmp_path, "x.exe")
    touch(game, "Real.exe")
    assert find_launch_exe(game, "..\\x.exe", EXCL).name == "Real.exe"
    assert find_launch_exe(game, str(tmp_path / "x.exe"), EXCL).name == "Real.exe"


def test_launch_path_through_junction_is_ignored(tmp_path):
    outside = tmp_path / "outside"
    touch(outside, "Evil.exe")
    game = tmp_path / "game"
    game.mkdir()
    touch(game, "Real.exe")
    link = game / "lnk"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("cannot create link")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                       check=True, capture_output=True)
    assert find_launch_exe(game, "lnk/Evil.exe", EXCL).name == "Real.exe"


def test_search_does_not_descend_links(tmp_path):
    outside = tmp_path / "outside"
    touch(outside, "Evil.exe")
    game = tmp_path / "game"
    game.mkdir()
    link = game / "lnk"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("cannot create link")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                       check=True, capture_output=True)
    assert find_launch_exe(game, None, EXCL) is None


def test_prefers_game_then_folder_name_and_skips_setup(tmp_path):
    game = tmp_path / "Sound Quest"
    for n in ("setup.exe", "unins000.exe", "Sound Quest.exe", "game.exe"):
        touch(game, n)
    assert find_launch_exe(game, None, EXCL).name == "game.exe"
    (game / "game.exe").unlink()
    assert find_launch_exe(game, None, EXCL).name == "Sound Quest.exe"


def test_shallowest_folder_wins(tmp_path):
    touch(tmp_path, "tools/a.exe")
    touch(tmp_path, "Main.exe")
    assert find_launch_exe(tmp_path, None, EXCL).name == "Main.exe"


def test_bat_files_count(tmp_path):
    touch(tmp_path, "start.bat")
    assert find_launch_exe(tmp_path, None, EXCL).name == "start.bat"


def test_engine_dirs_ignored_and_none_when_nothing(tmp_path):
    touch(tmp_path, f"{STAGING_DIR_NAME}/Staged.exe")
    touch(tmp_path, "Game Chooser backup 2026-11-04/Old.exe")
    touch(tmp_path, "setup.exe")
    assert find_launch_exe(tmp_path, None, EXCL) is None


def test_depth_limit(tmp_path):
    deep = "/".join(f"d{i}" for i in range(10))
    touch(tmp_path, f"{deep}/ok.exe")
    assert find_launch_exe(tmp_path, None, EXCL).name == "ok.exe"
    too_deep = "/".join(f"d{i}" for i in range(11))
    other = tmp_path / "other"
    touch(other, f"{too_deep}/no.exe")
    assert find_launch_exe(other, None, EXCL) is None


def test_select_best_executable_rules():
    p = [Path("a/zzz.exe"), Path("a/Play.exe"), Path("a/Game.exe")]
    assert select_best_executable(p, "x") == Path("a/Game.exe")
    assert select_best_executable([Path("q.exe"), Path("Dir.exe")], "dir") == Path("Dir.exe")
    assert select_best_executable([Path("q.exe"), Path("r.exe")], "dir") == Path("q.exe")
    assert select_best_executable([], "dir") is None
