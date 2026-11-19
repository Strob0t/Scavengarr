"""Tests for the MediathekViewWeb plugin (the public broadcasters' mediatheken).

The fixtures in ``tests/fixtures/json/mediathek/`` are real answers of the API
to the plugin's own query (title and topic, no trailers, newest first).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

from scavengarr.domain.entities.stremio import EpisodeRef
from scavengarr.domain.plugins.base import PluginUnreachableError, SearchResult
from scavengarr.infrastructure.stremio.release_parser import parse_language

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "mediathek.py"
_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "json" / "mediathek"
_API = "https://mediathekviewweb.de/api/query"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mediathek_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # As the plugin loader does: dataclasses find the module by its name
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_mod = _load_module()


def _answer(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _entry(**fields: Any) -> dict[str, Any]:
    """One entry of an API answer; the fields not given are a plain ARD film's."""
    entry: dict[str, Any] = {
        "channel": "ARD",
        "topic": "Filme in der ARD",
        "title": "Ein Film",
        "description": "Ein Film über etwas.",
        "timestamp": 1792354500,  # 2026-10-18 20:15 UTC, 22:15 in Germany
        "duration": 5400,
        "size": 1451229184,
        "url_website": "https://www.ardmediathek.de/video/ein-film",
        "url_subtitle": "",
        "url_video": "https://ctv-videos.daserste.de/de/f/ein_film_1280x720-50p-3200kbit.mp4",
        "url_video_low": "https://ctv-videos.daserste.de/de/f/ein_film_640x360-50p-1200kbit.mp4",
        "url_video_hd": "https://ctv-videos.daserste.de/de/f/ein_film_1920x1080-50p-5000kbit.mp4",
        "filmlisteTimestamp": 1792445460,
        "id": "id-ein-film",
    }
    entry.update(fields)
    return entry


