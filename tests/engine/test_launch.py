import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from engine.launch import (adopt_catalog_launch, find_launch_exe, select_best_executable, Started, launch_kind,
                           page_address, start_game)
from engine.paths import STAGING_DIR_NAME
from exception_manager import ExceptionManager

EXCL = ExceptionManager().should_auto_exclude


def touch(root: Path, rel: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    return p


def link_to(outside: Path, link: Path) -> None:
    """A directory symlink, or a junction where this account may not make one."""
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("cannot create link")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                       check=True, capture_output=True)


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
    link_to(outside, game / "lnk")
    assert find_launch_exe(game, "lnk/Evil.exe", EXCL).name == "Real.exe"


def test_search_does_not_descend_links(tmp_path):
    outside = tmp_path / "outside"
    touch(outside, "Evil.exe")
    game = tmp_path / "game"
    game.mkdir()
    link_to(outside, game / "lnk")
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


@pytest.mark.parametrize("name, decoy", [("index.html", "setup.exe"), ("jumpgame.hta", "game.exe")],
                         ids=["page", "hta"])
def test_a_catalog_named_page_or_hta_is_the_launch_file(tmp_path, name, decoy):
    launch_file = touch(tmp_path, name)
    touch(tmp_path, decoy)  # even the guess's favourite name loses to the catalog's path
    assert find_launch_exe(tmp_path, name, EXCL) == launch_file


def test_a_page_is_never_guessed(tmp_path):
    touch(tmp_path, "index.html")
    touch(tmp_path, "story/page2.htm")
    assert find_launch_exe(tmp_path, None, EXCL) is None


@pytest.mark.parametrize("name", ["index.html", "jumpgame.hta"], ids=["page", "hta"])
def test_adopt_catalog_launch_takes_a_page_or_an_hta(tmp_path, name):
    touch(tmp_path, name)
    touch(tmp_path, "guess.exe")
    rec = SimpleNamespace(launch_path="guess.exe", launch_from_catalog=False)
    entry = {"launch": {"windows": {"path": name}}}
    assert adopt_catalog_launch(rec, entry, "windows", tmp_path) is True
    assert rec.launch_path == name and rec.launch_from_catalog is True


# ---- start_program: an exe flagged to run as administrator (bug 19) ----------------------------------------

from engine.launch import LaunchCancelled, start_program


def _os_error(winerror):
    e = OSError(13, "boom")
    e.winerror = winerror
    return e


class _Process:
    def __init__(self, pid):
        self.pid = pid


def test_start_program_runs_the_exe_in_its_folder_and_returns_the_process_id():
    calls = []
    pid = start_program("C:/Games/X/x.exe", "C:/Games/X",
                        popen=lambda args, cwd: calls.append((args, cwd)) or _Process(4242),
                        shell_open=lambda *a: pytest.fail("no shell needed"))
    assert calls == [(["C:/Games/X/x.exe"], "C:/Games/X")] and pid == 4242


@pytest.mark.parametrize("shell_gives", [5151, None], ids=["a process", "no process"])
def test_start_program_asks_windows_when_the_exe_needs_elevation_and_returns_what_it_started(shell_gives):
    opened = []

    def popen(args, cwd):
        raise _os_error(740)

    pid = start_program("C:/Games/X/pipe.exe", "C:/Games/X", popen=popen,
                        shell_open=lambda path, cwd: opened.append((path, cwd)) or shell_gives)
    assert opened == [("C:/Games/X/pipe.exe", "C:/Games/X")] and pid == shell_gives


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


@pytest.mark.windows
def test_shell_execute_info_has_the_windows_layout():
    import ctypes
    import sys
    if sys.platform != "win32":
        pytest.skip("Windows' structure layout")
    from engine.launch import _shell_execute_info
    size = ctypes.sizeof(_shell_execute_info())
    assert size == (112 if ctypes.sizeof(ctypes.c_void_p) == 8 else 60)
    assert _shell_execute_info() is _shell_execute_info()


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


@pytest.mark.skipif(os.name != "nt", reason="a UNC path is a Windows path")
def test_page_address_of_a_network_share():
    path = "\\\\server\\share\\Café #1\\index.html"
    assert page_address(path) == "file://server/share/Café%20%231/index.html"


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


@pytest.mark.windows
def test_page_address_of_a_network_share_round_trips_through_the_windows_shell():
    import ctypes
    path = "\\\\server\\share\\Café #1\\index.html"
    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.c_ulong(len(buffer))
    hr = ctypes.windll.shlwapi.PathCreateFromUrlW(page_address(path), buffer, ctypes.byref(size), 0)
    assert hr == 0
    assert buffer.value == path


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


