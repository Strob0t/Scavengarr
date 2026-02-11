"""Release name parser using guessit for quality and language extraction."""

from __future__ import annotations

import re

from guessit import guessit

from scavengarr.domain.entities.stremio import StreamLanguage, StreamQuality

# --- Quality mappings ---

_SCREEN_SIZE_TO_QUALITY: dict[str, StreamQuality] = {
    "2160p": StreamQuality.UHD_4K,
    "1080p": StreamQuality.HD_1080P,
    "1080i": StreamQuality.HD_1080P,
    "720p": StreamQuality.HD_720P,
    "480p": StreamQuality.SD,
    "576p": StreamQuality.SD,
    "360p": StreamQuality.SD,
}

_BADGE_TO_QUALITY: dict[str, StreamQuality] = {
    "4K": StreamQuality.UHD_4K,
    "UHD": StreamQuality.UHD_4K,
    "2160P": StreamQuality.UHD_4K,
    "1080P": StreamQuality.HD_1080P,
    "FHD": StreamQuality.HD_1080P,
    "FULLHD": StreamQuality.HD_1080P,
    "BDRIP": StreamQuality.HD_1080P,
    "BLURAY": StreamQuality.HD_1080P,
    "720P": StreamQuality.HD_720P,
    "HD": StreamQuality.HD_720P,
    "WEBRIP": StreamQuality.HD_720P,
    "WEBDL": StreamQuality.HD_720P,
    "WEB-DL": StreamQuality.HD_720P,
    "480P": StreamQuality.SD,
    "SD": StreamQuality.SD,
    "DVDRIP": StreamQuality.SD,
    "TS": StreamQuality.TS,
    "TELESYNC": StreamQuality.TS,
    "HDTS": StreamQuality.TS,
    "CAM": StreamQuality.CAM,
    "HDCAM": StreamQuality.CAM,
    "CAMRIP": StreamQuality.CAM,
}

# --- Language helpers ---

_GERMAN_PATTERNS = re.compile(r"(?i)\b(ger(?:man)?|deu(?:tsch)?)\b")
_ENGLISH_PATTERNS = re.compile(r"(?i)\b(eng(?:lish)?)\b")
_SUBTITLE_PATTERNS = re.compile(r"(?i)\b(sub|untertitel)\b")


def _quality_from_badge(badge: str) -> StreamQuality:
    """Map a badge/link_quality string to StreamQuality (case-insensitive)."""
    return _BADGE_TO_QUALITY.get(badge.strip().upper(), StreamQuality.UNKNOWN)


def _language_code(lang_obj: object) -> str | None:
    """Extract a 2-letter language code from a guessit Language object."""
    alpha2 = getattr(lang_obj, "alpha2", None)
    if alpha2:
        return str(alpha2)
    alpha3 = getattr(lang_obj, "alpha3", None)
    if alpha3:
        if alpha3 in ("deu", "ger"):
            return "de"
        if alpha3 == "eng":
            return "en"
    return None


def _label_for(code: str, *, is_dubbed: bool) -> str:
    """Build a human-readable label like 'German Dub' or 'English Sub'."""
    names = {"de": "German", "en": "English"}
    name = names.get(code, code.upper())
    suffix = "Dub" if is_dubbed else "Sub"
    return f"{name} {suffix}"


# --- Public API ---


def parse_quality(
    *,
    release_name: str | None = None,
    quality_badge: str | None = None,
    link_quality: str | None = None,
) -> StreamQuality:
    """Determine quality from multiple sources.

    Priority: 1) guessit(release_name).screen_size, 2) link_quality, 3) quality_badge.
    """
    # 1) Try guessit on release_name
    if release_name:
        guess = guessit(release_name)
        screen_size = guess.get("screen_size")
        if screen_size and screen_size in _SCREEN_SIZE_TO_QUALITY:
            return _SCREEN_SIZE_TO_QUALITY[screen_size]

    # 2) Try link_quality
    if link_quality:
        q = _quality_from_badge(link_quality)
        if q != StreamQuality.UNKNOWN:
            return q

    # 3) Try quality_badge
    if quality_badge:
        q = _quality_from_badge(quality_badge)
        if q != StreamQuality.UNKNOWN:
            return q

    return StreamQuality.UNKNOWN


def parse_language(
    *,
    release_name: str | None = None,
    link_language: str | None = None,
    plugin_default_language: str | None = None,
) -> StreamLanguage | None:
    """Determine language from multiple sources.

    Priority: 1) guessit(release_name), 2) link_language, 3) plugin_default_language.
    """
    # 1) Try guessit on release_name
    if release_name:
        lang = _language_from_guessit(release_name)
        if lang is not None:
            return lang

    # 2) Try link_language string
    if link_language:
        lang = _language_from_link_string(link_language)
        if lang is not None:
            return lang

    # 3) Try plugin_default_language
    if plugin_default_language:
        code = plugin_default_language.lower()
        return StreamLanguage(
            code=code,
            label=_label_for(code, is_dubbed=True),
            is_dubbed=True,
        )

    return None


def _language_from_guessit(release_name: str) -> StreamLanguage | None:
    """Extract language info from a release name via guessit."""
    guess = guessit(release_name)

    # Check subtitle_language first (subs take priority as they're more specific)
    sub_langs = guess.get("subtitle_language")
    if sub_langs:
        lang_obj = sub_langs if not isinstance(sub_langs, list) else sub_langs[0]
        code = _language_code(lang_obj)
        if code:
            return StreamLanguage(
                code=f"{code}-sub",
                label=_label_for(code, is_dubbed=False),
                is_dubbed=False,
            )

    # Check audio language
    audio_langs = guess.get("language")
    if audio_langs:
        lang_obj = audio_langs if not isinstance(audio_langs, list) else audio_langs[0]
        code = _language_code(lang_obj)
        if code:
            return StreamLanguage(
                code=code,
                label=_label_for(code, is_dubbed=True),
                is_dubbed=True,
            )

    return None


def _language_from_link_string(link_language: str) -> StreamLanguage | None:
    """Parse a free-text language string (e.g. 'German Dub', 'English Sub')."""
    text = link_language.strip()
    if not text:
        return None

    is_sub = bool(_SUBTITLE_PATTERNS.search(text))

    if _GERMAN_PATTERNS.search(text):
        code = "de-sub" if is_sub else "de"
        return StreamLanguage(
            code=code,
            label=_label_for("de", is_dubbed=not is_sub),
            is_dubbed=not is_sub,
        )

    if _ENGLISH_PATTERNS.search(text):
        code = "en-sub" if is_sub else "en"
        return StreamLanguage(
            code=code,
            label=_label_for("en", is_dubbed=not is_sub),
            is_dubbed=not is_sub,
        )

    return None
