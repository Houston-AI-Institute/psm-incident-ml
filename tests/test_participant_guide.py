"""The participant guide (README_FIRST.md): structure, verbatim text, stamping, length, quick start.

Template tests read the guide source. Stamped tests build a bundle through the real
builder. Every guard (the placeholder refusal, the length cap, the fence balance check)
has a companion test that commits a real violation and shows it is caught.
"""
from __future__ import annotations

import contextlib
import csv
import io
import re
import subprocess
from pathlib import Path

import pytest

from psm import bundle
from psm.provenance import TOKENS

csv.field_size_limit(10**8)

REPO = Path(__file__).resolve().parents[1]
GUIDE = REPO / "docs/release/PARTICIPANT_GUIDE.md"
FIRST_GUIDE_COMMIT = "d37ce7a"      # the guide before fix round 1: it had the mode-truth defects

HEADINGS = [
    "What this is", "Content note", "Choosing tools", "Folders", "Provenance tokens",
    "Glossary", "Quick start", "Known defects", "Tracks", "Do not attempt",
    "About the companies", "How to report a result",
]
CONTENT_NOTE = (
    "These are real US offshore incident reports, written in the plain, factual style of an "
    "investigation. Some describe workplace injuries such as cuts, broken bones, burns and "
    "lost fingers. Incidents in which someone died were removed from this bundle. Names that "
    "follow a title (Mr., Mrs., Ms., Miss, Dr.) were replaced with `[name]`, along with the "
    "surname wherever it repeats. Other names may remain, because redaction is pattern-based. "
    "The source PDFs linked in `bsee_source_url` are not redacted. `content_flags.csv` marks "
    "rows that describe a serious injury or someone losing consciousness. The flags are "
    "pattern-based too, so they can miss some rows. If anything here is a problem, talk to a "
    "mentor."
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
    "bsee_operator": "telephone blocks",
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
    for name in ("companies", "text", "no-text"):
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


@pytest.fixture(scope="module")
def b_light_nocomp(tmp_path_factory):
    return _make(tmp_path_factory, "light_nocomp", text_light=True, include_companies=False)


@pytest.fixture(scope="module")
def modes(b, b_nocomp, b_light, b_light_nocomp):
    """name -> built bundle, for the four modes (companies x text-light)."""
    return {"core": b_nocomp, "text-light": b_light_nocomp, "companies": b, "text-light+companies": b_light}


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
                 "attribution, not secrecy", "generic templates, not linked to their incident",
                 "Contractor and rig names are not masked"):
        assert must in body, must
    assert not re.search(r"northstar|meridian|coastal", text, re.I)


def test_report_section_has_four_parts():
    text = section(template(), "How to report a result")
    for must in ("claim", "columns", "provenance", "n.", "limitation"):
        assert must in text, must


NEVER_ACCURACY = "Never call it accuracy."


def test_the_word_accuracy_appears_only_in_the_prohibition():
    """D2/R39: 'accuracy' never labels a metric. The one allowed use is the exact prohibition."""
    text = template()
    assert text.count(NEVER_ACCURACY) == 1
    assert NEVER_ACCURACY in section(text, "Tracks")
    assert not re.search(r"accura", text.replace(NEVER_ACCURACY, ""), re.I)
    assert re.search(r"accura", text.replace(NEVER_ACCURACY, "") + " the model accuracy is high", re.I)


def test_template_claims_that_were_false():
    text = template()
    assert "It says nothing about any company or person" not in text
    assert "makes no claims about any operator or person" in text
    assert "The reports name real operators, so do not judge them." in text
    gl = section(text, "Glossary")
    assert "`real_only` holds only real values" not in gl
    for must in ("`xw`", "`pseud`", "`key`", "generated cells left blank", "in `core/`, only `enriched` has provenance files"):
        assert must in gl, must
    assert "recommendations with no close-out" not in text
    assert "Tracks 3 and 4" in section(text, "Folders")
    assert "very small n (19 and 13)" in section(text, "Tracks")
    assert "None in this bundle." in section(text, "Provenance tokens").split("`llm`")[1].split("\n")[0]
    folders = section(text, "Folders")
    assert "no provenance file" in folders and "marked `mod`" in folders


def test_template_is_short():
    assert prose_words(template()) < MAX_WORDS - 250      # room for the stamped counts table


def test_length_cap_catches_a_long_guide():
    assert prose_words(template()) < MAX_WORDS
    assert prose_words(template() + "\nword " * MAX_WORDS) > MAX_WORDS
    code = "```python\n" + "word " * 5000 + "\n```"
    assert prose_words(template() + code) == prose_words(template())    # fenced code is excluded


