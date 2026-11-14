import os
from types import SimpleNamespace

import pytest

from engine import messages
from library_actions import (EXTRAS, LINK, REPAIR, UNDO, UNINSTALL, UPDATE, catalog_menu_items, delete_kind,
                             is_editable, is_tracked, is_uninstall_unfinished, is_uninstallable, launch_problem,
                             may_start_hta, start_launch_file, executable_problem,
                             library_row_name, remove_library_checkbox_label, remove_library_text, undo_item,
                             uninstall_checkbox_label)
from models import Game

LINKS = [{"url": "https://example.com/forum", "label": "Forum"}, {"url": "https://example.com/", "label": "Homepage"}]
BUY = "https://example.com/buy"


def catalog_game(**kw):
    fields = dict(title="Entombed", launch_path="Entombed/entombed.exe", library_id="lib1", catalog_id="entombed")
    fields.update(kw)
    return Game(**fields)


def record(**kw):
    fields = dict(tracked=True, uninstalled=False, previous=None, previous_unavailable=None)
    fields.update(kw)
    return SimpleNamespace(**fields)


def entry(links=LINKS, buy=BUY):
    e = {"id": "entombed", "title": "Entombed", "links": links}
    if buy:
        e["pricing"] = {"model": "one-time", "url": buy}
    return e


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
        (LINK, "Open Forum", True), (LINK, "Open Homepage", True), (LINK, "Open purchase page", True)]
    assert [i.url for i in items if i.action == LINK] == ["https://example.com/forum", "https://example.com/", BUY]


def test_update_only_when_available_and_extras_disabled_with_none_left():
    items = catalog_menu_items(catalog_game(), record(), entry([], buy=None), update_available=False, extras_to_add=False)
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
                              (LINK, "Open Homepage", True)]  # the homepage link, not the first
    no_update = catalog_menu_items(catalog_game(), untracked, entry([], buy=None), update_available=False, extras_to_add=True)
    assert no_update == []


def test_web_catalog_game_gets_its_links_only():
    web = catalog_game(launch_path="https://example.com/play", library_id="")
    assert summary(catalog_menu_items(web, None, entry(), False, False)) == [
        (LINK, "Open Forum", True), (LINK, "Open Homepage", True), (LINK, "Open purchase page", True)]


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
    assert launch_problem(catalog_game(), False, record(), entry()) == "unreachable"
    assert launch_problem(catalog_game(), True, record(), entry()) == "repair"
    assert launch_problem(catalog_game(), True, record(), None) == "catalog"  # left the catalog: Repair can't work
    assert launch_problem(catalog_game(), True, record(tracked=False), entry()) == "catalog"
    assert launch_problem(catalog_game(), True, None, entry()) == "catalog"
    assert launch_problem(Game(title="Hand", launch_path="C:/hand.exe"), True, None, None) == "locate"
    assert launch_problem(Game(title="Old", launch_path="old.exe", library_id="lib1"), False, None, None) == \
        "unreachable"


def test_uninstall_and_remove_library_text():
    assert uninstall_checkbox_label("Entombed", 3) == \
        "Also delete files Entombed created, like saves and settings (3 files)"
    assert remove_library_text("Games", ["Entombed", "Pawprints"]) == \
        "Games has 2 games installed from the catalog: Entombed and Pawprints. Removing it uninstalls them first."
    assert remove_library_text("Games", ["Entombed"], ["Pawprints", "Lone"]) == (
        "Games has 1 games installed from the catalog: Entombed. Removing it uninstalls them first. "
        "Pawprints and Lone will stay in your list as games added by hand. Their files aren't touched.")
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


def test_an_unfinished_uninstall_can_be_finished():
    """An uninstall that stopped partway (a file was locked) is offered again, from the menu and from Delete; a
    finished one (nothing left of the install) is not."""
    left_files = record(uninstalled=True, files={"entombed.exe": object()}, vendor_uninstaller=None)
    left_vendor = record(uninstalled=True, files={}, vendor_uninstaller='"C:/G/unins000.exe" /SILENT')
    finished = record(uninstalled=True, files={}, vendor_uninstaller=None)
    for unfinished in (left_files, left_vendor):
        assert is_uninstall_unfinished(unfinished) and is_uninstallable(unfinished) and not is_tracked(unfinished)
        assert summary(catalog_menu_items(catalog_game(), unfinished, entry(), True, True)) == [
            (UNINSTALL, "Uninstall", True), (LINK, "Open Forum", True), (LINK, "Open Homepage", True),
            (LINK, "Open purchase page", True)]
        assert summary(catalog_menu_items(catalog_game(), unfinished, None, False, False)) == [
            (UNINSTALL, "Uninstall", True)]
        assert delete_kind(catalog_game(), unfinished) == "uninstall"
        assert launch_problem(catalog_game(), True, unfinished, entry()) == "catalog"  # no Repair
    assert not is_uninstall_unfinished(finished) and not is_uninstallable(finished)
    assert delete_kind(catalog_game(), finished) == "delete"
    assert summary(catalog_menu_items(catalog_game(), finished, entry(), False, False)) == [
        (LINK, "Open Forum", True), (LINK, "Open Homepage", True), (LINK, "Open purchase page", True)]
    runtime = record(kind="runtime", uninstalled=True, files={"x.dll": object()}, vendor_uninstaller=None)
    assert not is_uninstallable(runtime) and delete_kind(catalog_game(), runtime) == "delete"


# ---- .hta: Windows' mshta.exe is given only the file the catalog named as this game's program ----

