"""The Installing preferences, without wx: check the address, and say what changed.

wx-free (test_no_wx covers it) so the rules are unit-tested; PreferencesDialog only reads and shows.
"""
from dataclasses import dataclass

from engine.index import DEFAULT_CATALOG_URL, normalize_catalog_url


@dataclass(frozen=True)
class InstallPrefsResult:
    ok: bool                 # False: the address isn't valid, so nothing is saved
    catalog_url: str         # the normalized address to store (the old one when not ok)
    url_changed: bool        # the normalized address differs from the stored one: refresh the catalog
    register_shared: bool


def current_catalog_url(install: dict) -> str:
    """The stored address, normalized the way the service uses it."""
    url = install.get("catalog_url") or DEFAULT_CATALOG_URL
    return normalize_catalog_url(url) or url


def register_shared_components(install: dict) -> bool:
    return bool(install.get("register_shared_components", True))


def check_install_prefs(install: dict, address: str, register_shared: bool) -> InstallPrefsResult:
    """Validate the typed address against the stored install section. Never changes `install`."""
    current = current_catalog_url(install)
    normalized = normalize_catalog_url(address)
    if normalized is None:
        return InstallPrefsResult(False, current, False, register_shared_components(install))
    return InstallPrefsResult(True, normalized, normalized != current, bool(register_shared))
