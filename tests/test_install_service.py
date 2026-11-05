import json
import shutil
import tempfile

import pytest

import install_service
from engine import messages
from engine.errors import InstallFailed
from engine.index import DEFAULT_CATALOG_URL
from engine.queue import InstallQueue, Job
from engine.status import EXE_NOT_FOUND, LIBRARY_NOT_FOUND, READY, UPDATE_AVAILABLE
from install_service import InstallService
from library_manager import LibraryError
from models import Game
from tests.engine.fakes import (FakeElevator, FakeFetcher, FakeInstallerUI, FakeRegistry, ZipExtractor, publish,
                                served_source, zip_bytes)

MIRROR = "https://files.accessiblegames.net/files/"


def game_entry(routes, id="entombed", title="Entombed", files=None, **fields):
    files = files if files is not None else {f"{id}.exe": b"exe", "sounds/a.wav": b"wav"}
    source = served_source(routes, f"{MIRROR}{id}/{id}.zip", zip_bytes(files))
    return {"id": id, "kind": "game", "title": title, "developer": ["Ghorse"], "genres": ["Arcade"],
            "year": 2008, "version": "1.0", "sources": [source], **fields}


class FakeListener:
    def __init__(self):
        self.events = []

    def job_started(self, job):
        self.events.append(("started", job))

    def job_progress(self, job, progress):
        pass

    def job_finished(self, job, result):
        self.events.append(("finished", job, result))

    def job_failed(self, job, message):
        self.events.append(("failed", job, message))

    def job_cancelled(self, job):
        self.events.append(("cancelled", job))

    def queue_changed(self, waiting):
        pass

    def result_for(self, job):
        found = [e[2] for e in self.events if e[1] is job and e[0] in ("finished", "failed")]
        assert found and not isinstance(found[0], str), found
        return found[0]


class SyncQueue:
    """InstallQueue's interface, running each job at once on the caller's thread.

    Jobs for ids in `fail` fail without running the engine; jobs for ids in `hold` stay queued."""

    def __init__(self, engine_factory, listener, log_path):
        self.engine_factory, self.listener, self.log_path = engine_factory, listener, log_path
        self.engines = []
        self.fail = set()
        self.hold = set()
        self.waiting = []
        self.next_id = 1

    def job_for(self, catalog_id):
        return next((j for j in self.waiting if j.request.catalog_id == catalog_id), None)

    def submit(self, request, title):
        job = Job(self.next_id, request, title)
        self.next_id += 1
        if request.catalog_id in self.hold:
            self.waiting.append(job)
            return job
        self.listener.job_started(job)
        if request.catalog_id in self.fail:
            job.state = "failed"
            self.listener.job_failed(job, "It broke.")
            return job
        engine = self.engine_factory()
        self.engines.append(engine)
        try:
            result = engine.run(request, lambda p: None, lambda: False)
        except InstallFailed as e:
            job.state = "failed"
            self.listener.job_failed(job, e.message)
        else:
            job.state = "done"
            self.listener.job_finished(job, result)
        return job


class Rig:
    def __init__(self, manager, tmp_path):
        self.manager = manager
        self.tmp_path = tmp_path
        self.fetcher = FakeFetcher()
        self.routes = self.fetcher.routes  # sources served_source adds here are downloadable
        self.listener = FakeListener()
        self.service = InstallService(manager, self.listener, FakeInstallerUI(), fetcher=self.fetcher,
                                      extractor=ZipExtractor(), elevator=FakeElevator(), registry=FakeRegistry())

    def library(self, name="Games"):
        folder = self.tmp_path / name
        folder.mkdir(exist_ok=True)
        return self.manager.add_library(str(folder))

    def publish(self, *entries):
        publish(self.fetcher, DEFAULT_CATALOG_URL, list(entries))
        return self.service.refresh_index()

    def run(self, action, catalog_id, **kw):
        """Submit a job, then apply its result as the UI would; returns the job."""
        job = self.service.submit(action, catalog_id, **kw)
        self.service.apply_result(self.listener.result_for(job))
        return job


@pytest.fixture
def rig(isolated_manager, tmp_path, monkeypatch):
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(install_service, "InstallQueue", SyncQueue)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    monkeypatch.setattr(install_service, "default_runtimes_root", lambda: tmp_path / "runtimes")
    return Rig(isolated_manager, tmp_path)


