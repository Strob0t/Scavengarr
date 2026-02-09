"""Scraping infrastructure - adapters for Scrapy and Playwright engines."""
from __future__ import annotations

from .scrapy_adapter import ScrapyAdapter, StageScraper

__all__ = ["ScrapyAdapter", "StageScraper"]
