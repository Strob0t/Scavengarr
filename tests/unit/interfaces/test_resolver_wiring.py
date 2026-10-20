"""Tests for the hoster resolvers the composition registers."""

from __future__ import annotations

import inspect
from typing import cast

import httpx

from scavengarr.infrastructure.browser.clearance_store import ClearanceStore
from scavengarr.infrastructure.browser.stealth_pool import StealthPool
from scavengarr.infrastructure.hoster_resolvers.generic_ddl import (
    ALL_DDL_CONFIGS,
    GenericDDLResolver,
)
from scavengarr.infrastructure.hoster_resolvers.xfs import ALL_XFS_CONFIGS, XFSResolver
from scavengarr.interfaces.composition import build_hoster_resolvers


def _generic(resolver: object) -> bool:
    return isinstance(resolver, XFSResolver | GenericDDLResolver)


async def test_every_xfs_and_ddl_config_is_registered() -> None:
    async with httpx.AsyncClient() as client:
        resolvers = build_hoster_resolvers(client, None)

    xfs = [r.name for r in resolvers if isinstance(r, XFSResolver)]
    ddl = [r.name for r in resolvers if isinstance(r, GenericDDLResolver)]
    assert xfs == [config.name for config in ALL_XFS_CONFIGS]
    assert ddl == [config.name for config in ALL_DDL_CONFIGS]


async def test_the_specific_resolvers_come_before_the_generic_ones() -> None:
    """A domain two resolvers claim stays with the first one registered."""
    async with httpx.AsyncClient() as client:
        resolvers = build_hoster_resolvers(client, None)

    kinds = [_generic(r) for r in resolvers]
    assert kinds == sorted(kinds)
    assert not kinds[0]


async def test_no_two_resolvers_share_a_name() -> None:
    async with httpx.AsyncClient() as client:
        names = [r.name for r in build_hoster_resolvers(client, None)]

    assert len(names) == len(set(names))


async def test_every_resolver_that_captures_gets_the_stealth_pool() -> None:
    pool = cast(StealthPool, object())
    async with httpx.AsyncClient() as client:
        resolvers = build_hoster_resolvers(client, pool)

    capturing = [
        r
        for r in resolvers
        if "stealth_pool" in inspect.signature(type(r).__init__).parameters
    ]
    assert {r.name for r in capturing} >= {
        "filemoon",
        "doodstream",
        "supervideo",
        "vixeo",
    }
    assert all(getattr(r, "_stealth_pool", None) is pool for r in capturing)


async def test_filemoon_reads_the_kept_capture_state() -> None:
    """Its captcha pass tells Filemoon a cold capture (row 121)."""
    pool = cast(StealthPool, object())
    store = cast(ClearanceStore, object())
    async with httpx.AsyncClient() as client:
        resolvers = build_hoster_resolvers(client, pool, clearance_store=store)

    (filemoon,) = [r for r in resolvers if r.name == "filemoon"]
    assert getattr(filemoon, "_clearance_store", None) is store
