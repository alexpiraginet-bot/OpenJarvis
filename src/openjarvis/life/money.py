"""Money formatting, in one place.

The Life OS stores integer cents everywhere and renders them in three
different surfaces — the API's spoken answers, the outbound nudges, and the
agent tools. Three copies of the formatter drifted apart immediately: the
first one used Python's default ``,.2f``, which renders R$20.142,50 as
"R$20,142.50". Wrong on a screen, worse read aloud by a Brazilian TTS voice.

The frontend has ``Intl.NumberFormat`` and needs none of this; this is for the
strings the backend composes itself.
"""

from __future__ import annotations

#: Symbol per ISO code. Anything unlisted falls back to the code itself, so an
#: unexpected currency reads as "CHF 1.234,00" rather than silently as reais.
_SYMBOLS = {"BRL": "R$", "USD": "US$", "EUR": "€", "GBP": "£"}

#: Locales that group with "." and decimalise with "," — the inverse of the
#: C default. Everything else is left in the anglophone convention.
_COMMA_DECIMAL_CURRENCIES = frozenset({"BRL", "EUR"})


def format_money(cents: int, currency: str = "BRL") -> str:
    """Render integer cents as a human-readable amount.

    >>> format_money(2014250)
    'R$ 20.142,50'
    >>> format_money(24780)
    'R$ 247,80'
    >>> format_money(150000, "USD")
    'US$ 1,500.00'
    """
    symbol = _SYMBOLS.get(currency, currency)
    amount = f"{cents / 100:,.2f}"
    if currency in _COMMA_DECIMAL_CURRENCIES:
        # Swap via a placeholder: a naive two-step replace would turn the
        # grouping commas into dots and then straight back again.
        amount = amount.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{symbol} {amount}"


__all__ = ["format_money"]
