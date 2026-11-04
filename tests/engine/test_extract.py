import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from engine import messages
from engine.errors import Cancelled, ExtractError, UnsafePath
from engine.extract import (Extractor, content_root, flatten_inno, innoextract_command, msi_admin_command,
                            run_process, seven_zip_command)
from tests.conftest import REPO_ROOT

TOOLS_DIR = REPO_ROOT / "bin"


def test_seven_zip_command():
    assert seven_zip_command(Path("bin/7z.exe"), Path("a.7z"), Path("out"), "pw") == [
        "bin\\7z.exe", "x", "-y", "-bso0", "-bsp0", "-ppw", "-oout", "a.7z"]
    assert "-p" not in " ".join(seven_zip_command(Path("bin/7z.exe"), Path("a.zip"), Path("out"), None))


def test_innoextract_and_msi_commands():
    assert innoextract_command(Path("bin/innoextract.exe"), Path("s.exe"), Path("o")) == [
        "bin\\innoextract.exe", "--silent", "--extract", "--output-dir", "o", "s.exe"]
    assert msi_admin_command(Path("g.msi"), Path("o")) == ["msiexec", "/a", "g.msi", "/qn", "TARGETDIR=o"]


def test_flatten_inno(tmp_path):
    (tmp_path / "app" / "data").mkdir(parents=True)
    (tmp_path / "app" / "Game.exe").write_bytes(b"x")
    (tmp_path / "app" / "data" / "x").write_bytes(b"y")
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "junk").write_bytes(b"j")
    flatten_inno(tmp_path)
    assert (tmp_path / "Game.exe").read_bytes() == b"x"
    assert (tmp_path / "data" / "x").read_bytes() == b"y"
    assert not (tmp_path / "app").exists()
    assert not (tmp_path / "tmp").exists()


def test_flatten_inno_with_a_folder_named_app_inside_app(tmp_path):
    (tmp_path / "app" / "app").mkdir(parents=True)
    (tmp_path / "app" / "app" / "inner.txt").write_bytes(b"i")
    flatten_inno(tmp_path)
    assert (tmp_path / "app" / "inner.txt").read_bytes() == b"i"


def test_flatten_inno_without_app_folder_is_a_no_op(tmp_path):
    (tmp_path / "Game.exe").write_bytes(b"x")
    (tmp_path / "tmp").mkdir()
    flatten_inno(tmp_path)
    assert (tmp_path / "Game.exe").exists()
    assert (tmp_path / "tmp").exists()


def test_content_root(tmp_path):
    (tmp_path / "Sub").mkdir()
    assert content_root(tmp_path, "Sub") == tmp_path / "Sub"
    assert content_root(tmp_path, None) == tmp_path
    with pytest.raises(ExtractError):
        content_root(tmp_path, "Missing")
    with pytest.raises(UnsafePath):
        content_root(tmp_path, "../x")


def test_single_top_folder_is_not_auto_descended(tmp_path):
    (tmp_path / "Only").mkdir()
    (tmp_path / "Only" / "Game.exe").write_bytes(b"x")
    assert content_root(tmp_path, None) == tmp_path


class FakeRun:
    def __init__(self, code=0, side_effect=None):
        self.calls = []
        self.code = code
        self.side_effect = side_effect

    def __call__(self, argv, cancel):
        self.calls.append(argv)
        if self.side_effect:
            self.side_effect(argv)
        return self.code


def _extractor(run):
    return Extractor(Path("tools"), run=run)


@pytest.mark.parametrize("install", [
    {"method": "zip"},
    {"method": "7z", "password": "pw"},
    {"method": "cab"},
    {"method": "installer", "installer": {"type": "nsis", "mode": "extract"}},
    {"method": "installer", "installer": {"type": "other", "mode": "extract"}},
])
def test_dispatch_uses_seven_zip(tmp_path, install):
    run = FakeRun()
    dest = tmp_path / "o"
    assert _extractor(run).extract(tmp_path / "a.bin", dest, install, lambda: False) == dest
    assert len(run.calls) == 1
    assert Path(run.calls[0][0]).name == "7z.exe"
    assert run.calls[0][-1] == str(tmp_path / "a.bin")
    assert ("-ppw" in run.calls[0]) == (install.get("password") == "pw")


def test_dispatch_inno_extracts_then_flattens(tmp_path):
    dest = tmp_path / "o"

    def make_inno_layout(argv):
        (dest / "app").mkdir(parents=True, exist_ok=True)
        (dest / "app" / "Game.exe").write_bytes(b"x")
        (dest / "tmp").mkdir(exist_ok=True)

    run = FakeRun(side_effect=make_inno_layout)
    install = {"method": "installer", "installer": {"type": "inno", "mode": "extract"}}
    root = _extractor(run).extract(tmp_path / "s.exe", dest, install, lambda: False)
    assert Path(run.calls[0][0]).name == "innoextract.exe"
    assert root == dest
    assert (dest / "Game.exe").exists()
    assert not (dest / "app").exists()


