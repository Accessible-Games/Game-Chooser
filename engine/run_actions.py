"""Repair and Undo for a game whose recorded version is an installer that must run, and the installed
files Extras... adds to when there is no archive to rebuild.

Repair runs the recorded installer again and Undo the previous one. Each is fetched by its exact
checksum (as an archive's Repair and Undo are) and run over the game's folder the way a run-mode
update is (engine.run_installer.update): the player's files are copied aside first and put back
afterwards if the installer changed or removed them. Undo then puts back the player's copies the
update it undoes moved into the backup folder.

An installer can't rebuild the extras added over it, so Repair and Undo refuse a version that has
extras, as a run-mode update does.
"""

import shutil
from pathlib import Path

from . import jobs, messages, run_installer
from .errors import InstallFailed, NoVerifiedSource, UnsafePath
from .records import InstalledSource, InstallRecord
from .run_installer import KEEP_UNDO_POINT, RunItem
from .safety import _resolve_game_path, restore_backups
from .sources import Fetched, hash_file


def repair(engine, entry: dict, rec: InstallRecord, game_dir: Path, progress, cancel) -> "jobs.JobResult":
    """Run the recorded installer again over the game's folder. The record keeps its source, and
    its undo point stays as it was: Repair isn't one."""
    game_id, title = entry["id"], jobs._title(entry)
    if rec.extras:
        raise InstallFailed(messages.RUN_INSTALLER_EXTRAS.format(title=title))
    with engine._work_folder(game_id) as work:
        try:
            fetched = _fetch_recorded(engine, entry, rec.source, work, progress, cancel)
        except NoVerifiedSource as e:
            raise InstallFailed(jobs._repair_failure_text(e, game_id, title)) from e
        record, notes = run_installer.update(engine.ctx, _run_item(engine, entry, fetched, rec.source), rec,
                                             game_dir, progress, cancel, previous=KEEP_UNDO_POINT)
    return jobs.JobResult("repair", game_id, title, jobs._game_dict(entry, record), notes)


def undo(engine, entry: dict, rec: InstallRecord, game_dir: Path, source: InstalledSource,
         progress, cancel) -> "jobs.JobResult":
    """Run the previous version's installer (`source`, from `rec.previous`) over the game's folder,
    then put back the player's copies the update moved aside, where the update's file is untouched."""
    ctx = engine.ctx
    game_id, title = entry["id"], jobs._title(entry)
    previous = rec.previous
    if previous.get("extras"):
        raise InstallFailed(messages.RUN_INSTALLER_EXTRAS.format(title=title))
    with engine._work_folder(game_id) as work:
        try:
            fetched = _fetch_recorded(engine, entry, source, work, progress, cancel)
        except NoVerifiedSource as e:
            if not e.downloaded:  # Nothing could be reached: maybe only for now, so Undo stays offered.
                raise InstallFailed(messages.DOWNLOAD_UNREACHABLE.format(title=title)) from e
            rec.previous_unavailable = messages.UNDO_UNAVAILABLE.format(title=title)
            ctx.records.save(rec)
            raise InstallFailed(rec.previous_unavailable) from e
        # Decided before anything changes: a backup goes back only over the file the update wrote.
        untouched = jobs._untouched_backups(game_dir, previous.get("backups") or [])
        try:
            # The undo point stays until the backups are back, so a failed Undo can be tried again.
            record, notes = run_installer.update(ctx, _run_item(engine, entry, fetched, source), rec, game_dir,
                                                 progress, cancel, previous=KEEP_UNDO_POINT)
        except BaseException:
            _retarget_rewritten(ctx, rec, game_dir, untouched)
            raise
    # The installer has just rewritten those files, so each is compared with what it wrote.
    restore_backups(game_dir, [jobs._rewritten_by(record.files, b) for b in untouched])
    record.previous = None
    record.previous_unavailable = None
    ctx.records.save(record)
    return jobs.JobResult("undo", game_id, title, jobs._game_dict(entry, record), notes)


def copy_installed_files(rec: InstallRecord, game_dir: Path, tree: Path) -> Path:
    """Copy into `tree` each recorded file the game's folder still holds exactly as installed.

    For an installer that must run, this is the installed version Extras... adds to: it can't be
    rebuilt in staging. Files the player changed or removed are left out, so an extra's file step
    that needs one stops with plain words instead of reading the player's copy.
    """
    tree.mkdir(parents=True)
    for recorded in rec.files.values():
        try:
            path, linked = _resolve_game_path(game_dir, recorded.path)
        except UnsafePath:
            continue
        if linked or not path.is_file() or hash_file(path) != recorded.sha256:
            continue
        copy = tree / recorded.path
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, copy)
    return tree


def _retarget_rewritten(ctx, rec: InstallRecord, game_dir: Path, untouched: list[dict]) -> None:
    """After an Undo that stopped once its installer had run (it failed, a step failed, or it was
    cancelled): a backup that was due to go back, whose file has changed since, was rewritten by that
    installer, so it is now compared with what the installer wrote. Trying the Undo again then still
    puts the player's copy back; if the player changes the file first, it stays in the backup folder.
    """
    if rec.previous is None:
        return
    rewritten = {}
    for b in untouched:
        try:
            path, linked = _resolve_game_path(game_dir, b["original"])
            if linked or not path.is_file():
                continue
            sha = hash_file(path)
        except (UnsafePath, OSError):
            continue
        if sha != b["installed_sha256"]:
            rewritten[(b["original"], b["backup"])] = sha
    if not rewritten:
        return
    rec.previous = {**rec.previous, "backups": [
        {**b, "installed_sha256": rewritten[(b.get("original"), b.get("backup"))]}
        if (b.get("original"), b.get("backup")) in rewritten else b
        for b in rec.previous.get("backups") or []]}
    ctx.records.save(rec)


def _fetch_recorded(engine, entry: dict, source: InstalledSource, work: Path, progress, cancel) -> Fetched:
    """Download exactly the recorded installer (raises NoVerifiedSource if no address serves it)."""
    return engine._exact_fetch({entry["id"]: source}, progress, cancel)(entry, work / "fetched")


def _run_item(engine, entry: dict, fetched: Fetched, source: InstalledSource) -> RunItem:
    """The downloaded installer, set up as recorded, with the recorded source kept exactly."""
    install = fetched.source.get("install") or {}
    return RunItem(entry["id"], entry.get("kind", "game"), jobs._title(entry), fetched.path,
                   dict(install.get("installer") or {}), jobs._steps(fetched), source,
                   jobs._recorded_launch(source, entry, engine.ctx.platform))
