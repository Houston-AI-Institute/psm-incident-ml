"""Release bundle builder: allowlist, fatal drop, redaction, provenance, determinism.

Every guard has a companion test that commits a real violation and shows the same
check catches it. `test_no_contacts` and `test_named_cells_have_no_original_name_tokens`
(and the helpers they use) deliberately do not import `psm.content`.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

from psm import bundle, scenario
from psm.provenance import TOKENS

csv.field_size_limit(10**8)

REPO = Path(__file__).resolve().parents[1]
E19 = REPO / "data" / "processed" / "e19"
COMP = REPO / "data" / "companies"
COMPANIES = ["northstar", "meridian", "coastal"]
NARRATIVE = [
    "What happened?  ", "What was the outcome?", "How did the incident occur",
    "Description", "incident Title", "Detail",
    "What was the worst outcome that could reasonably be expected to have happened?",
]
PINNED_FATAL = {"SP-38-20071229-1550", "ST-168-20080220-0915", "VR-318-20080413-0730"}
KEYS = {"incidents": ["Incident Number"], "causes": ["Incident Number", "Cause number"],
        "recommendations": ["Incident Number", "Recommendation Number"],
        "closeout": ["Incident Number", "Recommendation Number"]}


# ---- helpers ---------------------------------------------------------------

def rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def header(path):
    with open(path, encoding="utf-8", newline="") as f:
        return next(csv.reader(f))


def nrows(path):
    return len(rows(path))


def rel_files(b: Path):
    return sorted(p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file())


def text_files(b: Path):
    return [p for p in sorted(b.rglob("*")) if p.is_file()]


def incident_tables(b: Path):
    return sorted(b.rglob("incidents.csv"))


def zip_path(b: Path) -> Path:
    return b.parent / (b.name + ".zip")


def zip_bytes(b: Path) -> bytes:
    return zip_path(b).read_bytes()


def expected_allowlist(companies: bool) -> list[str]:
    s = ["README_FIRST.md", "TERMS.md", "BASELINE.md", "SHA256SUMS"]
    s += [f"core/real_only/{n}.csv" for n in ("incidents", "causes", "recommendations", "closeout")]
    s += ["core/real_only/splits.json"]
    s += [f"core/enriched/{n}.csv" for n in ("incidents", "provenance", "causes", "causes_provenance")]
    s += [f"core/{n}.csv" for n in ("cause_task", "cells_long", "content_flags", "redactions", "data_dictionary")]
    s += [f"advanced/{n}.csv" for n in ("causes_confidence", "causes_secondary_element",
                                         "causes_source_field", "bsee_unmapped")]
    s += [f"advanced/schema/{n}.yaml" for n in (
        "crosswalk", "e19_disposition", "synth_rules", "xw_cause_qualifiers",
        "xw_consequence_tiers", "xw_incident_type", "xw_outcome")]
    if companies:
        for co in COMPANIES:
            s += [f"companies/{co}/{n}.csv" for n in (
                "incidents", "causes", "recommendations", "closeout", "provenance",
                "causes_provenance", "recommendations_provenance", "closeout_provenance")]
    return sorted(s)


def _fatal(rs):
    return {r["Incident Number"] for r in rs
            if any(r.get(f"Incident Type {x}") == "Injury Fatality" for x in "ABCD")}


def fatal_sets(extra=()):
    """{scope: ids}; company scopes include companies whose donor is in the core set."""
    core = _fatal(rows(E19 / "enriched" / "incidents.csv")) | set(extra)
    out = {"core": core}
    for co in COMPANIES:
        dm = {scenario.scenario_incident_number(co, d): d for d in scenario.donor_partition(co)}
        out[co] = _fatal(rows(COMP / co / "incidents.csv")) | {s for s, d in dm.items() if d in core}
    return out


def all_fatal(extra=()):
    return set().union(*fatal_sets(extra).values())


def fatal_trace(b: Path, ids) -> list[str]:
    """Files that mention a fatal id, plus incidents tables that keep a fatal type."""
    bad = []
    for f in text_files(b):
        s = f.read_text(encoding="utf-8")
        if any(i in s for i in ids):
            bad.append(f"id in {f.relative_to(b)}")
    for t in incident_tables(b):
        for r in rows(t):
            if "Injury Fatality" in {r.get(f"Incident Type {x}", "") for x in "ABCD"}:
                bad.append(f"fatal type in {t.relative_to(b)}")
    return bad


# independent contact detector (no psm.content)
_CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|\(\s*\d{3}\s*\)\s*\d{3}[\s.-]\d{4}|\b\d{3}[\s.-]\d{3}[\s.-]\d{4}\b")


def contacts_in(b: Path) -> list[str]:
    return [f.relative_to(b).as_posix() for f in text_files(b)
            if _CONTACT.search(f.read_text(encoding="utf-8"))]


# independent titled-name tokenizer (no psm.content); same stop rule as tests/test_content.py
_TITLES = {"Mr", "Mrs", "Ms", "Miss", "Dr", "MR", "MRS", "MS", "MISS", "DR"}


def titled_words(text: str) -> set[str]:
    toks = text.split()
    out = set()
    for i, t in enumerate(toks):
        if t.strip(".,;:()\"") not in _TITLES:
            continue
        for nxt in toks[i + 1:i + 4]:
            w = nxt.strip(".,;:()\"")
            if not w or not w[0].isupper():
                break
            if len(w) > 1:
                out.add(w)
                if nxt[-1] in ".,;:)":
                    break
    return out


def surviving_names(original: str, bundled: str) -> set[str]:
    return {w for w in titled_words(original)
            if re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", bundled)}


# bundle path -> (repo value file, repo provenance file or None)
def value_pairs(companies=True):
    out = {}
    for n in ("incidents", "causes", "recommendations", "closeout"):
        out[f"core/real_only/{n}.csv"] = (E19 / "real_only" / f"{n}.csv", None)
    out["core/enriched/incidents.csv"] = (E19 / "enriched/incidents.csv", E19 / "enriched/provenance.csv")
    out["core/enriched/causes.csv"] = (E19 / "enriched/causes.csv", E19 / "enriched/causes_provenance.csv")
    out["core/cause_task.csv"] = (E19 / "cause_task.csv", None)
    out["advanced/causes_confidence.csv"] = (E19 / "enriched/causes_confidence.csv", None)
    out["advanced/causes_secondary_element.csv"] = (E19 / "enriched/causes_secondary_element.csv", None)
    out["advanced/causes_source_field.csv"] = (E19 / "causes_source_field.csv", None)
    out["advanced/bsee_unmapped.csv"] = (E19 / "bsee_unmapped.csv", None)
    if companies:
        for co in COMPANIES:
            for n, p in (("incidents", "provenance"), ("causes", "causes_provenance"),
                         ("recommendations", "recommendations_provenance"),
                         ("closeout", "closeout_provenance")):
                out[f"companies/{co}/{n}.csv"] = (COMP / co / f"{n}.csv", COMP / co / f"{p}.csv")
    return out


def bundle_prov(rel: str):
    """Bundled provenance path for a value file, or None."""
    m = {"core/enriched/incidents.csv": "core/enriched/provenance.csv",
         "core/enriched/causes.csv": "core/enriched/causes_provenance.csv"}
    if rel in m:
        return m[rel]
    if rel.startswith("companies/"):
        co, name = rel.split("/")[1:]
        n = name[:-4]
        return f"companies/{co}/{'provenance' if n == 'incidents' else n + '_provenance'}.csv"
    return None


def key_of(rel: str, r: dict):
    if rel.endswith("incidents.csv") or rel.endswith("bsee_unmapped.csv"):
        return (r["Incident Number"],)
    if "Recommendation Number" in r:
        return (r["Incident Number"], r["Recommendation Number"])
    return (r["Incident Number"], r["Cause number"])


def provenance_pairs(b: Path):
    out = []
    for rel in value_pairs():
        p = bundle_prov(rel)
        if p and (b / rel).exists():
            out.append((b / rel, b / p))
    return out


# ---- fixtures --------------------------------------------------------------

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
def build(b, b_nocomp):
    return lambda companies: b if companies else b_nocomp


@pytest.fixture(params=["full", "nocomp", "light"])
def any_b(request, b, b_nocomp, b_light):
    return {"full": b, "nocomp": b_nocomp, "light": b_light}[request.param]


# ---- allowlist -------------------------------------------------------------

@pytest.mark.parametrize("companies", [True, False])
def test_file_set_is_allowlist(build, companies):
    assert rel_files(build(companies)) == expected_allowlist(companies)


def test_text_light_file_set_is_allowlist(b_light):
    assert rel_files(b_light) == expected_allowlist(True)


def test_zip_matches_directory(b):
    with zipfile.ZipFile(zip_path(b)) as z:
        names = z.namelist()
        assert names == sorted(names)
        assert [n.split("/", 1)[1] for n in names] == rel_files(b)
        assert {n.split("/", 1)[0] for n in names} == {b.name}
        for n in names:
            assert z.read(n) == (b / n.split("/", 1)[1]).read_bytes()


def test_withheld_absent(any_b):
    names = " ".join(map(str, rel_files(any_b)))
    # Task 8 adds "northstar", "meridian", "coastal" (the company slugs are renamed there).
    for bad in ["manifest.json", "llm_", "filled", "gold", ".xlsx", "scenarios", "e19_labels",
                "investigations_index"]:
        assert bad not in names


# ---- fatal drop ------------------------------------------------------------

def test_pinned_fatal_ids_and_company_ids():
    s = fatal_sets()
    assert s["core"] == PINNED_FATAL
    assert len(s["meridian"]) >= 1 and len(s["coastal"]) >= 1     # company layers hold fatal ids


def test_no_fatal_trace_anywhere(any_b):
    ids = all_fatal()
    assert fatal_trace(any_b, ids) == []
    for t in incident_tables(any_b):
        for r in rows(t):
            assert "Injury Fatality" not in {r.get(f"Incident Type {x}", "") for x in "ABCD"}, t


def test_fatal_scan_catches_planted_violations(b, tmp_path):
    """Deliberate violation: plant a fatal id in a text file and a fatal type in an incidents table."""
    ids = all_fatal()
    assert fatal_trace(b, ids) == []
    copy = tmp_path / "copy"
    shutil.copytree(b, copy)
    (copy / "README_FIRST.md").write_text(
        (copy / "README_FIRST.md").read_text(encoding="utf-8") + "\n" + sorted(PINNED_FATAL)[0], encoding="utf-8")
    inc = copy / "core/real_only/incidents.csv"
    rs = rows(inc)
    cols = header(inc)
    rs[0]["Incident Type A"] = "Injury Fatality"
    with inc.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rs)
    bad = fatal_trace(copy, ids)
    assert any(x.startswith("id in README_FIRST.md") for x in bad)
    assert any(x.startswith("fatal type in core/real_only/incidents.csv") for x in bad)


def test_rows_dropped_exactly(b):
    """Each bundled table equals the repo table minus the rows of its scope's fatal ids."""
    sets = fatal_sets()
    dropped_any = 0
    for rel, (src, _prov) in value_pairs().items():
        scope = rel.split("/")[1] if rel.startswith("companies/") else "core"
        repo_rows = rows(src)
        ids = {r["Incident Number"] for r in repo_rows}
        want = [r for r in repo_rows if r["Incident Number"] not in sets[scope]]
        assert len(rows(b / rel)) == len(want), rel
        assert {r["Incident Number"] for r in rows(b / rel)} == {r["Incident Number"] for r in want}, rel
        dropped_any += len(repo_rows) - len(want)
        assert len(ids - sets[scope]) == len({r["Incident Number"] for r in want}), rel
    assert dropped_any > 0


