import gzip
import json

import pytest

from engine import messages
from engine.index import DEFAULT_CATALOG_URL, CatalogIndex, normalize_catalog_url
from tests.engine.fakes import FakeFetcher, publish, sha

BASE = DEFAULT_CATALOG_URL
GZ = BASE + "index.json.gz"
META = BASE + "index-meta.json"
ENTRIES = [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]


def make(tmp_path, entries=ENTRIES):
    fetcher = FakeFetcher()
    publish(fetcher, BASE, entries)
    return fetcher, CatalogIndex(tmp_path, BASE, fetcher)


def test_first_refresh_downloads_and_caches(tmp_path):
    fetcher, idx = make(tmp_path)
    s = idx.refresh()
    assert set(s.entries) == {"a", "b"} and s.entries["a"]["title"] == "A"
    assert s.error is None and s.newer_app_needed is False
    assert sorted(p.name for p in tmp_path.iterdir()) == ["index-meta.json", "index.json.gz"]


def test_unchanged_meta_skips_gz_download(tmp_path):
    fetcher, idx = make(tmp_path)
    idx.refresh()
    s = idx.refresh()
    assert fetcher.calls[GZ] == 1 and fetcher.calls[META] == 2
    assert set(s.entries) == {"a", "b"} and s.error is None


def test_changed_meta_downloads_again(tmp_path):
    fetcher, idx = make(tmp_path)
    idx.refresh()
    publish(fetcher, BASE, ENTRIES + [{"id": "c"}])
    s = idx.refresh()
    assert set(s.entries) == {"a", "b", "c"} and fetcher.calls[GZ] == 2


def test_entries_without_id_skipped(tmp_path):
    _, idx = make(tmp_path, [{"id": "a"}, {"title": "no id"}, "junk"])
    assert set(idx.refresh().entries) == {"a"}


GOOD = {"id": "good", "kind": "game", "title": "Good", "genres": ["Racing"], "version": "1.0",
        "sources": [{"url": "https://example.com/g.zip", "platform": "windows", "install": {"method": "zip"}}]}


def test_types_are_checked_as_the_index_is_read(tmp_path):
    """A wrong type in an install field hides the entry, one in a display field only drops that field, and an id
    that could not name a file or folder never gets in."""
    entries = [GOOD, {**GOOD, "id": "bad-sources", "sources": "https://example.com/g.zip"},
               {**GOOD, "id": "bad-genres", "genres": "Racing", "title": ["Bad"]},
               {**GOOD, "id": "con"}, {**GOOD, "id": "a" * 101}, {**GOOD, "id": ["good"]}, {**GOOD, "id": "Bad_ID"}]
    fetcher, idx = make(tmp_path, entries)
    loaded = idx.refresh().entries
    assert set(loaded) == {"good", "bad-genres"} and loaded["good"] == GOOD
    assert loaded["bad-genres"] == {k: v for k, v in GOOD.items() if k not in ("genres", "title")} | {"id": "bad-genres"}
    assert idx.load_cached().entries == loaded  # the saved copy is read the same way


def _publish_doc(fetcher, doc):
    gz = gzip.compress(json.dumps(doc).encode("utf-8"), mtime=0)
    fetcher.routes[GZ] = gz
    fetcher.routes[META] = json.dumps({"schema_version": 2, "sha256": sha(gz), "index_url": "index.json.gz"}).encode()
    return gz


@pytest.mark.parametrize("entries", [{"good": GOOD}, "good", None, 5], ids=["object", "string", "null", "number"])
def test_an_index_whose_entries_is_not_a_list_is_a_bad_download(tmp_path, entries):
    """Never an empty catalog saved over a good one."""
    fetcher, idx = make(tmp_path)
    idx.refresh()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    _publish_doc(fetcher, {"schema_version": 2, "entries": entries})
    s = idx.refresh()
    assert s.error == messages.CATALOG_BAD_DOWNLOAD and set(s.entries) == {"a", "b"}
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_a_newer_index_is_a_newer_index_whatever_shape_its_entries_have(tmp_path):
    fetcher, idx = make(tmp_path)
    idx.refresh()
    _publish_doc(fetcher, {"schema_version": 3, "entries": {"good": GOOD}})
    s = idx.refresh()
    assert s.newer_app_needed and s.error is None and set(s.entries) == {"a", "b"}


def test_an_index_with_no_entries_list_and_no_saved_copy(tmp_path):
    fetcher = FakeFetcher()
    _publish_doc(fetcher, {"schema_version": 2})
    s = CatalogIndex(tmp_path, BASE, fetcher).refresh()
    assert s.error == messages.CATALOG_BAD_DOWNLOAD_EMPTY and s.entries == {}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("change", [{"sha256": 5}, {"sha256": None}, {"sha256": ["a" * 64]}, {"index_url": ["x"]},
                                    {"index_url": 5}, {"index_url": {"href": "index.json.gz"}}],
                         ids=["hash number", "hash null", "hash list", "address list", "address number", "address object"])
def test_a_meta_value_of_the_wrong_type_is_a_bad_download(tmp_path, change):
    fetcher, idx = make(tmp_path)
    idx.refresh()
    publish(fetcher, BASE, [{"id": "z"}])
    fetcher.routes[META] = json.dumps({**json.loads(fetcher.routes[META]), **change}).encode()
    s = idx.refresh()
    assert s.error == messages.CATALOG_BAD_DOWNLOAD and set(s.entries) == {"a", "b"}
    assert fetcher.calls[GZ] == 1  # nothing was fetched on the word of such a meta


