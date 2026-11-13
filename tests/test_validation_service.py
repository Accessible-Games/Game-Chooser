"""Add Game's checks of what the player typed (ValidationService). wx-free."""

import pytest

from validation_service import ValidationService

# What Add Game's own web-address pattern refused and the catalog's test (engine.pricing.http_url) accepts: a port,
# localhost, an IP address, a long or one-letter top-level domain, an underscore, a fragment, a user name, a host
# with no dot, and the catalog's own leniencies (a host that starts with a dot or hyphen, a port it doesn't check)
NOW_ACCEPTED = ["https://example.com:8080/x", "http://localhost", "http://localhost:8000/game.html",
                "http://127.0.0.1/", "http://[::1]/", "https://192.168.1.10:3000", "https://example.technology",
                "https://example.c", "https://ex_ample.com", "https://example.com#frag", "https://user:pw@example.com/",
                "https://example", "https://.com", "https://-bad-.com", "https://example.com:99999",
                "https://example.com:abc", "https://münchen.de"]


@pytest.mark.parametrize("url", NOW_ACCEPTED + ["https://example.com", "HTTPS://Example.COM/Play",
                                                "http://example.com/game?level=1"])
def test_a_web_game_address_is_what_the_catalog_calls_one(url):
    assert ValidationService.validate_url(url) == (True, None)


@pytest.mark.parametrize("url", ["example.com", "ftp://example.com", "https://", "http://", "https:/example.com",
                                 "https://exa mple.com", "https://example.com/a b", "file:///C:/game.html",
                                 "https://example.com\n"])  # the old pattern let a trailing newline through
def test_anything_else_is_not_a_web_game_address(url):
    assert ValidationService.validate_url(url) == (False, "Invalid URL format")


def test_an_empty_address_says_so():
    assert ValidationService.validate_url("") == (False, "URL cannot be empty")
