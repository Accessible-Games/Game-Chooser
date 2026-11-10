"""Library status and update detection: recorded values only, no downloads and no file hashing."""

import os

from .entries import platform_sources

READY = "Ready"
UPDATE_AVAILABLE = "Update available"
EXE_NOT_FOUND = "Executable not found"
LIBRARY_NOT_FOUND = "Library not found"


def update_available(record, entry: dict | None, platform: str) -> bool:
    """Has the catalog's download for this install changed since it was installed?

    Extras do not count: only the main source is compared. A game that updates itself (`self_updating`) never
    has one: its catalog version and checksum follow the developer's releases, not what is on the player's disk.
    """
    if record is None or entry is None or record.uninstalled or record.kind == "runtime":
        return False
    if entry.get("self_updating") is True:
        return False
    sources = platform_sources(entry, platform)
    if not sources:
        return False
    installed = record.source
    hashes = [s["sha256"] for s in sources if s.get("sha256")]
    if hashes:
        # Either recorded hash: the catalog's at install time, or the file actually installed (a developer's
        # changed file, installed with a note) that the catalog may since have accepted.
        return installed.catalog_sha256 not in hashes and installed.sha256 not in hashes
    position = installed.position if 0 <= installed.position < len(sources) else 0
    current = sources[position]
    if current.get("url") != installed.url:
        return True
    if installed.catalog_size is not None and current.get("size") is not None \
            and current["size"] != installed.catalog_size:
        return True
    return entry.get("version") != installed.version


def game_status(full_path: str | None, is_web: bool, record, entry: dict | None, platform: str,
                library_reachable: bool = True) -> str:
    if not library_reachable:
        return LIBRARY_NOT_FOUND
    if not is_web and (not full_path or not os.path.exists(full_path)):
        return EXE_NOT_FOUND
    if update_available(record, entry, platform):
        return UPDATE_AVAILABLE
    return READY
