import pytest

from engine.errors import BlockedHost, Cancelled, DownloadError
from engine.http import Fetcher, check_url_allowed


def test_user_agent_is_sent(http_server):
    Fetcher().get_bytes(http_server.url("/a"))
    assert http_server.last_headers["User-Agent"] == "GameChooser/0.1 (+https://catalog.accessiblegames.net/)"


def test_agarchive_blocked_directly():
    for u in [
        "https://agarchive.net/x",
        "http://www.agarchive.net/x",
        "https://AGARCHIVE.NET/x",
        "https://agarchive.net./x",
        "https://agarchive.net:8443/x",
        "https://user@agarchive.net/x",
        "https://a.b.Agarchive.net/x",
        "https://agarchive。net/x",
        "https://ａgarchive.net/x",
        "https://agarchive.net．/x",
        "https://agarchive%E3%80%82net/x",
        "https://%61garchive.net/x",
    ]:
        with pytest.raises(BlockedHost):
            check_url_allowed(u)
    check_url_allowed("https://notagarchive.net/x")
    check_url_allowed("https://agarchive.net.example.com/x")


def test_non_http_schemes_refused():
    for u in ["file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)", "/relative"]:
        with pytest.raises(DownloadError):
            check_url_allowed(u)


def test_initial_url_is_checked(tmp_path):
    with pytest.raises(BlockedHost):
        Fetcher().get_bytes("https://agarchive.net/x")
    with pytest.raises(BlockedHost):
        Fetcher().download("https://www.agarchive.net/x", tmp_path / "f")
    assert list(tmp_path.iterdir()) == []


def test_redirect_to_agarchive_is_blocked(http_server, tmp_path):
    with pytest.raises(BlockedHost):
        Fetcher().download(http_server.url("/r"), tmp_path / "f")
    assert list(tmp_path.iterdir()) == []


def test_get_bytes_enforces_max_bytes(http_server):
    assert len(Fetcher().get_bytes(http_server.url("/sized"))) == 300_000
    with pytest.raises(DownloadError):
        Fetcher().get_bytes(http_server.url("/sized"), max_bytes=1000)
    with pytest.raises(DownloadError):
        Fetcher().get_bytes(http_server.url("/chunked"), max_bytes=1000)


def test_download_progress_with_and_without_length(http_server, tmp_path):
    seen = []
    Fetcher().download(http_server.url("/sized"), tmp_path / "a", progress=lambda d, t: seen.append(t))
    assert seen[-1] == 300_000
    seen = []
    Fetcher().download(http_server.url("/chunked"), tmp_path / "b", progress=lambda d, t: seen.append(t))
    assert set(seen) == {None} and (tmp_path / "b").stat().st_size == 300_000


def test_cancel_leaves_nothing(http_server, tmp_path):
    with pytest.raises(Cancelled):
        Fetcher().download(http_server.url("/sized"), tmp_path / "a", cancel=lambda: True)
    assert list(tmp_path.iterdir()) == []


def test_http_error_is_download_error(http_server, tmp_path):
    with pytest.raises(DownloadError):
        Fetcher().download(http_server.url("/404"), tmp_path / "a")
    assert list(tmp_path.iterdir()) == []


def test_unreachable_is_download_error(tmp_path):
    with pytest.raises(DownloadError):
        Fetcher(timeout=2).download("http://127.0.0.1:1/x", tmp_path / "a")


def test_redirect_to_unicode_spelled_agarchive_is_blocked(http_server, tmp_path):
    http_server.routes["/u"] = (302, {"Location": "https://agarchive。net/f.zip".encode("utf-8").decode("latin-1")}, b"")
    with pytest.raises(BlockedHost):
        Fetcher().download(http_server.url("/u"), tmp_path / "f")
    assert list(tmp_path.iterdir()) == []
