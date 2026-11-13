import gzip
import json

import pytest

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
    assert s.from_cache is False and s.error is None and s.newer_app_needed is False
    assert s.generated_at == "2026-11-04T00:00:00Z"
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
    assert s.newer_app_needed and s.entries == old and s.from_cache
    assert idx.load_cached().entries == old


def test_newer_schema_in_gz_only_keeps_cache(tmp_path):
    fetcher, idx = make(tmp_path)
    old = idx.refresh().entries
    publish(fetcher, BASE, [{"id": "z"}], schema_version=3)
    meta = json.loads(fetcher.routes[META])
    meta["schema_version"] = 2
    fetcher.routes[META] = json.dumps(meta).encode()
    s = idx.refresh()
    assert s.newer_app_needed and s.entries == old and s.from_cache


def test_bad_gz_hash_keeps_cache(tmp_path):
    fetcher, idx = make(tmp_path)
    idx.refresh()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    publish(fetcher, BASE, [{"id": "z"}])
    meta = json.loads(fetcher.routes[META])
    meta["sha256"] = "0" * 64
    fetcher.routes[META] = json.dumps(meta).encode()
    s = idx.refresh()
    assert s.error and set(s.entries) == {"a", "b"} and s.from_cache
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_garbage_meta_keeps_cache(tmp_path):
    fetcher, idx = make(tmp_path)
    idx.refresh()
    fetcher.routes[META] = b"not json"
    s = idx.refresh()
    assert s.error and set(s.entries) == {"a", "b"}


def test_offline_with_cache(tmp_path):
    fetcher, idx = make(tmp_path)
    idx.refresh()
    fetcher.routes.clear()
    s = idx.refresh()
    assert set(s.entries) == {"a", "b"} and s.from_cache and s.error


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
    assert s.entries == {} and s.from_cache
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


def test_corrupt_deflate_body_from_server_keeps_cache(tmp_path):
    fetcher, idx = make(tmp_path)
    idx.refresh()
    bad = _bad_body_gz()
    fetcher.routes[GZ] = bad
    meta = json.loads(fetcher.routes[META])
    meta["sha256"] = sha(bad)
    fetcher.routes[META] = json.dumps(meta).encode()
    s = idx.refresh()
    assert s.error and set(s.entries) == {"a", "b"}
