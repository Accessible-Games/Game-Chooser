from catalog_view import (action_kind, action_label, build_rows, link_items, description_text, filter_rows, initial_tab, link_at_line,
                          markdown_to_text, tree_categories)

SRC = [{"platform": "windows", "install": {"method": "zip"}, "url": "https://x/e.zip"}]
entombed = {"id": "entombed", "kind": "game", "title": "Entombed", "developer": ["Driftwood Entertainment"],
            "genres": ["Adventure"], "year": 2004, "flags": ["abandoned", "demo"], "sources": SRC}
troopanum = {"id": "troopanum", "kind": "game", "title": "Troopanum", "developer": ["Other Dev"],
             "genres": ["Strategy"], "year": 2006, "flags": ["abandoned", "requires-registration"], "sources": SRC}
minimal_entry = {"id": "minimal", "kind": "game", "title": "Minimal", "sources": []}
entry_with_links = {"id": "bsc", "title": "BSC", "description": "A **fun** game.", "links": {
    "agarchive": "https://www.agarchive.net/pages/devs/bsc.html", "homepage": "https://h.example",
    "purchase": "https://b.example"}}


def test_row_reads_title_developer_flags_installed():
    r = build_rows([entombed], {"entombed"})[0]
    assert (r.title, r.developer, r.flags_text) == ("Entombed", "Driftwood Entertainment",
                                                    "abandoned, demo, installed")
    assert build_rows([troopanum], set())[0].flags_text == "abandoned, needs registration"


def test_tree_categories_and_unknowns():
    cats = tree_categories(build_rows([minimal_entry], set()))
    assert cats["genre"] == {"Unknown Genre"} and cats["year"] == {"Unknown Year"}
    assert cats["developer"] == {"Unknown Developer"} and cats["installed"] == {"Not installed"}
    full = tree_categories(build_rows([entombed], {"entombed"}))
    assert full["platform"] == {"Windows"} and full["flags"] == {"abandoned", "demo"}
    assert full["year"] == {"2004"} and full["installed"] == {"Installed"}


def test_filter_or_within_and_across():
    rows = build_rows([entombed, troopanum, minimal_entry], set())
    both = {"developer": {"Driftwood Entertainment", "Other Dev"}}
    assert [r.id for r in filter_rows(rows, "", both)] == ["entombed", "troopanum"]
    assert [r.id for r in filter_rows(rows, "", {**both, "flags": {"demo"}})] == ["entombed"]
    assert [r.id for r in filter_rows(rows, "", {"genre": {"Unknown Genre"}})] == ["minimal"]
    assert len(filter_rows(rows, "", {"developer": set()})) == 3


def test_filter_search_title_and_developer_and_unknown_rule():
    rows = build_rows([entombed, troopanum, minimal_entry], set())
    assert [r.id for r in filter_rows(rows, "ENTOMB", {})] == ["entombed"]
    assert [r.id for r in filter_rows(rows, "driftwood", {})] == ["entombed"]
    assert filter_rows(rows, "adventure", {}) == []
    assert filter_rows(rows, "unknown", {}) == []


def test_markdown_to_text():
    md = ("# Entombed\n\n**Bold** and *italic* and `code`.\n\n- one\n- two\n\nSee [the site](https://x.org).\n\n"
          "2 * 3 = 6")
    assert markdown_to_text(md) == ("Entombed\n\nBold and italic and code.\n\n- one\n- two\n\n"
                                    "See the site (https://x.org).\n\n2 * 3 = 6")
    assert markdown_to_text("> quote\n\n![alt](i.png) <b>x</b> snake_case_name\n\n\n\n```\ncode\n```") == \
        "quote\n\nalt x snake_case_name\n\ncode"


def test_markdown_bold_labels_star_bullets_and_code_fences():
    assert markdown_to_text('**Note:** hi **Warning.** **"T"** **Free!** __Hey!__ a__b__c') == \
        'Note: hi Warning. "T" Free! Hey! a__b__c'
    assert markdown_to_text("* item one\n  + item two\n* item three") == "- item one\n  - item two\n- item three"
    assert markdown_to_text("```\n# comment\nx = *y*\n```\n\n# Head") == "# comment\nx = *y*\n\nHead"


def test_markdown_fences_verbatim_and_horizontal_rules():
    assert markdown_to_text("```\na\n\n\n\nb\n```") == "a\n\n\n\nb"
    assert markdown_to_text("```\n    indented\n```") == "    indented"
    assert markdown_to_text("one\n\n* * *\n\n---\n\ntwo") == "one\n\ntwo"


def test_description_text_links_on_own_lines():
    t = description_text(entry_with_links)
    assert t.startswith("A fun game.\n\n")
    assert t.endswith("Homepage: https://h.example\nBuy or register: https://b.example\n"
                      "Archived at agarchive: https://www.agarchive.net/pages/devs/bsc.html")
    assert link_at_line("Homepage: https://h.example") == "https://h.example" and link_at_line("Just text") is None
    assert description_text(None) == "" and description_text({"id": "x"}) == ""


def test_initial_tab():
    assert initial_tab(None, True) == "catalog" and initial_tab(None, False) == "library"
    assert initial_tab("catalog", False) == "catalog" and initial_tab("bogus", True) == "catalog"


WEB = {"id": "webgame", "kind": "game", "title": "Webgame", "sources": [
    {"platform": "web", "install": {"method": "url"}, "url": "https://play.example"}]}


def test_action_label_install_launch_add():
    rows = {r.id: r for r in build_rows([entombed, WEB], set())}
    assert action_label(rows["entombed"], "windows") == "Install Entombed"
    assert action_label(rows["webgame"], "windows") == "Add Webgame"
    assert action_kind(rows["webgame"], "linux") == "add"  # a web source suits every platform
    installed = {r.id: r for r in build_rows([entombed, WEB], {"entombed", "webgame"})}
    assert action_label(installed["entombed"], "windows") == "Launch Entombed"
    assert action_label(installed["webgame"], "windows") == "Launch Webgame"
    assert action_label(None) == "Install"


def test_action_kind_mixed_and_no_sources_is_install():
    mixed = dict(WEB, sources=WEB["sources"] + SRC)
    assert action_kind(build_rows([mixed], set())[0], "windows") == "install"
    assert action_kind(build_rows([minimal_entry], set())[0], "windows") == "install"


def test_link_items_in_menu_order_http_only():
    assert link_items(entry_with_links) == [
        ("Open homepage", "https://h.example"), ("Open purchase page", "https://b.example"),
        ("Open agarchive page", "https://www.agarchive.net/pages/devs/bsc.html")]
    assert link_items(None) == [] and link_items({"links": {"homepage": "javascript:x"}}) == []
