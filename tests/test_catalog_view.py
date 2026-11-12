from catalog_view import (action_kind, action_label, build_rows, link_items, description_text, entry_links, filter_rows,
                          homepage_item, initial_tab, library_rows, link_at_line, install_candidates,
                          markdown_to_text, pick_index, tree_categories, tree_values)
from models import Game

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


def _library():
    by_hand = Game(title="Alpha", genre="Action", developer="Ann Dev, Bob Dev", year="2001",
                   platforms=["Windows", "macOS"], launch_path="C:/games/alpha.exe")
    bare = Game(title="Bare Web", launch_path="https://bare.example/play", platforms=["Web"])
    installed = Game(title="Entombed", genre="Adventure", developer="Driftwood Entertainment", year="2004",
                     platforms=["Windows"], launch_path="Entombed/entombed.exe", library_id="lib1",
                     catalog_id="entombed")
    games = [by_hand, bare, installed]
    return games, library_rows(games, lambda game: {"entombed": entombed}.get(game.catalog_id))


def test_library_rows_are_catalog_rows_that_hold_their_game():
    games, rows = _library()
    assert [r.game for r in rows] == games and all(r.installed for r in rows)
    assert [(r.title, r.genre, r.developer, r.year, r.platforms) for r in rows] == [
        ("Alpha", "Action", "Ann Dev, Bob Dev", "2001", ["Windows", "macOS"]),
        ("Bare Web", "", "", "", ["Web"]),
        ("Entombed", "Adventure", "Driftwood Entertainment", "2004", ["Windows"])]
    assert [r.entry for r in rows] == [None, None, entombed]
    assert not any(r.tags or r.languages or r.contains or r.price or r.price_values or r.unsupported for r in rows)
    assert all(r.game is None for r in build_rows([entombed], set()))  # a Catalog row holds no game


def test_library_row_id_survives_the_game_object_being_replaced():
    games, rows = _library()
    assert [r.id for r in rows] == ["C:/games/alpha.exe", "https://bare.example/play", "entombed"]
    replaced = [Game.from_dict(game.to_dict()) for game in games]  # as an index take or a finished job does
    assert [r.id for r in library_rows(replaced, lambda game: None)] == [r.id for r in rows]


def test_library_row_ids_are_unique_when_games_share_a_launch_path():
    """Locate has no duplicate check and games.json can be edited by hand. With one id for two rows, the next
    refill would select the first of them, and Delete or Enter would act on the wrong game."""
    same = "C:/games/same.exe"
    games = [Game(title="First", launch_path=same), Game(title="Other", launch_path="C:/games/other.exe"),
             Game(title="Second", launch_path=same), Game(title="Third", launch_path=same),
             Game(title="Odd", launch_path=same + " #2")]  # a path that looks like a numbered repeat
    rows = library_rows(games, lambda game: None)
    ids = [r.id for r in rows]
    assert len(set(ids)) == 5 and ids[:2] == [same, "C:/games/other.exe"]  # a path used once is the id as it is
    assert [pick_index(rows, row.id, 0) for row in rows] == [0, 1, 2, 3, 4]  # each id finds its own row
    assert [r.id for r in library_rows(list(games), lambda game: None)] == ids  # and is the same on every build
    # A repeat is numbered by its place among the games with that path, so when the first of them goes the next
    # one takes over its id
    assert library_rows(games[2:], lambda game: None)[0].id == same


def test_library_rows_give_the_tree_each_field_as_one_value():
    _, rows = _library()
    cats = tree_categories(rows)
    assert cats["platform"] == {"Windows", "macOS", "Web"}
    assert cats["genre"] == {"Action", "Adventure", "Unknown Genre"}
    # the developer text is one tree value, also when it names several people
    assert cats["developer"] == {"Ann Dev, Bob Dev", "Driftwood Entertainment", "Unknown Developer"}
    assert cats["year"] == {"2001", "2004", "Unknown Year"}
    assert cats["tag"] == cats["price"] == cats["language"] == cats["support"] == set()


def test_library_rows_filter_search_label_and_describe_like_any_row():
    _, rows = _library()
    assert [r.title for r in filter_rows(rows, "", {"platform": {"macOS", "Web"}})] == ["Alpha", "Bare Web"]
    assert [r.title for r in filter_rows(rows, "", {"developer": {"Ann Dev, Bob Dev"}})] == ["Alpha"]
    assert [r.title for r in filter_rows(rows, "", {"genre": {"Unknown Genre"}, "year": {"Unknown Year"}})] == \
        ["Bare Web"]
    assert [r.title for r in filter_rows(rows, "BOB", {})] == ["Alpha"]  # title and developer are searched
    assert filter_rows(rows, "action", {}) == [] and filter_rows(rows, "unknown", {}) == []
    assert [action_label(r, "windows") for r in rows] == ["Launch Alpha", "Launch Bare Web", "Launch Entombed"]
    assert [description_text(r.entry) for r in rows] == ["", "", description_text(entombed)]
