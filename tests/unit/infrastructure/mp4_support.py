"""Byte builders for the boxes of an MP4 file's header (ISO/IEC 14496-12),
shared by the tests of the video size and of the playback check."""

from __future__ import annotations

import struct


def box(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def tkhd(width: int, height: int, *, version: int = 0, flags: int = 7) -> bytes:
    """A track header: version and flags (bit 0: the track is enabled),
    times, track id and duration (20 bytes, 32 in version 1), layer, volume
    and matrix, then the size in 16.16 fixed point."""
    times = bytes(20 if version == 0 else 32)
    size = struct.pack(">II", width << 16, height << 16)
    return box(
        b"tkhd", bytes([version]) + flags.to_bytes(3, "big") + times + bytes(52) + size
    )


def hdlr(handler: bytes) -> bytes:
    """A handler box: version and flags, the predefined field, the handler
    type and the reserved fields."""
    return box(b"hdlr", bytes(8) + handler + bytes(12))


def trak(
    width: int,
    height: int,
    *,
    version: int = 0,
    flags: int = 7,
    handler: bytes = b"vide",
    media: int = 0,
) -> bytes:
    mdia = box(b"mdia", box(b"mdhd", bytes(24)) + hdlr(handler) + bytes(media))
    return box(b"trak", tkhd(width, height, version=version, flags=flags) + mdia)


def mvhd(duration_s: float = 0.0, timescale: int = 1000, *, version: int = 0) -> bytes:
    """A movie header: version and flags, times, timescale and duration,
    then the rate, volume, matrix and the next track id."""
    times = bytes(8) if version == 0 else bytes(16)
    duration = b""
    if version == 0:
        duration = struct.pack(">II", timescale, int(duration_s * timescale))
    else:
        duration = struct.pack(">I", timescale) + struct.pack(
            ">Q", int(duration_s * timescale)
        )
    return box(b"mvhd", bytes([version]) + bytes(3) + times + duration + bytes(80))


def moov(*tracks: bytes, duration_s: float = 0.0, timescale: int = 1000) -> bytes:
    return box(
        b"moov",
        mvhd(duration_s, timescale) + b"".join(tracks),
    )


FTYP = box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41")


def mdat_header(size: int) -> bytes:
    """The header of a media data box of *size* bytes (a 64-bit size past
    4 GiB)."""
    if size < 2**32:
        return struct.pack(">I4s", size, b"mdat")
    return struct.pack(">I4sQ", 1, b"mdat", size)
