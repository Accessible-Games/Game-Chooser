"""What both tabs' games views show: catalog index entries (the Catalog tab) and the player's games (the Library
tab) as list rows, with their filter-tree categories and description text.

Never imports wx and never fetches anything (an agarchive link is shown like any other link, never fetched).
Entries follow catalog schema v2; a saved version 1 index may still be shown, so old shapes (links as an object,
flags) are skipped, never an error.
"""
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from engine import messages
from engine.entries import _PLATFORM_LABELS, _sources, current_platform, game_fields, platform_sources
from engine.pricing import can_play_free, description_lines, donate_link, price_text, pricing_of, purchase_link
from language_names import language_name

_UNKNOWN = {"genre": "Unknown Genre", "developer": "Unknown Developer", "year": "Unknown Year"}
_INSTALLED = "Installed"
_NOT_INSTALLED = "Not installed"
_CATEGORY_KEYS = ("platform", "genre", "tag", "price", "language", "developer", "year", "support", "installed")
# The Price branch: "Can be played for free" first, then the models in the spec's order
_PRICE_ORDER = [messages.PRICE_CAN_PLAY_FREE, *messages.PRICE_MODELS.values()]


@dataclass
class CatalogRow:
    id: str
    title: str
    developer: str
    developers: list[str]
    genres: list[str]
    year: str
    platforms: list[str]
    installed: bool
    entry: dict | None  # None for a Library game the catalog doesn't list
    tags: list[str]
    languages: list[str]  # names, such as "French"
    contains: list[str]
    unsupported: bool
    price: str  # how the price reads; empty when not recorded
    price_values: set[str]  # the Price branch values this row is under
    game: object = None  # a Library row's Game (library_rows); a Catalog row has none

    @property
    def genre(self) -> str:
        """The main genre (the first), or empty."""
        return self.genres[0] if self.genres else ""

    @property
    def details_text(self) -> str:
        """The list's Details column: tags, then whether it is no longer supported, then whether it is installed."""
        parts = list(self.tags)
        if self.unsupported:
            parts.append(messages.CATALOG_ROW_UNSUPPORTED)
        if self.installed:
            parts.append(messages.CATALOG_ROW_INSTALLED)
        return ", ".join(parts)


def _strings(value) -> list[str]:
    """The non-empty strings of a list, in order, each once; anything else gives []."""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for v in value:
        if isinstance(v, str) and v.strip() and v.strip() not in result:
            result.append(v.strip())
    return result


def _languages(entry: dict) -> list[str]:
    names: list[str] = []
    for code in _strings(entry.get("languages")):
        name = language_name(code)
        if name not in names:
            names.append(name)
    return names


def _price_values(pricing: dict | None) -> set[str]:
    if pricing is None:
        return set()  # not recorded: nothing to filter by, and no "unknown" value
    values = {messages.PRICE_MODELS[pricing["model"]]}
    if can_play_free(pricing):
        values.add(messages.PRICE_CAN_PLAY_FREE)
    return values


def build_rows(entries: list[dict], installed_ids: set[str]) -> list[CatalogRow]:
    rows = []
    for entry in entries:
        fields = game_fields(entry)
        platforms: list[str] = []
        for source in _sources(entry):
            label = _PLATFORM_LABELS.get(source.get("platform"))
            if label and label not in platforms:
                platforms.append(label)
        pricing = pricing_of(entry)
        rows.append(CatalogRow(
            id=entry.get("id", ""),
            title=fields["title"],
            developer=fields["developer"],
            developers=_strings(entry.get("developer")),
            genres=_strings(entry.get("genres")),
            year=fields["year"],
            platforms=platforms,
            installed=entry.get("id") in installed_ids,
            entry=entry,
            tags=_strings(entry.get("tags")),
            languages=_languages(entry),
            contains=_strings(entry.get("contains")),
            unsupported=entry.get("unsupported") is True,
            price=price_text(pricing),
            price_values=_price_values(pricing),
        ))
    return rows


