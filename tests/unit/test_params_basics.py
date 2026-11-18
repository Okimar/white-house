"""An empty connection still sends a temperature and a reply length (ST_PARITY item 14)."""

from __future__ import annotations

from tavern.providers.params import with_basics


def test_an_empty_connection_gets_the_table_defaults():
    assert with_basics({}) == {"temperature": 1.0, "max_tokens": 1024}


def test_values_the_connection_sets_are_kept_even_zero():
    out = with_basics({"temperature": 0, "max_tokens": 4000, "top_p": 0.9})
    assert out == {"temperature": 0, "max_tokens": 4000, "top_p": 0.9}


def test_only_the_missing_one_is_filled():
    assert with_basics({"max_tokens": 300}) == {"temperature": 1.0, "max_tokens": 300}
