"""All player-facing copy for the install engine."""

from dataclasses import dataclass

DEFENDER_NOTE = ("Windows Defender sometimes removes files from older audio games. To stop that, add your game "
                 "library folder to Defender's exclusions: open Windows Security, choose Virus & threat protection, "
                 "choose Manage settings, choose Add or remove exclusions, and add the folder.")
NO_EXE = "{title} was downloaded, but no program to start it was found."
FOLDER_EXISTS = 'A folder named "{folder}" is already in {library}. Game Chooser didn\'t install it, so it won\'t install over it.'
DEVELOPER_CHANGED_VERIFIED = "The developer has published a different file; installed the catalog's verified copy."
DEVELOPER_CHANGED_UNVERIFIED = "The developer has changed this file since the catalog checked it."
REPAIR_FILE_CHANGED = "{title}'s download has changed since it was installed, so Repair can't restore it. Use Update instead."
REPAIR_EXTRA_CHANGED = ("The download for {extra} has changed since it was added to {title}, so Repair can't "
                        "restore it. Use Update instead.")
DOWNLOAD_UNREACHABLE = "{title}'s download couldn't be reached right now. Check your connection and try again later."
UNDO_UNAVAILABLE = "The previous version of {title} is no longer available to download."
UNDO_NO_DETAILS = "Game Chooser doesn't have the details it needs to rebuild the previous version of {title}."
UNDO_NOT_POSSIBLE = "{reason}, so the update can't be undone. {title} is still installed, unchanged, on the current version."
UNDO_BACKUPS_WHERE = "Your copies of the files the update replaced are still in {folders}, in {title}'s folder."
REPAIR_FILE_GONE = "A file {title} was installed from is no longer available to download, so Repair can't restore it."
ELEVATION_DECLINED = "Administrator permission was declined, so {what} wasn't installed."
NO_SOURCE = "{title} couldn't be downloaded. {reasons}"
INSTALLED = "{title} is installed"
FAILED = "{title} couldn't be installed"
UNINSTALLED = "{title} is uninstalled"
UNEXPECTED_ERROR = ("Something went wrong installing {title}. Try again, and if it keeps happening, "
                    "the details are in the install log.")
# File errors that escape a job (jobs.InstallEngine.run gives every OSError one of these).
DISK_FULL = "There isn't enough space on the drive for {title}. Free some space, then try again."
FILE_LOCKED = "A file for {title} couldn't be changed. Close the game if it's running, then try again."
FILE_VANISHED = "A file for {title} disappeared while Game Chooser was working on it. Try again."
FILE_ERROR = "Game Chooser couldn't read or write {path} for {title}. ({detail})"
NEEDS_SHARED_COMPONENT = ("{title} needs {component}, which needs administrator permission. Turn on \"Register shared "
                          "components system-wide\" in Preferences, Installing, to install it.")
# A runtime's DLL and OCX files are copied beside the game's program (engine.runtimes); these say when they weren't.
RUNTIME_FILES_NOT_COPIED = ("Game Chooser didn't copy {component}'s files beside {title}, because it couldn't be sure "
                            "they are the files it installed. If {title} doesn't start, that may be why.")
RUNTIME_FILES_NO_PROGRAM = ("Game Chooser couldn't tell which program starts {title}, so it didn't copy "
                            "{component}'s files beside it. If {title} doesn't start, that may be why.")
RUNTIME_FILES_UNTRACKED = ("Game Chooser couldn't tell which files {title}'s installer added, so it didn't copy "
                           "{component}'s files into its folder. If {title} doesn't start, that may be why.")
RUNTIME_FILES_NEED_ADMIN = ("{title}'s folder {path} can only be changed with administrator permission, so Game "
                            "Chooser didn't copy {component}'s files into it. If {title} doesn't start, that may be "
                            "why.")


# The install confirmation dialog (engine.plan).
PLAN_DEPENDENCY_SHARED = "Also installs: {title} (needs administrator permission)"
PLAN_DEPENDENCY_SHARED_PLAIN = "Also installs: {title}"
PLAN_DEPENDENCY_PRIVATE = "Also installs: {title} (a private copy for this game)"
PLAN_DEPENDENCY_SKIPPED = ("Needs {title}, which isn't installed because shared components are turned off in "
                           "Preferences")