def library_rows(games, entry_for) -> list[CatalogRow]:
    """The player's games as rows, so the Library tab filters, labels and describes them as the Catalog tab does.
    `entry_for(game)` gives a catalog game's index entry (None for a game added by hand, or one the catalog no
    longer lists).

    The id is the catalog id, else the launch path: it stays the same when the Game object is replaced. Two
    games can share a launch path (Locate has no duplicate check, games.json can be edited by hand); a repeat
    then gets its number among them added, so every row has an id of its own. That number follows the games'
    order: when the first of them is deleted or gets another path, its id passes to the next one, so the selected
    game is looked for by its object first (`pick_index`). The values are the game's own (games.json): one genre,
    the developer text as one value, even when it names several people."""
    rows, used = [], set()
    for game in games:
        base = row_id = game.catalog_id or game.launch_path
        repeat = 1
        while row_id in used:
            repeat += 1
            row_id = f"{base} #{repeat}"
        used.add(row_id)
        rows.append(CatalogRow(
            id=row_id,
            title=game.title,
            developer=game.developer,
            developers=[game.developer] if game.developer else [],
            genres=[game.genre] if game.genre else [],
            year=game.year,
            platforms=list(game.platforms),
            installed=True,
            entry=entry_for(game),
            tags=[], languages=[], contains=[], unsupported=False, price="", price_values=set(),
            game=game,
        ))
    return rows


def _row_values(row: CatalogRow) -> dict[str, set[str]]:
    """Each filter-tree category's values for a row. Tags, price, language and support have no "unknown" value:
    a row that doesn't record them is simply not under them."""
    return {
        "platform": set(row.platforms),
        "genre": set(row.genres) or {_UNKNOWN["genre"]},
        "tag": set(row.tags),
        "price": set(row.price_values),
        "language": set(row.languages),
        "developer": set(row.developers) or {_UNKNOWN["developer"]},
        "year": {row.year or _UNKNOWN["year"]},
        "support": {messages.CATALOG_TREE_UNSUPPORTED} if row.unsupported else set(),
        "installed": {_INSTALLED if row.installed else _NOT_INSTALLED},
    }


def tree_categories(rows) -> dict[str, set[str]]:
    categories: dict[str, set[str]] = {key: set() for key in _CATEGORY_KEYS}
    for row in rows:
        for key, values in _row_values(row).items():
            categories[key] |= values
    return categories


def tree_values(key: str, values) -> list[str]:
    """A category's values in tree order: Price's "Can be played for free" first and then the models in the
    spec's order; every other category alphabetically."""
    if key == "price":
        return sorted(values, key=lambda v: (_PRICE_ORDER.index(v) if v in _PRICE_ORDER else len(_PRICE_ORDER),
                                             v.lower()))
    return sorted(values, key=str.lower)


def filter_rows(rows, search: str, criteria: dict[str, set[str]]) -> list[CatalogRow]:
    """OR within a criterion, AND across criteria; an empty or missing criterion matches everything.
    Search matches the title, the developer and the titles of the games a collection contains, case-insensitively;
    a search containing "unknown" matches nothing."""
    term = (search or "").strip().lower()
    if "unknown" in term:
        return []
    result = []
    for row in rows:
        values = _row_values(row)
        if any(wanted and not (values[key] & wanted) for key, wanted in criteria.items() if key in values):
            continue
        if term and not any(term in field.lower() for field in (row.title, row.developer, *row.contains)):
            continue
        result.append(row)
    return result


def pick_index(rows, wanted_id, fallback: int, wanted_game=None) -> int:
    """The row to select in a list that was just refilled: the wanted game's; when that game is not listed, row
    number `fallback`, clamped to the list; -1 for an empty list.

    `wanted_game` is the Game of the row that was selected (a Library row; a Catalog row has none, and at the
    first fill there is no row). The row that holds that same object is the game, whatever its id is now: a new
    launch path changes the id of a game added by hand, and the old id may then be another game's (two games
    with one launch path). A game added by hand is never another object, so when no row holds it, it is gone and
    its id is not asked. A catalog game's object is replaced by a finished job or an index take: its catalog id
    finds it then. `wanted_id` alone decides for a Catalog row and at the first fill."""
    if not rows:
        return -1
    by_id = True
    if wanted_game is not None:
        for index, row in enumerate(rows):
            if row.game is wanted_game:
                return index
        by_id = bool(wanted_game.catalog_id)
    if by_id:
        for index, row in enumerate(rows):
            if row.id == wanted_id:
                return index
    return min(max(fallback, 0), len(rows) - 1)


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


