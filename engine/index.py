"""Catalog index: fetch, verify, check the types of what it holds, and cache for offline use."""

import gzip
import hashlib
import json
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from engine import messages
from engine.entries import is_id
from engine.entry_types import checked
from engine.errors import DownloadError
from engine.paths import write_atomic

DEFAULT_CATALOG_URL = "https://catalog.accessiblegames.net/"
SUPPORTED_SCHEMA_VERSION = 2
META_NAME = "index-meta.json"
GZ_NAME = "index.json.gz"
MAX_INDEX_BYTES = 64 * 2**20


@dataclass
class IndexState:
    entries: dict[str, dict] = field(default_factory=dict)
    newer_app_needed: bool = False
    error: str | None = None


def normalize_catalog_url(url: str) -> str | None:
    """Return `url` with a trailing slash, or None unless https (or http to localhost)."""
    try:
        parts = urlsplit((url or "").strip())
        host = parts.hostname
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if not host or not (scheme == "https" or (scheme == "http" and host in ("127.0.0.1", "localhost"))):
        return None
    text = url.strip()
    return text if text.endswith("/") else text + "/"


def _schema_of(doc) -> int:
    version = doc.get("schema_version") if isinstance(doc, dict) else None
    return version if isinstance(version, int) and not isinstance(version, bool) else 0


def _decode(gz: bytes) -> dict:
    """Decode a gzipped index; every failure surfaces as ValueError."""
    try:
        doc = json.loads(gzip.decompress(gz).decode("utf-8"))
    except (OSError, EOFError, zlib.error, RecursionError, UnicodeError) as exc:
        raise ValueError(f"unreadable index: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError("index is not an object")
    return doc


def _entries_by_id(doc: dict) -> dict[str, dict]:
    """The index's entries as the app may read them (`entry_types.checked`), by id. An entry that is not an
    object, has no id the app takes (`entries.is_id`) or has an install field of the wrong type is left out.
    Raises ValueError when `entries` is not a list: that is no index, not an empty one."""
    entries = doc.get("entries")
    if not isinstance(entries, list):
        raise ValueError("index has no list of entries")
    out: dict[str, dict] = {}
    for entry in entries:
        entry = checked(entry)
        if entry is not None and is_id(entry.get("id")):
            out[entry["id"]] = entry
    return out


class CatalogIndex:
    def __init__(self, cache_dir: Path, base_url: str, fetcher):
        self.cache_dir = Path(cache_dir)
        self.base_url = base_url
        self.fetcher = fetcher
        self._gz_path, self._meta_path = self.cache_dir / GZ_NAME, self.cache_dir / META_NAME

    def _read_cache(self) -> tuple[IndexState, str | None] | None:
        """The cached index and its gz sha256, or None when absent or unusable."""
        try:
            gz = self._gz_path.read_bytes()
            doc = _decode(gz)
        except (OSError, ValueError, EOFError, gzip.BadGzipFile):
            return None
        if _schema_of(doc) > SUPPORTED_SCHEMA_VERSION:
            return None
        try:
            state = IndexState(_entries_by_id(doc))
        except ValueError:
            return None
        return state, hashlib.sha256(gz).hexdigest()

    def load_cached(self) -> IndexState:
        cached = self._read_cache()
        return cached[0] if cached else IndexState()

    def _cached_meta_sha(self) -> str | None:
        try:
            meta = json.loads(self._meta_path.read_bytes())
        except (OSError, ValueError):
            return None
        sha = meta.get("sha256") if isinstance(meta, dict) else None
        return sha.lower() if isinstance(sha, str) else None

    def _fallback(self, error: str, empty_error: str, newer: bool = False) -> IndexState:
        state = self.load_cached()
        state.newer_app_needed = newer
        state.error = (None if newer else error) if state.entries else empty_error
        return state

    def refresh(self) -> IndexState:
        try:
            meta = json.loads(self.fetcher.get_bytes(urljoin(self.base_url, META_NAME), max_bytes=1_000_000))
            if not isinstance(meta, dict):
                raise ValueError("meta is not an object")
            wanted, index_url = meta["sha256"], meta.get("index_url", GZ_NAME)
            if not isinstance(wanted, str) or not isinstance(index_url, str):
                raise ValueError("meta's sha256 or index_url is not text")
            wanted = wanted.lower()
            gz_url = urljoin(self.base_url, index_url or GZ_NAME)
        except DownloadError:
            return self._fallback(messages.CATALOG_OFFLINE, messages.CATALOG_OFFLINE_EMPTY)
        except (ValueError, KeyError):
            return self._fallback(messages.CATALOG_BAD_DOWNLOAD, messages.CATALOG_BAD_DOWNLOAD_EMPTY)

        if _schema_of(meta) > SUPPORTED_SCHEMA_VERSION:
            return self._fallback(messages.CATALOG_NEWER_APP, messages.CATALOG_NEWER_APP, newer=True)

        cached = self._read_cache()
        if cached and cached[1] == wanted and self._cached_meta_sha() == wanted:
            return cached[0]

        try:
            gz = self.fetcher.get_bytes(gz_url, max_bytes=MAX_INDEX_BYTES)
        except DownloadError:
            return self._fallback(messages.CATALOG_OFFLINE, messages.CATALOG_OFFLINE_EMPTY)
        if hashlib.sha256(gz).hexdigest() != wanted:
            return self._fallback(messages.CATALOG_BAD_DOWNLOAD, messages.CATALOG_BAD_DOWNLOAD_EMPTY)
        try:
            doc = _decode(gz)
        except (ValueError, EOFError, gzip.BadGzipFile):
            return self._fallback(messages.CATALOG_BAD_DOWNLOAD, messages.CATALOG_BAD_DOWNLOAD_EMPTY)
        if _schema_of(doc) > SUPPORTED_SCHEMA_VERSION:
            return self._fallback(messages.CATALOG_NEWER_APP, messages.CATALOG_NEWER_APP, newer=True)

        try:
            state = IndexState(_entries_by_id(doc))
        except ValueError:
            return self._fallback(messages.CATALOG_BAD_DOWNLOAD, messages.CATALOG_BAD_DOWNLOAD_EMPTY)
        try:
            self._write_cache(gz, json.dumps(meta).encode("utf-8"))
        except OSError:
            pass  # the fresh index is still usable this session
        return state

    def _write_cache(self, gz: bytes, meta: bytes) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for path, data in ((self._gz_path, gz), (self._meta_path, meta)):  # gz first, then meta
            write_atomic(path, data)
