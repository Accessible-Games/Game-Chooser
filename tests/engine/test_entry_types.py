"""The types of the entry fields the app reads, checked once as the index loads."""

import copy

import pytest

from engine.entry_types import checked, wrong_fields

SHA = "a" * 64


def source(**fields):
    return {
        "label": "Mirror", "url": "https://files.accessiblegames.net/files/" + SHA + "/g.exe", "platform": "windows",
        "arch": "x86", "size": 10, "first_run_download_size": 0, "sha256": SHA,
        "install": {"method": "installer", "extract_dir": "Game", "dmg": "Game.dmg",
                    "installer": {"type": "inno", "mode": "run", "silent_args": "/S", "elevation": True}},
        "steps": [
            {"action": "copy", "from": "a", "to": "b"}, {"action": "move", "from": "a", "to": "b"},
            {"action": "delete", "path": "old.dat"}, {"action": "register", "path": "dx.dll"},
            {"action": "run", "path": "fix.exe"}, {"action": "run", "path": "fix.exe", "args": "/q"},
            {"action": "registry", "hive": "HKCU", "key": "Software\\G", "name": "Path", "type": "REG_SZ", "value": "x"},
            {"action": "registry", "hive": "HKCU", "key": "Software\\G", "name": "N", "type": "REG_DWORD", "value": 1},
        ],
        **fields,
    }


def full(**fields):
    """An entry with every field the app reads, each of the right type."""
    return {
        "id": "game", "kind": "game", "title": "Game", "developer": ["A", "B"], "genres": ["Racing"], "year": 2007,
        "description": "A **game**.", "version": "1.0", "self_updating": True,
        "pricing": {"model": "one-time", "price": 4.99, "suggested": 5, "currency": "USD", "period": "month",
                    "checked": "2026-11-01", "no_longer_sold": True, "url": "https://shop.example/",
                    "donate_url": "https://give.example/", "note": "Sold by the developer."},
        "tags": ["Multiplayer"], "languages": ["en"], "contains": ["Violence"], "unsupported": True,
        "links": [{"url": "https://example.com/"}, {"url": "https://example.com/forum", "label": "Forum"}],
        "stores": [{"store": "steam", "app_id": 5, "platforms": ["windows"]}],
        "sources": [source(), {"url": "https://example.com/play", "platform": "web", "install": {"method": "url"}}],
        "persist": ["saves/"], "launch": {"windows": {"path": "game.exe", "args": "-x"}, "macos": {"path": "G.app"}},
        "dependencies": ["dx8vb"], "extras": [{"id": "game-music", "required": False}, {"id": "game-patch"}],
        "detect": {"registry": {"hive": "HKLM", "key": "Software\\G", "name": "V"}},
        "extends": ["other"], "required": True, "checkver": {"github": "a/b"}, "something_new": {"any": ["thing"]},
        **fields,
    }


def test_an_entry_of_the_right_types_comes_back_as_it_is():
    entry = full()
    assert wrong_fields(entry) == ([], []) and checked(entry) is entry
    assert checked({"id": "a"}) == {"id": "a"}  # nothing but the id: nothing to check
    assert checked(full(detect={"file": "%WINDIR%\\x.dll"})) is not None


DISPLAY = [
    ("title", ["Game"]), ("title", 5), ("title", None),
    ("developer", "Bob"), ("developer", [1]), ("developer", [["Bob"]]), ("developer", {"name": "Bob"}),
    ("genres", "Racing"), ("genres", {}), ("genres", [None]),
    ("tags", None), ("tags", [5]), ("languages", "en"), ("contains", [{}]),
    ("year", "2007"), ("year", True), ("year", 2007.5), ("year", [2007]),
    ("description", 5), ("description", ["a"]),
    ("unsupported", "yes"), ("unsupported", 1),
    ("links", "https://example.com/"), ("links", [{"label": "no address"}]), ("links", [{"url": 5}]),
    ("links", [{"url": "https://example.com/", "label": 5}]), ("links", ["https://example.com/"]),
    ("links", {"homepage": "https://example.com/"}),
    ("pricing", "free"), ("pricing", {"model": ["free"]}), ("pricing", {"price": 5}),
    ("pricing", {"model": "one-time", "price": "5"}), ("pricing", {"model": "one-time", "price": True}),
    ("pricing", {"model": "one-time", "currency": 5}), ("pricing", {"model": "one-time", "no_longer_sold": "yes"}),
    ("pricing", {"model": "one-time", "url": ["https://shop.example/"]}), ("pricing", {"model": "free", "note": 1}),
]


@pytest.mark.parametrize("field, value", DISPLAY)
def test_a_display_field_of_the_wrong_type_reads_as_not_recorded(field, value):
    entry = full(**{field: value})
    before = copy.deepcopy(entry)
    kept = checked(entry)
    assert wrong_fields(entry) == ([field], [])
    assert field not in kept and kept == {k: v for k, v in before.items() if k != field}
    assert entry == before  # the entry as it was read is not changed


def test_several_wrong_display_fields_are_all_dropped():
    kept = checked(full(title=5, year="x", links=7))
    assert not {"title", "year", "links"} & set(kept) and kept["genres"] == ["Racing"]


def step(**fields):
    return [source(steps=[fields])]


