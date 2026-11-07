"""The test catalog that scripts/serve_test_catalog.py builds: the app accepts its published index."""
import hashlib
import importlib.util
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from engine.entries import current_platform, is_readable, visible_games
from engine.errors import DownloadError
from engine.extract import Extractor
from engine.index import CatalogIndex

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("serve_test_catalog", ROOT / "scripts" / "serve_test_catalog.py")
serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve)

PORT = 8765
BASE = serve.base_url(PORT)
HAVE_7Z = serve.find_seven_zip() is not None


class LocalFetcher:
    """Reads what the test server would serve straight from the build folder."""

    def __init__(self, build_dir: Path):
        self.build_dir = build_dir

    def get_bytes(self, url: str, max_bytes: int = 64 * 2**20) -> bytes:
        assert url.startswith(BASE), url
        path = self.build_dir / urlsplit(url).path.lstrip("/")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise DownloadError(str(exc)) from exc


def load(tmp_path, **options):
    build = tmp_path / "build"
    serve.build_catalog(build, PORT, **options)
    state = CatalogIndex(tmp_path / "cache", BASE, LocalFetcher(build)).refresh()
    assert state.error is None and not state.from_cache
    return build, state


def test_index_loads_and_every_entry_is_readable(tmp_path):
    _, state = load(tmp_path)
    assert {"test-game", "test-game-no-path", "test-game-extras", "test-extra"} <= set(state.entries)
    assert all(is_readable(e) for e in state.entries.values())
    visible = {e["id"] for e in visible_games(state.entries, current_platform())}
    assert {"test-game", "test-game-no-path", "test-game-extras", "trace-test"} <= visible
    assert "test-extra" not in visible  # an extra is not a game


def test_every_source_matches_its_built_file_and_points_at_the_local_server(tmp_path):
    build, state = load(tmp_path)
    for entry in state.entries.values():
        for source in entry["sources"]:
            assert source["url"].startswith(BASE + "files/")
            assert "agarchive" not in source["url"]
            data = LocalFetcher(build).get_bytes(source["url"])
            assert hashlib.sha256(data).hexdigest() == source["sha256"] and len(data) == source["size"]


def test_version_two_and_the_launch_path_flags(tmp_path):
    _, v1 = load(tmp_path / "a")
    _, v2 = load(tmp_path / "b", version=2, add_launch_path=True)
    assert v1.entries["test-game"]["version"] == "1.0" and v2.entries["test-game"]["version"] == "2.0"
    assert v1.entries["test-game"]["sources"][0]["sha256"] != v2.entries["test-game"]["sources"][0]["sha256"]
    assert "launch" not in v1.entries["test-game-no-path"]
    assert v2.entries["test-game-no-path"]["launch"] == {"windows": {"path": "launcher/start.bat"}}


def test_required_extra_is_listed_on_its_game(tmp_path):
    _, state = load(tmp_path)
    assert state.entries["test-game-extras"]["extras"] == [{"id": "test-extra", "required": True}]
    assert state.entries["test-extra"]["install_into"] == "test-game-extras"


@pytest.mark.skipif(not serve.TINY_GAME_SOURCE.is_file(), reason="needs where.exe in System32")
def test_single_exe_game_is_a_copy_of_where_exe(tmp_path):
    build, state = load(tmp_path)
    source = state.entries["test-game-none"]["sources"][0]
    assert source["install"] == {"method": "none"} and source["url"].endswith("/TinyGame.exe")
    assert (build / "files" / "TinyGame.exe").read_bytes() == serve.TINY_GAME_SOURCE.read_bytes()


@pytest.mark.tools
@pytest.mark.skipif(not HAVE_7Z, reason="bin/7z.exe is missing")
def test_password_7z_extracts_with_the_catalog_password(tmp_path):
    build, state = load(tmp_path)
    install = state.entries["test-game-7z"]["sources"][0]["install"]
    archive = build / "files" / "test-game-7z.7z"
    root = Extractor(serve.ROOT / "bin").extract(archive, tmp_path / "out", install, lambda: False)
    assert (root / "game.bat").is_file() and (root / "data.dat").is_file()
    with pytest.raises(Exception):
        Extractor(serve.ROOT / "bin").extract(archive, tmp_path / "bad", {"method": "7z", "password": "nope"},
                                              lambda: False)