def _games_json(manager):
    return json.loads(manager.games_file.read_text())


def test_offline_first_run_state(rig):
    state = rig.service.refresh_index()
    assert state.error == messages.CATALOG_OFFLINE_EMPTY and state.entries == {}
    assert rig.service.index_state is state and rig.service.entries == {}
    assert rig.service.visible_games() == [] and rig.service.installed_ids() == set()


def test_real_queue_logs_to_config_dir(isolated_manager):
    service = InstallService(isolated_manager, FakeListener(), FakeInstallerUI(), fetcher=FakeFetcher(),
                             extractor=ZipExtractor(), elevator=FakeElevator(), registry=FakeRegistry())
    try:
        assert isinstance(service.queue, InstallQueue)
        assert service.queue._log_path == isolated_manager.config_dir / "install.log"
    finally:
        assert service.queue.shutdown(5)


def test_load_cached_index_reads_what_refresh_saved(rig):
    rig.publish(game_entry(rig.routes))
    rig.fetcher.routes.clear()
    state = rig.service.load_cached_index()
    assert state.from_cache and set(state.entries) == {"entombed"}
    assert [e["id"] for e in rig.service.visible_games()] == ["entombed"]


def test_submit_install_then_apply_result_adds_game(rig):
    lib = rig.library()
    rig.publish(game_entry(rig.routes))
    job = rig.service.submit("install", "entombed", library_id=lib["id"])
    assert job.title == "Entombed" and job.request.action == "install"
    assert rig.manager.catalog_game("entombed") is None  # nothing changes until the result is applied
    rig.service.apply_result(rig.listener.result_for(job))
    game = rig.manager.catalog_game("entombed")
    assert game.library_id == lib["id"] and game.launch_path == "Entombed/entombed.exe"
    assert [g["catalog_id"] for g in _games_json(rig.manager) if g.get("catalog_id")] == ["entombed"]
    assert rig.service.installed_ids() == {"entombed"}
    assert rig.service.record_for(game).library_id == lib["id"]
    assert rig.service.entry_for(game)["title"] == "Entombed"
    assert rig.service.record_for(Game(title="Hand")) is None and rig.service.entry_for(Game(title="Hand")) is None


def test_installed_ids_include_web_catalog_games(rig):
    rig.manager.add_catalog_game({"title": "Web", "launch_path": "https://example.com/play", "catalog_id": "webby"})
    rig.manager.games.append(Game(title="Hand", launch_path="https://example.com/hand"))
    assert rig.service.installed_ids() == {"webby"}


def test_uninstall_result_removes_game(rig):
    lib = rig.library()
    rig.publish(game_entry(rig.routes))
    rig.run("install", "entombed", library_id=lib["id"])
    job = rig.run("uninstall", "entombed")
    assert job.request.action == "uninstall"
    assert rig.manager.catalog_game("entombed") is None
    assert not any(g.get("catalog_id") for g in _games_json(rig.manager))
    assert rig.service.installed_ids() == set()
    assert not (rig.tmp_path / "Games" / "Entombed").exists()


def test_statuses_for_mixed_library(rig):
    lib = rig.library()
    other = rig.library("Other")
    rig.publish(game_entry(rig.routes))
    rig.run("install", "entombed", library_id=lib["id"])
    (rig.tmp_path / "Games" / "Hand").mkdir()
    (rig.tmp_path / "Games" / "Hand" / "hand.exe").write_bytes(b"x")
    rig.manager.games += [
        Game(title="Hand", launch_path="Hand/hand.exe", library_id=lib["id"]),
        Game(title="Missing", launch_path="Missing/missing.exe", library_id=lib["id"]),
        Game(title="Away", launch_path="Away/away.exe", library_id=other["id"]),
        Game(title="Web", launch_path="https://example.com/play"),
    ]
    (rig.tmp_path / "Other" / "Away").mkdir()
    (rig.tmp_path / "Other" / "Away" / "away.exe").write_bytes(b"x")
    shutil.rmtree(rig.tmp_path / "Other")
    rig.publish(game_entry(rig.routes, files={"entombed.exe": b"exe v2"}))  # a newer download
    assert rig.service.statuses() == {
        (lib["id"], "Entombed/entombed.exe"): UPDATE_AVAILABLE,
        (lib["id"], "Hand/hand.exe"): READY,
        (lib["id"], "Missing/missing.exe"): EXE_NOT_FOUND,
        (other["id"], "Away/away.exe"): LIBRARY_NOT_FOUND,
        ("", "https://example.com/play"): READY,
    }


