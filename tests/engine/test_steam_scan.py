from engine.steam_scan import Scan, SteamApp, scan, scan_folder


def kv(path) -> str:
    return str(path).replace("\\", "\\\\")


def library(root, *others, old_form=False):
    """A Steam folder at `root` whose library list names `others` too"""
    (root / "steamapps").mkdir(parents=True, exist_ok=True)
    if old_form:
        body = "".join(f'"{n}" "{kv(p)}"\n' for n, p in enumerate(others, 1)) + '"ContentStatsID" "123"\n'
        text = '"LibraryFolders"\n{\n' + body + "}\n"
    else:
        body = "".join(f'"{n}" {{ "path" "{kv(p)}" "apps" {{ }} }}\n' for n, p in enumerate((root, *others)))
        text = '"libraryfolders"\n{\n' + body + "}\n"
    (root / "steamapps" / "libraryfolders.vdf").write_text(text, encoding="utf-8")
    return root


def manifest(lib, app_id, installdir="Game", flags=4, folder=True, text=None):
    steamapps = lib / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    if folder and installdir not in ("", ".", ".."):
        (steamapps / "common" / installdir).mkdir(parents=True, exist_ok=True)
    body = text if text is not None else (
        f'"AppState"\n{{\n"appid" "{app_id}"\n"name" "App {app_id}"\n"StateFlags" "{flags}"\n'
        f'"installdir" "{installdir}"\n}}\n')
    (steamapps / f"appmanifest_{app_id}.acf").write_text(body, encoding="utf-8")


def test_installed_means_the_flag_and_the_folder(tmp_path):
    steam = library(tmp_path / "Steam")
    manifest(steam, 10, "Ten")                       # installed
    manifest(steam, 11, "Eleven", flags=6)           # update required too: still installed
    manifest(steam, 12, "Twelve", flags=36, folder=False)   # flagged installed, folder gone
    manifest(steam, 13, "Thirteen", flags=1026)      # not fully installed
    result = scan_folder(steam)
    assert (result.state, result.complete, result.unsure) == ("found", True, frozenset())
    assert sorted(result.apps) == [10, 11]
    assert result.apps[10] == SteamApp(10, "App 10", steam / "steamapps" / "common" / "Ten")


def test_every_listed_library_is_read_in_both_forms_of_the_list(tmp_path):
    for old_form in (False, True):
        root = tmp_path / str(old_form)
        second = root / "Second Library"
        steam = library(root / "Steam", second, old_form=old_form)
        manifest(steam, 10)
        manifest(second, 20, "Twenty")
        assert sorted(scan_folder(steam).apps) == [10, 20]


def test_an_unreachable_library_is_reported_not_guessed(tmp_path):
    steam = library(tmp_path / "Steam", tmp_path / "unplugged")
    manifest(steam, 10)
    result = scan_folder(steam)
    assert sorted(result.apps) == [10] and result.complete is False


def test_a_manifest_that_cannot_be_read_makes_that_app_unsure(tmp_path):
    steam = library(tmp_path / "Steam")
    manifest(steam, 10)
    manifest(steam, 20, text='"AppState"\n{\n"appid" "20"\n"StateFl')        # Steam is writing it
    manifest(steam, 21, text="")                                              # empty
    manifest(steam, 22, text='"AppState" { "appid" "999" "StateFlags" "4" "installdir" "Game" }')   # another id
    manifest(steam, 23, text='"AppState" { "appid" "23" "StateFlags" "four" "installdir" "Game" }')
    for app_id, bad_dir in ((30, ""), (31, ".."), (32, "a/b"), (33, "a\\\\b")):   # the last reads a\b once parsed
        manifest(steam, app_id, installdir=bad_dir)                           # not one plain folder name
    for app_id, bad_dir in ((34, "D:"), (35, "D:foo"), (36, "a:b"), (37, "a\x00b")):
        manifest(steam, app_id, installdir=bad_dir, folder=False)
    result = scan_folder(steam)
    assert sorted(result.apps) == [10]
    assert result.unsure == frozenset({20, 21, 22, 23, 30, 31, 32, 33, 34, 35, 36, 37})


def test_only_manifests_in_steamapps_itself_are_read(tmp_path):
    steam = library(tmp_path / "Steam")
    manifest(steam, 10)
    music = steam / "steamapps" / "music"
    music.mkdir()
    (music / "appmanifest_50.acf").write_text("not a manifest", encoding="utf-8")
    (steam / "steamapps" / "appmanifest_notanumber.acf").write_text("x", encoding="utf-8")
    result = scan_folder(steam)
    assert sorted(result.apps) == [10] and result.unsure == frozenset()


def test_the_three_outcomes(tmp_path):
    assert scan(lambda *args: None, platform="win32") == Scan("absent")
    assert scan(lambda *args: str(tmp_path), platform="darwin") == Scan("absent")
    bare = tmp_path / "Steam"
    bare.mkdir()
    reads = lambda hive, key, name, view: str(bare)
    assert scan(reads, platform="win32") == Scan("unknown")                   # no library list
    (bare / "steamapps").mkdir()
    (bare / "steamapps" / "libraryfolders.vdf").write_text('"libraryfolders" {', encoding="utf-8")
    assert scan(reads, platform="win32") == Scan("unknown")                   # a list that cannot be parsed
    library(bare)
    manifest(bare, 10)
    assert sorted(scan(reads, platform="win32").apps) == [10]


def test_a_library_entry_that_cannot_be_read_is_not_complete(tmp_path):
    steam = library(tmp_path / "Steam")
    manifest(steam, 10)
    listing = steam / "steamapps" / "libraryfolders.vdf"
    for entry in ('"1" { "path" { "x" "y" } }', '"1" { }', '"1" ""', '"1" { "path" "relative/folder" }',
                  '"1" { "path" "a\x00b" }'):
        listing.write_text('"libraryfolders"\n{\n"0" { "path" "%s" }\n%s\n}\n' % (kv(steam), entry),
                           encoding="utf-8")
        result = scan_folder(steam)
        assert (result.state, sorted(result.apps), result.complete) == ("found", [10], False), entry


def test_a_library_list_that_is_not_one_is_unknown(tmp_path):
    steam = library(tmp_path / "Steam")
    manifest(steam, 10)
    listing = steam / "steamapps" / "libraryfolders.vdf"
    for text in ('"other" { }', '"libraryfolders" "x"', ""):
        listing.write_text(text, encoding="utf-8")
        assert scan_folder(steam) == Scan("unknown"), text
