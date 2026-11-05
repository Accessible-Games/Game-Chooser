import os
import subprocess
import sys
import uuid

import pytest

from engine import messages
from engine.errors import ElevationDeclined, StepError, UnsafePath
from engine.registry import RegistryAccess
from engine.steps import (FILE_ACTIONS, SYSTEM_ACTIONS, StepContext, run_file_steps, run_system_steps,
                          undo_steps)
from tests.engine.fakes import FakeElevator, FakeWinreg

HKCU = FakeWinreg.HKEY_CURRENT_USER
HKLM = FakeWinreg.HKEY_LOCAL_MACHINE


@pytest.fixture
def fake_elev():
    return FakeElevator()


@pytest.fixture
def fake_winreg():
    return FakeWinreg()


@pytest.fixture
def ctx(tmp_path, fake_elev, fake_winreg):
    return StepContext(dir=tmp_path, elevator=fake_elev, registry=RegistryAccess(fake_elev, hkcu=fake_winreg))


def run_any(step, dir, ctx=None):
    if step["action"] in FILE_ACTIONS:
        run_file_steps([step], dir)
    else:
        elev = FakeElevator()
        run_system_steps([step], ctx or StepContext(dir, elev, RegistryAccess(elev, hkcu=FakeWinreg())))


def hkcu_step(name, value, type_="REG_SZ", key=r"Software\Game"):
    return {"action": "registry", "hive": "HKCU", "key": key, "name": name, "type": type_, "value": value}


def test_action_sets():
    assert FILE_ACTIONS == {"copy", "move", "delete"}
    assert SYSTEM_ACTIONS == {"registry", "register", "run"}


def test_file_steps_copy_move_delete(tmp_path):
    (tmp_path / "a.txt").write_text("A")
    (tmp_path / "old.txt").write_text("O")
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "x").write_text("x")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "f").write_text("f")
    run_file_steps([
        {"action": "copy", "from": "a.txt", "to": "sub/b.txt"},
        {"action": "move", "from": "old.txt", "to": "new\\old.txt"},
        {"action": "copy", "from": "data", "to": "data2"},
        {"action": "delete", "path": "junk"},
        {"action": "delete", "path": "not-there.txt"},
    ], tmp_path)
    assert (tmp_path / "a.txt").read_text() == "A"
    assert (tmp_path / "sub" / "b.txt").read_text() == "A"
    assert not (tmp_path / "old.txt").exists()
    assert (tmp_path / "new" / "old.txt").read_text() == "O"
    assert (tmp_path / "data2" / "f").read_text() == "f"
    assert not (tmp_path / "junk").exists()


def test_file_steps_replace_an_existing_target(tmp_path):
    (tmp_path / "a").write_text("new")
    (tmp_path / "b").write_text("old")
    (tmp_path / "c").write_text("new2")
    run_file_steps([{"action": "copy", "from": "a", "to": "b"}], tmp_path)
    assert (tmp_path / "b").read_text() == "new"
    run_file_steps([{"action": "move", "from": "c", "to": "b"}], tmp_path)
    assert (tmp_path / "b").read_text() == "new2"
    assert not (tmp_path / "c").exists()


def test_file_step_missing_source_is_step_error(tmp_path):
    with pytest.raises(StepError):
        run_file_steps([{"action": "copy", "from": "nope", "to": "b"}], tmp_path)
    with pytest.raises(StepError):
        run_file_steps([{"action": "move", "from": "nope", "to": "b"}], tmp_path)


def test_every_action_rejects_escaping_paths(tmp_path):
    for s in [{"action": "copy", "from": "..\\a", "to": "b"}, {"action": "delete", "path": "C:\\Windows\\x"},
              {"action": "register", "path": "\\\\srv\\x.dll"}, {"action": "run", "path": "../x.exe"},
              {"action": "copy", "from": "a", "to": "/etc/x"}, {"action": "move", "from": "a", "to": "..\\..\\b"},
              {"action": "delete", "path": "."}, {"action": "delete", "path": ""},
              {"action": "run", "path": "C:\\Windows\\notepad.exe"}]:
        (tmp_path / "a").write_text("a")
        with pytest.raises((StepError, UnsafePath)):
            run_any(s, tmp_path)
    assert tmp_path.exists()