PLAN_PRICE = "Price: {price}."
PLAN_PRICE_LINK = "Price: {price}. {link}: {url}"
PLAN_NO_LONGER_SOLD = "Price: {model}. The full version can no longer be bought."
PLAN_INTERACTIVE = "You'll be guided through this game's own installer."

# Pricing (engine.pricing): the Catalog's Price column and description, and the install confirmation
PRICE_MODELS = {
    "free": "Free",
    "free-to-try": "Free to try",
    "pay-what-you-want": "Pay what you want",
    "one-time": "Buy once",
    "subscription": "Subscription",
    "free-with-purchases": "Free, with optional purchases",
}
PRICE_FREE_TO_TRY_AMOUNT = "Free to try, {price} to unlock"
PRICE_PAY_WHAT_YOU_WANT_FROM = "Pay what you want, from {price}"
PRICE_SUGGESTED = "{text} (suggested {suggested})"
PRICE_ONE_TIME_AMOUNT = "Buy once, {price}"
PRICE_PERIODS = {"month": "Subscription, {price} a month", "year": "Subscription, {price} a year"}
PRICE_NO_LONGER_SOLD = "{model} (the full version can no longer be bought)"
PRICE_AS_OF = "{text}. Price as of {date}"
PRICE_HEADING = "Price: {text}"
PRICE_CAN_PLAY_FREE = "Can be played for free"
# The label of pricing.url, in the description and the install confirmation
PRICE_LINKS = {
    "free-to-try": "Buy or register",
    "pay-what-you-want": "Pay",
    "one-time": "Buy",
    "subscription": "Subscribe",
}
PRICE_LINK_OTHER = "Store page"
DONATE_LINK = "Support the developer"
MENU_OPEN_LINK = "Open {label}"
MENU_OPEN_PURCHASE = "Open purchase page"
MENU_OPEN_DONATE = "Open donation page"


@dataclass
class Progress:
    phase: str
    done: int | None = None
    total: int | None = None
    detail: str = ""


_KB, _MB, _GB = 1024, 1024 ** 2, 1024 ** 3


def format_size(n: int | None) -> str:
    if n is None:
        return "unknown size"
    if n < _MB:
        return f"{round(n / _KB)} kilobytes"
    if n < _GB:
        return f"{n / _MB:.1f} megabytes"
    return f"{n / _GB:.1f} gigabytes"


def _whole_in_unit_of(total: int, done: int) -> tuple[int, int, str]:
    """Return (done, total, unit word) as whole numbers in the unit chosen from `total`."""
    if total < _MB:
        return done // _KB, round(total / _KB), "kilobytes"
    if total < _GB:
        return done // _MB, round(total / _MB), "megabytes"
    return done // _GB, round(total / _GB), "gigabytes"


INSTALLER_WAITING = "Waiting for the installer"
_SIMPLE_PHASES = {
    "verifying": "Verifying",
    "extracting": "Extracting",
    "finishing": "Finishing",
    "waiting_for_installer": INSTALLER_WAITING,
}


def phase_label(p: Progress, title: str) -> str:
    if p.phase == "downloading":
        if p.total:
            done, total, unit = _whole_in_unit_of(p.total, p.done or 0)
            return f"Downloading {title}, {done} of {total} {unit}"
        return f"Downloading {title}"
    if p.phase == "installing_dependency":
        return f"Installing {p.detail}"
    if p.phase == "vendor_uninstaller":
        return f"Running {title}'s own uninstaller"
    return _SIMPLE_PHASES.get(p.phase, p.phase.replace("_", " ").capitalize())


def update_summary(title: str, backups: int) -> str:
    if backups <= 0:
        return f"{title} is updated"
    noun = "file" if backups == 1 else "files"
    return f"Updated {title}. Kept a backup of {backups} {noun} you'd changed."

