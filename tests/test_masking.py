# tests/test_masking.py
"""Real operator names are masked as [company] in the company registers, and the
provenance tokens of donor-copied cells are carried honestly: xw stays xw, text
this project altered is mod, everything else keeps the donor's token."""
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from psm import scenario as sc
from psm.masking import (mask_operators, masked_strings, operator_pattern,
                         top_masked)

REPO = Path(__file__).resolve().parents[1]
E19 = REPO / "data" / "processed" / "e19"
COMPANIES = ("alder", "hazel", "hornbeam")


@pytest.fixture(scope="module")
def p():
    return operator_pattern()


def test_mask_full_name(p):
    assert mask_operators("Northstar Offshore Group, LLC crew lifted", p) == "[company] crew lifted"


def test_mask_longest_first(p):
    assert mask_operators("BP Exploration & Production Inc. rig", p) == "[company] rig"


def test_mask_short_form(p):
    assert mask_operators("the BP company man", p) == "the [company] company man"


def test_mask_suffix_forms_and_all_caps(p):
    assert mask_operators("Shell Offshore Inc. platform", p) == "[company] platform"
    assert mask_operators("CHEVRON U.S.A. INC. LEASE", p) == "[company] LEASE"
    assert mask_operators("Apache Corporation and Stone Energy Corporation", p) \
        == "[company] and [company]"
    assert mask_operators("W&T and W & T Offshore, Inc.", p) == "[company] and [company]"


@pytest.mark.parametrize("s,want", [
    ("Shell reported", "[company] reported"),
    ("Shell's crew", "[company]'s crew"),
    ("Apache failed to notify, Murphy and Cox did", "[company] failed to notify, [company] and [company] did"),
    ("Walter's approved procedure", "[company]'s approved procedure"),
    ("Chevron U.S.A. Inc. and Arena Offshore, LP, then Cox Operating, L.P. crew",
     "[company] and [company], then [company] crew"),
    ("Talos U.S.A. Inc. lease", "[company] lease"),
])
def test_mask_capitalized_ambiguous_operator_words(p, s, want):
    assert mask_operators(s, p) == want


@pytest.mark.parametrize("s", ["Noble Driller", "the Noble Don Taylor drillship", "Helix Q4000 vessel",
                               "SHELL AND TUBE", "stone and shell", "Taylor-made fix", "a hess"])
def test_mask_leaves_contractors_rigs_and_other_casings(p, s):
    assert mask_operators(s, p) == s


@pytest.mark.parametrize("s", ["shell and tube exchanger", "a stone fell", "noble gas", "the arena deck",
    "lost-time accident", "production platform", "X-ray", "not listed", "apache helicopter",
    "inter\xadnational", "coastal waters", "the bp reading was high"])
def test_mask_leaves_common_words(p, s):
    assert mask_operators(s, p) == s


def test_pattern_stable_across_hash_seeds():
    code = "from psm.masking import operator_pattern; print(operator_pattern().pattern)"
    outs = []
    for seed in ("1", "2"):
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True,
                           text=True, check=True, env={**__import__("os").environ,
                                                       "PYTHONHASHSEED": seed})
        outs.append(r.stdout)
    assert outs[0] == outs[1] and len(outs[0]) > 1000


# 12 full operator names exactly as bsee_operator spells them (pasted, not derived),
# plus the retired fictional-company name that collided with a real operator.
OPERATOR_NAMES = ("Shell Offshore Inc.", "Chevron U.S.A. Inc.", "Apache Corporation",
                  "Cox Operating, L.L.C.", "Fieldwood Energy LLC", "W & T Offshore, Inc.",
                  "Anadarko Petroleum Corporation", "Energy XXI GOM, LLC",
                  "Arena Offshore, LP", "Black Elk Energy", "Stone Energy Corporation",
                  "McMoRan Oil & Gas LLC", "northstar")


def _variants(name):
    n = name.lower()
    return {n, n.rstrip("."), n.replace(",", ""), n.replace(",", "").rstrip(".")}


