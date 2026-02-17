"""Hoster resolver implementations for extracting playable video URLs."""

from __future__ import annotations

from .registry import HosterResolverRegistry, extract_domain

__all__ = ["HosterResolverRegistry", "extract_domain"]