CATALOG_NEWER_APP = "A newer version of Game Chooser is needed to see the latest catalog."
CATALOG_OFFLINE = "The catalog couldn't be reached. Showing the last saved copy."
CATALOG_OFFLINE_EMPTY = "The catalog couldn't be reached, and no saved copy is available."
CATALOG_BAD_DOWNLOAD = "The catalog download was damaged. Showing the last saved copy."
CATALOG_BAD_DOWNLOAD_EMPTY = "The catalog download was damaged, and no saved copy is available."

SOURCE_UNREACHABLE = "A download address couldn't be reached."
SOURCE_GONE = "A download is no longer available at its address."
SOURCE_CHECKSUM_MISMATCH = "A download didn't match the catalog's checksum."
SOURCE_BLOCKED = "A download address is blocked."

UPDATE_PATH_CONFLICT = ("The update can't be applied because \"{path}\" in your game folder is a {kind}, "
                        "and the update needs the opposite. Nothing was changed.")
RECYCLE_FAILED = "Your files couldn't be moved to the Recycle Bin, so they were left where they are."

EXTRACT_FAILED = "The downloaded files couldn't be unpacked."
EXTRACT_FOLDER_MISSING = "The game's folder wasn't found in the download."
EXTRACT_UNSAFE_LINK = "The download contained a shortcut that points outside the game folder, so it was not unpacked."
EXTRACT_NEEDS_WINDOWS = "This download is a Windows installer, which can only be unpacked on Windows."

STEP_UNKNOWN_ACTION = "This game needs a setup step Game Chooser doesn't know, so it wasn't installed."
STEP_UNSAFE_PATH = "A setup step points outside the game folder, so it wasn't run."
STEP_FILE_MISSING = 'A setup step needs "{path}", which isn\'t in the game folder.'
STEP_RUN_FAILED = "{name} stopped with error code {code} while setting up the game."
STEP_REGISTER_FAILED = "{name} couldn't be registered with Windows."
STEP_PROGRAM_NOT_STARTED = "{name} couldn't be started."
STEP_FILE_FAILED = "A setup step couldn't change the game's files."
STEP_FAILED = "A setup step for this game didn't work."
REGISTRY_UNSAFE_TEXT = "A Windows registry setting for this game contains a character Game Chooser won't write."
REGISTRY_BAD_SETTING = "A Windows registry setting for this game isn't valid."
REGISTRY_WRITE_FAILED = "The Windows registry setting {key} couldn't be saved."
REGISTRY_CANT_RESTORE = ("The Windows registry setting {key} already has a value Game Chooser couldn't put back "
                         "later, so it wasn't changed.")
UNDO_REGISTRY_FAILED = "The Windows registry setting {key} couldn't be put back."
UNDO_UNREGISTER_FAILED = "{name} couldn't be unregistered from Windows."

NOT_IN_CATALOG = "This game is no longer in the catalog."
NOT_INSTALLED = "{title} isn't installed by Game Chooser."
INSTALL_NOT_CHANGEABLE = "Game Chooser can't change {title}, because of how it was installed."
GAME_FOLDER_MISSING = "{title}'s folder {path} wasn't found. If it was moved or deleted, install the game again."
GAME_FOLDER_IS_LIBRARY = ("{title}'s folder {path} is, or holds, a library folder, so Game Chooser won't change "
                          "anything there. Uninstall {title} to take it off the list; no files are touched.")
GAME_FOLDER_IS_LIBRARY_FORGOTTEN = ("{title}'s folder {path} is, or holds, a library folder, so {title} was taken off "
                                    "the list and nothing there was touched.")
GAME_FOLDER_LINK_REMOVED = ("{title}'s folder {path} was a link to {target}. Game Chooser removed the link and left "
                            "everything in {target} as it is, so you can delete it yourself.")
GAME_FOLDER_LINK_BROKEN = ("{title}'s folder {path} was a link to {target}, which no longer exists. Game Chooser "
                           "removed the link.")
GAME_FOLDER_LINK_VENDOR = ("{title}'s folder {path} is a link to another place, and {title}'s own uninstaller is "
                           "reached through it, so Game Chooser left everything as it is. Uninstall {title} from "
                           "Installed apps in Windows Settings, or put its folder back where it was and try again.")
