"""The install plan behind the confirmation dialog."""

from pathlib import Path

from engine import messages
from engine.plan import ExtraChoice, build_install_plan, dependency_line
from engine.records import InstalledSource, InstallRecord, RecordStore
from tests.engine.fakes import FakeRegistry

WIN = "windows"


def source(size=50, method="zip", platform=WIN, **install):
    s = {"platform": platform, "url": f"https://x/{size}.zip", "install": {"method": method, **install}}
    if size is not None:
        s["size"] = size
    return s


def game(sources=None, **fields):
    return {"id": "entombed", "kind": "game", "title": "Entombed", "sources": sources or [source()], **fields}


def extra(id, title, size=1, **fields):
    return {"id": id, "kind": "extra", "title": title, "sources": [source(size)], **fields}


def dx8vb(size=2):
    com = {**source(size, method="none"), "steps": [{"action": "register", "path": "dx8vb.dll"}]}
    return {"id": "dx8vb", "kind": "runtime", "title": "DirectX 8 for Visual Basic", "sources": [com],
            "detect": {"registry": {"hive": "HKCR", "key": "DxVBLibA.DirectX8"}}}


def vb6():
    installer = {"type": "other", "mode": "run", "silent_args": "/Q"}
    return {"id": "vb6", "kind": "runtime", "title": "Visual Basic 6 runtime",
            "sources": [source(3, method="installer", installer=installer)]}


def libs(*names):
    return [{"id": n, "path": f"C:/Games/{n}"} for n in names]


def plan(tmp_path, entry, entries=None, libraries=None, records=None, registry=None, shared=True, last=None,
         action="install", **kw):
    entries = {e["id"]: e for e in [entry, *(entries or [])]}
    return build_install_plan(entry["id"], entries, libraries if libraries is not None else libs("a"),
                              records or RecordStore(tmp_path / "records"), registry or FakeRegistry(), shared,
                              last, WIN, action, **kw)


def test_library_choices_default_to_last_library_id(tmp_path):
    libraries = libs("a", "b")
    p = plan(tmp_path, game(), libraries=libraries, last="b")
    assert [lib["id"] for lib in p.libraries] == ["a", "b"]
    assert p.default_library == "b"
    assert p.suggested_folder is None
    assert plan(tmp_path, game(), libraries=libraries, last="gone").default_library == "a"
    assert plan(tmp_path, game(), libraries=libraries).default_library == "a"