def _page(entries: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    return {
        "result": {
            "results": entries,
            "queryInfo": {
                "resultCount": len(entries),
                "totalResults": len(entries) if total is None else total,
                "totalRelation": "eq",
            },
        },
        "err": None,
    }


def _body(request: httpx.Request) -> dict[str, Any]:
    """The query the plugin sent: the JSON of the ``query`` parameter."""
    return json.loads(request.url.params["query"])


def _serve(*answers: dict[str, Any]) -> respx.Route:
    """Answer the API's pages in order of their offset (1000 entries each)."""

    def _answer_for(request: httpx.Request) -> httpx.Response:
        index = _body(request)["offset"] // 1000
        return httpx.Response(200, json=answers[index])

    return respx.route(method="GET", url__startswith=_API).mock(side_effect=_answer_for)


def _plugin() -> Any:
    plugin = _mod.MediathekPlugin()
    plugin._client = httpx.AsyncClient()
    return plugin


def _described(count: int) -> list[dict[str, Any]]:
    """*count* versions with audio description: entries the plugin drops."""
    return [
        _entry(title=f"Film {n} (Audiodeskription)", topic=f"Film {n}", id=f"ad-{n}")
        for n in range(count)
    ]


def _language(result: SearchResult) -> str:
    links = result.download_links or []
    return links[0]["language"]


class TestQuery:
    @pytest.mark.asyncio()
    @respx.mock
    async def test_the_query_searches_title_and_topic_without_trailers(self) -> None:
        route = _serve(_page([]))

        await _plugin().search("The Whale")

        body = _body(route.calls[0].request)
        assert body["queries"] == [{"fields": ["title", "topic"], "query": "The Whale"}]
        assert body["duration_min"] == 299
        assert body["future"] is False
        assert (body["sortBy"], body["sortOrder"]) == ("timestamp", "desc")
        assert (body["offset"], body["size"]) == (0, 1000)

    @pytest.mark.asyncio()
    async def test_an_empty_query_asks_nothing(self) -> None:
        assert await _plugin().search("") == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_a_category_the_mediatheken_lack_asks_nothing(self) -> None:
        route = _serve(_page([_entry()]))

        assert await _plugin().search("Ein Film", category=7000) == []
        assert not route.called

    @pytest.mark.asyncio()
    @respx.mock
    async def test_an_api_error_is_no_result(self) -> None:
        respx.route(method="GET", url__startswith=_API).respond(
            200, json={"result": None, "err": ["bad query"]}
        )

        assert await _plugin().search("Ein Film") == []


class TestFailures:
    @pytest.mark.asyncio()
    @respx.mock
    async def test_a_first_page_that_times_out_is_a_timeout(self) -> None:
        respx.route(method="GET", url__startswith=_API).mock(
            side_effect=httpx.ReadTimeout("slow")
        )

        with pytest.raises(TimeoutError):
            await _plugin().search("Ein Film")

    @pytest.mark.asyncio()
    @respx.mock
    async def test_a_first_page_answering_503_is_unreachable(self) -> None:
        respx.route(method="GET", url__startswith=_API).respond(503)

        with pytest.raises(PluginUnreachableError):
            await _plugin().search("Ein Film")

    @pytest.mark.asyncio()
    @respx.mock
    async def test_a_later_page_that_fails_keeps_the_first(self) -> None:
        def _answer_for(request: httpx.Request) -> httpx.Response:
            if _body(request)["offset"]:
                return httpx.Response(503)
            entries = [_entry(), *_described(999)]
            return httpx.Response(200, json=_page(entries, total=1500))

        respx.route(method="GET", url__startswith=_API).mock(side_effect=_answer_for)

        results = await _plugin().search("Ein Film")

        assert [r.title for r in results] == ["Ein Film"]


class TestPages:
    @staticmethod
    def _films(start: int, count: int) -> list[dict[str, Any]]:
        return [
            _entry(title=f"Film {n}", topic=f"Film {n}", id=f"id-{n}")
            for n in range(start, start + count)
        ]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_a_full_page_ends_the_search(self) -> None:
        route = _serve(
            _page(self._films(0, 1000), total=2500),
            _page(self._films(1000, 1000), total=2500),
        )

        results = await _plugin().search("Film")

        assert len(results) == 1000
        assert route.call_count == 1

    @pytest.mark.asyncio()
    @respx.mock
    async def test_the_next_page_fills_the_results_up_to_1000(self) -> None:
        route = _serve(
            _page([*self._films(0, 600), *_described(400)], total=2500),
            _page(self._films(600, 1000), total=2500),
            _page(self._films(1600, 900), total=2500),
        )

        results = await _plugin().search("Film")

        assert len(results) == 1000
        assert (results[0].title, results[-1].title) == ("Film 0", "Film 999")
        offsets = [_body(call.request)["offset"] for call in route.calls]
        assert offsets == [0, 1000]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_no_page_is_read_past_the_total(self) -> None:
        route = _serve(_page(self._films(0, 3)))

        results = await _plugin().search("Film")

        assert len(results) == 3
        assert route.call_count == 1

    @pytest.mark.asyncio()
    @respx.mock
    async def test_dropped_entries_let_the_next_page_fill_the_results(self) -> None:
        # The first page's versions for accessibility leave room for the second
        route = _serve(
            _page(_described(1000), total=1002), _page(self._films(0, 2), total=1002)
        )

        results = await _plugin().search("Film")

        assert [r.title for r in results] == ["Film 0", "Film 1"]
        assert route.call_count == 2

    @pytest.mark.asyncio()
    @respx.mock
    async def test_at_most_three_pages_are_read(self) -> None:
        route = _serve(*[_page(_described(1000), total=9000) for _ in range(9)])

        assert await _plugin().search("Film") == []
        assert route.call_count == 3


class TestVersions:
    @pytest.mark.parametrize(
        "title",
        [
            "Ein Film (Audiodeskription)",
            "Ein Film - Audiodeskription",
            "Ein Film (mit Audiodeskription)",
            "Ein Film (Hörfassung)",
            "Ein Film (Gebärdensprache)",
            "Ein Film - Gebärdensprache",
            "Ein Film (mit Gebärdensprache)",
            "Ein Film (Video in klarer Sprache)",
            "Ein Film (1981) (klare Sprache)",
            "Ein Film (mit Untertitel)",
            "Ein Film (DGS)",
        ],
    )
    def test_a_version_for_accessibility_is_dropped(self, title: str) -> None:
        assert _mod._read_entry(_entry(title=title, topic="Ein Film")) is None

    @pytest.mark.parametrize(
        ("title", "language"),
        [
            ("Ein Film", "German"),
            ("Ein Film (Originalversion mit Untertitel)", "German Sub"),
            ("Ein Film (OmU)", "German Sub"),
            ("Ein Film (Englisch)", "English"),
            ("Ein Film (eng)", "English"),
            ("Ein Film (Originalversion)", "Original"),
            ("Ein Film (OV)", "Original"),
            # An Austrian film in its own German
            ("Ein Film (Österreich. OV)", "German"),
        ],
    )
    def test_a_language_version_is_a_result_of_that_language(
        self, title: str, language: str
    ) -> None:
        broadcast = _mod._read_entry(_entry(title=title, topic="Ein Film"))

        assert broadcast is not None
        assert broadcast.title == "Ein Film"
        assert broadcast.language == language

    @pytest.mark.parametrize(
        "channel", ["ARTE.FR", "ARTE.EN", "ARTE.ES", "ARTE.IT", "ARTE.PL"]
    )
    def test_arte_in_another_language_is_dropped(self, channel: str) -> None:
        assert _mod._read_entry(_entry(channel=channel)) is None

    def test_arte_in_german_is_kept_as_arte(self) -> None:
        broadcast = _mod._read_entry(_entry(channel="ARTE.DE", topic="Kino - Filme"))

        assert broadcast is not None
        assert broadcast.channel == "ARTE"

    @pytest.mark.parametrize(
        "channel",
        ["3Sat", "ARD", "ARD-alpha", "ARTE.DE", "BR", "DW", "Funk.net", "HR", "KiKA"]
        + ["MDR", "NDR", "ONE", "ORF", "PHOENIX", "Radio Bremen TV", "RBB", "SR"]
        + ["SRF", "SWR", "WDR", "ZDF", "ZDF-tivi", "ZDFinfo", "ZDFneo"],
    )
    def test_the_release_name_names_no_other_language(self, channel: str) -> None:
        # The Stremio streams read the release name before the link's language:
        # guessit took "BR" for Brazilian Portuguese
        broadcast = _mod._read_entry(_entry(channel=channel))

        assert broadcast is not None
        language = parse_language(
            release_name=broadcast.release_name, link_language=broadcast.language
        )
        assert language is not None
        assert language.code == "de"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_the_fixture_keeps_one_film_per_language(self) -> None:
        _serve(_answer("the_whale"))

        results = await _plugin().search("The Whale")

        assert sorted(_language(r) for r in results) == ["German", "Original"]
        assert {r.title for r in results} == {"The Whale"}


class TestLinks:
    def test_the_hd_file_is_taken(self) -> None:
        broadcast = _mod._read_entry(_entry())

        assert broadcast is not None
        assert broadcast.url.endswith("ein_film_1920x1080-50p-5000kbit.mp4")
        assert broadcast.quality == "1080p"

    def test_without_an_hd_file_the_normal_one_is_taken(self) -> None:
        broadcast = _mod._read_entry(_entry(url_video_hd=""))

        assert broadcast is not None
        assert broadcast.url.endswith("ein_film_1280x720-50p-3200kbit.mp4")
        assert broadcast.quality == "720p"

    def test_an_entry_without_a_file_is_dropped(self) -> None:
        assert _mod._read_entry(_entry(url_video_hd="", url_video="")) is None

    @pytest.mark.parametrize(
        ("url", "field", "quality"),
        [
            (
                "https://hr-progressive.ard-mcdn.de/a/b_1920x1080-50p-5000kbit.mp4",
                "url_video_hd",
                "1080p",
            ),
            ("https://swr-pd.ard-mcdn.de/a/123.avc-720.mp4", "url_video", "720p"),
            (
                "https://mdr-progressive.ard-mcdn.de/a/mdrde-x.avc1080.mp4",
                "url_video_hd",
                "1080p",
            ),
            (
                "https://ndr-progressive.ard-mcdn.de/a/TV-1-2.1080.mp4",
                "url_video_hd",
                "1080p",
            ),
            (
                "https://rbprogressivedl-a.akamaihd.net/a/x_video_540p.mp4",
                "url_video",
                "SD",
            ),
            (
                "https://nrodlzdf-a.akamaihd.net/a/x_6660k_p37v17.mp4",
                "url_video_hd",
                "1080p",
            ),
            (
                "https://nrodlzdf-a.akamaihd.net/a/x_3360k_p36v17.mp4",
                "url_video",
                "720p",
            ),
            ("https://nrodlzdf-a.akamaihd.net/a/x_2360k_p35v17.mp4", "url_video", "SD"),
            (
                "https://arteptweb-a.akamaihd.net/am/x-000-A_SQ_0_VA-STA_1_MP4-2200_AMM-PTWEB-1_a.mp4",
                "url_video_hd",
                "720p",
            ),
            (
                "https://arteptweb-a.akamaihd.net/am/x-000-A_EQ_0_VA-STA_1_MP4-1500_AMM-PTWEB-1_a.mp4",
                "url_video",
                "SD",
            ),
            # The Filmliste's HD file without a marker: HD, no height
            ("https://cdn-storage.br.de/a/x_HD.mp4", "url_video_hd", "HD"),
            ("https://cdn-storage.br.de/a/x_X.mp4", "url_video", None),
            (
                "https://srf-vod-amd.akamaized.net/a/index-f5-v1-a1.m3u8",
                "url_video_hd",
                None,
            ),
        ],
    )
    def test_the_quality_comes_from_the_file_name(
        self, url: str, field: str, quality: str | None
    ) -> None:
        assert _mod._quality(url, field) == quality

    @pytest.mark.asyncio()
    @respx.mock
    async def test_a_result_carries_the_broadcasters_file(self) -> None:
        _serve(_page([_entry()]))

        (result,) = await _plugin().search("Ein Film")

        url = "https://ctv-videos.daserste.de/de/f/ein_film_1920x1080-50p-5000kbit.mp4"
        assert result.download_link == url
        assert result.download_links == [
            {
                "link": url,
                "hoster": "mediathek",
                "language": "German",
                "quality": "1080p",
            }
        ]
        assert result.source_url == "https://www.ardmediathek.de/video/ein-film"
        assert result.description == "Ein Film über etwas."
        assert result.metadata["channel"] == "ARD"


class TestReleaseName:
    def test_the_release_name_names_channel_day_and_quality(self) -> None:
        broadcast = _mod._read_entry(_entry())

        assert broadcast is not None
        # The broadcasters' day: 22:15 in Germany
        assert broadcast.release_name == "Ein Film (ARD, 18.10.26, 1080p)"

    def test_the_day_is_the_broadcasters(self) -> None:
        # 22:05 UTC in summer time is 00:05 on the next day in Germany
        assert _mod._broadcast_day(1792361100) == dt.date(2026, 10, 19)
        # 23:30 UTC in winter time is 00:30 on the next day
        assert _mod._broadcast_day(1798155000) == dt.date(2026, 12, 25)
        assert _mod._broadcast_day(1798151400) == dt.date(2026, 12, 24)

    def test_a_production_year_follows_the_title(self) -> None:
        entry = _entry(
            channel="3Sat",
            topic="Spielfilm",
            title="Nikita - Spielfilm, Frankreich/Italien 1990",
        )

        broadcast = _mod._read_entry(entry)

        assert broadcast is not None
        assert broadcast.title == "Nikita"
        assert broadcast.year == 1990
        assert broadcast.release_name == "Nikita (1990) (3Sat, 18.10.26, 1080p)"

    def test_the_description_names_the_production_year(self) -> None:
        broadcast = _mod._read_entry(
            _entry(description="Spielfilm USA 2022 Für den stark adipösen Dozenten")
        )

        assert broadcast is not None
        assert broadcast.year == 2022

    def test_an_episode_names_series_number_and_episode(self) -> None:
        entry = _entry(
            channel="ZDF",
            topic="Under Salt Marsh - Tod in den Salzwiesen",
            title="Rätselhafte Bilder (S01/E02)",
            url_video_hd="",
            url_video="https://nrodlzdf-a.akamaihd.net/a/x_3360k_p36v17.mp4",
        )

        broadcast = _mod._read_entry(entry)

        assert broadcast is not None
        assert broadcast.release_name == (
            "Under Salt Marsh - Tod in den Salzwiesen S01E02 Rätselhafte Bilder"
            " (ZDF, 18.10.26, 720p)"
        )


class TestNumbers:
    @pytest.mark.parametrize(
        ("topic", "title", "series", "season", "episode", "episode_title"),
        [
            ("Ella Schön", "Feuertaufe (S03/E01)", "Ella Schön", 3, 1, "Feuertaufe"),
            (
                "Terra X",
                "Die Varusschlacht (S2026/E28)",
                "Terra X",
                2026,
                28,
                "Die Varusschlacht",
            ),
            (
                "Die Bergretter im Himalaya",
                "Feuertaufe am Everest (Staffel 1, Folge 2)",
                "Die Bergretter im Himalaya",
                1,
                2,
                "Feuertaufe am Everest",
            ),
            (
                "Der Bergdoktor Staffel 5",
                "Der Bergdoktor (12/14): Verlorene Kinder",
                "Der Bergdoktor",
                5,
                12,
                "Verlorene Kinder",
            ),
            (
                "Springflut",
                "Kriminelle Machenschaften (OmU, 3/5)",
                "Springflut",
                1,
                3,
                "Kriminelle Machenschaften",
            ),
        ],
    )
    def test_a_stated_number_places_the_episode(
        self,
        topic: str,
        title: str,
        series: str,
        season: int,
        episode: int,
        episode_title: str,
    ) -> None:
        broadcast = _mod._read_entry(_entry(channel="ZDF", topic=topic, title=title))

        assert broadcast is not None
        assert broadcast.category == 5000
        assert (broadcast.title, broadcast.season, broadcast.episode) == (
            series,
            season,
            episode,
        )
        assert broadcast.episode_title == episode_title

    @pytest.mark.parametrize(
        ("title", "series", "season", "episode", "episode_title"),
        [
            (
                "Haus aus Glas (1/6) - Der Wassermann",
                "Haus aus Glas",
                1,
                1,
                "Der Wassermann",
            ),
            (
                "Mord im Mittsommer - Staffel 11 (3/3) - Fall 25: Amanda",
                "Mord im Mittsommer",
                11,
                3,
                "Fall 25: Amanda",
            ),
        ],
    )
    def test_arte_names_its_series_in_the_title(
        self, title: str, series: str, season: int, episode: int, episode_title: str
    ) -> None:
        entry = _entry(
            channel="ARTE.DE", topic="Fernsehfilme und Serien - Serien", title=title
        )

        broadcast = _mod._read_entry(entry)

        assert broadcast is not None
        assert (broadcast.title, broadcast.season, broadcast.episode) == (
            series,
            season,
            episode,
        )
        assert broadcast.episode_title == episode_title

    @pytest.mark.parametrize(
        ("topic", "title", "episode_title"),
        [
            ("Tatort", "Feuertaufe", "Feuertaufe"),
            ("Tatort", "Tatort: Unter Feinden", "Unter Feinden"),
            ("Mankells Wallander", "Todesengel – Mankells Wallander", "Todesengel"),
            ("Der Usedom-Krimi", "Schlepper (22) – Der Usedom-Krimi", "Schlepper"),
            ("Die Fallers", "Folge 1208 - Feuertaufe", "Feuertaufe"),
        ],
    )
    def test_an_episode_without_a_number_keeps_its_title(
        self, topic: str, title: str, episode_title: str
    ) -> None:
        broadcast = _mod._read_entry(_entry(topic=topic, title=title))

        assert broadcast is not None
        assert broadcast.category == 5000
        assert broadcast.title == topic
        assert (broadcast.season, broadcast.episode) == (None, None)
        assert broadcast.episode_title == episode_title


class TestCategories:
    @pytest.mark.parametrize(
        ("channel", "topic", "title"),
        [
            ("ARD", "Filme in der ARD", "Servus, Schwiegersohn!"),
            ("ZDF", "Filme", "Ewig Dein"),
            (
                "ZDF",
                "Das kleine Fernsehspiel",
                "Was an Empfindsamkeit bleibt - Dokumentarfilm",
            ),
            (
                "3Sat",
                "Fernsehfilm",
                "Mama und der Millionär - Fernsehfilm, Deutschland 2005",
            ),
            ("ZDF", "Dokus", "Kriegstüchtig? 70 Jahre Bundeswehr"),
            (
                "HR",
                "Dokus & Reportagen",
                "Always on - Wie viel Macht haben digitale Medien",
            ),
            ("ARD", "The Whale", "The Whale"),
            ("ARTE.DE", "Geschichte - Das 20. Jahrhundert", "Entrechtete Frauen"),
            ("3Sat", "Feuerwehrfrauen", "Heim gesucht - Fernsehfilm, Deutschland 2024"),
        ],
    )
    def test_a_film_or_documentary_is_a_movie(
        self, channel: str, topic: str, title: str
    ) -> None:
        broadcast = _mod._read_entry(_entry(channel=channel, topic=topic, title=title))

        assert broadcast is not None
        assert broadcast.category == 2000
        assert (broadcast.season, broadcast.episode) == (None, None)

    def test_a_film_drops_the_genre_and_keeps_its_title(self) -> None:
        broadcast = _mod._read_entry(
            _entry(
                topic="Das kleine Fernsehspiel",
                title="Was an Empfindsamkeit bleibt - Dokumentarfilm",
            )
        )

        assert broadcast is not None
        assert broadcast.title == "Was an Empfindsamkeit bleibt"

    def test_a_topics_episode_is_a_series_episode(self) -> None:
        broadcast = _mod._read_entry(
            _entry(topic="Traumhäuser im Norden", title="Ein Haus aus Glas und Reet")
        )

        assert broadcast is not None
        assert broadcast.category == 5000
        assert broadcast.title == "Traumhäuser im Norden"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_a_movie_request_gets_the_films(self) -> None:
        _serve(_answer("nikita"))

        results = await _plugin().search("Nikita", category=2000)

        assert [(r.title, r.metadata["channel"]) for r in results] == [
            ("Nikita", "3Sat"),
            ("Nikita", "Bayerischer Rundfunk"),
        ]
        assert {r.category for r in results} == {2000}

    @pytest.mark.asyncio()
    @respx.mock
    async def test_a_series_request_gets_the_episodes(self) -> None:
        _serve(_answer("haus_aus_glas"))

        results = await _plugin().search("Haus aus Glas", category=5000)

        assert {r.category for r in results} == {5000}
        arte = [r for r in results if r.metadata["channel"] == "ARTE"]
        assert sorted(_language(r) for r in arte) == ["German", "German Sub"]
        assert {r.title for r in arte} == {"Haus aus Glas"}

    @pytest.mark.asyncio()
    @respx.mock
    async def test_a_search_without_category_gets_both(self) -> None:
        # The film, and an episode of the series of the same name
        film = _entry(topic="Ein Film", id="film")
        episode = _entry(topic="Ein Film", title="Der Anfang (S01/E01)", id="episode")
        _serve(_page([film, episode]))

        results = await _plugin().search("Ein Film")

        assert [r.category for r in results] == [2000, 5000]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_other_titles_with_the_query_words_are_dropped(self) -> None:
        # The API finds the words inside other titles and topics as well
        _serve(_answer("nikita"))

        results = await _plugin().search("Nikita")

        assert {r.title for r in results} == {"Nikita"}


class TestDuplicates:
    @pytest.mark.asyncio()
    @respx.mock
    async def test_one_result_per_channel_title_and_language(self) -> None:
        # A repeat on the same channel, and the film on a second channel
        _serve(
            _page(
                [
                    _entry(id="new"),
                    _entry(id="old", timestamp=1790000000),
                    _entry(id="other", channel="ONE"),
                ]
            )
        )

        results = await _plugin().search("Ein Film")

        assert [r.metadata["channel"] for r in results] == ["ARD", "ONE"]
        assert results[0].release_name == "Ein Film (ARD, 18.10.26, 1080p)"


def _ref(**fields: Any) -> EpisodeRef:
    values: dict[str, Any] = {"season": 2026, "episode": 41, "titles": ("Tatort",)}
    values.update(fields)
    return EpisodeRef(**values)


class TestEpisodes:
    @pytest.mark.asyncio()
    @respx.mock
    async def test_a_stated_number_answers_its_episode(self) -> None:
        _serve(_answer("salt_marsh"))

        results = await _plugin().search(
            "Under Salt Marsh",
            5000,
            season=1,
            episode=2,
            episode_ref=_ref(season=1, episode=2, title="Strange Pictures"),
        )

        assert sorted(_language(r) for r in results) == ["English", "German"]
        for result in results:
            assert result.metadata["season"] == 1
            assert result.metadata["episode"] == 2
            assert result.metadata["episode_located_by"] == "number"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_a_stated_number_never_answers_another_episode(self) -> None:
        _serve(_answer("salt_marsh"))

        results = await _plugin().search(
            "Under Salt Marsh",
            5000,
            season=1,
            episode=3,
            # Its title and day are the second episode's: the number decides
            episode_ref=_ref(
                season=1, episode=3, title="Rätselhafte Bilder", aired="2026-10-18"
            ),
        )

        assert results == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_an_episode_without_a_number_is_found_by_its_title(self) -> None:
        _serve(_answer("feuertaufe"))

        results = await _plugin().search(
            "Tatort",
            5000,
            season=2026,
            episode=41,
            episode_ref=_ref(title="Feuertaufe", aired="2026-11-01"),
        )

        tatort = [r for r in results if r.title == "Tatort"]
        assert len(tatort) == 1
        assert tatort[0].metadata["episode_located_by"] == "title"
        assert (tatort[0].metadata["season"], tatort[0].metadata["episode"]) == (
            2026,
            41,
        )

    @pytest.mark.asyncio()
    @respx.mock
    async def test_an_episode_without_a_number_is_found_by_its_day(self) -> None:
        _serve(_answer("feuertaufe"))

        results = await _plugin().search(
            "Tatort",
            5000,
            season=2026,
            episode=41,
            episode_ref=_ref(title="Baptism of Fire", aired="2026-10-19"),
        )

        assert [r.title for r in results] == ["Tatort"]
        assert results[0].metadata["episode_located_by"] == "date"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_without_a_match_nothing_answers(self) -> None:
        # Never the request's numbers: no entry is taken for being the series'
        _serve(_answer("feuertaufe"))

        results = await _plugin().search(
            "Tatort",
            5000,
            season=2026,
            episode=41,
            episode_ref=_ref(title="Another Case", aired="2026-11-08"),
        )

        assert results == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_a_numbered_request_without_reference_takes_stated_numbers(
        self,
    ) -> None:
        _serve(_answer("feuertaufe"))

        results = await _plugin().search("Ella Schön", 5000, season=3, episode=1)

        assert [
            (r.title, r.metadata["season"], r.metadata["episode"]) for r in results
        ] == [("Ella Schön", 3, 1)]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_without_reference_an_entry_without_number_never_answers(
        self,
    ) -> None:
        _serve(_answer("feuertaufe"))

        assert await _plugin().search("Tatort", 5000, season=2026, episode=41) == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_a_season_request_takes_the_seasons_episodes(self) -> None:
        _serve(_answer("feuertaufe"))

        results = await _plugin().search("Die Bergretter im Himalaya", 5000, season=1)

        assert [(r.title, r.metadata["episode"]) for r in results] == [
            ("Die Bergretter im Himalaya", 2)
        ]