NOTHING_TO_UNDO = "{title} hasn't been updated, so there's nothing to undo."
EXTRAS_NOT_AVAILABLE = "The extras you chose for {title} aren't in the catalog anymore."
EXTRAS_FILE_GONE = ("A file {title} was installed from is no longer available to download, so extras can't be added "
                    "to it now. If an update is offered, update {title} first.")
EXTRAS_FILE_CHANGED = ("A file {title} was installed from has changed since then, so extras can't be added to it now. "
                       "If an update is offered, update {title} first.")
EXTRAS_NEED_UPDATE = "Game Chooser can't add extras to this copy of {title}. Update {title} first, then add them."
REPAIR_NOT_POSSIBLE = ("Game Chooser no longer has the details it needs to rebuild {title}, so Repair can't "
                       "restore it. Use Update instead.")
FILES_NOT_CHANGED = ("Some of {title}'s files couldn't be changed. Close the game if it's running, then try "
                     "again. Any file of yours that was replaced is in the game's backup folder.")
UNINSTALL_FAILED = "Some of {title}'s files couldn't be removed. Close the game if it's running, then try again."
DEPENDENCY_CYCLE = ("The catalog's list of other software {title} needs goes round in a circle, so Game "
                    "Chooser can't install or change {title} right now.")
DEPENDENCY_UNAVAILABLE = ("{title} needs other software that the catalog doesn't offer right now, so Game Chooser "
                          "can't install or change {title}.")
RUNTIME_FOLDER_LINKED = ("{title} wasn't installed, because the folder {path} is a link to another place. Game "
                         "Chooser only installs shared components into a real folder. Remove the link, then try again.")
RUNTIME_FOLDER_NOT_SECURED = ("Game Chooser couldn't protect the folder {path} for {title}, so {title} wasn't "
                              "installed.")
RUNTIME_FOLDER_CHANGED = ("The files in {path} changed while {title} was being installed, so Game Chooser stopped and "
                          "registered nothing.")
RUNTIME_FOLDER_NOT_OURS = ("{title} wasn't installed, because the folder {path} is already there, and Game Chooser "
                           "can't tell that it holds the right files. Game Chooser didn't make that folder, so it "
                           "won't remove it. If no other program uses it, delete it yourself, then try again.")
RUNTIME_FOLDER_NOT_CREATED = ("Game Chooser couldn't create the folder {path} for {title}. Check that the drive has "
                              "free space, then try again.")
TEMP_FOLDER_NOT_CLEARED = ("Game Chooser couldn't empty its temporary folder {path}. Close any program using "
                           "files in it, then try again.")
FOLDER_NOT_REMOVED = ("The game's folder {path} couldn't be fully removed. Game Chooser isn't keeping track of "
                      "it, so you can delete it yourself.")
LIBRARY_FOLDER_NOT_CREATED = ("The library folder {path} couldn't be created. Check that the drive is connected, "
                              "then try again.")
WORK_FOLDER_NOT_CREATED = ("Game Chooser couldn't create its work folder {path}. Check that the drive is "
                           "connected and has free space, then try again.")

WEB_GAME_NOT_INSTALLED = "{title} is played in your web browser, so there's nothing to install."
EXTRA_NOT_SUPPORTED = "{extra} has its own installer, so Game Chooser can't add it."
INSTALLER_FAILED = "{title}'s installer stopped with error code {code}, so {title} wasn't installed."
INSTALLER_CODE_NOTE = "{title}'s installer finished with error code {code}. If the game doesn't start, install it again."
INSTALLER_RESTART_NOTE = "Restart Windows to finish installing {title}."
INSTALL_FOLDER_NOT_FOUND = ("The installer finished, but Game Chooser couldn't find where {title} was installed. "
                            "You can add it yourself with Add Game.")
UNTRACKED_NOTE = ("Game Chooser couldn't tell which files {title}'s installer added, so it can't update or "
                  "uninstall {title} for you.")
FILES_NOT_SET_ASIDE = "Your files for {title} couldn't be copied to safety, so its installer wasn't run."
SETASIDE_LEFT_OVER = ("Copies of your files for {title} from an earlier try are still in {path}. Move them "
                      "somewhere safe, then try again.")
