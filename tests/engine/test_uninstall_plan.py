import pytest
import hashlib
from pathlib import Path

from engine.records import RecordedFile
from engine.safety import UninstallPlan, apply_uninstall, count_player_files, plan_uninstall

BK = "Game Chooser backup 2026-11-04"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(root, rel, data=b"x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


class FakeRecord:
    def __init__(self, files):
        self.files = {p.lower(): RecordedFile(p, sha(d), "game") for p, d in files.items()}


class FakeRecycle:
    def __init__(self):
        self.calls = []

    def __call__(self, paths):
        self.calls.append(list(paths))
        for p in paths:
            assert Path(p).is_absolute()
            Path(p).unlink()


def setup(tmp_path):
    game = tmp_path / "game"
    put(game, "game.exe", b"exe")
    put(game, "sub/data.bin", b"bin")
    put(game, "cfg.ini", b"edited")
    put(game, "Saves/1.sav", b"save")
    put(game, f"{BK}/x.ini", b"old")
    rec = FakeRecord({"game.exe": b"exe", "sub/data.bin": b"bin", "cfg.ini": b"original", "gone.txt": b"g"})
    return game, rec


def test_uninstall_plan_separates_player_files(tmp_path):
    game, rec = setup(tmp_path)
    put(game, ".gamechooser-staging/junk", b"j")
    p = plan_uninstall(rec, game)
    assert isinstance(p, UninstallPlan)
    assert p.delete == ["game.exe", "sub/data.bin"]
    assert p.player_files == [f"{BK}/x.ini", "Saves/1.sav", "cfg.ini"]
    assert count_player_files(rec, game) == 3


def test_uninstall_keeps_player_data_by_default(tmp_path):
    game, rec = setup(tmp_path)
    p = plan_uninstall(rec, game)
    fake = FakeRecycle()
    kept = apply_uninstall(p, game, False, recycle=fake)
    assert kept == 3 and fake.calls == []
    assert (game / "Saves/1.sav").exists() and not (game / "game.exe").exists()
    assert not (game / "sub").exists()


def test_uninstall_recycles_player_data_when_asked(tmp_path):
    game, rec = setup(tmp_path)
    p = plan_uninstall(rec, game)
    fake = FakeRecycle()
    kept = apply_uninstall(p, game, True, recycle=fake)
    assert kept == 0 and len(fake.calls) == 1 and len(fake.calls[0]) == 3
    assert not game.exists()


def test_game_folder_removed_only_when_empty(tmp_path):
    game = tmp_path / "game"
    put(game, "game.exe", b"exe")
    rec = FakeRecord({"game.exe": b"exe"})
    fake = FakeRecycle()
    assert apply_uninstall(plan_uninstall(rec, game), game, False, recycle=fake) == 0
    assert not game.exists() and fake.calls == []
    game2 = tmp_path / "game2"
    put(game2, "game.exe", b"exe")
    put(game2, "mine.txt", b"m")
    apply_uninstall(plan_uninstall(rec, game2), game2, False, recycle=fake)
    assert game2.exists() and (game2 / "mine.txt").exists()


def test_empty_player_list_skips_recycle(tmp_path):
    game = tmp_path / "game"
    put(game, "game.exe", b"exe")
    fake = FakeRecycle()
    apply_uninstall(plan_uninstall(FakeRecord({"game.exe": b"exe"}), game), game, True, recycle=fake)
    assert fake.calls == [] and not game.exists()


def test_changed_file_is_kept_and_counted_at_apply_time(tmp_path):
    game, rec = setup(tmp_path)
    p = plan_uninstall(rec, game)
    (game / "game.exe").write_bytes(b"changed after planning")
    fake = FakeRecycle()
    kept = apply_uninstall(p, game, False, recycle=fake)
    assert kept == 4 and (game / "game.exe").read_bytes() == b"changed after planning"
    assert not (game / "sub").exists()


def test_changed_file_is_recycled_when_asked(tmp_path):
    game, rec = setup(tmp_path)
    p = plan_uninstall(rec, game)
    (game / "game.exe").write_bytes(b"changed after planning")
    fake = FakeRecycle()
    apply_uninstall(p, game, True, recycle=fake)
    assert len(fake.calls[0]) == 4 and not game.exists()


@pytest.mark.windows
def test_junction_is_never_followed_or_removed(tmp_path):
    import _winapi
    outside = tmp_path / "outside"
    put(outside, "keep.txt", b"not yours")
    (outside / "emptydir").mkdir()
    game = tmp_path / "game"
    put(game, "game.exe", b"exe")
    _winapi.CreateJunction(str(outside), str(game / "Saves"))
    try:
        rec = FakeRecord({"game.exe": b"exe"})
        p = plan_uninstall(rec, game)
        assert p.player_files == ["Saves"]
        fake = FakeRecycle()
        kept = apply_uninstall(p, game, True, recycle=fake)
        assert fake.calls == [] and kept == 1
        assert (outside / "keep.txt").read_bytes() == b"not yours" and (outside / "emptydir").is_dir()
        assert (game / "Saves").exists() and game.exists()
    finally:
        import os
        os.rmdir(game / "Saves")


@pytest.mark.windows
def test_hash_tree_does_not_follow_junction(tmp_path):
    import _winapi
    from engine.records import hash_tree
    outside = tmp_path / "outside"
    put(outside, "keep.txt", b"x")
    game = tmp_path / "game"
    put(game, "a.txt", b"a")
    _winapi.CreateJunction(str(outside), str(game / "J"))
    try:
        assert list(hash_tree(game)) == ["a.txt"]
    finally:
        import os
        os.rmdir(game / "J")


@pytest.mark.windows
def test_recorded_file_under_outward_junction(tmp_path):
    import os
    import _winapi
    outside = tmp_path / "outside"
    put(outside, "1.sav", b"save")
    game = tmp_path / "game"
    put(game, "game.exe", b"exe")
    _winapi.CreateJunction(str(outside), str(game / "Saves"))
    try:
        rec = FakeRecord({"game.exe": b"exe", "Saves/1.sav": b"save"})
        p = plan_uninstall(rec, game)
        assert p.delete == ["game.exe"] and p.player_files == ["Saves"]
        assert count_player_files(rec, game) == 1
        fake = FakeRecycle()
        kept = apply_uninstall(p, game, True, recycle=fake)
        assert fake.calls == [] and kept == 1
        assert (outside / "1.sav").read_bytes() == b"save"
        assert (game / "Saves").exists()
    finally:
        os.rmdir(game / "Saves")


@pytest.mark.windows
def test_stale_plan_path_that_became_junction_is_never_recycled(tmp_path):
    import os
    import _winapi
    game = tmp_path / "game"
    put(game, "game.exe", b"exe")
    put(game, "Saves/1.sav", b"save")
    rec = FakeRecord({"game.exe": b"exe", "Saves/1.sav": b"save"})
    p = plan_uninstall(rec, game)
    assert p.delete == ["Saves/1.sav", "game.exe"]
    outside = tmp_path / "outside"
    put(outside, "1.sav", b"save")
    (game / "Saves" / "1.sav").unlink()
    (game / "Saves").rmdir()
    _winapi.CreateJunction(str(outside), str(game / "Saves"))
    try:
        fake = FakeRecycle()
        kept = apply_uninstall(p, game, True, recycle=fake)
        assert all("Saves" not in str(c) for call in fake.calls for c in call)
        assert (outside / "1.sav").read_bytes() == b"save"
        assert kept == 1 and (game / "Saves").exists()
    finally:
        os.rmdir(game / "Saves")
