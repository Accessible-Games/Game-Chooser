import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from engine import messages
from engine.errors import Cancelled, ExtractError, UnsafePath
from engine.extract import (Extractor, _app_program, content_root, ditto_command, flatten_inno,
                            innoextract_command, msi_admin_command, run_process, seven_zip_command)
from tests.conftest import REPO_ROOT

TOOLS_DIR = REPO_ROOT / "bin"
# NTFS keeps no run bit: a file's mode reads 0o666 whatever chmod sets, so only the choice of program is checked there
RUN_BIT = sys.platform != "win32"


def test_seven_zip_command():
    """The tool's path as this system writes it ("bin\\7z.exe" on Windows; the Mac runs bin/7zz the same way)"""
    assert seven_zip_command(Path("bin/7z.exe"), Path("a.7z"), Path("out")) == [
        str(Path("bin/7z.exe")), "x", "-y", "-bso0", "-bsp0", "-oout", "a.7z"]


def test_innoextract_and_msi_commands():
    assert innoextract_command(Path("bin/innoextract.exe"), Path("s.exe"), Path("o")) == [
        str(Path("bin/innoextract.exe")), "--silent", "--extract", "--output-dir", "o", "s.exe"]
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


def test_flatten_inno_keeps_a_game_folder_named_tmp(tmp_path):
    (tmp_path / "app" / "tmp").mkdir(parents=True)
    (tmp_path / "app" / "tmp" / "keep.txt").write_bytes(b"k")
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "junk").write_bytes(b"j")
    flatten_inno(tmp_path)
    assert (tmp_path / "tmp" / "keep.txt").read_bytes() == b"k"
    assert not (tmp_path / "tmp" / "junk").exists()


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


def _extractor(run, platform="win32"):
    return Extractor(Path("tools"), run=run, platform=platform)


