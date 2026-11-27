"""The types of the catalog entry fields this app reads, checked once as the index is read.

The site checks every entry before it publishes one, so a wrong type here is a slip in how the index was written,
or a file that is not the site's. Either way the rest of the app reads entries without asking what type a value
is, so nothing of the wrong type gets past this module:

- an **install field** (what the app fetches, runs or writes by) of the wrong type hides the whole entry;
- a **display field** (what the app only shows) of the wrong type is left out, so it reads as not recorded.

JSON `null` fits nothing: the site leaves an empty value out. A field or member not named here is not checked
(the app does not read it). `stores` is not here either: `stores.stores_of` is its one reader and trusts nothing.
Whether a value is one the app knows (a kind, a method, a step's action) is `entries.is_readable`'s question.
"""

from collections.abc import Callable

Fits = Callable[[object], bool]


def _str(value: object) -> bool:
    return isinstance(value, str)


def _bool(value: object) -> bool:
    return isinstance(value, bool)


def _int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _list_of(item: Fits) -> Fits:
    return lambda value: isinstance(value, list) and all(item(x) for x in value)


def _map_of(item: Fits) -> Fits:
    return lambda value: isinstance(value, dict) and all(item(x) for x in value.values())


def _obj(required: dict[str, Fits], optional: dict[str, Fits] | None = None) -> Fits:
    """An object holding every `required` member, each member named here of its type. Other members are ignored."""
    optional = optional or {}

    def fits(value: object) -> bool:
        return (isinstance(value, dict)
                and all(name in value and check(value[name]) for name, check in required.items())
                and all(check(value[name]) for name, check in optional.items() if name in value))
    return fits


_FROM_TO = _obj({"from": _str, "to": _str})
_PATH = _obj({"path": _str})
# The members engine.steps reads without asking, by action
_STEPS: dict[str, Fits] = {
    "copy": _FROM_TO, "move": _FROM_TO, "delete": _PATH, "register": _PATH,
    "run": _obj({"path": _str}, {"args": _str}),
    "registry": _obj({"hive": _str, "key": _str, "name": _str, "type": _str,
                      "value": lambda value: _str(value) or _int(value)}),
}


def _step(value: object) -> bool:
    if not isinstance(value, dict) or not _str(value.get("action")):
        return False
    shape = _STEPS.get(value["action"])
    return shape is None or shape(value)  # an action this app does not know: is_readable hides the entry


_INSTALL = _obj({"method": _str}, {
    "extract_dir": _str, "dmg": _str,
    "installer": _obj({}, {"type": _str, "mode": _str, "silent_args": _str, "elevation": _bool}),
})
_SOURCE = _obj({"url": _str, "platform": _str, "install": _INSTALL}, {
    "label": _str, "arch": _str, "sha256": _str, "size": _int, "first_run_download_size": _int,
    "steps": _list_of(_step),
})

INSTALL_FIELDS: dict[str, Fits] = {
    "kind": _str,
    "version": _str,
    "self_updating": _bool,
    "sources": _list_of(_SOURCE),
    "launch": _map_of(_obj({"path": _str}, {"args": _str})),
    "dependencies": _list_of(_str),
    "extras": _list_of(_obj({"id": _str}, {"required": _bool})),
    "persist": _list_of(_str),
    "detect": _obj({}, {"file": _str, "registry": _obj({"hive": _str, "key": _str}, {"name": _str})}),
}

DISPLAY_FIELDS: dict[str, Fits] = {
    "title": _str,
    "developer": _list_of(_str),
    "genres": _list_of(_str),
    "tags": _list_of(_str),
    "languages": _list_of(_str),
    "contains": _list_of(_str),
    "year": _int,
    "description": _str,
    "unsupported": _bool,
    "links": _list_of(_obj({"url": _str}, {"label": _str})),
    "pricing": _obj({"model": _str}, {
        "price": _number, "suggested": _number, "currency": _str, "period": _str, "checked": _str,
        "no_longer_sold": _bool, "url": _str, "donate_url": _str, "note": _str,
    }),
}


def _wrong(entry: dict, fields: dict[str, Fits]) -> list[str]:
    return sorted(name for name, fits in fields.items() if name in entry and not fits(entry[name]))


def wrong_fields(entry: dict) -> tuple[list[str], list[str]]:
    """(the display fields, the install fields) of `entry` whose value is of the wrong type, each by name."""
    return _wrong(entry, DISPLAY_FIELDS), _wrong(entry, INSTALL_FIELDS)


def checked(entry: object) -> dict | None:
    """`entry` as the app may read it: None if it is not an object or an install field is of the wrong type, else
    the entry without its display fields of the wrong type (the entry itself when there are none)."""
    if not isinstance(entry, dict):
        return None
    display, install = wrong_fields(entry)
    if install:
        return None
    if not display:
        return entry
    return {name: value for name, value in entry.items() if name not in display}
