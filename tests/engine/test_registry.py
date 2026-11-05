import pytest

from engine.errors import ElevationDeclined, StepError
from engine.registry import RegistryAccess
from tests.engine.fakes import FakeElevator, FakeWinreg


@pytest.fixture
def fake_winreg():
    return FakeWinreg()


@pytest.fixture
def fake_elev():
    return FakeElevator()


def test_hklm_write_goes_through_reg_exe(fake_elev, fake_winreg):
    reg = RegistryAccess(elevator=fake_elev, hkcu=fake_winreg)
    reg.set("HKLM", r"Software\X", "Path", "REG_SZ", "C:\\G")
    assert fake_elev.calls == [("reg.exe", r'add "HKLM\Software\X" /v "Path" /t REG_SZ /d "C:\G" /f /reg:32', True)]
    assert fake_elev.shown == [False]  # hidden: its console window would take the focus from Game Chooser
    assert fake_winreg.keys == {}


def test_hklm_dword_is_passed_as_decimal(fake_elev, fake_winreg):
    reg = RegistryAccess(elevator=fake_elev, hkcu=fake_winreg)
    reg.set("HKLM", r"Software\X", "Count", "REG_DWORD", "16")
    reg.set("HKLM", r"Software\X", "Flag", "REG_DWORD", 1)
    assert [c[1] for c in fake_elev.calls] == [
        r'add "HKLM\Software\X" /v "Count" /t REG_DWORD /d "16" /f /reg:32',
        r'add "HKLM\Software\X" /v "Flag" /t REG_DWORD /d "1" /f /reg:32',
    ]


def test_hklm_trailing_backslash_is_doubled_so_the_quote_survives(fake_elev, fake_winreg):
    reg = RegistryAccess(elevator=fake_elev, hkcu=fake_winreg)
    reg.set("HKLM", r"Software\X", "Dir", "REG_SZ", "C:\\Games\\")
    assert fake_elev.calls[0][1] == r'add "HKLM\Software\X" /v "Dir" /t REG_SZ /d "C:\Games\\" /f /reg:32'


def test_hklm_default_value_uses_ve(fake_elev, fake_winreg):
    reg = RegistryAccess(elevator=fake_elev, hkcu=fake_winreg)
    reg.set("HKLM", r"Software\X", "", "REG_SZ", "v")
    assert fake_elev.calls[0][1] == r'add "HKLM\Software\X" /ve /t REG_SZ /d "v" /f /reg:32'


@pytest.mark.parametrize("key,name,value", [
    ('Software\\X"', "Path", "v"),
    ("Software\\X", 'Pa"th', "v"),
    ("Software\\X", "Path", 'C:\\G" /f'),
    ("Software\\X", "Path", "line1\nline2"),
    ("Software\\X\r", "Path", "v"),
])
def test_hklm_refuses_quotes_and_newlines(fake_elev, fake_winreg, key, name, value):
    reg = RegistryAccess(elevator=fake_elev, hkcu=fake_winreg)
    with pytest.raises(StepError):
        reg.set("HKLM", key, name, "REG_SZ", value)
    assert fake_elev.calls == []


def test_hklm_nonzero_exit_is_step_error(fake_winreg):
    elev = FakeElevator(exit_codes={"reg.exe": 1})
    reg = RegistryAccess(elevator=elev, hkcu=fake_winreg)
    with pytest.raises(StepError):
        reg.set("HKLM", r"Software\X", "Path", "REG_SZ", "v")


def test_hklm_declined_elevation_propagates_raw(fake_winreg):
    reg = RegistryAccess(elevator=FakeElevator(decline=True), hkcu=fake_winreg)
    with pytest.raises(ElevationDeclined):
        reg.set("HKLM", r"Software\X", "Path", "REG_SZ", "v")