def test_donor_linked_company_rows_are_dropped(b):
    sets = fatal_sets()
    total_linked = 0
    for co in COMPANIES:
        dm = {scenario.scenario_incident_number(co, d): d for d in scenario.donor_partition(co)}
        in_company = {r["Incident Number"] for r in rows(COMP / co / "incidents.csv")}
        assert set(dm) <= in_company, co                        # the donor map covers real company rows
        linked = {s for s, d in dm.items() if d in sets["core"]}
        kept = {r["Incident Number"] for r in rows(b / "companies" / co / "incidents.csv")}
        assert not (linked & kept), co                          # every donor-linked row is gone
        assert len(kept) == len(in_company) - len(sets[co]), co
        total_linked += len(linked)
    assert total_linked > 0


def test_exclusions_drop_whole_incident(tmp_path, monkeypatch):
    """Deliberate violation: list a non-fatal id in the exclusions and see it vanish everywhere,
    including the company row minted from it."""
    task_ids = {r["Incident Number"] for r in rows(E19 / "cause_task.csv")}
    donors = scenario.donor_partition("northstar")
    victim = next(d for d in donors if d in task_ids and d not in PINNED_FATAL)
    company_id = scenario.scenario_incident_number("northstar", victim)
    assert victim in {r["Incident Number"] for r in rows(E19 / "enriched/incidents.csv")}
    assert company_id in {r["Incident Number"] for r in rows(COMP / "northstar/incidents.csv")}
    monkeypatch.setattr(bundle, "load_exclusions", lambda *a, **k: [victim])
    out = bundle.build_bundle(tmp_path / "x", allow_dirty=True, allow_placeholder_terms=True)
    assert fatal_trace(out, all_fatal(extra=[victim])) == []
    assert victim not in {i for t in out.rglob("*.csv") for r in rows(t) for i in [r.get("Incident Number", "")]}
    assert company_id not in {r["Incident Number"] for r in rows(out / "companies/northstar/incidents.csv")}
    splits = json.loads((out / "core/real_only/splits.json").read_text(encoding="utf-8"))
    assert victim not in {i for v in splits["incident_ids"].values() for i in v}


