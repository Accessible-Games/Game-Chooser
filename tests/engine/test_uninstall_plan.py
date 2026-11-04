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
