"""Public-content scan: no text in the public repo or the bundle may discuss the event
organiser beyond the event itself and the schema permission.

Two halves share `scan_lines`, PATTERNS and ALLOWLIST. The bundle half (Task 7) scans the
bundle's authored text only, never the source-derived CSV cells. The branch-diff half
(Task 12) scans the lines added by ``git diff main...HEAD`` in tracked files outside
``data/``, which is source-derived BSEE text (real reports say "commercial diving" and
"client"); it skips, with a reason, when ``main`` is not available.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, NamedTuple

import pytest

from psm import bundle

REPO = Path(__file__).resolve().parent.parent

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
# line is scanned, so the rest of the line is still checked. Each entry must be live:
# `allowlist_problems` fails an entry whose text is not in its file, or whose text no pattern
# would flag. The one entry this module had, for "commercially" in DATA_SOURCES.md, was dead:
# \bcommercial\b does not match "commercially". It was removed. Task 12 may add entries here.
ALLOWLIST: tuple[tuple[str, re.Pattern], ...] = ()

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


def allowlist_problems(allowlist, files: dict[str, str]) -> list[str]:
    """Entries that are dead. ``files`` maps a file name to its text. An entry is dead if its
    file is not given, its text is on no line of the file, or no pattern flags the text it
    excuses (so excusing it changes nothing)."""
    out = []
    for fname, rx in allowlist:
        text = files.get(fname)
        if text is None:
            out.append(f"{fname}: file not found")
            continue
        found = [m.group(0) for line in text.splitlines() for m in [rx.search(line)] if m]
        if not found:
            out.append(f"{fname}: text not in file: {rx.pattern}")
        elif not any(prx.search(f) for f in found for _, prx in PATTERNS):
            out.append(f"{fname}: no pattern flags the excused text: {rx.pattern}")
    return out


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


FIXTURE_ENTRY = ("NOTES.md", re.compile(r"Our client list is private\."))


def test_every_allowlist_entry_is_live():
    """Each entry's text is in the file it excuses, and a pattern would flag it."""
    files = {}
    for fname, _ in ALLOWLIST:
        path = REPO / fname
        files[fname] = path.read_text(encoding="utf-8") if path.exists() else None
    files = {k: v for k, v in files.items() if v is not None}
    assert allowlist_problems(ALLOWLIST, files) == []


def test_dead_allowlist_entries_are_caught():
    """Deliberate violations: text absent, file absent, and text no pattern flags."""
    live = {"NOTES.md": "intro\nOur client list is private.\n"}
    assert allowlist_problems((FIXTURE_ENTRY,), live) == []
    assert "text not in file" in allowlist_problems((FIXTURE_ENTRY,), {"NOTES.md": "nothing here"})[0]
    assert "file not found" in allowlist_problems((FIXTURE_ENTRY,), {})[0]
    old = ("DATA_SOURCES.md", re.compile(r"Do your own diligence before relying on any of this commercially\."))
    p = allowlist_problems((old,), {"DATA_SOURCES.md": "> Do your own diligence before relying on any of this commercially."})
    assert "no pattern flags" in p[0]


def test_allowlist_excuses_only_the_named_file_and_line(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "ALLOWLIST", (FIXTURE_ENTRY,))
    excused = "Our client list is private."
    assert [h.pattern for h in scan_text("x/NOTES.md", excused)] == []
    assert [h.pattern for h in scan_text("README_FIRST.md", excused)] == [r"\bclients?\b"]
    both = excused + " A commercial client."
    assert {h.pattern for h in scan_text("NOTES.md", both)} == {r"\bclients?\b", r"\bcommercial\b"}
    assert scan_text("NOTES.md", "an unrelated client line") != []


def test_scan_lines_takes_any_line_source():
    """The seam Task 12 uses: added lines from a diff, labelled with the file they came from."""
    added = ["+a clean line", "+our offering"]
    hits = scan_lines("docs/new.md", [ln[1:] for ln in added])
    assert [(h.source, h.line_no, h.pattern) for h in hits] == [("docs/new.md", 2, r"\boffering\b")]


# ---- Task 12: the branch-diff half -------------------------------------------------------

BASE = "main"
# The scanner module holds the patterns and the deliberate violations, so it cannot scan itself.
DIFF_EXCLUDED_FILES = frozenset({"tests/test_public_content.py"})
_HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,\d+)? @@")


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=repo,
                          capture_output=True, text=True, encoding="utf-8")