def test_hklm_read_uses_32bit_view(fake_elev, fake_winreg):
    fake_winreg.put(fake_winreg.HKEY_LOCAL_MACHINE, r"Software\X", "Path", "C:\\G", fake_winreg.REG_SZ)
    reg = RegistryAccess(elevator=fake_elev, hkcu=fake_winreg)
    assert reg.get("HKLM", r"Software\X", "Path") == (True, "C:\\G", "REG_SZ")
    hive, key, access = fake_winreg.opens[-1]
    assert hive == fake_winreg.HKEY_LOCAL_MACHINE
    assert access & fake_winreg.KEY_WOW64_32KEY
    assert reg.get("HKLM", r"Software\X", "Missing") == (False, None, None)
    assert reg.get("HKLM", r"Software\Nope", "Path") == (False, None, None)
    assert fake_elev.calls == []


def test_hklm_delete_goes_through_reg_exe_and_skips_missing(fake_elev, fake_winreg):
    fake_winreg.put(fake_winreg.HKEY_LOCAL_MACHINE, r"Software\X", "Path", "C:\\G", fake_winreg.REG_SZ)
    reg = RegistryAccess(elevator=fake_elev, hkcu=fake_winreg)
    reg.delete("HKLM", r"Software\X", "Path")
    assert fake_elev.calls == [("reg.exe", r'delete "HKLM\Software\X" /v "Path" /f /reg:32', True)]
    reg.delete("HKLM", r"Software\X", "Other")
    assert len(fake_elev.calls) == 1


def test_hkcu_goes_through_winreg_directly(fake_elev, fake_winreg):
    reg = RegistryAccess(elevator=fake_elev, hkcu=fake_winreg)
    assert reg.get("HKCU", r"Software\X", "Path") == (False, None, None)
    reg.set("HKCU", r"Software\X", "Path", "REG_SZ", "C:\\G")
    reg.set("HKCU", r"Software\X", "Count", "REG_DWORD", "7")
    assert reg.get("HKCU", r"Software\X", "Path") == (True, "C:\\G", "REG_SZ")
    assert reg.get("HKCU", r"Software\X", "Count") == (True, 7, "REG_DWORD")
    reg.delete("HKCU", r"Software\X", "Path")
    reg.delete("HKCU", r"Software\X", "Path")  # already gone: no error
    reg.delete("HKCU", r"Software\Nope", "Path")
    assert reg.get("HKCU", r"Software\X", "Path") == (False, None, None)
    assert fake_elev.calls == []


def test_get_reports_other_value_types_by_name(fake_elev, fake_winreg):
    fake_winreg.put(fake_winreg.HKEY_CURRENT_USER, r"Software\X", "Blob", b"\x01", fake_winreg.REG_BINARY)
    reg = RegistryAccess(elevator=fake_elev, hkcu=fake_winreg)
    assert reg.get("HKCU", r"Software\X", "Blob") == (True, b"\x01", "REG_BINARY")


@pytest.mark.parametrize("hive,type_,value", [
    ("HKCR", "REG_SZ", "v"),
    ("HKCU", "REG_BINARY", "v"),
    ("HKCU", "REG_DWORD", "abc"),
    ("HKCU", "REG_DWORD", -1),
    ("HKCU", "REG_DWORD", 2 ** 32),
])
def test_bad_hive_type_or_dword_is_step_error(fake_elev, fake_winreg, hive, type_, value):
    reg = RegistryAccess(elevator=fake_elev, hkcu=fake_winreg)
    with pytest.raises(StepError):
        reg.set(hive, r"Software\X", "Path", type_, value)


def test_can_restore(fake_elev, fake_winreg):
    reg = RegistryAccess(elevator=fake_elev, hkcu=fake_winreg)
    assert reg.can_restore("HKLM", "REG_SZ", r"C:\G")
    assert reg.can_restore("HKLM", "REG_DWORD", 5)
    assert not reg.can_restore("HKLM", "REG_SZ", r'"C:\G\g.exe" %1')
    assert not reg.can_restore("HKLM", "REG_EXPAND_SZ", "a\nb")
    assert not reg.can_restore("HKLM", "REG_BINARY", b"\x01")
    assert reg.can_restore("HKCU", "REG_SZ", r'"C:\G\g.exe" %1')
    assert not reg.can_restore("HKCU", "REG_MULTI_SZ", ["a", "b"])
    assert not reg.can_restore("HKCR", "REG_SZ", "v")
