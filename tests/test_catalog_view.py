from catalog_view import (action_kind, action_label, build_rows, link_items, description_text, entry_links, filter_rows,
                          homepage_item, initial_tab, link_at_line, install_candidates, markdown_to_text,
                          pick_index, tree_categories, tree_values)

SRC = [{"platform": "windows", "install": {"method": "zip"}, "url": "https://x/e.zip"}]
entombed = {"id": "entombed", "kind": "game", "title": "Entombed", "developer": ["Driftwood Entertainment"],
            "genres": ["Adventure", "Puzzle"], "year": 2004, "unsupported": True, "tags": ["Horror"],
            "pricing": {"model": "free-to-try"}, "sources": SRC}
troopanum = {"id": "troopanum", "kind": "game", "title": "Troopanum", "developer": ["Other Dev"],
             "genres": ["Strategy"], "year": 2006, "unsupported": True, "tags": ["Multiplayer", "Survival"],
             "languages": ["en", "fr", "EN", "xx"],
             "pricing": {"model": "one-time", "price": 20, "currency": "USD"}, "sources": SRC}
minimal_entry = {"id": "minimal", "kind": "game", "title": "Minimal", "sources": []}
entry_with_links = {"id": "bsc", "title": "BSC", "description": "A **fun** game.", "links": [
    {"url": "https://h.example", "label": "Homepage"}, {"url": "https://www.forum.example/t/1"},
    {"url": "https://www.agarchive.net/pages/devs/bsc.html", "label": "Archived at agarchive"}],
    "pricing": {"model": "free-to-try", "url": "https://b.example", "donate_url": "https://d.example"}}
V1_SHAPES = {"id": "old", "kind": "game", "title": "Old", "flags": ["demo", "abandoned"],
             "links": {"homepage": "https://h.example", "purchase": "https://b.example"}, "sources": SRC}


def test_row_reads_title_developer_genre_price_details():
    r = build_rows([entombed], {"entombed"})[0]
    assert (r.title, r.developer, r.genre, r.price) == ("Entombed", "Driftwood Entertainment", "Adventure",
                                                        "Free to try")
    assert r.details_text == "Horror, no longer supported, installed"
    t = build_rows([troopanum], set())[0]
    assert (t.price, t.details_text) == ("Buy once, $20", "Multiplayer, Survival, no longer supported")
    assert t.languages == ["English", "French", "xx"]  # names; a code with no name shows as written
    m = build_rows([minimal_entry], set())[0]
    assert (m.genre, m.price, m.details_text, m.languages) == ("", "", "", [])


def test_tree_categories_and_unknowns():
    cats = tree_categories(build_rows([minimal_entry], set()))
    assert cats["genre"] == {"Unknown Genre"} and cats["year"] == {"Unknown Year"}
    assert cats["developer"] == {"Unknown Developer"} and cats["installed"] == {"Not installed"}
    assert cats["tag"] == cats["price"] == cats["language"] == cats["support"] == set()  # no "unknown" values
    full = tree_categories(build_rows([entombed, troopanum], {"entombed"}))
    assert full["platform"] == {"Windows"} and full["genre"] == {"Adventure", "Puzzle", "Strategy"}
    assert full["tag"] == {"Horror", "Multiplayer", "Survival"} and full["support"] == {"No longer supported"}
    assert full["price"] == {"Free to try", "Can be played for free", "Buy once"}
    assert full["language"] == {"English", "French", "xx"}
    assert full["year"] == {"2004", "2006"} and full["installed"] == {"Installed", "Not installed"}


def test_filter_or_within_and_across():
    rows = build_rows([entombed, troopanum, minimal_entry], set())
    both = {"developer": {"Driftwood Entertainment", "Other Dev"}}
    assert [r.id for r in filter_rows(rows, "", both)] == ["entombed", "troopanum"]
    assert [r.id for r in filter_rows(rows, "", {**both, "price": {"Can be played for free"}})] == ["entombed"]
    assert [r.id for r in filter_rows(rows, "", {"genre": {"Unknown Genre"}})] == ["minimal"]
    assert [r.id for r in filter_rows(rows, "", {"genre": {"Puzzle"}})] == ["entombed"]  # any genre, not only the first
    assert [r.id for r in filter_rows(rows, "", {"tag": {"Survival", "Horror"}})] == ["entombed", "troopanum"]
    assert [r.id for r in filter_rows(rows, "", {"language": {"French"}})] == ["troopanum"]
    assert [r.id for r in filter_rows(rows, "", {"support": {"No longer supported"}})] == ["entombed", "troopanum"]
    assert [r.id for r in filter_rows(rows, "", {"price": {"Buy once"}})] == ["troopanum"]
    assert len(filter_rows(rows, "", {"developer": set()})) == 3


def test_filter_search_title_developer_contains_and_unknown_rule():
    rows = build_rows([entombed, troopanum, minimal_entry], set())
    assert [r.id for r in filter_rows(rows, "ENTOMB", {})] == ["entombed"]
    assert [r.id for r in filter_rows(rows, "driftwood", {})] == ["entombed"]
    assert filter_rows(rows, "adventure", {}) == []
    assert filter_rows(rows, "unknown", {}) == []
    collection = {"id": "pack", "kind": "game", "title": "Classic Pack", "contains": ["Lone Wolf", "Shades of Doom"],
                  "sources": SRC}
    rows = build_rows([collection, entombed], set())
    assert [r.id for r in filter_rows(rows, "shades", {})] == ["pack"]  # a collection's games are searched too


