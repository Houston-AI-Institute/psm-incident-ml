"""The participant guide (README_FIRST.md): structure, verbatim text, stamping, length, quick start.

Template tests read the guide source. Stamped tests build a bundle through the real
builder. Every guard (the placeholder refusal, the length cap, the fence balance check)
has a companion test that commits a real violation and shows it is caught.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
from pathlib import Path

import pytest

from psm import bundle
from psm.provenance import TOKENS

REPO = Path(__file__).resolve().parents[1]
# RED-phase hook: lets the template tests be pointed at another copy of the guide.
GUIDE = Path(os.environ.get("PSM_GUIDE_UNDER_TEST", REPO / "docs/release/PARTICIPANT_GUIDE.md"))

HEADINGS = [
    "What this is", "Content note", "Choosing tools", "Folders", "Provenance tokens",
    "Glossary", "Quick start", "Known defects", "Tracks", "Do not attempt",
    "About the companies", "How to report a result",
]
CONTENT_NOTE = (
    "These are real US offshore incident reports. Injuries are described plainly. Fatal "
    "incidents were removed from this bundle. Names that follow a title (Mr., Ms., Dr.) were "
    "replaced with `[name]`, along with the surname wherever it repeats. Other names may "
    "remain: redaction is pattern-based. The source PDFs linked in `bsee_source_url` are not "
    "redacted. `content_flags.csv` marks rows that mention death, near-fatal events or serious "
    "injury. The flags are pattern-based too, so unflagged rows can still describe serious "
    "injury. If any of this is a problem, talk to a mentor."
)
GOLD_SENTENCE = (
    "There are no independent (gold) labels: nothing in this bundle is validated. The cause "
    "labels are the investigator's own category, relabelled through the repo's crosswalk, "
    "which is an opinion."
)
EXCEL_SENTENCE = ("Excel: use Data → From Text/CSV and choose UTF-8, or accented characters "
                  "and dashes will display wrongly.")
PLACEHOLDER = "<!-- organiser text: under-18 tool guidance -->"
MAX_WORDS = 1600

# data_dictionary_notes.yaml keys -> a phrase the guide's Known defects must carry for each.
DEFECT_PHRASES = {
    "Approval Date": "Approval Date",
    "Description": "`Description`",
    "Investigation leader - Name": "different pseudonyms",
    "Investigation Acceptor/Approver (Owner) - Name": "different pseudonyms",
    "Health & Safety  - Consequence": "xw_consequence_tiers",
    "Environment & Reputation  - Consequence": "xw_consequence_tiers",
    "Financial Cost & Business Interruption  - Consequence": "xw_consequence_tiers",
    "Cause Description": "begins with its own category",
    "src_text_stripped": "src_text_stripped",
    "bsee_operator": "contact blocks",
    "syn_regime": "src_year",
}


def template() -> str:
    return GUIDE.read_text(encoding="utf-8")


def section(text: str, name: str) -> str:
    m = re.search(rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, f"no section {name!r}"
    return m.group(1)


def fences_balanced(text: str) -> bool:
    """Each fence name opens and closes alternately, never nested, never left open."""
    for name in ("companies", "text"):
        marks = re.findall(rf"<!-- (/?){name} -->", text)
        if [m == "/" for m in marks] != [False, True] * (len(marks) // 2) or len(marks) % 2:
            return False
    return True


def prose_words(text: str) -> int:
    """Whitespace-separated words, excluding fenced code blocks."""
    return len(re.sub(r"```.*?```", "", text, flags=re.S).split())


# ---- built bundles ---------------------------------------------------------

def _make(tmp_path_factory, name, **kw):
    return bundle.build_bundle(tmp_path_factory.mktemp(name), allow_dirty=True,
                               allow_placeholder_terms=True, **kw)


@pytest.fixture(scope="module")
def b(tmp_path_factory):
    return _make(tmp_path_factory, "full")


@pytest.fixture(scope="module")
def b_nocomp(tmp_path_factory):
    return _make(tmp_path_factory, "nocomp", include_companies=False)


@pytest.fixture(scope="module")
def b_light(tmp_path_factory):
    return _make(tmp_path_factory, "light", text_light=True)


def readme(b: Path) -> str:
    return (b / "README_FIRST.md").read_text(encoding="utf-8")


# ---- template --------------------------------------------------------------

def test_stub_is_gone():
    assert "STUB" not in template()
    assert "STUB" not in template().split("\n", 1)[0]


def test_twelve_headings_in_order():
    assert re.findall(r"^## (.+)$", template(), re.M) == HEADINGS


def test_every_nonempty_token_is_in_backticks():
    text = section(template(), "Provenance tokens")
    for tok in sorted(TOKENS - {""}):
        assert f"`{tok}`" in text, tok


def test_token_meanings_are_not_empty():
    rows = dict(re.findall(r"^- `(\w+)`: (.+)$", section(template(), "Provenance tokens"), re.M))
    assert set(rows) == TOKENS - {""}
    assert all(len(v.split()) >= 3 for v in rows.values())     # no one-word meanings
    assert rows["mod"].startswith("real source text that this project altered: dates shifted, "
                                  "or a company name, person name or contact detail masked; "
                                  "otherwise verbatim")


def test_content_note_core_is_verbatim():
    assert "> " + CONTENT_NOTE in section(template(), "Content note")


def test_gold_sentence_is_verbatim():
    assert GOLD_SENTENCE in section(template(), "Provenance tokens")


def test_pattern_based_appears_twice():
    assert template().count("pattern-based") >= 2


def test_excel_sentence_is_verbatim():
    assert EXCEL_SENTENCE in section(template(), "Quick start")


def test_choosing_tools_names_ai_studio_and_the_placeholder():
    text = section(template(), "Choosing tools")
    assert "AI Studio" in text
    assert "Gemini API" in text
    assert re.search(r"[Uu]nder-18 participants do not use Google AI Studio or the Gemini API "
                     r"themselves", text)
    assert PLACEHOLDER in text


def test_fences_are_balanced():
    assert fences_balanced(template())


def test_fence_check_catches_an_unbalanced_template():
    t = template()
    assert fences_balanced(t)
    assert not fences_balanced(t.replace("<!-- /text -->", "", 1))
    assert not fences_balanced(t.replace("<!-- companies -->", "<!-- /companies -->", 1))
    assert not fences_balanced("<!-- text -->\n<!-- text -->\n<!-- /text -->\n<!-- /text -->")


def test_known_defects_cover_every_notes_item():
    import yaml
    notes = yaml.safe_load((REPO / "schema/data_dictionary_notes.yaml").read_text(encoding="utf-8"))["notes"]
    assert set(notes) == set(DEFECT_PHRASES), "a notes item was added or removed: update the guide and this map"
    text = section(template(), "Known defects")
    for col, phrase in DEFECT_PHRASES.items():
        assert phrase in text, col
    assert "causes.csv" in text and "leak" in text


def test_tracks_are_tagged():
    text = section(template(), "Tracks")
    tracks = re.findall(r"^\d\. \*\*(.+?)\*\*(.*)$", text, re.M)
    assert [t[0] for t in tracks] == [
        "Explore what goes wrong offshore", "Provenance detective", "Cause classification",
        "Era drift", "LLM structured extraction", "Company register audit"]
    tags = {name: set(re.findall(r"`(\[[^`]+\])`", rest)) for name, rest in tracks}
    assert tags["Explore what goes wrong offshore"] == {"[no-code ok]"}
    assert tags["Provenance detective"] == {"[no-code ok]"}
    assert tags["Cause classification"] == {"[code]"}
    assert tags["Era drift"] == {"[code]"}
    assert tags["LLM structured extraction"] == {"[code]", "[AI tool]"}
    assert tags["Company register audit"] == {"[no-code ok]"}


def test_cause_classification_protocol():
    text = section(template(), "Tracks")
    for must in ("syn_regime == modern_six", "5-fold", "`syn_fold`", "out-of-fold", "macro-F1",
                 "bootstrap", "over incidents", "per-class n and F1", "`BASELINE.md`",
                 "`src_year`", "`syn_regime`", "`src_category`", "`Incident Number`", "as features"):
        assert must in text, must


def test_era_drift_and_extraction_rules():
    text = section(template(), "Tracks")
    assert "a change in label mix between regimes is not a finding" in text
    assert "n, classes and majority rate" in text
    assert "at least 30" in text and "by a stated rule" in text
    assert "agreement with our n hand labels: k/n" in text
    assert "independently" in text


def test_do_not_attempt_list():
    text = section(template(), "Do not attempt").lower()
    for must in ("ranking real operators", "naming individuals", "real person's conduct",
                 "fictional companies' records as real", "score without n", "source pdf"):
        assert must in text, must


def test_about_the_companies_uses_the_final_names():
    text = template()
    body = section(text, "About the companies")
    for must in ("Alder, Hazel and Hornbeam are fictional", "generated", "masked as `[company]`",
                 "attribution, not secrecy"):
        assert must in body, must
    assert not re.search(r"northstar|meridian|coastal", text, re.I)


def test_report_section_has_four_parts():
    text = section(template(), "How to report a result")
    for must in ("claim", "columns", "provenance", "n.", "limitation"):
        assert must in text, must


def test_the_word_accuracy_is_not_used():
    assert not re.search(r"accura", template(), re.I)


def test_template_is_short():
    assert prose_words(template()) < MAX_WORDS - 250      # room for the stamped counts table


def test_length_cap_catches_a_long_guide():
    assert prose_words(template()) < MAX_WORDS
    assert prose_words(template() + "\nword " * MAX_WORDS) > MAX_WORDS
    code = "```python\n" + "word " * 5000 + "\n```"
    assert prose_words(template() + code) == prose_words(template())    # fenced code is excluded


# ---- stamped ---------------------------------------------------------------

def test_stamped_readme_has_no_placeholder(b, b_nocomp, b_light):
    for bd in (b, b_nocomp, b_light):
        text = readme(bd)
        assert not re.search(r"\{[A-Za-z_]+\}", text), bd.name
        assert "STUB" not in text and "<!--" not in text and "-->" not in text


def test_stamped_readme_is_within_three_pages(b, b_nocomp, b_light):
    for bd in (b, b_nocomp, b_light):
        assert prose_words(readme(bd)) <= MAX_WORDS, (bd.name, prose_words(readme(bd)))


def test_stamped_readme_headings(b, b_nocomp, b_light):
    assert re.findall(r"^## (.+)$", readme(b), re.M) == HEADINGS
    assert re.findall(r"^## (.+)$", readme(b_light), re.M) == HEADINGS
    assert re.findall(r"^## (.+)$", readme(b_nocomp), re.M) == [h for h in HEADINGS if h != "About the companies"]


def test_fences_follow_the_mode(b, b_nocomp, b_light):
    full, nocomp, light = readme(b), readme(b_nocomp), readme(b_light)
    assert "Alder, Hazel and Hornbeam" in full and "Alder" not in nocomp and "Hornbeam" not in nocomp
    assert "Company register audit" in full and "Company register audit" not in nocomp
    assert "LLM structured extraction" in full and "LLM structured extraction" not in light
    assert "Company register audit" in light
    assert "companies/" not in nocomp and "companies/" in full


def test_content_note_survives_stamping(b, b_nocomp, b_light):
    for bd in (b, b_nocomp, b_light):
        assert "> " + CONTENT_NOTE in readme(bd)
        assert GOLD_SENTENCE in readme(bd)


# ---- the Choosing-tools placeholder guard ----------------------------------

REAL_TERMS = "Terms of use\n\nThese are the organiser's terms.\n"


def test_builder_refuses_the_choosing_tools_placeholder(tmp_path, monkeypatch):
    """Deliberate violation: real terms, but the guide still holds the placeholder comment."""
    monkeypatch.setattr(bundle, "terms_text", lambda *a, **k: REAL_TERMS)
    assert PLACEHOLDER in (REPO / "docs/release/PARTICIPANT_GUIDE.md").read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="Choosing tools"):
        bundle.build_bundle(tmp_path / "x", allow_dirty=True)
    assert not (tmp_path / "x").exists()


def test_the_same_flag_that_allows_placeholder_terms_allows_the_guide_placeholder(b):
    assert (b / "README_FIRST.md").exists()      # built with allow_placeholder_terms=True
    assert "Under-18 participants do not use Google AI Studio" in readme(b)


def test_check_guide():
    with_ph = f"x\n{PLACEHOLDER}\ny\n"
    with pytest.raises(SystemExit):
        bundle.check_guide(with_ph, allow_placeholder=False)
    bundle.check_guide(with_ph, allow_placeholder=True)
    bundle.check_guide("x\nThe organiser's own text.\ny\n", allow_placeholder=False)


# ---- the pandas quick start must run ----------------------------------------

def snippet() -> str:
    m = re.search(r"```python\n(.*?)```", section(template(), "Quick start"), re.S)
    assert m, "no python snippet in Quick start"
    return m.group(1)


def test_snippet_is_at_most_30_lines():
    lines = snippet().rstrip("\n").split("\n")
    assert len(lines) <= 30
    assert "modern_six" in snippet() and "syn_fold" in snippet() and "cause_task.csv" in snippet()


@pytest.mark.parametrize("which", ["b", "b_light"])
def test_snippet_runs_against_a_built_bundle(which, request, monkeypatch):
    import csv
    pytest.importorskip("pandas")
    bd = request.getfixturevalue(which)
    monkeypatch.chdir(bd)
    ns: dict = {}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(snippet(), "<quick-start>", "exec"), ns)
    with (bd / "core/cause_task.csv").open(encoding="utf-8", newline="") as f:
        want = sum(1 for r in csv.DictReader(f) if r["syn_regime"] == "modern_six")
    assert want > 300
    assert len(ns["task"]) == want
    assert str(want) in out.getvalue()


def test_snippet_check_catches_a_broken_snippet(b, monkeypatch):
    """Deliberate violation: a snippet that names a column the bundle does not have."""
    pytest.importorskip("pandas")
    monkeypatch.chdir(b)
    broken = snippet().replace('"syn_fold"', '"no_such_column"')
    with pytest.raises(KeyError):
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(broken, "<broken>", "exec"), {})
