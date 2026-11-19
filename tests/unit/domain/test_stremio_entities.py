"""Tests for Stremio domain entities."""

from __future__ import annotations

import pytest

from scavengarr.domain.entities.stremio import (
    EpisodeRef,
    RankedStream,
    StreamLanguage,
    StreamQuality,
    StremioMetaPreview,
    StremioStream,
    StremioStreamRequest,
    TitleMatches,
    is_partial_title,
    quality_from_resolution,
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


class TestQualityFromResolution:
    @pytest.mark.parametrize(
        ("width", "height", "quality"),
        [
            (1920, 1080, StreamQuality.HD_1080P),
            (1920, 800, StreamQuality.HD_1080P),  # letterboxed: by width
            (1248, 520, StreamQuality.HD_720P),  # cropped 720p
            (1280, 720, StreamQuality.HD_720P),
            (3840, 1600, StreamQuality.UHD_4K),
            (3840, 2160, StreamQuality.UHD_4K),
            (1824, 760, StreamQuality.HD_1080P),  # cropped 1080p
            (1216, 400, StreamQuality.HD_720P),  # the tolerance's edge
            (1215, 400, StreamQuality.SD),
            (1823, 700, StreamQuality.HD_720P),
            (3648, 1500, StreamQuality.UHD_4K),
            (3647, 1500, StreamQuality.HD_1080P),
            (720, 576, StreamQuality.SD),
            (854, 480, StreamQuality.SD),
            (640, 360, StreamQuality.SD),
            (None, None, StreamQuality.UNKNOWN),
        ],
    )
    def test_class_by_width_and_height(
        self, width: int | None, height: int | None, quality: StreamQuality
    ) -> None:
        assert quality_from_resolution(width, height) is quality


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


class TestPartialTitle:
    """A series named only by a shorter form of the request's series titles
    (normalised: lower-case ASCII words) is partial: the request's parent,
    or the site's short name for it."""

    def test_the_parent_is_partial(self) -> None:
        assert is_partial_title(["naruto"], ["naruto shippuden"])

    def test_an_episode_result_of_the_parent_is_partial(self) -> None:
        """sto's result title and its guessit title: neither holds
        "shippuden", the second is a part of the request's title."""
        titles = ["naruto s01e06 gefahrliche mission die reise ins land", "naruto"]

        assert is_partial_title(titles, ["naruto shippuden"])

    def test_the_full_title_is_not_partial(self) -> None:
        assert not is_partial_title(["naruto shippuden"], ["naruto shippuden"])
        assert not is_partial_title(
            ["naruto shippuden s01e06 endlich wieder nudelsuppe", "naruto shippuden"],
            ["naruto shippuden"],
        )

    def test_a_title_that_holds_a_request_title_whole_is_not_partial(self) -> None:
        """aniworld's "Yu-Gi-Oh!" is TMDB's title of Duel Monsters, however
        long another title of the request is."""
        assert not is_partial_title(
            ["yu gi oh"], ["yu gi oh duel monsters", "yu gi oh"]
        )

    def test_any_request_title_counts(self) -> None:
        """An original title the site's name shares no word with (ナルト 疾風伝
        transliterated) does not keep the parent from being partial."""
        assert is_partial_title(["naruto"], ["ji feng chuan", "naruto shippuden"])

    def test_other_words_are_not_partial(self) -> None:
        assert not is_partial_title(
            ["boruto naruto next generations"], ["naruto shippuden"]
        )
        assert not is_partial_title(["dragonball super"], ["dragon ball super"])

    def test_a_year_is_no_word(self) -> None:
        assert is_partial_title(["naruto 2002"], ["naruto shippuden"])

    def test_an_article_or_and_is_no_word(self) -> None:
        """A site name that drops the article or writes "&" for "and" (the
        normalising drops it) is the request's series, not its parent (code
        review 2026-10-12, finding 10)."""
        assert not is_partial_title(["walking dead"], ["the walking dead"])
        assert not is_partial_title(["rick morty"], ["rick and morty"])
        assert not is_partial_title(["simpsons"], ["die simpsons"])
        assert not is_partial_title(["bergdoktor"], ["der bergdoktor"])

    def test_the_parent_without_its_article_stays_partial(self) -> None:
        assert is_partial_title(["walking dead"], ["the walking dead world beyond"])
        assert is_partial_title(["rick morty"], ["rick and morty the anime"])

    def test_without_titles_nothing_is_partial(self) -> None:
        assert not is_partial_title(["naruto"], [])
        assert not is_partial_title([], ["naruto shippuden"])
        assert not is_partial_title([""], ["naruto shippuden"])


class TestTitleMatches:
    def test_defaults(self) -> None:
        matches = TitleMatches()

        assert matches.matching == []
        assert matches.partial == []


class TestEpisodeRef:
    def test_the_series_titles_default_to_none(self) -> None:
        assert EpisodeRef(1, 2).titles == ()
