"""The catalog's post-install steps, run inside the game folder and nowhere else.

File steps (copy, move, delete) change the staged files before they are put in place. System
steps (registry, register, run) run afterwards in the final folder; registry and register steps
are returned with their undo data so uninstall can reverse them. An action this code doesn't
know is a hard error, never skipped.
"""

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath

from . import messages
from .elevation import Elevator
from .errors import ElevationDeclined, StepError, UnsafePath
from .registry import RegistryAccess
from .safety import _resolve_game_path

log = logging.getLogger(__name__)

FILE_ACTIONS = {"copy", "move", "delete"}
SYSTEM_ACTIONS = {"registry", "register", "run"}


@dataclass
class StepContext:
    dir: Path
    elevator: Elevator
    registry: RegistryAccess


def _action(step: object) -> str:
    action = step.get("action") if isinstance(step, dict) else None
    if action not in FILE_ACTIONS and action not in SYSTEM_ACTIONS:
        raise StepError(messages.STEP_UNKNOWN_ACTION)
    return action


def _path(root: Path, rel: object) -> Path:
    """`rel` inside `root`; refuses anything outside it, `root` itself, and paths through a link."""
    if not isinstance(rel, str):
        raise StepError(messages.STEP_UNSAFE_PATH)
    try:
        path, through_link = _resolve_game_path(Path(root), rel)
    except UnsafePath as e:
        raise StepError(messages.STEP_UNSAFE_PATH) from e
    if through_link or path.resolve() == Path(root).resolve():
        raise StepError(messages.STEP_UNSAFE_PATH)
    return path


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _overlaps(a: Path, b: Path) -> bool:
    a, b = (PurePath(os.path.normcase(p.resolve())) for p in (a, b))
    return a == b or a in b.parents or b in a.parents


def _run_file_step(step: dict, root: Path, action: str) -> None:
    if action == "delete":
        _remove(_path(root, step["path"]))
        return
    source, target = _path(root, step["from"]), _path(root, step["to"])
    if not source.exists():
        raise StepError(messages.STEP_FILE_MISSING.format(path=step["from"]))
    if _overlaps(source, target):
        raise StepError(messages.STEP_FILE_FAILED)  # Replacing the target would destroy the source.
    # The target ends up exactly as the source was: anything already there is replaced.
    _remove(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if action == "move":
        source.rename(target)
    elif source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target)


def run_file_steps(steps: list[dict], dir: Path) -> None:
    """Run the copy, move and delete steps, in order, on the files in `dir`. Other steps are skipped."""
    for step in steps:
        action = _action(step)
        if action in SYSTEM_ACTIONS:
            continue
        try:
            _run_file_step(step, Path(dir), action)
        except (OSError, KeyError) as e:
            raise StepError(messages.STEP_FILE_FAILED) from e


def _apply_registry(step: dict, ctx: StepContext) -> dict:
    hive, key, name = step["hive"], step["key"], step["name"]
    label = f"{hive}\\{key}"
    try:
        existed, previous, previous_type = ctx.registry.get(hive, key, name)
        if existed and not ctx.registry.can_restore(hive, previous_type, previous):
            raise StepError(messages.REGISTRY_CANT_RESTORE.format(key=label))
        ctx.registry.set(hive, key, name, step["type"], step["value"])
    except ElevationDeclined as e:
        raise StepError(messages.ELEVATION_DECLINED.format(what=label)) from e
    except OSError as e:
        raise StepError(messages.REGISTRY_WRITE_FAILED.format(key=label)) from e
    return {**step, "undo": {"existed": existed, "previous": previous, "previous_type": previous_type}}


def _existing_file(ctx: StepContext, rel: object) -> Path:
    path = _path(ctx.dir, rel)
    if not path.is_file():
        raise StepError(messages.STEP_FILE_MISSING.format(path=rel))
    return path


