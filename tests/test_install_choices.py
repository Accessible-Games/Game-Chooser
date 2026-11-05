from pathlib import Path
from types import SimpleNamespace as NS

from engine.plan import DependencyLine, ExtraChoice, InstallPlan
from install_choices import (build_choice, chosen_optional, default_library_index, extra_label, intro_text,
                             library_choice_label, ok_label, selected_library, window_title)

LIBS = [{"id": "a", "path": "C:/Games"}, {"id": "b", "path": "D:/Games"}]


def make(**kw):
    base = dict(catalog_id="g", title="Game", action="install", libraries=LIBS, default_library="b", base_size=1048576 * 10,
                extras=[ExtraChoice("req", "Patch", True, 1048576), ExtraChoice("opt", "Music", False, 1048576 * 5)],
                dependencies=[DependencyLine("rt", "Also installs: RT", True, 0),
                              DependencyLine("sk", "Needs X", False, None)])
    base.update(kw)
    return InstallPlan(**base)


def test_titles_and_buttons():
    assert window_title(make()) == "Install Game"
    assert window_title(make(action="update")) == "Update Game"
    assert window_title(make(action="extras")) == "Add extras to Game"
    assert ok_label(make(action="extras")) == "Add extras"
    assert ok_label(make()) == "Install"


def test_library_labels_and_default():
    assert library_choice_label(LIBS[0]) == "Games (C:/Games)"
    assert default_library_index(make()) == 1
    assert default_library_index(make(default_library="zzz")) == 0
    assert selected_library(make(libraries=[]), 0) is None
    assert selected_library(make(), 1) == LIBS[1]


def test_intro_size_updates_and_lists_includes():
    plan = make()
    titles = {"rt": "Runtime"}
    assert intro_text(plan, set(), titles) == "Download size: 11.0 megabytes. Includes: Patch, Runtime"
    assert intro_text(plan, {"opt"}, titles).startswith("Download size: 16.0 megabytes")


def test_intro_without_includes_and_unknown_size():
    plan = make(extras=[], dependencies=[], base_size=None)
    assert intro_text(plan, set(), {}) == "Download size: unknown size"


def test_extra_labels_and_choice():
    plan = make()
    assert extra_label(plan.extras[0]) == "Patch (required, always installed)"
    assert extra_label(plan.extras[1]) == "Music"
    assert chosen_optional(plan, {"req": True, "opt": True}) == ("opt",)
    assert chosen_optional(plan, {"opt": False}) == ()
    choice = build_choice(plan, LIBS[1], {"opt": True})
    assert (choice.library_id, choice.library_path, choice.extras) == ("b", Path("D:/Games"), ("opt",))