def test_unknown_exclusion_id_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "load_exclusions", lambda *a, **k: ["NOT-AN-INCIDENT"])
    with pytest.raises(SystemExit):
        bundle.build_bundle(tmp_path / "x", allow_dirty=True, allow_placeholder_terms=True)


def test_splits_counts_recomputed(b):
    src = json.loads((E19 / "real_only/splits.json").read_text(encoding="utf-8"))
    out = json.loads((b / "core/real_only/splits.json").read_text(encoding="utf-8"))
    drop = fatal_sets()["core"]
    removed = 0
    for reg, ids in out["incident_ids"].items():
        assert out["regimes"][reg]["n_incidents"] == len(ids), reg
        assert ids == [i for i in src["incident_ids"][reg] if i not in drop], reg
        removed += len(src["incident_ids"][reg]) - len(ids)
    assert removed == len(drop & {i for v in src["incident_ids"].values() for i in v}) > 0
    assert out["why"] == src["why"] and out["undated_excluded"] == src["undated_excluded"]


def test_splits_transform_is_byte_stable_without_drops():
    text = (E19 / "real_only/splits.json").read_text(encoding="utf-8")
    assert bundle.transform_splits(text, set()) == text


# ---- contacts and names ----------------------------------------------------

def test_no_contacts(any_b):
    assert contacts_in(any_b) == []


def test_contact_check_catches_planted_contacts(b, tmp_path):
    """R26. Real data has 0 contacts, so the bare check passes vacuously unless it can fail."""
    assert contacts_in(b) == []
    copy = tmp_path / "copy"
    shutil.copytree(b, copy)
    causes = copy / "core/real_only/causes.csv"
    rs, cols = rows(causes), header(causes)
    rs[0]["Cause Description"] += " reach me at 713-555-0142 or j.doe@example.com"
    with causes.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rs)
    assert contacts_in(copy) == ["core/real_only/causes.csv"]
    (copy / "README_FIRST.md").write_text("call (713) 555-0142", encoding="utf-8")
    assert "README_FIRST.md" in contacts_in(copy)


PINNED_CELLS = [("EI-330-20061011-2345", "What happened?  "), ("SM-106-20060529-1530", "What happened?  ")]


def _named_cells(bundled_incidents: Path, bundled_causes: Path, repo_incidents: Path, repo_causes: Path):
    """Yield (label, original text, bundled text) for the pinned named cells."""
    orig = {r["Incident Number"]: r for r in rows(repo_incidents)}
    got = {r["Incident Number"]: r for r in rows(bundled_incidents)}
    for inc, col in PINNED_CELLS:
        assert inc in orig and inc in got, inc
        yield (inc, col), orig[inc][col], got[inc][col]
    orig_c = {(r["Incident Number"], r["Cause number"]): r["Cause Description"]
              for r in rows(repo_causes) if r["Incident Number"] == "PN-891-20051205-2200"}
    got_c = {(r["Incident Number"], r["Cause number"]): r["Cause Description"]
             for r in rows(bundled_causes) if r["Incident Number"] == "PN-891-20051205-2200"}
    assert orig_c and set(orig_c) == set(got_c)
    for k in orig_c:
        yield k, orig_c[k], got_c[k]


def test_named_cells_have_no_original_name_tokens(b):
    checked = 0
    for layer in ("real_only", "enriched"):
        for label, original, bundled in _named_cells(
                b / f"core/{layer}/incidents.csv", b / f"core/{layer}/causes.csv",
                E19 / layer / "incidents.csv", E19 / layer / "causes.csv"):
            checked += len(titled_words(original))
            assert surviving_names(original, bundled) == set(), (layer, label)
    assert checked > 0


def test_name_check_catches_an_unredacted_cell(b):
    """Deliberate violation: feed the ORIGINAL text as the bundled text; the check must fire."""
    fired = 0
    for _label, original, _bundled in _named_cells(
            b / "core/real_only/incidents.csv", b / "core/real_only/causes.csv",
            E19 / "real_only/incidents.csv", E19 / "real_only/causes.csv"):
        if titled_words(original):
            assert surviving_names(original, original) == titled_words(original)
            fired += 1
    assert fired >= 3


# R28. Names are redacted per incident, not per cell: a surname that follows a title in one
# cell must not survive bare in another cell of the same incident. This test's own copy of the
# function-word stoplist (words that start a sentence after a nameless title); it deliberately
# does not import psm.content. test_stoplist_copy_matches_content keeps the two in step.
_STOP = {w.lower() for w in (
    "The", "There", "This", "That", "These", "Those", "Then", "Thus", "A", "An", "In", "On", "At",
    "It", "Its", "He", "She", "They", "We", "I", "You", "And", "But", "Or", "If", "When", "While",
    "After", "Before", "During", "As", "For", "From", "By", "With", "Of", "To", "Was", "Were",
    "Is", "Are", "Not", "No", "Also", "However", "Investigation", "Personnel",
    "Moreover", "Furthermore", "Additionally", "Therefore", "Although", "Because", "Since",
    "Once", "Upon", "Following", "According", "Both", "All", "Each", "Another", "Other",
    "Within", "Without", "Between", "Through", "Per", "Via")}
_TITLE_ANY = re.compile(r"\b(?:mr|mrs|ms|miss|dr)\b\.?", re.I)


