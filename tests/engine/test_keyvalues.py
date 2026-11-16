import pytest

from engine.keyvalues import parse

MANIFEST = '''"AppState"
{
	"appid"		"5054570"
	"name"		"Dugout \\"Dice\\""
	"StateFlags"		"4"
	"installdir"		"Dugout Dice"
	"UserConfig"
	{
		"language"		"english"
	}
}
'''


def test_parse_reads_pairs_and_nested_objects_with_lowercased_keys():
    assert parse(MANIFEST) == {"appstate": {"appid": "5054570", "name": 'Dugout "Dice"', "stateflags": "4",
                                            "installdir": "Dugout Dice", "userconfig": {"language": "english"}}}


def test_parse_unescapes_backslashes_and_skips_comments():
    assert parse('"a" { "path" "D:\\\\Steam Library" // a comment\n "b" "x\\ty" }') == {
        "a": {"path": "D:\\Steam Library", "b": "x\ty"}}
    assert parse("") == {} and parse("  \n") == {}


@pytest.mark.parametrize("text", [MANIFEST[:-3], MANIFEST[:40], '"a"', '"a" {', '"a" }', 'a "b"', '"a" "b" "c"',
                                  '"a" { "b" }', '{ "a" "b" }'])
def test_parse_refuses_a_file_that_is_cut_short_or_malformed(text):
    with pytest.raises(ValueError):
        parse(text)
