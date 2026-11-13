"""Add Game's checks of what the player typed (ValidationService). wx-free."""

import pytest

from validation_service import ValidationService


# A web address is what the catalog calls one (engine.pricing.http_url): a port, localhost or an IP address too
@pytest.mark.parametrize("url, valid", [("https://example.com", True), ("HTTP://localhost:8000/game.html", True),
                                        ("http://127.0.0.1/", True), ("example.com", False),
                                        ("ftp://example.com", False), ("https://", False),
                                        ("https://example.com/a b", False)])
def test_a_web_game_address_is_what_the_catalog_calls_one(url, valid):
    assert ValidationService.validate_url(url) == ((True, None) if valid else (False, "Invalid URL format"))


def test_an_empty_address_says_so():
    assert ValidationService.validate_url("") == (False, "URL cannot be empty")