def original_name_words(text: str) -> set[str]:
    """Words after Mr/Mrs/Ms/Miss/Dr (any case, period optional); same stop rule as titled_words
    (stop at a lowercase word or after a word ending in punctuation), plus: a word that opens with
    a bracket or quote ends the run, e.g. "Mr. A B (Acme operator)" does not take Acme as a name.
    Initials carry no identity."""
    out = set()
    for m in _TITLE_ANY.finditer(text):
        for nxt in text[m.end():].split()[:3]:
            if not nxt[0].isalpha() or not nxt[0].isupper():
                break                       # lowercase, or an opening bracket/quote: a new clause
            w = nxt.strip(".,;:()\"")
            if len(w) > 1 and w.lower() not in _STOP:
                out.add(w)
            if nxt[-1] in ".,;:)":
                break
    return out


def survivors(original: dict[str, list[str]], bundled: dict[str, list[str]]) -> list[tuple[str, int]]:
    """(incident, n distinct words) where a title-following word of ANY original cell of the
    incident occurs as a whole word in ANY bundled cell of the same incident. Words never printed."""
    bad = []
    for inc, cells in original.items():
        words = set().union(*(original_name_words(c) for c in cells)) if cells else set()
        if not words:
            continue
        alt = "|".join(re.escape(w) for w in sorted(words))
        rx = re.compile(rf"(?<![A-Za-z])(?:{alt})(?![A-Za-z])")
        hit = {w for c in bundled.get(inc, []) for w in rx.findall(c)}
        if hit:
            bad.append((inc, len(hit)))
    return sorted(bad)