SETASIDE_KEPT = "Some of your files for {title} couldn't be put back. Copies of them are in {path}."
SETASIDE_KEPT_TO_BE_SAFE = ("Your files for {title} are back in place. Something its installer started may still "
                            "be running, so copies of them were also kept in {path}. Move or delete them before "
                            "you update or uninstall {title} again.")
INSTALLER_CANCELLED = "You cancelled, so Game Chooser stopped {title}'s installer."
UNINSTALLER_CANCELLED = "You cancelled, so Game Chooser stopped {title}'s own uninstaller."
VENDOR_UNINSTALLER_MISSING = ("{title}'s own uninstaller wasn't found, so Game Chooser removed only the files it "
                              "knows {title} installed.")
UNINSTALLER_FAILED = ("{title}'s own uninstaller stopped with error code {code}, so some of the settings it added "
                      "to Windows may be left behind.")
VENDOR_UNINSTALLER_RAN = ("Game Chooser also ran {title}'s own uninstaller, to remove the settings it added to "
                          "Windows.")
SETASIDE_NOT_PUT_BACK = ("{title}'s own uninstaller may still be running, so your files weren't put back into its "
                         "folder. Copies of them are in {path}.")
INSTALLER_STILL_RUNNING = ("{title}'s installer is still running, so Game Chooser left everything as it is. Close "
                           "the installer, then try again.")
STEP_TOUCHES_OTHER_FILES = ("A setup step for {title} would change files its installer didn't add, so Game Chooser "
                            "stopped.")
FILE_STEPS_SKIPPED = ("Some setup steps for {title} weren't run, because Game Chooser couldn't tell which files are "
                      "the game's.")
FOLDER_NEEDS_ADMIN = ("{title}'s folder {path} can only be changed with administrator permission, so Game "
                      "Chooser left it as it is. To let Game Chooser change it, give your Windows account permission "
                      "to change that folder. Otherwise, make the change by hand.")
EXTRAS_SKIPPED_UNTRACKED = ("Game Chooser couldn't tell which files {title}'s installer added, so it couldn't add "
                            "{extras}.")
EXTRAS_SKIPPED_NEEDS_ADMIN = ("{title}'s folder {path} can only be changed with administrator permission, so Game "
                              "Chooser couldn't add {extras}.")
EXTRAS_REQUIRED_MISSING = "{title} may not work properly without {extras}."
EXTRAS_FILES_NOT_COPIED = ("{title}'s files couldn't be copied to add its extras. Close the game if it's running and "
                           "check that the drive has free space, then try again.")
UNDO_FILES_NOT_REMOVED = ("Some files from the newer version of {title} couldn't be removed, so they were left in its "
                          "folder. Uninstalling {title} removes them.")
UNDO_BACKUPS_KEPT = ("Your copies of {names} weren't put back, because the files they would replace have changed "
                     "since the update or couldn't be replaced. They are still in {title}'s backup folder.")
NEW_FILES_SET_ASIDE = ("Files that appeared in {title}'s folder while its installer ran were moved to {path}. Look "
                       "through them, and delete that folder once you don't need them.")


def names(items: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]

LIBRARY_ALREADY_ADDED = "That folder is already a library."
LIBRARY_INSIDE = "That folder is inside the library {label}."
LIBRARY_CONTAINS = "That folder contains the library {label}."
LIBRARY_IN_GAME_FOLDER = "That folder is, or is inside, {path}, the folder of a game Game Chooser installed."
LIBRARY_FOLDER_MISSING = "That folder doesn't exist."
LIBRARY_UNREACHABLE = ("The library folder {path} can't be reached. If it's on a drive that isn't connected, "
                       "connect it and try again. If it moved, use Change folder in Preferences.")
CATALOG_NOT_EDITABLE = "{title} comes from the catalog, so its details can't be edited."
LIBRARY_REMOVE_UNREACHABLE = ("That library's folder can't be reached, so its games can't be uninstalled. Connect the "
                              "drive, or use Change folder if it moved.")
