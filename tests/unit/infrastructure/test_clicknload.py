"""Tests for Click'n'Load (CNL2) link decryption."""

from __future__ import annotations

import base64
from binascii import unhexlify

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from scavengarr.infrastructure.plugins.clicknload import decrypt_cnl

# Real vector from anime-loads.org (2026-09-29)
_JK = "614346725a3558555a6137797475506c"
_CRYPTED = (
    "MgYUF0kfcvEQX9V1SxO8nsf4m+kGHW6/cIoT0kBtA+GsgcZY7oU749Ht/KPYmLU+EPARctoB8g5i"
    "T3xPg/Iu+AsBxZe6RD0t6yDF/E0SyxrgD4U6copA/scdefgH4rLD"
)
_LINK = (
    "https://rapidgator.net/file/aef78eee80e8079458a3dbec26e7ed84/"
    "onepunchman.1080p.e01.rar.html"
)


def _encrypt(key_hex: str, text: str) -> str:
    key = unhexlify(key_hex)
    data = text.encode()
    data += b"\x00" * (-len(data) % 16)  # CNL uses zero padding
    enc = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


def test_real_vector() -> None:
    assert decrypt_cnl(_JK, _CRYPTED) == [_LINK]


def test_several_links_and_blank_lines() -> None:
    key = "00112233445566778899aabbccddeeff"
    crypted = _encrypt(key, "https://a.example/1\r\n\r\nhttps://b.example/2\n")
    assert decrypt_cnl(key, crypted) == ["https://a.example/1", "https://b.example/2"]


def test_key_with_swapped_hex_digits() -> None:
    """Some sites swap hex digits 15 and 16 of ``jk`` as obfuscation."""
    real = "00112233445566778899aabbccddeeff"
    obfuscated = real[:15] + real[16] + real[15] + real[17:]
    crypted = _encrypt(real, "https://a.example/1")
    assert decrypt_cnl(obfuscated, crypted) == ["https://a.example/1"]


@pytest.mark.parametrize(
    ("jk", "crypted"),
    [
        ("zz", _CRYPTED),
        (_JK, "not base64!"),
        (_JK, base64.b64encode(b"x" * 5).decode()),
    ],
)
def test_invalid_input_returns_empty(jk: str, crypted: str) -> None:
    assert decrypt_cnl(jk, crypted) == []