def _cells_by_incident(files) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in files:
        with open(f, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                if "Incident Number" in r:
                    out.setdefault(r["Incident Number"], []).extend(v for v in r.values() if v)
    return out


def test_no_title_following_word_survives_in_any_cell_of_its_incident(b):
    original = _cells_by_incident(p for p, _ in value_pairs().values())
    bundled = _cells_by_incident(sorted(b.rglob("*.csv")))
    assert sum(1 for cells in original.values() if any(original_name_words(c) for c in cells)) > 20
    assert survivors(original, bundled) == []


def test_survivor_check_can_fail():
    """Planted: a titled name in one cell, the bare surname in another cell of the same incident."""
    orig = {"I1": ["Mr. Zorblax slipped on the deck", "Zorblax was taken ashore"], "I2": ["Dr. Quimby arrived"]}
    assert survivors(orig, orig) == [("I1", 1), ("I2", 1)]
    # the cell that carries the title is masked, the other cell is not: still caught
    half = {"I1": ["[name] slipped on the deck", "Zorblax was taken ashore"], "I2": ["[name] arrived"]}
    assert survivors(orig, half) == [("I1", 1)]
    # fully masked, and a same-text word in another incident, are both fine
    ok = {"I1": ["[name] slipped on the deck", "[name] was taken ashore"], "I2": ["[name] arrived"],
          "I3": ["Zorblax is an unrelated word here"]}
    assert survivors(orig, ok) == []
    # titles in either case, with or without a period; sentence-start words are not names
    assert original_name_words("MR. QUILLEN and mrs Baxter and dr. Pell. The end") == {"QUILLEN", "Baxter", "Pell"}
    assert original_name_words("Mr. The crane tipped. Ms There") == set()
    assert original_name_words("Mr. Aa Bb (Cc operator) said") == {"Aa", "Bb"}


def test_planted_names_are_masked_across_cells_of_one_incident():
    """The production path on a synthetic incident: title in one cell, bare surname in the other
    cell, in different tables of the same scope; a second incident keeps the same word."""
    inc_cols = ["Incident Number", "What happened?  ", "What was the outcome?"]
    cause_cols = ["Incident Number", "Cause number", "Cause Description"]
    inc = _tbl("incidents", inc_cols, [
        {"Incident Number": "I1", "What happened?  ": "Mr. Zorblax slipped.", "What was the outcome?": "Zorblax was hurt."},
        {"Incident Number": "I2", "What happened?  ": "A crew met.", "What was the outcome?": "Zorblax Marine won."}])
    cause = _tbl("causes", cause_cols, [
        {"Incident Number": "I1", "Cause number": "1", "Cause Description": "Poor grip, said Zorblax."}])
    got = bundle.redact_scope([inc, cause], {inc.rel: inc.cols, cause.rel: cause.cols})
    assert inc.rows[0]["What was the outcome?"] == "[name] was hurt."
    assert cause.rows[0]["Cause Description"] == "Poor grip, said [name]."
    assert inc.rows[1]["What was the outcome?"] == "Zorblax Marine won."      # other incident: untouched
    assert {(g["Incident Number"], g["column"]) for g in got} == {
        ("I1", "What happened?  "), ("I1", "What was the outcome?"), ("I1", "Cause Description")}


def test_stoplist_words_are_not_collected_and_cell_still_masks_its_own_title():
    cols = ["Incident Number", "What happened?  ", "What was the outcome?"]
    t = _tbl("incidents", cols, [{"Incident Number": "I1", "What happened?  ": "Mr. The crane tipped.",
                                  "What was the outcome?": "The deck was wet. There was a crew. This held."}])
    bundle.redact_scope([t], {t.rel: t.cols})
    assert t.rows[0]["What was the outcome?"] == "The deck was wet. There was a crew. This held."


def test_redact_names_argument():
    from psm import content
    # names=None is the original per-cell rule, stop words and all
    assert content.redact("Mr. The crane tipped. The end") == "[name] crane tipped. [name] end"
    assert content.redact("Mr. Zorblax left. Zorblax won") == "[name] left. [name] won"
    # with names: the incident's words mask here too; the stoplist is never collected
    assert content.redact("Zorblax won", {"Zorblax"}) == "[name] won"
    assert content.redact("Mr. The crane tipped. The end", set()) == "[name] crane tipped. The end"
    assert content.titled_name_words("Mr. The crane. Dr. Zorblax Q. Vee, said") == {"Zorblax", "Vee"}
    assert content.titled_name_words("THE end. MR. THE crane. MRS. ZORBLAX") == {"ZORBLAX"}


def test_stoplist_copy_matches_content():
    from psm import content
    assert _STOP == {w.lower() for w in content.NAME_STOPWORDS}


# ---- structure -------------------------------------------------------------

def test_bsee_unmapped_columns(any_b):
    assert header(any_b / "advanced/bsee_unmapped.csv") == [
        "Incident Number", "bsee_operator", "bsee_water_depth", "bsee_panel_formed", "bsee_source_url"]
    vals = {r["bsee_panel_formed"] for r in rows(any_b / "advanced/bsee_unmapped.csv")}
    assert vals <= {"YES", "NO", ""} and "NO" in vals


def test_panel_formed_parser():
    f = bundle.parse_panel_formed
    assert f("29. ACCIDENT INVESTIGATION PANEL FORMED: NO Guillotte /") == "NO"
    assert f("29. ACCIDENT INVESTIGATION NO / PANEL FORMED:") == "NO"
    assert f("PANEL FORMED: YES") == "YES"
    assert f("29. ACCIDENT INVESTIGATION PANEL FORMED:") == ""
    assert f("") == ""
    assert f("YES NO") == ""


def test_provenance_aligned(any_b):
    for vals, prov in provenance_pairs(any_b):
        assert header(vals) == header(prov) and nrows(vals) == nrows(prov)
        for vr, pr in zip(rows(vals), rows(prov)):
            for c in header(vals):
                if c not in ("Incident Number", "Cause number", "Recommendation Number"):
                    assert bool(vr[c].strip()) == bool(pr[c]), (vals, c)


def test_tokens_closed(any_b):
    seen = set()
    for _vals, prov in provenance_pairs(any_b):
        for pr in rows(prov):
            seen |= set(pr.values())
    assert seen <= TOKENS
    assert "src" in seen
    # text-light ships no narrative, hence no redacted (mod) cell
    has_narrative = "What happened?  " in header(any_b / "core/enriched/incidents.csv")
    assert ("mod" in seen) == has_narrative


def test_mod_only_replaces_src(b):
    n_mod = 0
    for rel, (src, src_prov) in value_pairs().items():
        pb = bundle_prov(rel)
        if not pb:
            continue
        repo_vals, repo_prov = rows(src), rows(src_prov)
        repo = {key_of(rel, v): (v, p) for v, p in zip(repo_vals, repo_prov)}
        assert len(repo) == len(repo_vals), rel
        for vr, pr in zip(rows(b / rel), rows(b / pb)):
            rv, rp = repo[key_of(rel, vr)]
            for c, tok in pr.items():
                if tok == "mod":
                    n_mod += 1
                    assert rp[c] in ("src", "mod"), (rel, c, rp[c])
                    assert vr[c] != rv[c], (rel, c)          # 'mod' means the text really changed
                elif c in rp:
                    assert tok == rp[c], (rel, c, tok, rp[c])
    assert n_mod > 0


def test_untransformed_cells_round_trip(b):
    red = {(r["table"], r["row_key"], r["column"]) for r in rows(b / "core/redactions.csv")}
    checked = 0
    for rel, (src, _sp) in value_pairs().items():
        repo = {key_of(rel, r): r for r in rows(src)}
        pb = bundle_prov(rel)
        prov = rows(b / pb) if pb else None
        for i, vr in enumerate(rows(b / rel)):
            rv = repo[key_of(rel, vr)]
            for c in header(b / rel):
                if rel.endswith("bsee_unmapped.csv") and c == "bsee_panel_formed":
                    continue
                if prov is not None:
                    changed = prov[i][c] == "mod"
                else:
                    changed = (rel, "|".join(key_of(rel, vr)), c) in red
                if not changed:
                    assert vr[c] == rv[c], (rel, c)
                    checked += 1
    assert checked > 100000


def test_round_trip_check_can_fail(b):
    """Deliberate violation: a cell redacted but not listed in redactions.csv is caught as a mismatch."""
    rel = "core/real_only/causes.csv"
    repo = {key_of(rel, r): r for r in rows(E19 / "real_only/causes.csv")}
    listed = {(r["table"], r["row_key"], r["column"]) for r in rows(b / "core/redactions.csv")}
    diffs = {(rel, "|".join(key_of(rel, r)), c) for r in rows(b / rel) for c in r
             if r[c] != repo[key_of(rel, r)][c]}
    assert diffs and diffs <= listed                              # every difference is listed
    assert {x for x in listed if x[0] == rel} == diffs            # and every listing is a real difference


def test_redactions_file(b):
    assert header(b / "core/redactions.csv") == ["table", "Incident Number", "row_key", "column", "kind"]
    rs = rows(b / "core/redactions.csv")
    assert rs and {r["kind"] for r in rs} <= {"name", "contact"}
    # only tables with no provenance file land here
    assert not any(r["table"].startswith("companies/") or "enriched" in r["table"] for r in rs)
    assert {r["table"] for r in rs} >= {"core/real_only/incidents.csv", "core/real_only/causes.csv"}


def test_redacted_cells_carry_marker(b):
    prov = rows(b / "core/enriched/provenance.csv")
    vals = rows(b / "core/enriched/incidents.csv")
    marked = [(v, p) for v, p in zip(vals, prov) for c in NARRATIVE if p.get(c) == "mod" and "[name]" in v[c]]
    assert marked
    # every 'mod' narrative cell has a marker, and every marker sits in a 'mod' cell
    for v, p in zip(vals, prov):
        for c in NARRATIVE:
            if c in v:
                assert (p[c] == "mod") == ("[name]" in v[c] or "[contact]" in v[c]), c


def test_content_flags(b):
    f = rows(b / "core/content_flags.csv")
    assert header(b / "core/content_flags.csv") == ["Incident Number", "table", "row_key", "column", "flag"]
    flags = {r["flag"] for r in f}
    assert flags <= {"death_mention", "near_fatal", "serious_injury", "name_redacted", "contact_redacted"}
    assert {"serious_injury", "name_redacted", "near_fatal"} <= flags
    tables = {r["table"] for r in f}
    # cause_task.csv and bsee_operator are scanned too but hold no flaggable text today
    assert {"core/enriched/incidents.csv", "core/enriched/causes.csv",
            "core/real_only/recommendations.csv"} <= tables
    assert any(t.startswith("companies/") for t in tables)
    # computed BEFORE redaction: a name_redacted flag points at a cell that now holds the marker
    texts = {("core/real_only/incidents.csv", r["Incident Number"]): r for r in rows(b / "core/real_only/incidents.csv")}
    hits = [r for r in f if r["flag"] == "name_redacted" and r["table"] == "core/real_only/incidents.csv"]
    assert hits
    for r in hits:
        assert "[name]" in texts[(r["table"], r["Incident Number"])][r["column"]]
    # no flag points at a dropped row or a withheld column
    assert not any(r["column"] == "Description" for r in f)


def test_cells_long_unique(b):
    cl = rows(b / "core/cells_long.csv")
    assert header(b / "core/cells_long.csv") == [
        "Incident Number", "table", "Cause number", "column", "value", "provenance"]
    keys = [(r["Incident Number"], r["table"], r["Cause number"], r["column"]) for r in cl]
    assert len(keys) == len(set(keys))
    assert all(r["value"] != "" and r["provenance"] in TOKENS - {""} for r in cl)
    assert {r["table"] for r in cl} == {"incidents", "causes"}
    assert all((r["Cause number"] == "") == (r["table"] == "incidents") for r in cl)
    want = 0
    for name, kcols in (("incidents", ["Incident Number"]), ("causes", ["Incident Number", "Cause number"])):
        for r in rows(b / f"core/enriched/{name}.csv"):
            want += sum(1 for c, v in r.items() if c not in kcols and v != "")
    assert len(cl) == want


def test_sha256sums_verify(any_b):
    def mismatches(root: Path):
        lines = (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        listed = {}
        for ln in lines:
            h, name = ln.split("  ", 1)
            listed[name] = h
        bad = [n for n, h in listed.items() if hashlib.sha256((root / n).read_bytes()).hexdigest() != h]
        return bad, set(listed) ^ (set(rel_files(root)) - {"SHA256SUMS"})
    assert mismatches(any_b) == ([], set())


def test_sha256sums_detects_tampering(b, tmp_path):
    copy = tmp_path / "copy"
    shutil.copytree(b, copy)
    (copy / "core/real_only/closeout.csv").write_text("tampered", encoding="utf-8")
    listed = dict(reversed(ln.split("  ", 1)) for ln in (copy / "SHA256SUMS").read_text(encoding="utf-8").splitlines())
    h = hashlib.sha256((copy / "core/real_only/closeout.csv").read_bytes()).hexdigest()
    assert listed["core/real_only/closeout.csv"] != h


def test_deterministic(tmp_path):
    a = bundle.build_bundle(tmp_path / "a", allow_dirty=True, allow_placeholder_terms=True)
    c = bundle.build_bundle(tmp_path / "b", allow_dirty=True, allow_placeholder_terms=True)
    assert a.name == c.name and zip_bytes(a) == zip_bytes(c)
    assert rel_files(a) == rel_files(c)
    assert all((a / n).read_bytes() == (c / n).read_bytes() for n in rel_files(a))


def test_zip_times_come_from_commit_time(b):
    ct = int(subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=REPO, check=True,
                            capture_output=True, text=True).stdout)
    import time
    want = time.gmtime(ct)[:6]
    # ZIP stores seconds at 2-second resolution: an odd commit second reads back one lower.
    want = (*want[:5], want[5] - want[5] % 2)
    with zipfile.ZipFile(zip_path(b)) as z:
        assert {i.date_time for i in z.infolist()} == {want}


def test_text_light_has_no_narrative(b_light, b):
    dropped = set(NARRATIVE) | {"Cause Description", "src_text_stripped", "Recommendation Description"}
    for f in b_light.rglob("*.csv"):
        assert not (set(header(f)) & dropped), f
    # control: the full bundle does carry them
    assert "Cause Description" in header(b / "core/enriched/causes.csv")
    assert "Recommendation Description" in header(b / "core/real_only/recommendations.csv")
    assert "Recommendation Description" in header(b / "companies/northstar/recommendations.csv")
    # R29: the recommendations value and provenance files lose the column together
    for rel in ("companies/northstar/recommendations.csv", "companies/northstar/recommendations_provenance.csv",
                "core/real_only/recommendations.csv"):
        assert "Recommendation Description" not in header(b_light / rel), rel
    assert header(b_light / "companies/northstar/recommendations.csv") == header(
        b_light / "companies/northstar/recommendations_provenance.csv")
    assert nrows(b_light / "core/real_only/recommendations.csv") == nrows(b / "core/real_only/recommendations.csv")
    assert "src_text_stripped" in header(b / "core/cause_task.csv")
    assert "What happened?  " in header(b / "core/enriched/incidents.csv")
    for rel in ("core/enriched/incidents.csv", "core/enriched/provenance.csv"):
        assert header(b_light / rel) == header(b_light / "core/enriched/incidents.csv")
    cl = rows(b_light / "core/cells_long.csv")
    assert not ({r["column"] for r in cl} & dropped)
    assert "Narrative text" not in (b_light / "README_FIRST.md").read_text(encoding="utf-8")
    assert "Narrative text" in (b / "README_FIRST.md").read_text(encoding="utf-8")


def test_description_withheld_everywhere(any_b):
    for f in any_b.rglob("*.csv"):
        if f.name.startswith("incidents") or f.name.startswith("provenance"):
            assert "Description" not in header(f), f


def test_data_dictionary(b):
    dd = rows(b / "core/data_dictionary.csv")
    assert header(b / "core/data_dictionary.csv") == [
        "table", "column", "meaning", "pct_src", "pct_xw", "pct_llm", "pct_gold", "pct_syn",
        "pct_key", "pct_pseud", "pct_mod", "pct_blank", "notes"]
    by = {(r["table"], r["column"]): r for r in dd}
    assert len(by) == len(dd)
    # one row per bundled column of every bundled value table, plus withheld columns
    for rel in value_pairs():
        for c in header(b / rel):
            assert (rel, c) in by, (rel, c)
    # pct_* sums to 100 for provenance tables, is n/a otherwise
    for rel in value_pairs():
        r = by[(rel, header(b / rel)[1])]
        pcts = [r[k] for k in r if k.startswith("pct_")]
        if bundle_prov(rel):
            assert abs(sum(float(x) for x in pcts) - 100.0) < 0.6, rel
        else:
            assert set(pcts) == {"n/a"}, rel
    # meaning from e19_disposition where present; notes from the notes file
    assert "constructed key" in by[("core/enriched/incidents.csv", "Incident Number")]["meaning"]
    notes = lambda t, c: by[(t, c)]["notes"]
    assert "syn" in notes("core/enriched/incidents.csv", "Approval Date")
    assert "withheld" in notes("core/enriched/incidents.csv", "Description")
    assert by[("core/enriched/incidents.csv", "Description")]["pct_src"] == "n/a"
    assert "per field string, not per person" in notes("core/enriched/incidents.csv", "Investigation leader - Name")
    assert "assumed matrix" in notes("core/enriched/incidents.csv", "Health & Safety  - Consequence")
    assert "0.98" in notes("core/enriched/causes.csv", "Cause Description")
    assert "cause_task.csv" in notes("core/enriched/causes.csv", "Cause Description")
    assert "contact blocks" in notes("advanced/bsee_unmapped.csv", "bsee_operator")
    assert "real year" in notes("core/cause_task.csv", "syn_regime")
    # the enriched provenance really carries mod after redaction
    assert float(by[("core/enriched/incidents.csv", "What happened?  ")]["pct_mod"]) > 0


def test_notes_file_has_required_notes():
    n = yaml.safe_load((REPO / "schema/data_dictionary_notes.yaml").read_text(encoding="utf-8"))["notes"]
    for col in ("Approval Date", "Description", "Investigation leader - Name", "Cause Description",
                "bsee_operator", "syn_regime"):
        assert col in n, col


def test_readme_filled(b, b_nocomp, b_light):
    full = (b / "README_FIRST.md").read_text(encoding="utf-8")
    for p in ("{version}", "{commit}", "{commit_date}", "{counts}", "<!--"):
        assert p not in full, p
    short = b.name.rsplit("-", 1)[1]
    assert short in full
    for rel in expected_allowlist(True):
        if rel.endswith(".csv") and "provenance" not in rel:
            assert rel in full, rel                                # the counts table lists every table
    assert "Company tables" in full
    assert "Company tables" not in (b_nocomp / "README_FIRST.md").read_text(encoding="utf-8")
    assert "companies/" not in (b_nocomp / "README_FIRST.md").read_text(encoding="utf-8")
    assert "Narrative text" not in (b_light / "README_FIRST.md").read_text(encoding="utf-8")
    assert "Company tables" in (b_light / "README_FIRST.md").read_text(encoding="utf-8")


def _changed_cells(b: Path):
    """(cells whose bundled text differs from the repo cell, in tables with / without a provenance
    file), counted by comparing every shipped value table with its source by row key."""
    with_prov = without = 0
    for rel, (src, _prov) in value_pairs().items():
        if rel.endswith("bsee_unmapped.csv"):
            continue                                   # column reduction, not redaction
        orig = {key_of(rel, r): r for r in rows(src)}
        for r in rows(b / rel):
            o = orig[key_of(rel, r)]
            n = sum(1 for c, v in r.items() if v != o[c])
            if bundle_prov(rel):
                with_prov += n
            else:
                without += n
    return with_prov, without


def test_readme_counts_say_which_figure_they_report(b):
    text = (b / "README_FIRST.md").read_text(encoding="utf-8")
    with_prov, without = _changed_cells(b)
    m = re.search(r"Total shipped: (\d+) cell copies\. (\d+) of them are in tables with no provenance "
                  r"file.*?the other (\d+) are in tables with a provenance file", text, re.S)
    assert m, "the counts note is missing"
    assert (int(m[1]), int(m[2]), int(m[3])) == (with_prov + without, without, with_prov)
    assert "counts cell copies" in text
    # the table column sums to the same total
    col = sum(int(x) for x in re.findall(r"^\| `[^`]+` \| \d+ \| (?:\d+|-) \| (\d+|-) \|$", text, re.M) if x != "-")
    assert col == int(m[1])
    # and redactions.csv lists exactly the no-provenance cells (distinct table/row/column)
    red = rows(b / "core/redactions.csv")
    assert len({(r["table"], r["row_key"], r["column"]) for r in red}) == without


def test_baseline_is_run_on_the_bundled_task(b):
    from psm.baseline import regime_summary, render, run_baseline
    short = b.name.rsplit("-", 1)[1]
    res = run_baseline(b / "core/cause_task.csv")
    text = (b / "BASELINE.md").read_text(encoding="utf-8")
    assert text.startswith(render(res, short, regime_summary(b / "core/cause_task.csv")))
    assert f"| n (rows scored) | {res['n']} |" in text
    assert res["n"] <= 374


def test_baseline_light_uses_the_text_variant(b_light, b):
    assert (b_light / "BASELINE.md").read_text(encoding="utf-8").startswith(
        (b / "BASELINE.md").read_text(encoding="utf-8").split("\n")[0])
    assert "| n (rows scored) |" in (b_light / "BASELINE.md").read_text(encoding="utf-8")
    assert "text-light" in (b_light / "BASELINE.md").read_text(encoding="utf-8")


def test_terms_copied_verbatim(b):
    assert (b / "TERMS.md").read_bytes() == (REPO / "docs/release/TERMS.md").read_bytes()


# ---- refusals --------------------------------------------------------------

def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_sandbox(tmp_path):
    """A throwaway `git init` repo with a minimal fixture tree; one tracked-but-ignored source."""
    root = tmp_path / "sbx"
    for rel, text in {"src/psm/mod.py": "x = 1\n", "schema/s.yaml": "a: 1\n",
                      "docs/release/TERMS.md": "terms\n", "data/x.csv": "a\n1\n", "data/y.csv": "a\n2\n",
                      ".gitignore": "data/ignored.csv\n", "data/ignored.csv": "a\n3\n"}.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".gitignore", "src", "schema", "docs", "data/x.csv", "data/y.csv")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