def _hta_install(tmp_path, **record_fields):
    """A catalog game installed into a library: its game, its record, the libraries and its .hta on disk."""
    folder = tmp_path / "Lib" / "Jump"
    folder.mkdir(parents=True)
    hta = folder / "jumpgame.hta"
    hta.write_text("<html></html>")
    fields = dict(kind="game", library_id="lib1", folder="Jump", launch_path="jumpgame.hta", launch_from_catalog=True)
    fields.update(record_fields)
    game = catalog_game(title="Jump", launch_path="Jump/jumpgame.hta", catalog_id="jump")
    return game, record(**fields), [{"id": "lib1", "path": str(tmp_path / "Lib")}], hta


def test_the_hta_the_catalog_named_may_start(tmp_path):
    game, rec, libraries, hta = _hta_install(tmp_path)
    assert may_start_hta(str(hta), game, rec, libraries)
    # the same file, spelled another way
    assert may_start_hta(str(tmp_path / "Lib" / "Other" / ".." / "Jump" / "jumpgame.hta"), game, rec, libraries)
    if os.name == "nt":
        assert may_start_hta(str(hta).upper(), game, rec, libraries)


def test_an_hta_the_catalog_did_not_name_may_not_start(tmp_path):
    game, rec, libraries, hta = _hta_install(tmp_path)
    other = hta.with_name("other.hta")  # another .hta in the game's own folder
    other.write_text("<html></html>")
    assert not may_start_hta(str(other), game, rec, libraries)
    outside = tmp_path / "Lib" / "names" / "-x.hta"  # games.json saying "../names/-x.hta"
    outside.parent.mkdir()
    outside.write_text("<html></html>")
    assert not may_start_hta(str(tmp_path / "Lib" / "Jump" / ".." / "names" / "-x.hta"), game, rec, libraries)
    elsewhere = tmp_path / "Elsewhere" / "jumpgame.hta"  # an absolute path in games.json
    elsewhere.parent.mkdir()
    elsewhere.write_text("<html></html>")
    assert not may_start_hta(str(elsewhere), game, rec, libraries)


def test_an_hta_may_not_start_when_the_record_does_not_vouch_for_it(tmp_path):
    game, rec, libraries, hta = _hta_install(tmp_path)
    assert not may_start_hta(str(hta), game, None, libraries)  # no record
    assert not may_start_hta(str(hta), game, record(**{**vars(rec), "launch_from_catalog": False}), libraries)
    assert not may_start_hta(str(hta), game, record(**{**vars(rec), "kind": "runtime"}), libraries)
    assert not may_start_hta(str(hta), game, record(**{**vars(rec), "launch_path": "game.exe"}), libraries)
    assert not may_start_hta(str(hta), game, record(**{**vars(rec), "launch_path": ""}), libraries)
    assert not may_start_hta(str(hta), game, rec, [])  # the record's library is not configured
    by_hand = Game(title="Hand", launch_path=str(hta))  # picked in the Locate dialog, say
    assert not may_start_hta(str(hta), by_hand, None, libraries)
    assert not may_start_hta(str(hta), by_hand, rec, libraries)


def test_an_hta_installed_outside_every_library_may_start(tmp_path):
    game, rec, libraries, hta = _hta_install(tmp_path, library_id="", folder=str(tmp_path / "Lib" / "Jump"))
    assert may_start_hta(str(hta), game, rec, libraries)
    assert not may_start_hta(str(hta.with_name("other.hta")), game, rec, libraries)


def test_start_launch_file_lets_only_the_catalogs_hta_run(tmp_path):
    game, rec, libraries, hta = _hta_install(tmp_path)
    other = hta.with_name("other.hta")
    other.write_text("<html></html>")
    calls = []

    def start(path, cwd, hta_allowed):
        calls.append((path, cwd, hta_allowed))
        return "started"

    folder = str(hta.parent)
    assert start_launch_file(str(hta), folder, game, lambda g: rec, libraries, start=start) == "started"
    start_launch_file(str(other), folder, game, lambda g: rec, libraries, start=start)
    start_launch_file(str(hta), folder, game, lambda g: None, libraries, start=start)
    assert calls == [(str(hta), folder, True), (str(other), folder, False), (str(hta), folder, False)]


def test_start_launch_file_reads_no_record_for_a_file_that_is_not_an_hta(tmp_path):
    calls = []
    exe = str(tmp_path / "game.exe")
    start_launch_file(exe, str(tmp_path), catalog_game(), lambda g: pytest.fail("only an .hta needs the record"),
                      [], start=lambda path, cwd, hta_allowed: calls.append((path, cwd, hta_allowed)))
    assert calls == [(exe, str(tmp_path), False)]



def test_a_hand_added_games_file_is_one_add_game_and_locate_both_accept(tmp_path):
    """executable_problem decides: an .exe or .bat file, or an .app folder. Never an .hta."""
    for name in ("game.exe", "PLAY.BAT", "readme.txt", "menu.hta"):
        (tmp_path / name).write_bytes(b"MZ")
    (tmp_path / "Game.app").mkdir()
    for name in ("game.exe", "PLAY.BAT", "Game.app"):
        assert executable_problem(str(tmp_path / name), "Windows") is None
    invalid = (messages.INVALID_EXECUTABLE.format(platform="Windows", types=".exe, .bat"),
               messages.INVALID_EXECUTABLE_TITLE)
    assert executable_problem(str(tmp_path / "readme.txt"), "Windows") == invalid
    assert executable_problem(str(tmp_path / "menu.hta"), "Windows") == invalid
    assert executable_problem(str(tmp_path / "menu.hta"), "macOS")[0].endswith("macOS: .app")
    gone = str(tmp_path / "gone.exe")
    assert executable_problem(gone, "Windows") == (messages.FILE_MISSING.format(path=gone),
                                                   messages.LAUNCH_NOT_FOUND_TITLE)
