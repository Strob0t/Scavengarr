"""Tests for scripts/media_codecs.py on synthetic boxes and TS packets."""

from __future__ import annotations

import importlib.util
import struct
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "media_codecs", _SCRIPTS / "media_codecs.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load()


def box(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def trak(handler: bytes, entry: bytes, tables: int = 0) -> bytes:
    """A track: its handler, one sample entry, and *tables* bytes of sample
    tables after the ``stsd`` (a long film's tables run to megabytes)."""
    stsd = box(b"stsd", b"\0" * 4 + struct.pack(">I", 1) + box(entry, b"\0" * 8))
    hdlr = box(b"hdlr", b"\0" * 8 + handler + b"\0" * 12)
    stbl = box(b"stbl", stsd + box(b"stsz", b"\0" * tables))
    return box(b"trak", box(b"mdia", hdlr + box(b"minf", stbl)))


FTYP = box(b"ftyp", b"isom\0\0\0\0isomiso2")
MOOV = box(
    b"moov",
    box(b"mvhd", b"\0" * 100)
    + trak(b"vide", b"hvc1", tables=4000)
    + trak(b"soun", b"ec-3"),
)
MDAT = box(b"mdat", b"\0" * 6000)
FASTSTART = FTYP + MOOV + MDAT
TAIL_MOOV = FTYP + MDAT + MOOV


class TestMp4:
    def test_codecs_of_a_moov(self) -> None:
        assert _mod.mp4_codecs(FASTSTART) == ("hevc", "ec-3")

    @pytest.mark.parametrize(
        ("entries", "pair"),
        [
            ((b"avc1", b"mp4a"), ("h264", "aac")),
            ((b"avc3", b"ac-3"), ("h264", "ac-3")),
            ((b"hev1", b"Opus"), ("hevc", "opus")),
        ],
    )
    def test_sample_entry_names(
        self, entries: tuple[bytes, bytes], pair: tuple[str, str]
    ) -> None:
        moov = box(b"moov", trak(b"soun", entries[1]) + trak(b"vide", entries[0]))
        assert _mod.mp4_codecs(FTYP + moov) == pair

    def test_a_cut_moov_still_names_the_tracks_it_holds(self) -> None:
        cut = FASTSTART[: len(FTYP) + 400]
        assert _mod.mp4_codecs(cut) == ("hevc", None)

    def test_faststart_moov_lies_in_the_head(self) -> None:
        start = len(FTYP)
        assert _mod.moov_place(FASTSTART[:2048]) == ("head", start, len(MOOV))

    def test_moov_after_mdat_is_at_the_tail(self) -> None:
        assert _mod.moov_place(TAIL_MOOV[:2048]) == ("tail", 0, 0)

    def test_a_head_of_no_boxes_has_no_place(self) -> None:
        assert _mod.moov_place(b"\0\0\0\x20ftypisom" + b"\0" * 100) is None

    def test_the_moov_in_a_tail_read(self) -> None:
        tail = TAIL_MOOV[-(len(MOOV) + 300) :]
        assert _mod.mp4_codecs(_mod.tail_moov(tail)) == ("hevc", "ec-3")
        assert _mod.tail_moov(TAIL_MOOV[-200:]) == b""


def _packet(pid: int, section: bytes) -> bytes:
    """A TS packet starting a PSI section (pointer field 0), 0xFF padded."""
    head = bytes([0x47, 0x40 | (pid >> 8), pid & 0xFF, 0x10, 0x00])
    return (head + section).ljust(188, b"\xff")


def _section(table_id: int, body: bytes) -> bytes:
    length = len(body) + 4  # the CRC
    return bytes([table_id, 0xB0 | (length >> 8), length & 0xFF]) + body + b"\0" * 4


PAT = _packet(0, _section(0x00, b"\0\x01\xc1\0\0" + b"\0\x01\xe1\x00"))
PMT = _packet(
    0x100,
    _section(
        0x02,
        b"\0\x01\xc1\0\0"
        + b"\xe1\x01\xf0\x00"
        + b"\x1b\xe1\x01\xf0\x00"  # H.264 on PID 0x101
        + b"\x81\xe1\x02\xf0\x00",  # AC-3 on PID 0x102
    ),
)


class TestTs:
    def test_pmt_stream_types(self) -> None:
        assert _mod.ts_codecs(PAT + PMT) == ("h264", "ac-3")

    def test_no_pmt_no_codecs(self) -> None:
        assert _mod.ts_codecs(b"\x47" + b"\0" * 187) == (None, None)


class TestHlsCodecs:
    @pytest.mark.parametrize(
        ("attr", "pair"),
        [
            ("avc1.640028,mp4a.40.2", ("h264", "aac")),
            ("hvc1.2.4.L120.B0,ec-3", ("hevc", "ec-3")),
            ("avc1.4d401f,mp4a.a5", ("h264", "ac-3")),
            ("mp4a.40.2", (None, "aac")),
        ],
    )
    def test_codecs_attribute(self, attr: str, pair: tuple[str, str]) -> None:
        assert _mod.hls_codecs(attr) == pair


@pytest.mark.parametrize(
    ("video", "audio", "text"),
    [
        ("h264", "aac", "h264/aac"),
        ("hevc", None, "hevc/?"),
        (None, None, "codecs unread"),
    ],
)
def test_pair(video: str | None, audio: str | None, text: str) -> None:
    assert _mod.pair(video, audio) == text
