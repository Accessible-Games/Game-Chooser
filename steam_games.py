"""Which Steam rows a scan of Steam's files adds to and removes from the Library. Pure rules, no wx."""
from engine.entries import game_fields, platform_label
from engine.stores import stores_of


def _steam_ids(entry):
    """(full id, demo id) of the entry's Steam store, or None."""
    for store in stores_of(entry):
        if store.is_steam:
            return store.app_id, store.demo_app_id
    return None


def steam_changes(entries: dict, scan, games: list, platform: str) -> tuple[list[dict], list]:
    """(fields of the rows to add, the Steam rows to remove). A scan that could not tell changes nothing."""
    if not entries or scan is None or scan.state == "unknown":
        return [], []
    add, remove = [], []

    def new_row(entry, app_id):
        return {**game_fields(entry), "platforms": [platform_label(platform)], "launch_path": "",
                "library_id": "", "catalog_id": entry["id"], "steam_app_id": app_id}

    def wanted(ids):
        full, demo = ids
        return full if full in scan.apps else demo if demo and demo in scan.apps else 0

    seen = set()
    for g in games:
        if not g.is_steam:
            continue
        if g.catalog_id in seen:  # a second Steam row for one entry
            remove.append(g)
            continue
        seen.add(g.catalog_id)
        entry = entries.get(g.catalog_id)
        ids = _steam_ids(entry) if entry and entry.get("kind") == "game" else None
        if (ids is None or g.steam_app_id not in ids or scan.state == "absent"
                or any(o.catalog_id == g.catalog_id and not o.is_steam for o in games)):
            remove.append(g)
            continue
        want = wanted(ids)
        if not want:
            if scan.gone(g.steam_app_id):
                remove.append(g)
        elif want == ids[0] and g.steam_app_id != want:
            remove.append(g)  # rule 3 adds the full game's row
        elif want != ids[0] and g.steam_app_id == ids[0] and scan.gone(ids[0]):
            remove.append(g)  # rule 3 adds the demo's row

    if scan.state == "found":
        have = {g.catalog_id for g in games if not any(g is r for r in remove)}
        for cid, entry in entries.items():
            ids = _steam_ids(entry)
            if entry.get("kind") == "game" and ids and cid not in have and wanted(ids):
                add.append(new_row(entry, wanted(ids)))
    return add, remove


class ScanGate:
    """One scan at a time, and one more after it when one was asked for meanwhile. UI thread only."""

    def __init__(self):
        self._running = False
        self._again = False

    def request(self) -> bool:
        if self._running:
            self._again = True
            return False
        self._running = True
        return True

    def done(self) -> bool:
        again, self._again = self._again, False
        self._running = again
        return again