# ---- stamped ---------------------------------------------------------------

def test_stamped_readme_has_no_placeholder(modes):
    for name, bd in modes.items():
        text = readme(bd)
        assert not re.search(r"\{[A-Za-z_]+\}", text), name
        assert "STUB" not in text and "<!--" not in text and "-->" not in text, name


def test_stamped_readme_is_within_three_pages(modes):
    for name, bd in modes.items():
        assert prose_words(readme(bd)) <= MAX_WORDS, (name, prose_words(readme(bd)))


def test_stamped_readme_headings(modes):
    for name, bd in modes.items():
        want = HEADINGS if "companies" in name else [h for h in HEADINGS if h != "About the companies"]
        assert re.findall(r"^## (.+)$", readme(bd), re.M) == want, name


def test_fences_follow_the_mode(modes):
    r = {k: readme(v) for k, v in modes.items()}
    for name, text in r.items():
        has_co, has_text = "companies" in name, not name.startswith("text-light")
        assert ("Company register audit" in text) is has_co, name
        assert ("companies/" in text) is has_co, name
        assert ("Alder" in text) is has_co, name
        assert ("Treating the fictional companies" in text) is has_co, name
        assert ("LLM structured extraction" in text) is has_text, name
        assert ("Cause classification" in text) is has_text, name
        assert ("Era drift" in text) is has_text, name
        assert ("Narrative text" in text) is has_text, name
        assert ("Tracks 3 and 4" in text) is has_text, name
        assert ("src_text_stripped" in text) is has_text, name
        assert ("Cause Description" in text) is has_text, name
        assert ("Text-light bundle" in text) is (not has_text), name
        assert ("The narrative and cause text are withheld in this bundle." in text) is (not has_text), name
        assert ("Their narratives are real BSEE text" in text) is (has_co and has_text), name
        assert ("unmasked narratives appear in" in text) is (has_co and has_text), name
        assert ("no text" in text) is (not has_text), name


def test_content_note_survives_stamping(modes):
    for bd in modes.values():
        assert "> " + CONTENT_NOTE in readme(bd)
        assert GOLD_SENTENCE in readme(bd)


# ---- the preview line ------------------------------------------------------

PREVIEW = "PREVIEW BUILD: organiser text not final. Not for distribution."


def test_a_flag_built_bundle_says_it_is_a_preview(modes):
    for name, bd in modes.items():            # every fixture is built with allow_placeholder_terms=True
        assert readme(bd).split("\n")[0] == PREVIEW, name
        assert readme(bd).split("\n")[2].startswith("# PSM incident dataset"), name


def test_a_build_without_the_flag_has_no_preview_line(tmp_path, monkeypatch):
    """Organiser text and terms supplied (stand-ins), no flag: it builds, with no preview line."""
    guide = bundle.GUIDE_PLACEHOLDER_RE.sub("The organiser's own tool guidance.", template())
    monkeypatch.setattr(bundle, "terms_text", lambda *a, **k: REAL_TERMS)
    monkeypatch.setattr(bundle, "guide_text", lambda *a, **k: guide)
    out = bundle.build_bundle(tmp_path / "x", include_companies=False, allow_dirty=True)
    text = readme(out)
    assert "PREVIEW" not in text and "The organiser's own tool guidance." in text
    assert text.startswith("# PSM incident dataset")


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
    with_ph = _guide_with(f"Some rules.\n{PLACEHOLDER}")
    with pytest.raises(SystemExit):
        bundle.check_guide(with_ph, allow_placeholder=False)
    bundle.check_guide(with_ph, allow_placeholder=True)
    bundle.check_guide(_guide_with("The organiser's own text."), allow_placeholder=False)


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


# ---- check_guide variants --------------------------------------------------

def _guide_with(choosing_body: str) -> str:
    return f"# T\n\n## What this is\n\nx\n\n## Choosing tools\n\n{choosing_body}\n\n## Folders\n\ny\n"


@pytest.mark.parametrize("comment", [
    "<!-- organiser text: under-18 tool guidance -->",
    "<!--organiser text: under-18 tool guidance-->",
    "<!--   ORGANISER   Text: under-18 tool guidance -->",
    "<!--\n  Organiser text:\n  under-18 tool guidance\n-->",
])
def test_check_guide_refuses_every_spelling_of_the_placeholder(comment):
    """Deliberate violations: case, spacing and line breaks inside the comment."""
    text = _guide_with("Under-18 participants do not use Google AI Studio.\n\n" + comment)
    with pytest.raises(SystemExit, match="placeholder"):
        bundle.check_guide(text, allow_placeholder=False)
    bundle.check_guide(text, allow_placeholder=True)