def test_adopt_launch_paths_after_index_load(rig):
    lib = rig.library()
    entry = game_entry(rig.routes, files={"entombed.exe": b"exe", "sub/other.exe": b"other"})
    rig.publish(entry)
    rig.run("install", "entombed", library_id=lib["id"])
    assert rig.manager.catalog_game("entombed").launch_path == "Entombed/entombed.exe"  # guessed
    counts = []
    adopt = rig.service.adopt_launch_paths
    rig.service.adopt_launch_paths = lambda: counts.append(adopt()) or counts[-1]
    rig.publish({**entry, "launch": {"windows": {"path": "sub/other.exe"}}})
    assert counts == [1]
    assert rig.manager.catalog_game("entombed").launch_path == "Entombed/sub/other.exe"
    assert [g["launch_path"] for g in _games_json(rig.manager)] == ["Entombed/sub/other.exe"]
    rec = rig.manager.records.load("entombed")
    assert rec.launch_path == "sub/other.exe" and rec.launch_from_catalog
    assert adopt() == 0
    rig.service.load_cached_index()
    assert counts == [1, 0]


def test_adopt_skips_unreachable_library(rig):
    lib = rig.library()
    entry = game_entry(rig.routes, files={"entombed.exe": b"exe", "sub/other.exe": b"other"})
    rig.publish(entry)
    rig.run("install", "entombed", library_id=lib["id"])
    shutil.rmtree(rig.tmp_path / "Games")
    rig.publish({**entry, "launch": {"windows": {"path": "sub/other.exe"}}})
    assert rig.service.adopt_launch_paths() == 0
    assert rig.manager.catalog_game("entombed").launch_path == "Entombed/entombed.exe"


def test_engine_context_uses_current_prefs(rig):
    lib = rig.library()
    rig.service.submit("install", "nothing", library_id=lib["id"])
    rig.manager.config["install"]["register_shared_components"] = False
    newer = rig.library("Newer")
    rig.service.submit("install", "nothing-else", library_id=newer["id"])
    first, second = rig.service.queue.engines
    assert first.ctx.register_shared_components is True and second.ctx.register_shared_components is False
    assert [l["id"] for l in second.ctx.libraries] == [lib["id"], newer["id"]]
    assert second.ctx.work_root == rig.tmp_path / "temp" / "GameChooser"
    assert second.ctx.runtimes_root == rig.tmp_path / "runtimes"
    assert second.ctx.records is rig.manager.records


def test_last_library_remembered(rig):
    lib = rig.library()
    rig.publish(game_entry(rig.routes))
    rig.service.submit("install", "entombed", library_id=lib["id"])
    assert rig.manager.config["install"]["last_library"] == lib["id"]
    saved = json.loads(rig.manager.config_file.read_text())
    assert saved["install"]["last_library"] == lib["id"]
    assert rig.service.plan("entombed").default_library == lib["id"]


def test_last_library_not_remembered_when_submit_fails(rig):
    lib = rig.library()
    other = rig.library("Other")
    rig.publish(game_entry(rig.routes))
    rig.service.submit("install", "entombed", library_id=lib["id"])

    def refuse(request, title):
        raise RuntimeError("The install queue has been shut down.")
    rig.service.queue.submit = refuse
    with pytest.raises(RuntimeError):
        rig.service.submit("install", "entombed", library_id=other["id"])
    assert rig.manager.config["install"]["last_library"] == lib["id"]
    assert json.loads(rig.manager.config_file.read_text())["install"]["last_library"] == lib["id"]


