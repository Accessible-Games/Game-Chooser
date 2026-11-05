"""What the install confirmation dialog says and decides. No wx here, so it is unit-tested."""

from dataclasses import dataclass
from pathlib import Path

from engine import messages
from engine.folders import library_label

_TITLES = {"update": messages.CONFIRM_TITLE_UPDATE, "extras": messages.CONFIRM_TITLE_EXTRAS}
_BUTTONS = {"update": messages.CONFIRM_BUTTON_UPDATE, "extras": messages.CONFIRM_BUTTON_EXTRAS}


@dataclass
class InstallChoice:
    library_id: str
    library_path: Path
    extras: tuple[str, ...]  # the optional extras chosen; required ones are always installed


def window_title(plan) -> str:
    return _TITLES.get(plan.action, messages.CONFIRM_TITLE_INSTALL).format(title=plan.title)


def ok_label(plan) -> str:
    return _BUTTONS.get(plan.action, messages.CONFIRM_BUTTON_INSTALL)


def library_choice_label(lib: dict) -> str:
    return f"{library_label(lib)} ({lib['path']})"


def library_choice_labels(libraries: list[dict]) -> list[str]:
    return [library_choice_label(lib) for lib in libraries]


def default_library_index(plan) -> int:
    for index, lib in enumerate(plan.libraries):
        if lib["id"] == plan.default_library:
            return index
    return 0


def included_titles(plan, entry_titles: dict[str, str]) -> list[str]:
    """Titles of what always comes with the game: required extras and dependencies that install."""
    titles = [x.title for x in plan.extras if x.required]
    titles += [entry_titles.get(d.id) or d.id for d in plan.dependencies if d.installs]
    return titles


def intro_text(plan, chosen_extras: set[str], entry_titles: dict[str, str]) -> str:
    text = messages.CONFIRM_DOWNLOAD_SIZE.format(size=messages.format_size(plan.download_size(chosen_extras)))
    included = included_titles(plan, entry_titles)
    if included:
        text += ". " + messages.CONFIRM_INCLUDES.format(items=", ".join(included))
    return text


def extra_label(extra) -> str:
    return messages.CONFIRM_EXTRA_REQUIRED.format(title=extra.title) if extra.required else extra.title


def chosen_optional(plan, checked: dict[str, bool]) -> tuple[str, ...]:
    return tuple(x.id for x in plan.extras if not x.required and checked.get(x.id))


def selected_library(plan, index: int) -> dict | None:
    if not plan.libraries:
        return None
    return plan.libraries[index if 0 <= index < len(plan.libraries) else 0]


def build_choice(plan, library: dict, checked: dict[str, bool]) -> InstallChoice:
    return InstallChoice(library["id"], Path(library["path"]), chosen_optional(plan, checked))
