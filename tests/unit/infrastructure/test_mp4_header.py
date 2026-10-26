"""Tests for the video size read from an MP4 file's header boxes."""

from __future__ import annotations

import struct

from scavengarr.infrastructure.hoster_resolvers._mp4 import moov_offset, video_size


def _box(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def _tkhd(width: int, height: int, *, version: int = 0) -> bytes:
    """A track header: version and flags, times, track id and duration (20
    bytes, 32 in version 1), layer, volume and matrix, then the size in
    16.16 fixed point."""
    times = bytes(20 if version == 0 else 32)
    size = struct.pack(">II", width << 16, height << 16)
    return _box(b"tkhd", bytes([version, 0, 0, 7]) + times + bytes(52) + size)


def _trak(width: int, height: int, *, version: int = 0, media: int = 0) -> bytes:
    return _box(
        b"trak", _tkhd(width, height, version=version) + _box(b"mdia", bytes(media))
    )


def _moov(*tracks: bytes) -> bytes:
    return _box(b"moov", _box(b"mvhd", bytes(100)) + b"".join(tracks))


_FTYP = _box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41")


def _mdat_header(size: int) -> bytes:
    """The header of a media data box of *size* bytes (a 64-bit size past
    4 GiB)."""
    if size < 2**32:
        return struct.pack(">I4s", size, b"mdat")
    return struct.pack(">I4sQ", 1, b"mdat", size)


class TestVideoSize:
    def test_fast_start_file_names_its_video_size(self) -> None:
        head = _FTYP + _moov(_trak(1920, 800)) + _mdat_header(10**9)
        assert video_size(head) == (1920, 800)

    def test_the_video_track_after_an_audio_track(self) -> None:
        # An audio track's header names no size (0x0)
        head = _FTYP + _moov(_trak(0, 0), _trak(1280, 720))
        assert video_size(head) == (1280, 720)

    def test_version_1_track_header(self) -> None:
        head = _FTYP + _moov(_trak(3840, 2160, version=1))
        assert video_size(head) == (3840, 2160)

    def test_moov_after_a_free_box(self) -> None:
        head = _FTYP + _box(b"free", bytes(16)) + _moov(_trak(1920, 1080))
        assert video_size(head) == (1920, 1080)

    def test_a_moov_cut_by_the_read_still_names_its_first_track(self) -> None:
        # The 4 KiB read ends inside the track's media box
        head = (_FTYP + _moov(_trak(1920, 1080, media=10_000)))[:4096]
        assert video_size(head) == (1920, 1080)

    def test_a_track_header_cut_by_the_read_names_nothing(self) -> None:
        data = _FTYP + _moov(_trak(1920, 1080))
        cut = data.index(b"tkhd") + 60
        assert video_size(data[:cut]) is None

    def test_a_video_track_past_the_read_names_nothing(self) -> None:
        head = (_FTYP + _moov(_trak(0, 0, media=10_000), _trak(1920, 1080)))[:4096]
        assert video_size(head) is None

    def test_media_data_first_names_nothing(self) -> None:
        assert video_size(_FTYP + _mdat_header(10**9) + bytes(64)) is None

    def test_a_tkhd_outside_a_track_does_not_count(self) -> None:
        assert video_size(_FTYP + _box(b"udta", _tkhd(1920, 1080))) is None

    def test_other_bytes_name_nothing(self) -> None:
        assert video_size(b"\x1aE\xdf\xa3" + bytes(60)) is None
        assert video_size(b"") is None
        assert video_size(b"\x00\x00\x00\x02moov") is None


class TestMoovOffset:
    def test_moov_after_the_media_data(self) -> None:
        head = _FTYP + _mdat_header(10**9) + bytes(64)
        assert moov_offset(head, total=len(_FTYP) + 10**9 + 4000) == len(_FTYP) + 10**9

    def test_media_data_past_4_gib(self) -> None:
        size = 5 * 2**30
        head = _FTYP + _box(b"free", bytes(8)) + _mdat_header(size) + bytes(64)
        start = len(_FTYP) + 16
        assert moov_offset(head, total=start + size + 9000) == start + size

    def test_moov_first_needs_no_second_read(self) -> None:
        head = _FTYP + _moov(_trak(1920, 1080)) + _mdat_header(10**9)
        assert moov_offset(head, total=10**9 + 5000) is None

    def test_nothing_after_the_media_data(self) -> None:
        head = _FTYP + _mdat_header(10**9)
        assert moov_offset(head, total=len(_FTYP) + 10**9) is None

    def test_media_data_to_the_end_of_the_file(self) -> None:
        # Size 0: the box runs to the end of the file
        head = _FTYP + struct.pack(">I4s", 0, b"mdat") + bytes(64)
        assert moov_offset(head, total=10**9) is None

    def test_no_mp4_without_ftyp(self) -> None:
        head = _mdat_header(10**9) + bytes(64)
        assert moov_offset(head, total=10**9 + 5000) is None
