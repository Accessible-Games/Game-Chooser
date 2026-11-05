"""What the install confirmation dialog shows: library choice, extras, dependencies, size and warnings.

Pure data. The UI asks `blocked_reason` for each library the player picks, at the time of choosing.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import messages
from .entries import extras_of, platform_sources
from .errors import InstallFailed
from .run_installer import silent_args_for
from .runtimes import _has_elevated_steps, _is_installer_runtime, _runs_installer, is_com_runtime, missing_runtimes

MANUAL_LIBRARY = "manual"
_DEMO_FLAGS = ("demo", "requires-registration")


@dataclass
class ExtraChoice:
    id: str
    title: str
    required: bool
    size: int | None


@dataclass
class DependencyLine:
    id: str
    text: str
    installs: bool
    size: int | None


@dataclass
class InstallPlan:
    catalog_id: str
    title: str
    action: str
    libraries: list[dict] = field(default_factory=list)
    default_library: str | None = None
    suggested_folder: Path | None = None
    extras: list[ExtraChoice] = field(default_factory=list)
    dependencies: list[DependencyLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    interactive: bool = False
    is_web: bool = False
    base_size: int | None = None

    def download_size(self, chosen_extras: set[str]) -> int | None:
        """The game, its required and chosen extras, and the dependencies that install; None if any is unknown."""
        sizes = [self.base_size]
        sizes += [x.size for x in self.extras if x.required or x.id in chosen_extras]
        sizes += [d.size for d in self.dependencies if d.installs]
        return None if any(s is None for s in sizes) else sum(sizes)


def _first_source(entry: dict, platform: str | None) -> dict | None:
    sources = platform_sources(entry, platform) if platform else entry.get("sources") or []
    return sources[0] if sources else None


def _size(entry: dict, platform: str | None) -> int | None:
    source = _first_source(entry, platform)
    return None if source is None else source.get("size")


def _title(entry: dict) -> str:
    return entry.get("title") or entry["id"]


def _installer_only(runtime: dict, platform: str | None) -> bool:
    if platform:
        return _is_installer_runtime(runtime, platform)
    sources = runtime.get("sources") or []
    return bool(sources) and all(_runs_installer(s) for s in sources)


def _needs_administrator(runtime: dict, platform: str | None) -> bool:
    """True if installing the runtime machine-wide brings an administrator prompt: a register or HKLM step,
    an installer that must run (its own prompt), or an installer flagged for elevation."""
    flagged = any(bool(((s.get("install") or {}).get("installer") or {}).get("elevation"))
                  for s in runtime.get("sources") or [] if isinstance(s, dict))
    return _has_elevated_steps(runtime) or _installer_only(runtime, platform) or flagged


def dependency_line(runtime: dict, register_shared_components: bool, platform: str | None = None) -> DependencyLine:
    """How one missing runtime is described, following what the install will do about it (plan_runtimes).

    Without a `platform`, every source of the runtime is considered. A shared install is said to need
    administrator permission only when it will ask for it.
    """
    title = _title(runtime)
    size = _size(runtime, platform)
    if not register_shared_components:
        if _installer_only(runtime, platform):
            return DependencyLine(runtime["id"], messages.PLAN_DEPENDENCY_SKIPPED.format(title=title), False, size)
        if is_com_runtime(runtime):
            return DependencyLine(runtime["id"], messages.PLAN_DEPENDENCY_PRIVATE.format(title=title), True, size)
    text = messages.PLAN_DEPENDENCY_SHARED if _needs_administrator(runtime, platform) else messages.PLAN_DEPENDENCY_SHARED_PLAIN
    return DependencyLine(runtime["id"], text.format(title=title), True, size)


def _is_interactive(entry: dict, platform: str) -> bool:
    source = _first_source(entry, platform)
    install = (source or {}).get("install") or {}
    if install.get("method") != "installer":
        return False
    installer = install.get("installer") or {}
    return installer.get("mode") == "run" and silent_args_for(installer) is None


def _is_web(entry: dict, platform: str) -> bool:
    sources = platform_sources(entry, platform)
    return bool(sources) and all((s.get("install") or {}).get("method") == "url" for s in sources)


def _suggested_folder() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Games"


def _extra_choices(entry: dict, entries: dict, platform: str, installed: set[str],
                   optional_only: bool) -> list[ExtraChoice]:
    choices = []
    for info in extras_of(entry, entries, platform):
        if info.entry["id"] in installed or (optional_only and info.required):
            continue
        choices.append(ExtraChoice(info.entry["id"], _title(info.entry), info.required,
                                   _size(info.entry, platform)))
    return choices


def _choose_library(plan: InstallPlan, libraries: list[dict], last_library: str | None) -> None:
    plan.libraries = [lib for lib in libraries if lib.get("name") != MANUAL_LIBRARY]
    if not plan.libraries:
        plan.suggested_folder = _suggested_folder()
        return
    names = [lib.get("name") for lib in plan.libraries]
    plan.default_library = last_library if last_library in names else names[0]


def _warnings(entry: dict, action: str, interactive: bool) -> list[str]:
    warnings = []
    if action == "install" and any(flag in (entry.get("flags") or []) for flag in _DEMO_FLAGS):
        url = (entry.get("links") or {}).get("purchase")
        warnings.append(messages.PLAN_DEMO_BUY.format(url=url) if url else messages.PLAN_DEMO)
    if interactive:
        warnings.append(messages.PLAN_INTERACTIVE)
    return warnings


def build_install_plan(entry_id: str, entries: dict, libraries: list[dict], records, registry,
                       register_shared_components: bool, last_library: str | None, platform: str,
                       action: str = "install", runtimes_root: Path | None = None,
                       security=None) -> InstallPlan:
    """The plan for installing, updating or adding extras to one catalog game.

    "update" and "extras" act on the game's existing folder, so no library is offered. A dependency
    problem (a cycle, or something the catalog no longer offers) is a warning, not an exception, so the
    dialog can say why the install will fail. `runtimes_root` and `security` let a machine-wide marker
    count as "present", as the install itself does.
    """
    entry = entries[entry_id]
    plan = InstallPlan(entry_id, _title(entry), action, is_web=_is_web(entry, platform))
    record = records.load(entry_id)
    installed = set(record.extras) if record is not None and not record.uninstalled else set()
    if action == "extras":
        plan.base_size = 0  # the game itself isn't downloaded
        plan.extras = _extra_choices(entry, entries, platform, installed, optional_only=True)
        return plan
    plan.base_size = _size(entry, platform)
    if plan.is_web:
        return plan
    if action == "install":
        _choose_library(plan, libraries, last_library)
    plan.extras = _extra_choices(entry, entries, platform, installed, optional_only=False)
    try:
        missing = missing_runtimes(entry, entries, records, registry, runtimes_root, security)
    except InstallFailed as problem:
        plan.warnings.append(problem.message)
    else:
        plan.dependencies = [dependency_line(r, register_shared_components, platform) for r in missing]
    plan.interactive = _is_interactive(entry, platform)
    plan.warnings += _warnings(entry, action, plan.interactive)
    return plan
