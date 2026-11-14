"""Choosing a download source and checking it against the catalog."""

import hashlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from engine.entries import is_mirror_url, platform_sources
from engine.errors import BlockedHost, DownloadError, NoVerifiedSource
from engine.messages import (
    DEVELOPER_CHANGED_UNVERIFIED,
    DEVELOPER_CHANGED_VERIFIED,
    SOURCE_BLOCKED,
    SOURCE_CHECKSUM_MISMATCH,
    SOURCE_UNREACHABLE,
    Progress,
)
from engine.paths import sanitize_title

_HASH_CHUNK = 1 << 20


@dataclass
class Fetched:
    path: Path
    source: dict
    position: int
    sha256: str
    size: int
    note: str | None


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _download_name(url: str) -> str:
    name = unquote(PurePosixPath(urlparse(url).path).name).strip()
    return sanitize_title(name) if name else "download.bin"


def _matches(source: dict, actual_sha: str, actual_size: int) -> bool:
    if source.get("sha256") and source["sha256"].lower() != actual_sha:
        return False
    if source.get("size") is not None and source["size"] != actual_size:
        return False
    return True


def _walk(sources: list[dict], work_dir: Path, fetcher, progress, cancel, *, keep_changed_developer: bool) -> Fetched:
    work_dir = Path(work_dir)
    reasons: list[str] = []
    downloaded = False  # a file arrived but didn't match
    candidate: Fetched | None = None
    made: list[Path] = []  # attempt folders still on disk

    def drop(folder: Path) -> None:
        shutil.rmtree(folder, ignore_errors=True)
        if folder in made:
            made.remove(folder)

    try:
        for position, source in enumerate(sources):
            url = source["url"]
            folder = work_dir / f"source-{position}"
            folder.mkdir(parents=True, exist_ok=True)
            made.append(folder)
            dest = folder / _download_name(url)
            try:
                fetcher.download(
                    url, dest,
                    lambda done, total: progress(Progress("downloading", done, total)),
                    cancel,
                )
            except BlockedHost:
                reasons.append(SOURCE_BLOCKED)
                drop(folder)
                continue
            except DownloadError:
                reasons.append(SOURCE_UNREACHABLE)
                drop(folder)
                continue
            progress(Progress("verifying"))
            downloaded = True
            actual_sha = hash_file(dest)
            actual_size = dest.stat().st_size
            if _matches(source, actual_sha, actual_size):
                if candidate is not None:
                    drop(candidate.path.parent)
                note = DEVELOPER_CHANGED_VERIFIED if candidate is not None else None
                made.remove(folder)
                return Fetched(dest, source, position, actual_sha, actual_size, note)
            if keep_changed_developer and not is_mirror_url(url) and candidate is None:
                candidate = Fetched(dest, source, position, actual_sha, actual_size, DEVELOPER_CHANGED_UNVERIFIED)
                continue
            reasons.append(SOURCE_CHECKSUM_MISMATCH)
            drop(folder)
        if candidate is not None:
            made.remove(candidate.path.parent)
            return candidate
        raise NoVerifiedSource(reasons, downloaded)
    finally:
        for folder in list(made):
            shutil.rmtree(folder, ignore_errors=True)


def fetch_verified(entry: dict, platform: str, work_dir: Path, fetcher,
                   progress: Callable[[Progress], None], cancel) -> Fetched:
    """First source that verifies wins; a changed developer file is the fallback."""
    return _walk(platform_sources(entry, platform), work_dir, fetcher, progress, cancel,
                 keep_changed_developer=True)


def fetch_exact(urls: list[str], sha256: str, work_dir: Path, fetcher,
                progress: Callable[[Progress], None], cancel) -> Fetched:
    """Accept only a file whose hash equals `sha256`."""
    sources = [{"url": u, "sha256": sha256} for u in urls]
    return _walk(sources, work_dir, fetcher, progress, cancel, keep_changed_developer=False)
