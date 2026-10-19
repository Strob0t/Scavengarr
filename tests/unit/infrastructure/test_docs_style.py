"""The docs follow the document contract of ``docs/README.md``.

Every covered document starts with its title and a status block: the purpose line
(``> **Purpose:**`` and one sentence) on line 3, ``> **Status:**`` on line 4 and
``> **As of:**`` with a commit hash on line 5. A generated reference file names its
generator script in its purpose line and carries no further status lines; a file
with generated tables names the script there too and keeps them. No heading names
a round, a trial, a measurement, a proof or a finding, and no line cites a backlog
id (``row 106``, ``finding 11``, ``N1``, ``R2``): a current or target doc states
the behaviour, the archive says where it came from. Quoted examples (the
contract's own list of banned ids) stay allowed, and code fences are not read.

Covered: every document under ``docs/`` outside the archive, which stays exempt.
Every document outside the archive carries the header; plans keep their ids and
history by the contract (``docs/plans/`` checks the header only), and
``docs/backlog.md`` cites its ids. A decision record has the sections of
``docs/target/decisions/_template.md`` in their order, its alternatives included.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[3]
_DOCS_DIR = _ROOT / "docs"
_DOCS = [
    doc
    for doc in sorted(_DOCS_DIR.rglob("*.md"))
    if "archive" not in doc.relative_to(_DOCS_DIR).parts
]
# Documents that cite backlog ids by design
_ID_EXEMPT = (_DOCS_DIR / "backlog.md",)
# Plans keep their ids and their history: only the header is checked
_PLANS = _DOCS_DIR / "plans"
_DECISIONS = sorted((_DOCS_DIR / "target" / "decisions").glob("*.md"))
_DECISION_SECTIONS = [
    "Context",
    "Decision",
    "Consequences",
    "Alternatives",
    "Status",
    "References",
]

_PURPOSE = re.compile(r"^> \*\*Purpose:\*\* \S")
_STATUS = re.compile(r"^> \*\*Status:\*\* (current|target|how-to|reference|plan)\b")
_AS_OF = re.compile(r"^> \*\*As of:\*\* [0-9a-f]{7,40}\b")
# The decision template keeps the placeholder of the contract
_AS_OF_PLACEHOLDER = re.compile(r"^> \*\*As of:\*\* <short hash[^>]*>")
_GENERATED = re.compile(r"`scripts/generate_\w+\.py`")
_HEADING = re.compile(r"^#{1,6} ")
_HISTORY_WORD = re.compile(
    r"\b(?:round(?![- ]trip)|trial|measurement|proof|finding)s?\b", re.IGNORECASE
)
_BACKLOG_IDS = (
    re.compile(r"\b(?:rows?|findings?) \d+\b", re.IGNORECASE),
    # The ideas' and decisions' letter codes, upper case only
    re.compile(r"\b[NRI]\d{1,3}\b"),
)
_QUOTED = re.compile(r'"[^"\n]*"')


def _prose(lines: list[str]) -> list[tuple[int, str]]:
    """The numbered lines outside code fences."""
    prose: list[tuple[int, str]] = []
    fenced = False
    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced:
            prose.append((number, line))
    return prose


def _header_problems(name: str, lines: list[str]) -> list[str]:
    padded = [*lines, "", "", "", "", ""]
    if not padded[0].startswith("# "):
        return [f"{name}:1: the title is not the first line"]
    if not _PURPOSE.match(padded[2]):
        return [f"{name}:3: no `> **Purpose:** <one sentence>`"]
    # A wholly generated file: its purpose line names the generator, nothing follows
    if _GENERATED.search(padded[2]) and not padded[3].startswith(">"):
        return []
    problems: list[str] = []
    if not _STATUS.match(padded[3]):
        problems.append(f"{name}:4: no status block (`> **Status:** <kind>`)")
    as_of = _AS_OF.match(padded[4]) or (
        name.endswith("_template.md") and _AS_OF_PLACEHOLDER.match(padded[4])
    )
    if not as_of:
        problems.append(f"{name}:5: no `> **As of:** <commit hash>`")
    return problems


def style_problems(
    doc: Path, root: Path = _ROOT, *, ids: bool = True, history: bool = True
) -> list[str]:
    """Where *doc* breaks the document contract, as ``path:line: problem``;
    *ids* and *history* off skip the backlog-id and the history-heading check."""
    name = doc.relative_to(root).as_posix()
    lines = doc.read_text(encoding="utf-8").splitlines()
    problems = _header_problems(name, lines)
    for number, line in _prose(lines):
        if history and _HEADING.match(line) and _HISTORY_WORD.search(line):
            problems.append(f"{name}:{number}: a heading names history")
        if not ids:
            continue
        unquoted = _QUOTED.sub("", line)
        for pattern in _BACKLOG_IDS:
            if cited := pattern.search(unquoted):
                problems.append(f"{name}:{number}: a backlog id ({cited.group()})")
                break
    return problems


@pytest.mark.parametrize("doc", _DOCS, ids=lambda doc: str(doc.relative_to(_ROOT)))
def test_the_docs_follow_the_contract(doc: Path) -> None:
    plan = doc.is_relative_to(_PLANS)
    problems = style_problems(
        doc, ids=not plan and doc not in _ID_EXEMPT, history=not plan
    )

    assert not problems, "\n".join(problems)


def decision_problems(doc: Path, root: Path = _ROOT) -> list[str]:
    """Where a decision record's sections differ from the template's."""
    lines = doc.read_text(encoding="utf-8").splitlines()
    sections = [line[3:].strip() for _, line in _prose(lines) if line.startswith("## ")]
    if sections == _DECISION_SECTIONS:
        return []
    name = doc.relative_to(root).as_posix()
    return [f"{name}: sections {sections}, not {_DECISION_SECTIONS}"]


@pytest.mark.parametrize("doc", _DECISIONS, ids=lambda doc: str(doc.relative_to(_ROOT)))
def test_a_decision_record_names_its_alternatives(doc: Path) -> None:
    problems = decision_problems(doc)

    assert not problems, "\n".join(problems)


def test_a_decision_without_alternatives_is_named(tmp_path: Path) -> None:
    body = "## Context\n\n## Decision\n\n## Consequences\n\n"
    tail = "## Status\n\naccepted\n\n## References\n"
    complete = _write(tmp_path, "0001-a.md", body + "## Alternatives\n\n" + tail)
    missing = _write(tmp_path, "0002-b.md", body + tail)

    assert decision_problems(complete, tmp_path) == []
    assert decision_problems(missing, tmp_path) == [
        "0002-b.md: sections ['Context', 'Decision', 'Consequences', 'Status',"
        " 'References'], not ['Context', 'Decision', 'Consequences', 'Alternatives',"
        " 'Status', 'References']"
    ]


def _write(tmp_path: Path, name: str, text: str) -> Path:
    doc = tmp_path / name
    doc.write_text(text, encoding="utf-8")
    return doc


def test_a_document_with_its_status_block_passes(tmp_path: Path) -> None:
    doc = _write(
        tmp_path,
        "doc.md",
        "# Link validation\n\n> **Purpose:** Which links work.\n"
        "> **Status:** current\n> **As of:** d17c66f7\n\n"
        "## How it works\n\nA round-trip per link; the rule bans"
        ' ("row 106", "N1").\n\n```bash\n# row 7 in a shell comment\n```\n',
    )

    assert style_problems(doc, tmp_path) == []


def test_a_document_without_its_purpose_line_is_named(tmp_path: Path) -> None:
    doc = _write(
        tmp_path,
        "doc.md",
        "# Link validation\n\n> **Status:** current\n> **As of:** d17c66f7\n",
    )

    assert style_problems(doc, tmp_path) == [
        "doc.md:3: no `> **Purpose:** <one sentence>`"
    ]


def test_a_generated_file_names_its_generator_in_its_purpose(tmp_path: Path) -> None:
    generated = _write(
        tmp_path,
        "plugins.md",
        "# Plugins\n\n> **Purpose:** The plugin list, generated by"
        " `scripts/generate_plugin_list.py`.\n",
    )
    old_form = _write(
        tmp_path,
        "resolvers.md",
        "# Resolvers\n\n> Generated by `scripts/generate_resolver_list.py`.\n",
    )

    assert style_problems(generated, tmp_path) == []
    assert style_problems(old_form, tmp_path) == [
        "resolvers.md:3: no `> **Purpose:** <one sentence>`"
    ]


def test_a_partly_generated_file_keeps_its_status_block(tmp_path: Path) -> None:
    head = (
        "# Configuration\n\n> **Purpose:** Every setting; the tables are generated"
        " by `scripts/generate_config_doc.py`.\n> **Status:** reference\n"
    )
    whole = _write(tmp_path, "configuration.md", head + "> **As of:** d17c66f7\n")
    cut = _write(tmp_path, "metrics.md", head)

    assert style_problems(whole, tmp_path) == []
    assert style_problems(cut, tmp_path) == [
        "metrics.md:5: no `> **As of:** <commit hash>`"
    ]


def test_the_template_keeps_its_placeholder(tmp_path: Path) -> None:
    text = (
        "# NNNN: <Title>\n\n> **Purpose:** <one sentence>\n"
        "> **Status:** target\n> **As of:** <short hash>\n"
    )

    assert style_problems(_write(tmp_path, "_template.md", text), tmp_path) == []
    assert style_problems(_write(tmp_path, "0001-x.md", text), tmp_path) == [
        "0001-x.md:5: no `> **As of:** <commit hash>`"
    ]


def test_a_status_block_out_of_place_is_named(tmp_path: Path) -> None:
    doc = _write(
        tmp_path,
        "doc.md",
        "# Stremio\n\n> **Purpose:** The addon.\n\n> **Status:** current\n",
    )

    assert style_problems(doc, tmp_path) == [
        "doc.md:4: no status block (`> **Status:** <kind>`)",
        "doc.md:5: no `> **As of:** <commit hash>`",
    ]


def test_the_breaches_are_named(tmp_path: Path) -> None:
    doc = _write(
        tmp_path,
        "doc.md",
        "# Stremio\n\nNo status here.\n\n### The second round\n\n"
        "Fixed in row 106, see R2; h2 and S05E02 are no ids.\n"
        "## Proof on the production host\n",
    )

    assert style_problems(doc, tmp_path) == [
        "doc.md:3: no `> **Purpose:** <one sentence>`",
        "doc.md:5: a heading names history",
        "doc.md:7: a backlog id (row 106)",
        "doc.md:8: a heading names history",
    ]


def test_the_backlog_may_cite_its_ids(tmp_path: Path) -> None:
    doc = _write(
        tmp_path,
        "backlog.md",
        "# Backlog\n\n> **Purpose:** The queue.\n> **Status:** plan\n"
        "> **As of:** d17c66f7\n\n"
        "| 106 | row 106, N12 |\n",
    )

    assert style_problems(doc, tmp_path, ids=False) == []
    assert style_problems(doc, tmp_path) == ["backlog.md:7: a backlog id (row 106)"]


def test_a_plan_keeps_its_ids_and_history_but_not_a_bare_header(tmp_path: Path) -> None:
    text = "### Round 2: the trial\n\nRow 106 measured it, see N1.\n"
    plan = _write(
        tmp_path,
        "plan.md",
        "# Plan: latency\n\n> **Purpose:** The latency.\n> **Status:** plan\n"
        "> **As of:** d17c66f7\n\n" + text,
    )
    bare = _write(tmp_path, "bare.md", "[← Back](../README.md)\n\n# Plan: latency\n")

    assert style_problems(plan, tmp_path, ids=False, history=False) == []
    assert style_problems(bare, tmp_path, ids=False, history=False) == [
        "bare.md:1: the title is not the first line"
    ]


def test_the_archive_is_exempt() -> None:
    assert _DOCS
    assert not [doc for doc in _DOCS if "archive" in doc.parts]
    assert _DOCS_DIR / "README.md" in _DOCS