@pytest.mark.windows
def test_steps_refuse_paths_through_a_junction(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.exe").write_bytes(b"x")
    (outside / "keep.txt").write_text("keep")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(game / "link"), str(outside)], check=True,
                   capture_output=True)
    elev = FakeElevator()
    ctx = StepContext(game, elev, RegistryAccess(elev, hkcu=FakeWinreg()))
    with pytest.raises(StepError):
        run_system_steps([{"action": "run", "path": "link/x.exe"}], ctx)
    with pytest.raises(StepError):
        run_system_steps([{"action": "register", "path": "link/x.exe"}], ctx)
    with pytest.raises(StepError):
        run_file_steps([{"action": "delete", "path": "link/keep.txt"}], game)
    assert elev.calls == []
    assert (outside / "keep.txt").exists()
    os.rmdir(game / "link")


def test_unknown_action_is_hard_error_and_undoes_earlier(tmp_path, ctx, fake_winreg):
    with pytest.raises(StepError) as exc:
        run_system_steps([hkcu_step("Path", "C:\\G"), {"action": "shell", "path": "x"}], ctx)
    assert str(exc.value) == messages.STEP_UNKNOWN_ACTION
    assert fake_winreg.value(HKCU, r"Software\Game", "Path") is None


def test_unknown_action_in_file_steps_is_hard_error(tmp_path):
    (tmp_path / "a").write_text("a")
    with pytest.raises(StepError):
        run_file_steps([{"action": "copy", "from": "a", "to": "b"}, {"action": "chmod", "path": "a"}], tmp_path)
    with pytest.raises(StepError):
        run_file_steps([{"path": "a"}], tmp_path)


def test_failed_system_step_undoes_predecessors_in_reverse(tmp_path, fake_winreg):
    fake_winreg.put(HKCU, r"Software\Game", "Path", "before", fake_winreg.REG_SZ)
    (tmp_path / "x.dll").write_bytes(b"x")
    (tmp_path / "setup.exe").write_bytes(b"x")
    elev = FakeElevator(exit_codes={str(tmp_path / "setup.exe"): 2})
    ctx = StepContext(tmp_path, elev, RegistryAccess(elev, hkcu=fake_winreg))
    with pytest.raises(StepError):
        run_system_steps([hkcu_step("Path", "first"), hkcu_step("Path", "second"),
                          {"action": "register", "path": "x.dll"}, {"action": "run", "path": "setup.exe"}], ctx)
    assert fake_winreg.value(HKCU, r"Software\Game", "Path") == ("before", fake_winreg.REG_SZ)
    assert elev.calls[-1] == ("regsvr32.exe", f'/u /s "{tmp_path / "x.dll"}"', True)


def test_file_step_order_is_kept(tmp_path):
    (tmp_path / "a").write_text("A")
    run_file_steps([{"action": "move", "from": "a", "to": "b"}, {"action": "copy", "from": "b", "to": "c"}], tmp_path)
    assert not (tmp_path / "a").exists()
    assert (tmp_path / "b").read_text() == "A"
    assert (tmp_path / "c").read_text() == "A"


def test_system_steps_skip_file_actions_and_vice_versa(tmp_path, ctx, fake_winreg, fake_elev):
    (tmp_path / "a").write_text("A")
    run_file_steps([hkcu_step("Path", "v"), {"action": "register", "path": "a"}, {"action": "run", "path": "a"}],
                   tmp_path)
    assert fake_winreg.keys == {}
    assert fake_elev.calls == []
    applied = run_system_steps([{"action": "copy", "from": "a", "to": "b"}, {"action": "delete", "path": "a"}], ctx)
    assert applied == []
    assert (tmp_path / "a").exists() and not (tmp_path / "b").exists()


def test_registry_step_returns_undo_data(ctx, fake_winreg):
    fake_winreg.put(HKCU, r"Software\Game", "Old", 5, fake_winreg.REG_DWORD)
    applied = run_system_steps([hkcu_step("Old", "x"), hkcu_step("New", 1, "REG_DWORD")], ctx)
    assert applied == [
        {**hkcu_step("Old", "x"), "undo": {"existed": True, "previous": 5, "previous_type": "REG_DWORD"}},
        {**hkcu_step("New", 1, "REG_DWORD"), "undo": {"existed": False, "previous": None, "previous_type": None}},
    ]
    assert fake_winreg.value(HKCU, r"Software\Game", "New") == (1, fake_winreg.REG_DWORD)
    assert undo_steps(applied, ctx) == []
    assert fake_winreg.value(HKCU, r"Software\Game", "Old") == (5, fake_winreg.REG_DWORD)
    assert fake_winreg.value(HKCU, r"Software\Game", "New") is None


