"""Filemoon hoster resolver — extracts HLS URLs from filemoon.sx embed pages.

Supports two architectures:
1. Byse SPA (new): Vite/React frontend that loads video sources via a
   challenge/attest/playback API flow with AES-256-GCM encrypted responses.
2. Legacy XFS: Packed JavaScript (Dean Edwards packer) containing JWPlayer config.

Filemoon domain variants: filemoon.sx, filemoon.to, filemoon.eu, etc.
"""

from __future__ import annotations

import base64
import json
import os
import re
import uuid
from typing import TypedDict

import httpx
import structlog
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDSA,
    SECP256R1,
    EllipticCurvePublicNumbers,
    generate_private_key,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers._video_extract import (
    extract_hls_from_unpacked,
    unpack_p_a_c_k,
)

log = structlog.get_logger(__name__)

_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ---------------------------------------------------------------------------
# TypedDicts for Byse API responses
# ---------------------------------------------------------------------------


class ByseEncryptedPlayback(TypedDict, total=False):
    """AES-256-GCM encrypted playback response."""

    key_parts: list[str]
    iv: str
    payload: str


class ByseSource(TypedDict, total=False):
    """A single video source from the Byse playback response."""

    url: str
    file: str
    mimeType: str
    type: str


class ByseChallengeResponse(TypedDict, total=False):
    """Response from /api/videos/access/challenge."""

    challenge_id: str
    nonce: str
    viewer_hint: str


class ByseAttestResponse(TypedDict, total=False):
    """Response from /api/videos/access/attest."""

    token: str
    viewer_id: str
    device_id: str
    confidence: float


class BysePlaybackResponse(TypedDict, total=False):
    """Response from /api/videos/{id}/embed/playback."""

    playback: ByseEncryptedPlayback


class ByseDetailsResponse(TypedDict, total=False):
    """Response from /api/videos/{id}/embed/details."""

    embed_frame_url: str
    sources: list[ByseSource]
    data: dict[str, object]


