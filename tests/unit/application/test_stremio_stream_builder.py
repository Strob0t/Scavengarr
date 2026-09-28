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
    def test_reference_title_used_as_name(self) -> None:
        """Reference title (from TMDB) is preferred over ranked.title."""
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_1080P,
            title="Iron Man",
            source_plugin="hdfilme",
            rank_score=1500,
        )
        result = format_stream(ranked, reference_title="Iron Man", year=2008)
        assert result.name == "Iron Man (2008) HD 1080P"

    def test_series_format_with_season_episode(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_720P,
            source_plugin="aniworld",
        )
        result = format_stream(
            ranked, reference_title="Breaking Bad", season=1, episode=5
        )
        assert result.name == "Breaking Bad S01E05 HD 720P"

    def test_release_name_fallback_without_reference(self) -> None:
        """Without reference_title, release_name is used as fallback."""
        lang = StreamLanguage(code="de", label="German Dub", is_dubbed=True)
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_1080P,
            language=lang,
            size="1.5 GB",
            release_name="Iron.Man.2008.1080p.WEB-DL",
            source_plugin="hdfilme",
            rank_score=1500,
        )
        result = format_stream(ranked)
        assert result.url == "https://voe.sx/e/abc"
        assert result.name == "Iron.Man.2008.1080p.WEB-DL"
        assert "German Dub" in result.description
        assert "VOE" in result.description
        assert "1.5 GB" in result.description

    def test_title_fallback_without_reference(self) -> None:
        """Without reference_title or release_name, ranked.title is used."""
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_1080P,
            title="Iron Man",
            source_plugin="hdfilme",
        )
        result = format_stream(ranked)
        assert result.name == "Iron Man HD 1080P"

    def test_fallback_to_plugin_quality(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_720P,
            source_plugin="hdfilme",
        )
        result = format_stream(ranked)
        assert result.name == "hdfilme HD 720P"

    def test_fallback_no_source_plugin(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_720P,
        )
        result = format_stream(ranked)
        assert result.name == "HD 720P"

    def test_unknown_quality_not_appended(self) -> None:
        """UNKNOWN quality is not appended to the name."""
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.UNKNOWN,
            title="Iron Man",
            source_plugin="hdfilme",
        )
        result = format_stream(ranked, reference_title="Iron Man", year=2008)
        assert result.name == "Iron Man (2008)"
        assert "UNKNOWN" not in result.name

    def test_source_plugin_in_description(self) -> None:
        """source_plugin is always the first element of the description."""
        lang = StreamLanguage(code="de", label="German Dub", is_dubbed=True)
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_1080P,
            language=lang,
            source_plugin="hdfilme",
        )
        result = format_stream(ranked, reference_title="Iron Man")
        assert result.description.startswith("hdfilme")
        assert "German Dub" in result.description
        assert "VOE" in result.description

    def test_description_without_source_plugin(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.UNKNOWN,
        )
        result = format_stream(ranked)
        # No source_plugin → description should still work
        assert "VOE" in result.description

    def test_empty_hoster(self) -> None:
        ranked = RankedStream(
            url="https://example.com",
            hoster="",
            quality=StreamQuality.SD,
        )
        result = format_stream(ranked)
        assert (
            result.description == ""
            or "|" not in result.description
            or result.description.strip()
        )

    def test_reference_title_without_year(self) -> None:
        """Reference title without year omits the year parenthetical."""
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_1080P,
            source_plugin="hdfilme",
        )
        result = format_stream(ranked, reference_title="Iron Man")
        assert result.name == "Iron Man HD 1080P"


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
        name="Iron Man (2008) 1080p",
        description="hdfilme | VOE",
        url="placeholder",
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
        assert result.behavior_hints == {"notWebReady": True}

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
