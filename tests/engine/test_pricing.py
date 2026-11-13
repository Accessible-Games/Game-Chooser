"""How a catalog entry's pricing reads to players."""

import pytest

from engine.pricing import (can_play_free, checked_date, description_lines, donate_link, format_amount,
                            install_warning, price_text, pricing_of, purchase_link, shown_price)


def p(model, **fields):
    return {"model": model, **fields}


def test_format_amount_by_currency():
    assert format_amount(5, "USD") == "$5"
    assert format_amount(4.99, "USD") == "$4.99"
    assert format_amount(3.5, "EUR") == "€3.50"
    assert format_amount(10, "gbp") == "£10"
    assert format_amount(500, "JPY") == "¥500"
    assert format_amount(1500, "USD") == "$1,500"
    assert format_amount(12, "CAD") == "12 CAD"
    assert format_amount(0, "USD") == "$0"
    for amount, currency in [(5, None), (5, "dollars"), (-1, "USD"), (True, "USD"), ("5", "USD"), (None, "USD")]:
        assert format_amount(amount, currency) is None


@pytest.mark.parametrize("pricing, text", [
    (p("free"), "Free"),
    (p("free-to-try"), "Free to try"),
    (p("free-to-try", price=5, currency="USD"), "Free to try, $5 to unlock"),
    (p("pay-what-you-want"), "Pay what you want"),
    (p("pay-what-you-want", price=2, suggested=5, currency="USD"), "Pay what you want, from $2 (suggested $5)"),
    (p("pay-what-you-want", price=0, currency="USD"), "Pay what you want, from $0"),
    (p("pay-what-you-want", suggested=5, currency="EUR"), "Pay what you want (suggested €5)"),
    (p("one-time", price=9.99, currency="USD"), "Buy once, $9.99"),
    (p("one-time"), "Buy once"),
    (p("subscription", price=4.99, currency="USD", period="month"), "Subscription, $4.99 a month"),
    (p("subscription", price=40, currency="GBP", period="year"), "Subscription, £40 a year"),
    (p("subscription", price=40, currency="GBP"), "Subscription"),  # no period: no amount
    (p("free-with-purchases"), "Free, with optional purchases"),
    (p("free-with-purchases", price=3, currency="USD"), "Free, with optional purchases"),  # price unused
    (p("free-to-try", price=5, currency="USD", no_longer_sold=True),
     "Free to try (the full version can no longer be bought)"),
    (p("free", no_longer_sold=True), "Free"),  # only meaningful for paid models
])
def test_price_text(pricing, text):
    assert price_text(pricing) == text


def test_nothing_shows_without_readable_pricing():
    for pricing in (None, {}, [], "free", p("donationware"), {"price": 5}):
        assert price_text(pricing) == ""
        assert description_lines(pricing) == []
        assert install_warning(pricing) is None
        assert not can_play_free(pricing)
    assert pricing_of({"pricing": p("donationware")}) is None and pricing_of(None) is None
    assert pricing_of({"pricing": p("free")}) == p("free")


def test_description_lines_price_as_of_and_note():
    pricing = p("one-time", price=10, currency="USD", checked="2026-11-01", note="Full version adds 12 levels")
    assert description_lines(pricing) == ["Price: Buy once, $10. Price as of 1 November 2026",
                                          "Full version adds 12 levels"]
    # "price as of" only when an amount is shown, and only for a real date
    assert description_lines(p("free", checked="2026-11-01")) == ["Price: Free"]
    assert description_lines(p("one-time", price=10, currency="USD", checked="2026-13-01")) == \
        ["Price: Buy once, $10"]
    assert description_lines(p("subscription", price=3, currency="USD", checked="2026-11-01")) == [
        "Price: Subscription"]  # no period, so no amount shown
    assert description_lines(p("pay-what-you-want", suggested=5, currency="USD", checked="2026-11-01")) == [
        "Price: Pay what you want (suggested $5). Price as of 1 November 2026"]
    assert checked_date(p("free", checked="nope")) is None and checked_date(p("free")) is None


def test_can_play_free():
    assert all(can_play_free(p(m)) for m in ("free", "free-to-try", "free-with-purchases", "pay-what-you-want"))
    assert can_play_free(p("pay-what-you-want", price=0, currency="USD"))
    assert not can_play_free(p("pay-what-you-want", price=1, currency="USD"))
    assert not can_play_free(p("one-time", price=1, currency="USD"))
    assert not can_play_free(p("subscription"))


def test_shown_price_is_the_amount_price_text_names():
    assert shown_price(p("one-time", price=10, currency="USD")) == 10
    assert shown_price(p("subscription", price=4.99, currency="USD", period="month")) == 4.99
    assert shown_price(p("free-to-try", price=5, currency="USD")) == 5
    assert shown_price(p("pay-what-you-want", price=0, currency="USD", suggested=5)) == 0
    # price_text names no price for these, so there is none to sort by: no currency, a free model, a period it
    # doesn't know, no longer sold, only a suggested amount, nothing recorded
    for pricing in (p("one-time", price=10), p("free", price=9, currency="USD"),
                    p("subscription", price=2, currency="USD", period="decade"),
                    p("one-time", price=3, currency="USD", no_longer_sold=True),
                    p("pay-what-you-want", price="5", currency="USD", suggested=5), p("free"), None):
        assert shown_price(pricing) is None


def test_purchase_and_donate_links():
    assert purchase_link(p("free-to-try", url="https://x/buy")) == ("Buy or register", "https://x/buy")
    assert purchase_link(p("one-time", url="https://x/buy")) == ("Buy", "https://x/buy")
    assert purchase_link(p("subscription", url="https://x/sub")) == ("Subscribe", "https://x/sub")
    assert purchase_link(p("pay-what-you-want", url="https://x/pay")) == ("Pay", "https://x/pay")
    assert purchase_link(p("free-with-purchases", url="https://x/store")) == ("Store page", "https://x/store")
    assert purchase_link(p("one-time", url="javascript:alert(1)")) is None
    assert purchase_link(p("one-time", url="https://x/buy", no_longer_sold=True)) is None
    assert donate_link(p("free", donate_url="https://x/give")) == ("Support the developer", "https://x/give")
    assert donate_link(p("free", donate_url="ftp://x")) is None and donate_link(None) is None


def test_install_warning():
    assert install_warning(p("free")) is None and install_warning(p("free-with-purchases")) is None
    assert install_warning(p("free-to-try", price=5, currency="USD", url="https://x/buy")) == \
        "Price: Free to try, $5 to unlock. Buy or register: https://x/buy"
    assert install_warning(p("free-to-try")) == "Price: Free to try."
    assert install_warning(p("pay-what-you-want", price=0, currency="USD")) == "Price: Pay what you want, from $0."
    assert install_warning(p("subscription", price=3, currency="USD", period="month", url="https://x/s")) == \
        "Price: Subscription, $3 a month. Subscribe: https://x/s"
    # never tells players to pay or register for what can't be bought
    assert install_warning(p("one-time", price=5, currency="USD", url="https://x/buy", no_longer_sold=True)) == \
        "Price: Buy once. The full version can no longer be bought."