def test_start_game_passes_on_what_the_program_start_returned(tmp_path):
    result = start_game(str(tmp_path / "game.exe"), str(tmp_path), start=lambda p, c: 4321)
    assert result == Started("program", 4321)
    assert result.kind == "program" and result.pid == 4321


# ---- .hta games (HTML Applications): run by Windows' own mshta.exe, never opened as a page, never guessed ----

from engine import launch, messages
from engine.launch import hta_host, start_hta

WINDOWS_64 = ("C:\\Windows\\system32", "C:\\Windows\\SysWOW64")  # (system folder, its 32-bit twin)
WINDOWS_32 = ("C:\\Windows\\system32", None)  # 32-bit Windows has no twin


@pytest.mark.parametrize("name", ["game.hta", "Game.HTA"])
def test_an_hta_file_goes_to_the_hta_starter_and_is_a_program(tmp_path, name):
    hta = str(tmp_path / name)
    opened, started, hosted = [], [], []
    result = start_game(hta, str(tmp_path), open_page=lambda a: opened.append(a) or True,
                        start=lambda p, c: started.append((p, c)),
                        hta_start=lambda p, c: hosted.append((p, c)) or 6001, hta_allowed=True)
    assert hosted == [(hta, str(tmp_path))]
    assert opened == [] and started == []  # not the browser or the shell, and not run as if it were an exe
    assert result == Started("program", 6001)  # a program: traced, and the window minimizes


def test_a_program_never_goes_to_the_hta_starter(tmp_path):
    start_game(str(tmp_path / "game.exe"), str(tmp_path), start=lambda p, c: 1,
               hta_start=lambda p, c: pytest.fail("an exe is not an HTML Application"), hta_allowed=True)


@pytest.mark.parametrize("name", ["game.hta", "Game.HTA"])
def test_an_hta_the_catalog_did_not_name_is_started_by_nothing(tmp_path, name):
    # start_game's caller says whether the catalog named the file. Unless it does, nothing starts an .hta: not
    # the host, not the browser, and not start_program, whose administrator-prompt route is a shell open (the
    # .hta file association).
    with pytest.raises(OSError) as e:
        start_game(str(tmp_path / name), str(tmp_path),
                   open_page=lambda a: pytest.fail("an .hta is not a page"),
                   start=lambda p, c: pytest.fail("an .hta is never run directly or through the shell"),
                   hta_start=lambda p, c: pytest.fail("only a catalog-named .hta gets the host"))
    assert str(e.value) == messages.HTA_NOT_ALLOWED


def test_an_hta_that_is_not_allowed_never_reaches_start_program(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "start_program", lambda p, c: pytest.fail("no direct run, no shell open"))
    monkeypatch.setattr(launch, "start_hta", lambda p, c: pytest.fail("no host"))
    with pytest.raises(OSError):
        start_game(str(tmp_path / "game.hta"), str(tmp_path))


def test_start_game_uses_start_hta_when_no_starter_is_given(tmp_path, monkeypatch):
    hosted = []
    monkeypatch.setattr(launch, "start_hta", lambda p, c: hosted.append((p, c)) or 6002)
    monkeypatch.setattr(launch, "start_program", lambda p, c: pytest.fail("an allowed .hta is not run directly"))
    hta = str(tmp_path / "game.hta")
    assert start_game(hta, str(tmp_path), hta_allowed=True) == Started("program", 6002)
    assert hosted == [(hta, str(tmp_path))]


def test_start_game_passes_on_an_hta_that_would_not_start(tmp_path):
    def broken(path, cwd):
        raise OSError("no host")
    with pytest.raises(OSError, match="no host"):
        start_game(str(tmp_path / "game.hta"), str(tmp_path), hta_start=broken, hta_allowed=True)


@pytest.mark.parametrize("bits, dirs, host", [
    (32, WINDOWS_64, "C:\\Windows\\SysWOW64\\mshta.exe"),
    (32, WINDOWS_32, "C:\\Windows\\system32\\mshta.exe"),  # on 32-bit Windows the system folder is the 32-bit one
    (64, WINDOWS_64, "C:\\Windows\\system32\\mshta.exe"),
])
def test_hta_host_is_mshta_in_the_windows_system_folder(bits, dirs, host):
    assert hta_host(bits, system_dirs=lambda: dirs) == host


@pytest.mark.parametrize("bits, host", [(32, "C:\\Windows\\SysWOW64\\mshta.exe"),
                                        (64, "C:\\Windows\\system32\\mshta.exe")])
def test_start_hta_runs_the_chosen_host_with_the_file_as_its_one_argument(monkeypatch, bits, host):
    monkeypatch.setattr(launch, "HTA_HOST_BITS", bits)
    calls = []

    def popen(args, cwd):
        calls.append((args, cwd))
        return _Process(7007)

    pid = start_hta("C:/Games/Jump Game/jumpgame.hta", "C:/Games/Jump Game", popen=popen,
                    system_dirs=lambda: WINDOWS_64, windows=True)
    assert calls == [([host, "C:\\Games\\Jump Game\\jumpgame.hta"], "C:/Games/Jump Game")]
    assert pid == 7007


