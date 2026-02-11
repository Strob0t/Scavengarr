"""Tests for Stremio domain entities."""

from __future__ import annotations

import pytest

from scavengarr.domain.entities.stremio import (
    RankedStream,
    StreamLanguage,
    StreamQuality,
    StremioMetaPreview,
    StremioStream,
    StremioStreamRequest,
)


class TestStreamQuality:
    def test_ordering(self) -> None:
        assert StreamQuality.UHD_4K > StreamQuality.HD_1080P
        assert StreamQuality.HD_1080P > StreamQuality.HD_720P
        assert StreamQuality.HD_720P > StreamQuality.SD
        assert StreamQuality.SD > StreamQuality.TS
        assert StreamQuality.TS > StreamQuality.CAM
        assert StreamQuality.CAM > StreamQuality.UNKNOWN

    def test_values(self) -> None:
        assert StreamQuality.UNKNOWN == 0
        assert StreamQuality.CAM == 10
        assert StreamQuality.TS == 20
        assert StreamQuality.SD == 30
        assert StreamQuality.HD_720P == 40
        assert StreamQuality.HD_1080P == 50
        assert StreamQuality.UHD_4K == 60

    def test_comparison_with_int(self) -> None:
        assert StreamQuality.HD_1080P > 40
        assert StreamQuality.HD_720P == 40


class TestStreamLanguage:
    def test_frozen(self) -> None:
        lang = StreamLanguage(code="de", label="German Dub", is_dubbed=True)
        with pytest.raises(AttributeError):
            lang.code = "en"  # type: ignore[misc]

    def test_fields(self) -> None:
        lang = StreamLanguage(code="en-sub", label="English Sub", is_dubbed=False)
        assert lang.code == "en-sub"
        assert lang.label == "English Sub"
        assert lang.is_dubbed is False


class TestRankedStream:
    def test_frozen(self) -> None:
        stream = RankedStream(url="https://example.com", hoster="voe")
        with pytest.raises(AttributeError):
            stream.url = "other"  # type: ignore[misc]

    def test_defaults(self) -> None:
        stream = RankedStream(url="https://example.com", hoster="voe")
        assert stream.quality == StreamQuality.UNKNOWN
        assert stream.language is None
        assert stream.size is None
        assert stream.release_name is None
        assert stream.source_plugin == ""
        assert stream.rank_score == 0

    def test_full_construction(self) -> None:
        lang = StreamLanguage(code="de", label="German Dub", is_dubbed=True)
        stream = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_1080P,
            language=lang,
            size="1.2 GB",
            release_name="Movie.2024.1080p.WEB-DL",
            source_plugin="hdfilme",
            rank_score=1500,
        )
        assert stream.quality == StreamQuality.HD_1080P
        assert stream.language is not None
        assert stream.language.code == "de"
        assert stream.size == "1.2 GB"


class TestStremioStream:
    def test_frozen(self) -> None:
        s = StremioStream(name="HDFilme 1080p", description="German", url="https://x")
        with pytest.raises(AttributeError):
            s.name = "other"  # type: ignore[misc]

    def test_fields(self) -> None:
        s = StremioStream(
            name="Aniworld 720p",
            description="German Dub | VOE",
            url="https://voe.sx/e/123",
        )
        assert s.name == "Aniworld 720p"
        assert s.description == "German Dub | VOE"
        assert s.url == "https://voe.sx/e/123"


class TestStremioMetaPreview:
    def test_defaults(self) -> None:
        meta = StremioMetaPreview(id="tt1234567", type="movie", name="Test Movie")
        assert meta.poster == ""
        assert meta.description == ""
        assert meta.release_info == ""
        assert meta.imdb_rating == ""
        assert meta.genres == []

    def test_full_construction(self) -> None:
        meta = StremioMetaPreview(
            id="tt1234567",
            type="series",
            name="Test Series",
            poster="https://image.tmdb.org/t/p/w500/abc.jpg",
            description="A test series",
            release_info="2024",
            imdb_rating="8.5",
            genres=["Action", "Drama"],
        )
        assert meta.type == "series"
        assert len(meta.genres) == 2

    def test_frozen(self) -> None:
        meta = StremioMetaPreview(id="tt1", type="movie", name="X")
        with pytest.raises(AttributeError):
            meta.name = "Y"  # type: ignore[misc]


class TestStremioStreamRequest:
    def test_movie_request(self) -> None:
        req = StremioStreamRequest(imdb_id="tt1234567", content_type="movie")
        assert req.imdb_id == "tt1234567"
        assert req.content_type == "movie"
        assert req.season is None
        assert req.episode is None

    def test_series_request(self) -> None:
        req = StremioStreamRequest(
            imdb_id="tt1234567",
            content_type="series",
            season=1,
            episode=5,
        )
        assert req.season == 1
        assert req.episode == 5

    def test_frozen(self) -> None:
        req = StremioStreamRequest(imdb_id="tt1", content_type="movie")
        with pytest.raises(AttributeError):
            req.imdb_id = "tt2"  # type: ignore[misc]