def _company_text_cells():
    for c in COMPANIES:
        for table in ("incidents", "causes", "recommendations", "closeout"):
            with (REPO / "data" / "companies" / c / f"{table}.csv").open(
                    encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    for col, v in row.items():
                        if v:
                            yield c, table, row.get("Incident Number"), col, v


def test_company_text_has_no_operator_name():
    # independent of operator_pattern(): fixed names, plain substring search
    hits = []
    for c, table, inc, col, v in _company_text_cells():
        low = v.lower()
        for name in OPERATOR_NAMES:
            if any(x in low for x in _variants(name)):
                hits.append((c, table, inc, col.strip(), name))
    assert not hits, hits[:10]


@pytest.mark.parametrize("company", COMPANIES)
def test_generated_text_has_no_operator_name(company):
    # same independent name list, applied to the generator output itself, so the
    # check cannot pass just because data/companies was last regenerated by a good run
    res = sc.generate(company)
    hits = []
    for table, (cols, rows, _prov) in res["tables"].items():
        for row in rows:
            for col, v in row.items():
                low = (v or "").lower()
                for name in OPERATOR_NAMES:
                    if any(x in low for x in _variants(name)):
                        hits.append((table, row["Incident Number"], col.strip(), name))
    assert not hits, hits[:10]


def _read(path):
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_enriched_provenance_is_row_aligned_with_the_value_files():
    # the generator reads donor tokens by row index; prove the index is sound
    for t, prov in (("incidents", "provenance"), ("causes", "causes_provenance")):
        real, enr, pv = (_read(E19 / "real_only" / f"{t}.csv"),
                         _read(E19 / "enriched" / f"{t}.csv"),
                         _read(E19 / "enriched" / f"{prov}.csv"))
        assert len(real) == len(enr) == len(pv)
        for r, e in zip(real, enr):
            assert r["Incident Number"] == e["Incident Number"]
            for col, v in r.items():
                if v.strip():
                    assert e[col] == v, (t, r["Incident Number"], col)


def _donor_tokens_independent():
    """incident number -> {col: token}, and (incident, cause number) -> {col: token},
    read straight from the files without scenario.py."""
    inc = {}
    for r, pv in zip(_read(E19 / "real_only" / "incidents.csv"),
                     _read(E19 / "enriched" / "provenance.csv")):
        inc[r["Incident Number"]] = (r, pv)
    cau = {}
    for r, pv in zip(_read(E19 / "real_only" / "causes.csv"),
                     _read(E19 / "enriched" / "causes_provenance.csv")):
        cau[(r["Incident Number"], r["Cause number"])] = (r, pv)
    return inc, cau


def _rows_by_company(company):
    res = sc.generate(company)
    return res, {r["Incident Number"]: (r, pv) for r, pv in
                 zip(res["tables"]["incidents"][1], res["tables"]["incidents"][2])}


DONOR_COPIED_INCIDENT_COLS = sc._FREE_TEXT + sc._VERBATIM


@pytest.mark.parametrize("company", COMPANIES)
def test_mod_iff_src_text_changed(company):
    inc, cau = _donor_tokens_independent()
    res = sc.generate(company)
    seen_mod = seen_src = 0
    cols, rows, provs = res["tables"]["incidents"]
    plan_by_sid = {p.sid: p for p in res["plans"]}
    for row, pv in zip(rows, provs):
        donor_row, donor_pv = inc[plan_by_sid[row["Incident Number"]].donor_id]
        for c in DONOR_COPIED_INCIDENT_COLS:
            if not row[c].strip():
                assert pv[c] == ""
                continue
            if donor_pv[c] == "src" and c in sc._FREE_TEXT:
                changed = row[c] != donor_row[c]
                assert pv[c] == ("mod" if changed else "src"), (row["Incident Number"], c)
                seen_mod += changed
                seen_src += not changed
            else:
                assert row[c] == donor_row[c] and pv[c] == donor_pv[c], c
    _, crows, cprovs = res["tables"]["causes"]
    for row, pv in zip(crows, cprovs):
        donor_id = plan_by_sid[row["Incident Number"]].donor_id
        donors = sorted((k for k in cau if k[0] == donor_id), key=lambda k: int(k[1]))
        if donors:
            # the n-th generated cause copies donor cause n (mod the donor's count),
            # matched on the donor's Cause number
            drow, dpv = cau[donors[(int(row["Cause number"]) - 1) % len(donors)]]
            donor_text, donor_tok = drow["Cause Description"], dpv["Cause Description"]
        else:
            drow, dpv = inc[donor_id]
            donor_text, donor_tok = drow["Description"], dpv["Description"]
        assert donor_tok == "src"
        changed = row["Cause Description"] != donor_text
        assert pv["Cause Description"] == ("mod" if changed else "src")
        seen_mod += changed
        seen_src += not changed
    assert seen_mod and seen_src, "the corpus must exercise both outcomes"


@pytest.mark.parametrize("company", COMPANIES)
def test_donor_xw_cells_stay_xw(company):
    inc, cau = _donor_tokens_independent()
    res = sc.generate(company)
    plan_by_sid = {p.sid: p for p in res["plans"]}
    n_xw = 0
    _, rows, provs = res["tables"]["incidents"]
    for row, pv in zip(rows, provs):
        donor_row, donor_pv = inc[plan_by_sid[row["Incident Number"]].donor_id]
        for c in DONOR_COPIED_INCIDENT_COLS:
            if donor_pv[c] == "xw" and row[c].strip():
                assert pv[c] == "xw" and row[c] == donor_row[c], c
                n_xw += 1
    _, crows, cprovs = res["tables"]["causes"]
    xw_cols = (" Failed PSM Framework Element", "Risk Management Cause", "Human Factors  Cause")
    for row, pv in zip(crows, cprovs):
        overridden = (plan_by_sid[row["Incident Number"]].element_override
                      and row["Cause number"] == "1")
        for c in xw_cols:
            if not row[c].strip() or (overridden and c == xw_cols[0]):
                continue
            assert pv[c] in ("xw", "src"), c
            n_xw += pv[c] == "xw"
    assert n_xw > 0


def test_element_override_is_the_one_syn_over_xw():
    res = sc.generate("hazel")
    plans = {p.sid: p for p in res["plans"]}
    _, crows, cprovs = res["tables"]["causes"]
    overridden = [(r, pv) for r, pv in zip(crows, cprovs)
                  if plans[r["Incident Number"]].element_override and r["Cause number"] == "1"]
    assert overridden
    for r, pv in overridden:
        assert pv[" Failed PSM Framework Element"] == "syn"


def test_about_legend_covers_tokens():
    from psm.export_companies import ABOUT_TEMPLATE, COMPANY_LABELS
    text = "\n".join(ABOUT_TEMPLATE).lower()
    legend = {"src": "no colour", "xw": "blue", "syn": "grey", "key": "green", "mod": "peach"}
    for c in COMPANIES:
        tokens = set()
        for f in ("provenance.csv", "causes_provenance.csv",
                  "recommendations_provenance.csv", "closeout_provenance.csv"):
            for row in _read(REPO / "data" / "companies" / c / f):
                tokens |= set(row.values())
        tokens.discard("")
        assert tokens <= set(legend), (c, tokens - set(legend))
        for t in tokens:
            assert legend[t] in text, (c, t)
    assert "names the real operators" not in text
    assert "masked as [company]" in "\n".join(ABOUT_TEMPLATE)


def test_top_masked_strings_reported(p):
    texts = [v for c, t, i, col, v in _company_text_cells()]
    # masking is applied, so the shipped text has nothing left to report ...
    assert not masked_strings(texts, p)
    # ... the donor text it came from is what the owner reviews
    donor = [r[c] for r in sc.donor_incidents().values() for c in sc._FREE_TEXT]
    donor += [r["Cause Description"] for rows in sc.donor_causes().values() for r in rows]
    top = top_masked(donor, p, 20)
    assert top and len(top) <= 20
    common = {"noble", "production", "offshore",
              "company", "energy", "oil", "gas", "not listed", "coastal"}
    assert not [s for s, _ in top if s.lower() in common]
    assert [n for _, n in top] == sorted((n for _, n in top), reverse=True)
    # The counter keeps case. An ambiguous word may be masked only as written with a
    # capital (Shell) or with a corporate suffix, never as bare lowercase (shell, stone).
    ambiguous = {"shell", "apache", "stone", "cox", "arena", "hess", "murphy", "walter",
                 "devon", "noble", "taylor", "helix", "mariner", "cobalt", "nippon",
                 "paloma", "panther", "marlin"}
    bare_lower = [s for s in masked_strings(donor, p) if s in ambiguous]
    assert not bare_lower, bare_lower


# -- word boundary after the corporate tail (F2) and fragments it should consume (F5)

@pytest.mark.parametrize("s,want", [
    ("McMoRan Incident Investigation", "[company] Incident Investigation"),
    ("BP Corporate office", "[company] Corporate office"),
    ("Talos LPG tank", "[company] LPG tank"),
    ("Shell Offshore Incorporated", "[company]"),
    ("Talos Energy, LLC reported", "[company] reported"),
    ("Black Elk Energy Offshore Operations, LLC", "[company]"),
    ("Murphy Exploration & Production Company - USA", "[company]"),
    ("shell offshore inc reported", "[company] reported"),
    ("Talos LP. Then", "[company]. Then"),
])
def test_corporate_tail_stops_at_a_word_boundary(p, s, want):
    assert mask_operators(s, p) == want


# -- short forms (F1)

@pytest.mark.parametrize("s,want", [
    ("Cantium personnel", "[company] personnel"),
    ("ExxonMobil and Exxon", "[company] and [company]"),
    ("the El Paso platform", "the [company] platform"),
    ("Kosmos, Marubeni and EnVen", "[company], [company] and [company]"),
    ("Mariner Energy LLC", "[company]"),
    ("Mariner crew", "[company] crew"),
    ("EPL staff", "[company] staff"),
    ("Panther Operating Company, LLC crew", "[company] crew"),
    ("Panther felt the swell", "[company] felt the swell"),
])
def test_mask_short_forms_of_real_operators(p, s, want):
    assert mask_operators(s, p) == want


@pytest.mark.parametrize("s", ["the mariner on watch", "cobalt blue", "a nippon import", "marlin fishing",
                               "a panther", "the paloma", "epl", "the Marlin platform", "high pressure line", "Union Hall",
                               "Wild crew", "Fairways narrow"])
def test_mask_leaves_common_words_and_contractors_alone(p, s):
    assert mask_operators(s, p) == s


# -- independent scan for first tokens (F1). Does NOT use operator_pattern() or any
# psm.masking helper: its own simple rule on the scrubbed bsee_operator column.

# First tokens that may legitimately appear capitalized in company text, with the reason.
FIRST_TOKEN_ALLOW = {
    "high",       # High Island (lease area), "High pressure": common word, owner-reviewed exclusion
    "union", "hydro", "probe", "palm",    # lowercase outnumbers capitalized in the corpus
    "fairways",   # nautical noun
    "wild",       # Wild Well Control, a contractor (contractors stay named)
    "black",      # a colour; "Black Elk" itself is masked by its own alias
    "noble", "murphy", "exxon", "pioneer", "pacific", "total", "white", "badger",
                  # also first tokens of contractors, kept named by owner decision
    "taylor", "helix",    # rigs / vessels; masked only with a corporate suffix
    "marlin",     # a platform name: the verbatim Unit column of one incident (not free text)
    "forest", "plains", "merit", "guardian", "magnum", "manta",    # ordinary words; only a hit if the text uses them capitalized
}
_LOCAL_STOP = {"gulf", "company", "corporation", "energy", "offshore", "exploration",
               "production", "petroleum", "resources", "operating", "oil", "gas", "united",
               "royal", "international", "marine", "services", "systems"}


def _first_tokens():
    contractor, first = set(), set()
    with (E19 / "bsee_unmapped.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            for i, part in enumerate((row["bsee_operator"] or "").split("CONTRACTOR:")):
                toks = part.replace("\xad", "").replace("\x01", "").split()
                if not toks:
                    continue
                tok = toks[0].strip(",.()")
                if i:
                    contractor.add(tok.lower())
                elif tok.isascii() and tok.isalpha() and len(tok) >= 4:
                    first.add(tok.lower())
    return sorted(first - contractor - _LOCAL_STOP)


def _capitalized_first_token_hits(cells):
    rx = {t: re.compile(rf"(?<!\w)(?:{t.capitalize()}|{t.upper()})(?!\w)")
          for t in _first_tokens() if t not in FIRST_TOKEN_ALLOW}
    return sorted({(where, t) for where, v in cells for t, r in rx.items() if r.search(v)})


def test_first_tokens_scan_is_not_vacuous():
    toks = _first_tokens()
    assert len(toks) > 60
    for must in ("cantium", "mariner", "cobalt", "marubeni", "kosmos", "statoil", "sanare"):
        assert must in toks


def test_company_text_has_no_capitalized_operator_first_token():
    cells = [((c, t, i, col.strip()), v) for c, t, i, col, v in _company_text_cells()]
    hits = _capitalized_first_token_hits(cells)
    assert not hits, hits[:10]


@pytest.mark.parametrize("company", COMPANIES)
def test_generated_text_has_no_capitalized_operator_first_token(company):
    res = sc.generate(company)
    cells = [((table, row["Incident Number"], col.strip()), v or "")
             for table, (cols, rows, _prov) in res["tables"].items()
             for row in rows for col, v in row.items()]
    hits = _capitalized_first_token_hits(cells)
    assert not hits, hits[:10]
