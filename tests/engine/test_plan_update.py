import pytest

from engine.records import RecordedFile
from engine.safety import Op, matches_persist, plan_update

OWNER = "game"


def rf(path, sha, owner=OWNER):
    return RecordedFile(path, sha, owner)


# test id, path, I, D, N, expected op (None = no op), new_files hash (None = absent)
TABLE = [
    ("unchanged_and_changed_by_new", "f.txt", "a", "a", "b", Op.WRITE, "b"),
    ("unchanged_and_removed_by_new", "f.txt", "a", "a", None, Op.DELETE, None),
    ("unchanged_and_same", "f.txt", "a", "a", "a", Op.KEEP, "a"),
    ("already_equals_new", "f.txt", "a", "b", "b", Op.KEEP, "b"),
    ("player_changed_new_same", "f.txt", "a", "p", "a", Op.KEEP, "a"),
    ("player_changed_and_new_changes", "f.txt", "a", "p", "b", Op.BACKUP_AND_WRITE, "b"),
    ("player_changed_exe_replaced_without_copy", "game.exe", "a", "p", "b", Op.WRITE, "b"),
    ("player_changed_and_removed_by_new", "f.txt", "a", "p", None, Op.KEEP, None),
    ("unrecorded_and_new_ships_path", "f.txt", None, "p", "b", Op.BACKUP_AND_WRITE, "b"),
    ("unrecorded_dll_new_ships_path", "x.dll", None, "p", "b", Op.WRITE, "b"),
    ("unrecorded_equal_to_new", "f.txt", None, "b", "b", Op.KEEP, "b"),
    ("unrecorded_not_shipped", "f.txt", None, "p", None, Op.KEEP, None),
    ("missing_and_new_ships", "f.txt", "a", None, "b", Op.WRITE, "b"),
    ("missing_and_not_shipped", "f.txt", "a", None, None, None, None),
    ("brand_new_file", "f.txt", None, None, "b", Op.WRITE, "b"),
]


@pytest.mark.parametrize("path,i,d,n,op,new",
                         [pytest.param(*row[1:], id=row[0]) for row in TABLE])
def test_table(path, i, d, n, op, new):
    key = path.lower()
    installed = {key: rf(path, i)} if i else {}
    on_disk = {key: (path, d)} if d else {}
    incoming = {key: rf(path, n)} if n else {}
    plan = plan_update(installed, on_disk, incoming)
    if op is None:
        assert plan.ops == []
    else:
        assert [(o.key, o.op) for o in plan.ops] == [(key, op)]
    if new is None:
        assert key not in plan.new_files
    else:
        assert plan.new_files[key].sha256 == new


def test_case_only_difference_is_same_file():
    plan = plan_update({"data/config.ini": rf("Data/Config.ini", "a")}, {"data/config.ini": ("data/config.ini", "p")},
                       {"data/config.ini": rf("DATA/CONFIG.INI", "b")})
    assert [o.op for o in plan.ops] == [Op.BACKUP_AND_WRITE]
    assert plan.ops[0].path == "DATA/CONFIG.INI"


def test_persist_paths_are_always_player_owned():
    plan = plan_update({}, {"saves/s1.dat": ("Saves/s1.dat", "p")}, {"saves/s1.dat": rf("Saves/s1.dat", "b")}, persist=["Saves/"])
    assert plan.ops[0].op is Op.KEEP and "saves/s1.dat" not in plan.new_files
    plan = plan_update({}, {}, {"saves/s1.dat": rf("Saves/s1.dat", "b")}, persist=["Saves/"])
    assert plan.ops[0].op is Op.WRITE and "saves/s1.dat" not in plan.new_files


def test_persist_wins_over_an_old_record():
    inst = {"saves/s1.dat": rf("Saves/s1.dat", "a")}
    for disk_hash, inc in (("a", None), ("p", "b"), ("a", "b")):
        disk = {"saves/s1.dat": ("Saves/s1.dat", disk_hash)}
        incoming = {"saves/s1.dat": rf("Saves/s1.dat", inc)} if inc else {}
        plan = plan_update(inst, disk, incoming, persist=["saves/"])
        assert [o.op for o in plan.ops] == [Op.KEEP]
        assert plan.new_files == {}


def test_repair_restores_changed_code_only():
    inst = {"game.exe": rf("game.exe", "a"), "cfg.ini": rf("cfg.ini", "a")}
    disk = {"game.exe": ("game.exe", "q"), "cfg.ini": ("cfg.ini", "p")}
    ops = {o.key: o.op for o in plan_update(inst, disk, inst, restore_changed_code=True).ops}
    assert ops == {"game.exe": Op.WRITE, "cfg.ini": Op.KEEP}


def test_repair_suffix_is_case_insensitive():
    inst = {"game.exe": rf("GAME.EXE", "a")}
    disk = {"game.exe": ("GAME.EXE", "q")}
    plan = plan_update(inst, disk, inst, restore_changed_code=True)
    assert [o.op for o in plan.ops] == [Op.WRITE]


def test_without_repair_changed_code_is_kept_when_new_is_same():
    inst = {"game.exe": rf("game.exe", "a")}
    plan = plan_update(inst, {"game.exe": ("game.exe", "q")}, inst)
    assert [o.op for o in plan.ops] == [Op.KEEP]


def test_owner_tag_comes_from_incoming():
    plan = plan_update({"a.txt": rf("a.txt", "a", "old")}, {"a.txt": ("a.txt", "a")},
                       {"a.txt": rf("a.txt", "b", "new-owner")})
    assert plan.new_files["a.txt"].owner == "new-owner"


def test_ops_are_sorted_and_path_falls_back():
    inst = {"b.txt": rf("B.txt", "a"), "a.txt": rf("A.txt", "a")}
    disk = {"b.txt": ("b.TXT", "a"), "a.txt": ("a.TXT", "a")}
    plan = plan_update(inst, disk, {})
    assert [(o.key, o.path, o.op) for o in plan.ops] == [
        ("a.txt", "a.TXT", Op.DELETE), ("b.txt", "b.TXT", Op.DELETE)]


@pytest.mark.parametrize("path,pats,expected", [
    ("Saves/s1.dat", ["saves/"], True),
    ("saves/sub/x", ["Saves/"], True),
    ("savesX/x", ["saves/"], False),
    ("config\\user.ini", ["*.ini"], True),
    ("a/b.txt", ["*.ini"], False),
    ("a/b.txt", [], False),
])
def test_matches_persist(path, pats, expected):
    assert matches_persist(path, pats) is expected


def test_unnormalized_keys_are_one_file():
    plan = plan_update({"Data/Config.ini": rf("Data/Config.ini", "a")},
                       {"data/config.ini": ("Data/Config.ini", "p")},
                       {"Data/Config.ini": rf("Data/Config.ini", "b")})
    assert [(o.key, o.op) for o in plan.ops] == [("data/config.ini", Op.BACKUP_AND_WRITE)]
    assert list(plan.new_files) == ["data/config.ini"]


@pytest.mark.parametrize("which", [0, 1, 2])
def test_key_collision_within_one_dict_raises(which):
    dicts = [{}, {}, {}]
    if which == 1:
        dicts[1] = {"a/b.txt": ("a/b.txt", "x"), "A/B.txt": ("A/B.txt", "y")}
    else:
        dicts[which] = {"a/b.txt": rf("a/b.txt", "x"), "A\\B.txt": rf("A/B.txt", "y")}
    with pytest.raises(ValueError):
        plan_update(*dicts)
