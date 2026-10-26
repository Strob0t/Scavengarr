"""Release name parser using guessit for quality and language extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass

from scavengarr.domain.entities.stremio import StreamLanguage, StreamQuality
from scavengarr.infrastructure.stremio.release_guess import guess_release

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

# Badges that name no resolution: their class ranks the stream, but no
# source said it is 720p or 1080p ("HD" is any of them)
_GUESSED_BADGES = frozenset({"HD", "WEBRIP", "WEBDL", "WEB-DL", "BDRIP", "BLURAY"})

# Separators of a badge's spellings ("Full HD", "Blu-Ray") and of its parts
# ("HD-1080p", "4K/UHD", "1080p/5.1")
_BADGE_SEPARATORS_RE = re.compile(r"[\s._/-]+")
_BADGE_TOKEN_RE = re.compile(r"[0-9A-Z]+")


@dataclass(frozen=True)
class ParsedQuality:
    """A stream's quality class, and whether a word that names no
    resolution guessed it (``_GUESSED_BADGES``): the class ranks the
    stream, a label would claim a resolution no source named."""

    quality: StreamQuality = StreamQuality.UNKNOWN
    guessed: bool = False


_UNKNOWN = ParsedQuality()

# --- Language helpers ---

# German labels too (s.to: "Englisch", "Japanisch mit deutschen Untertiteln")
_GERMAN_PATTERNS = re.compile(r"(?i)\b(ger(?:man)?|deu(?:tsch(?:en)?)?)\b")
_ENGLISH_PATTERNS = re.compile(r"(?i)\b(eng(?:lish|lisch(?:en)?)?)\b")
_SUBTITLE_PATTERNS = re.compile(r"(?i)\b(sub|untertiteln?)\b")
# The original audio, whichever language it is (the mediatheken's
# "Originalversion")
_ORIGINAL_PATTERNS = re.compile(r"(?i)\b(original(?:version|fassung)?|ov)\b")
_ORIGINAL = StreamLanguage(code="ov", label="Original", is_dubbed=False)


def _quality_from_badge(badge: str) -> ParsedQuality:
    """The quality a badge or link_quality string names (case-insensitive).

    The whole badge in any spelling ("Full HD"), else the best part that
    names a quality of its own ("HD-1080p" is 1080p, "4K/UHD" 4K): a word
    without a resolution inside a composite badge ("HD/Deutsch") adds no
    guess.
    """
    text = badge.strip().upper()
    for whole in (text, _BADGE_SEPARATORS_RE.sub("", text)):
        quality = _BADGE_TO_QUALITY.get(whole)
        if quality is not None:
            return ParsedQuality(quality, guessed=whole in _GUESSED_BADGES)
    parts = (
        _BADGE_TO_QUALITY.get(token, StreamQuality.UNKNOWN)
        for token in _BADGE_TOKEN_RE.findall(text)
        if token not in _GUESSED_BADGES
    )
    return ParsedQuality(max(parts, default=StreamQuality.UNKNOWN))


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
) -> ParsedQuality:
    """Determine quality from multiple sources.

    Priority: 1) guessit(release_name).screen_size, 2) link_quality, 3) quality_badge.
    """
    # 1) Try guessit on release_name
    if release_name:
        guess = guess_release(release_name)
        screen_size = guess.get("screen_size")
        if screen_size and screen_size in _SCREEN_SIZE_TO_QUALITY:
            return ParsedQuality(_SCREEN_SIZE_TO_QUALITY[screen_size])

    # 2) Try link_quality, 3) quality_badge
    for badge in (link_quality, quality_badge):
        if badge:
            parsed = _quality_from_badge(badge)
            if parsed.quality != StreamQuality.UNKNOWN:
                return parsed

    return _UNKNOWN


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


def _language_codes(value: object) -> list[str]:
    """The language codes of a guessit field: one Language or a list."""
    langs = value if isinstance(value, list) else [value] if value else []
    return [code for lang in langs if (code := _language_code(lang))]


def _language_from_guessit(release_name: str) -> StreamLanguage | None:
    """Extract language info from a release name via guessit.

    German audio wins over any subtitle language ("German.DL.EngSub" is a
    German Dub), German subtitles over a foreign audio
    ("English.German.Subbed" is a German Sub); otherwise the subtitle's
    language comes first, then the audio's.
    """
    guess = guess_release(release_name)
    audio = _language_codes(guess.get("language"))
    subs = _language_codes(guess.get("subtitle_language"))

    if "de" in audio:
        code, is_dubbed = "de", True
    elif "de" in subs:
        code, is_dubbed = "de", False
    elif subs:
        code, is_dubbed = subs[0], False
    elif audio:
        code, is_dubbed = audio[0], True
    else:
        return None
    return StreamLanguage(
        code=code if is_dubbed else f"{code}-sub",
        label=_label_for(code, is_dubbed=is_dubbed),
        is_dubbed=is_dubbed,
    )


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

    if _ORIGINAL_PATTERNS.search(text):
        return _ORIGINAL

    return None