def test_plan_and_blocked(rig):
    lib = rig.library()
    rig.publish(game_entry(rig.routes))
    plan = rig.service.plan("entombed")
    assert plan.catalog_id == "entombed" and [l["id"] for l in plan.libraries] == [lib["id"]]
    assert rig.service.blocked("entombed", rig.tmp_path / "Games") is None
    (rig.tmp_path / "Games" / "Entombed").mkdir()
    assert rig.service.blocked("entombed", rig.tmp_path / "Games") == messages.FOLDER_EXISTS.format(
        folder="Entombed", library="Games")


def test_ensure_library_creates_folder(rig):
    folder = rig.tmp_path / "New" / "Games"
    lib = rig.service.ensure_library(folder)
    assert folder.is_dir() and rig.manager.library_by_id(lib["id"])["path"] == str(folder)


def test_remove_library_without_catalog_games(rig):
    lib = rig.library()
    (rig.tmp_path / "Games" / "Hand").mkdir()
    (rig.tmp_path / "Games" / "Hand" / "hand.exe").write_bytes(b"x")
    rig.manager.games.append(Game(title="Hand", launch_path="Hand/hand.exe", library_id=lib["id"]))
    assert rig.service.remove_library(lib["id"], delete_player_data=False) == []
    assert rig.manager.library_by_id(lib["id"]) is None
    assert [g.title for g in rig.manager.games] == ["Hand"]
    assert (rig.tmp_path / "Games" / "Hand" / "hand.exe").exists()


def _two_installed(rig):
    lib = rig.library()
    rig.publish(game_entry(rig.routes), game_entry(rig.routes, id="pawprints", title="Pawprints"))
    rig.run("install", "entombed", library_id=lib["id"])
    rig.run("install", "pawprints", library_id=lib["id"])
    return lib


def test_remove_library_uninstalls_first(rig):
    lib = _two_installed(rig)
    (rig.tmp_path / "Games" / "Entombed" / "save.dat").write_bytes(b"mine")  # kept, so its record stays
    jobs = rig.service.remove_library(lib["id"], delete_player_data=False)
    assert [(j.request.action, j.request.catalog_id) for j in jobs] == [("uninstall", "entombed"),
                                                                         ("uninstall", "pawprints")]
    assert lib["id"] in rig.service.pending_removal
    assert rig.manager.records.load("entombed").uninstalled
    rig.service.apply_result(rig.listener.result_for(jobs[0]))
    assert rig.manager.library_by_id(lib["id"]) is not None  # pawprints is still in it
    rig.service.apply_result(rig.listener.result_for(jobs[1]))
    assert rig.manager.library_by_id(lib["id"]) is None and rig.service.pending_removal == set()
    assert rig.manager.games == [] and not rig.manager.records.exists("entombed")
    assert (rig.tmp_path / "Games" / "Entombed" / "save.dat").exists()  # the player's file stays on disk


def test_remove_library_failure_keeps_it(rig):
    lib = _two_installed(rig)
    rig.service.queue.fail.add("pawprints")
    jobs = rig.service.remove_library(lib["id"], delete_player_data=False)
    rig.service.job_failed(jobs[1])
    assert lib["id"] not in rig.service.pending_removal
    rig.service.apply_result(rig.listener.result_for(jobs[0]))
    assert rig.manager.library_by_id(lib["id"]) is not None
    assert [g.catalog_id for g in rig.manager.games] == ["pawprints"]


def test_remove_unreachable_library_refused(rig):
    lib = rig.library()
    rig.publish(game_entry(rig.routes))
    rig.run("install", "entombed", library_id=lib["id"])
    shutil.rmtree(rig.tmp_path / "Games")
    engines = len(rig.service.queue.engines)
    with pytest.raises(LibraryError) as raised:
        rig.service.remove_library(lib["id"], delete_player_data=False)
    assert str(raised.value) == messages.LIBRARY_REMOVE_UNREACHABLE
    assert rig.manager.library_by_id(lib["id"]) is not None and lib["id"] not in rig.service.pending_removal
    assert len(rig.service.queue.engines) == engines
    empty = rig.library("Empty")
    shutil.rmtree(rig.tmp_path / "Empty")
    assert rig.service.remove_library(empty["id"], delete_player_data=False) == []  # nothing to uninstall
    assert rig.manager.library_by_id(empty["id"]) is None


