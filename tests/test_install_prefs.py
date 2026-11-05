import pytest

from engine.index import DEFAULT_CATALOG_URL
from install_prefs import check_install_prefs, current_catalog_url

BAD = ["", "   ", "catalog.example.com", "http://example.com/", "ftp://example.com/", "https://", "https://[bad"]


def install(url=DEFAULT_CATALOG_URL, shared=True):
    return {"catalog_url": url, "register_shared_components": shared, "last_library": None}


@pytest.mark.parametrize("address", BAD)
def test_invalid_address_keeps_old_values(address):
    section = install(shared=True)
    result = check_install_prefs(section, address, False)
    assert not result.ok and not result.url_changed
    assert result.catalog_url == DEFAULT_CATALOG_URL and result.register_shared is True
    assert section == install()  # never mutates


def test_unchanged_address_needs_no_refresh_even_without_trailing_slash():
    result = check_install_prefs(install(), DEFAULT_CATALOG_URL.rstrip("/"), False)
    assert result.ok and not result.url_changed and result.register_shared is False


def test_new_address_is_normalized_and_flagged():
    result = check_install_prefs(install(), " http://127.0.0.1:9 ", True)
    assert result.ok and result.url_changed and result.catalog_url == "http://127.0.0.1:9/"


def test_stored_address_missing_or_unnormalized():
    assert current_catalog_url({}) == DEFAULT_CATALOG_URL
    assert current_catalog_url({"catalog_url": "https://x.example/c"}) == "https://x.example/c/"
