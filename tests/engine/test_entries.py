import pytest

from engine.entries import (
    current_platform, extras_of, game_fields, is_mirror_url, mirror_url_for, platform_sources,
    resolve_dependencies, visible_games,
)
from engine.errors import DependencyError

MIRROR = "https://files.accessiblegames.net/files/" + "a" * 64 + "/g.zip"


def src(**kw):
    s = {"label": "Mirror", "url": MIRROR, "platform": "windows",
         "install": {"method": "zip"}, "sha256": "a" * 64, "size": 10}
    s.update(kw)
    return s


def entry(id, kind="game", sources=None, **kw):
    e = {"id": id, "kind": kind, "title": id.upper(), "sources": sources or [src()]}
    e.update(kw)
    return e


def test_minimal_entry_is_visible():
    e = {"id": "g", "kind": "game", "title": "G", "sources": [src()]}
    assert [x["id"] for x in visible_games({"g": e}, "windows")] == ["g"]
    assert game_fields(e) == {"title": "G", "developer": "", "genre": "", "year": "", "platforms": ["Windows"]}


def test_game_fields_full():
    e = entry("g", developer=["A", "B"], genres=["RPG", "Action"], year=1999)
    assert game_fields(e) == {"title": "G", "developer": "A, B", "genre": "RPG", "year": "1999", "platforms": ["Windows"]}


def test_unknown_values_hide_entry():
    for bad in [dict(kind="mod-pack"), dict(sources=[src(install={"method": "squashfs"})]),
                dict(sources=[src(install={"method": "installer", "installer": {"type": "wix", "mode": "extract"}})]),
                dict(sources=[src(install={"method": "installer", "installer": {"type": "msi", "mode": "x"}})]),
                dict(sources=[src(steps=[{"action": "shell", "cmd": "x"}])])]:
        assert visible_games({"g": entry("g", **bad)}, "windows") == []


@pytest.mark.parametrize("bad_id", ["Bad_ID", "UPPER", "a--b", "-a", "a-", "", "a b", "a.b", "a/b"])
def test_id_outside_the_catalogs_pattern_hides_the_entry(bad_id):
    """M9: ids become folder names and appear in elevated command lines; only ^[a-z0-9]+(-[a-z0-9]+)*$ is read."""
    assert visible_games({bad_id: entry(bad_id)}, "windows") == []
    assert visible_games({"ok-id-2": entry("ok-id-2")}, "windows")


def test_malformed_sources_steps_and_extras_hide_or_skip_never_raise():
    """Task 2/11 minors: a non-object where an object is due is read leniently, never an AttributeError."""
    for bad in [dict(sources=[1]), dict(sources="x"), dict(sources=[None, src()]),
                dict(sources=[src(install=5)]), dict(sources=[src(install={"method": "installer", "installer": "inno"})]),
                dict(sources=[src(steps=[1])]), dict(sources=[src(steps="copy")]), dict(sources=[src(steps=[None])])]:
        assert visible_games({"g": entry("g", **bad)}, "windows") == []
    g = entry("g", extras=[1, "x", None, {"id": "e", "required": True}])
    e = entry("e", kind="extra")
    assert [x["id"] for x in visible_games({"g": g, "e": e}, "windows")] == ["g"]
    assert [x.entry["id"] for x in extras_of(g, {"g": g, "e": e}, "windows")] == ["e"]
    assert platform_sources({"sources": [1, None, src(), "x"]}, "windows") == [src()]


def test_game_fields_label_the_platform_the_install_is_for():
    """M10: an entry listing a macOS source first still shows Windows for a Windows install."""
    e = entry("g", sources=[src(platform="macos"), src()])
    assert game_fields(e)["platforms"] == ["macOS"]
    assert game_fields(e, "windows")["platforms"] == ["Windows"]
    assert game_fields(entry("g", sources=[src(platform="macos")]), "windows")["platforms"] == []


def test_unknown_fields_are_ignored():
    assert visible_games({"g": entry("g", future_field={"x": 1})}, "windows")


def test_platform_filter_and_web():
    mac = entry("m", sources=[src(platform="macos")])
    web = entry("w", sources=[src(platform="web", install={"method": "url"})])
    assert [x["id"] for x in visible_games({"m": mac, "w": web}, "windows")] == ["w"]


def test_extras_and_runtimes_are_not_rows():
    assert visible_games({"x": entry("x", kind="extra", extends="g"), "r": entry("r", kind="runtime"),
                          "a": entry("a", kind="access-mod")}, "windows") == []


def test_dependencies_resolve_depth_first_and_dedupe():
    es = {"g": entry("g", dependencies=["a", "b"]), "a": entry("a", kind="runtime", dependencies=["b"]),
          "b": entry("b", kind="runtime")}
    assert resolve_dependencies("g", es) == ["b", "a"]


def test_cycle_and_missing_dependency_hide_game():
    es = {"g": entry("g", dependencies=["a"]), "a": entry("a", kind="runtime", dependencies=["g2"]),
          "g2": entry("g2", kind="runtime", dependencies=["a"])}
    with pytest.raises(DependencyError):
        resolve_dependencies("g", es)
    assert visible_games(es, "windows") == []
    assert visible_games({"h": entry("h", dependencies=["nope"])}, "windows") == []


def test_non_runtime_dependency_hides_game():
    es = {"g": entry("g", dependencies=["x"]), "x": entry("x", kind="game")}
    assert [e["id"] for e in visible_games(es, "windows")] == ["x"]


def test_unreadable_required_extra_hides_game_optional_is_skipped():
    g = entry("g", extras=[{"id": "r", "required": True}])
    assert visible_games({"g": g, "r": entry("r", kind="extra", sources=[src(steps=[{"action": "shell"}])])}, "windows") == []
    g2 = entry("g2", extras=[{"id": "o", "required": False}])
    es = {"g2": g2, "o": entry("o", kind="extra", sources=[src(steps=[{"action": "shell"}])])}
    assert visible_games(es, "windows") and extras_of(g2, es, "windows") == []


def test_extras_of_returns_usable_extras():
    g = entry("g", extras=[{"id": "o", "required": False}])
    o = entry("o", kind="extra")
    infos = extras_of(g, {"g": g, "o": o}, "windows")
    assert [(i.entry["id"], i.required) for i in infos] == [("o", False)]


def test_mirror_helpers():
    assert is_mirror_url("https://files.accessiblegames.net/files/abc/x.zip")
    assert not is_mirror_url("https://example.com/files/abc/x.zip")
    assert mirror_url_for("ab" * 32, "x y.zip") == f"https://files.accessiblegames.net/files/{'ab'*32}/x%20y.zip"


def test_current_platform_is_known():
    assert current_platform() in {"windows", "macos", "linux"}