SBX_SOURCES = ["data/x.csv", "schema/s.yaml", "docs/release/TERMS.md"]


def test_clean_sandbox_is_accepted(git_sandbox):
    assert bundle.input_problems(git_sandbox, SBX_SOURCES) == []
    # untracked files outside the named sources must not trip the guard (the _pilot_* folders)
    (git_sandbox / "data/_pilot").mkdir()
    (git_sandbox / "data/_pilot/z.csv").write_text("a\n", encoding="utf-8")
    (git_sandbox / "data/y.csv").write_text("a\nchanged\n", encoding="utf-8")
    assert bundle.input_problems(git_sandbox, SBX_SOURCES) == []


def test_refuses_modified_input(git_sandbox):
    (git_sandbox / "data/x.csv").write_text("a\n999\n", encoding="utf-8")
    assert any(" M data/x.csv" in p for p in bundle.input_problems(git_sandbox, SBX_SOURCES))
    with pytest.raises(SystemExit):
        bundle.require_clean_inputs(git_sandbox, SBX_SOURCES)


def test_refuses_untracked_input(git_sandbox):
    (git_sandbox / "src/psm/x.py").write_text("y = 2\n", encoding="utf-8")
    assert any("?? src/psm/x.py" in p for p in bundle.input_problems(git_sandbox, SBX_SOURCES))
    with pytest.raises(SystemExit):
        bundle.require_clean_inputs(git_sandbox, SBX_SOURCES)