def diff_base_problem(repo: Path = REPO, base: str = BASE) -> str | None:
    """Why the branch diff cannot be taken (no ``main`` ref, no common ancestor); None if it can."""
    if _run(repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode != 0:
        return (f"no `{base}` ref here (shallow clone, or CI without the ref): "
                f"the branch-diff scan needs `git diff {base}...HEAD`")
    if _run(repo, "merge-base", base, "HEAD").returncode != 0:
        return f"`{base}` and HEAD share no history: cannot take `git diff {base}...HEAD`"
    return None


def parse_added_lines(diff: str) -> dict[str, list[tuple[int, str]]]:
    """file -> [(line number in the new file, text)] for the ``+`` lines of a ``--unified=0``
    diff. Deleted files and ``DIFF_EXCLUDED_FILES`` are left out."""
    out: dict[str, list[tuple[int, str]]] = {}
    path: str | None = None
    n = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            tgt = line[4:]
            path = tgt[2:] if tgt.startswith("b/") else None
            if path in DIFF_EXCLUDED_FILES:
                path = None
        elif line.startswith("--- ") or line.startswith("diff --git"):
            continue
        elif (m := _HUNK.match(line)):
            n = int(m.group(1)) - 1
        elif line.startswith("+") and path:
            n += 1
            out.setdefault(path, []).append((n, line[1:]))
    return out


def added_lines(repo: Path = REPO, base: str = BASE) -> dict[str, list[tuple[int, str]]]:
    """Added lines of ``git diff base...HEAD`` outside ``data/``; empty if the diff cannot be taken."""
    if diff_base_problem(repo, base):
        return {}
    r = _run(repo, "diff", f"{base}...HEAD", "--unified=0", "--no-color", "--no-ext-diff",
             "--no-renames", "--", ".", ":(exclude)data")
    if r.returncode != 0:
        raise RuntimeError(f"git diff failed: {r.stderr.strip()}")
    return parse_added_lines(r.stdout)


def scan_branch_diff(repo: Path = REPO, base: str = BASE) -> list[Hit]:
    hits = []
    for path, lines in added_lines(repo, base).items():
        nos = [n for n, _ in lines]
        hits += [h._replace(line_no=nos[h.line_no - 1])
                 for h in scan_lines(path, [t for _, t in lines])]
    return hits


def _git(repo: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "PATH": os.environ["PATH"]}
    return subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=repo, env=env, check=True,
                          capture_output=True, text=True).stdout


def _fixture_repo(root: Path, *, base: str = "main") -> Path:
    """A repo with a base branch and a feature branch. The feature adds a clean doc line, a
    violating doc line and a violating line under data/ (which the scan must not read)."""
    _git(root, "init", "-q", "-b", base)
    (root / "docs").mkdir()
    (root / "data").mkdir()
    (root / "docs/new.md").write_text("first\n", encoding="utf-8")
    _git(root, "add", "docs/new.md")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "feat")
    (root / "docs/new.md").write_text("first\na clean line\nbuilt for a consultancy project\n", encoding="utf-8")
    (root / "data/raw.csv").write_text("id,text\n1,the commercial diving client\n", encoding="utf-8")
    _git(root, "add", "docs/new.md", "data/raw.csv")
    _git(root, "commit", "-q", "-m", "feature")
    return root


def test_the_diff_scan_catches_a_deliberate_violation(tmp_path):
    """A fixture line containing 'consultancy' is caught, with its real line number and file;
    the same sort of text under data/ is source-derived and is not read."""
    repo = _fixture_repo(tmp_path)
    assert diff_base_problem(repo) is None
    assert added_lines(repo) == {"docs/new.md": [(2, "a clean line"), (3, "built for a consultancy project")]}
    hits = scan_branch_diff(repo)
    assert [(h.source, h.line_no, h.pattern) for h in hits] == [("docs/new.md", 3, "consult(ing|ancy|ant)")]


def test_the_diff_scan_skips_the_scanner_module_only():
    """tests/test_public_content.py holds the patterns and the deliberate violations, so it
    cannot scan itself. It is the only file excused, and it is a real tracked path."""
    assert DIFF_EXCLUDED_FILES == frozenset({"tests/test_public_content.py"})
    assert (REPO / "tests/test_public_content.py").exists()
    added = parse_added_lines("+++ b/tests/test_public_content.py\n@@ -0,0 +1 @@\n+a consultancy\n"
                              "+++ b/docs/x.md\n@@ -0,0 +1 @@\n+a consultancy\n")
    assert list(added) == ["docs/x.md"]


def test_the_diff_base_problem_names_a_missing_main(tmp_path):
    """No `main` ref (shallow clone, CI without the ref): a reason, not a failure."""
    repo = _fixture_repo(tmp_path, base="trunk")
    reason = diff_base_problem(repo)
    assert reason is not None and "main" in reason
    assert added_lines(repo) == {}


def test_parse_added_lines_reads_line_numbers_from_hunk_headers():
    diff = ("diff --git a/docs/a.md b/docs/a.md\n--- a/docs/a.md\n+++ b/docs/a.md\n"
            "@@ -1,0 +5,2 @@\n+five\n+six\n@@ -9 +12 @@\n-old\n+twelve\n"
            "diff --git a/gone.md b/gone.md\n--- a/gone.md\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n")
    assert parse_added_lines(diff) == {"docs/a.md": [(5, "five"), (6, "six"), (12, "twelve")]}


def test_branch_diff_added_lines_are_clean():
    """Every line this branch adds outside data/ passes the same scan as the bundle's text.
    data/** is source-derived BSEE text (real reports say 'commercial diving' and 'client')."""
    reason = diff_base_problem(REPO)
    if reason:
        pytest.skip(reason)
    hits = scan_branch_diff(REPO)
    assert hits == [], "\n".join(f"{h.source}:{h.line_no} [{h.pattern}] {h.line}" for h in hits)
