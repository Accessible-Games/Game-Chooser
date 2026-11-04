"""Shared test fakes for the engine tests."""

import gzip
import hashlib
import json

from pathlib import Path

from engine.errors import Cancelled, DownloadError
from engine.http import check_url_allowed


class FakeFetcher:
    """Serves canned bytes by URL; counts calls per URL."""

    def __init__(self, routes: dict[str, bytes] | None = None):
        self.routes: dict[str, bytes] = dict(routes or {})
        self.calls: dict[str, int] = {}
        self.requests: list[str] = []

    def get_bytes(self, url: str, max_bytes: int = 64 * 2**20) -> bytes:
        self.calls[url] = self.calls.get(url, 0) + 1
        if url not in self.routes:
            raise DownloadError(f"404 Not Found: {url}")
        return self.routes[url]

    def download(self, url: str, dest: Path, progress=None, cancel=None) -> None:
        """Like Fetcher.download: blocked hosts refused, bytes written, progress called, cancel honored."""
        check_url_allowed(url)
        self.requests.append(url)
        if url not in self.routes:
            raise DownloadError(f"404 Not Found: {url}")
        data = self.routes[url]
        half = len(data) // 2
        dest.write_bytes(data[:half])
        if progress:
            progress(half, len(data))
        if cancel and cancel():
            dest.unlink(missing_ok=True)
            raise Cancelled()
        dest.write_bytes(data)
        if progress:
            progress(len(data), len(data))


def publish(fetcher: FakeFetcher, base_url: str, entries: list[dict], schema_version: int = 1) -> bytes:
    """Publish index.json.gz and index-meta.json the way catalog-index does; returns the gz bytes."""
    doc = {"schema_version": schema_version, "generated_at": "2026-11-04T00:00:00Z", "entries": entries}
    gz = gzip.compress(json.dumps(doc).encode("utf-8"), mtime=0)
    meta = {
        "schema_version": schema_version,
        "generated_at": doc["generated_at"],
        "entry_count": len(entries),
        "sha256": hashlib.sha256(gz).hexdigest(),
        "index_url": "index.json.gz",
    }
    fetcher.routes[base_url + "index.json.gz"] = gz
    fetcher.routes[base_url + "index-meta.json"] = json.dumps(meta).encode("utf-8")
    return gz
