"""The dead case of the live resolver tests: a live hoster URL with its id
altered (``tests/live/test_resolver_live.py``, which runs with ``-m live``
only; its helper is tested here)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_MODULE = Path(__file__).resolve().parents[2] / "live" / "test_resolver_live.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("resolver_live", str(_MODULE))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load()
dead_url = _mod.dead_url


class TestDeadUrl:
    def test_reverses_the_last_four_characters_of_the_id(self) -> None:
        assert (
            dead_url("https://voe.sx/e/abcdefgh1234") == "https://voe.sx/e/abcdefgh4321"
        )

    def test_keeps_a_trailing_extension(self) -> None:
        assert (
            dead_url("https://filemoon.sx/e/abcdefgh1234.html")
            == "https://filemoon.sx/e/abcdefgh4321.html"
        )

    def test_keeps_query_and_fragment(self) -> None:
        assert (
            dead_url("https://host.tld/d/abcd1234?x=1#p")
            == "https://host.tld/d/abcd4321?x=1#p"
        )

    def test_a_palindromic_tail_reverses_six(self) -> None:
        # "abba" reversed is "abba": four characters would leave the id alone
        assert dead_url("https://host.tld/e/xyzabba") == "https://host.tld/e/xabbazy"

    def test_a_trailing_slash_stays_behind_the_id(self) -> None:
        assert (
            dead_url("https://host.tld/embed/abcdefgh/")
            == "https://host.tld/embed/abcdhgfe/"
        )

    def test_a_numeric_id_becomes_zeros(self) -> None:
        # fsst's ids are small sequential numbers: a reversed one exists too
        assert (
            dead_url("https://fsst.online/v/905058") == "https://fsst.online/v/000000"
        )

    def test_the_length_and_alphabet_stay(self) -> None:
        live = "https://host.tld/e/Ab9_k2Qz"
        dead = dead_url(live)
        assert dead != live
        assert len(dead) == len(live)
        assert sorted(dead) == sorted(live)
