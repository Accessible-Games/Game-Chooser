from types import SimpleNamespace

from engine import messages
from library_actions import (EXTRAS, LINK, REPAIR, UNDO, UNINSTALL, UPDATE, catalog_menu_items, delete_kind,
                             is_editable, launch_problem, library_row_name, remove_library_checkbox_label,
                             remove_library_text, undo_item, uninstall_checkbox_label)
from models import Game

LINKS = {"homepage": "https://example.com/", "purchase": "https://example.com/buy"}


def catalog_game(**kw):
    fields = dict(title="Entombed", launch_path="Entombed/entombed.exe", library_id="lib1", catalog_id="entombed")
    fields.update(kw)
    return Game(**fields)


def record(**kw):
    fields = dict(tracked=True, uninstalled=False, previous=None, previous_unavailable=None)
    fields.update(kw)
    return SimpleNamespace(**fields)


def entry(links=LINKS):
    return {"id": "entombed", "title": "Entombed", "links": links}


def summary(items):
    return [(i.action, i.label, i.enabled) for i in items]


def test_hand_added_game_gets_nothing_new():
    hand = Game(title="Hand", launch_path="C:/Games/hand.exe")
    assert catalog_menu_items(hand, None, None, update_available=False, extras_to_add=False) == []


def test_tracked_game_items_in_order():
    items = catalog_menu_items(catalog_game(), record(previous={"source": {}}), entry(), update_available=True,
                               extras_to_add=True)
    assert summary(items) == [
        (UPDATE, "Update", True), (UNDO, "Undo last update", True), (EXTRAS, "Extras…", True),
        (REPAIR, "Repair", True), (UNINSTALL, "Uninstall", True),
        (LINK, "Open homepage", True), (LINK, "Open purchase page", True)]
    assert [i.url for i in items if i.action == LINK] == [LINKS["homepage"], LINKS["purchase"]]


def test_update_only_when_available_and_extras_disabled_with_none_left():
    items = catalog_menu_items(catalog_game(), record(), entry({}), update_available=False, extras_to_add=False)
    assert summary(items) == [(UNDO, "Undo last update", False), (EXTRAS, "Extras…", False),
                              (REPAIR, "Repair", True), (UNINSTALL, "Uninstall", True)]


def test_undo_says_why_it_is_unavailable():
    reason = messages.UNDO_UNAVAILABLE.format(title="Entombed")
    item = undo_item(record(previous={"source": {}}, previous_unavailable=reason))
    assert not item.enabled
    assert item.label == ("Undo last update (not available: The previous version of Entombed is no longer "
                          "available to download)")
    assert undo_item(record(previous={"source": {}})).enabled and not undo_item(record()).enabled


def test_untracked_game_shows_update_by_hand_and_homepage_only():
    untracked = record(tracked=False, previous={"source": {}})
    items = catalog_menu_items(catalog_game(), untracked, entry(), update_available=True, extras_to_add=True)
    assert summary(items) == [(UPDATE, "Update available: install it by hand", False),
                              (LINK, "Open homepage", True)]
    no_update = catalog_menu_items(catalog_game(), untracked, entry({}), update_available=False, extras_to_add=True)
    assert no_update == []


def test_web_catalog_game_gets_its_links_only():
    web = catalog_game(launch_path="https://example.com/play", library_id="")
    assert summary(catalog_menu_items(web, None, entry(), False, False)) == [
        (LINK, "Open homepage", True), (LINK, "Open purchase page", True)]


def test_game_that_left_the_catalog_can_only_be_uninstalled():
    assert summary(catalog_menu_items(catalog_game(), record(), None, False, False)) == [
        (UNINSTALL, "Uninstall", True)]


def test_edit_is_for_games_added_by_hand_only():
    assert is_editable(Game(title="Hand", launch_path="C:/hand.exe"))
    assert not is_editable(catalog_game())
    assert not is_editable(catalog_game(launch_path="https://example.com/", library_id=""))
    assert not is_editable(Game(title="Odd", launch_path="odd.exe", library_id="lib1"))


def test_delete_kind():
    assert delete_kind(catalog_game(), record()) == "uninstall"
    assert delete_kind(catalog_game(), record(tracked=False)) == "forget"
    assert delete_kind(catalog_game(), None) == "delete"  # no record (or an unreadable one)
    assert delete_kind(catalog_game(), record(uninstalled=True)) == "delete"
    assert delete_kind(catalog_game(launch_path="https://example.com/", library_id=""), None) == "delete"
    assert delete_kind(Game(title="Hand", launch_path="C:/hand.exe"), None) == "delete"


def test_launch_problem_branches():
    assert launch_problem(catalog_game(), False, record()) == "unreachable"
    assert launch_problem(catalog_game(), True, record()) == "repair"
    assert launch_problem(catalog_game(), True, record(tracked=False)) == "catalog"
    assert launch_problem(catalog_game(), True, None) == "catalog"
    assert launch_problem(Game(title="Hand", launch_path="C:/hand.exe"), True, None) == "locate"
    assert launch_problem(Game(title="Old", launch_path="old.exe", library_id="lib1"), False, None) == "unreachable"


def test_uninstall_and_remove_library_text():
    assert uninstall_checkbox_label("Entombed", 3) == \
        "Also delete files Entombed created, like saves and settings (3 files)"
    assert remove_library_text("Games", ["Entombed", "Pawprints"]) == \
        "Games has 2 games installed from the catalog: Entombed and Pawprints. Removing it uninstalls them first."
    assert remove_library_checkbox_label(5) == \
        "Also delete files these games created, like saves and settings (5 files)"
    assert messages.UNINSTALL_TITLE.format(title="Entombed") == "Uninstall Entombed"
    assert messages.REMOVE_LIBRARY_TITLE.format(label="Games") == "Remove Games"
    assert (messages.UNINSTALL_BUTTON, messages.REMOVE_LIBRARY_BUTTON) == ("Uninstall", "Uninstall and remove")


def test_launch_problem_copy():
    assert messages.LAUNCH_NOT_FOUND_REPAIR.format(title="Entombed") == (
        "Game executable not found.\n\n" + messages.DEFENDER_NOTE + "\n\nRepair Entombed now?")
    assert messages.LAUNCH_NOT_FOUND_LOCATE == (
        "Game executable not found. Please locate the game.\n\n" + messages.DEFENDER_NOTE)


def test_library_row_name():
    assert library_row_name("Games", False) == "Games"
    assert library_row_name("Games", True) == "Games (being removed)"
