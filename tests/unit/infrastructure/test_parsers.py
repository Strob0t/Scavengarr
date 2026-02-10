"""Tests for infrastructure parsers."""

from __future__ import annotations

from scavengarr.infrastructure.common.parsers import parse_size_to_bytes


class TestParseSizeToBytes:
    def test_empty_string_returns_zero(self) -> None:
        assert parse_size_to_bytes("") == 0

    def test_raw_digits(self) -> None:
        assert parse_size_to_bytes("1234") == 1234

    def test_bytes(self) -> None:
        assert parse_size_to_bytes("500 B") == 500

    def test_kilobytes(self) -> None:
        assert parse_size_to_bytes("1 KB") == 1024

    def test_megabytes(self) -> None:
        assert parse_size_to_bytes("500 MB") == 500 * 1024**2

    def test_gigabytes(self) -> None:
        assert parse_size_to_bytes("4.5 GB") == int(4.5 * 1024**3)

    def test_terabytes(self) -> None:
        assert parse_size_to_bytes("1 TB") == 1024**4

    def test_case_insensitive(self) -> None:
        assert parse_size_to_bytes("500 mb") == 500 * 1024**2

    def test_no_space_between_value_and_unit(self) -> None:
        assert parse_size_to_bytes("4.5GB") == int(4.5 * 1024**3)

    def test_invalid_string_returns_zero(self) -> None:
        assert parse_size_to_bytes("invalid") == 0

    def test_fractional_megabytes(self) -> None:
        assert parse_size_to_bytes("1.5 MB") == int(1.5 * 1024**2)
