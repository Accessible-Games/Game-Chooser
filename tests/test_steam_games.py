from pathlib import Path

from engine.steam_scan import Scan, SteamApp
from models import Game
from steam_games import ScanGate, steam_changes

FULL, DEMO = 5054570, 5273960
ENTRY = {"id": "dice", "kind": "game", "title": "Dugout Dice", "developer": ["Dev"], "genres": ["Sports"],
         "stores": [{"store": "steam", "app_id": FULL, "demo_app_id": DEMO, "platforms": ["windows"]}]}
ENTRIES = {"dice": ENTRY}
APP_COPY = Game(title="Dugout Dice", catalog_id="dice", library_id="lib", launch_path="Dice/dice.exe")


def found(*ids, unsure=(), complete=True):
    apps = {i: SteamApp(i, f"App {i}", Path("steamapps") / "common" / str(i)) for i in ids}
    return Scan("found", apps, frozenset(unsure), complete)


def row(app_id=FULL, catalog_id="dice"):
    return Game(title="Dugout Dice", catalog_id=catalog_id, platforms=["Windows"], steam_app_id=app_id)


def change(scan, games=(), entries=ENTRIES):
    add, remove = steam_changes(entries, scan, list(games), "windows")
    return [(f["catalog_id"], f["steam_app_id"]) for f in add], [g.steam_app_id for g in remove]


def test_a_catalogued_game_that_is_installed_gets_a_row():
    assert change(found(FULL)) == ([("dice", FULL)], [])
    assert change(found(DEMO)) == ([("dice", DEMO)], [])               # only the demo: the row holds the demo
    assert change(found(FULL, DEMO)) == ([("dice", FULL)], [])
    assert change(found(228980, 999)) == ([], [])                      # not in the catalog: ignored
    fields = steam_changes(ENTRIES, found(FULL), [], "windows")[0][0]
    assert fields == {"title": "Dugout Dice", "developer": "Dev", "genre": "Sports", "year": "",
                      "platforms": ["Windows"], "launch_path": "", "library_id": "", "catalog_id": "dice",
                      "steam_app_id": FULL}
    assert Game.from_dict(fields).is_steam


def test_a_row_leaves_only_when_the_scan_could_tell():
    assert change(found(FULL), [row()]) == ([], [])
    assert change(found(), [row()]) == ([], [FULL])
    assert change(found(complete=False), [row()]) == ([], [])          # an unplugged library
    assert change(found(unsure=[FULL]), [row()]) == ([], [])           # its manifest could not be read
    assert change(Scan("unknown"), [row()]) == ([], [])
    assert change(None, [row()]) == ([], [])                           # no scan yet
    assert change(Scan("absent"), [row(), row(7, "other")]) == ([], [FULL, 7])
    assert change(found(FULL, complete=False)) == ([("dice", FULL)], [])    # rows are still added


def test_a_row_leaves_when_the_unread_libraries_do_not_hold_it():
    elsewhere = Scan("found", {}, frozenset(), False, frozenset({7}))    # the unplugged library holds only app 7
    assert change(elsewhere, [row()]) == ([], [FULL])
    holds_it = Scan("found", {}, frozenset(), False, frozenset({FULL}))
    assert change(holds_it, [row()]) == ([], [])


def test_the_row_follows_the_full_game_and_the_demo():
    assert change(found(FULL, DEMO), [row(DEMO)]) == ([("dice", FULL)], [DEMO])
    assert change(found(DEMO), [row(FULL)]) == ([("dice", DEMO)], [FULL])
    assert change(found(DEMO, complete=False), [row(FULL)]) == ([], [])   # the full game may be on the missing drive
    assert change(found(DEMO, unsure=[FULL]), [row(FULL)]) == ([], [])


def test_nothing_changes_while_no_index_is_loaded():
    assert change(found(FULL), entries={}) == ([], [])
    assert change(Scan("absent"), [row()], entries={}) == ([], [])


def test_a_row_the_index_no_longer_lists_leaves():
    other = {"dice": dict(ENTRY, stores=[{"store": "steam", "app_id": 7, "platforms": ["windows"]}])}
    for entries in (other, {"another": ENTRY | {"id": "another"}}, {"dice": dict(ENTRY, stores=[])}):
        assert change(found(FULL, complete=False), [row()], entries)[1] == [FULL]


def test_the_apps_own_copy_keeps_the_row():
    web = Game(title="Dugout Dice", catalog_id="dice", platforms=["Web"], launch_path="https://play.example/")
    for own in (APP_COPY, web):
        assert change(found(FULL), [own]) == ([], [])
        assert change(found(FULL), [own, row()]) == ([], [FULL])
    by_hand = Game(title="Dugout Dice", launch_path="C:/Games/dice.exe")
    assert change(found(FULL), [by_hand]) == ([("dice", FULL)], [])    # a game added by hand is not the entry's


def test_one_scan_at_a_time_and_one_more_after():
    gate = ScanGate()
    assert gate.request() is True
    assert gate.request() is False and gate.request() is False         # asked twice more while it runs
    assert gate.done() is True                                         # one more scan, now running
    assert gate.done() is False
    assert gate.request() is True


def test_a_stale_row_is_replaced_in_the_same_pass():
    other = {"dice": dict(ENTRY, stores=[{"store": "steam", "app_id": 7, "platforms": ["windows"]}])}
    assert change(found(7), [row()], other) == ([("dice", 7)], [FULL])               # the entry's Steam id changed
    no_demo = {"dice": dict(ENTRY, stores=[{"store": "steam", "app_id": FULL, "platforms": ["windows"]}])}
    assert change(found(FULL, DEMO), [row(DEMO)], no_demo) == ([("dice", FULL)], [DEMO])   # the entry dropped its demo
    assert change(found(DEMO), [row(DEMO)], no_demo) == ([], [DEMO])                 # and the full game is not installed


def test_two_steam_rows_for_one_entry_become_one():
    first, second = row(FULL), row(DEMO)
    add, remove = steam_changes(ENTRIES, found(FULL), [first, second], "windows")
    assert add == [] and len(remove) == 1 and remove[0] is second                    # the very object from the list
    add, remove = steam_changes(ENTRIES, found(FULL, DEMO), [row(DEMO), row(DEMO)], "windows")
    assert [(f["catalog_id"], f["steam_app_id"]) for f in add] == [("dice", FULL)] and len(remove) == 2


def test_a_row_whose_entry_is_no_longer_a_game_leaves():
    assert change(found(FULL), [row()], {"dice": dict(ENTRY, kind="extra")}) == ([], [FULL])