def _b64url_encode(data: bytes) -> str:
    """Encode bytes to base64url without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    """Decode a base64url string (with or without padding)."""
    s = s.replace("-", "+").replace("_", "/")
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.b64decode(s)


def _decrypt_playback(encrypted: ByseEncryptedPlayback) -> dict[str, object] | None:
    """Decrypt AES-256-GCM encrypted playback response from Byse API.

    The key is formed by concatenating base64url-decoded key_parts.
    """
    key_parts = encrypted.get("key_parts")
    if not isinstance(key_parts, list) or not key_parts:
        return None

    try:
        # Concatenate base64url-decoded key parts to form the AES key
        key = b"".join(_b64url_decode(part) for part in key_parts)
        iv = _b64url_decode(encrypted["iv"])
        ciphertext = _b64url_decode(encrypted["payload"])

        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(iv, ciphertext, None)
        return json.loads(plaintext)
    except Exception:  # noqa: BLE001
        log.debug("filemoon_byse_decrypt_failed")
        return None


def _build_attest_body(
    challenge_id: str,
    nonce: str,
    viewer_id: str,
) -> dict[str, object]:
    """Build the attestation request body with ECDSA signature.

    Generates a P-256 keypair, signs the nonce, and returns the
    request body matching the Byse SPA fingerprint format.
    """
    device_id = uuid.uuid4().hex

    # Generate ECDSA P-256 key pair and sign the nonce
    private_key = generate_private_key(SECP256R1())
    nonce_bytes = _b64url_decode(nonce)
    signature = private_key.sign(nonce_bytes, ECDSA(SHA256()))

    # Export public key coordinates for JWK format
    pub_numbers: EllipticCurvePublicNumbers = private_key.public_key().public_numbers()
    x_bytes = pub_numbers.x.to_bytes(32, "big")
    y_bytes = pub_numbers.y.to_bytes(32, "big")

    return {
        "viewer_id": viewer_id,
        "device_id": device_id,
        "challenge_id": challenge_id,
        "nonce": nonce,
        "signature": _b64url_encode(signature),
        "public_key": {
            "crv": "P-256",
            "ext": True,
            "key_ops": ["verify"],
            "kty": "EC",
            "x": _b64url_encode(x_bytes),
            "y": _b64url_encode(y_bytes),
        },
        "client": {
            "user_agent": _BROWSER_UA,
            "architecture": "x86",
            "bitness": "64",
            "platform": "Windows",
            "platform_version": "15.0.0",
            "model": "",
            "ua_full_version": "131.0.6778.86",
            "brand_full_versions": [
                {"brand": "Chromium", "version": "131.0.6778.86"},
                {"brand": "Not_A Brand", "version": "24.0.0.0"},
                {"brand": "Google Chrome", "version": "131.0.6778.86"},
            ],
            "pixel_ratio": 1,
            "screen_width": 1920,
            "screen_height": 1080,
            "color_depth": 24,
            "languages": ["en-US", "en"],
            "timezone": "Europe/Berlin",
            "hardware_concurrency": 8,
            "device_memory": 8,
            "touch_points": 0,
            "webgl_vendor": "Google Inc. (NVIDIA)",
            "webgl_renderer": (
                "ANGLE (NVIDIA, NVIDIA GeForce GTX 1080 Direct3D11"
                " vs_5_0 ps_5_0, D3D11)"
            ),
            "canvas_hash": _b64url_encode(os.urandom(32)),
            "audio_hash": _b64url_encode(os.urandom(32)),
            "pointer_type": "fine,hover",
            "extra": {
                "vendor": "Google Inc.",
                "appVersion": (
                    "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    " (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            },
        },
        "storage": {
            "cookie": viewer_id,
            "local_storage": viewer_id,
            "indexed_db": f"{viewer_id}:{device_id}",
            "cache_storage": f"{viewer_id}:{device_id}",
        },
        "attributes": {"entropy": "high"},
    }


def _unpack_p_a_c_k(packed: str) -> str | None:
    """Unpack Dean Edwards packed JavaScript (delegates to shared module)."""
    return unpack_p_a_c_k(packed)


def _extract_hls_from_unpacked(js: str) -> str | None:
    """Extract HLS URL from unpacked JWPlayer config (delegates to shared module)."""
    return extract_hls_from_unpacked(js)


class FilemoonResolver:
    """Resolves Filemoon embed pages to playable HLS URLs.

    Supports filemoon.sx, filemoon.to and domain variants.
    Extracts HLS URL from packed JavaScript (Dean Edwards packer).
    """

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "filemoon"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Fetch Filemoon embed page and extract HLS URL."""
        embed_url = self._normalize_embed_url(url)

        # Headers that the video CDN requires for playback
        playback_headers = {"Referer": embed_url}

        # Method 0: Byse SPA API (new Filemoon architecture)
        result = await self._try_byse_api(embed_url)
        if result:
            return ResolvedStream(
                video_url=result.video_url,
                is_hls=result.is_hls,
                quality=result.quality,
                headers=playback_headers,
            )

        # Fetch HTML for legacy extraction methods
        try:
            resp = await self._http.get(
                embed_url,
                follow_redirects=True,
                timeout=15,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    ),
                },
            )
            if resp.status_code != 200:
                log.warning(
                    "filemoon_http_error",
                    status=resp.status_code,
                    url=embed_url,
                )
                return None

            html = resp.text
            # Update Referer to the final URL after any redirects
            playback_headers = {"Referer": str(resp.url)}
        except httpx.HTTPError:
            log.warning("filemoon_request_failed", url=embed_url)
            return None

        # Check offline markers
        if self._is_offline(html):
            log.info("filemoon_offline", url=url)
            return None

        # Method 1: Unpack packed JS blocks (legacy XFS)
        result = self._try_packed_js(html)
        if result:
            return ResolvedStream(
                video_url=result.video_url,
                is_hls=result.is_hls,
                quality=result.quality,
                headers=playback_headers,
            )

        # Method 2: Direct HLS URL in page source (rare, but possible)
        result = self._try_direct_hls(html)
        if result:
            return ResolvedStream(
                video_url=result.video_url,
                is_hls=result.is_hls,
                quality=result.quality,
                headers=playback_headers,
            )

        log.warning("filemoon_extraction_failed", url=url)
        return None

    async def _try_byse_api(self, url: str) -> ResolvedStream | None:
        """Extract video URL via Byse SPA API (new Filemoon architecture).

        Filemoon migrated to a Vite/React SPA ("Byse Frontend") that loads
        video sources through a multi-step API flow:
        1. GET /api/videos/{id}/embed/details → embed_frame_url (CDN domain)
        2. POST {cdn}/api/videos/access/challenge → challenge_id, nonce
        3. POST {cdn}/api/videos/access/attest → access token
        4. POST {cdn}/api/videos/{id}/embed/playback → AES-256-GCM encrypted sources
        5. Decrypt → JSON with video sources
        """
        video_id = self._extract_video_id(url)
        if not video_id:
            return None

        base_match = re.match(r"(https?://[^/]+)", url)
        if not base_match:
            return None
        base_url = base_match.group(1)

        # Step 1: Get embed details (including CDN domain)
        details = await self._byse_get_details(base_url, video_id, url)
        if not details:
            return None

        # Check if sources are directly in the response (some older Byse versions)
        result = self._parse_byse_sources(details)
        if result:
            return result

        # Step 2-5: Full challenge/attest/playback flow
        embed_frame_url = details.get("embed_frame_url", "")
        if not embed_frame_url:
            return None

        cdn_match = re.match(r"(https?://[^/]+)", embed_frame_url)
        if not cdn_match:
            return None
        cdn_base = cdn_match.group(1)

        return await self._byse_challenge_flow(cdn_base, video_id, embed_frame_url)

    async def _byse_get_details(
        self, base_url: str, video_id: str, referer: str
    ) -> ByseDetailsResponse | None:
        """Fetch Byse embed details API."""
        api_url = f"{base_url}/api/videos/{video_id}/embed/details"
        try:
            resp = await self._http.get(
                api_url,
                follow_redirects=True,
                timeout=15,
                headers={"User-Agent": _BROWSER_UA, "Referer": referer},
            )
            if resp.status_code != 200:
                log.debug(
                    "filemoon_byse_api_not_ok",
                    status=resp.status_code,
                    url=api_url,
                )
                return None
            return resp.json()  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            log.debug("filemoon_byse_api_failed", url=api_url)
            return None

    async def _byse_challenge_flow(
        self, cdn_base: str, video_id: str, referer: str
    ) -> ResolvedStream | None:
        """Execute the Byse challenge/attest/playback flow."""
        headers = {"User-Agent": _BROWSER_UA, "Referer": referer}

        # Step 2: Request challenge
        challenge = await self._byse_post_json(
            f"{cdn_base}/api/videos/access/challenge", headers=headers
        )
        if not challenge:
            return None

        challenge_id = challenge.get("challenge_id", "")
        nonce = challenge.get("nonce", "")
        viewer_id = challenge.get("viewer_hint", uuid.uuid4().hex)

        if not challenge_id or not nonce:
            log.debug("filemoon_byse_challenge_incomplete")
            return None

        # Step 3: Attest with ECDSA signature + fingerprint
        attest_body = _build_attest_body(challenge_id, nonce, viewer_id)
        attest = await self._byse_post_json(
            f"{cdn_base}/api/videos/access/attest",
            headers=headers,
            body=attest_body,
        )
        if not attest:
            return None

        token = attest.get("token", "")
        if not token:
            log.debug("filemoon_byse_attest_no_token")
            return None

        # Step 4-5: Get encrypted playback and decrypt
        return await self._byse_get_playback(
            cdn_base, video_id, headers, referer, token, attest, attest_body
        )

    async def _byse_get_playback(
        self,
        cdn_base: str,
        video_id: str,
        headers: dict[str, str],
        referer: str,
        token: str,
        attest: dict[str, object],
        attest_body: dict[str, object],
    ) -> ResolvedStream | None:
        """Fetch and decrypt Byse playback data."""
        playback_body = {
            "fingerprint": {
                "token": token,
                "viewer_id": attest.get("viewer_id", ""),
                "device_id": attest.get("device_id", attest_body["device_id"]),
                "confidence": attest.get("confidence", 0.9),
            },
        }
        playback_data = await self._byse_post_json(
            f"{cdn_base}/api/videos/{video_id}/embed/playback",
            headers={**headers, "X-Embed-Parent": referer},
            body=playback_body,
        )
        if not playback_data:
            return None

        encrypted = playback_data.get("playback")
        if not isinstance(encrypted, dict):
            log.debug("filemoon_byse_no_playback_data")
            return None

        decrypted = _decrypt_playback(encrypted)
        if not decrypted:
            return None

        log.debug("filemoon_byse_decrypted", data_keys=list(decrypted.keys()))
        return self._parse_byse_sources(decrypted)

    async def _byse_post_json(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        """POST to a Byse API endpoint and return JSON response."""
        try:
            kwargs: dict[str, object] = {
                "headers": {**headers, "Content-Type": "application/json"},
                "timeout": 15,
            }
            if body is not None:
                kwargs["content"] = json.dumps(body)
            resp = await self._http.post(url, **kwargs)
            if resp.status_code != 200:
                log.debug("filemoon_byse_post_failed", url=url, status=resp.status_code)
                return None
            return resp.json()  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            log.debug("filemoon_byse_post_error", url=url)
            return None

    def _extract_video_id(self, url: str) -> str:
        """Extract video ID from Filemoon URL (e.g., /e/abc123 -> abc123)."""
        match = re.search(r"/(?:e|d|download)/([a-z0-9]+)", url)
        return match.group(1) if match else ""

    def _parse_byse_sources(self, data: dict[str, object]) -> ResolvedStream | None:
        """Parse Byse API response for video sources."""
        sources = data.get("sources")
        if not isinstance(sources, list):
            # Try nested structure
            inner = data.get("data")
            if isinstance(inner, dict):
                sources = inner.get("sources")
            if not isinstance(sources, list):
                return None

        for source in sources:
            if not isinstance(source, dict):
                continue
            video_url = source.get("url") or source.get("file", "")
            if not video_url or not video_url.startswith("http"):
                continue
            mime = (source.get("mimeType") or source.get("type") or "").lower()
            is_hls = "mpegurl" in mime or ".m3u8" in video_url
            log.debug("filemoon_byse_api_success", url=video_url[:80])
            return ResolvedStream(
                video_url=video_url,
                is_hls=is_hls,
                quality=StreamQuality.UNKNOWN,
            )
        return None

    def _try_packed_js(self, html: str) -> ResolvedStream | None:
        """Extract HLS URL from packed JavaScript blocks.

        Uses a robust start-marker approach: find eval(function(p,a,c,k,e,d))
        start positions, extract a chunk, and let _unpack_p_a_c_k() handle
        parameter extraction. This avoids fragile full-block regex matching
        that breaks on real-world Filemoon page variations.
        """
        for m in re.finditer(
            r"eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*d\s*\)",
            html,
        ):
            # Extract a chunk large enough to contain the full packed block
            chunk = html[m.start() : m.start() + 65536]
            unpacked = _unpack_p_a_c_k(chunk)
            if not unpacked:
                continue

            hls_url = _extract_hls_from_unpacked(unpacked)
            if hls_url:
                log.debug("filemoon_packed_js_success", url=hls_url[:80])
                return ResolvedStream(
                    video_url=hls_url,
                    is_hls=True,
                    quality=StreamQuality.UNKNOWN,
                )

        return None

    def _try_direct_hls(self, html: str) -> ResolvedStream | None:
        """Look for HLS URL directly in page source."""
        match = re.search(
            r"""["'](https?://[^"']+\.m3u8[^"']*)["']""",
            html,
        )
        if match:
            url = match.group(1)
            if "thumbnail" not in url.lower() and "track" not in url.lower():
                log.debug("filemoon_direct_hls", url=url[:80])
                return ResolvedStream(
                    video_url=url,
                    is_hls=True,
                    quality=StreamQuality.UNKNOWN,
                )
        return None

    def _normalize_embed_url(self, url: str) -> str:
        """Ensure URL uses the /e/ embed format."""
        if "/e/" in url:
            return url
        # Convert /d/ or /download/ to /e/
        url = re.sub(r"/(?:d|download)/", "/e/", url)
        return url

    def _is_offline(self, html: str) -> bool:
        """Check for Filemoon offline/removed markers."""
        if "File Not Found" in html:
            return True
        if "file was deleted" in html.lower():
            return True
        if 'class="fake-signup"' in html:
            return True
        return False
