"""Solve ALTCHA proof-of-work challenges (v2 key-derivation format).

ALTCHA is not a human test: the client proves it spent CPU time by finding
the first counter whose derived key starts with ``keyPrefix``
(https://altcha.org/docs/v2/proof-of-work/). Solving it programmatically is
what the widget itself does in a web worker.

Challenge (``GET`` from the site's challenge endpoint)::

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


class AltchaError(Exception):
    """The challenge cannot be solved (unsupported, malformed or too hard)."""


def solve_altcha(challenge: dict[str, Any]) -> str:
    """Solve *challenge* and return the base64 payload the widget would post."""
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

    started = time.monotonic()
    for counter in range(_MAX_COUNTER):
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
