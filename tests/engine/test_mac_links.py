"""Links a Mac app ships (a framework's Versions/Current) are game files on a Mac, and the player's elsewhere."""
import os
import zipfile
from pathlib import Path

import pytest

from engine import paths
from engine.errors import ExtractError
from engine.extract import _refuse_links_leaving
from engine.jobs import InstallRequest
from engine.records import hash_tree
from engine.sources import hash_file
from tests.engine.fakes import MIRROR, FakeFetcher, ZipExtractor, make_engine, never, served_source, zip_bytes

LINK = b"LINK:"
FW = "Game.app/Contents/Frameworks/S.framework"
V1 = {
    "Game.app/Contents/MacOS/game": b"program",
    f"{FW}/Versions/B/S": b"lib",
    f"{FW}/Versions/B/Resources/r.txt": b"r",
    f"{FW}/Versions/Current": LINK + b"B",
    f"{FW}/S": LINK + b"Versions/Current/S",
    f"{FW}/Resources": LINK + b"Versions/Current/Resources",
}


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(paths, "KEEPS_LINKS", True)


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(paths, "KEEPS_LINKS", False)


class LinkExtractor(ZipExtractor):
    """Unzips, then turns each file holding LINK + target into a link to that target, as a Mac app's zip has."""

    def extract(self, archive, dest, install, cancel):
        root = super().extract(archive, dest, install, cancel)
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                data = zf.read(name)
                if data.startswith(LINK):
                    path = Path(dest) / name
                    path.unlink()
                    path.symlink_to(data[len(LINK):].decode())
        return root


def catalog(tmp_path, files=V1):
    routes = {}
    e = mac_game(routes, files)
    engine = make_engine(tmp_path, [e], extractor=LinkExtractor(), fetcher=FakeFetcher(routes))
    return engine, tmp_path / "Games" / "Game"


def mac_game(routes, files, version="1.0"):
    source = served_source(routes, f"{MIRROR}game/game-{version}.zip", zip_bytes(files))
    return {"id": "game", "kind": "game", "title": "Game", "version": version, "sources": [source],
            "launch": {"windows": {"path": "Game.app/Contents/MacOS/game"}}}


def run(engine, action, lib):
    return engine.run(InstallRequest(action, "game", "Games", lib), lambda p: None, never)


def links(folder):
    return {p.relative_to(folder).as_posix(): os.readlink(p) for p in folder.rglob("*") if p.is_symlink()}


def test_mac_unpack_keeps_links_that_stay_inside(tmp_path):
    (tmp_path / "Versions" / "B").mkdir(parents=True)
    (tmp_path / "Versions" / "Current").symlink_to("B")
    (tmp_path / "S").symlink_to("Versions/Current")
    _refuse_links_leaving(tmp_path, mac=True)
    with pytest.raises(ExtractError):
        _refuse_links_leaving(tmp_path, mac=False)


@pytest.mark.parametrize("target", ["/Applications", "../outside", "missing"])
def test_mac_unpack_refuses_links_that_leave_or_are_broken(tmp_path, target):
    (tmp_path / "outside").mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "link").symlink_to(target)
    with pytest.raises(ExtractError):
        _refuse_links_leaving(dest, mac=True)


def test_hash_tree_records_links_on_a_mac(tmp_path, mac):
    (tmp_path / "B").mkdir()
    (tmp_path / "B" / "f").write_bytes(b"f")
    (tmp_path / "Current").symlink_to("B")
    (tmp_path / "file-link").symlink_to("B/f")
    assert hash_tree(tmp_path) == {"b/f": ("B/f", hash_file(tmp_path / "B" / "f")),
                                   "current": ("Current", "link:B"), "file-link": ("file-link", "link:B/f")}


def test_hash_tree_is_unchanged_elsewhere(tmp_path, windows):
    (tmp_path / "B").mkdir()
    (tmp_path / "B" / "f").write_bytes(b"f")
    (tmp_path / "Current").symlink_to("B")
    (tmp_path / "file-link").symlink_to("B/f")
    content = hash_file(tmp_path / "B" / "f")
    assert hash_tree(tmp_path) == {"b/f": ("B/f", content), "file-link": ("file-link", content)}


def test_mac_install_repair_and_uninstall_of_an_app_with_a_framework(tmp_path, mac):
    engine, folder = catalog(tmp_path)
    lib = folder.parent
    run(engine, "install", lib)
    expected = {f"{FW}/Versions/Current": "B", f"{FW}/S": "Versions/Current/S",
                f"{FW}/Resources": "Versions/Current/Resources"}
    assert links(folder) == expected
    rec = engine.ctx.records.load("game")
    assert rec.files[f"{FW}/Versions/Current".lower()].sha256 == "link:B"
    assert not any("current/" in key or key.endswith("/s/s") for key in rec.files)
    (folder / FW / "Versions" / "Current").unlink()
    (folder / FW / "S").unlink()
    run(engine, "repair", lib)
    assert links(folder) == expected
    assert (folder / FW / "S").read_bytes() == b"lib"
    run(engine, "uninstall", lib)
    assert not folder.exists()


def test_mac_update_and_undo_change_add_and_remove_links(tmp_path, mac):
    engine, folder = catalog(tmp_path)
    lib = folder.parent
    run(engine, "install", lib)
    v2 = {k: v for k, v in V1.items() if not k.endswith("/Resources")}
    v2.update({f"{FW}/Versions/C/S": b"lib 2", f"{FW}/Versions/Current": LINK + b"C",
               f"{FW}/Extra": LINK + b"Versions/Current/S"})
    engine.ctx.entries["game"] = mac_game(engine.ctx.fetcher.routes, v2, version="2.0")
    run(engine, "update", lib)
    assert links(folder) == {f"{FW}/Versions/Current": "C", f"{FW}/S": "Versions/Current/S",
                             f"{FW}/Extra": "Versions/Current/S"}
    assert (folder / FW / "S").read_bytes() == b"lib 2"
    run(engine, "undo", lib)
    assert links(folder) == {f"{FW}/Versions/Current": "B", f"{FW}/S": "Versions/Current/S",
                             f"{FW}/Resources": "Versions/Current/Resources"}
    assert (folder / FW / "S").read_bytes() == b"lib"


def test_mac_uninstall_keeps_a_link_the_player_changed(tmp_path, mac):
    engine, folder = catalog(tmp_path)
    lib = folder.parent
    run(engine, "install", lib)
    (folder / FW / "Resources").unlink()
    (folder / FW / "Resources").symlink_to("Versions/B")
    run(engine, "uninstall", lib)
    assert links(folder) == {f"{FW}/Resources": "Versions/B"}
    assert not (folder / FW / "Versions" / "B").exists()
