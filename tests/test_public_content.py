"""Public-content scan: no text in the public repo or the bundle may discuss the event
organiser beyond the event itself and the schema permission.

This module holds the bundle half (Task 7): it scans the bundle's authored text only,
never the source-derived CSV cells. Task 12 adds the branch-diff half
(``git diff main...HEAD``, added lines) on top of the same `scan_lines`, PATTERNS and
ALLOWLIST defined here.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, NamedTuple

import pytest

from psm import bundle

# (label, compiled pattern). Verbatim from Task 12. Everything is case-insensitive except
# the abbreviation HAI, which would otherwise match the word "hai" inside other text.
PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    (r"\bHAI\b", re.compile(r"\bHAI\b")),
    (r"consult(ing|ancy|ant)", re.compile(r"consult(ing|ancy|ant)", re.I)),
    (r"\bclients?\b", re.compile(r"\bclients?\b", re.I)),
    (r"\bcommercial\b", re.compile(r"\bcommercial\b", re.I)),
    (r"\bengagement\b", re.compile(r"\bengagement\b", re.I)),
    (r"\boffering\b", re.compile(r"\boffering\b", re.I)),
)

# (file name, exact text to excuse). The text is cut out of a line of that file before the
# line is scanned, so the rest of the line is still checked. The existing disclaimer in
# DATA_SOURCES.md says "commercially"; the Task 12 diff scan needs this entry, the bundle
# scan does not.
ALLOWLIST: tuple[tuple[str, re.Pattern], ...] = (
    ("DATA_SOURCES.md", re.compile(r"Do your own diligence before relying on any of this commercially\.")),
)

# The authored files of the bundle, relative to its root (glob patterns).
AUTHORED = ("README_FIRST.md", "TERMS.md", "BASELINE.md", "core/data_dictionary.csv",
            "advanced/schema/*.yaml")


class Hit(NamedTuple):
    source: str
    line_no: int
    pattern: str
    line: str


def excuse(source: str, line: str) -> str:
    """``line`` with the allowlisted text of this file cut out."""
    name = Path(source).name
    for fname, rx in ALLOWLIST:
        if name == fname:
            line = rx.sub("", line)
    return line


def scan_lines(source: str, lines: Iterable[str]) -> list[Hit]:
    """Every pattern hit in ``lines`` that the allowlist does not excuse. Task 12 feeds the
    added lines of a diff through this same function."""
    out = []
    for n, line in enumerate(lines, 1):
        rest = excuse(source, line)
        for label, rx in PATTERNS:
            if rx.search(rest):
                out.append(Hit(source, n, label, line.strip()))
    return out


def scan_text(source: str, text: str) -> list[Hit]:
    return scan_lines(source, text.splitlines())


def authored_files(b: Path) -> list[Path]:
    out: list[Path] = []
    for pat in AUTHORED:
        out += sorted(b.glob(pat))
    return out


def scan_bundle(b: Path) -> list[Hit]:
    hits = []
    for p in authored_files(b):
        hits += scan_text(p.relative_to(b).as_posix(), p.read_text(encoding="utf-8"))
    return hits


@pytest.fixture(scope="module")
def b(tmp_path_factory):
    return bundle.build_bundle(tmp_path_factory.mktemp("pub"), allow_dirty=True,
                               allow_placeholder_terms=True)


def test_the_scan_reads_every_authored_file(b):
    names = {p.relative_to(b).as_posix() for p in authored_files(b)}
    assert {"README_FIRST.md", "TERMS.md", "BASELINE.md", "core/data_dictionary.csv"} <= names
    assert any(n.startswith("advanced/schema/") and n.endswith(".yaml") for n in names)
    assert not any(n.endswith("cause_task.csv") or "real_only" in n or "enriched" in n for n in names)


def test_bundle_authored_text_is_clean(b):
    assert scan_bundle(b) == []


def test_deliberate_violation_is_caught():
    """A fixture line with 'consultancy' must be flagged, and so must each other pattern."""
    hits = scan_text("README_FIRST.md", "fine line\nbuilt for a consultancy project\n")
    assert [(h.line_no, h.pattern) for h in hits] == [(2, "consult(ing|ancy|ant)")]
    for line, label in (("the HAI team", r"\bHAI\b"), ("Our Clients", r"\bclients?\b"),
                        ("a commercial use", r"\bcommercial\b"), ("an Engagement", r"\bengagement\b"),
                        ("an Offering", r"\boffering\b"), ("a Consulting firm", "consult(ing|ancy|ant)")):
        assert [h.pattern for h in scan_text("x.md", line)] == [label], line


def test_hai_is_case_sensitive():
    assert scan_text("x.md", "hai and Hai are not the abbreviation") == []
    assert scan_text("x.md", "HAI") != []


def test_a_planted_violation_in_a_built_bundle_file_is_caught(b, tmp_path):
    import shutil
    copy = tmp_path / "copy"
    shutil.copytree(b, copy)
    p = copy / "README_FIRST.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nWe are a consultancy.\n", encoding="utf-8")
    hits = scan_bundle(copy)
    assert [(h.source, h.pattern) for h in hits] == [("README_FIRST.md", "consult(ing|ancy|ant)")]


def test_allowlist_excuses_only_the_named_file_and_line():
    line = "> Do your own diligence before relying on any of this commercially."
    assert not any(rx.search(line) for _, rx in PATTERNS)       # 'commercially' is not 'commercial'
    # a pattern hit on the allowlisted line is excused only in the allowlisted file
    excused = "Do your own diligence before relying on any of this commercially."
    assert excuse("DATA_SOURCES.md", excused) == ""
    assert excuse("README_FIRST.md", excused) == excused
    both = excused + " A commercial client."
    assert {h.pattern for h in scan_text("DATA_SOURCES.md", both)} == {r"\bclients?\b", r"\bcommercial\b"}
    assert scan_text("DATA_SOURCES.md", "an unrelated client line") != []


def test_scan_lines_takes_any_line_source():
    """The seam Task 12 uses: added lines from a diff, labelled with the file they came from."""
    added = ["+a clean line", "+our offering"]
    hits = scan_lines("docs/new.md", [ln[1:] for ln in added])
    assert [(h.source, h.line_no, h.pattern) for h in hits] == [("docs/new.md", 2, r"\boffering\b")]
