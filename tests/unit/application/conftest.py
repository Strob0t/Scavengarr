"""Fixtures of the application tests."""

from __future__ import annotations

import pytest
from guessit import guessit


@pytest.fixture(autouse=True, scope="session")
def _configured_guessit() -> None:
    """guessit compiles its rules on its first call of a process (125 ms on
    the dev machine), inside the title match: a test whose answer budget
    leaves its fast site 0.1 s failed whenever it ran first in its process.
    The tests start with guessit configured."""
    guessit("Iron.Man.2008.German.1080p.BluRay")
