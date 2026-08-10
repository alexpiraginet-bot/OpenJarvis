"""Tests for money formatting — the strings a client reads and hears."""

from __future__ import annotations

import pytest

from openjarvis.life.money import format_money


@pytest.mark.parametrize(
    "cents,expected",
    [
        (0, "R$ 0,00"),
        (5, "R$ 0,05"),
        (100, "R$ 1,00"),
        (24780, "R$ 247,80"),
        (2014250, "R$ 20.142,50"),
        (123456789, "R$ 1.234.567,89"),
    ],
)
def test_brazilian_grouping_and_decimals(cents, expected):
    """Grouping with '.' and decimals with ',' — read aloud by a pt-BR voice."""
    assert format_money(cents) == expected


def test_dollars_keep_the_anglophone_convention():
    assert format_money(150000, "USD") == "US$ 1,500.00"


def test_euros_follow_the_comma_decimal_convention():
    assert format_money(150000, "EUR") == "€ 1.500,00"


def test_an_unknown_currency_shows_its_code_rather_than_pretending():
    """Falling back to 'R$' would quietly mislabel someone's money."""
    assert format_money(150000, "CHF").startswith("CHF ")


def test_negative_amounts_survive_the_separator_swap(life=None):
    assert format_money(-24780) == "R$ -247,80"


def test_the_swap_never_leaves_a_stray_separator():
    """A naive two-step replace turns grouping commas into dots and back."""
    rendered = format_money(1234567890)
    assert rendered == "R$ 12.345.678,90"
    assert rendered.count(",") == 1
