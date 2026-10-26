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
# The boxes on the way to a track header: the movie and its tracks
_CONTAINERS = frozenset({b"moov", b"trak"})
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


def _track_sizes(
    data: bytes, start: int, end: int, *, in_track: bool = False
) -> Iterator[tuple[int, int]]:
    """The width and height of each track header (``tkhd``) of a track in
    ``data[start:end]`` whose size the bytes hold."""
    for kind, payload, box_end in _boxes(data, start, end):
        inner = min(box_end, end)
        if in_track and kind == b"tkhd":
            size = _tkhd_size(data, payload, inner)
            if size is not None:
                yield size
        elif kind in _CONTAINERS:
            yield from _track_sizes(data, payload, inner, in_track=kind == b"trak")


def _tkhd_size(data: bytes, payload: int, end: int) -> tuple[int, int] | None:
    """A track header's width and height (16.16 fixed point), ``None`` when
    the bytes cut them or the track has none (an audio track's 0x0)."""
    if payload >= end:
        return None
    at = payload + _TKHD_SIZE_AT.get(data[payload], _TKHD_SIZE_AT[0])
    if at + 8 > end:
        return None
    width, height = struct.unpack_from(">II", data, at)
    width, height = width >> 16, height >> 16
    return (width, height) if width and height else None


def video_size(data: bytes) -> tuple[int, int] | None:
    """The largest width and height a track header of the ``moov`` box at
    the top level of *data* names, ``None`` without one in the bytes."""
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
