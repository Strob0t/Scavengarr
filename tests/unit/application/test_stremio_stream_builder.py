"""Tests for Stremio stream building helpers."""

from __future__ import annotations

import pytest

from scavengarr.application.stremio.stream_builder import (
    build_stream_from_resolved,
    deduplicate_by_hoster,
    format_stream,
    is_direct_video_url,
)
from scavengarr.domain.entities.stremio import (
    RankedStream,
    ResolvedStream,
    StreamLanguage,
    StreamQuality,
    StremioStream,
)
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

# ---------------------------------------------------------------------------
# format_stream
# ---------------------------------------------------------------------------


class TestFormatStream:
    """Stremio shows ``name`` in a narrow column and ``description`` beside it."""

    _DE = StreamLanguage(code="de", label="German Dub", is_dubbed=True)

    def test_name_is_addon_and_quality(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc", hoster="voe", quality=StreamQuality.HD_1080P
        )
        result = format_stream(ranked, reference_title="Iron Man", year=2008)
        assert result.name == "Scavengarr\n1080p"

    @pytest.mark.parametrize(
        ("quality", "label"),
        [
            (StreamQuality.UHD_4K, "4K"),
            (StreamQuality.HD_1080P, "1080p"),
            (StreamQuality.HD_720P, "720p"),
            (StreamQuality.SD, "SD"),
            (StreamQuality.TS, "TS"),
            (StreamQuality.CAM, "CAM"),
        ],
    )
    def test_quality_labels(self, quality: StreamQuality, label: str) -> None:
        ranked = RankedStream(url="https://voe.sx/e/abc", hoster="voe", quality=quality)
        assert format_stream(ranked).name == f"Scavengarr\n{label}"

    def test_unknown_quality_name_is_addon_only(self) -> None:
        ranked = RankedStream(url="https://voe.sx/e/abc", hoster="voe")
        assert format_stream(ranked).name == "Scavengarr"

    def test_description_lines(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_1080P,
            language=self._DE,
            size="1.5 GB",
            release_name="Iron.Man.2008.1080p.WEB-DL",
            title="Iron Man",
            source_plugin="hdfilme",
        )
        result = format_stream(ranked, reference_title="Iron Man", year=2008)
        assert result.description == (
            "Iron.Man.2008.1080p.WEB-DL\nGerman Dub · 1.5 GB\nVOE · hdfilme"
        )

    def test_source_title_without_release_name(self) -> None:
        # The site's own title shows a wrong match; the reference title hides it
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            language=self._DE,
            title="Iron Man 2",
            source_plugin="kinoger",
        )
        result = format_stream(ranked, reference_title="Iron Man", year=2008)
        assert result.description == "Iron Man 2\nGerman Dub\nVOE · kinoger"

    def test_reference_title_fallback_for_movie(self) -> None:
        ranked = RankedStream(url="https://voe.sx/e/abc", hoster="voe")
        result = format_stream(ranked, reference_title="Iron Man", year=2008)
        assert result.description == "Iron Man (2008)\nVOE"

    def test_reference_title_fallback_for_episode(self) -> None:
        ranked = RankedStream(url="https://voe.sx/e/abc", hoster="voe")
        result = format_stream(
            ranked, reference_title="Breaking Bad", year=2008, season=1, episode=5
        )
        assert result.description == "Breaking Bad S01E05\nVOE"

    def test_source_title_gets_episode(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc", hoster="voe", title="Breaking Bad"
        )
        result = format_stream(
            ranked, reference_title="Breaking Bad", season=2, episode=3
        )
        assert result.description == "Breaking Bad S02E03\nVOE"

    def test_title_with_the_episode_gets_it_once(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc", hoster="voe", title="Peaky Blinders s01e01"
        )
        result = format_stream(ranked, season=1, episode=1)
        assert result.description == "Peaky Blinders s01e01\nVOE"

    def test_release_name_keeps_its_own_episode(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            release_name="Breaking.Bad.S02E03.German.720p.WEB.x264",
        )
        result = format_stream(ranked, season=2, episode=3)
        assert result.description == "Breaking.Bad.S02E03.German.720p.WEB.x264\nVOE"

    def test_size_without_language(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc", hoster="voe", size="700 MB", title="Iron Man"
        )
        assert format_stream(ranked).description == "Iron Man\n700 MB\nVOE"

    def test_without_hoster(self) -> None:
        ranked = RankedStream(
            url="https://example.com/v", hoster="", title="Iron Man", source_plugin="x"
        )
        assert format_stream(ranked).description == "Iron Man\nx"

    def test_binge_group_is_language(self) -> None:
        # Stremio autoplays the next episode's first stream with the same group
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_720P,
            language=self._DE,
            source_plugin="aniworld",
        )
        hints = format_stream(ranked).behavior_hints
        assert hints is not None
        assert hints["bingeGroup"] == "scavengarr|de"

    def test_binge_group_without_language(self) -> None:
        ranked = RankedStream(url="https://voe.sx/e/abc", hoster="voe")
        hints = format_stream(ranked).behavior_hints
        assert hints is not None
        assert hints["bingeGroup"] == "scavengarr|unknown"

    def test_filename_from_release_name(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            release_name="Iron.Man.2008.German.DL.1080p.BluRay.x264",
        )
        hints = format_stream(ranked).behavior_hints
        assert hints is not None
        assert hints["filename"] == "Iron.Man.2008.German.DL.1080p.BluRay.x264"

    def test_no_filename_without_release_name(self) -> None:
        ranked = RankedStream(url="https://voe.sx/e/abc", hoster="voe", title="X")
        hints = format_stream(ranked).behavior_hints
        assert hints is not None
        assert "filename" not in hints


