"""An extra that belongs to more than one game (catalog schema v2: `extends` is a list of game ids).

The index lists the extra in the `extras` of every game it extends. It installs into the folder of whichever
game it is added to, and each game's install record holds its own copy: the extra never has a record of its own,
and nothing done to one game's copy touches another's.
"""

import hashlib
from dataclasses import asdict
from pathlib import Path

from engine.entries import extras_of
from engine.jobs import InstallRequest
from engine.plan import build_install_plan
from tests.engine.fakes import FakeFetcher, FakeRegistry, make_engine, served_source, zip_bytes

MIRROR = "https://files.accessiblegames.net/files/"
ALPHA_V1 = {"alpha.exe": b"alpha v1", "data/patch.dat": b"alpha's own patch"}
ALPHA_V2 = {"alpha.exe": b"alpha v2", "data/patch.dat": b"alpha's own patch"}
BRAVO_V1 = {"bravo.exe": b"bravo v1", "data/patch.dat": b"bravo's own patch"}
SHARED_V1 = {"data/patch.dat": b"series patch v1", "data/bonus.dat": b"bonus v1"}
SHARED_V2 = {"data/patch.dat": b"series patch v2", "data/bonus.dat": b"bonus v2"}


def never():
    return False


def quiet(p):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def game(routes, id, title, files, version="1.0", required=False):
    data = zip_bytes(files)
    source = served_source(routes, f"{MIRROR}{sha(data)}/{id}.zip", data)
    return {"id": id, "kind": "game", "title": title, "developer": ["Series Games"], "genres": ["Arcade"],
            "year": 2012, "version": version, "sources": [source],
            "extras": [{"id": "series-patch", "required": required}]}


def shared_extra(routes, files=SHARED_V1, version="1", required=False):
    data = zip_bytes(files)
    source = served_source(routes, f"{MIRROR}{sha(data)}/series-patch.zip", data)
    entry = {"id": "series-patch", "kind": "extra", "title": "Series Patch", "extends": ["alpha", "bravo"],
             "version": version, "sources": [source]}
    if required:
        entry["required"] = True
    return entry


def catalog(tmp_path, required=False):
    routes = {}
    entries = [game(routes, "alpha", "Alpha", ALPHA_V1, required=required),
               game(routes, "bravo", "Bravo", BRAVO_V1, required=required),
               shared_extra(routes, required=required)]
    return make_engine(tmp_path, entries, fetcher=FakeFetcher(routes))


def run(eng, action, id, **kw):
    return eng.run(InstallRequest(action, id, "Games", **kw), quiet, never)


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def install_both(tmp_path, required=False):
    """Alpha, then Bravo, each with the shared extra (chosen, or required); returns the engine and both folders."""
    eng = catalog(tmp_path, required=required)
    chosen = () if required else ("series-patch",)
    run(eng, "install", "alpha", extras=chosen)
    run(eng, "install", "bravo", extras=chosen)
    return eng, tmp_path / "Games" / "Alpha", tmp_path / "Games" / "Bravo"


def state(eng, folder: Path, id: str):
    """A game's folder contents and install record, to compare before and after something done to the other."""
    return snapshot(folder), asdict(eng.ctx.records.load(id))


def leftovers(eng, lib: Path):
    staging = lib / ".gamechooser-staging"
    return [p for root in (staging, eng.ctx.work_root) if root.exists() for p in root.rglob("*")]


# ---- installing it with each game ----------------------------------------------------------------------------


def test_installing_with_each_game_gives_two_independent_copies(tmp_path):
    eng, alpha, bravo = install_both(tmp_path)
    for folder, exe in ((alpha, "alpha.exe"), (bravo, "bravo.exe")):
        assert (folder / "data" / "patch.dat").read_bytes() == SHARED_V1["data/patch.dat"]
        assert (folder / "data" / "bonus.dat").read_bytes() == SHARED_V1["data/bonus.dat"]
        assert (folder / exe).is_file()
    records = eng.ctx.records
    assert set(records.all()) == {"alpha", "bravo"}  # the extra never gets a record of its own
    for id in ("alpha", "bravo"):
        rec = records.load(id)
        assert set(rec.extras) == {"series-patch"}
        assert rec.extras["series-patch"].sha256 == eng.ctx.entries["series-patch"]["sources"][0]["sha256"]
        assert rec.files["data/patch.dat"].owner == rec.files["data/bonus.dat"].owner == "series-patch"
        assert rec.files[f"{id}.exe"].owner == id
    assert records.load("alpha").folder == "Alpha" and records.load("bravo").folder == "Bravo"
    assert leftovers(eng, tmp_path / "Games") == []