def _guessed_install(rig, *ids):
    """Install games whose zips hold two exes, so the engine guesses the root one; returns (lib, entries)."""
    lib = rig.library()
    entries = [game_entry(rig.routes, id=cid, title=cid.title(), files={f"{cid}.exe": b"exe", "sub/other.exe": b"o"})
               for cid in ids]
    rig.publish(*entries)
    for cid in ids:
        rig.run("install", cid, library_id=lib["id"])
    return lib, entries


def test_fetch_index_changes_nothing_until_taken(rig):
    _, (entry,) = _guessed_install(rig, "entombed")
    before_entries, before_json = rig.service.entries, _games_json(rig.manager)
    publish(rig.fetcher, DEFAULT_CATALOG_URL, [{**entry, "launch": {"windows": {"path": "sub/other.exe"}}}])
    state = rig.service.fetch_index()
    assert state.entries["entombed"]["launch"] and state.error is None
    assert rig.service.entries is before_entries and "launch" not in rig.service.entries["entombed"]
    assert rig.service.index_state is not state
    assert rig.manager.catalog_game("entombed").launch_path == "Entombed/entombed.exe"
    assert _games_json(rig.manager) == before_json
    assert not rig.manager.records.load("entombed").launch_from_catalog
    assert rig.service.take_index(state) is state
    assert rig.service.index_state is state and rig.service.entries is state.entries
    assert rig.manager.catalog_game("entombed").launch_path == "Entombed/sub/other.exe"


def test_adopt_skips_a_game_with_a_job(rig):
    _, (entry,) = _guessed_install(rig, "entombed")
    rig.service.queue.hold.add("entombed")
    rig.service.submit("repair", "entombed")
    rig.publish({**entry, "launch": {"windows": {"path": "sub/other.exe"}}})
    assert rig.service.adopt_launch_paths() == 0
    assert rig.manager.catalog_game("entombed").launch_path == "Entombed/entombed.exe"
    assert not rig.manager.records.load("entombed").launch_from_catalog


def test_malformed_entry_does_not_stop_adoption(rig):
    _, (first, second) = _guessed_install(rig, "entombed", "pawprints")
    rig.publish({**first, "launch": {"windows": "sub/other.exe"}},  # not an object: adopting it raises
                {**second, "launch": {"windows": {"path": "sub/other.exe"}}})
    assert rig.manager.catalog_game("entombed").launch_path == "Entombed/entombed.exe"
    assert rig.manager.catalog_game("pawprints").launch_path == "Pawprints/sub/other.exe"


def test_remove_library_refused_while_a_game_is_busy(rig):
    lib = _two_installed(rig)
    rig.service.queue.hold.add("pawprints")
    rig.service.submit("update", "pawprints")
    submitted = rig.service.queue.next_id
    with pytest.raises(LibraryError) as raised:
        rig.service.remove_library(lib["id"], delete_player_data=True)
    assert str(raised.value) == messages.LIBRARY_REMOVE_BUSY.format(title="Pawprints")
    assert rig.service.queue.next_id == submitted and rig.service.pending_removal == set()
    assert rig.manager.library_by_id(lib["id"]) is not None
    assert {g.catalog_id for g in rig.manager.games} == {"entombed", "pawprints"}


def test_nothing_installs_into_a_library_being_removed(rig):
    lib = _two_installed(rig)
    other = rig.library("Other")
    rig.publish(game_entry(rig.routes), game_entry(rig.routes, id="pawprints", title="Pawprints"),
                game_entry(rig.routes, id="third", title="Third"))
    rig.service.remove_library(lib["id"], delete_player_data=False)
    assert lib["id"] in rig.service.pending_removal
    assert rig.service.blocked("third", rig.tmp_path / "Games") == messages.LIBRARY_BEING_REMOVED
    assert rig.service.blocked("third", rig.tmp_path / "Other") is None


def _untrack(rig, catalog_id):
    rec = rig.manager.records.load(catalog_id)
    rec.tracked = False
    rig.manager.records.save(rec)