LIBRARY_REMOVE_BUSY = "{title} is still installing or updating. Wait for it to finish, then try again."
LIBRARY_BEING_REMOVED = "That library is being removed."

CATALOG_CHECKING = "Checking for catalog updates"
CATALOG_LOAD_FAILED = "Couldn't load the catalog. Check your internet connection, then choose Retry."
CATALOG_RANDOM_PROMPT = "Your game for today is...\n\n{title}\n\n{verb} now?"
CATALOG_NO_GAMES = "No games available to choose from."
CATALOG_NO_GAMES_TO_INSTALL = "No games to install in the list as shown."
CATALOG_NOT_IN_LIBRARY = "{title} is installed, but Game Chooser can't find it in your Library."

# The Catalog tab's list, filter tree and description (catalog_view); the Library description is the same text
CATALOG_ROW_UNSUPPORTED = "no longer supported"
CATALOG_ROW_INSTALLED = "installed"
CATALOG_TREE_UNSUPPORTED = "No longer supported"
CATALOG_GENRE = "Genre: {items}"
CATALOG_GENRES = "Genres: {items}"
CATALOG_TAGS = "Tags: {items}"
CATALOG_LANGUAGES = "Languages: {items}"
CATALOG_CONTAINS = "Games included: {items}"
CATALOG_UNSUPPORTED = "No longer supported by its developer."

ADDED = "{title} is added"

# The install confirmation dialog
CONFIRM_TITLE_INSTALL = "Install {title}"
CONFIRM_TITLE_UPDATE = "Update {title}"
CONFIRM_TITLE_EXTRAS = "Add extras to {title}"
CONFIRM_DOWNLOAD_SIZE = "Download size: {size}"
CONFIRM_DOWNLOAD_SIZE_FIRST_RUN = ("Download size: {size}, including {first_run} that the game downloads the first "
                                   "time you start it")
CONFIRM_DOWNLOAD_SIZE_PLUS_FIRST_RUN = ("Download size: unknown size, plus {first_run} that the game downloads the "
                                        "first time you start it")
CONFIRM_INCLUDES = "Includes: {items}"
CONFIRM_WHERE = "Where to install"
CONFIRM_INSTALLS_INTO = "Installs into {library}"
CONFIRM_FOLDER_LABEL = "Install folder:"
CONFIRM_FOLDER_NAME = "Install folder"
CONFIRM_BROWSE = "Browse..."
CONFIRM_BROWSE_PROMPT = "Choose the folder to install games into"
CONFIRM_EXTRAS_BOX = "Extras"
CONFIRM_EXTRA_REQUIRED = "{title} (required, always installed)"
CONFIRM_BUTTON_INSTALL = "Install"
CONFIRM_BUTTON_UPDATE = "Update"
CONFIRM_BUTTON_EXTRAS = "Add extras"
CONFIRM_BUTTON_CANCEL = "Cancel"

# Background installs: the progress area, notifications, the installer prompts and the close guard
REPAIRED = "{title} is repaired"
UNDONE = "{title} is restored to the previous version"
EXTRAS_INSTALLED = "{title}'s extras are installed"
INSTALL_PROBLEM_TITLE = "Install problem"
PROGRESS_GAUGE_NAME = "Install progress"
PROGRESS_STATUS_NAME = "Install status"
PROGRESS_DONE = "Done"
JOB_PREPARING = "Preparing {title}"
JOB_CANCELLING = "Cancelling {title}"
ONE_MORE_WAITING = "1 more waiting"
MORE_WAITING = "{count} more waiting"
INSTALLER_TARGET_TITLE = "Installing {title}"
INSTALLER_TARGET = "When the installer asks where to install, use: {path}\n\nThe path is on your clipboard."
INSTALLER_TARGET_NO_CLIPBOARD = "When the installer asks where to install, use: {path}"
LOCATE_FOLDER = "Locate where {title} was installed"
QUIT_WHILE_BUSY = "{title} is still installing. Cancel it and quit?"
QUIT_WHILE_UPDATING = "{title} is still being updated. Cancel it and quit?"
QUIT_WHILE_UNINSTALLING = "{title} is still being uninstalled. Cancel it and quit?"
QUIT_WHILE_REPAIRING = "{title} is still being repaired. Cancel it and quit?"
QUIT_WHILE_UNDOING = "{title} is still being restored to the previous version. Cancel it and quit?"
QUIT_WHILE_ADDING_EXTRAS = "{title}'s extras are still installing. Cancel them and quit?"
QUIT_WHILE_BUSY_TITLE = "Quit Game Chooser"
JOB_ALREADY_RUNNING = "{title} is already installing. Its progress is shown below the games list."
JOB_ALREADY_WAITING = "{title} is already waiting to install. It starts when the install before it finishes."
INSTALL_NOT_STARTED = "Game Chooser couldn't start installing {title}. Try again."
# The failure notification, by action (FAILED is the install one)
FAILED_UPDATE = "{title} couldn't be updated"
FAILED_UNINSTALL = "{title} couldn't be uninstalled"
FAILED_REPAIR = "{title} couldn't be repaired"
FAILED_UNDO = "{title} couldn't be restored to the previous version"
FAILED_EXTRAS = "{title}'s extras couldn't be installed"