def test_registry_step_refuses_to_replace_a_value_it_cannot_restore(ctx, fake_winreg):
    fake_winreg.put(HKCU, r"Software\Game", "Blob", b"\x01", fake_winreg.REG_BINARY)
    with pytest.raises(StepError):
        run_system_steps([hkcu_step("Blob", "x")], ctx)
    assert fake_winreg.value(HKCU, r"Software\Game", "Blob") == (b"\x01", fake_winreg.REG_BINARY)


def test_register_is_elevated_regsvr32(tmp_path, ctx, fake_elev):
    (tmp_path / "dx8vb.dll").write_bytes(b"x")
    applied = run_system_steps([{"action": "register", "path": "dx8vb.dll"}], ctx)
    assert fake_elev.calls == [("regsvr32.exe", f'/s "{tmp_path / "dx8vb.dll"}"', True)]
    assert undo_steps(applied, ctx) == []
    assert fake_elev.calls[-1] == ("regsvr32.exe", f'/u /s "{tmp_path / "dx8vb.dll"}"', True)
    assert fake_elev.shown == [False, False]  # no console window flashing up and taking the focus (I2)


def test_register_missing_file_or_failure_is_step_error(tmp_path):
    elev = FakeElevator(exit_codes={"regsvr32.exe": 5})
    ctx = StepContext(tmp_path, elev, RegistryAccess(elev, hkcu=FakeWinreg()))
    with pytest.raises(StepError):
        run_system_steps([{"action": "register", "path": "missing.dll"}], ctx)
    assert elev.calls == []
    (tmp_path / "x.dll").write_bytes(b"x")
    with pytest.raises(StepError):
        run_system_steps([{"action": "register", "path": "x.dll"}], ctx)


def test_run_step_substitutes_dir_and_fails_on_nonzero(tmp_path):
    (tmp_path / "bin").mkdir()
    exe = tmp_path / "bin" / "setup.exe"
    exe.write_bytes(b"x")
    elev = FakeElevator()
    ctx = StepContext(tmp_path, elev, RegistryAccess(elev, hkcu=FakeWinreg()))
    applied = run_system_steps([{"action": "run", "path": "bin\\setup.exe", "args": '/cfg "{dir}\\cfg"'}], ctx)
    assert elev.calls == [(str(exe), f'/cfg "{tmp_path}\\cfg"', False)]
    assert applied == []
    elev.exit_codes[str(exe)] = 3
    with pytest.raises(StepError) as exc:
        run_system_steps([{"action": "run", "path": "bin/setup.exe"}], ctx)
    assert "3" in str(exc.value)
    assert elev.calls[-1] == (str(exe), "", False)


def test_run_step_missing_file_is_step_error(tmp_path, ctx, fake_elev):
    with pytest.raises(StepError):
        run_system_steps([{"action": "run", "path": "setup.exe"}], ctx)
    assert fake_elev.calls == []


def test_elevation_declined_message(tmp_path, fake_winreg):
    (tmp_path / "dx8vb.dll").write_bytes(b"x")
    elev = FakeElevator(decline=True)
    ctx = StepContext(tmp_path, elev, RegistryAccess(elev, hkcu=fake_winreg))
    with pytest.raises(StepError) as exc:
        run_system_steps([{"action": "register", "path": "dx8vb.dll"}], ctx)
    assert "Administrator permission was declined" in str(exc.value)
    assert "dx8vb.dll" in str(exc.value)
    assert isinstance(exc.value.__cause__, ElevationDeclined)
    hklm = {"action": "registry", "hive": "HKLM", "key": r"Software\X", "name": "P", "type": "REG_SZ", "value": "v"}
    with pytest.raises(StepError) as exc:
        run_system_steps([hklm], ctx)
    assert "Administrator permission was declined" in str(exc.value)
    assert isinstance(exc.value.__cause__, ElevationDeclined)