def test_price_branch_order_and_can_be_played_for_free():
    values = {"Buy once", "Free", "Subscription", "Can be played for free", "Free to try", "Odd"}
    assert tree_values("price", values) == ["Can be played for free", "Free", "Free to try", "Buy once",
                                            "Subscription", "Odd"]
    assert tree_values("genre", {"b", "A", "c"}) == ["A", "b", "c"]
    pwyw = {"id": "p", "title": "P", "pricing": {"model": "pay-what-you-want", "price": 0, "currency": "USD"}}
    paid = {"id": "q", "title": "Q", "pricing": {"model": "pay-what-you-want", "price": 3, "currency": "USD"}}
    sub = {"id": "s", "title": "S", "pricing": {"model": "subscription", "period": "month"}}
    rows = build_rows([pwyw, paid, sub], set())
    assert [r.id for r in filter_rows(rows, "", {"price": {"Can be played for free"}})] == ["p"]
    assert [r.id for r in filter_rows(rows, "", {"price": {"Pay what you want"}})] == ["p", "q"]


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


def test_description_text_details_then_links_on_own_lines():
    t = description_text(entry_with_links)
    assert t == ("A fun game.\n\n"
                 "Price: Free to try\n\n"
                 "Homepage: https://h.example\n"
                 "forum.example: https://www.forum.example/t/1\n"
                 "Archived at agarchive: https://www.agarchive.net/pages/devs/bsc.html\n"
                 "Buy or register: https://b.example\n"
                 "Support the developer: https://d.example")
    for line in t.splitlines()[4:]:
        assert link_at_line(line) == line.split(": ", 1)[1]
    assert link_at_line("Just text") is None and link_at_line("Price: Free to try") is None
    assert description_text(None) == "" and description_text({"id": "x"}) == ""


def test_description_text_lists_every_detail():
    entry = {"id": "x", "description": "Body.", "genres": ["Puzzle"], "tags": ["Idle"], "languages": ["ja"],
             "contains": ["One", "Two"], "unsupported": True,
             "pricing": {"model": "one-time", "price": 5, "currency": "USD", "checked": "2026-10-31",
                         "note": "Full version adds 12 levels"}}
    assert description_text(entry) == ("Body.\n\n"
                                       "Price: Buy once, $5. Price as of 31 October 2026\n"
                                       "Full version adds 12 levels\n"
                                       "Genre: Puzzle\n"
                                       "Tags: Idle\n"
                                       "Languages: Japanese\n"
                                       "Games included: One, Two\n"
                                       "No longer supported by its developer.")
    assert "Genres: Adventure, Puzzle" in description_text(entombed)


def test_version_one_shapes_are_skipped():
    row = build_rows([V1_SHAPES], set())[0]
    assert row.details_text == "" and row.price == ""
    assert not any(tree_categories([row])[key] for key in ("tag", "price", "language", "support"))
    assert description_text(V1_SHAPES) == "" and link_items(V1_SHAPES) == [] and entry_links(V1_SHAPES) == []
    assert homepage_item(V1_SHAPES) is None


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
        ("Open Homepage", "https://h.example"), ("Open forum.example", "https://www.forum.example/t/1"),
        ("Open Archived at agarchive", "https://www.agarchive.net/pages/devs/bsc.html"),
        ("Open purchase page", "https://b.example"), ("Open donation page", "https://d.example")]
    assert link_items(None) == []
    assert link_items({"links": [{"url": "javascript:x"}, {"label": "No url"}, "x", {"url": "https://"}]}) == []
    same = {"links": [{"url": "https://b.example", "label": "Store"}],
            "pricing": {"model": "one-time", "url": "https://b.example"}}
    assert link_items(same) == [("Open Store", "https://b.example")]  # each address once


def test_homepage_item():
    assert homepage_item(entry_with_links) == ("Open Homepage", "https://h.example")
    later = {"links": [{"url": "https://f.example", "label": "Forum"},
                       {"url": "https://h.example", "label": "homepage"}]}
    assert homepage_item(later) == ("Open homepage", "https://h.example")
    assert homepage_item({"links": [{"url": "https://f.example", "label": "Forum"}]}) == \
        ("Open Forum", "https://f.example")
    assert homepage_item({"pricing": {"model": "one-time", "url": "https://b.example"}}) is None


def test_install_candidates_only_games_that_install_and_are_not_busy():
    rows = build_rows([entombed, troopanum, WEB, minimal_entry], {"troopanum"})
    # troopanum is installed (Launch), the web game is only added, minimal is waiting in the queue
    assert [r.id for r in install_candidates(rows, {"minimal"}, "windows")] == ["entombed"]
    assert [r.id for r in install_candidates(rows, set(), "windows")] == ["entombed", "minimal"]
    assert install_candidates([], set(), "windows") == []


def test_pick_index_finds_the_wanted_game_else_the_clamped_fallback_row():
    rows = build_rows([entombed, troopanum, minimal_entry], set())
    assert pick_index(rows, "troopanum", 0) == 1  # the wanted game, wherever it is now
    assert pick_index(rows, "gone", 1) == 1  # not listed: the fallback row number
    assert pick_index(rows, "gone", 7) == 2  # clamped to the last row
    assert pick_index(rows, "gone", -1) == 0 and pick_index(rows, None, -1) == 0  # nothing was selected before
    assert pick_index([], "troopanum", 3) == -1  # an empty list selects nothing
