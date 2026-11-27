"""Choosing the folder a catalog game is installed into."""

import os
from pathlib import Path


def library_label(lib: dict) -> str:
    """What a library is called on screen: its folder's name, or the whole path for a drive root."""
    path = str(lib.get("path") or "")
    return os.path.basename(os.path.normpath(path)) or path


def target_folder(entry: dict, library_path: Path) -> Path:
    """`<library>/<id>`. Never the title or the developer: on the site anyone approved to edit can change those,
    and an id is unique, never changes and is safe as a folder's name (`entries.is_id`)."""
    return Path(library_path) / entry["id"]
