import os
import subprocess
from pathlib import Path

import pytest

from engine.launch import find_launch_exe, select_best_executable, Started, launch_kind, page_address, start_game
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


def test_is_catalog_launch(tmp_path):
    from engine.launch import is_catalog_launch
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "Game.exe").write_bytes(b"x")
    (tmp_path / "other.exe").write_bytes(b"x")
    assert is_catalog_launch(tmp_path, tmp_path / "bin" / "Game.exe", "bin/Game.exe")
    assert not is_catalog_launch(tmp_path, tmp_path / "other.exe", "bin/Game.exe")
    assert not is_catalog_launch(tmp_path, tmp_path / "other.exe", None)
    assert not is_catalog_launch(tmp_path, tmp_path / "other.exe", "missing.exe")


def test_adopt_catalog_launch(tmp_path):
    from types import SimpleNamespace
    from engine.launch import adopt_catalog_launch
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "Game.exe").write_bytes(b"x")
    rec = SimpleNamespace(launch_path="old.exe", launch_from_catalog=False)
    entry = {"launch": {"windows": {"path": "bin/Game.exe"}}}
    assert adopt_catalog_launch(rec, entry, "windows", tmp_path) is True
    assert rec.launch_path == "bin/Game.exe" and rec.launch_from_catalog is True
    assert adopt_catalog_launch(rec, entry, "windows", tmp_path) is False
    gone = SimpleNamespace(launch_path="old.exe", launch_from_catalog=False)
    assert adopt_catalog_launch(gone, {"launch": {"windows": {"path": "nope.exe"}}}, "windows", tmp_path) is False
    assert gone.launch_path == "old.exe" and gone.launch_from_catalog is False
    assert adopt_catalog_launch(gone, {}, "windows", tmp_path) is False


def test_a_catalog_named_page_is_the_launch_file(tmp_path):
    page = touch(tmp_path, "index.html")
    touch(tmp_path, "setup.exe")
    assert find_launch_exe(tmp_path, "index.html", EXCL) == page


def test_a_page_is_never_guessed(tmp_path):
    touch(tmp_path, "index.html")
    touch(tmp_path, "story/page2.htm")
    assert find_launch_exe(tmp_path, None, EXCL) is None


def test_adopt_catalog_launch_takes_a_page(tmp_path):
    from types import SimpleNamespace
    from engine.launch import adopt_catalog_launch
    touch(tmp_path, "index.html")
    touch(tmp_path, "guess.exe")
    rec = SimpleNamespace(launch_path="guess.exe", launch_from_catalog=False)
    entry = {"launch": {"windows": {"path": "index.html"}}}
    assert adopt_catalog_launch(rec, entry, "windows", tmp_path) is True
    assert rec.launch_path == "index.html" and rec.launch_from_catalog is True


# ---- start_program: an exe flagged to run as administrator (bug 19) ----------------------------------------

import pytest

from engine.launch import LaunchCancelled, start_program


def _os_error(winerror):
    e = OSError(13, "boom")
    e.winerror = winerror
    return e


def test_start_program_runs_the_exe_in_its_folder():
    calls = []
    start_program("C:/Games/X/x.exe", "C:/Games/X", popen=lambda args, cwd: calls.append((args, cwd)),
                  shell_open=lambda *a: pytest.fail("no shell needed"))
    assert calls == [(["C:/Games/X/x.exe"], "C:/Games/X")]


def test_start_program_asks_windows_when_the_exe_needs_elevation():
    opened = []

    def popen(args, cwd):
        raise _os_error(740)

    start_program("C:/Games/X/pipe.exe", "C:/Games/X", popen=popen,
                  shell_open=lambda path, cwd: opened.append((path, cwd)))
    assert opened == [("C:/Games/X/pipe.exe", "C:/Games/X")]


def test_start_program_passes_on_other_errors():
    def popen(args, cwd):
        raise _os_error(2)

    with pytest.raises(OSError):
        start_program("C:/Games/X/x.exe", "C:/Games/X", popen=popen,
                      shell_open=lambda *a: pytest.fail("only 740 goes to the shell"))


