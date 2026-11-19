"""The video size an MP4 file's header boxes name (ISO/IEC 14496-12).

The playback check reads the first 4 KiB of a file. A file whose ``moov``
box comes first (a "fast start" file) names its tracks' sizes there; one
whose ``moov`` follows the media data (``mdat``) shows where it starts.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator

_HEADER = 8
_LARGE_HEADER = 16
# A box of size 0 runs to the end of the file
_TO_THE_END = 2**63
# The handler type of a video track, and the flag of an enabled track
_VIDEO = b"vide"
_TRACK_ENABLED = 0x1
# Where a track header's width starts, after version and flags, times,
# track id and duration (20 bytes, 32 in version 1), layer, volume and
# matrix (52 bytes)
_TKHD_SIZE_AT = {0: 4 + 20 + 52, 1: 4 + 32 + 52}


def _boxes(data: bytes, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """The boxes in ``data[start:end]`` as type, payload offset and end (past
    *end* for a box the bytes cut). Stops at a header the bytes cut and at
    a size smaller than its header."""
    offset = start
    while offset + _HEADER <= end:
        size, kind = struct.unpack_from(">I4s", data, offset)
        payload = offset + _HEADER
        if size == 1:
            if offset + _LARGE_HEADER > end:
                return
            (size,) = struct.unpack_from(">Q", data, payload)
            payload = offset + _LARGE_HEADER
        elif size == 0:
            size = _TO_THE_END
        if size < payload - offset:
            return
        yield kind, payload, offset + size
        offset += size


def _track_sizes(data: bytes, start: int, end: int) -> Iterator[tuple[int, int]]:
    """The width and height of each video track in ``data[start:end]`` whose
    size the bytes hold: the boxes on the way to a track, then the track."""
    for kind, payload, box_end in _boxes(data, start, end):
        inner = min(box_end, end)
        if kind == b"trak":
            size = _track_size(data, payload, inner)
            if size is not None:
                yield size
        elif kind == b"moov":
            yield from _track_sizes(data, payload, inner)


def _track_size(data: bytes, start: int, end: int) -> tuple[int, int] | None:
    """The size of the track in ``data[start:end]``: its header's, when the
    track is enabled and its handler (``hdlr`` in ``mdia``) is ``vide``. A
    cover, text or disabled track and one whose handler the bytes cut name
    none."""
    size = None
    video = False
    for kind, payload, box_end in _boxes(data, start, end):
        inner = min(box_end, end)
        if kind == b"tkhd":
            size = _tkhd_size(data, payload, inner)
        elif kind == b"mdia":
            video = _handler(data, payload, inner) == _VIDEO
    return size if video else None


def _handler(data: bytes, start: int, end: int) -> bytes | None:
    """The handler type of the ``hdlr`` box in the media box
    ``data[start:end]``, after version and flags and the predefined field."""
    for kind, payload, box_end in _boxes(data, start, end):
        if kind == b"hdlr":
            at = payload + 8
            return data[at : at + 4] if at + 4 <= min(box_end, end) else None
    return None


def _tkhd_size(data: bytes, payload: int, end: int) -> tuple[int, int] | None:
    """A track header's width and height (16.16 fixed point), ``None`` when
    the bytes cut them, the header's version is not 0 or 1, the track is
    disabled or has no size (an audio track's 0x0)."""
    if payload + 4 > end:
        return None
    version = data[payload]
    flags = int.from_bytes(data[payload + 1 : payload + 4], "big")
    if version not in _TKHD_SIZE_AT or not flags & _TRACK_ENABLED:
        return None
    at = payload + _TKHD_SIZE_AT[version]
    if at + 8 > end:
        return None
    width, height = struct.unpack_from(">II", data, at)
    width, height = width >> 16, height >> 16
    return (width, height) if width and height else None


def video_size(data: bytes) -> tuple[int, int] | None:
    """The largest width and height an enabled video track of the ``moov``
    box at the top level of *data* names, ``None`` without one in the bytes."""
    return max(
        _track_sizes(data, 0, len(data)),
        key=lambda size: size[0] * size[1],
        default=None,
    )


def moov_offset(head: bytes, total: int) -> int | None:
    """Where the box after an MP4 file's media data starts (its ``moov``),
    from the file's first bytes *head* and its *total* size. ``None`` for a
    file that does not start with ``ftyp``, whose ``moov`` comes first,
    whose media data runs to the end, or that ends with it."""
    if head[4:8] != b"ftyp":
        return None
    end = 0
    after_media = False
    for kind, _payload, box_end in _boxes(head, 0, len(head)):
        if kind == b"moov":
            return None
        after_media = after_media or kind == b"mdat"
        end = box_end
    if after_media and end + _HEADER <= total:
        return end
    return None