@pytest.mark.parametrize("comment", [
    "<!--organiser text-->", "<!--   ORGANISER   Text: x -->", "<!--\n Organiser text\n-->"])
def test_every_spelling_is_dropped_from_the_stamped_readme(comment):
    tpl = _guide_with("Real sentence.\n\n" + comment) + "{version} {commit} {commit_date} {counts}"
    out = bundle._readme(tpl, label="abc", commit_date="d", counts="c",
                         include_companies=True, text_light=False)
    assert "<!--" not in out and "organiser text" not in out.lower() and "Real sentence." in out


@pytest.mark.parametrize("body", ["", "<!-- organiser text: x -->", "<!-- a -->\n\n<!-- b -->"])
def test_check_guide_refuses_a_section_with_no_text_even_with_the_flag(body):
    """Deliberate violation: Choosing tools holds nothing but comments."""
    for allow in (False, True):
        with pytest.raises(SystemExit, match="no text"):
            bundle.check_guide(_guide_with(body), allow_placeholder=allow)


def test_check_guide_refuses_a_missing_section():
    with pytest.raises(SystemExit, match="no text"):
        bundle.check_guide("# T\n\n## What this is\n\nx\n", allow_placeholder=True)


def test_check_guide_accepts_supplied_text():
    bundle.check_guide(_guide_with("The organiser's own rules."), allow_placeholder=False)


# ---- mode truth: everything the README names exists in that bundle ----------

# Backticked tokens that are neither a file or folder in the bundle nor a column in a bundled
# CSV header. Each entry says why it is allowed. None of them may be a real file or column
# name (see test_the_token_allowlist_hides_nothing).
TOKEN_ALLOWLIST = {
    # provenance tokens (cell values)
    "src": "provenance token", "xw": "provenance token", "llm": "provenance token",
    "gold": "provenance token", "syn": "provenance token", "key": "provenance token",
    "pseud": "provenance token", "mod": "provenance token",
    # redaction placeholders shown as examples in the content note and About the companies
    "[name]": "redaction placeholder", "[company]": "redaction placeholder",
    # track tags
    "[no-code ok]": "track tag", "[code]": "track tag", "[AI tool]": "track tag",
    # cell values and protocol text
    "modern_six": "a syn_regime value", "syn_regime == modern_six": "the filter, as code",
    # named in Known defects as not shipped
    "Description": "withheld column; the known-defect bullet says it is not shipped",
}
HASH = re.compile(r"[0-9a-f]{7}(\+dirty)?")        # the commit label in "Built from commit"


def bundle_paths(b: Path) -> set[str]:
    out = set()
    for p in b.rglob("*"):
        out.add(p.relative_to(b).as_posix())
    return out


def bundle_headers(b: Path) -> set[str]:
    cols: set[str] = set()
    for f in b.rglob("*.csv"):
        with f.open(encoding="utf-8", newline="") as fh:
            cols |= set(next(csv.reader(fh), []))
    return cols


def backticked(text: str) -> list[str]:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.findall(r"`([^`\n]+)`", text)


def unknown_tokens(text: str, b: Path) -> list[str]:
    """Backticked names in ``text`` that are not in bundle ``b``: not a path or a path suffix
    (a file, a folder, a name inside a folder), not a CSV column, not an allowlisted token."""
    paths, cols = bundle_paths(b), bundle_headers(b)
    bad = []
    for tok in backticked(text):
        p = tok.rstrip("/")
        in_paths = any(x == p or x.endswith("/" + p) for x in paths)
        if in_paths or tok in cols or tok in TOKEN_ALLOWLIST or HASH.fullmatch(tok):
            continue
        bad.append(tok)
    return bad


def test_every_name_in_the_readme_exists_in_that_bundle(modes):
    for name, bd in modes.items():
        assert unknown_tokens(readme(bd), bd) == [], name


def test_the_token_allowlist_hides_nothing(modes):
    for name, bd in modes.items():
        paths, cols = bundle_paths(bd), bundle_headers(bd)
        for tok in TOKEN_ALLOWLIST:
            assert tok not in cols, (name, tok)
            assert not any(x == tok or x.endswith("/" + tok) for x in paths), (name, tok)


