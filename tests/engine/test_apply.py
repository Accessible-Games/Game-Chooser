import os
from datetime import date

import pytest

from engine import safety
from engine.errors import EngineError
from engine.records import RecordedFile
from engine.safety import (ABSENT, ApplyResult, FileOp, Op, UpdatePlan, apply_plan, plain_file, remove_unchanged,
                           restore_backups)
from tests.engine.fakes import BK, put, sha

DAY = date(2026, 11, 4)


def make_plan(*ops):
    """ops: (path, Op, incoming_bytes|None)"""
    plan = UpdatePlan()
    for path, op, data in ops:
        key = path.lower()
        plan.ops.append(FileOp(key, path, op, ""))
        if data is not None:
            plan.new_files[key] = RecordedFile(path, sha(data), "game")
    return plan


@pytest.fixture
def dirs(tmp_path):
    game, inc = tmp_path / "game", tmp_path / "inc"
    game.mkdir()
    inc.mkdir()
    return game, inc


def test_backup_then_write(dirs):
    game, inc = dirs
    put(game, "cfg.ini", b"mine")
    put(inc, "cfg.ini", b"new")
    r = apply_plan(make_plan(("cfg.ini", Op.BACKUP_AND_WRITE, b"new")), game, inc, DAY)
    assert isinstance(r, ApplyResult)
    assert (game / BK / "cfg.ini").read_bytes() == b"mine"
    assert (game / "cfg.ini").read_bytes() == b"new"
    assert r.backups == [{"original": "cfg.ini", "backup": f"{BK}/cfg.ini", "installed_sha256": sha(b"new")}]
    assert [p.name for p in game.iterdir() if p.is_file()] == ["cfg.ini"]  # no temp left


def test_backup_name_collision_same_day(dirs):
    game, inc = dirs
    put(game, "cfg.ini", b"one")
    put(inc, "cfg.ini", b"two")
    plan = make_plan(("cfg.ini", Op.BACKUP_AND_WRITE, b"two"))
    apply_plan(plan, game, inc, DAY)
    put(game, "cfg.ini", b"mine2")
    r = apply_plan(plan, game, inc, DAY)
    assert (game / BK / "cfg.ini").read_bytes() == b"one"
    assert (game / BK / "cfg (2).ini").read_bytes() == b"mine2"
    assert r.backups[0]["backup"] == f"{BK}/cfg (2).ini"


def test_backup_keeps_subfolders(dirs):
    game, inc = dirs
    put(game, "Data/cfg.ini", b"mine")
    put(inc, "Data/cfg.ini", b"new")
    r = apply_plan(make_plan(("Data/cfg.ini", Op.BACKUP_AND_WRITE, b"new")), game, inc, DAY)
    assert (game / BK / "Data" / "cfg.ini").read_bytes() == b"mine"
    assert r.backups[0]["backup"] == f"{BK}/Data/cfg.ini"


def test_write_creates_dirs_and_delete_prunes_empty_dirs(dirs):
    game, inc = dirs
    put(inc, "a/b/new.txt", b"n")
    put(game, "old/deep/gone.txt", b"g")
    put(game, "keep/other.txt", b"k")
    put(game, "keep/gone2.txt", b"g")
    plan = make_plan(("a/b/new.txt", Op.WRITE, b"n"), ("old/deep/gone.txt", Op.DELETE, None),
                     ("keep/gone2.txt", Op.DELETE, None), ("keep/other.txt", Op.KEEP, None))
    r = apply_plan(plan, game, inc, DAY)
    assert (game / "a/b/new.txt").read_bytes() == b"n"
    assert not (game / "old").exists()
    assert (game / "keep/other.txt").exists() and not (game / "keep/gone2.txt").exists()
    assert game.exists() and r.backups == []


def test_keep_does_nothing(dirs):
    game, inc = dirs
    put(game, "k.txt", b"mine")
    apply_plan(make_plan(("k.txt", Op.KEEP, b"new")), game, inc, DAY)
    assert (game / "k.txt").read_bytes() == b"mine"


def test_preflight_target_is_directory_changes_nothing(dirs):
    game, inc = dirs
    put(game, "data/keep.txt", b"p")
    put(game, "other.txt", b"old")
    put(inc, "data", b"file")
    put(inc, "other.txt", b"new")
    plan = make_plan(("other.txt", Op.WRITE, b"new"), ("data", Op.WRITE, b"file"))
    with pytest.raises(EngineError):
        apply_plan(plan, game, inc, DAY)
    assert (game / "other.txt").read_bytes() == b"old"
    assert (game / "data/keep.txt").exists()