# ---------------------------------------------------------------------------
# deduplicate_by_hoster
# ---------------------------------------------------------------------------


class TestDeduplicateByHoster:
    def test_keeps_best_per_hoster(self) -> None:
        streams = [
            RankedStream(url="https://voe.sx/e/a", hoster="voe", rank_score=100),
            RankedStream(url="https://voe.sx/e/b", hoster="voe", rank_score=90),
            RankedStream(
                url="https://streamtape.com/v/c", hoster="streamtape", rank_score=80
            ),
        ]
        result = deduplicate_by_hoster(streams)
        assert len(result) == 2
        assert result[0].url == "https://voe.sx/e/a"
        assert result[1].url == "https://streamtape.com/v/c"

    def test_empty_hoster_always_kept(self) -> None:
        streams = [
            RankedStream(url="https://a.com/1", hoster="", rank_score=100),
            RankedStream(url="https://b.com/2", hoster="", rank_score=90),
            RankedStream(url="https://voe.sx/e/c", hoster="voe", rank_score=80),
        ]
        result = deduplicate_by_hoster(streams)
        assert len(result) == 3

    def test_single_stream_unchanged(self) -> None:
        streams = [
            RankedStream(url="https://voe.sx/e/a", hoster="voe", rank_score=100),
        ]
        result = deduplicate_by_hoster(streams)
        assert len(result) == 1
        assert result[0].url == "https://voe.sx/e/a"

    def test_empty_list(self) -> None:
        assert deduplicate_by_hoster([]) == []

    def test_all_unique_hosters(self) -> None:
        streams = [
            RankedStream(url="https://voe.sx/e/a", hoster="voe", rank_score=100),
            RankedStream(
                url="https://streamtape.com/v/b",
                hoster="streamtape",
                rank_score=90,
            ),
            RankedStream(url="https://dood.re/e/c", hoster="doodstream", rank_score=80),
        ]
        result = deduplicate_by_hoster(streams)
        assert len(result) == 3

    def test_preserves_sort_order(self) -> None:
        streams = [
            RankedStream(url="https://voe.sx/e/a", hoster="voe", rank_score=100),
            RankedStream(
                url="https://streamtape.com/v/b",
                hoster="streamtape",
                rank_score=90,
            ),
            RankedStream(url="https://voe.sx/e/c", hoster="voe", rank_score=80),
            RankedStream(url="https://dood.re/e/d", hoster="doodstream", rank_score=70),
            RankedStream(
                url="https://streamtape.com/v/e",
                hoster="streamtape",
                rank_score=60,
            ),
        ]
        result = deduplicate_by_hoster(streams)
        assert len(result) == 3
        assert [s.hoster for s in result] == ["voe", "streamtape", "doodstream"]

    def test_languages_of_one_hoster_are_kept(self) -> None:
        # Anime sites offer dub and sub on the same hoster: both are wanted
        dub = StreamLanguage(code="de", label="German Dub", is_dubbed=True)
        sub = StreamLanguage(code="de-sub", label="German Sub", is_dubbed=False)
        streams = [
            RankedStream(url="https://voe.sx/e/a", hoster="voe", language=dub),
            RankedStream(url="https://voe.sx/e/b", hoster="voe", language=sub),
            RankedStream(url="https://voe.sx/e/c", hoster="voe", language=dub),
        ]
        result = deduplicate_by_hoster(streams)
        assert [s.url for s in result] == ["https://voe.sx/e/a", "https://voe.sx/e/b"]

    def test_many_duplicates(self) -> None:
        streams = [
            RankedStream(url=f"https://voe.sx/e/{i}", hoster="voe", rank_score=100 - i)
            for i in range(10)
        ]
        result = deduplicate_by_hoster(streams)
        assert len(result) == 1
        assert result[0].url == "https://voe.sx/e/0"