def test_hklm_registry_step_and_undo_go_through_reg_exe(tmp_path, ctx, fake_elev):
    hklm = {"action": "registry", "hive": "HKLM", "key": r"Software\X", "name": "P", "type": "REG_SZ", "value": "v"}
    applied = run_system_steps([hklm], ctx)
    assert fake_elev.calls == [("reg.exe", r'add "HKLM\Software\X" /v "P" /t REG_SZ /d "v" /f /reg:32', True)]
    assert fake_elev.shown == [False]  # reg.exe gets no console window (I2)
    # The fake reg.exe doesn't really write, so the value reads as absent and undo has nothing to delete.
    assert undo_steps(applied, ctx) == []


def test_undo_never_raises_and_reports_each_failure(tmp_path, fake_winreg):
    (tmp_path / "a.dll").write_bytes(b"x")
    (tmp_path / "b.dll").write_bytes(b"x")
    elev = FakeElevator()
    ctx = StepContext(tmp_path, elev, RegistryAccess(elev, hkcu=fake_winreg))
    applied = run_system_steps([{"action": "register", "path": "a.dll"}, hkcu_step("Path", "v"),
                                {"action": "register", "path": "b.dll"}], ctx)
    elev.exit_codes["regsvr32.exe"] = 4
    failures = undo_steps(applied, ctx)
    assert len(failures) == 2
    assert all(isinstance(f, str) and f for f in failures)
    assert "b.dll" in failures[0] and "a.dll" in failures[1]
    assert fake_winreg.value(HKCU, r"Software\Game", "Path") is None
    assert [c[1] for c in elev.calls[-2:]] == [f'/u /s "{tmp_path / "b.dll"}"', f'/u /s "{tmp_path / "a.dll"}"']


def test_undo_reports_declined_elevation_and_continues(tmp_path, fake_winreg):
    (tmp_path / "a.dll").write_bytes(b"x")
    elev = FakeElevator()
    ctx = StepContext(tmp_path, elev, RegistryAccess(elev, hkcu=fake_winreg))
    applied = run_system_steps([hkcu_step("Path", "v"), {"action": "register", "path": "a.dll"}], ctx)
    elev.decline = True
    failures = undo_steps(applied, ctx)
    assert len(failures) == 1 and "a.dll" in failures[0]
    assert fake_winreg.value(HKCU, r"Software\Game", "Path") is None


@pytest.fixture
def real_hkcu_key():
    import winreg
    key = rf"Software\GameChooserTests\{uuid.uuid4()}"
    try:
        yield key
    finally:
        for path in (key, r"Software\GameChooserTests"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except OSError:
                pass  # missing, or the parent still holds another run's key


@pytest.mark.windows
def test_hkcu_set_and_undo_restores_previous(tmp_path, real_hkcu_key):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, real_hkcu_key, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Existing", 0, winreg.REG_SZ, "before")
    elev = FakeElevator()
    ctx = StepContext(tmp_path, elev, RegistryAccess(elev, hkcu=winreg))
    applied = run_system_steps([hkcu_step("Existing", "after", key=real_hkcu_key),
                                hkcu_step("Fresh", 42, "REG_DWORD", key=real_hkcu_key)], ctx)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, real_hkcu_key) as k:
        assert winreg.QueryValueEx(k, "Existing") == ("after", winreg.REG_SZ)
        assert winreg.QueryValueEx(k, "Fresh") == (42, winreg.REG_DWORD)
    assert undo_steps(applied, ctx) == []
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, real_hkcu_key) as k:
        assert winreg.QueryValueEx(k, "Existing") == ("before", winreg.REG_SZ)
        with pytest.raises(FileNotFoundError):
            winreg.QueryValueEx(k, "Fresh")
    assert elev.calls == []


@pytest.mark.parametrize("src,dst", [("a", "a"), ("A", "a"), ("data", "data/sub"), ("data/f", "data")])
def test_file_steps_refuse_overlapping_source_and_target(tmp_path, src, dst):
    (tmp_path / "a").write_text("A")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "f").write_text("f")
    for action in ("copy", "move"):
        with pytest.raises(StepError):
            run_file_steps([{"action": action, "from": src, "to": dst}], tmp_path)
    assert (tmp_path / "a").read_text() == "A"
    assert (tmp_path / "data" / "f").read_text() == "f"


