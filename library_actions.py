"""What the Library tab offers for a game: context menu items, Edit, Delete, launch problems and the Uninstall and
Remove-library wording. No wx here, so it is unit-tested; main_window and the dialogs only show what this decides.
"""

from dataclasses import dataclass

from catalog_view import homepage_item, link_items
from engine import messages

# Context menu actions for catalog-installed games, in menu order after "Delete"
UPDATE, UNDO, EXTRAS, REPAIR, UNINSTALL, LINK = "update", "undo", "extras", "repair", "uninstall", "link"


@dataclass
class MenuItem:
    action: str  # one of the names above
    label: str
    enabled: bool = True
    url: str | None = None  # for LINK


def is_web(game) -> bool:
    return game.launch_path.startswith("http")


def is_editable(game) -> bool:
    """Edit is for games added by hand only: the catalog owns a catalog game's details (and, defensively, a game
    that belongs to a library is a catalog game)."""
    return not game.catalog_id and not game.library_id


def is_game_record(record) -> bool:
    """A record of an installed game. A shared runtime's record (kind "runtime") is never a game's: the Library
    never offers to change or forget it."""
    return record is not None and getattr(record, "kind", "game") == "game"


def is_tracked(record) -> bool:
    """A catalog install Game Chooser can change: Update, Undo, Repair and Uninstall work on it."""
    return is_game_record(record) and not record.uninstalled and record.tracked


def is_uninstall_unfinished(record) -> bool:
    """An uninstall that stopped partway (the game was running, say): some files Game Chooser installed are still
    there, or the game's own uninstaller still has to run. Uninstalling again finishes it; nothing else applies."""
    return (is_game_record(record) and record.uninstalled and getattr(record, "tracked", True)
            and bool(getattr(record, "files", None) or getattr(record, "vendor_uninstaller", None)))


def is_uninstallable(record) -> bool:
    """Uninstall (and Delete, as Uninstall) works on it: a tracked install, or one whose uninstall is unfinished."""
    return is_tracked(record) or is_uninstall_unfinished(record)


def undo_item(record) -> MenuItem:
    """Enabled when there is a previous version; says why when it can no longer be downloaded."""
    if record.previous_unavailable:
        reason = record.previous_unavailable.strip().rstrip(".")
        return MenuItem(UNDO, messages.MENU_UNDO_UNAVAILABLE.format(reason=reason), enabled=False)
    return MenuItem(UNDO, messages.MENU_UNDO, enabled=record.previous is not None)


def catalog_menu_items(game, record, entry: dict | None, update_available: bool,
                       extras_to_add: bool) -> list[MenuItem]:
    """The items that follow "Delete" in the Library context menu.

    A game added by hand gets nothing. A catalog web game gets its links. An untracked install gets one disabled
    item saying its update has to be installed by hand (only while an update is available), plus its homepage
    (catalog_view.homepage_item).
    A tracked install gets Update (when one is available), Undo last update, Extras…, Repair, Uninstall and its
    links; while the game is no longer in the catalog, only Uninstall. One whose uninstall stopped partway gets
    Uninstall (to finish it) and its links.
    """
    if not game.catalog_id:
        return []
    links = [MenuItem(LINK, label, url=url) for label, url in link_items(entry)]
    if not is_web(game) and is_uninstall_unfinished(record):
        return [MenuItem(UNINSTALL, messages.MENU_UNINSTALL)] + links
    if is_web(game) or not is_game_record(record) or record.uninstalled:
        return links
    if not record.tracked:
        items = [MenuItem(UPDATE, messages.MENU_UPDATE_BY_HAND, enabled=False)] if update_available else []
        homepage = homepage_item(entry)
        return items + ([MenuItem(LINK, homepage[0], url=homepage[1])] if homepage else [])
    if entry is None:  # left the catalog: it can still be uninstalled, nothing else
        return [MenuItem(UNINSTALL, messages.MENU_UNINSTALL)]
    items = [MenuItem(UPDATE, messages.MENU_UPDATE)] if update_available else []
    items += [undo_item(record),
              MenuItem(EXTRAS, messages.MENU_EXTRAS, enabled=extras_to_add),
              MenuItem(REPAIR, messages.MENU_REPAIR),
              MenuItem(UNINSTALL, messages.MENU_UNINSTALL)]
    return items + links


def delete_kind(game, record) -> str:
    """What Delete does with a game: "uninstall" (a tracked catalog install, or one whose uninstall stopped partway:
    the Uninstall dialog), "forget" (an untracked one: remove it from the list and forget its record, files
    untouched) or "delete" (anything else: the usual confirmation, then it leaves the list)."""
    if not game.catalog_id or is_web(game):
        return "delete"
    if is_uninstallable(record):
        return "uninstall"
    if is_game_record(record) and not record.uninstalled:
        return "forget"
    return "delete"


def launch_problem(game, library_reachable: bool, record, entry: dict | None) -> str:
    """Which missing-executable branch of a launch applies: "unreachable" (the game's library folder can't be
    reached), "repair" (a tracked catalog install still in the catalog: offer Repair), "catalog" (another catalog
    game: say so, no file picker, since the player doesn't choose a catalog game's executable) or "locate" (a game
    added by hand)."""
    if game.library_id and not library_reachable:
        return "unreachable"
    if game.catalog_id:
        return "repair" if is_tracked(record) and entry is not None else "catalog"
    return "locate"


def may_start_hta(game, record) -> bool:
    """May this game's .hta launch file be run (engine.launch.start_game's `hta_allowed`)? Only when the catalog's
    launch path named it: a catalog game whose install record says its launch path came from the catalog. A game
    added by hand never qualifies, whatever file was picked for it."""
    return bool(game.catalog_id) and is_game_record(record) and getattr(record, "launch_from_catalog", False) is True


def uninstall_checkbox_label(title: str, player_files: int) -> str:
    return messages.UNINSTALL_DELETE_FILES.format(title=title, count=player_files)


def remove_library_text(label: str, titles: list[str], kept=()) -> str:
    """What removing a library does: its tracked games are uninstalled; the games it can't uninstall (`kept`)
    stay in the list as games added by hand."""
    text = messages.REMOVE_LIBRARY_TEXT.format(label=label, count=len(titles), titles=messages.names(titles))
    if kept:
        text += " " + messages.REMOVE_LIBRARY_KEPT.format(titles=messages.names(list(kept)))
    return text


def remove_library_checkbox_label(player_files: int) -> str:
    return messages.REMOVE_LIBRARY_DELETE_FILES.format(count=player_files)


def library_row_name(label: str, being_removed: bool) -> str:
    """A library's name in the Preferences list, saying so while it waits for its games' uninstalls."""
    return messages.LIBRARY_BEING_REMOVED_LABEL.format(label=label) if being_removed else label
