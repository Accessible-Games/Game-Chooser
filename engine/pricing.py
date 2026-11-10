"""What a catalog entry costs, in the player's words (catalog schema v2 `pricing`).

Lenient like the rest of the entry reading: a missing or unreadable `pricing` (or an unknown model) shows
nothing, and an amount without a currency is left out.
"""

import re
from datetime import date

from . import messages

FREE_MODELS = frozenset({"free", "free-with-purchases"})  # no install warning for these
NO_LONGER_SOLD_MODELS = frozenset({"free-to-try", "one-time", "subscription"})

# Currencies shown with a symbol; any other is shown as "5 CAD"
_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
_NO_DECIMALS = frozenset({"JPY", "KRW"})
_CURRENCY = re.compile(r"[A-Z]{3}")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")


def pricing_of(entry: dict | None) -> dict | None:
    """The entry's pricing object when its model is one this app knows, else None."""
    pricing = entry.get("pricing") if isinstance(entry, dict) else None
    if not isinstance(pricing, dict) or pricing.get("model") not in messages.PRICE_MODELS:
        return None
    return pricing


def _number(value) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return value


def format_amount(amount, currency) -> str | None:
    """`amount` in `currency`: "$5", "$4.99", "€3.50", "¥500", "12 CAD". None when either is unusable."""
    amount = _number(amount)
    if amount is None or not isinstance(currency, str) or not _CURRENCY.fullmatch(currency.upper()):
        return None
    code = currency.upper()
    if code in _NO_DECIMALS or float(amount).is_integer():
        number = f"{round(amount):,}"
    else:
        number = f"{amount:,.2f}"
    symbol = _SYMBOLS.get(code)
    return f"{symbol}{number}" if symbol else f"{number} {code}"


def _amount(pricing: dict, key: str) -> str | None:
    return format_amount(pricing.get(key), pricing.get("currency"))


def _dict(pricing) -> dict | None:
    return pricing if isinstance(pricing, dict) else None


def model_label(pricing: dict | None) -> str | None:
    pricing = _dict(pricing)
    return messages.PRICE_MODELS.get(pricing.get("model")) if pricing else None


def no_longer_sold(pricing: dict | None) -> bool:
    pricing = _dict(pricing)
    return bool(pricing) and pricing.get("no_longer_sold") is True and pricing.get("model") in NO_LONGER_SOLD_MODELS


def price_text(pricing: dict | None) -> str:
    """How the price reads: "Free", "Free to try, $5 to unlock", "Pay what you want, from $0 (suggested $5)",
    "Buy once, $10", "Subscription, $4.99 a month", "Free, with optional purchases". Empty when not recorded."""
    model = model_label(pricing)
    if model is None:
        return ""
    kind = pricing["model"]
    if no_longer_sold(pricing):
        return messages.PRICE_NO_LONGER_SOLD.format(model=model)
    price = _amount(pricing, "price") if kind not in FREE_MODELS else None
    text = model
    if price and kind == "free-to-try":
        text = messages.PRICE_FREE_TO_TRY_AMOUNT.format(price=price)
    elif price and kind == "pay-what-you-want":
        text = messages.PRICE_PAY_WHAT_YOU_WANT_FROM.format(price=price)
    elif price and kind == "one-time":
        text = messages.PRICE_ONE_TIME_AMOUNT.format(price=price)
    elif price and kind == "subscription" and pricing.get("period") in messages.PRICE_PERIODS:
        text = messages.PRICE_PERIODS[pricing["period"]].format(price=price)
    if kind == "pay-what-you-want":
        suggested = _amount(pricing, "suggested")
        if suggested:
            text = messages.PRICE_SUGGESTED.format(text=text, suggested=suggested)
    return text


def shows_amount(pricing: dict | None) -> bool:
    """True when price_text names an amount, so "price as of" is worth saying."""
    pricing = _dict(pricing)
    if not pricing or no_longer_sold(pricing) or pricing.get("model") in FREE_MODELS:
        return False
    return bool(_amount(pricing, "price") or (pricing.get("model") == "pay-what-you-want"
                                              and _amount(pricing, "suggested")))


def checked_date(pricing: dict | None) -> str | None:
    """pricing.checked as "1 November 2026", or None when missing or not a YYYY-MM-DD date."""
    pricing = _dict(pricing)
    value = pricing.get("checked") if pricing else None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return None
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def description_lines(pricing: dict | None) -> list[str]:
    """The Price line ("price as of" when it names an amount and the date is known), then pricing.note."""
    text = price_text(pricing)
    if not text:
        return []
    checked = checked_date(pricing)
    if checked and shows_amount(pricing):
        text = messages.PRICE_AS_OF.format(text=text, date=checked)
    lines = [messages.PRICE_HEADING.format(text=text)]
    note = pricing.get("note")
    if isinstance(note, str) and note.strip():
        lines.append(note.strip())
    return lines


def can_play_free(pricing: dict | None) -> bool:
    """Free, free to try, free with optional purchases, or pay what you want with no minimum (0 or none)."""
    pricing = _dict(pricing)
    if not pricing:
        return False
    kind = pricing.get("model")
    if kind == "pay-what-you-want":
        price = _number(pricing.get("price"))
        return pricing.get("price") is None or price == 0
    return kind in ("free", "free-to-try", "free-with-purchases")


def _http(url) -> str | None:
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else None


def purchase_link(pricing: dict | None) -> tuple[str, str] | None:
    """(label, url) of pricing.url, labelled by model ("Buy or register", "Buy", ...). None when there is no
    usable address, or when the full version can no longer be bought (there is nothing to pay for)."""
    pricing = _dict(pricing)
    url = _http(pricing.get("url")) if pricing else None
    if url is None or no_longer_sold(pricing):
        return None
    return messages.PRICE_LINKS.get(pricing["model"], messages.PRICE_LINK_OTHER), url


def donate_link(pricing: dict | None) -> tuple[str, str] | None:
    pricing = _dict(pricing)
    url = _http(pricing.get("donate_url")) if pricing else None
    return (messages.DONATE_LINK, url) if url else None


def install_warning(pricing: dict | None) -> str | None:
    """The install confirmation's price warning: for every model but free and free with optional purchases,
    with where to pay or register when known. When the full version can no longer be bought, it says so instead."""
    if model_label(pricing) is None or pricing.get("model") in FREE_MODELS:
        return None
    if no_longer_sold(pricing):
        return messages.PLAN_NO_LONGER_SOLD.format(model=model_label(pricing))
    link = purchase_link(pricing)
    if link:
        return messages.PLAN_PRICE_LINK.format(price=price_text(pricing), link=link[0], url=link[1])
    return messages.PLAN_PRICE.format(price=price_text(pricing))