def test_a_required_shared_extra_installs_automatically_with_each_game(tmp_path):
    eng, alpha, bravo = install_both(tmp_path, required=True)
    for id, folder in (("alpha", alpha), ("bravo", bravo)):
        assert (folder / "data" / "bonus.dat").read_bytes() == SHARED_V1["data/bonus.dat"]
        rec = eng.ctx.records.load(id)
        assert set(rec.extras) == {"series-patch"} and rec.files["data/bonus.dat"].owner == "series-patch"


def test_the_second_game_downloads_its_own_copy(tmp_path):
    eng = catalog(tmp_path)
    run(eng, "install", "alpha", extras=("series-patch",))
    eng.ctx.fetcher.requests.clear()
    run(eng, "install", "bravo", extras=("series-patch",))
    assert eng.ctx.fetcher.requests == [eng.ctx.entries["bravo"]["sources"][0]["url"],
                                        eng.ctx.entries["series-patch"]["sources"][0]["url"]]


def test_installing_the_second_game_leaves_the_first_alone(tmp_path):
    eng = catalog(tmp_path)
    run(eng, "install", "alpha", extras=("series-patch",))
    alpha = tmp_path / "Games" / "Alpha"
    before = state(eng, alpha, "alpha")
    run(eng, "install", "bravo", extras=("series-patch",))
    assert state(eng, alpha, "alpha") == before


def test_adding_it_later_to_the_second_game_leaves_the_first_alone(tmp_path):
    eng = catalog(tmp_path)
    run(eng, "install", "alpha", extras=("series-patch",))
    run(eng, "install", "bravo")
    alpha, bravo = tmp_path / "Games" / "Alpha", tmp_path / "Games" / "Bravo"
    assert not (bravo / "data" / "bonus.dat").exists()
    before = state(eng, alpha, "alpha")
    r = run(eng, "extras", "bravo", extras=("series-patch",))
    assert r.action == "extras" and r.game["installed_extras"] == ["series-patch"]
    assert (bravo / "data" / "bonus.dat").read_bytes() == SHARED_V1["data/bonus.dat"]
    assert eng.ctx.records.load("bravo").files["data/bonus.dat"].owner == "series-patch"
    assert state(eng, alpha, "alpha") == before


# ---- changing one game's copy ----------------------------------------------------------------------------------


def test_uninstalling_one_game_leaves_the_others_copy_and_record_alone(tmp_path):
    eng, alpha, bravo = install_both(tmp_path)
    before = state(eng, bravo, "bravo")
    run(eng, "uninstall", "alpha")
    assert not alpha.exists()
    assert set(eng.ctx.records.all()) == {"bravo"}
    assert state(eng, bravo, "bravo") == before


def test_repairing_one_game_leaves_the_others_copy_and_record_alone(tmp_path):
    eng, alpha, bravo = install_both(tmp_path)
    (alpha / "data" / "bonus.dat").unlink()
    (bravo / "data" / "patch.dat").write_bytes(b"damaged in Bravo")  # only Bravo's own repair may fix this
    before = state(eng, bravo, "bravo")
    r = run(eng, "repair", "alpha")
    assert r.action == "repair"
    assert (alpha / "data" / "bonus.dat").read_bytes() == SHARED_V1["data/bonus.dat"]
    assert state(eng, bravo, "bravo") == before


def test_updating_one_game_leaves_the_others_copy_and_record_alone(tmp_path):
    eng, alpha, bravo = install_both(tmp_path)
    before = state(eng, bravo, "bravo")
    routes = eng.ctx.fetcher.routes
    eng.ctx.entries["alpha"] = game(routes, "alpha", "Alpha", ALPHA_V2, version="2.0")
    run(eng, "update", "alpha")
    assert (alpha / "alpha.exe").read_bytes() == b"alpha v2"
    assert set(eng.ctx.records.load("alpha").extras) == {"series-patch"}
    assert state(eng, bravo, "bravo") == before