def with_waiting(label: str, waiting: int) -> str:
    """The progress status: the phase, then how many jobs wait behind it ("..., 1 more waiting")."""
    if waiting <= 0:
        return label
    return f"{label}, " + (ONE_MORE_WAITING if waiting == 1 else MORE_WAITING.format(count=waiting))


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def finished_text(action: str, title: str, notes=(), backups: int = 0) -> str:
    """The notification for a finished job: what happened, then each note as a sentence of its own."""
    if action == "update":
        text = update_summary(title, backups)
    else:
        text = {"uninstall": UNINSTALLED, "repair": REPAIRED, "undo": UNDONE,
                "extras": EXTRAS_INSTALLED}.get(action, INSTALLED).format(title=title)
    notes = [n for n in notes if n and n.strip()]
    if not notes:
        return text
    return " ".join([_sentence(text), *(_sentence(n) for n in notes)])


def quit_while_busy_text(action: str, title: str) -> str:
    """The question when the player quits while a job runs, by the job's action (QUIT_WHILE_BUSY for an install)."""
    return {"update": QUIT_WHILE_UPDATING, "uninstall": QUIT_WHILE_UNINSTALLING, "repair": QUIT_WHILE_REPAIRING,
            "undo": QUIT_WHILE_UNDOING, "extras": QUIT_WHILE_ADDING_EXTRAS}.get(action, QUIT_WHILE_BUSY).format(
        title=title)


def failed_text(action: str, title: str) -> str:
    """The notification for a failed job, by its action."""
    return {"update": FAILED_UPDATE, "uninstall": FAILED_UNINSTALL, "repair": FAILED_REPAIR, "undo": FAILED_UNDO,
            "extras": FAILED_EXTRAS}.get(action, FAILED).format(title=title)

# The Library tab's actions for catalog games: the context menu, Uninstall, removing a library, launch problems
MENU_UNDO = "Undo last update"
MENU_UNDO_UNAVAILABLE = "Undo last update (not available: {reason})"
MENU_EXTRAS = "Extras…"
MENU_REPAIR = "Repair"
MENU_UPDATE_BY_HAND = "Update available: install it by hand"
UNINSTALL_TITLE = "Uninstall {title}"
UNINSTALL_DELETE_FILES = "Also delete files {title} created, like saves and settings ({count} files)"
UNINSTALL_BUTTON = "Uninstall"
UNINSTALL_INTRO = "Uninstall {title}? The files Game Chooser installed are removed."
REMOVE_LIBRARY_TITLE = "Remove {label}"
REMOVE_LIBRARY_TEXT = "{label} has {count} games installed from the catalog: {titles}. Removing it uninstalls them first."
REMOVE_LIBRARY_DELETE_FILES = "Also delete files these games created, like saves and settings ({count} files)"
REMOVE_LIBRARY_BUTTON = "Uninstall and remove"
LIBRARY_BEING_REMOVED_LABEL = "{label} (being removed)"
REMOVE_LIBRARY_KEPT = "{titles} will stay in your list as games added by hand. Their files aren't touched."
LIBRARY_NOT_REMOVED = ("Game Chooser couldn't finish removing that library, because one of its install records "
                       "couldn't be deleted. Try again.")