def test_preflight_parent_is_file(dirs):
    game, inc = dirs
    put(game, "data", b"player file")
    put(inc, "data/x.txt", b"n")
    with pytest.raises(EngineError):
        apply_plan(make_plan(("data/x.txt", Op.WRITE, b"n")), game, inc, DAY)
    assert (game / "data").read_bytes() == b"player file"


def test_unsafe_path_rejected(dirs):
    game, inc = dirs
    with pytest.raises(EngineError):
        apply_plan(make_plan(("../evil.txt", Op.WRITE, b"n")), game, inc, DAY)


def test_failed_write_keeps_backup_and_cleans_temp(dirs, monkeypatch):
    game, inc = dirs
    put(game, "cfg.ini", b"mine")
    put(inc, "cfg.ini", b"new")
    real = os.replace

    def boom(src, dst):
        if str(dst).endswith("cfg.ini") and BK not in str(dst):
            raise OSError("nope")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        apply_plan(make_plan(("cfg.ini", Op.BACKUP_AND_WRITE, b"new")), game, inc, DAY)
    monkeypatch.setattr(os, "replace", real)
    files = [p for p in game.rglob("*") if p.is_file()]
    assert len(files) == 1 and files[0].read_bytes() == b"mine"  # player's data survives, no temp left


def test_missing_incoming_file_leaves_player_file_alone(dirs):
    game, inc = dirs
    put(game, "cfg.ini", b"mine")
    with pytest.raises(OSError):
        apply_plan(make_plan(("cfg.ini", Op.BACKUP_AND_WRITE, b"new")), game, inc, DAY)
    assert (game / "cfg.ini").read_bytes() == b"mine"
    assert not (game / BK).exists()


def test_restore_backups_only_when_untouched_since(dirs):
    game, inc = dirs
    put(game, "a.ini", b"new a")
    put(game, "b.ini", b"player changed again")
    put(game, f"{BK}/a.ini", b"player original")
    put(game, f"{BK}/b.ini", b"player original b")
    backups = [
        {"original": "a.ini", "backup": f"{BK}/a.ini", "installed_sha256": sha(b"new a")},
        {"original": "b.ini", "backup": f"{BK}/b.ini", "installed_sha256": sha(b"new b")},
    ]
    assert restore_backups(game, backups) == ["a.ini"]
    assert (game / "a.ini").read_bytes() == b"player original"
    assert (game / BK / "b.ini").exists() and (game / "b.ini").read_bytes() == b"player changed again"
    assert not (game / BK / "a.ini").exists()


def test_restore_when_original_missing_and_prunes_emptied_backup_dirs(dirs):
    game, inc = dirs
    put(game, f"{BK}/Data/c.ini", b"orig")
    backups = [{"original": "Data/c.ini", "backup": f"{BK}/Data/c.ini", "installed_sha256": sha(b"x")}]
    assert restore_backups(game, backups) == ["Data/c.ini"]
    assert (game / "Data/c.ini").read_bytes() == b"orig"
    assert not (game / BK).exists()


def test_restore_skips_missing_backup_file(dirs):
    game, inc = dirs
    put(game, "a.ini", b"new a")
    backups = [{"original": "a.ini", "backup": f"{BK}/a.ini", "installed_sha256": sha(b"new a")}]
    assert restore_backups(game, backups) == []
    assert (game / "a.ini").read_bytes() == b"new a"


def test_restore_never_deletes_other_backup_files(dirs):
    game, inc = dirs
    put(game, "a.ini", b"new a")
    put(game, f"{BK}/a.ini", b"orig")
    put(game, f"{BK}/other.ini", b"someone else")
    backups = [{"original": "a.ini", "backup": f"{BK}/a.ini", "installed_sha256": sha(b"new a")}]
    restore_backups(game, backups)
    assert (game / BK / "other.ini").read_bytes() == b"someone else"


# ---- last-moment re-check, partial results, backup-path preflight ----------


def make_op_plan(path, op, expected, new=b"new"):
    plan = UpdatePlan()
    plan.ops.append(FileOp(path.lower(), path, op, "", expected))
    if new is not None:
        plan.new_files[path.lower()] = RecordedFile(path, sha(new), "game")
    return plan


