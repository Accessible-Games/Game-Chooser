"""A reader for Steam's KeyValues text files (appmanifest_*.acf, libraryfolders.vdf)."""
import re

_TOKEN = re.compile(r'\s+|//[^\n]*|(?P<str>"(?:[^"\\]|\\.)*")|(?P<brace>[{}])|(?P<bad>.)', re.S)
_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "t": "\t"}


def _unescape(quoted: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), quoted[1:-1], flags=re.S)


def _tokens(text: str):
    for m in _TOKEN.finditer(text):
        if m.group("bad"):
            raise ValueError(f"unexpected {m.group('bad')!r} at {m.start()}")
        if m.group("str") is not None:
            yield "str", _unescape(m.group("str"))
        elif m.group("brace"):
            yield m.group("brace"), None


def parse(text: str) -> dict:
    """Keys lowercased; a value is a str or a dict. ValueError for anything not well-formed."""
    tokens = iter(_tokens(text))

    def read_object(top: bool) -> dict:
        result = {}
        for kind, value in tokens:
            if kind == "}":
                if top:
                    raise ValueError("unmatched }")
                return result
            if kind != "str":
                raise ValueError("expected a key")
            nxt = next(tokens, None)
            if nxt is None:
                raise ValueError("key without a value")
            if nxt[0] == "str":
                result[value.lower()] = nxt[1]
            elif nxt[0] == "{":
                result[value.lower()] = read_object(False)
            else:
                raise ValueError("key without a value")
        if not top:
            raise ValueError("object is not closed")
        return result

    return read_object(True)