LIBRARY_ROW_GONE = ("{title}'s library is no longer in Preferences, so Game Chooser can't find it. Delete it and add "
                    "it again with Add Game.")
FORGET_FAILED = "Game Chooser couldn't remove {title} from the list. Try again."
COUNTING_FILES = "Checking {title}'s files"
COUNTING_LIBRARY_FILES = "Checking the files of the games in {label}"
FORGET_GAME_CONFIRM = ("Remove {title} from the list? Game Chooser didn't keep track of its files, so they stay "
                       "where they are.")
DELETE_GAME_CONFIRM = "Delete '{title}' from library?"
DELETE_GAME_TITLE = "Confirm Delete"
LAUNCH_NOT_FOUND_TITLE = "File Not Found"
FILE_MISSING = "File does not exist: {path}"
INVALID_EXECUTABLE = "File is not a valid executable.\n\nSupported types for {platform}: {types}"
INVALID_EXECUTABLE_TITLE = "Invalid Executable"
LAUNCH_NOT_FOUND_LOCATE = "Game executable not found. Please locate the game.\n\n" + DEFENDER_NOTE
LAUNCH_NOT_FOUND_REPAIR = "Game executable not found.\n\n" + DEFENDER_NOTE + "\n\nRepair {title} now?"
LAUNCH_NOT_FOUND_CATALOG = "Game executable not found.\n\n" + DEFENDER_NOTE
# An .hta game that can't be started (engine.launch.start_hta); shown after "Failed to launch game: "
HTA_NEEDS_WINDOWS = "This game only runs on Windows."
HTA_HOST_MISSING = ("This game needs mshta.exe, a part of Windows that runs this kind of game, and it wasn't found "
                    "on this computer.")
# An .hta that is not the file the catalog names for the game (engine.launch.start_game): nothing starts it
HTA_NOT_ALLOWED = ("This is a .hta file, and Game Chooser only starts one when it is the file the catalog names as "
                   "the game's program.")
EDIT_GAME_TITLE = "Edit Game"
LIBRARY_NOT_FOUND_TITLE = "Library Not Found"
GAME_BUSY = "Game Chooser is still working on {title}. Wait for it to finish, then try again."
ACTION_NOT_STARTED = "Game Chooser couldn't start that for {title}. Try again."
GAMES_NOT_SAVED = ("Game Chooser couldn't save your games list ({path}). Your change is kept for now; Game Chooser "
                   "tries to save it again with your next change and when it closes.")

# games.json or config.json couldn't be read at startup (told once, after the window opens)
FILE_DAMAGED_TITLE = "Game Chooser"
GAMES_FILE_DAMAGED = ("Game Chooser couldn't read your games list, so it started with an empty one. A copy of the "
                      "damaged list was kept here: {path}")
GAMES_FILE_DAMAGED_NOT_COPIED = ("Game Chooser couldn't read your games list, so it started with an empty one. It "
                                 "won't save the list until it can keep a copy of the damaged one: {path}")
CONFIG_FILE_DAMAGED = ("Game Chooser couldn't read its settings, so it started with the default ones. Games "
                       "installed into your libraries show \"Library not found\" until settings that list those "
                       "libraries are put back. The damaged file was kept as it was, in case it can be repaired or "
                       "read by hand: {path}")
CONFIG_FILE_DAMAGED_NOT_COPIED = ("Game Chooser couldn't read its settings, so it started with the default ones. "
                                  "It won't save settings until it can keep a copy of the damaged ones: {path}")

# Preferences, Installing tab
PREFS_INSTALLING_TAB = "Installing"
PREFS_REGISTER_SHARED = "Register shared components system-wide (needs administrator permission)"
PREFS_CATALOG_ADDRESS = "Catalog address:"
PREFS_BAD_CATALOG_ADDRESS = "That catalog address isn't valid. Use an https:// address."
PREFS_BAD_ADDRESS_TITLE = "Catalog address"