def test_remove_library_uninstalls_tracked_and_keeps_untracked_as_hand_added(rig, isolated_manager_factory):
    lib = _two_installed(rig)
    _untrack(rig, "pawprints")
    assert [[g.catalog_id for g in part] for part in rig.service.library_games(lib["id"])] == \
        [["entombed"], ["pawprints"]]
    jobs = rig.service.remove_library(lib["id"], delete_player_data=False)
    assert [(j.request.action, j.request.catalog_id) for j in jobs] == [("uninstall", "entombed")]
    assert rig.manager.catalog_game("pawprints") is not None  # kept as it is until the library actually goes
    rig.service.apply_result(rig.listener.result_for(jobs[0]))
    assert rig.manager.library_by_id(lib["id"]) is None and rig.service.pending_removal == set()
    kept = [g for g in rig.manager.games if g.title == "Pawprints"]
    folder = rig.tmp_path / "Games" / "Pawprints"
    assert len(kept) == 1 and kept[0].catalog_id == "" and kept[0].library_id == ""
    assert kept[0].launch_path == str(folder / "pawprints.exe")
    assert not rig.manager.records.exists("pawprints") and (folder / "pawprints.exe").exists()  # files untouched
    assert not (rig.tmp_path / "Games" / "Entombed").exists()
    restarted = isolated_manager_factory()  # still a game added by hand at the next start
    assert [(g.title, g.catalog_id, g.library_id) for g in restarted.games] == [("Pawprints", "", "")]


def test_remove_library_with_only_untracked_games_goes_at_once(rig):
    lib = _two_installed(rig)
    _untrack(rig, "entombed")
    rec = rig.manager.records.load("pawprints")
    rig.manager.records.delete("pawprints")  # record-less
    engines = len(rig.service.queue.engines)
    assert rig.service.remove_library(lib["id"], delete_player_data=True) == []
    assert len(rig.service.queue.engines) == engines  # nothing uninstalled
    assert rig.manager.library_by_id(lib["id"]) is None and rig.service.pending_removal == set()
    assert sorted((g.title, g.catalog_id, g.library_id) for g in rig.manager.games) == \
        [("Entombed", "", ""), ("Pawprints", "", "")]
    assert not rig.manager.records.exists("entombed")
    assert (rig.tmp_path / "Games" / "Entombed" / "entombed.exe").exists()
    assert (rig.tmp_path / "Games" / "Pawprints" / "pawprints.exe").exists() and rec is not None


def test_remove_library_refused_while_an_untracked_game_is_busy(rig):
    lib = _two_installed(rig)
    _untrack(rig, "pawprints")
    rig.service.queue.hold.add("pawprints")
    rig.service.submit("repair", "pawprints")
    with pytest.raises(LibraryError):
        rig.service.remove_library(lib["id"], delete_player_data=False)
    assert rig.manager.library_by_id(lib["id"]) is not None and rig.manager.catalog_game("pawprints") is not None


def test_unrelated_failed_job_leaves_a_pending_removal(rig):
    lib = _two_installed(rig)
    rig.publish(game_entry(rig.routes), game_entry(rig.routes, id="pawprints", title="Pawprints"),
                game_entry(rig.routes, id="third", title="Third"))
    rig.run("install", "third", library_id=lib["id"])
    _untrack(rig, "third")  # kept, not uninstalled
    rig.service.queue.hold.update({"entombed", "pawprints"})
    jobs = rig.service.remove_library(lib["id"], delete_player_data=False)
    assert [j.request.catalog_id for j in jobs] == ["entombed", "pawprints"]
    rig.service.queue.fail.add("third")
    other = rig.service.submit("repair", "third")  # a job in the same library that isn't one of its uninstalls
    rig.service.job_failed(other)
    assert lib["id"] in rig.service.pending_removal
    rig.service.job_failed(jobs[1])  # one of its uninstalls: the library stays
    assert lib["id"] not in rig.service.pending_removal


def test_forget_deletes_the_record_first(rig, monkeypatch):
    _two_installed(rig)
    def broken(catalog_id):
        raise PermissionError("locked")
    monkeypatch.setattr(rig.manager.records, "delete", broken)
    with pytest.raises(OSError):
        rig.service.forget("entombed")
    assert rig.manager.catalog_game("entombed") is not None  # nothing changed
    assert "entombed" in [g["catalog_id"] for g in _games_json(rig.manager)]