def _http(url) -> str | None:
    """`url` when it is an http(s) address with a host (and no spaces), else None."""
    if not isinstance(url, str) or any(c.isspace() for c in url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    return url if parts.scheme in ("http", "https") and host else None


def _host(url: str) -> str:
    """The site's name for a link without a label: its host name, without "www."."""
    host = urlsplit(url).hostname or url
    return host[4:] if host.startswith("www.") else host


def _own_links(entry: dict) -> list[tuple[str, str]]:
    links = entry.get("links")
    result = []
    for link in links if isinstance(links, list) else []:  # a version 1 links object is skipped
        url = _http(link.get("url")) if isinstance(link, dict) else None
        if url is None:
            continue
        label = link.get("label")
        label = " ".join(label.split()) if isinstance(label, str) else ""
        result.append((label or _host(url), url))
    return result


def entry_links(entry: dict | None) -> list[tuple[str, str]]:
    """(label, url) for each web page the entry names, in order: its links as listed (a missing label shows the
    site's host name), then where to pay or register (pricing.url) and where to support the developer
    (pricing.donate_url). Only http(s) addresses, each once."""
    if not isinstance(entry, dict):
        return []
    pricing = pricing_of(entry)
    result = _own_links(entry) + [link for link in (purchase_link(pricing), donate_link(pricing)) if link]
    seen: set[str] = set()
    unique = []
    for label, url in result:
        if url not in seen:
            seen.add(url)
            unique.append((label, url))
    return unique


def _details_lines(entry: dict) -> list[str]:
    """Price (and its note), genres, tags, languages, the games a collection contains, and whether it is still
    supported."""
    lines = description_lines(pricing_of(entry))
    genres = _strings(entry.get("genres"))
    if genres:
        text = messages.CATALOG_GENRE if len(genres) == 1 else messages.CATALOG_GENRES
        lines.append(text.format(items=", ".join(genres)))
    for values, text in ((_strings(entry.get("tags")), messages.CATALOG_TAGS),
                         (_languages(entry), messages.CATALOG_LANGUAGES),
                         (_strings(entry.get("contains")), messages.CATALOG_CONTAINS)):
        if values:
            lines.append(text.format(items=", ".join(values)))
    if entry.get("unsupported") is True:
        lines.append(messages.CATALOG_UNSUPPORTED)
    return lines


def description_text(entry: dict | None) -> str:
    """The description field: the entry's description, then its details, then one line per web page
    ("label: url", so Enter on the line opens it)."""
    if not entry:
        return ""
    description = entry.get("description")
    body = markdown_to_text(description) if isinstance(description, str) else ""
    details = "\n".join(_details_lines(entry))
    links = "\n".join(f"{label}: {url}" for label, url in entry_links(entry))
    return "\n\n".join(part for part in (body, details, links) if part)


_LINK_LINE = re.compile(r"^.+?: (https?://\S+)$")


def link_at_line(line: str) -> str | None:
    """The address on a "label: url" line, or None."""
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


def install_candidates(rows: list[CatalogRow], busy_ids, platform: str | None = None) -> list[CatalogRow]:
    """The rows a random install may pick: games that install (not installed, not web games that are only added)
    and have no job queued or running."""
    return [r for r in rows if r.id not in busy_ids and action_kind(r, platform) == "install"]


def link_items(entry: dict | None) -> list[tuple[str, str]]:
    """(menu label, url) for each of the entry's web pages, in entry_links order: "Open <label>" for its links,
    "Open purchase page" for pricing.url and "Open donation page" for pricing.donate_url."""
    if not isinstance(entry, dict):
        return []
    pricing = pricing_of(entry)
    own = {url for _, url in _own_links(entry)}
    special = {}
    for link, label in ((donate_link(pricing), messages.MENU_OPEN_DONATE),
                        (purchase_link(pricing), messages.MENU_OPEN_PURCHASE)):
        if link and link[1] not in own:
            special[link[1]] = label
    return [(special.get(url) or messages.MENU_OPEN_LINK.format(label=label), url)
            for label, url in entry_links(entry)]


def homepage_item(entry: dict | None) -> tuple[str, str] | None:
    """The menu item for the game's homepage: its link labelled "Homepage" (any case), else its first link.
    The pricing pages don't count."""
    own = _own_links(entry) if isinstance(entry, dict) else []
    if not own:
        return None
    label, url = next(((label, url) for label, url in own if label.lower() == "homepage"), own[0])
    return messages.MENU_OPEN_LINK.format(label=label), url