# ---------------------------------------------------------------------------
# is_direct_video_url — detect actual video vs embed page URLs
# ---------------------------------------------------------------------------


class TestIsDirectVideoUrl:
    """Ensure only genuine video URLs are sent to Stremio."""

    def test_hls_m3u8_url(self) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.voe.sx/hls/master.m3u8",
            is_hls=True,
            headers={"Referer": "https://voe.sx/e/abc"},
        )
        assert is_direct_video_url(resolved, "https://voe.sx/e/abc") is True

    def test_mp4_url(self) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.example.com/video.mp4",
        )
        assert is_direct_video_url(resolved, "https://voe.sx/e/abc") is True

    def test_mkv_url(self) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.example.com/video.mkv",
        )
        assert is_direct_video_url(resolved, "https://voe.sx/e/abc") is True

    def test_hls_path_pattern(self) -> None:
        resolved = ResolvedStream(
            video_url="https://hfs.serversicuro.cc/hls/,token,.urlset/master.m3u8",
            is_hls=True,
        )
        assert is_direct_video_url(resolved, "https://supervideo.cc/e/abc") is True

    def test_streamtape_get_video(self) -> None:
        resolved = ResolvedStream(
            video_url="https://streamtape.com/get_video?id=abc&stream=1",
            headers={"Referer": "https://streamtape.com/"},
        )
        assert is_direct_video_url(resolved, "https://streamtape.com/e/abc") is True

    def test_xfs_embed_url_echoed_back(self) -> None:
        """XFS resolver returning the embed URL unchanged — NOT a video."""
        embed = "https://veev.to/e/2EwYsJS8frxAbWIzEhmWIJlqeGylzY9utsaUISu"
        resolved = ResolvedStream(video_url=embed)
        assert is_direct_video_url(resolved, embed) is False

    def test_xfs_embed_html_extension(self) -> None:
        embed = "https://vidmoly.to/embed-bvhzy03fsrcx.html"
        resolved = ResolvedStream(video_url=embed)
        assert is_direct_video_url(resolved, embed) is False

    def test_ddl_url_echoed_back(self) -> None:
        """DDL resolver returning the download page URL — NOT a video."""
        page = "https://dropload.tv/n2sostug0kwa"
        resolved = ResolvedStream(video_url=page)
        assert is_direct_video_url(resolved, page) is False

    def test_mixdrop_embed_echoed_back(self) -> None:
        page = "https://mixdrop.co/e/1vlvk1pli1w98k"
        resolved = ResolvedStream(video_url=page)
        assert is_direct_video_url(resolved, page) is False

    def test_different_url_with_headers(self) -> None:
        """Resolver returned a different URL + headers = actual extraction."""
        resolved = ResolvedStream(
            video_url="https://cdn.voe.sx/redirect/abc123",
            headers={"Referer": "https://voe.sx/e/abc"},
        )
        assert is_direct_video_url(resolved, "https://voe.sx/e/abc") is True

    def test_different_url_without_headers_no_extension(self) -> None:
        """Different URL but no headers and no video extension — ambiguous, reject."""
        resolved = ResolvedStream(
            video_url="https://ddownload.com/abc123",
        )
        assert (
            is_direct_video_url(resolved, "https://ddownload.com/abc123/file") is False
        )

    def test_is_hls_flag_overrides_all(self) -> None:
        """is_hls=True always means it's a video, regardless of URL."""
        resolved = ResolvedStream(
            video_url="https://weird-url.com/no-extension",
            is_hls=True,
        )
        assert is_direct_video_url(resolved, "https://embed.com/e/abc") is True

    def test_webm_extension(self) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.example.com/clip.webm",
        )
        assert is_direct_video_url(resolved, "https://example.com/e/abc") is True

    def test_ts_extension(self) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.example.com/segment.ts",
        )
        assert is_direct_video_url(resolved, "https://example.com/e/abc") is True


