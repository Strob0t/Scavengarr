"""Tests for the ALTCHA proof-of-work solver."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from typing import Any

import pytest

from scavengarr.infrastructure.captcha import altcha
from scavengarr.infrastructure.captcha.altcha import AltchaError, solve_altcha


def _challenge(
    *, algorithm: str = "PBKDF2/SHA-256", key_prefix: str = "0", cost: int = 10
) -> dict[str, Any]:
    return {
        "parameters": {
            "algorithm": algorithm,
            "cost": cost,
            "expiresAt": 1790616473,
            "keyLength": 32,
            "keyPrefix": key_prefix,
            "nonce": "819e0a36785d38c70fc29964db8e2344",
            "salt": "03e7caa891db9dec7ae70c388b35dd8a",
        },
        "signature": "sig",
    }


def _derive(params: dict[str, Any], counter: int, digest: str = "sha256") -> bytes:
    password = bytes.fromhex(params["nonce"]) + struct.pack(">I", counter)
    return hashlib.pbkdf2_hmac(
        digest,
        password,
        bytes.fromhex(params["salt"]),
        params["cost"],
        params["keyLength"],
    )


def _decode(payload: str) -> dict[str, Any]:
    return json.loads(base64.b64decode(payload))


class TestSolveAltcha:
    def test_payload_carries_challenge_and_first_matching_counter(self) -> None:
        challenge = _challenge()

        body = _decode(solve_altcha(challenge))

        params = challenge["parameters"]
        counter = body["solution"]["counter"]
        derived = _derive(params, counter)
        assert derived.hex().startswith("0")
        assert body["solution"]["derivedKey"] == derived.hex()
        assert all(not _derive(params, n).hex().startswith("0") for n in range(counter))
        assert body["challenge"] == {
            "parameters": params,
            "signature": "sig",
        }
        assert isinstance(body["solution"]["time"], int)

    @pytest.mark.parametrize(
        ("algorithm", "digest"),
        [("PBKDF2/SHA-384", "sha384"), ("PBKDF2/SHA-512", "sha512")],
    )
    def test_other_pbkdf2_digests(self, algorithm: str, digest: str) -> None:
        challenge = _challenge(algorithm=algorithm)

        body = _decode(solve_altcha(challenge))

        derived = _derive(challenge["parameters"], body["solution"]["counter"], digest)
        assert body["solution"]["derivedKey"] == derived.hex()

    def test_unsupported_algorithm_raises(self) -> None:
        with pytest.raises(AltchaError, match="unsupported"):
            solve_altcha(_challenge(algorithm="ARGON2ID"))

    def test_malformed_challenge_raises(self) -> None:
        with pytest.raises(AltchaError, match="malformed"):
            solve_altcha({"parameters": {"algorithm": "PBKDF2/SHA-256"}})

    def test_gives_up_after_max_counter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(altcha, "_MAX_COUNTER", 5)

        with pytest.raises(AltchaError, match="no solution"):
            solve_altcha(_challenge(key_prefix="00000000"))


class TestHostileChallenges:
    """Site-supplied parameters must not pin a worker thread for hours."""

    def test_rejects_excessive_cost(self) -> None:
        with pytest.raises(AltchaError, match="cost"):
            solve_altcha(_challenge(cost=50_000_000))

    @pytest.mark.parametrize("key_length", [0, 100_000_000])
    def test_rejects_key_length_out_of_range(self, key_length: int) -> None:
        challenge = _challenge()
        challenge["parameters"]["keyLength"] = key_length

        with pytest.raises(AltchaError, match="keyLength"):
            solve_altcha(challenge)

    @pytest.mark.parametrize("key_prefix", ["zz", "0" * 65])
    def test_rejects_unsatisfiable_key_prefix(self, key_prefix: str) -> None:
        with pytest.raises(AltchaError, match="keyPrefix"):
            solve_altcha(_challenge(key_prefix=key_prefix))

    def test_gives_up_after_the_time_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(altcha, "_MAX_SECONDS", 0.0)

        with pytest.raises(AltchaError, match="gave up"):
            solve_altcha(_challenge(key_prefix="00000000"))