def test_mode_truth_check_catches_planted_violations(modes):
    """Deliberate violations: a folder, a column and a file that the bundle does not have."""
    core, light = modes["core"], modes["text-light"]
    assert unknown_tokens(readme(core), core) == []
    assert unknown_tokens(readme(core) + "\nSee `companies/`.", core) == ["companies/"]
    assert unknown_tokens(readme(light) + "\nUse `src_text_stripped` and `Cause Description`.", light) == [
        "src_text_stripped", "Cause Description"]
    assert unknown_tokens("`no_such_file.csv` and `core/nope.csv`", core) == ["no_such_file.csv", "core/nope.csv"]
    # the same names are fine where they do ship
    assert unknown_tokens("`companies/` `src_text_stripped` `Cause Description`", modes["companies"]) == []


def test_mode_truth_check_would_have_caught_the_first_guide(tmp_path, monkeypatch):
    """The guide of d37ce7a named src_text_stripped and Cause Description in every mode. Built
    text-light, the check flags both."""
    old = subprocess.run(["git", "show", f"{FIRST_GUIDE_COMMIT}:docs/release/PARTICIPANT_GUIDE.md"],
                         cwd=REPO, check=True, capture_output=True, text=True).stdout
    monkeypatch.setattr(bundle, "guide_text", lambda *a, **k: old)
    out = bundle.build_bundle(tmp_path / "old", include_companies=False, text_light=True,
                              allow_dirty=True, allow_placeholder_terms=True)
    bad = unknown_tokens(readme(out), out)
    assert {"src_text_stripped", "Cause Description"} <= set(bad), bad


# ---- claims the guide makes about the data ----------------------------------

def test_no_llm_or_gold_cells_in_any_mode(modes):
    for name, bd in modes.items():
        for f in sorted(bd.rglob("*provenance.csv")):
            with f.open(encoding="utf-8", newline="") as fh:
                toks = {v for row in csv.reader(fh) for v in row}
            assert not ({"llm", "gold"} & toks), (name, f.name)
        with (bd / "core/cells_long.csv").open(encoding="utf-8", newline="") as fh:
            assert not ({"llm", "gold"} & {r["provenance"] for r in csv.DictReader(fh)}), name
        with (bd / "core/data_dictionary.csv").open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                for k in ("pct_llm", "pct_gold"):
                    assert r[k] in ("n/a", "0.0"), (name, r["table"], r["column"], k)


def test_real_only_holds_no_generated_cells(b_nocomp):
    """The glossary: real_only has real, xw, pseud and key cells; generated cells are blank;
    in core/, only enriched has provenance files."""
    def rd(rel):
        with (b_nocomp / rel).open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    for real, prov in (("core/real_only/incidents.csv", "core/enriched/provenance.csv"),):
        ro, pv = rd(real), rd(prov)
        assert len(ro) == len(pv)
        seen = set()
        for r, p in zip(ro, pv):
            for c, v in r.items():
                if v:
                    seen.add(p[c])
                else:
                    assert p[c] in ("", "syn"), (c, p[c])
                if p[c] == "syn":
                    assert v == "", c
        assert seen <= {"src", "mod", "xw", "pseud", "key"}, seen
        assert {"xw", "pseud", "key"} <= seen
    assert not list((b_nocomp / "core/real_only").glob("*provenance*"))
    assert (b_nocomp / "core/enriched/provenance.csv").exists()


def test_the_small_regime_numbers_in_track_4_are_the_bundles(b_nocomp):
    from psm.baseline import regime_summary
    ns = sorted(g["n"] for g in regime_summary(b_nocomp / "core/cause_task.csv"))
    assert ns == [13, 19]
    assert "(19 and 13)" in section(template(), "Tracks")


def test_track_6_gaps_exist_in_every_company(b):
    """The guide names one gap, close-outs marked Behind. The second gap (incidents with no
    recommendation) is no longer named in the guide (word cap), but must still exist in all
    three companies in the bundle."""
    def rd(rel):
        with (b / rel).open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    assert '"Behind"' in section(template(), "Tracks")
    for co in sorted(p.name for p in (b / "companies").iterdir()):
        inc = {r["Incident Number"] for r in rd(f"companies/{co}/incidents.csv")}
        rec = {r["Incident Number"] for r in rd(f"companies/{co}/recommendations.csv")}
        behind = [r for r in rd(f"companies/{co}/closeout.csv") if r["Schedule Status"] == "Behind"]
        assert behind, co
        assert inc - rec, co