def test_a_new_version_of_the_extra_reaches_only_the_game_being_updated(tmp_path):
    eng, alpha, bravo = install_both(tmp_path)
    before = state(eng, bravo, "bravo")
    routes = eng.ctx.fetcher.routes
    new = shared_extra(routes, SHARED_V2, version="2")
    eng.ctx.entries["series-patch"] = new
    eng.ctx.entries["alpha"] = game(routes, "alpha", "Alpha", ALPHA_V2, version="2.0")
    run(eng, "update", "alpha")
    assert snapshot(alpha)["data/bonus.dat"] == SHARED_V2["data/bonus.dat"]
    alpha_rec = eng.ctx.records.load("alpha")
    assert alpha_rec.extras["series-patch"].sha256 == new["sources"][0]["sha256"]
    assert alpha_rec.extras["series-patch"].version == "2"
    assert state(eng, bravo, "bravo") == before  # still version 1, until Bravo itself is updated
    assert eng.ctx.records.load("bravo").extras["series-patch"].version == "1"


def test_undoing_one_games_update_leaves_the_others_copy_and_record_alone(tmp_path):
    eng, alpha, bravo = install_both(tmp_path)
    routes = eng.ctx.fetcher.routes
    eng.ctx.entries["series-patch"] = shared_extra(routes, SHARED_V2, version="2")
    eng.ctx.entries["alpha"] = game(routes, "alpha", "Alpha", ALPHA_V2, version="2.0")
    run(eng, "update", "alpha")
    before = state(eng, bravo, "bravo")
    run(eng, "undo", "alpha")
    assert snapshot(alpha)["data/bonus.dat"] == SHARED_V1["data/bonus.dat"]
    assert eng.ctx.records.load("alpha").extras["series-patch"].version == "1"
    assert state(eng, bravo, "bravo") == before


def test_uninstalling_both_removes_each_copy(tmp_path):
    eng, alpha, bravo = install_both(tmp_path)
    run(eng, "uninstall", "alpha")
    run(eng, "uninstall", "bravo")
    assert not alpha.exists() and not bravo.exists()


# ---- offering it to each game ----------------------------------------------------------------------------------


def plan(eng, id, action="install"):
    return build_install_plan(id, eng.ctx.entries, eng.ctx.libraries, eng.ctx.records, FakeRegistry(), True, None,
                              "windows", action)


def test_extras_of_offers_it_to_each_game(tmp_path):
    eng = catalog(tmp_path)
    for id in ("alpha", "bravo"):
        found = extras_of(eng.ctx.entries[id], eng.ctx.entries, "windows")
        assert [(x.entry["id"], x.required) for x in found] == [("series-patch", False)]


def test_the_install_plan_offers_it_to_each_game(tmp_path):
    eng = catalog(tmp_path)
    size = eng.ctx.entries["series-patch"]["sources"][0]["size"]
    for id in ("alpha", "bravo"):
        extras = plan(eng, id).extras
        assert [(x.id, x.title, x.required, x.size) for x in extras] == [("series-patch", "Series Patch", False, size)]


def test_having_it_in_one_game_still_offers_it_to_the_other(tmp_path):
    eng = catalog(tmp_path)
    run(eng, "install", "alpha", extras=("series-patch",))
    run(eng, "install", "bravo")
    assert plan(eng, "alpha", "extras").extras == []
    assert [x.id for x in plan(eng, "bravo", "extras").extras] == ["series-patch"]
    assert [x.id for x in plan(eng, "alpha", "update").extras] == []
    assert [x.id for x in plan(eng, "bravo", "update").extras] == ["series-patch"]


def test_a_required_shared_extra_is_required_in_each_games_plan(tmp_path):
    eng = catalog(tmp_path, required=True)
    for id in ("alpha", "bravo"):
        assert [(x.id, x.required) for x in plan(eng, id).extras] == [("series-patch", True)]
