"""The docs carry no traces of the maintainer's playbacks or network.

The privacy rule of ``docs/agents/orchestration.md`` (Working rules) keeps clock
times of observations, request ids, LAN hostnames, network numbers, the
production machine's device name and the maintainer's time zone out of the docs:
durations, counts and build hashes carry the evidence instead. This test fails on
the patterns a reviewer would otherwise have to spot by eye. Documented examples
stay allowed: payload timestamps of 2025, the weekly schedule, ``*.example.lan``
hosts and the private ranges the configuration docs explain. LAN hostnames, time
zones and the device name stay out of the code, its comments and tests too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[3]
_DOCS = [
    _ROOT / "AGENTS.md",
    _ROOT / "README.md",
    _ROOT / "CONTRIBUTING.md",
    _ROOT / "CHANGELOG.md",
    *sorted((_ROOT / "docs").rglob("*.md")),
    *sorted((_ROOT / "openspec").rglob("*.md")),
]
_CODE = [
    *sorted((_ROOT / "src").rglob("*.py")),
    *sorted((_ROOT / "plugins").rglob("*.py")),
    *sorted((_ROOT / "scripts").rglob("*.py")),
    *sorted((_ROOT / "tests").rglob("*.py")),
    *sorted((_ROOT / ".devcontainer").glob("*")),
    *sorted((_ROOT / ".github").rglob("*.yml")),
    *sorted((_ROOT / "docker").glob("*")),
    _ROOT / "docker-compose.yml",
    _ROOT / ".env.devcontainer.example",
]

# Lines with documented examples, never observations.
_EXAMPLE_LINE = re.compile(r"\bMonday\b|\b2025\b|a1b2c3d4|\bcron\b")
_DOC_PATTERNS = {
    "clock time with seconds": re.compile(r"\b\d\d:\d\d:\d\d\b"),
    "clock time in UTC": re.compile(r"\b\d\d:\d\d\s?UTC\b"),
    "request id": re.compile(r"request(?:_id=| )[0-9a-f]{12}\b"),
    "network number": re.compile(r"\b192\.168\.(?!0\.0\b|1\.\d+\b)\d+\.\d+\b"),
    "autonomous system": re.compile(r"\bAS\d{4,6}\b"),
}
_ANY_PATTERNS = {
    # The production machine is "the production host" (decision 0015)
    "device name": re.compile(r"\bRaspberry\b|\bPi\b"),
    "LAN hostname": re.compile(r"(?<!example)(?<!\*)\.lan\b"),
    "time zone": re.compile(r"\bEurope/\w+"),
}


def _findings(
    paths: list[Path], patterns: dict[str, re.Pattern[str]], *, examples: bool
) -> list[str]:
    found: list[str] = []
    for path in paths:
        if not path.is_file() or path == Path(__file__):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if examples and _EXAMPLE_LINE.search(line):
                continue
            for name, pattern in patterns.items():
                if pattern.search(line):
                    rel = path.relative_to(_ROOT).as_posix()
                    found.append(f"{rel}:{number}: {name}")
    return found


@pytest.mark.parametrize("kind", ["docs", "code"])
def test_no_private_traces(kind: str) -> None:
    if kind == "docs":
        found = _findings(_DOCS, {**_DOC_PATTERNS, **_ANY_PATTERNS}, examples=True)
    else:
        found = _findings(_CODE, _ANY_PATTERNS, examples=False)
    assert not found, (
        "private traces (see the privacy rule in docs/agents/orchestration.md):\n"
        + "\n".join(found)
    )
