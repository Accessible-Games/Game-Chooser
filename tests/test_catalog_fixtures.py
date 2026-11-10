"""The test catalog that scripts/serve_test_catalog.py builds: the app accepts its published index."""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from engine.entries import current_platform, extras_of, is_readable, visible_games
from engine.errors import DownloadError
from engine.extract import Extractor
from engine.index import SUPPORTED_SCHEMA_VERSION, CatalogIndex
from engine.plan import build_install_plan
from engine.records import RecordStore
from tests.engine.fakes import FakeRegistry

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
    assert {"test-game", "test-game-no-path", "test-game-extras", "test-extra", "test-shared-extra",
            "test-game-tar"} <= set(state.entries)
    assert all(is_readable(e) for e in state.entries.values())
    visible = {e["id"] for e in visible_games(state.entries, current_platform())}
    assert {"test-game", "test-game-no-path", "test-game-extras"} <= visible
    assert {"test-game-tar"} <= visible
    assert not {"test-extra", "test-shared-extra"} & visible  # an extra is not a game


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


# The catalog schema v2 genres (docs: catalog-schema-v2 spec)
GENRES = {"Action", "Adventure", "Arcade", "Board and dice", "Card", "Casino", "Educational", "Fighting",
          "Interactive fiction", "Music and rhythm", "Platformer", "Puzzle", "Racing", "Role-playing", "Shooter",
          "Simulation", "Sports", "Strategy", "Trivia", "Word"}


def test_published_index_is_schema_version_2(tmp_path):
    build, state = load(tmp_path)
    doc = json.loads(gzip.decompress((build / "index.json.gz").read_bytes()))
    meta = json.loads((build / "index-meta.json").read_text(encoding="utf-8"))
    assert doc["schema_version"] == meta["schema_version"] == SUPPORTED_SCHEMA_VERSION == 2
    assert [e["id"] for e in doc["entries"]] == sorted(e["id"] for e in doc["entries"])
    for entry in state.entries.values():
        assert "flags" not in entry
        assert set(entry.get("genres", [])) <= GENRES
        assert all(isinstance(link, dict) and link["url"].startswith("https://") for link in entry.get("links", []))
        assert "install_into" not in entry
        if entry["kind"] == "extra":  # extends: a list of one or more game ids, no repeats
            assert isinstance(entry["extends"], list) and entry["extends"]
            assert len(set(entry["extends"])) == len(entry["extends"])
            assert all(state.entries[i]["kind"] == "game" for i in entry["extends"])
        elif entry["kind"] != "access-mod":  # the only other kind that may set extends
            assert "extends" not in entry


def test_free_to_try_game_warns_before_installing(tmp_path):
    _, state = load(tmp_path)
    plan = build_install_plan("test-game-no-path", state.entries, [{"id": "a", "path": str(tmp_path)}],
                              RecordStore(tmp_path / "records"), FakeRegistry(), True, None, "windows")
    assert plan.warnings == ["This is a test warning, shown before installing.",
                             "Price: Free to try, $5 to unlock. Buy or register: https://example.com/register"]
    tar = build_install_plan("test-game-tar", state.entries, [], RecordStore(tmp_path / "records"), FakeRegistry(),
                             True, None, "windows")
    assert tar.first_run_size == 50 * 2**20 and tar.warnings == []


def test_tar_game_extracts_without_the_bundled_tools(tmp_path):
    build, state = load(tmp_path)
    install = state.entries["test-game-tar"]["sources"][0]["install"]
    root = Extractor(tmp_path / "no-tools").extract(build / "files" / "test-game-tar.tar.gz", tmp_path / "out",
                                                     install, lambda: False)
    assert (root / "game.bat").is_file() and (root / "data.dat").is_file()


def test_extras_are_listed_on_every_game_they_extend(tmp_path):
    _, state = load(tmp_path)
    assert state.entries["test-extra"]["extends"] == ["test-game-extras"]
    assert state.entries["test-shared-extra"]["extends"] == ["test-game-extras", "test-game-tar"]
    assert state.entries["test-game-extras"]["extras"] == [{"id": "test-extra", "required": True},
                                                           {"id": "test-shared-extra", "required": False}]
    assert state.entries["test-game-tar"]["extras"] == [{"id": "test-shared-extra", "required": False}]
    assert "extras" not in state.entries["test-game"]
    for entry in state.entries.values():  # extras is exactly the entries whose extends names the game
        extending = sorted(e["id"] for e in state.entries.values() if entry["id"] in e.get("extends", []))
        assert [x["id"] for x in entry.get("extras", [])] == extending


def test_shared_extra_is_offered_to_each_of_its_games(tmp_path):
    _, state = load(tmp_path)
    records = RecordStore(tmp_path / "records")
    for game_id in ("test-game-extras", "test-game-tar"):
        assert "test-shared-extra" in [x.entry["id"] for x in extras_of(state.entries[game_id], state.entries,
                                                                         "windows")]
        plan = build_install_plan(game_id, state.entries, [{"id": "a", "path": str(tmp_path)}], records,
                                  FakeRegistry(), True, None, "windows")
        shared = [x for x in plan.extras if x.id == "test-shared-extra"]
        assert len(shared) == 1 and not shared[0].required and shared[0].title == "Test Shared Extra Level"


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
