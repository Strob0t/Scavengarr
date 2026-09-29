"""Decrypt Click'n'Load (CNL2) link packages.

CNL2 is JDownloader's link-transfer format: ``crypted`` is base64 AES-128-CBC
with key = IV = ``unhexlify(jk)`` and zero padding; the plaintext holds one
link per line. Sites hand it out instead of plain links (anime-loads).
"""

from __future__ import annotations

import base64
import binascii

import structlog
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

log = structlog.get_logger(__name__)


def _decrypt(key_hex: str, data: bytes) -> list[str] | None:
    key = binascii.unhexlify(key_hex)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
    plain = (decryptor.update(data) + decryptor.finalize()).rstrip(b"\x00")
    try:
        lines = plain.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    links = [line.strip() for line in lines if line.strip()]
    return links if links and all(u.startswith("http") for u in links) else None


def decrypt_cnl(jk: str, crypted: str) -> list[str]:
    """Return the links in a CNL2 package (empty list if it cannot be read).

    Some sites swap hex digits 15 and 16 of ``jk``; both keys are tried and
    the one that yields URLs wins.
    """
    try:
        data = base64.b64decode(crypted, validate=True)
    except (binascii.Error, ValueError):
        log.warning("clicknload_bad_base64")
        return []
    swapped = jk[:15] + jk[16:17] + jk[15:16] + jk[17:]
    for key_hex in dict.fromkeys((jk, swapped)):
        try:
            links = _decrypt(key_hex, data)
        except (binascii.Error, ValueError):
            continue
        if links:
            return links
    log.warning("clicknload_undecryptable", key_length=len(jk))
    return []
