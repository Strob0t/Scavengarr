"""Tests for CrawlJobFactory."""

from __future__ import annotations

from datetime import timedelta

from scavengarr.application.factories import CrawlJobFactory
from scavengarr.domain.entities.crawljob import BooleanStatus, Priority
from scavengarr.domain.plugins import SearchResult


class TestCrawlJobFactoryCreate:
    def test_maps_title_to_package_name(
        self, crawljob_factory: CrawlJobFactory, search_result: SearchResult
    ) -> None:
        job = crawljob_factory.create_from_search_result(search_result)
        assert job.package_name == search_result.title

    def test_maps_download_link_to_text(
        self, crawljob_factory: CrawlJobFactory, search_result: SearchResult
    ) -> None:
        job = crawljob_factory.create_from_search_result(search_result)
        assert job.text == search_result.download_link

    def test_maps_validated_urls(
        self, crawljob_factory: CrawlJobFactory, search_result: SearchResult
    ) -> None:
        job = crawljob_factory.create_from_search_result(search_result)
        assert job.validated_urls == [search_result.download_link]

    def test_maps_source_url(
        self, crawljob_factory: CrawlJobFactory, search_result: SearchResult
    ) -> None:
        job = crawljob_factory.create_from_search_result(search_result)
        assert job.source_url == search_result.source_url

    def test_maps_release_name_to_filename(
        self, crawljob_factory: CrawlJobFactory, search_result: SearchResult
    ) -> None:
        job = crawljob_factory.create_from_search_result(search_result)
        assert job.filename == search_result.release_name

    def test_ttl_reflected_in_expires_at(
        self, crawljob_factory: CrawlJobFactory, search_result: SearchResult
    ) -> None:
        job = crawljob_factory.create_from_search_result(search_result)
        delta = job.expires_at - job.created_at
        assert delta == timedelta(hours=1)

    def test_custom_ttl(self, search_result: SearchResult) -> None:
        factory = CrawlJobFactory(default_ttl_hours=2)
        job = factory.create_from_search_result(search_result)
        delta = job.expires_at - job.created_at
        assert delta == timedelta(hours=2)

    def test_generates_unique_job_ids(
        self, crawljob_factory: CrawlJobFactory, search_result: SearchResult
    ) -> None:
        job1 = crawljob_factory.create_from_search_result(search_result)
        job2 = crawljob_factory.create_from_search_result(search_result)
        assert job1.job_id != job2.job_id

    def test_auto_start_true(self, search_result: SearchResult) -> None:
        factory = CrawlJobFactory(auto_start=True)
        job = factory.create_from_search_result(search_result)
        assert job.auto_start == BooleanStatus.TRUE

    def test_auto_start_false(self, search_result: SearchResult) -> None:
        factory = CrawlJobFactory(auto_start=False)
        job = factory.create_from_search_result(search_result)
        assert job.auto_start == BooleanStatus.FALSE

    def test_default_priority(self, search_result: SearchResult) -> None:
        factory = CrawlJobFactory(default_priority=Priority.HIGH)
        job = factory.create_from_search_result(search_result)
        assert job.priority == Priority.HIGH

    def test_fallback_package_name_when_no_title(self) -> None:
        result = SearchResult(
            title="",
            download_link="http://x",
            metadata={},
        )
        factory = CrawlJobFactory()
        job = factory.create_from_search_result(result)
        assert job.package_name == "Scavengarr Download"


class TestMultiLinkCrawlJob:
    def test_multi_link_text(self, crawljob_factory: CrawlJobFactory) -> None:
        result = SearchResult(
            title="Movie",
            download_link="https://a.com/dl",
            validated_links=[
                "https://a.com/dl",
                "https://b.com/dl",
                "https://c.com/dl",
            ],
            metadata={},
        )
        job = crawljob_factory.create_from_search_result(result)
        assert job.text == "https://a.com/dl\r\nhttps://b.com/dl\r\nhttps://c.com/dl"

    def test_multi_link_validated_urls(self, crawljob_factory: CrawlJobFactory) -> None:
        result = SearchResult(
            title="Movie",
            download_link="https://a.com/dl",
            validated_links=["https://a.com/dl", "https://b.com/dl"],
            metadata={},
        )
        job = crawljob_factory.create_from_search_result(result)
        assert job.validated_urls == ["https://a.com/dl", "https://b.com/dl"]

    def test_fallback_to_download_link_when_no_validated_links(
        self, crawljob_factory: CrawlJobFactory
    ) -> None:
        result = SearchResult(
            title="Movie",
            download_link="https://a.com/dl",
            validated_links=None,
            metadata={},
        )
        job = crawljob_factory.create_from_search_result(result)
        assert job.text == "https://a.com/dl"
        assert job.validated_urls == ["https://a.com/dl"]


class TestBuildComment:
    def test_with_description_and_size(self, crawljob_factory: CrawlJobFactory) -> None:
        result = SearchResult(
            title="T",
            download_link="http://x",
            description="A great movie",
            size="4.5 GB",
            source_url="https://example.com/movie/1",
            metadata={},
        )
        comment = crawljob_factory._build_comment(result)
        assert "A great movie" in comment
        assert "Size: 4.5 GB" in comment
        assert "Source: https://example.com/movie/1" in comment

    def test_with_no_metadata(self, crawljob_factory: CrawlJobFactory) -> None:
        result = SearchResult(
            title="T",
            download_link="http://x",
            metadata={},
        )
        comment = crawljob_factory._build_comment(result)
        assert comment == "Downloaded via Scavengarr"

    def test_parts_joined_with_pipe(self, crawljob_factory: CrawlJobFactory) -> None:
        result = SearchResult(
            title="T",
            download_link="http://x",
            description="Desc",
            size="1 GB",
            metadata={},
        )
        comment = crawljob_factory._build_comment(result)
        assert " | " in comment