@pytest.mark.parametrize("install", [
    {"method": "zip"},
    {"method": "7z"},
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
def test_real_zip(tmp_path, make_zip):
    zpath = make_zip({"Game/Game.exe": b"exe", "Game/data/x.txt": b"x"})
    out = tmp_path / "zip-out"
    assert Extractor(TOOLS_DIR).extract(zpath, out, {"method": "zip"}, lambda: False) == out
    assert (out / "Game" / "Game.exe").read_bytes() == b"exe"


@pytest.mark.tools
def test_password_archive_fails_fast(tmp_path):
    src = tmp_path / "secret.txt"
    src.write_bytes(b"s")
    sevenz = tmp_path / "pw.7z"
    subprocess.run([str(TOOLS_DIR / "7z.exe"), "a", "-pSECRET", "-mhe=on", str(sevenz), str(src)],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)
    start = time.monotonic()
    with pytest.raises(ExtractError) as info:  # 7z must not prompt
        Extractor(TOOLS_DIR).extract(sevenz, tmp_path / "out", {"method": "7z"}, lambda: False)
    assert str(info.value) == messages.EXTRACT_FAILED
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


@pytest.mark.tools
@pytest.mark.parametrize("form", ["drive", "rooted", "backslash", "dotdot"])
def test_link_entries_do_not_write_outside_dest(tmp_path, form):
    # 7-Zip 26.06 tries to create absolute-target links 24.09 refused up front; only a machine with the
    # symlink privilege (the elevated release runner) gets as far as creating one, elsewhere this still holds
    outside = tmp_path / "outside"
    outside.mkdir()
    rooted = outside.as_posix()[len(outside.drive):]
    target = {"drive": outside.as_posix(), "rooted": rooted, "backslash": rooted.replace("/", "\\"),
              "dotdot": "../outside"}[form]
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        link = zipfile.ZipInfo("lnk")
        link.create_system, link.external_attr = 3, (stat.S_IFLNK | 0o777) << 16  # a unix symlink entry
        zf.writestr(link, target)
        zf.writestr("lnk/pwned.txt", b"bad")
    dest = tmp_path / "out"
    try:
        Extractor(TOOLS_DIR).extract(evil, dest, {"method": "zip"}, lambda: False)
    except ExtractError:
        pass  # refusing the archive is fine; writing through the link is not
    else:
        assert not [p for p in dest.rglob("*") if p.is_symlink() or p.is_junction()]
    assert list(outside.iterdir()) == []


def _tar(path: Path, mode: str, members: dict[str, bytes], links: dict[str, tuple[bytes, str]] | None = None) -> Path:
    """A tar archive of `members` (name -> bytes); `links` adds (type, target) link members."""
    import io
    import tarfile
    with tarfile.open(path, mode) as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, (kind, target) in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname = kind, target
            tar.addfile(info)
    return path


@pytest.mark.parametrize("suffix, mode", [(".tar.gz", "w:gz"), (".tar.xz", "w:xz"), (".tar.bz2", "w:bz2")])
def test_tar_archives_unpack_without_a_tool(tmp_path, suffix, mode):
    archive = _tar(tmp_path / f"game{suffix}", mode, {"Game/game.sh": b"run", "Game/data/level.dat": b"1"})
    run = FakeRun()
    root = _extractor(run).extract(archive, tmp_path / "o", {"method": "tar", "extract_dir": "Game"},
                                   lambda: False)
    assert run.calls == []
    assert root == tmp_path / "o" / "Game"
    assert (root / "game.sh").read_bytes() == b"run" and (root / "data" / "level.dat").read_bytes() == b"1"


@pytest.mark.parametrize("name", ["../escape.txt", "a/../../escape.txt"])
def test_tar_entries_never_escape_dest(tmp_path, name):
    archive = _tar(tmp_path / "bad.tar.gz", "w:gz", {"ok.txt": b"ok", name: b"evil"})
    with pytest.raises(ExtractError) as info:
        _extractor(FakeRun()).extract(archive, tmp_path / "out" / "o", {"method": "tar"}, lambda: False)
    assert str(info.value) == messages.EXTRACT_FAILED
    assert not (tmp_path / "out" / "escape.txt").exists() and not (tmp_path / "escape.txt").exists()


def test_tar_absolute_names_land_inside_dest(tmp_path):
    archive = _tar(tmp_path / "abs.tar.gz", "w:gz", {"/abs-gc-test.txt": b"x"})
    root = _extractor(FakeRun()).extract(archive, tmp_path / "o", {"method": "tar"}, lambda: False)
    assert (root / "abs-gc-test.txt").read_bytes() == b"x"


@pytest.mark.windows
def test_tar_drive_letter_names_are_refused(tmp_path):
    archive = _tar(tmp_path / "drive.tar.gz", "w:gz", {f"{tmp_path.drive}/gc-escape.txt": b"x"})
    with pytest.raises(ExtractError):
        _extractor(FakeRun()).extract(archive, tmp_path / "o", {"method": "tar"}, lambda: False)
    assert not Path(f"{tmp_path.drive}/gc-escape.txt").exists()


@pytest.mark.parametrize("kind", ["sym", "hard"])
def test_tar_links_are_refused(tmp_path, kind):
    import tarfile
    link = (tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE, "game.sh")
    archive = _tar(tmp_path / "links.tar.gz", "w:gz", {"game.sh": b"run"}, {"lib.so": link})
    with pytest.raises(ExtractError) as info:
        _extractor(FakeRun()).extract(archive, tmp_path / "o", {"method": "tar"}, lambda: False)
    assert str(info.value) == messages.EXTRACT_UNSAFE_LINK


def test_tar_cancel_and_damaged_archive(tmp_path):
    archive = _tar(tmp_path / "g.tar.gz", "w:gz", {"a.txt": b"a", "b.txt": b"b"})
    with pytest.raises(Cancelled):
        _extractor(FakeRun()).extract(archive, tmp_path / "o", {"method": "tar"}, lambda: True)
    damaged = tmp_path / "damaged.tar.gz"
    damaged.write_bytes(archive.read_bytes()[:40])
    with pytest.raises(ExtractError):
        _extractor(FakeRun()).extract(damaged, tmp_path / "o2", {"method": "tar"}, lambda: False)
    not_tar = tmp_path / "not.tar.gz"
    not_tar.write_bytes(b"not an archive at all")
    with pytest.raises(ExtractError):
        _extractor(FakeRun()).extract(not_tar, tmp_path / "o3", {"method": "tar"}, lambda: False)


def test_mac_unzips_with_ditto(tmp_path):
    run = FakeRun()
    dest = tmp_path / "o"
    assert _extractor(run, "darwin").extract(tmp_path / "a.zip", dest, {"method": "zip"}, lambda: False) == dest
    assert run.calls == [ditto_command(tmp_path / "a.zip", dest)]
    assert run.calls[0][:3] == ["/usr/bin/ditto", "-x", "-k"]


@pytest.mark.parametrize("install", [
    {"method": "7z"},
    {"method": "cab"},
    {"method": "installer", "installer": {"type": "nsis", "mode": "extract"}},
])
def test_mac_uses_the_mac_seven_zip(tmp_path, install):
    run = FakeRun()
    _extractor(run, "darwin").extract(tmp_path / "a.bin", tmp_path / "o", install, lambda: False)
    assert Path(run.calls[0][0]) == Path("tools") / "7zz"


@pytest.mark.parametrize("kind", ["inno", "msi"])
def test_mac_refuses_windows_installers_without_running_anything(tmp_path, kind):
    run = FakeRun()
    install = {"method": "installer", "installer": {"type": kind, "mode": "extract"}}
    with pytest.raises(ExtractError) as err:
        _extractor(run, "darwin").extract(tmp_path / "s.exe", tmp_path / "o", install, lambda: False)
    assert str(err.value) == messages.EXTRACT_NEEDS_WINDOWS
    assert run.calls == [] and not (tmp_path / "o").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="ditto is macOS'")
def test_real_ditto_keeps_the_run_bit(tmp_path):
    src = tmp_path / "src" / "Game.app" / "Contents" / "MacOS"
    src.mkdir(parents=True)
    (src / "Game").write_bytes(b"program")
    (src / "Game").chmod(0o755)
    archive = tmp_path / "game.zip"
    subprocess.run(["/usr/bin/zip", "-qry", str(archive), "Game.app"], cwd=tmp_path / "src", check=True)
    out = tmp_path / "out"
    Extractor(tmp_path / "no-tools", platform="darwin").extract(archive, out, {"method": "zip"}, lambda: False)
    assert (out / "Game.app" / "Contents" / "MacOS" / "Game").stat().st_mode & stat.S_IXUSR


@pytest.mark.skipif(sys.platform != "darwin", reason="ditto is macOS'")
def test_real_ditto_writes_nothing_through_a_link_that_leaves(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "link").symlink_to(outside)
    (tmp_path / "b" / "link").mkdir(parents=True)
    (tmp_path / "b" / "link" / "evil.txt").write_text("bad")
    archive = tmp_path / "evil.zip"
    subprocess.run(["/usr/bin/zip", "-qy", str(archive), "link"], cwd=tmp_path / "a", check=True)
    subprocess.run(["/usr/bin/zip", "-q", str(archive), "link/evil.txt"], cwd=tmp_path / "b", check=True)
    with pytest.raises(ExtractError):
        Extractor(tmp_path / "no-tools", platform="darwin").extract(archive, tmp_path / "out", {"method": "zip"},
                                                                    lambda: False)
    assert list(outside.iterdir()) == []


def _app_unpacker(name="Game.app", program="Game", plist_program="Game", mode=0o644):
    """A fake 7-Zip run that leaves a Mac app whose program lacks the run bit, as a zip made on Windows does."""
    import plistlib

    def unpack(argv):
        dest = Path(next(a for a in argv if a.startswith("-o"))[2:])
        contents = dest / name / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": plist_program}))
        (contents / "MacOS" / program).write_bytes(b"program")
        (contents / "MacOS" / program).chmod(mode)
        (contents / "MacOS" / "helper").write_bytes(b"not the app's program")
        (contents / "MacOS" / "helper").chmod(mode)
    return unpack


def test_mac_gives_each_apps_program_the_run_bit(tmp_path):
    run = FakeRun(side_effect=_app_unpacker())
    dest = tmp_path / "o"
    _extractor(run, "darwin").extract(tmp_path / "a.7z", dest, {"method": "7z"}, lambda: False)
    macos = dest / "Game.app" / "Contents" / "MacOS"
    assert _app_program(dest / "Game.app") == macos / "Game"
    if RUN_BIT:
        assert stat.S_IMODE((macos / "Game").stat().st_mode) == 0o755
        assert stat.S_IMODE((macos / "helper").stat().st_mode) == 0o644


def test_mac_skips_a_plist_that_names_a_path_out_of_the_app(tmp_path):
    run = FakeRun(side_effect=_app_unpacker(plist_program="../../../Game.app/Contents/MacOS/helper"))
    dest = tmp_path / "o"
    _extractor(run, "darwin").extract(tmp_path / "a.7z", dest, {"method": "7z"}, lambda: False)
    assert _app_program(dest / "Game.app") is None
    if RUN_BIT:
        assert stat.S_IMODE((dest / "Game.app" / "Contents" / "MacOS" / "helper").stat().st_mode) == 0o644


def test_mac_skips_an_app_without_a_readable_plist(tmp_path):
    def unpack(argv):
        _app_unpacker()(argv)
        dest = Path(next(a for a in argv if a.startswith("-o"))[2:])
        (dest / "Game.app" / "Contents" / "Info.plist").write_bytes(b"not a plist")
    dest = tmp_path / "o"
    _extractor(FakeRun(side_effect=unpack), "darwin").extract(tmp_path / "a.7z", dest, {"method": "7z"}, lambda: False)
    assert _app_program(dest / "Game.app") is None
    if RUN_BIT:
        assert stat.S_IMODE((dest / "Game.app" / "Contents" / "MacOS" / "Game").stat().st_mode) == 0o644


def test_windows_leaves_an_apps_program_alone(tmp_path, monkeypatch):
    monkeypatch.setattr("engine.extract.mark_mac_programs", lambda dest: pytest.fail("marked on Windows"))
    run = FakeRun(side_effect=_app_unpacker())
    dest = tmp_path / "o"
    _extractor(run, "win32").extract(tmp_path / "a.7z", dest, {"method": "7z"}, lambda: False)
    if RUN_BIT:
        assert stat.S_IMODE((dest / "Game.app" / "Contents" / "MacOS" / "Game").stat().st_mode) == 0o644


def test_mac_takes_a_program_named_from_contents_as_nvgt_writes_it(tmp_path):
    run = FakeRun(side_effect=_app_unpacker(plist_program="MacOS/Game"))
    dest = tmp_path / "o"
    _extractor(run, "darwin").extract(tmp_path / "a.7z", dest, {"method": "7z"}, lambda: False)
    assert _app_program(dest / "Game.app") == dest / "Game.app" / "Contents" / "MacOS" / "Game"
    if RUN_BIT:
        assert stat.S_IMODE((dest / "Game.app" / "Contents" / "MacOS" / "Game").stat().st_mode) == 0o755
