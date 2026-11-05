"""Catalog tab helpers: turn catalog index entries into list rows, filter-tree categories and description text.

Never imports wx and never fetches anything (the agarchive link is shown as text, a credit only).
"""
import re
from dataclasses import dataclass

from engine.entries import _PLATFORM_LABELS, _sources, current_platform, game_fields, platform_sources

FLAG_LABELS = {"requires-registration": "needs registration"}
LINK_LABELS = [
    ("homepage", "Homepage"),
    ("purchase", "Buy or register"),
    ("audiogames_net", "audiogames.net page"),
    ("agarchive", "Archived at agarchive"),
]

_UNKNOWN = {"genre": "Unknown Genre", "developer": "Unknown Developer", "year": "Unknown Year"}
_INSTALLED = "Installed"
_NOT_INSTALLED = "Not installed"
_CATEGORY_KEYS = ("platform", "genre", "developer", "year", "flags", "installed")


@dataclass
class CatalogRow:
    id: str
    title: str
    developer: str
    developers: list[str]
    genres: list[str]
    year: str
    platforms: list[str]
    flags: list[str]
    installed: bool
    entry: dict

    @property
    def flags_text(self) -> str:
        parts = [FLAG_LABELS.get(flag, flag) for flag in self.flags]
        if self.installed:
            parts.append("installed")
        return ", ".join(parts)


def _strings(value) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def build_rows(entries: list[dict], installed_ids: set[str]) -> list[CatalogRow]:
    rows = []
    for entry in entries:
        fields = game_fields(entry)
        platforms: list[str] = []
        for source in _sources(entry):
            label = _PLATFORM_LABELS.get(source.get("platform"))
            if label and label not in platforms:
                platforms.append(label)
        rows.append(CatalogRow(
            id=entry.get("id", ""),
            title=fields["title"],
            developer=fields["developer"],
            developers=_strings(entry.get("developer")),
            genres=_strings(entry.get("genres")),
            year=fields["year"],
            platforms=platforms,
            flags=_strings(entry.get("flags")),
            installed=entry.get("id") in installed_ids,
            entry=entry,
        ))
    return rows


def _row_values(row: CatalogRow) -> dict[str, set[str]]:
    return {
        "platform": set(row.platforms),
        "genre": set(row.genres) or {_UNKNOWN["genre"]},
        "developer": set(row.developers) or {_UNKNOWN["developer"]},
        "year": {row.year or _UNKNOWN["year"]},
        "flags": set(row.flags),
        "installed": {_INSTALLED if row.installed else _NOT_INSTALLED},
    }


def tree_categories(rows) -> dict[str, set[str]]:
    categories: dict[str, set[str]] = {key: set() for key in _CATEGORY_KEYS}
    for row in rows:
        for key, values in _row_values(row).items():
            categories[key] |= values
    return categories


def filter_rows(rows, search: str, criteria: dict[str, set[str]]) -> list[CatalogRow]:
    """OR within a criterion, AND across criteria; an empty or missing criterion matches everything.
    Search matches title and developer only, case-insensitively; a search containing "unknown" matches nothing
    (as in the Library's FilterWorker)."""
    term = (search or "").strip().lower()
    if "unknown" in term:
        return []
    result = []
    for row in rows:
        values = _row_values(row)
        if any(wanted and not (values[key] & wanted) for key, wanted in criteria.items() if key in values):
            continue
        if term and not any(term in field.lower() for field in (row.title, row.developer)):
            continue
        result.append(row)
    return result


_FENCE = re.compile(r"^[ \t]*(```|~~~)")


def _prose_to_text(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)", r"\1 (\2)", text)
    text = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$", r"\1", text, flags=re.M)
    text = re.sub(r"^[ \t]*>[ \t]?", "", text, flags=re.M)
    text = re.sub(r"^[ \t]{0,3}([*_-])(?:[ \t]*\1){2,}[ \t]*$", "", text, flags=re.M)
    text = re.sub(r"^([ \t]*)[*+][ \t]+", r"\1- ", text, flags=re.M)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", r"\1", text)
    text = re.sub(r"(?<![\w*])\*(?=\w)([^*\n]+?)(?<=\w)\*(?![\w*])", r"\1", text)
    text = re.sub(r"(?<![\w_])_(?=\w)([^_\n]+?)(?<=\w)_(?![\w_])", r"\1", text)
    text = re.sub(r"</?[A-Za-z][^>\n]*>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Next to a fence at most one blank line remains (the fence's own newline is added when joining).
    return re.sub(r"\A\n{2,}", "\n", re.sub(r"\n{2,}\Z", "\n", text))


def markdown_to_text(md: str) -> str:
    """Plain text from Markdown. Fenced code keeps its lines verbatim (only the fence lines are dropped)."""
    segments: list[str] = []
    prose: list[str] = []
    in_fence = False
    for line in md.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE.match(line):
            if not in_fence and prose:
                segments.append(_prose_to_text("\n".join(prose)))
                prose = []
            in_fence = not in_fence
        elif in_fence:
            segments.append(line)
        else:
            prose.append(line)
    if prose:
        segments.append(_prose_to_text("\n".join(prose)))
    text = re.sub(r"\A(?:[ \t]*\n)+", "", "\n".join(segments))
    return text.rstrip()


def description_text(entry: dict | None) -> str:
    if not entry:
        return ""
    description = entry.get("description")
    body = markdown_to_text(description) if isinstance(description, str) else ""
    links = entry.get("links")
    links = links if isinstance(links, dict) else {}
    lines = [f"{label}: {links[key]}" for key, label in LINK_LABELS
             if isinstance(links.get(key), str) and links[key]]
    return "\n\n".join(part for part in (body, "\n".join(lines)) if part)


_LINK_LINE = re.compile(r"^(?:%s): (https?://\S+)$" % "|".join(re.escape(label) for _, label in LINK_LABELS))


def link_at_line(line: str) -> str | None:
    match = _LINK_LINE.match(line.strip())
    return match.group(1) if match else None


def initial_tab(saved: str | None, is_first_run: bool) -> str:
    if saved in ("catalog", "library"):
        return saved
    return "catalog" if is_first_run else "library"


def action_kind(row: CatalogRow, platform: str | None = None) -> str:
    """What Enter does on a row: "launch" (installed), "add" (a web game: every source for this platform is a
    link, nothing downloads) or "install"."""
    if row.installed:
        return "launch"
    sources = platform_sources(row.entry, platform or current_platform())
    if sources and all(isinstance(s.get("install"), dict) and s["install"].get("method") == "url" for s in sources):
        return "add"
    return "install"


_ACTION_VERBS = {"launch": "Launch", "add": "Add", "install": "Install"}


def action_verb(row: CatalogRow, platform: str | None = None) -> str:
    return _ACTION_VERBS[action_kind(row, platform)]


def action_label(row: CatalogRow | None, platform: str | None = None) -> str:
    """The action button's label; "Install" alone while nothing is selected."""
    if row is None:
        return "Install"
    return f"{action_verb(row, platform)} {row.title}"


def link_items(entry: dict | None) -> list[tuple[str, str]]:
    """(menu label, url) for each http(s) link the entry has, in the context menu's order."""
    links = entry.get("links") if entry else None
    links = links if isinstance(links, dict) else {}
    result = []
    for key, label in _LINK_MENU:
        url = links.get(key)
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            result.append((label, url))
    return result


_LINK_MENU = [
    ("homepage", "Open homepage"),
    ("purchase", "Open purchase page"),
    ("audiogames_net", "Open audiogames.net page"),
    ("agarchive", "Open agarchive page"),
]