def _apply_register(step: dict, ctx: StepContext) -> dict:
    path = _existing_file(ctx, step["path"])
    try:
        code = ctx.elevator.run("regsvr32.exe", f'/s "{path}"', True, show=False)
    except ElevationDeclined as e:
        raise StepError(messages.ELEVATION_DECLINED.format(what=path.name)) from e
    if code != 0:
        raise StepError(messages.STEP_REGISTER_FAILED.format(name=path.name))
    return {**step}


def _apply_run(step: dict, ctx: StepContext) -> None:
    path = _existing_file(ctx, step["path"])
    args = step.get("args", "").replace("{dir}", str(ctx.dir))
    code = ctx.elevator.run(str(path), args, False, cwd=path.parent)
    if code != 0:
        raise StepError(messages.STEP_RUN_FAILED.format(name=path.name, code=code))


def run_system_steps(steps: list[dict], ctx: StepContext) -> list[dict]:
    """Run the registry, register and run steps, in order, in `ctx.dir`. Other steps are skipped.

    Returns the registry and register steps that were applied, each with its undo data. If a
    step fails, the ones already applied are undone (last first) and StepError is raised; the
    undo failures, if any, are on the error's `undo_failures`.
    """
    applied: list[dict] = []
    for step in steps:
        try:
            action = _action(step)
            if action == "registry":
                applied.append(_apply_registry(step, ctx))
            elif action == "register":
                applied.append(_apply_register(step, ctx))
            elif action == "run":
                _apply_run(step, ctx)  # Not recorded: a program's effects can't be undone.
        except Exception as e:
            failures = undo_steps(applied, ctx)
            error = e if isinstance(e, StepError) else StepError(messages.STEP_FAILED)
            error.undo_failures = failures
            if error is e:
                raise
            raise error from e
    return applied


def _bare(step: dict) -> dict:
    """A step as the catalog wrote it, without the undo data an applied step carries."""
    return {k: v for k, v in step.items() if k != "undo"}


def split_steps(applied: list[dict], wanted: list[dict], spare: list[dict] = ()
                ) -> tuple[list[dict], list[dict], list[dict]]:
    """(kept, unwanted, new): the applied steps still wanted or in `spare` (kept with their undo data, never
    run again, so their undo never captures a value Game Chooser set itself), the other applied steps (to
    undo), and the wanted steps not applied yet (to run)."""
    keep = [_bare(s) for s in [*wanted, *spare]]
    kept = [s for s in applied if _bare(s) in keep]
    kept_bare = [_bare(s) for s in kept]
    return kept, [s for s in applied if _bare(s) not in keep], [s for s in wanted if _bare(s) not in kept_bare]


def _undo_one(item: dict, ctx: StepContext) -> None:
    if item.get("action") == "registry":
        undo = item["undo"]
        if undo["existed"]:
            ctx.registry.set(item["hive"], item["key"], item["name"], undo["previous_type"], undo["previous"])
        else:
            ctx.registry.delete(item["hive"], item["key"], item["name"])
    elif item.get("action") == "register":
        path = _path(ctx.dir, item["path"])
        if ctx.elevator.run("regsvr32.exe", f'/u /s "{path}"', True, show=False) != 0:
            raise StepError(messages.UNDO_UNREGISTER_FAILED.format(name=path.name))


def _undo_failure_text(item: dict) -> str:
    if item.get("action") == "registry":
        return messages.UNDO_REGISTRY_FAILED.format(key=f"{item.get('hive')}\\{item.get('key')}\\{item.get('name')}")
    return messages.UNDO_UNREGISTER_FAILED.format(name=PureWindowsPath(str(item.get("path", ""))).name)


def undo_steps(applied: list[dict], ctx: StepContext) -> list[str]:
    """Undo applied system steps, last first. Never raises; returns each failure as plain text."""
    failures = []
    for item in reversed(applied):
        try:
            _undo_one(item, ctx)
        except Exception:
            log.warning("Couldn't undo step %r", item, exc_info=True)
            failures.append(_undo_failure_text(item))
    return failures
