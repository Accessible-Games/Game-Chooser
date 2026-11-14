"""scripts/collect_licenses.py: the license texts it ships."""

import importlib.util
from importlib.metadata import PathDistribution
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("collect_licenses", ROOT / "scripts" / "collect_licenses.py")
collect_licenses = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collect_licenses)


def test_a_license_that_is_not_utf8_ships_byte_for_byte(tmp_path, monkeypatch):
    info = tmp_path / "pkg-1.dist-info"
    (info / "licenses").mkdir(parents=True)
    text = b"Copyright \x93Someone\x94\r\n"  # cp1252 quotes, as in wxPython 4.3.2's LICENSE.txt
    (info / "licenses" / "LICENSE.txt").write_bytes(text)
    (info / "METADATA").write_text("Metadata-Version: 2.4\nName: pkg\nVersion: 1\nLicense-File: LICENSE.txt\n")
    (info / "RECORD").write_text("pkg-1.dist-info/METADATA,,\npkg-1.dist-info/licenses/LICENSE.txt,,\n")
    monkeypatch.setattr(collect_licenses, "distribution", lambda name: PathDistribution(info))
    assert collect_licenses.package_texts("pkg") == [("pkg-LICENSE.txt", text)]


def test_python_license_is_beside_python_on_windows_and_with_the_standard_library_elsewhere():
    base = Path("/py")
    version = f"python{collect_licenses.sys.version_info.major}.{collect_licenses.sys.version_info.minor}"
    assert collect_licenses.python_license(base, "win32") == base / "LICENSE.txt"
    assert collect_licenses.python_license(base, "darwin") == base / "lib" / version / "LICENSE.txt"