def test_start_hta_never_takes_the_host_from_the_games_folder_or_the_search_path(tmp_path):
    (tmp_path / "mshta.exe").write_bytes(b"MZ")  # a planted host beside the game
    calls = []
    start_hta(str(tmp_path / "game.hta"), str(tmp_path), popen=lambda args, cwd: calls.append(args) or _Process(1),
              system_dirs=lambda: WINDOWS_64, windows=True)
    assert calls[0][0] == "C:\\Windows\\SysWOW64\\mshta.exe"  # a full path, so nothing is searched for


def test_start_hta_says_when_windows_has_no_host():
    def popen(args, cwd):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    with pytest.raises(OSError) as e:
        start_hta("C:/Games/X/x.hta", "C:/Games/X", popen=popen, system_dirs=lambda: WINDOWS_64, windows=True)
    assert str(e.value) == messages.HTA_HOST_MISSING


def test_start_hta_passes_on_other_errors():
    def popen(args, cwd):
        raise PermissionError(13, "Access is denied")

    with pytest.raises(PermissionError):
        start_hta("C:/Games/X/x.hta", "C:/Games/X", popen=popen, system_dirs=lambda: WINDOWS_64, windows=True)


def test_an_hta_does_not_start_on_another_system():
    with pytest.raises(OSError) as e:
        start_hta("/games/x/x.hta", "/games/x", popen=lambda *a, **k: pytest.fail("nothing to run it with"),
                  system_dirs=lambda: pytest.fail("no Windows folders to ask for"), windows=False)
    assert str(e.value) == messages.HTA_NEEDS_WINDOWS


@pytest.mark.windows
def test_the_real_hosts_are_syswow64_for_32_bits_and_system32_for_64():
    # Nothing injected: the answer Windows itself gives. (A 32768-character buffer once got no text back for the
    # 32-bit folder, and the 64-bit host ran in silence.)
    import sys
    if sys.platform != "win32":
        pytest.skip("Windows' own folders")
    windows = Path(os.environ["SystemRoot"])
    twin = "SysWOW64" if (windows / "SysWOW64").is_dir() else "System32"  # 32-bit Windows has one system folder
    system, wow64 = launch._system_dirs()
    assert os.path.normcase(system) == os.path.normcase(str(windows / "System32"))
    assert (wow64 is None) if twin == "System32" else \
        os.path.normcase(wow64) == os.path.normcase(str(windows / "SysWOW64"))
    assert os.path.normcase(hta_host(32)) == os.path.normcase(str(windows / twin / "mshta.exe"))
    assert os.path.normcase(hta_host(64)) == os.path.normcase(str(windows / "System32" / "mshta.exe"))


def _folder_call(*answers):
    """A stand-in for GetSystemDirectoryW: each call gives the next answer, (the length it returns, the text it
    writes or None). `sizes` lists the buffer size of each call."""
    sizes = []

    def function(buffer, size):
        length, text = answers[len(sizes)]
        sizes.append(size)
        if text is not None:
            assert len(text) < size, "Windows never writes past the buffer"
            buffer.value = text
        return length

    function.sizes = sizes
    return function


@pytest.mark.parametrize("answer, folder", [
    pytest.param((19, "C:\\WINDOWS\\SysWOW64"), "C:\\WINDOWS\\SysWOW64", id="the text Windows wrote"),
    pytest.param((0, None), None, id="Windows has no such folder"),
    pytest.param((20, None), None, id="an answer with no text is no folder"),
])
def test_windows_folder(answer, folder):
    assert launch._windows_folder(_folder_call(answer)) == folder


def test_windows_folder_asks_again_with_a_bigger_buffer_when_told_it_was_too_small():
    deep = "C:\\" + "w" * 400
    call = _folder_call((len(deep) + 1, None), (len(deep), deep))
    assert launch._windows_folder(call) == deep
    assert len(call.sizes) == 2 and call.sizes[0] <= 260 < len(deep) < call.sizes[1]


def test_an_hta_is_never_guessed(tmp_path):
    touch(tmp_path, "game.hta")
    touch(tmp_path, "more/play.hta")
    assert find_launch_exe(tmp_path, None, EXCL) is None
    deeper = touch(tmp_path, "tools/helper.exe")
    assert find_launch_exe(tmp_path, None, EXCL) == deeper  # the guess looks past the .hta beside it
    assert find_launch_exe(tmp_path, "missing.hta", EXCL) == deeper  # a path that names no file is not followed