def test_player_files_counts_what_the_player_added(rig):
    _two_installed(rig)
    assert rig.service.player_files("entombed") == 0
    (rig.tmp_path / "Games" / "Entombed" / "save.dat").write_bytes(b"mine")
    (rig.tmp_path / "Games" / "Entombed" / "sounds" / "a.wav").write_bytes(b"changed")  # changed: kept too
    assert rig.service.player_files("entombed") == 2
    assert rig.service.player_files("nothing") == 0
    shutil.rmtree(rig.tmp_path / "Games")  # an unplugged drive: nothing to count
    assert rig.service.player_files("entombed") == 0


def test_forget_an_untracked_game_keeps_its_files_and_it_stays_gone(rig, isolated_manager_factory):
    lib = _two_installed(rig)
    rec = rig.manager.records.load("entombed")
    rec.tracked = False
    rig.manager.records.save(rec)
    rig.service.forget("entombed")
    assert rig.manager.catalog_game("entombed") is None and not rig.manager.records.exists("entombed")
    assert [g["catalog_id"] for g in _games_json(rig.manager) if g.get("catalog_id")] == ["pawprints"]
    assert (rig.tmp_path / "Games" / "Entombed" / "entombed.exe").exists()  # its files stay where they are
    assert rig.manager.records.exists("pawprints") and rig.manager.library_by_id(lib["id"]) is not None
    restarted = isolated_manager_factory()  # reconcile at the next start doesn't bring it back
    assert restarted.catalog_game("entombed") is None and restarted.catalog_game("pawprints") is not None


def test_changed_catalog_url_is_used_by_the_next_fetch(rig):
    other = "http://127.0.0.1:9/"
    publish(rig.fetcher, DEFAULT_CATALOG_URL, [game_entry(rig.routes)])
    publish(rig.fetcher, other, [game_entry(rig.routes, id="elsewhere", title="Elsewhere")])
    assert set(rig.service.fetch_index().entries) == {"entombed"}
    rig.manager.config["install"]["catalog_url"] = other
    assert set(rig.service.fetch_index().entries) == {"elsewhere"}
    assert rig.fetcher.calls[other + "index-meta.json"] >= 1


def _runtime_record(rig, catalog_id="dx8vb"):
    from engine.records import InstalledSource, InstallRecord
    rec = InstallRecord(catalog_id=catalog_id, kind="runtime", library_id="",
                        folder=str(rig.tmp_path / "runtimes" / catalog_id), launch_path="",
                        source=InstalledSource("https://x/r.zip", "a" * 64, 1, None, None, 0, "1"), extras={},
                        files={}, steps=[], method="zip")
    rig.manager.records.save(rec)
    return rec


def test_installed_ids_skip_runtime_records(rig):
    lib = rig.library()
    rig.publish(game_entry(rig.routes))
    rig.run("install", "entombed", library_id=lib["id"])
    _runtime_record(rig)
    assert rig.service.installed_ids() == {"entombed"}
    assert set(rig.service.statuses()) == {(lib["id"], "Entombed/entombed.exe")}


def test_runtime_records_are_never_offered_forgotten_or_deleted(rig):
    from library_actions import catalog_menu_items, delete_kind, is_tracked
    lib = rig.library()
    runtime = _runtime_record(rig)
    # a hand-edited games.json row naming the runtime, in a library (reconcile would unlink it at the next start)
    game = Game(title="DX", launch_path="DX/dx.exe", library_id=lib["id"], catalog_id="dx8vb")
    rig.manager.games.append(game)
    assert rig.service.record_for(game) is None and rig.service.player_files("dx8vb") == 0
    assert delete_kind(game, rig.service.record_for(game)) == "delete"
    assert delete_kind(game, runtime) == "delete" and not is_tracked(runtime)
    assert catalog_menu_items(game, runtime, None, update_available=False, extras_to_add=False) == []
    assert rig.service.library_games(lib["id"]) == ([], [game])
    rig.service.forget("dx8vb")
    assert rig.manager.records.load("dx8vb") == runtime and rig.manager.catalog_game("dx8vb") is None
    rig.manager.games.append(game)
    assert rig.service.remove_library(lib["id"], delete_player_data=False) == []
    assert rig.manager.records.load("dx8vb") == runtime and rig.manager.library_by_id(lib["id"]) is None
    assert rig.service.record_for(Game(title="Odd", launch_path="https://example.com/", catalog_id="a/b")) is None