def test_a_saved_copy_whose_entries_is_not_a_list_is_not_used(tmp_path):
    fetcher, idx = make(tmp_path)
    idx.refresh()
    (tmp_path / "index.json.gz").write_bytes(gzip.compress(b'{"schema_version": 2, "entries": {"a": {"id": "a"}}}'))
    assert idx.load_cached().entries == {}
    assert set(idx.refresh().entries) == {"a", "b"} and fetcher.calls[GZ] == 2  # fetched again


def test_version_one_and_two_indexes_both_load(tmp_path):
    fetcher, idx = make(tmp_path)
    publish(fetcher, BASE, [{"id": "old", "flags": ["demo"], "links": {"homepage": "https://h"}}], schema_version=1)
    assert set(idx.refresh().entries) == {"old"}  # an app may still hold a saved version 1 index
    publish(fetcher, BASE, [{"id": "new", "links": [{"url": "https://h"}]}], schema_version=2)
    s = idx.refresh()
    assert set(s.entries) == {"new"} and not s.newer_app_needed


def test_newer_schema_keeps_cache(tmp_path):
    fetcher, idx = make(tmp_path)
    old = idx.refresh().entries
    publish(fetcher, BASE, [{"id": "z"}], schema_version=3)
    s = idx.refresh()
    assert s.newer_app_needed and s.entries == old
    assert idx.load_cached().entries == old


def test_newer_schema_in_gz_only_keeps_cache(tmp_path):
    fetcher, idx = make(tmp_path)
    old = idx.refresh().entries
    publish(fetcher, BASE, [{"id": "z"}], schema_version=3)
    meta = json.loads(fetcher.routes[META])
    meta["schema_version"] = 2
    fetcher.routes[META] = json.dumps(meta).encode()
    s = idx.refresh()
    assert s.newer_app_needed and s.entries == old


def serve(fetcher, gz, sha256):
    """Serve `gz` as the index, with `sha256` in its meta."""
    fetcher.routes[GZ] = gz
    meta = json.loads(fetcher.routes[META])
    meta["sha256"] = sha256
    fetcher.routes[META] = json.dumps(meta).encode()


@pytest.mark.parametrize("server_breaks", [
    lambda fetcher: serve(fetcher, publish(fetcher, BASE, [{"id": "z"}]), "0" * 64),
    lambda fetcher: fetcher.routes.update({META: b"not json"}),
    lambda fetcher: fetcher.routes.clear(),
    lambda fetcher: serve(fetcher, _bad_body_gz(), sha(_bad_body_gz())),
], ids=["bad_gz_hash_keeps_cache", "garbage_meta_keeps_cache", "offline_with_cache",
        "corrupt_deflate_body_from_server_keeps_cache"])
def test_a_refresh_that_fails_keeps_the_cache(tmp_path, server_breaks):
    fetcher, idx = make(tmp_path)
    idx.refresh()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    server_breaks(fetcher)
    s = idx.refresh()
    assert s.error and set(s.entries) == {"a", "b"}
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_offline_without_cache(tmp_path):
    s = CatalogIndex(tmp_path, DEFAULT_CATALOG_URL, FakeFetcher({})).refresh()
    assert s.entries == {} and s.error


def _bad_body_gz():
    good = gzip.compress(b'{"schema_version": 1, "entries": []}' * 50, mtime=0)
    return good[:12] + bytes(b ^ 0xFF for b in good[12:-8]) + good[-8:]


@pytest.mark.parametrize("garbage", [b"garbage", _bad_body_gz()], ids=["not gzip", "a corrupt deflate body"])
def test_corrupt_cache_is_ignored(tmp_path, garbage):
    fetcher, idx = make(tmp_path)
    idx.refresh()
    (tmp_path / "index.json.gz").write_bytes(garbage)
    s = idx.load_cached()
    assert s.entries == {}
    s = idx.refresh()  # meta unchanged but cache unusable: re-download
    assert set(s.entries) == {"a", "b"} and fetcher.calls[GZ] == 2


def test_corrupt_cache_offline(tmp_path):
    (tmp_path / "index.json.gz").write_bytes(b"garbage")
    (tmp_path / "index-meta.json").write_bytes(b"{}")
    s = CatalogIndex(tmp_path, BASE, FakeFetcher()).refresh()
    assert s.entries == {} and s.error


def test_load_cached_empty(tmp_path):
    s = CatalogIndex(tmp_path / "missing", BASE, FakeFetcher()).load_cached()
    assert s.entries == {} and s.error is None


def test_normalize_catalog_url():
    assert normalize_catalog_url("https://catalog.accessiblegames.net") == "https://catalog.accessiblegames.net/"
    assert normalize_catalog_url("http://127.0.0.1:8765/") == "http://127.0.0.1:8765/"
    assert normalize_catalog_url("http://localhost:8765") == "http://localhost:8765/"
    assert normalize_catalog_url("ftp://x/") is None and normalize_catalog_url("http://example.com/") is None
    assert normalize_catalog_url("") is None


def test_a_cache_that_cant_be_replaced_leaves_no_temp_file(tmp_path):
    fetcher, idx = make(tmp_path)
    (tmp_path / "index.json.gz").mkdir()  # a file can't replace a folder
    assert set(idx.refresh().entries) == {"a", "b"}  # still usable this session
    assert [p.name for p in tmp_path.iterdir()] == ["index.json.gz"]
