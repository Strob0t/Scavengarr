import asyncio

import httpx
import yaml
from diskcache import Cache
from scavengarr.domain.plugins.schema import YamlPluginDefinition

from scavengarr.adapters.scraping.scrapy_adapter import ScrapyAdapter


async def example_fastapi_usage():
    """
    Example: How to use MultiStageScraper in FastAPI context.
    """
    # Load plugin config
    with open("plugins/filmpalast.to.yaml", "r") as f:
        config_dict = yaml.safe_load(f)

    plugin = YamlPluginDefinition(**config_dict)

    # Shared httpx.AsyncClient (created on FastAPI startup)
    async with httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=True,
        headers={"User-Agent": "Scavengarr/1.0"},
    ) as http_client:
        # Shared diskcache (created on FastAPI startup)
        cache = Cache("./cache/scrapy")
        cache.clear()
        # Create scraper
        scraper = ScrapyAdapter(
            plugin=plugin,
            http_client=http_client,
            cache=cache,
            delay_seconds=1.0,
            max_depth=3,
            max_retries=3,
            retry_backoff_base=2.0,
        )

        # Execute search
        stage_results = await scraper.scrape(query="Iron Man", category="movies")

        # Normalize to SearchResult
        search_results = scraper.normalize_results(stage_results)

        # Output
        for result in search_results[:5]:
            print(f"\n[{result.scraped_from_stage}] {result.title}")
            print(f"  Release: {result.release_name}")
            print(f"  Source: {result.source_url}")

            if result.download_links:
                for link in result.download_links[:3]:
                    print(f"  - {link.get('hoster_name')}: {link.get('link')}")


if __name__ == "__main__":
    asyncio.run(example_fastapi_usage())
