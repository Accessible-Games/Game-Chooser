"""Choosing the folder a catalog game is installed into."""

import os
from pathlib import Path

from .entries import first_developer
from .paths import sanitize_title
from .records import RecordStore


def library_label(lib: dict) -> str:
    """What a library is called on screen: its folder's name, or the whole path for a drive root."""
    path = str(lib.get("path") or "")
    return os.path.basename(os.path.normpath(path)) or path


def target_folder(entry: dict, library_path: Path, records: RecordStore, libraries: list[dict]) -> Path:
    """`<library>/<Title>`, unless another catalog game's record owns that folder.

    Then the first developer is added, `<Title> (<Developer>)`, and if that is taken too,
    the catalog id, `<Title> (<id>)`. A folder this game's own record owns is never "taken".
    """
    title = entry.get("title") or entry["id"]
    library_path = Path(library_path)
    names = [title]
    developer = first_developer(entry)
    if developer:
        names.append(f"{title} ({developer})")
    names.append(f"{title} ({entry['id']})")
    candidates = [library_path / sanitize_title(name) for name in names]
    for candidate in candidates:
        owner = records.owner_of_folder(candidate, libraries)
        if owner is None or owner.catalog_id == entry["id"]:
            return candidate
    return candidates[-1]