@pytest.mark.parametrize("path, op, expected, new, on_disk, after, backup", [
    ("save.dat", Op.WRITE, ABSENT, b"new", b"game made this", b"new", b"game made this"),
    ("a.txt", Op.WRITE, sha(b"old"), b"new", b"changed later", b"new", b"changed later"),
    ("g.exe", Op.WRITE, sha(b"old"), b"new", b"changed later", b"new", None),
    ("a.txt", Op.WRITE, sha(b"old"), b"new", None, b"new", None),
    ("p.ini", Op.WRITE, ABSENT, None, b"player", b"player", None),
    ("d/x.txt", Op.DELETE, sha(b"old"), None, b"changed later", b"changed later", None),
    ("d/x.txt", Op.DELETE, sha(b"same"), None, b"same", None, None),
], ids=["write_over_file_that_appeared_since_planning_is_backed_up",
        "write_over_file_changed_since_planning_is_backed_up", "changed_exe_is_replaced_without_copy",
        "write_when_file_vanished_just_writes", "persist_default_that_appeared_is_kept",
        "delete_of_changed_file_is_kept", "delete_of_unchanged_file_still_deletes"])
def test_last_moment_recheck(dirs, path, op, expected, new, on_disk, after, backup):
    game, inc = dirs
    if op == Op.WRITE:
        put(inc, path, b"new")
    if on_disk is not None:
        put(game, path, on_disk)
    r = apply_plan(make_op_plan(path, op, expected, new=new), game, inc, DAY)
    if after is None:
        assert not (game / "d").exists()  # the emptied folder goes too
    else:
        assert (game / path).read_bytes() == after
    if backup is None:
        assert r.backups == [] and not (game / BK).exists()
    else:
        assert (game / BK / path).read_bytes() == backup and len(r.backups) == 1


def test_plan_update_fills_expected():
    from engine.safety import plan_update
    plan = plan_update({}, {"a.txt": ("a.txt", "h")}, {"a.txt": RecordedFile("a.txt", "n", "g"),
                                                       "b.txt": RecordedFile("b.txt", "n", "g")})
    assert {o.path: o.expected for o in plan.ops} == {"a.txt": "h", "b.txt": ABSENT}


def test_failure_partway_reports_partial_backups(dirs, monkeypatch):
    game, inc = dirs
    for name in ("a.ini", "b.ini"):
        put(game, name, b"mine " + name.encode())
        put(inc, name, b"new")
    real = os.replace

    def boom(src, dst):
        if str(dst).endswith("b.ini") and BK not in str(dst):
            raise OSError("nope")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", boom)
    plan = make_plan(("a.ini", Op.BACKUP_AND_WRITE, b"new"), ("b.ini", Op.BACKUP_AND_WRITE, b"new"))
    with pytest.raises(OSError) as info:
        apply_plan(plan, game, inc, DAY)
    monkeypatch.setattr(os, "replace", real)
    assert [b["original"] for b in info.value.partial.backups] == ["a.ini"]
    assert (game / BK / "a.ini").exists() and (game / "b.ini").read_bytes() == b"mine b.ini"


def test_preflight_catches_backup_path_blocked_by_file(dirs):
    game, inc = dirs
    put(game, "Data/cfg.ini", b"mine")
    put(game, "other.txt", b"old")
    put(game, BK, b"a file where the backup folder goes")
    put(inc, "Data/cfg.ini", b"new")
    put(inc, "other.txt", b"new")
    plan = make_plan(("other.txt", Op.WRITE, b"new"), ("Data/cfg.ini", Op.BACKUP_AND_WRITE, b"new"))
    with pytest.raises(EngineError):
        apply_plan(plan, game, inc, DAY)
    assert (game / "other.txt").read_bytes() == b"old"


def test_a_file_that_cant_be_read_is_never_taken_as_changed(tmp_path, monkeypatch):
    put(tmp_path, "a.dll", b"a")

    def locked(path):
        raise PermissionError(13, "locked", str(path))
    monkeypatch.setattr(safety, "hash_file", locked)
    with pytest.raises(OSError):
        plain_file(tmp_path, "a.dll", sha(b"a"))


def test_remove_unchanged_keeps_a_changed_file(tmp_path):
    put(tmp_path, "a.dll", b"the player's")
    assert not remove_unchanged(tmp_path, "a.dll", sha(b"installed"))
    assert (tmp_path / "a.dll").read_bytes() == b"the player's"
