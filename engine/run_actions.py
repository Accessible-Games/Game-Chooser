"""Repair and Undo for a game whose recorded version is an installer that must run, and the installed
files extras are added to when there is no archive to rebuild.

Repair runs the recorded installer again and Undo the previous one. Each is fetched by its exact
checksum (as an archive's Repair and Undo are), with the version's recorded extras, before anything
changes. The installer runs over the game's folder the way a run-mode update does
(engine.run_installer.update): the player's files are copied aside first and put back afterwards
if the installer changed or removed them. The extras are then applied over the folder under the
three-checksum rule, and the version's private COM copies are made again, as an archive's Repair and
Undo make them (engine.runtimes). Undo also removes the newer version's unchanged files the older installer
didn't write, and puts back the player's copies the update it undoes moved into the backup folder.
"""

import shutil
from pathlib import Path

from . import jobs, messages, run_installer, runtimes
from .entries import title_of
from .errors import InstallFailed, NoVerifiedSource, UnsafePath
from .records import InstalledSource, InstallRecord
from .run_installer import KEEP_UNDO_POINT, RunItem
from .safety import plain_file, restore_backups
from .sources import Fetched, hash_file


def repair(engine, entry: dict, rec: InstallRecord, game_dir: Path, progress, cancel) -> "jobs.JobResult":
    """Run the recorded installer again over the game's folder, then put its recorded extras back.
    The record keeps its source and extras, and its undo point stays as it was: Repair isn't one."""
    return undo(engine, entry, rec, game_dir, rec.source, None, progress, cancel)


def undo(engine, entry: dict, rec: InstallRecord, game_dir: Path, source: InstalledSource, previous: dict | None,
         progress, cancel) -> "jobs.JobResult":
    """Run the previous version's installer (`source`, from `rec.previous`) over the game's folder and
    put its extras back, then put back the player's copies the update moved aside, where the update's
    file is untouched. With `previous` None this is Repair (see repair)."""
    ctx = engine.ctx
    game_id, title = entry["id"], title_of(entry)
    if previous is None:
        extra_sources, prior = dict(rec.extras), jobs._snapshot(rec)
    else:
        extra_sources = {k: jobs._source_from(v) for k, v in (previous.get("extras") or {}).items()}
        prior = previous
    if extra_sources or rec.extras:  # changed by Game Chooser after the installer, so checked before it runs
        engine._require_writable(rec, game_dir, title)
    private = runtimes.private_runtimes(rec, entry, ctx, (previous or {}).get("private_runtimes"))
    with engine._workspace(engine._staging_root(rec, game_dir), game_id) as (staging, work):
        try:
            fetched, unpacked, copies = _fetch_recorded(engine, entry, source, extra_sources, private, staging,
                                                        work, progress, cancel)
        except (NoVerifiedSource, jobs._NoUnpackSpec) as e:
            if previous is None:
                raise InstallFailed(jobs._repair_failure_text(e, game_id, title)) from e
            raise engine._undo_failed(e, rec, title) from e
        # Decided before anything changes: a backup goes back only over the file the update wrote.
        untouched = jobs._untouched_backups(game_dir, (previous or {}).get("backups") or [])
        had = rec.files  # run_installer.update replaces this dict, never changes it
        try:
            # The undo point stays until the backups are back, so a failed Undo can be tried again.
            record, notes = run_installer.update(ctx, _run_item(engine, entry, fetched, source), rec, game_dir,
                                                 progress, cancel, previous=KEEP_UNDO_POINT,
                                                 spare_steps=jobs._extra_steps(unpacked), undo_of=previous)
            extra_notes, backups = engine._extras_after_installer(entry, record, unpacked, staging, prior,
                                                                  cancel, recorded=extra_sources)
            extra_notes += engine._private_after_installer(entry, record, copies)
            extra_notes += engine._copies_after_installer(
                entry, record, copies,
                record.runtime_copies if previous is None else jobs._previous_copies(previous, rec), had)
        except BaseException:
            _retarget_rewritten(ctx, rec, game_dir, untouched)
            raise
    if previous is not None:
        # The installer and extras have just rewritten those files, so each is compared with what they wrote.
        restored = restore_backups(game_dir, [jobs._rewritten_by(record.files, b) for b in untouched])
        notes += jobs._backups_kept_note(game_dir, previous.get("backups") or [], restored, title)
        record.previous = None
        record.previous_unavailable = None
        ctx.records.save(record)
    return jobs.JobResult("repair" if previous is None else "undo", game_id, title,
                          jobs.game_fields_for(entry, record, ctx.platform), [*notes, *extra_notes], backups=backups)


def copy_installed_files(rec: InstallRecord, game_dir: Path, tree: Path, title: str) -> Path:
    """Copy into `tree` each recorded file the game's folder still holds exactly as installed.

    For an installer that must run, this is the installed version its extras are added to: it can't
    be rebuilt in staging. Files the player changed or removed are left out, so an extra's file step
    that needs one stops with plain words instead of reading the player's copy. A file that can't be
    read or copied (locked, or the drive is full) stops it with plain words too; only staging changed.
    """
    try:
        tree.mkdir(parents=True)
        for recorded in rec.files.values():
            try:
                path = plain_file(game_dir, recorded.path, recorded.sha256)
            except UnsafePath:
                continue
            if path is None:
                continue
            copy = tree / recorded.path
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copy)
    except OSError as e:
        raise InstallFailed(messages.EXTRAS_FILES_NOT_COPIED.format(title=title)) from e
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
            path = plain_file(game_dir, b["original"])
            if path is None:
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


def _fetch_recorded(engine, entry: dict, source: InstalledSource, extra_sources: dict[str, InstalledSource],
                    private: list[dict], staging: Path, work: Path, progress,
                    cancel) -> tuple[Fetched, list, list[runtimes.PrivateCopy]]:
    """Download exactly the recorded installer, and download and unpack exactly the recorded extras and
    (from today's catalog) the `private` COM runtimes, before anything changes. Raises NoVerifiedSource
    if an address doesn't serve the installer or an extra."""
    fetch = engine._exact_fetch({entry["id"]: source, **extra_sources}, progress, cancel)
    fetched = fetch(entry, work / "fetched")
    extras = [engine.ctx.entries.get(x) or {"id": x} for x in extra_sources]
    copies = runtimes.unpack_private(engine.ctx, private, staging, work, engine._catalog_fetch(progress, cancel),
                                     progress, cancel)
    return fetched, engine._unpack_extras(extras, staging, work, fetch, progress, cancel), copies


def _run_item(engine, entry: dict, fetched: Fetched, source: InstalledSource) -> RunItem:
    """The downloaded installer, set up as recorded, with the recorded source kept exactly."""
    install = fetched.source.get("install") or {}
    return RunItem(entry["id"], entry.get("kind", "game"), title_of(entry), fetched.path,
                   dict(install.get("installer") or {}), jobs._steps(fetched), source,
                   jobs._recorded_launch(source, entry, engine.ctx.platform))
