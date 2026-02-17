"""Tests for the shared video URL extraction utilities.

Covers:
- Dean Edwards packed JS unpacking
- HLS/MP4 URL extraction from unpacked JWPlayer config
- Combined extract_video_url with all strategies
"""

from __future__ import annotations

from scavengarr.infrastructure.hoster_resolvers._video_extract import (
    extract_hls_from_unpacked,
    extract_video_url,
    unpack_p_a_c_k,
)


# ---------------------------------------------------------------------------
# unpack_p_a_c_k
# ---------------------------------------------------------------------------


class TestUnpackPACK:
    def test_simple_packed_js(self) -> None:
        packed = (
            "eval(function(p,a,c,k,e,d)"
            "{e=function(c){return c};if(!''.replace(/^/,String))"
            "{while(c--)d[c]=k[c]||c;k=[function(e)"
            "{return d[e]}];e=function(){return'\\w+'};c=1};"
            "while(c--)if(k[c])p=p.replace(new RegExp('\\b'+e(c)+'\\b','g'),k[c]);"
            "return p}("
            "'0=[{1:\"https://cdn.example.com/video/master.m3u8\"}]'"
            ",2,2,'sources|file'.split('|'),0,{}))"
        )
        result = unpack_p_a_c_k(packed)
        assert result is not None
        assert "sources" in result
        assert "master.m3u8" in result

    def test_returns_none_for_non_packed(self) -> None:
        assert unpack_p_a_c_k("var x = 1;") is None

    def test_returns_none_for_empty(self) -> None:
        assert unpack_p_a_c_k("") is None


# ---------------------------------------------------------------------------
# extract_hls_from_unpacked
# ---------------------------------------------------------------------------


class TestExtractHlsFromUnpacked:
    def test_sources_file_pattern(self) -> None:
        js = 'sources:[{file:"https://cdn.example.com/video/master.m3u8"}]'
        result = extract_hls_from_unpacked(js)
        assert result == "https://cdn.example.com/video/master.m3u8"

    def test_file_pattern(self) -> None:
        js = 'file:"https://cdn.example.com/stream.m3u8"'
        result = extract_hls_from_unpacked(js)
        assert result == "https://cdn.example.com/stream.m3u8"

    def test_source_pattern_mp4(self) -> None:
        js = 'source:"https://cdn.example.com/movie.mp4"'
        result = extract_hls_from_unpacked(js)
        assert result == "https://cdn.example.com/movie.mp4"

    def test_escaped_quotes(self) -> None:
        js = "sources:[{file:\\'https://cdn.example.com/video/master.m3u8\\'}]"
        result = extract_hls_from_unpacked(js)
        assert result == "https://cdn.example.com/video/master.m3u8"

    def test_returns_none_for_no_video(self) -> None:
        assert extract_hls_from_unpacked("var x = 1;") is None


# ---------------------------------------------------------------------------
# extract_video_url — combined strategies
# ---------------------------------------------------------------------------


class TestExtractVideoUrl:
    def test_hls2_pattern(self) -> None:
        html = '<script>"hls2":"https://cdn.example.com/video.m3u8"</script>'
        result = extract_video_url(html)
        assert result == "https://cdn.example.com/video.m3u8"

    def test_jwplayer_sources(self) -> None:
        html = (
            '<script>sources:[{file:"https://cdn.example.com/video.m3u8"}]</script>'
        )
        result = extract_video_url(html)
        assert result == "https://cdn.example.com/video.m3u8"

    def test_direct_hls_url(self) -> None:
        html = '"https://cdn.example.com/stream/master.m3u8"'
        result = extract_video_url(html)
        assert result == "https://cdn.example.com/stream/master.m3u8"

    def test_mp4_in_sources(self) -> None:
        html = 'sources:[{file:"https://cdn.example.com/movie.mp4"}]'
        result = extract_video_url(html)
        assert result == "https://cdn.example.com/movie.mp4"

    def test_hls2_takes_priority_over_jwplayer(self) -> None:
        html = (
            '"hls2":"https://priority.example.com/video.m3u8"'
            'sources:[{file:"https://fallback.example.com/video.m3u8"}]'
        )
        result = extract_video_url(html)
        assert result == "https://priority.example.com/video.m3u8"

    def test_returns_none_for_plain_html(self) -> None:
        html = "<html><body><h1>Hello</h1></body></html>"
        assert extract_video_url(html) is None

    def test_skips_thumbnail_urls(self) -> None:
        html = '"https://cdn.example.com/thumbnail.m3u8"'
        assert extract_video_url(html) is None

    def test_skips_track_urls(self) -> None:
        html = '"https://cdn.example.com/track/subtitle.m3u8"'
        assert extract_video_url(html) is None

    def test_packed_js_extraction(self) -> None:
        packed_html = (
            "<script>eval(function(p,a,c,k,e,d)"
            "{e=function(c){return c};if(!''.replace(/^/,String))"
            "{while(c--)d[c]=k[c]||c;k=[function(e)"
            "{return d[e]}];e=function(){return'\\w+'};c=1};"
            "while(c--)if(k[c])p=p.replace(new RegExp('\\b'+e(c)+'\\b','g'),k[c]);"
            "return p}("
            "'0=[{1:\"https://cdn.example.com/packed.m3u8\"}]'"
            ",2,2,'sources|file'.split('|'),0,{}))</script>"
        )
        result = extract_video_url(packed_html)
        assert result == "https://cdn.example.com/packed.m3u8"
