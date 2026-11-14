from tracing.split import virtual_store_path

LOCAL = "C:\\Users\\Ann\\AppData\\Local"


def test_registry_maps_to_virtualstore():
    assert (virtual_store_path("HKLM\\SOFTWARE\\Acme\\Game", True, LOCAL)
            == "HKCU\\Software\\Classes\\VirtualStore\\MACHINE\\SOFTWARE\\Acme\\Game")
    assert (virtual_store_path("HKLM\\SOFTWARE\\WOW6432Node\\Acme", True, LOCAL)
            == "HKCU\\Software\\Classes\\VirtualStore\\MACHINE\\SOFTWARE\\WOW6432Node\\Acme")


def test_files_map_to_local_virtualstore():
    assert (virtual_store_path("C:\\Program Files (x86)\\Acme\\save.dat", True, LOCAL)
            == "C:\\Users\\Ann\\AppData\\Local\\VirtualStore\\Program Files (x86)\\Acme\\save.dat")


def test_not_virtualized_or_excluded_is_none():
    assert virtual_store_path("HKLM\\SOFTWARE\\Acme", False, LOCAL) is None
    assert virtual_store_path("C:\\Program Files\\Acme\\x", False, LOCAL) is None
    assert virtual_store_path("HKLM\\SOFTWARE\\Classes\\x", True, LOCAL) is None
    assert virtual_store_path("HKLM\\SOFTWARE\\Microsoft\\Windows\\x", True, LOCAL) is None
    assert virtual_store_path("HKLM\\SOFTWARE\\Microsoft\\Windows NT\\x", True, LOCAL) is None
    assert virtual_store_path(LOCAL + "\\VirtualStore\\Program Files\\Acme\\x", True, LOCAL) is None
    assert virtual_store_path("D:\\Games\\x", True, LOCAL) is None