INSTALL = [
    ("kind", ["game"]), ("kind", 5), ("kind", None),
    ("version", 1.0), ("version", None), ("version", ["1.0"]),
    ("self_updating", "true"), ("self_updating", 1),
    ("dependencies", "dx8vb"), ("dependencies", [5]), ("dependencies", [["dx8vb"]]), ("dependencies", None),
    ("persist", "saves/"), ("persist", [1]),
    ("extras", {"id": "x"}), ("extras", ["x"]), ("extras", [{"required": True}]), ("extras", [{"id": ["x"]}]),
    ("extras", [{"id": "x", "required": "no"}]),
    ("launch", "game.exe"), ("launch", {"windows": "game.exe"}), ("launch", {"windows": {"args": "-x"}}),
    ("launch", {"windows": {"path": ["game.exe"]}}), ("launch", {"windows": {"path": "g.exe", "args": 5}}),
    ("launch", ["game.exe"]),
    ("detect", "x.dll"), ("detect", {"file": 5}), ("detect", {"registry": "HKLM"}),
    ("detect", {"registry": {"hive": "HKLM"}}), ("detect", {"registry": {"hive": "HKLM", "key": 5}}),
    ("detect", {"registry": {"hive": "HKLM", "key": "k", "name": 5}}),
    ("sources", "https://example.com/g.zip"), ("sources", {"url": "x"}), ("sources", [None]), ("sources", ["x"]),
    ("sources", [source(url=None)]), ("sources", [source(url=["https://example.com/"])]),
    ("sources", [{"platform": "windows", "install": {"method": "zip"}}]),             # no url
    ("sources", [{"url": "https://example.com/g.zip", "install": {"method": "zip"}}]),  # no platform
    ("sources", [{"url": "https://example.com/g.zip", "platform": "windows"}]),         # no install
    ("sources", [source(platform=5)]), ("sources", [source(arch=["x64"])]), ("sources", [source(label=5)]),
    ("sources", [source(size="10")]), ("sources", [source(size=1.5)]), ("sources", [source(size=True)]),
    ("sources", [source(first_run_download_size="1")]), ("sources", [source(sha256=5)]),
    ("sources", [source(sha256=None)]),
    ("sources", [source(install="zip")]), ("sources", [source(install={})]),
    ("sources", [source(install={"method": ["zip"]})]),
    ("sources", [source(install={"method": "zip", "extract_dir": 5})]),
    ("sources", [source(install={"method": "dmg", "dmg": ["x.dmg"]})]),
    ("sources", [source(install={"method": "installer", "installer": "inno"})]),
    ("sources", [source(install={"method": "installer", "installer": {"type": 5, "mode": "run"}})]),
    ("sources", [source(install={"method": "installer", "installer": {"type": "inno", "mode": ["run"]}})]),
    ("sources", [source(install={"method": "installer",
                                 "installer": {"type": "inno", "mode": "run", "silent_args": 5}})]),
    ("sources", [source(install={"method": "installer",
                                 "installer": {"type": "inno", "mode": "run", "elevation": "yes"}})]),
    ("sources", [source(steps="copy")]), ("sources", [source(steps=[None])]), ("sources", [source(steps=["x"])]),
    ("sources", step(path="x")),                                           # no action
    ("sources", step(action=["delete"], path="x")),
    ("sources", step(action="copy", to="b")), ("sources", step(action="copy", **{"from": "a"})),
    ("sources", step(action="move", to=5, **{"from": "a"})),
    ("sources", step(action="delete")), ("sources", step(action="delete", path=5)),
    ("sources", step(action="register")), ("sources", step(action="register", path=["dx.dll"])),
    ("sources", step(action="run")), ("sources", step(action="run", path="fix.exe", args=5)),
    ("sources", step(action="registry", hive="HKCU", key="k", name="n", type="REG_SZ")),              # no value
    ("sources", step(action="registry", hive="HKCU", key="k", name="n", type="REG_SZ", value=True)),
    ("sources", step(action="registry", hive="HKCU", key="k", name="n", type="REG_SZ", value=1.5)),
    ("sources", step(action="registry", hive="HKCU", key="k", name="n", type="REG_SZ", value=["x"])),
    ("sources", step(action="registry", hive="HKCU", key="k", type="REG_SZ", value="x")),             # no name
    ("sources", step(action="registry", hive=5, key="k", name="n", type="REG_SZ", value="x")),
    ("sources", step(action="registry", hive="HKCU", key="k", name="n", type=1, value="x")),
]


@pytest.mark.parametrize("field, value", INSTALL)
def test_an_install_field_of_the_wrong_type_hides_the_entry(field, value):
    entry = full(**{field: value})
    before = copy.deepcopy(entry)
    assert checked(entry) is None and wrong_fields(entry) == ([], [field])
    assert entry == before


def test_wrong_fields_names_both_kinds():
    assert wrong_fields(full(year="x", tags=5, kind=5, sources=7)) == (["tags", "year"], ["kind", "sources"])


def test_a_step_with_an_action_this_app_does_not_know_is_left_to_is_readable():
    """It hides the entry there ("a value this app does not know"), so its members are not this check's to judge."""
    assert checked(full(sources=step(action="shell", cmd=5))) is not None


def test_fields_the_app_does_not_read_are_not_checked():
    entry = full(extends="other", required="yes", checkver=5, something_new=None, stores="steam")
    assert checked(entry) is entry  # `stores` has its own reader, stores_of, which trusts nothing


def test_an_entry_that_is_not_an_object_is_no_entry():
    for junk in (None, "game", 5, ["id"]):
        assert checked(junk) is None
