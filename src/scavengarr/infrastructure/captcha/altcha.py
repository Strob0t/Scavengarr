"""Solve ALTCHA proof-of-work challenges (v2 key derivation and classic).

ALTCHA is not a human test: the client proves it spent CPU time by finding
the first counter whose derived key starts with ``keyPrefix``
(https://altcha.org/docs/v2/proof-of-work/). Solving it programmatically is
what the widget itself does in a web worker.

The classic format (s.to's link-out gate) asks for the number whose hash of
salt + number is the challenge::

    {"algorithm": "SHA-256", "challenge": "<hex>", "maxnumber": 100000,
     "salt": "<hex>", "signature": "<hex>"}

v2 challenge (``GET`` from the site's challenge endpoint)::

    {"parameters": {"algorithm": "PBKDF2/SHA-256", "cost": 5000,
                    "keyLength": 32, "keyPrefix": "00", "nonce": "<hex>",
                    "salt": "<hex>", ...},
     "signature": "<hex>"}

Password per attempt: ``nonce`` bytes + counter as big-endian uint32 (the
widget's default ``counterMode``). The payload the widget posts back is the
base64-encoded JSON of the challenge plus ``{counter, derivedKey, time}``.

CPU-bound: call :func:`solve_altcha` via ``asyncio.to_thread``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
import time
from typing import Any

_DIGESTS: dict[str, str] = {
    "PBKDF2/SHA-256": "sha256",
    "PBKDF2/SHA-384": "sha384",
    "PBKDF2/SHA-512": "sha512",
}

# A two-hex-digit prefix needs ~256 attempts on average; the cap only stops a
# runaway loop on a malformed or hostile challenge.
_MAX_COUNTER = 1_000_000

# Classic format: one hash per number. The widget's default maxnumber is 1e6;
# s.to's gate asks for up to 1e5 (0.3 s on a Raspberry Pi 4, 2026-10-04)
_CLASSIC_DIGESTS: dict[str, str] = {
    "SHA-1": "sha1",
    "SHA-256": "sha256",
    "SHA-512": "sha512",
}
_MAX_NUMBER = 10_000_000

# Bounds for the site-supplied parameters: a hostile or broken challenge must
# not pin a worker thread (``asyncio.to_thread`` cannot be cancelled). The
# widget's default cost is 5000; one attempt at the cap takes well under a
# second, a real challenge is solved in about one.
_MAX_COST = 500_000
_MAX_KEY_LENGTH = 64
_MAX_SECONDS = 20.0
_HEX_RE = re.compile(r"[0-9a-f]*")


class AltchaError(Exception):
    """The challenge cannot be solved (unsupported, malformed or too hard)."""


def solve_altcha(challenge: dict[str, Any]) -> str:
    """Solve *challenge* and return the base64 payload the widget would post.

    Takes both formats: v2 key derivation (``parameters``) and the classic
    hash of salt + number.
    """
    if "parameters" in challenge:
        return _solve_key_derivation(challenge)
    return _solve_classic(challenge)


def _solve_classic(challenge: dict[str, Any]) -> str:
    """Find the number whose hash of salt + number is the challenge.

    Payload: base64 of ``{algorithm, challenge, number, salt, signature,
    took}`` (the widget's own JSON, without spaces).
    """
    try:
        algorithm = str(challenge["algorithm"])
        target = str(challenge["challenge"]).lower()
        salt = str(challenge["salt"])
        signature = challenge["signature"]
        max_number = int(challenge.get("maxnumber", challenge.get("maxNumber", 10**6)))
    except (KeyError, TypeError, ValueError) as exc:
        raise AltchaError(f"malformed challenge: {exc!r}") from exc

    digest = _CLASSIC_DIGESTS.get(algorithm.upper())
    if digest is None:
        raise AltchaError(f"unsupported algorithm: {algorithm}")
    if not 0 <= max_number <= _MAX_NUMBER:
        raise AltchaError(f"maxnumber out of range: {max_number}")

    started = time.monotonic()
    for number in range(max_number + 1):
        if number % 10_000 == 0 and time.monotonic() - started > _MAX_SECONDS:
            raise AltchaError(f"gave up after {_MAX_SECONDS:.0f} s, number {number}")
        if hashlib.new(digest, f"{salt}{number}".encode()).hexdigest() == target:
            body = {
                "algorithm": algorithm,
                "challenge": challenge["challenge"],
                "number": number,
                "salt": salt,
                "signature": signature,
                "took": int((time.monotonic() - started) * 1000),
            }
            text = json.dumps(body, separators=(",", ":"))
            return base64.b64encode(text.encode()).decode()

    raise AltchaError(f"no solution up to maxnumber {max_number}")


def _solve_key_derivation(challenge: dict[str, Any]) -> str:
    """Find the first counter whose derived key starts with ``keyPrefix``."""
    try:
        params = challenge["parameters"]
        algorithm = params["algorithm"]
        nonce = bytes.fromhex(params["nonce"])
        salt = bytes.fromhex(params["salt"])
        key_prefix = str(params["keyPrefix"]).lower()
        cost = int(params["cost"])
        key_length = int(params.get("keyLength", 32))
    except (KeyError, TypeError, ValueError) as exc:
        raise AltchaError(f"malformed challenge: {exc!r}") from exc

    digest = _DIGESTS.get(algorithm)
    if digest is None:
        raise AltchaError(f"unsupported algorithm: {algorithm}")
    if not 1 <= cost <= _MAX_COST:
        raise AltchaError(f"cost out of range: {cost}")
    if not 1 <= key_length <= _MAX_KEY_LENGTH:
        raise AltchaError(f"keyLength out of range: {key_length}")
    if not _HEX_RE.fullmatch(key_prefix) or len(key_prefix) > 2 * key_length:
        raise AltchaError(f"unsatisfiable keyPrefix: {key_prefix[:20]!r}")

    started = time.monotonic()
    for counter in range(_MAX_COUNTER):
        if time.monotonic() - started > _MAX_SECONDS:
            raise AltchaError(f"gave up after {_MAX_SECONDS:.0f} s, counter {counter}")
        password = nonce + struct.pack(">I", counter)
        derived = hashlib.pbkdf2_hmac(digest, password, salt, cost, key_length).hex()
        if derived.startswith(key_prefix):
            solution = {
                "counter": counter,
                "derivedKey": derived,
                "time": int((time.monotonic() - started) * 1000),
            }
            body = {
                "challenge": {
                    "parameters": params,
                    "signature": challenge.get("signature"),
                },
                "solution": solution,
            }
            return base64.b64encode(json.dumps(body).encode()).decode()

    raise AltchaError(f"no solution below counter {_MAX_COUNTER}")