# ---------------------------------------------------------------------------
# build_stream_from_resolved
# ---------------------------------------------------------------------------


class TestBuildStreamFromResolved:
    """Unit tests for proxy URL construction in build_stream_from_resolved."""

    _STREAM = StremioStream(
        name="Scavengarr\n1080p",
        description="Iron Man\nVOE · hdfilme",
        url="placeholder",
        behavior_hints={"bingeGroup": "scavengarr|de"},
    )
    _BASE_URL = "http://localhost:7979"
    _SID = "abc123"
    _UA = DEFAULT_USER_AGENT

    def test_hls_with_headers_builds_proxy_url(self) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.dropcdn.io/hls2/video/master.m3u8?t=abc&expires=123",
            is_hls=True,
            headers={"Referer": "https://dropload.io/"},
        )
        result = build_stream_from_resolved(
            self._STREAM,
            resolved,
            "https://dropload.io/e/xyz",
            self._SID,
            self._BASE_URL,
            self._UA,
        )
        assert result is not None
        assert result.url == (
            f"{self._BASE_URL}/api/v1/stremio/proxy/{self._SID}"
            "/master.m3u8?t=abc&expires=123"
        )
        assert result.behavior_hints == {
            "bingeGroup": "scavengarr|de",
            "notWebReady": True,
        }

    def test_hls_with_headers_preserves_custom_filename(self) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.example.com/hls/index-v1-a1.m3u8?token=xyz",
            is_hls=True,
            headers={"Referer": "https://example.com/"},
        )
        result = build_stream_from_resolved(
            self._STREAM,
            resolved,
            "https://example.com/e/abc",
            self._SID,
            self._BASE_URL,
            self._UA,
        )
        assert result is not None
        assert "/index-v1-a1.m3u8?token=xyz" in result.url

    def test_hls_with_headers_no_query_string(self) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.example.com/hls/master.m3u8",
            is_hls=True,
            headers={"Referer": "https://example.com/"},
        )
        result = build_stream_from_resolved(
            self._STREAM,
            resolved,
            "https://example.com/e/abc",
            self._SID,
            self._BASE_URL,
            self._UA,
        )
        assert result is not None
        assert result.url.endswith(f"/proxy/{self._SID}/master.m3u8")
        assert "?" not in result.url

    def test_hls_without_headers_direct_url(self) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.example.com/video/master.m3u8",
            is_hls=True,
        )
        result = build_stream_from_resolved(
            self._STREAM,
            resolved,
            "https://example.com/e/abc",
            self._SID,
            self._BASE_URL,
            self._UA,
        )
        assert result is not None
        assert result.url == "https://cdn.example.com/video/master.m3u8"
        assert "proxyHeaders" in result.behavior_hints

    def test_direct_mp4_builds_direct_url(self) -> None:
        resolved = ResolvedStream(
            video_url="https://delivery.voe.sx/video.mp4",
            headers={"Referer": "https://voe.sx/"},
        )
        result = build_stream_from_resolved(
            self._STREAM,
            resolved,
            "https://voe.sx/e/abc",
            self._SID,
            self._BASE_URL,
            self._UA,
        )
        assert result is not None
        assert result.url == "https://delivery.voe.sx/video.mp4"
        assert (
            result.behavior_hints["proxyHeaders"]["request"]["Referer"]
            == "https://voe.sx/"
        )
        assert result.behavior_hints["bingeGroup"] == "scavengarr|de"
        assert result.behavior_hints["notWebReady"] is True

    def test_echo_url_returns_none(self) -> None:
        """When resolver echoes back the embed page URL, skip it."""
        original = "https://voe.sx/e/abc123"
        resolved = ResolvedStream(video_url=original)
        result = build_stream_from_resolved(
            self._STREAM, resolved, original, self._SID, self._BASE_URL, self._UA
        )
        assert result is None

    @pytest.mark.parametrize("status", [200, 206])
    def test_preserves_name_and_description(self, status: int) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.example.com/hls/master.m3u8",
            is_hls=True,
            headers={"Referer": "https://example.com/"},
        )
        result = build_stream_from_resolved(
            self._STREAM,
            resolved,
            "https://example.com/e/abc",
            self._SID,
            self._BASE_URL,
            self._UA,
        )
        assert result is not None
        assert result.name == self._STREAM.name
        assert result.description == self._STREAM.description
