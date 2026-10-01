"""Tests for the mirror groups the composition root hands to Stremio."""

from __future__ import annotations

from pathlib import Path

from scavengarr.infrastructure.plugins.registry import PluginRegistry
from scavengarr.interfaces.composition import _mirror_groups

_PLUGINS = Path(__file__).resolve().parents[3] / "plugins"


def test_the_dle_mirrors_share_one_group() -> None:
    """hdfilme, streamcloud and streamkiste front one database (same news
    ids, titles and links); no other plugin declares a group."""
    assert _mirror_groups(PluginRegistry(_PLUGINS)) == {
        "hdfilme": "hdfilme",
        "streamcloud": "hdfilme",
        "streamkiste": "hdfilme",
    }
