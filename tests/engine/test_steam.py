import pytest

from engine import steam


def reader(values):
    return lambda hive, key, name, view: values.get((hive, name, view))


def test_addresses_come_from_a_plain_positive_int_only():
    assert steam.address("run", 1234560) == "steam://run/1234560"
    assert [steam.address(a, 7) for a in ("rungameid", "install", "store", "uninstall")] == [
        "steam://rungameid/7", "steam://install/7", "steam://store/7", "steam://uninstall/7"]
    assert steam.store_page(1234560) == "https://store.steampowered.com/app/1234560/"
    assert steam.is_app_id(1234560)
    for bad in (True, False, 0, -1, 1.0, "1234560", None, "1/../x"):
        assert not steam.is_app_id(bad)
        with pytest.raises(ValueError):
            steam.address("run", bad)
        with pytest.raises(ValueError):
            steam.store_page(bad)
    with pytest.raises(ValueError):
        steam.address("open", 1)
    assert (steam.GET_ACTION, steam.GET_DEMO_ACTION, steam.LAUNCH_ACTION) == ("store", "install", "rungameid")


def test_steam_folder_is_the_first_registry_value_that_names_a_real_folder(tmp_path):
    here, gone = str(tmp_path), str(tmp_path / "gone")
    find = lambda values: steam.steam_folder(reader(values), platform="win32")
    assert find({("HKCU", "SteamPath", ""): here}) == tmp_path
    assert find({("HKLM", "InstallPath", "32"): here}) == tmp_path
    assert find({("HKLM", "InstallPath", "64"): here}) == tmp_path
    assert find({("HKCU", "SteamPath", ""): gone, ("HKLM", "InstallPath", "64"): here}) == tmp_path
    assert find({}) is None and find({("HKCU", "SteamPath", ""): gone}) is None
    assert find({("HKCU", "SteamPath", ""): 3}) is None           # a value that is not text


def test_steam_folder_survives_a_registry_error_and_asks_nothing_off_windows():
    def broken(*args):
        raise OSError("registry")
    assert steam.steam_folder(broken, platform="win32") is None
    asked = []
    assert steam.steam_folder(lambda *args: asked.append(args), platform="darwin") is None
    assert asked == []


def test_steam_folder_asks_for_the_right_keys(tmp_path):
    asked = []
    steam.steam_folder(lambda *args: asked.append(args), platform="win32")
    assert asked == [("HKCU", r"Software\Valve\Steam", "SteamPath", ""),
                     ("HKLM", r"SOFTWARE\Valve\Steam", "InstallPath", "32"),
                     ("HKLM", r"SOFTWARE\Valve\Steam", "InstallPath", "64")]


def test_send_hands_over_the_built_address_and_nothing_else():
    sent = []
    steam.send("uninstall", 1234560, open_address=sent.append)
    with pytest.raises(ValueError):
        steam.send("run", "1234560", open_address=sent.append)
    assert sent == ["steam://uninstall/1234560"]
