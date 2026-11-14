"""HTTP fetching for the install engine. Never contacts agarchive.net."""

import contextlib
import http.client
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlsplit

from engine.errors import BlockedHost, Cancelled, DownloadError

USER_AGENT = "GameChooser/0.1 (+https://catalog.accessiblegames.net/)"
BLOCKED_DOMAIN = "agarchive.net"
CHUNK = 64 * 1024


def check_url_allowed(url: str) -> None:
    """Raise unless `url` is plain http(s) and not agarchive.net (or a subdomain)."""
    try:
        parts = urlsplit(url)
        host = parts.hostname  # lowercased; drops userinfo and port
    except ValueError as exc:
        raise DownloadError(f"Bad address: {url}") from exc
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise DownloadError(f"Not a web address: {url}")
    # urllib percent-encodes redirect Locations, then unquotes the host again
    # when it builds the request, so compare the unquoted spelling.
    host = unquote(host)
    if not host.isascii():
        # http.client falls back to the idna codec, which maps U+3002, fullwidth
        # letters and the like onto plain ASCII, so compare what it would connect to.
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise DownloadError(f"Bad address: {url}") from exc
    host = host.lower().rstrip(".")
    if host == BLOCKED_DOMAIN or host.endswith("." + BLOCKED_DOMAIN):
        raise BlockedHost(host)


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url_allowed(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Fetcher:
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_CheckedRedirects)

    def _open(self, url: str):
        check_url_allowed(url)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            return self._opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise DownloadError(f"{exc.code} {exc.reason}") from exc
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            raise DownloadError(str(getattr(exc, "reason", exc))) from exc

    def get_bytes(self, url: str, max_bytes: int = 64 * 2**20) -> bytes:
        with self._open(url) as response:
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise DownloadError(f"Response is larger than {max_bytes} bytes")
            try:
                data = response.read(max_bytes + 1)
            except (http.client.HTTPException, OSError) as exc:
                raise DownloadError(str(exc)) from exc
        if len(data) > max_bytes:
            raise DownloadError(f"Response is larger than {max_bytes} bytes")
        return data

    def download(
        self,
        url: str,
        dest: Path,
        progress: Callable[[int, int | None], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> None:
        part = dest.with_name(dest.name + ".part")
        try:
            with self._open(url) as response:
                length = response.headers.get("Content-Length")
                total = int(length) if length and length.isdigit() else None
                done = 0
                with open(part, "wb") as out:  # a write error stays an OSError: another address won't help
                    while True:
                        if cancel and cancel():
                            raise Cancelled()
                        try:
                            chunk = response.read(CHUNK)
                        except (http.client.HTTPException, OSError) as exc:
                            raise DownloadError(str(exc)) from exc
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
            if total is not None and done < total:
                raise DownloadError(f"Download ended early: {done} of {total} bytes")
            os.replace(part, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                part.unlink()
            raise
