"""Tests for the bounds of Stremio settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scavengarr.infrastructure.config.schema import StremioConfig


class TestFileStallTimeout:
    """The file proxy's stall timeout must stay well under the 60 s a
    player's proxy waits for a byte (decision 0017)."""

    def test_the_default_is_10_s(self) -> None:
        assert StremioConfig().file_stall_timeout_seconds == 10.0

    @pytest.mark.parametrize("seconds", [1.0, 30.0])
    def test_the_bounds_are_allowed(self, seconds: float) -> None:
        config = StremioConfig(file_stall_timeout_seconds=seconds)

        assert config.file_stall_timeout_seconds == seconds

    @pytest.mark.parametrize("seconds", [0.5, 31.0, 60.0])
    def test_values_out_of_bounds_are_refused(self, seconds: float) -> None:
        with pytest.raises(ValidationError):
            StremioConfig(file_stall_timeout_seconds=seconds)