def test_dispatch_msi_uses_absolute_targetdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = FakeRun()
    install = {"method": "installer", "installer": {"type": "msi", "mode": "extract"}}
    _extractor(run).extract(Path("g.msi"), Path("rel-out"), install, lambda: False)
    argv = run.calls[0]
    assert argv[0] == "msiexec" and argv[1] == "/a"
    assert Path(argv[2]).is_absolute()
    assert argv[4] == f"TARGETDIR={tmp_path / 'rel-out'}"


def test_dispatch_none_copies_the_file(tmp_path):
    src = tmp_path / "Game.exe"
    src.write_bytes(b"exe")
    run = FakeRun()
    dest = tmp_path / "o"
    root = _extractor(run).extract(src, dest, {"method": "none"}, lambda: False)
    assert run.calls == []
    assert root == dest
    assert (dest / "Game.exe").read_bytes() == b"exe"


def test_extract_dir_is_applied(tmp_path):
    dest = tmp_path / "o"
    run = FakeRun(side_effect=lambda argv: (dest / "Sub").mkdir(parents=True, exist_ok=True))
    result = _extractor(run).extract(tmp_path / "a.zip", dest, {"method": "zip", "extract_dir": "Sub"},
                                     lambda: False)
    assert result == dest / "Sub"


def test_run_mode_and_web_methods_are_refused(tmp_path):
    run = FakeRun()
    with pytest.raises(ValueError):
        _extractor(run).extract(tmp_path / "s.exe", tmp_path / "o",
                                {"method": "installer", "installer": {"type": "inno", "mode": "run"}},
                                lambda: False)
    with pytest.raises(ValueError):
        _extractor(run).extract(tmp_path / "s.exe", tmp_path / "o", {"method": "url"}, lambda: False)
    assert run.calls == []


def test_nonzero_exit_is_extract_error(tmp_path):
    with pytest.raises(ExtractError) as info:
        _extractor(FakeRun(code=2)).extract(tmp_path / "a.zip", tmp_path / "o", {"method": "zip"}, lambda: False)
    assert str(info.value) == messages.EXTRACT_FAILED
    assert info.value.exit_code == 2


@pytest.mark.windows
def test_junction_pointing_outside_dest_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "o"

    def plant_junction(argv):
        dest.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cmd", "/c", "mklink", "/J", str(dest / "evil"), str(outside)], check=True,
                       capture_output=True)

    with pytest.raises(ExtractError):
        _extractor(FakeRun(side_effect=plant_junction)).extract(tmp_path / "a.zip", dest, {"method": "zip"},
                                                                lambda: False)


def test_default_run_cancel_kills_the_process():
    start = time.monotonic()
    with pytest.raises(Cancelled):
        run_process([sys.executable, "-c", "import time; time.sleep(60)"], lambda: time.monotonic() - start > 0.5)
    assert time.monotonic() - start < 10


def test_default_run_returns_exit_code():
    assert run_process([sys.executable, "-c", "raise SystemExit(3)"], lambda: False) == 3


def test_default_run_missing_tool_is_extract_error(tmp_path):
    with pytest.raises(ExtractError):
        run_process([str(tmp_path / "nope.exe")], lambda: False)


@pytest.mark.tools
def test_real_zip_and_password_7z(tmp_path, make_zip):
    extractor = Extractor(TOOLS_DIR)
    zpath = make_zip({"Game/Game.exe": b"exe", "Game/data/x.txt": b"x"})
    out = tmp_path / "zip-out"
    assert extractor.extract(zpath, out, {"method": "zip"}, lambda: False) == out
    assert (out / "Game" / "Game.exe").read_bytes() == b"exe"

    src = tmp_path / "secret.txt"
    src.write_bytes(b"s")
    sevenz = tmp_path / "pw.7z"
    subprocess.run([str(TOOLS_DIR / "7z.exe"), "a", "-pSECRET", "-mhe=on", str(sevenz), str(src)],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)
    out2 = tmp_path / "7z-out"
    extractor.extract(sevenz, out2, {"method": "7z", "password": "SECRET"}, lambda: False)
    assert (out2 / "secret.txt").read_bytes() == b"s"


@pytest.mark.tools
def test_wrong_password_fails_fast(tmp_path):
    src = tmp_path / "secret.txt"
    src.write_bytes(b"s")
    sevenz = tmp_path / "pw.7z"
    subprocess.run([str(TOOLS_DIR / "7z.exe"), "a", "-pSECRET", "-mhe=on", str(sevenz), str(src)],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)
    start = time.monotonic()
    with pytest.raises(ExtractError):
        Extractor(TOOLS_DIR).extract(sevenz, tmp_path / "out", {"method": "7z", "password": "WRONG"},
                                     lambda: False)
    with pytest.raises(ExtractError):  # no password at all: 7z must not prompt
        Extractor(TOOLS_DIR).extract(sevenz, tmp_path / "out2", {"method": "7z"}, lambda: False)
    assert time.monotonic() - start < 10


@pytest.mark.tools
def test_dotdot_entries_do_not_escape_dest(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escaped.txt", b"bad")
        zf.writestr("ok.txt", b"ok")
    dest = tmp_path / "work" / "out"
    try:
        Extractor(TOOLS_DIR).extract(evil, dest, {"method": "zip"}, lambda: False)
    except ExtractError:
        pass  # refusing the archive is also fine; writing outside dest is not
    assert not (tmp_path / "work" / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()