def test_no_library_suggests_userprofile_games(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", "C:/Users/x")
    p = plan(tmp_path, game(), libraries=[])
    assert p.libraries == [] and p.default_library is None
    assert p.suggested_folder == Path("C:/Users/x/Games")


def test_required_extras_listed_required(tmp_path):
    patch = extra("registration-patch-for-entombed-1-05", "Registration patch for Entombed 1.05", 1000)
    other = extra("manual", "The manual", None)
    g = game(extras=[{"id": patch["id"], "required": True}, {"id": "manual", "required": False}])
    p = plan(tmp_path, g, [patch, other])
    assert p.extras[0] == ExtraChoice("registration-patch-for-entombed-1-05",
                                      "Registration patch for Entombed 1.05", True, 1000)
    assert p.extras[1] == ExtraChoice("manual", "The manual", False, None)


def bass():
    return {"id": "bass", "kind": "runtime", "title": "BASS", "sources": [source(4)]}


def test_dependency_lines():
    assert dependency_line(dx8vb(), True).text == (
        "Also installs: DirectX 8 for Visual Basic (needs administrator permission)")
    # M2: a loose runtime with no register or HKLM step and no installer gets no prompt, so none is promised.
    assert dependency_line(bass(), True).text == "Also installs: BASS"
    assert dependency_line(bass(), False).text == "Also installs: BASS"
    assert dependency_line(bass(), True).installs and dependency_line(bass(), True).size == 4
    hklm = {**bass(), "sources": [{**source(4), "steps": [{"action": "registry", "hive": "HKLM", "key": "S", "name": "n",
                                                            "type": "REG_SZ", "value": "v"}]}]}
    assert dependency_line(hklm, False).text == "Also installs: BASS (needs administrator permission)"
    installer = {**bass(), "sources": [source(4, method="installer", installer={"type": "inno", "mode": "run"})]}
    assert dependency_line(installer, True).text == "Also installs: BASS (needs administrator permission)"
    flagged = {**bass(), "sources": [source(4), source(5, method="installer", platform="macos",
                                                      installer={"type": "inno", "mode": "run", "elevation": True})]}
    assert dependency_line(flagged, True).text == "Also installs: BASS (needs administrator permission)"
    private = dependency_line(dx8vb(), False)
    assert private.text == "Also installs: DirectX 8 for Visual Basic (a private copy for this game)"
    assert private.installs and private.size == 2
    skipped = dependency_line(vb6(), False)
    assert skipped.text == ("Needs Visual Basic 6 runtime, which isn't installed because shared components "
                            "are turned off in Preferences")
    assert not skipped.installs
    assert dependency_line(vb6(), True).installs


def test_present_dependencies_not_shown(tmp_path):
    g = game(dependencies=["dx8vb"])
    assert [d.id for d in plan(tmp_path, g, [dx8vb()]).dependencies] == ["dx8vb"]
    registry = FakeRegistry()
    registry.set("HKLM", "SOFTWARE\\Classes\\DxVBLibA.DirectX8", "", "REG_SZ", "x")
    assert plan(tmp_path, g, [dx8vb()], registry=registry).dependencies == []


def test_dependency_problem_becomes_a_warning(tmp_path):
    p = plan(tmp_path, game(dependencies=["nope"]))
    assert messages.DEPENDENCY_UNAVAILABLE.format(title="Entombed") in p.warnings
    a = {**dx8vb(), "dependencies": ["b"]}
    b = {**dx8vb(), "id": "b", "dependencies": ["dx8vb"]}
    p = plan(tmp_path, game(dependencies=["dx8vb"]), [a, b])
    assert messages.DEPENDENCY_CYCLE.format(title="Entombed") in p.warnings
    assert p.dependencies == []


def test_warnings(tmp_path):
    demo = plan(tmp_path, game(pricing={"model": "free-to-try", "url": "https://x/buy"}))
    assert demo.warnings == ["Price: Free to try. Buy or register: https://x/buy"]
    assert plan(tmp_path, game(pricing={"model": "free"})).warnings == []
    assert plan(tmp_path, game(flags=["demo"], links={"purchase": "https://x/buy"})).warnings == []  # v1 shapes
    interactive = plan(tmp_path, game([source(method="installer", installer={"type": "other", "mode": "run"})]))
    assert "You'll be guided through this game's own installer." in interactive.warnings
    assert interactive.interactive
    silent = plan(tmp_path, game([source(method="installer", installer={"type": "inno", "mode": "run"})]))
    assert not silent.interactive and silent.warnings == []
    assert not plan(tmp_path, game()).interactive


def test_download_size_sums_and_unknown(tmp_path):
    req = extra("req", "Req", 1)
    opt = extra("opt", "Opt", None)
    g = game(dependencies=["dx8vb"], extras=[{"id": "req", "required": True}, {"id": "opt"}])
    p = plan(tmp_path, g, [req, opt, dx8vb(2)])
    assert p.base_size == 50
    assert p.download_size(set()) == 53
    assert p.download_size({"opt"}) is None
    skipped = plan(tmp_path, game(dependencies=["vb6"]), [vb6()], shared=False)
    assert skipped.download_size(set()) == 50
    assert plan(tmp_path, game([source(None)])).download_size(set()) is None


def test_web_game_plan(tmp_path):
    p = plan(tmp_path, game([source(None, method="url", platform="web")]))
    assert p.is_web
    assert p.libraries == [] and p.suggested_folder is None and p.dependencies == []
    assert not plan(tmp_path, game()).is_web


def record(extras):
    src = InstalledSource("https://x/50.zip", "a" * 64, 50, None, None, 0, None)
    return InstallRecord(catalog_id="entombed", kind="game", library_id="a", folder="Entombed",
                         launch_path="e.exe", source=src, extras={x: src for x in extras}, files={}, steps=[],
                         method="extract")


def test_extras_action_lists_only_optional_not_installed(tmp_path):
    req = extra("req", "Req")
    one = extra("one", "One")
    two = extra("two", "Two")
    g = game(extras=[{"id": "req", "required": True}, {"id": "one"}, {"id": "two"}])
    store = RecordStore(tmp_path / "records")
    store.save(record(["two"]))
    p = plan(tmp_path, g, [req, one, two], records=store, action="extras")
    assert [x.id for x in p.extras] == ["one"]
    assert p.action == "extras" and p.libraries == [] and p.suggested_folder is None
    assert p.warnings == [] and p.download_size({"one"}) == 1
    assert p.download_size(set()) == 0


def test_update_lists_missing_extras_and_fixes_folder(tmp_path):
    req = extra("req", "Req")
    g = game(extras=[{"id": "req", "required": True}], pricing={"model": "free-to-try"})
    store = RecordStore(tmp_path / "records")
    store.save(record([]))
    p = plan(tmp_path, g, [req], records=store, action="update", libraries=libs("a", "b"))
    assert p.libraries == [] and p.suggested_folder is None
    assert p.warnings == []  # the price was shown when it was installed
    assert [x.id for x in p.extras] == ["req"]
    store.save(record(["req"]))
    assert plan(tmp_path, g, [req], records=store, action="update").extras == []


def test_entry_warning_shows_before_installing_and_updating(tmp_path):
    g = game(warning="  Some antivirus programs flag this game by mistake. ",
             pricing={"model": "one-time", "price": 5, "currency": "USD"})
    assert plan(tmp_path, g).warnings == ["Some antivirus programs flag this game by mistake.",
                                          "Price: Buy once, $5."]
    store = RecordStore(tmp_path / "records")
    store.save(record([]))
    assert plan(tmp_path, g, records=store, action="update").warnings == [
        "Some antivirus programs flag this game by mistake."]
    assert plan(tmp_path, g, records=store, action="extras").warnings == []
    assert plan(tmp_path, game(warning=["not text"])).warnings == []


def test_first_run_download_size_comes_from_the_source_installed(tmp_path):
    first = {**source(50), "first_run_download_size": 1000}
    assert plan(tmp_path, game([first])).first_run_size == 1000
    assert plan(tmp_path, game()).first_run_size == 0
    assert plan(tmp_path, game([{**source(50), "first_run_download_size": "big"}])).first_run_size == 0
    store = RecordStore(tmp_path / "records")
    store.save(record([]))
    assert plan(tmp_path, game([first]), records=store, action="update").first_run_size == 0
