"""An entry's `stores`, read without trusting it: an item or value of the wrong shape is skipped, never raised on."""

from dataclasses import dataclass

from .pricing import http_url
from .steam import is_app_id, store_page


@dataclass(frozen=True)
class Store:
    name: str                    # what the player reads: "Steam", or the entry's own store name
    platforms: tuple[str, ...]   # catalog platform names, as written
    url: str                     # the store page for the browser; Steam's is built from app_id
    app_id: int = 0              # Steam only
    demo_app_id: int = 0         # Steam only; 0 when there is no demo

    @property
    def is_steam(self) -> bool:
        return bool(self.app_id)


def _platforms(item: dict) -> tuple[str, ...]:
    raw = item.get("platforms")
    names = raw if isinstance(raw, list) else []
    return tuple(dict.fromkeys(p for p in names if isinstance(p, str) and p.strip()))


def stores_of(entry: dict | None) -> list[Store]:
    raw = entry.get("stores") if isinstance(entry, dict) else None
    found: list[Store] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("store"), str):
            continue
        platforms = _platforms(item)
        name = item["store"].strip()
        key = name.casefold()
        if not platforms or not name or key in seen:
            continue
        if item["store"] == "steam":
            app_id = item.get("app_id")
            if not is_app_id(app_id):
                continue
            demo = item.get("demo_app_id")
            demo = demo if is_app_id(demo) and demo != app_id else 0
            store = Store("Steam", platforms, store_page(app_id), app_id, demo)
            key = "steam"
        elif key == "steam":
            continue
        else:
            url = http_url(item.get("url"))
            if url is None:
                continue
            store = Store(name, platforms, url)
        seen.add(key)
        found.append(store)
    return found