@pytest.mark.windows
def test_windows_elevator_runs_unelevated_and_reports_exit_code(tmp_path):
    from engine.elevation import WindowsElevator
    elev = WindowsElevator()
    assert elev.run(sys.executable, '-c "import sys; sys.exit(3)"', False, cwd=tmp_path) == 3
    process = elev.start(sys.executable, '-c "import time; time.sleep(30)"', False)
    try:
        assert process.pid and process.handle
        assert process.wait(0.05) is None
    finally:
        subprocess.run(["taskkill", "/F", "/PID", str(process.pid)], capture_output=True)
        process.wait(10)
        process.close()


@pytest.mark.windows
def test_windows_elevator_elevated_path_without_uac(monkeypatch, tmp_path):
    """ShellExecuteEx is replaced, so no UAC prompt appears and nothing runs elevated."""
    import pywintypes
    import win32api
    import win32con
    from win32com.shell import shell

    from engine.elevation import WindowsElevator
    seen = []

    def declined(**kwargs):
        seen.append(kwargs)
        raise pywintypes.error(1223, "ShellExecuteEx", "The operation was canceled by the user.")

    monkeypatch.setattr(shell, "ShellExecuteEx", declined)
    with pytest.raises(ElevationDeclined):
        WindowsElevator().run("regsvr32.exe", '/s "x.dll"', True, cwd=tmp_path)
    assert seen[0]["lpVerb"] == "runas" and seen[0]["lpFile"] == "regsvr32.exe"
    assert seen[0]["lpParameters"] == '/s "x.dll"' and seen[0]["fMask"] == 0x40 | 0x100
    assert seen[0]["nShow"] == win32con.SW_SHOWNORMAL  # an installer the player may need to see
    with pytest.raises(ElevationDeclined):
        WindowsElevator().run("regsvr32.exe", '/s "x.dll"', True, show=False)
    assert seen[1]["nShow"] == win32con.SW_HIDE  # a helper program whose console would steal the focus

    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(5)"])
    access = win32con.SYNCHRONIZE | 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
    monkeypatch.setattr(shell, "ShellExecuteEx",
                        lambda **kw: {"hProcess": win32api.OpenProcess(access, False, child.pid), "hInstApp": 42})
    assert WindowsElevator().run("x.exe", "", True) == 5
    child.wait()

    def failed(**kwargs):
        raise pywintypes.error(2, "ShellExecuteEx", "The system cannot find the file specified.")

    monkeypatch.setattr(shell, "ShellExecuteEx", failed)
    with pytest.raises(StepError) as exc:
        WindowsElevator().run("missing.exe", "", True)
    assert not isinstance(exc.value, ElevationDeclined)


@pytest.mark.parametrize("value,type_", [(r'"C:\Program Files\X\x.exe" %1', "REG_SZ"),
                                          ("line1\nline2", "REG_EXPAND_SZ")])
def test_hklm_step_refuses_to_replace_a_value_reg_exe_cannot_write_back(ctx, fake_winreg, fake_elev, value, type_):
    fake_winreg.put(HKLM, r"Software\Classes\X\shell\open\command", "", value, getattr(fake_winreg, type_))
    step = {"action": "registry", "hive": "HKLM", "key": r"Software\Classes\X\shell\open\command", "name": "",
            "type": "REG_SZ", "value": "new"}
    with pytest.raises(StepError) as exc:
        run_system_steps([step], ctx)
    assert str(exc.value) == messages.REGISTRY_CANT_RESTORE.format(key=r"HKLM\Software\Classes\X\shell\open\command")
    assert fake_elev.calls == []


def test_hkcu_step_may_replace_a_value_with_quotes(ctx, fake_winreg):
    fake_winreg.put(HKCU, r"Software\Game", "Cmd", r'"C:\G\g.exe" %1', fake_winreg.REG_SZ)
    applied = run_system_steps([hkcu_step("Cmd", "new")], ctx)
    assert undo_steps(applied, ctx) == []
    assert fake_winreg.value(HKCU, r"Software\Game", "Cmd") == (r'"C:\G\g.exe" %1', fake_winreg.REG_SZ)


@pytest.mark.windows
@pytest.mark.parametrize("with_cwd", [False, True])
def test_windows_elevator_real_shell_execute_path(tmp_path, with_cwd):
    """The real ShellExecuteEx call, with the "open" verb so no UAC prompt appears."""
    from engine.elevation import WindowsElevator
    elev = WindowsElevator(elevated_verb="open")
    code = elev.run(sys.executable, '-c "import sys; sys.exit(4)"', True, cwd=tmp_path if with_cwd else None)
    assert code == 4
