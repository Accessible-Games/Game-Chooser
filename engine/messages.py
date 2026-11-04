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
UNDO_UNAVAILABLE = "The previous version of {title} is no longer available to download."
ELEVATION_DECLINED = "Administrator permission was declined, so {what} wasn't installed."
NO_SOURCE = "{title} couldn't be downloaded. {reasons}"
INSTALLED = "{title} is installed"
FAILED = "{title} couldn't be installed"
UNINSTALLED = "{title} is uninstalled"
NEEDS_SHARED_COMPONENT = ("{title} needs {component}, which needs administrator permission. Turn on \"Register shared "
                          "components system-wide\" in Preferences, Installing, to install it.")


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


_SIMPLE_PHASES = {
    "verifying": "Verifying",
    "extracting": "Extracting",
    "finishing": "Finishing",
    "waiting_for_installer": "Waiting for the installer",
}


def phase_label(p: Progress, title: str) -> str:
    if p.phase == "downloading":
        if p.total:
            done, total, unit = _whole_in_unit_of(p.total, p.done or 0)
            return f"Downloading {title}, {done} of {total} {unit}"
        return f"Downloading {title}"
    if p.phase == "installing_dependency":
        return f"Installing {p.detail}"
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
SOURCE_CHECKSUM_MISMATCH = "A download didn't match the catalog's checksum."
SOURCE_BLOCKED = "A download address is blocked."

UPDATE_PATH_CONFLICT = ("The update can't be applied because \"{path}\" in your game folder is a {kind}, "
                        "and the update needs the opposite. Nothing was changed.")
RECYCLE_FAILED = "Your files couldn't be moved to the Recycle Bin, so they were left where they are."

EXTRACT_FAILED = "The downloaded files couldn't be unpacked."
EXTRACT_FOLDER_MISSING = "The game's folder wasn't found in the download."
EXTRACT_UNSAFE_LINK = "The download contained a shortcut that points outside the game folder, so it was not unpacked."
