"""Unpack downloaded archives with the bundled 7-Zip, innoextract, Windows' msiexec, or Python's tarfile."""

import bz2  # noqa: F401  tarfile imports it lazily; imported here so the build includes it
import gzip
import logging
import lzma
import os
import shutil
import subprocess
import sys
import tarfile
import zlib
from pathlib import Path
from typing import Callable

from . import messages
from .errors import Cancelled, ExtractError
from .paths import safe_join

log = logging.getLogger(__name__)

CancelCheck = Callable[[], bool]
Run = Callable[[list[str], CancelCheck], int]

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_POLL_SECONDS = 0.2
_SEVEN_ZIP_METHODS = {"zip", "7z", "cab"}
_SEVEN_ZIP_INSTALLERS = {"nsis", "installshield", "other"}


def default_tools_dir() -> Path:
    return Path(sys.argv[0]).resolve().parent / "bin"


def seven_zip_command(seven_zip: Path, archive: Path, dest: Path) -> list[str]:
    return [str(seven_zip), "x", "-y", "-bso0", "-bsp0", f"-o{dest}", str(archive)]


def innoextract_command(tool: Path, archive: Path, dest: Path) -> list[str]:
    return [str(tool), "--silent", "--extract", "--output-dir", str(dest), str(archive)]


def msi_admin_command(msi: Path, dest: Path) -> list[str]:
    return ["msiexec", "/a", str(msi), "/qn", f"TARGETDIR={dest}"]


def _merge_move(src: Path, target: Path) -> None:
    if src.is_dir() and not src.is_symlink() and target.is_dir() and not target.is_symlink():
        for child in list(src.iterdir()):
            _merge_move(child, target / child.name)
        src.rmdir()
        return
    if target.exists() or target.is_symlink():
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    shutil.move(str(src), str(target))


def flatten_inno(dest: Path) -> None:
    """innoextract writes the game to `app/` (and scratch files to `tmp/`); lift `app/` up into `dest`."""
    app = dest / "app"
    if not app.is_dir():
        return
    holding = dest / ".gamechooser-inno-app"  # so a folder called "app" inside app/ can't collide with app/
    if holding.exists():
        shutil.rmtree(holding)
    shutil.rmtree(dest / "tmp", ignore_errors=True)  # innoextract's scratch; before the move so the game's own tmp/ survives
    app.rename(holding)
    for child in list(holding.iterdir()):
        _merge_move(child, dest / child.name)
    holding.rmdir()


def content_root(dest: Path, extract_dir: str | None) -> Path:
    if not extract_dir:
        return dest
    root = safe_join(dest, extract_dir)
    if not root.is_dir():
        raise ExtractError(messages.EXTRACT_FOLDER_MISSING)
    return root


def run_process(argv: list[str], cancel: CancelCheck) -> int:
    """Run a tool without a console window or stdin. Cancelling kills it and raises Cancelled."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=_CREATE_NO_WINDOW)
    except OSError as exc:
        log.warning("could not start %s: %s", argv[0], exc)
        raise ExtractError(messages.EXTRACT_FAILED) from exc
    try:
        while True:
            try:
                return proc.wait(timeout=_POLL_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            if cancel():
                raise Cancelled()
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        raise


def extract_tar(archive: Path, dest: Path, cancel: CancelCheck) -> None:
    """Unpack a .tar.gz, .tar.xz or .tar.bz2 (or a plain .tar) into `dest`, one member at a time so a cancel
    stops it. tarfile's "data" filter refuses absolute names, `..`, links leading out of `dest` and device
    files; links of any kind are refused here too, as for every archive (see _refuse_links_leaving)."""
    try:
        with tarfile.open(archive, "r:*") as tar:
            for member in tar:
                if cancel():
                    raise Cancelled()
                if member.issym() or member.islnk():
                    log.warning("tar archive has a link: %s", member.name)
                    raise ExtractError(messages.EXTRACT_UNSAFE_LINK)
                tar.extract(member, dest, filter="data")
    except tarfile.FilterError as exc:
        log.warning("tar archive refused: %s", exc)
        raise ExtractError(messages.EXTRACT_FAILED) from exc
    except (tarfile.TarError, gzip.BadGzipFile, zlib.error, lzma.LZMAError, EOFError) as exc:
        # a damaged archive; other OSErrors (a full disk, say) reach the job, which words them
        log.warning("could not unpack the tar archive: %s", exc)
        raise ExtractError(messages.EXTRACT_FAILED) from exc


def _refuse_links_leaving(dest: Path) -> None:
    """Archives must not leave links (symlinks or junctions) in `dest`: they could point anywhere."""
    for current, dirs, files in os.walk(dest):
        for name in dirs + files:
            path = Path(current) / name
            if path.is_symlink() or path.is_junction():
                log.warning("extraction produced a link: %s", path)
                raise ExtractError(messages.EXTRACT_UNSAFE_LINK)


class Extractor:
    def __init__(self, tools_dir: Path, run: Run | None = None):
        self.tools_dir = Path(tools_dir)
        self.run = run or run_process

    def _run_checked(self, argv: list[str], cancel: CancelCheck) -> None:
        code = self.run(argv, cancel)
        if code != 0:
            log.warning("%s exited with %s", Path(argv[0]).name, code)
            err = ExtractError(messages.EXTRACT_FAILED)
            err.exit_code = code  # internal detail only; never shown to players
            raise err

    def extract(self, archive: Path, dest: Path, install: dict, cancel: CancelCheck) -> Path:
        """Unpack `archive` into `dest` and return the folder that holds the game.

        Only `installer` entries with `mode: extract` are handled here; running an installer
        (`mode: run`) is not extraction and raises ValueError. 7-Zip's own refusal of absolute and
        `..` entry names is relied on to keep writes inside `dest`; afterwards we also refuse links.
        """
        method = install.get("method")
        archive, dest = Path(archive), Path(dest)
        if method in _SEVEN_ZIP_METHODS:
            tool = "7z"
        elif method == "installer":
            installer = install.get("installer") or {}
            if installer.get("mode") != "extract":
                raise ValueError("Only installers marked mode 'extract' can be extracted")
            kind = installer.get("type")
            if kind == "inno":
                tool = "inno"
            elif kind == "msi":
                tool = "msi"
            elif kind in _SEVEN_ZIP_INSTALLERS:
                tool = "7z"
            else:
                raise ValueError(f"Unknown installer type: {kind!r}")
        elif method == "tar":
            tool = "tar"
        elif method == "none":
            tool = "copy"
        else:
            raise ValueError(f"Nothing to extract for method {method!r}")

        dest.mkdir(parents=True, exist_ok=True)
        if tool == "copy":
            shutil.copy2(archive, dest / archive.name)
        elif tool == "tar":
            extract_tar(archive, dest, cancel)
        elif tool == "7z":
            self._run_checked(seven_zip_command(self.tools_dir / "7z.exe", archive, dest), cancel)
        elif tool == "inno":
            self._run_checked(innoextract_command(self.tools_dir / "innoextract.exe", archive, dest), cancel)
            flatten_inno(dest)
        else:  # msi: msiexec needs absolute paths, and admin installs leave a copy of the .msi behind
            msi, target = archive.resolve(), dest.resolve()
            self._run_checked(msi_admin_command(msi, target), cancel)
            stray = target / msi.name
            if stray.is_file():
                stray.unlink()
        _refuse_links_leaving(dest)
        return content_root(dest, install.get("extract_dir"))