def test_start_program_passes_on_a_declined_prompt():
    def popen(args, cwd):
        raise _os_error(740)

    def shell_open(path, cwd):
        raise LaunchCancelled()

    with pytest.raises(LaunchCancelled):
        start_program("C:/Games/X/x.exe", "C:/Games/X", popen=popen, shell_open=shell_open)


@pytest.mark.parametrize("name, kind", [
    ("index.html", "page"), ("Start.Htm", "page"), ("INDEX.HTML", "page"),
    ("game.exe", "program"), ("run.bat", "program"), ("play.cmd", "program"),
    ("game.hta", "program"), ("readme.txt", "program"), ("Game.app", "program"), ("game", "program"),
])
def test_launch_kind(name, kind):
    assert launch_kind(f"C:/Games/Story/{name}") == kind


def _page_in(tmp_path, folder_name):
    folder = tmp_path / folder_name
    folder.mkdir()
    page = folder / "index.html"
    page.write_text("<p>hi</p>", encoding="utf-8")
    return page


def test_page_address_escapes_the_path(tmp_path):
    page = _page_in(tmp_path, "We don't need #1 100% é")
    address = page_address(str(page))
    assert address.startswith("file:///") and not address.startswith("file:////")
    assert "%20" in address and "%23" in address and "%25" in address
    assert "é" in address and "%C3" not in address
    assert " " not in address and "#" not in address


def test_page_address_keeps_japanese_letters(tmp_path):
    page = _page_in(tmp_path, "日本 #1")
    address = page_address(str(page))
    assert "日本" in address and "%E6" not in address
    assert "%20" in address and "%23" in address
    assert " " not in address and "#" not in address


def test_page_address_does_not_follow_a_link(tmp_path):
    target = tmp_path / "x.exe"
    target.write_bytes(b"MZ")
    link = tmp_path / "index.html"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("cannot create link")
    address = page_address(str(link))
    assert address.endswith("/index.html") and "x.exe" not in address


@pytest.mark.windows
@pytest.mark.parametrize("folder_name", ["Café review #1 100%", "日本 #1"])
def test_page_address_round_trips_through_the_windows_shell(tmp_path, folder_name):
    import ctypes
    page = _page_in(tmp_path, folder_name)
    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.c_ulong(len(buffer))
    hr = ctypes.windll.shlwapi.PathCreateFromUrlW(page_address(str(page)), buffer, ctypes.byref(size), 0)
    assert hr == 0
    assert Path(buffer.value) == page


def test_start_game_opens_a_page_in_the_browser(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<p>hi</p>", encoding="utf-8")
    opened, started = [], []
    kind = start_game(str(page), str(tmp_path), open_page=lambda a: opened.append(a) or True,
                      start=lambda p, c: started.append((p, c)))
    assert kind == Started("page", None)
    assert opened == [page_address(str(page))]
    assert started == []


def test_start_game_starts_a_program(tmp_path):
    exe = tmp_path / "game.exe"
    opened, started = [], []
    kind = start_game(str(exe), str(tmp_path), open_page=lambda a: opened.append(a) or True,
                      start=lambda p, c: started.append((p, c)))
    assert kind == Started("program", None)
    assert started == [(str(exe), str(tmp_path))]
    assert opened == []


def test_start_game_says_when_the_browser_will_not_open(tmp_path):
    page = tmp_path / "index.html"
    page.write_text("<p>hi</p>", encoding="utf-8")
    with pytest.raises(OSError, match="web browser"):
        start_game(str(page), str(tmp_path), open_page=lambda a: False)


def test_start_game_passes_on_a_declined_prompt(tmp_path):
    def declined(path, cwd):
        raise LaunchCancelled()
    with pytest.raises(LaunchCancelled):
        start_game(str(tmp_path / "game.exe"), str(tmp_path), start=declined)


def test_an_hta_file_is_never_opened_as_a_page(tmp_path):
    opened, started = [], []
    start_game(str(tmp_path / "game.hta"), str(tmp_path), open_page=lambda a: opened.append(a) or True,
               start=lambda p, c: started.append((p, c)))
    assert opened == [] and len(started) == 1


def test_start_game_passes_on_what_the_program_start_returned(tmp_path):
    result = start_game(str(tmp_path / "game.exe"), str(tmp_path), start=lambda p, c: 4321)
    assert result == Started("program", 4321)
    assert result.kind == "program" and result.pid == 4321
