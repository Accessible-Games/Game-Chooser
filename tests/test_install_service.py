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

    Jobs for ids in `fail` fail without running the engine."""

    def __init__(self, engine_factory, listener, log_path):
        self.engine_factory, self.listener, self.log_path = engine_factory, listener, log_path
        self.engines = []
        self.fail = set()
        self.next_id = 1

    def submit(self, request, title):
        job = Job(self.next_id, request, title)
        self.next_id += 1
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
