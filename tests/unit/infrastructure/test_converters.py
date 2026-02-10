"""Tests for infrastructure converters."""

from __future__ import annotations

from scavengarr.infrastructure.common.converters import to_int


class TestToInt:
    def test_none_returns_none(self) -> None:
        assert to_int(None) is None

    def test_int_passthrough(self) -> None:
        assert to_int(42) == 42

    def test_zero(self) -> None:
        assert to_int(0) == 0

    def test_string_digits(self) -> None:
        assert to_int("123") == 123

    def test_string_with_commas(self) -> None:
        assert to_int("1,234") == 1234

    def test_string_with_spaces(self) -> None:
        assert to_int("1 234") == 1234

    def test_empty_string_returns_none(self) -> None:
        assert to_int("") is None

    def test_non_numeric_string_returns_none(self) -> None:
        assert to_int("abc") is None

    def test_mixed_string_extracts_digits(self) -> None:
        assert to_int("v1.2.3") == 123

    def test_negative_int_passthrough(self) -> None:
        assert to_int(-5) == -5
