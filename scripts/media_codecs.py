"""The video and audio codecs of a stream's bytes (stdlib only).

Used by ``stremio_playcheck.py``: MP4's ``moov`` → ``stsd`` sample entries,
the MPEG-TS PMT's stream types and an HLS variant's ``CODECS`` attribute,
each named the same way (``h264``, ``hevc``, ``aac``, ``ac-3``, ``ec-3``,
``opus``; an unknown entry by its own code). Truncated input gives what it
holds: a parser reads boxes and packets up to the end of the bytes.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator

# MP4 sample entries (ISO/IEC 14496-12, -15; ETSI TS 102 366; Opus in ISOBMFF)
_MP4_VIDEO = {b"avc1": "h264", b"avc3": "h264", b"hvc1": "hevc", b"hev1": "hevc"}
_MP4_AUDIO = {b"mp4a": "aac", b"ac-3": "ac-3", b"ec-3": "ec-3", b"Opus": "opus"}
_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}
# MPEG-TS stream types (ISO/IEC 13818-1; ATSC A/52 for AC-3 and E-AC-3)
_TS_VIDEO = {0x1B: "h264", 0x24: "hevc", 0x02: "mpeg2"}
_TS_AUDIO = {0x0F: "aac", 0x11: "aac", 0x03: "mp3", 0x04: "mp3", 0x81: "ac-3"}
_TS_AUDIO |= {0x87: "ec-3"}
# RFC 8216 CODECS values (RFC 6381 names) by prefix
_HLS_VIDEO = {"avc1": "h264", "avc3": "h264", "hvc1": "hevc", "hev1": "hevc"}
_HLS_AUDIO = {
    "mp4a.40": "aac",
    "mp4a.a5": "ac-3",
    "mp4a.a6": "ec-3",
    "mp4a.69": "mp3",
    "mp4a.6b": "mp3",
    "ac-3": "ac-3",
    "ec-3": "ec-3",
    "opus": "opus",
}

Codecs = tuple[str | None, str | None]


def pair(video: str | None, audio: str | None) -> str:
    """``video/audio`` for a verdict; ``?`` for the one not read."""
    if video is None and audio is None:
        return "codecs unread"
    return f"{video or '?'}/{audio or '?'}"


def _boxes(data: bytes, start: int = 0, end: int | None = None) -> Iterator[tuple]:
    """``(kind, payload start, box end)`` of the boxes in ``data[start:end]``;
    a box running past the bytes is cut to them."""
    end = len(data) if end is None else min(end, len(data))
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1 and pos + 16 <= end:
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            return
        yield kind, pos + header, min(pos + size, end)
        pos += size


def moov_place(head: bytes) -> tuple[str, int, int] | None:
    """Where the ``moov`` is by a file's first bytes: ``("head", offset,
    size)`` when it starts in them, ``("tail", 0, 0)`` when the ``mdat``
    comes first, ``None`` when the head shows neither."""
    for kind, payload, _ in _boxes(head):
        if kind == b"moov":
            start = payload - 8
            size = struct.unpack_from(">I", head, start)[0]
            return "head", start, size
        if kind == b"mdat":
            return "tail", 0, 0
    return None


def tail_moov(tail: bytes) -> bytes:
    """The ``moov`` box in a file's last bytes; empty when it starts before
    them."""
    pos = tail.rfind(b"moov")
    while pos >= 4:
        size = struct.unpack_from(">I", tail, pos - 4)[0]
        if size >= 8 and pos - 4 + size <= len(tail):
            return tail[pos - 4 : pos - 4 + size]
        pos = tail.rfind(b"moov", 0, pos)
    return b""


def _track(data: bytes, start: int, end: int) -> tuple[bytes, bytes]:
    """A ``trak``'s handler type and first sample entry (empty if not read)."""
    handler = entry = b""
    for kind, payload, box_end in _boxes(data, start, end):
        if kind == b"hdlr" and payload + 12 <= box_end:
            handler = data[payload + 8 : payload + 12]
        elif kind == b"stsd" and payload + 16 <= box_end:
            entry = data[payload + 12 : payload + 16]
        elif kind in _CONTAINERS:
            inner_handler, inner_entry = _track(data, payload, box_end)
            handler = handler or inner_handler
            entry = entry or inner_entry
    return handler, entry


def _traks(data: bytes, start: int = 0, end: int | None = None) -> Iterator[tuple]:
    for kind, payload, box_end in _boxes(data, start, end):
        if kind == b"trak":
            yield _track(data, payload, box_end)
        elif kind == b"moov":
            yield from _traks(data, payload, box_end)


def mp4_codecs(data: bytes) -> Codecs:
    """The first video and audio sample entries of the ``moov`` in *data*
    (from a file's start, a ``moov`` alone or an init segment)."""
    video = audio = None
    for handler, entry in _traks(data):
        if not entry:
            continue
        name = entry.decode("latin-1").strip()
        if entry in _MP4_VIDEO or (handler == b"vide" and entry not in _MP4_AUDIO):
            video = video or _MP4_VIDEO.get(entry, name)
        elif entry in _MP4_AUDIO or handler == b"soun":
            audio = audio or _MP4_AUDIO.get(entry, name)
    return video, audio


def _section(packet: bytes) -> bytes:
    """The PSI section a TS packet starts (empty if it starts none)."""
    if not packet[1] & 0x40:  # payload_unit_start_indicator
        return b""
    pos = 4
    if packet[3] & 0x20:  # adaptation field
        pos += 1 + packet[4]
    if pos >= len(packet):
        return b""
    pos += 1 + packet[pos]  # pointer field
    return packet[pos:]


def ts_codecs(data: bytes) -> Codecs:
    """The stream types of the PMT in an MPEG-TS segment's first bytes."""
    packets = [data[i : i + 188] for i in range(0, len(data) - 187, 188)]
    packets = [p for p in packets if p[0] == 0x47]
    pmt_pid = None
    for packet in packets:
        pid = (packet[1] & 0x1F) << 8 | packet[2]
        section = _section(packet)
        if len(section) < 12:
            continue
        if pid == 0 and section[0] == 0x00:
            pmt_pid = (section[10] & 0x1F) << 8 | section[11]
        elif pid == pmt_pid and section[0] == 0x02:
            return _pmt_codecs(section)
    return None, None


def _pmt_codecs(section: bytes) -> Codecs:
    length = (section[1] & 0x0F) << 8 | section[2]
    end = min(3 + length - 4, len(section))  # without the CRC
    pos = 12 + ((section[10] & 0x0F) << 8 | section[11])
    video = audio = None
    while pos + 5 <= end:
        kind = section[pos]
        if kind in _TS_VIDEO:
            video = video or _TS_VIDEO[kind]
        elif kind in _TS_AUDIO:
            audio = audio or _TS_AUDIO[kind]
        pos += 5 + ((section[pos + 3] & 0x0F) << 8 | section[pos + 4])
    return video, audio


def hls_codecs(attribute: str) -> Codecs:
    """The ``CODECS`` attribute of an ``EXT-X-STREAM-INF`` line."""
    video = audio = None
    for value in (v.strip().lower() for v in attribute.split(",")):
        found = next((n for p, n in _HLS_VIDEO.items() if value.startswith(p)), None)
        if found:
            video = video or found
            continue
        found = next((n for p, n in _HLS_AUDIO.items() if value.startswith(p)), None)
        if found:
            audio = audio or found
    return video, audio
