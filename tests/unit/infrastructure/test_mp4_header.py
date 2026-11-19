"""Tests for the video size read from an MP4 file's header boxes."""

from __future__ import annotations

import struct

from scavengarr.infrastructure.hoster_resolvers._mp4 import moov_offset, video_size

from .mp4_support import FTYP, box, mdat_header, moov, tkhd, trak


class TestVideoSize:
    def test_fast_start_file_names_its_video_size(self) -> None:
        head = FTYP + moov(trak(1920, 800)) + mdat_header(10**9)
        assert video_size(head) == (1920, 800)

    def test_the_video_track_after_an_audio_track(self) -> None:
        # An audio track's header names no size (0x0)
        head = FTYP + moov(trak(0, 0), trak(1280, 720))
        assert video_size(head) == (1280, 720)

    def test_version_1_track_header(self) -> None:
        head = FTYP + moov(trak(3840, 2160, version=1))
        assert video_size(head) == (3840, 2160)

    def test_moov_after_a_free_box(self) -> None:
        head = FTYP + box(b"free", bytes(16)) + moov(trak(1920, 1080))
        assert video_size(head) == (1920, 1080)

    def test_a_moov_cut_by_the_read_still_names_its_first_track(self) -> None:
        # The 4 KiB read ends inside the track's media box
        head = (FTYP + moov(trak(1920, 1080, media=10_000)))[:4096]
        assert video_size(head) == (1920, 1080)

    def test_a_track_header_cut_by_the_read_names_nothing(self) -> None:
        data = FTYP + moov(trak(1920, 1080))
        cut = data.index(b"tkhd") + 60
        assert video_size(data[:cut]) is None

    def test_a_video_track_past_the_read_names_nothing(self) -> None:
        head = (FTYP + moov(trak(0, 0, media=10_000), trak(1920, 1080)))[:4096]
        assert video_size(head) is None

    def test_media_data_first_names_nothing(self) -> None:
        assert video_size(FTYP + mdat_header(10**9) + bytes(64)) is None

    def test_a_larger_cover_or_text_track_does_not_set_the_size(self) -> None:
        head = FTYP + moov(
            trak(4000, 4000, handler=b"pict"),
            trak(3000, 3000, handler=b"text"),
            trak(1280, 720),
        )
        assert video_size(head) == (1280, 720)

    def test_a_disabled_track_does_not_set_the_size(self) -> None:
        head = FTYP + moov(trak(3840, 2160, flags=0), trak(1920, 1080, flags=3))
        assert video_size(head) == (1920, 1080)

    def test_a_track_without_a_handler_in_the_bytes_names_nothing(self) -> None:
        bare = box(b"trak", tkhd(1920, 1080) + box(b"mdia", bytes(100)))
        assert video_size(FTYP + moov(bare)) is None

    def test_a_track_header_of_a_later_version_names_nothing(self) -> None:
        # Laid out like version 0, so that reading it as version 0 finds a size
        track = trak(1920, 1080).replace(b"tkhd\x00", b"tkhd\x02")
        assert b"tkhd\x02" in track
        assert video_size(FTYP + moov(track)) is None

    def test_a_tkhd_outside_a_track_does_not_count(self) -> None:
        assert video_size(FTYP + box(b"udta", tkhd(1920, 1080))) is None

    def test_other_bytes_name_nothing(self) -> None:
        assert video_size(b"\x1aE\xdf\xa3" + bytes(60)) is None
        assert video_size(b"") is None
        assert video_size(b"\x00\x00\x00\x02moov") is None


class TestMoovOffset:
    def test_moov_after_the_media_data(self) -> None:
        head = FTYP + mdat_header(10**9) + bytes(64)
        assert moov_offset(head, total=len(FTYP) + 10**9 + 4000) == len(FTYP) + 10**9

    def test_media_data_past_4_gib(self) -> None:
        size = 5 * 2**30
        head = FTYP + box(b"free", bytes(8)) + mdat_header(size) + bytes(64)
        start = len(FTYP) + 16
        assert moov_offset(head, total=start + size + 9000) == start + size

    def test_moov_first_needs_no_second_read(self) -> None:
        head = FTYP + moov(trak(1920, 1080)) + mdat_header(10**9)
        assert moov_offset(head, total=10**9 + 5000) is None

    def test_nothing_after_the_media_data(self) -> None:
        head = FTYP + mdat_header(10**9)
        assert moov_offset(head, total=len(FTYP) + 10**9) is None

    def test_media_data_to_the_end_of_the_file(self) -> None:
        # Size 0: the box runs to the end of the file
        head = FTYP + struct.pack(">I4s", 0, b"mdat") + bytes(64)
        assert moov_offset(head, total=10**9) is None

    def test_the_moov_header_must_fit_in_the_file(self) -> None:
        head = FTYP + mdat_header(10**9) + bytes(64)
        start = len(FTYP) + 10**9
        assert moov_offset(head, total=start + 8) == start
        assert moov_offset(head, total=start + 7) is None

    def test_a_64_bit_header_the_read_cuts_names_nothing(self) -> None:
        # The size field says 1, the 8 bytes of the real size are cut
        cut = struct.pack(">I4s", 1, b"mdat") + bytes(4)
        assert moov_offset(FTYP + cut, total=10**9) is None
        assert video_size(FTYP + cut) is None

    def test_a_64_bit_size_under_its_header_ends_the_boxes(self) -> None:
        # A real size of 12 is smaller than the 16 bytes of its header
        bad = struct.pack(">I4sQ", 1, b"mdat", 12)
        assert moov_offset(FTYP + bad + bytes(64), total=10**9) is None
        moov_bad = struct.pack(">I4sQ", 1, b"moov", 12) + trak(1920, 1080)
        assert video_size(FTYP + moov_bad) is None

    def test_a_64_bit_size_under_its_header_is_not_walked_past(self) -> None:
        # Its 12 would put the next box inside the size field: the last four
        # bytes (12) read as that box's size, the bytes after the header as
        # its type
        bad = struct.pack(">I4sQ", 1, b"mdat", 12) + b"free" + bytes(4)
        assert moov_offset(FTYP + bad, total=10**9) is None

    def test_no_mp4_without_ftyp(self) -> None:
        head = mdat_header(10**9) + bytes(64)
        assert moov_offset(head, total=10**9 + 5000) is None
