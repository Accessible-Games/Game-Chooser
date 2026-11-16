import pytest

from engine.stores import Store, stores_of

STEAM = {"store": "steam", "app_id": 1234560, "demo_app_id": 1234570, "platforms": ["windows"]}
ITCH = {"store": "itch.io", "url": "https://dev.itch.io/game", "platforms": ["windows", "macos"]}


def test_stores_of_reads_both_shapes():
    steam_store, itch = stores_of({"stores": [STEAM, ITCH]})
    assert steam_store == Store("Steam", ("windows",), "https://store.steampowered.com/app/1234560/", 1234560, 1234570)
    assert itch == Store("itch.io", ("windows", "macos"), "https://dev.itch.io/game")
    assert steam_store.is_steam and not itch.is_steam


@pytest.mark.parametrize("stores", [
    None, 3, "steam", {}, [None, 3, "x"],
    [dict(STEAM, app_id=True)], [dict(STEAM, app_id="1234560")], [dict(STEAM, app_id=0)], [dict(STEAM, app_id=1.0)],
    [dict(STEAM, platforms=[])], [dict(STEAM, platforms="windows")], [dict(STEAM, platforms=[3])],
    [dict(ITCH, url="steam://run/1")], [dict(ITCH, url="javascript:alert(1)")], [dict(ITCH, url=None)],
    [dict(ITCH, url="https://dev.itch.io/a b")],
    [dict(ITCH, store="")], [dict(ITCH, store="  ")], [dict(ITCH, store=3)], [dict(ITCH, store="Steam")],
])
def test_a_store_of_the_wrong_shape_does_not_exist(stores):
    assert stores_of({"stores": stores}) == []


def test_what_is_right_is_kept_beside_what_is_wrong():
    gog = dict(ITCH, store=" GOG ", url="https://www.gog.com/game/a")
    names = [s.name for s in stores_of({"stores": [dict(STEAM, app_id="x"), ITCH, dict(ITCH, store="ITCH.IO"), gog]})]
    assert names == ["itch.io", "GOG"]                                          # a repeated name counts once
    assert stores_of({"stores": [dict(STEAM, demo_app_id=True)]})[0].demo_app_id == 0
    assert stores_of({"stores": [dict(STEAM, demo_app_id=1234560)]})[0].demo_app_id == 0   # the demo is the game
    assert [s.app_id for s in stores_of({"stores": [STEAM, dict(STEAM, app_id=5)]})] == [1234560]   # Steam once
    assert stores_of({"stores": [dict(ITCH, platforms=["windows", 3, "windows", "ios"])]})[0].platforms == (
        "windows", "ios")
    assert stores_of(None) == [] and stores_of({}) == []


def test_a_store_name_is_one_line():
    assert stores_of({"stores": [dict(ITCH, store="  itch\n.io\t shop ")]})[0].name == "itch .io shop"
    assert stores_of({"stores": [dict(ITCH, store="\n\t")]}) == []