def test_refuses_source_not_in_git(git_sandbox):
    with pytest.raises(SystemExit):
        bundle.require_clean_inputs(git_sandbox, SBX_SOURCES + ["data/ignored.csv"])
    with pytest.raises(SystemExit):
        bundle.require_clean_inputs(git_sandbox, SBX_SOURCES + ["data/missing.csv"])


def test_refuses_bytes_that_differ_from_head(git_sandbox):
    """Hide the modification from `git status`; the HEAD byte comparison must still catch it."""
    (git_sandbox / "data/x.csv").write_text("a\n999\n", encoding="utf-8")
    _git(git_sandbox, "update-index", "--assume-unchanged", "data/x.csv")
    assert not [p for p in bundle.input_problems(git_sandbox, SBX_SOURCES) if p.startswith(" M")]
    assert any("differs from HEAD" in p for p in bundle.input_problems(git_sandbox, SBX_SOURCES))
    with pytest.raises(SystemExit):
        bundle.require_clean_inputs(git_sandbox, SBX_SOURCES)


def test_build_refuses_a_dirty_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "input_problems", lambda *a, **k: [" M src/psm/x.py"])
    with pytest.raises(SystemExit):
        bundle.build_bundle(tmp_path / "x", allow_placeholder_terms=True)
    assert not (tmp_path / "x").exists()


def test_refuses_placeholder_terms(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "terms_text", lambda *a, **k: "PLACEHOLDER — terms pending organiser\nmore\n")
    with pytest.raises(SystemExit):
        bundle.build_bundle(tmp_path / "x", allow_dirty=True)
    assert not (tmp_path / "x").exists()


def test_check_terms():
    bundle.check_terms("Terms of use\n", allow_placeholder=False)
    with pytest.raises(SystemExit):
        bundle.check_terms("PLACEHOLDER — terms pending organiser\nx", allow_placeholder=False)
    bundle.check_terms("PLACEHOLDER — terms pending organiser\nx", allow_placeholder=True)


def test_existing_output_is_not_overwritten(b):
    with pytest.raises(SystemExit):
        bundle.build_bundle(b.parent, allow_dirty=True, allow_placeholder_terms=True)


def test_main_flags(monkeypatch):
    seen = {}
    monkeypatch.setattr(bundle, "build_bundle", lambda **kw: seen.update(kw) or Path("/x/psm-hackathon-v1-abc"))
    bundle.main(["--no-companies", "--text-light"])
    assert seen["include_companies"] is False and seen["text_light"] is True
    seen.clear()
    bundle.main([])
    assert seen["include_companies"] is True and seen["text_light"] is False
    assert "allow_dirty" not in seen and "allow_placeholder_terms" not in seen


# ---- redaction rule, on synthetic tables (real data has no titled name outside src cells) ----

def _tbl(kind, cols, rows, prov=None):
    return bundle.Tbl(bundle.Spec(f"t/{kind}.csv", "x", kind), cols, rows, prov)


def test_redaction_touches_only_src_and_mod_cells():
    cols = ["Incident Number", "Cause number", "Cause Description"]
    text = "Mr. John Smith slipped"
    rs = [dict(zip(cols, ["I1", "1", text])) for _ in range(4)]
    prov = [dict(zip(cols, ["key", "key", tok])) for tok in ("src", "syn", "mod", "xw")]
    t = _tbl("causes", cols, rs, prov)
    assert bundle._redact_table(t, ["Cause Description"]) == []          # provenance file: no redactions.csv rows
    assert [r["Cause Description"] for r in rs] == ["[name] slipped", text, "[name] slipped", text]
    assert [p["Cause Description"] for p in prov] == ["mod", "syn", "mod", "xw"]
    assert t.redacted["Cause Description"] == 2


def test_redaction_without_provenance_lists_name_and_contact_rows():
    cols = ["Incident Number", "Cause number", "Cause Description"]
    rs = [dict(zip(cols, ["I1", "2", "Mr. Smith rang 713-555-0142"])),
          dict(zip(cols, ["I1", "3", "nothing to hide"]))]
    t = _tbl("causes", cols, rs)
    got = bundle._redact_table(t, ["Cause Description"])
    assert [(g["row_key"], g["column"], g["kind"]) for g in got] == [
        ("I1|2", "Cause Description", "name"), ("I1|2", "Cause Description", "contact")]
    assert rs[0]["Cause Description"] == "[name] rang [contact]" and rs[1]["Cause Description"] == "nothing to hide"


# ---- the source list -------------------------------------------------------

def _bad_source_paths(paths):
    return [p for p in paths if "_pilot" in p or "_smoketest" in p]


def test_source_paths_cover_inputs_and_exclude_scratch_folders():
    paths = bundle.source_paths(bundle._specs(True))
    assert paths == sorted(set(paths))
    for co in COMPANIES:
        for n in ("incidents", "causes", "recommendations", "closeout", "provenance",
                  "causes_provenance", "recommendations_provenance", "closeout_provenance"):
            assert f"data/companies/{co}/{n}.csv" in paths, (co, n)
    for rel in ("real_only/incidents.csv", "enriched/incidents.csv", "enriched/provenance.csv",
                "enriched/causes_provenance.csv", "enriched/causes_confidence.csv",
                "cause_task.csv", "causes_source_field.csv", "bsee_unmapped.csv", "real_only/splits.json"):
        assert f"data/processed/e19/{rel}" in paths, rel
    for n in ("crosswalk", "e19_disposition", "synth_rules", "xw_cause_qualifiers",
              "xw_consequence_tiers", "xw_incident_type", "xw_outcome"):
        assert f"schema/{n}.yaml" in paths, n
    for rel in ("schema/data_dictionary_notes.yaml", "schema/bundle_exclusions.yaml",
                "docs/release/TERMS.md", "docs/release/PARTICIPANT_GUIDE.md"):
        assert rel in paths, rel
    assert _bad_source_paths(paths) == []
    assert not [p for p in bundle.source_paths(bundle._specs(False)) if p.startswith("data/companies/")]


def test_scratch_folder_check_can_fail():
    assert _bad_source_paths(["data/processed/e19/_pilot_haiku/incidents.csv",
                              "data/processed/e19/_smoketest/x.csv", "data/processed/e19/enriched/incidents.csv"]) == [
        "data/processed/e19/_pilot_haiku/incidents.csv", "data/processed/e19/_smoketest/x.csv"]

