"""Tests for the leak-free cause-classification task file.

The cause statement text opens with the category (and usually the subcategory)
that *is* the label, so a model can read the answer off the first words.
`strip_category` removes both; `build_cause_task` assembles the task CSV.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest
import yaml

from psm.cause_task import TASK_COLUMNS, build_cause_task, write_task
from psm.causes import normalise_category, parse_statement, strip_category, subcategory_stems

REPO = Path(__file__).resolve().parents[1]
REAL_ONLY = REPO / "data" / "processed" / "e19" / "real_only"
TASK_CSV = REPO / "data" / "processed" / "e19" / "cause_task.csv"
CROSSWALK = REPO / "schema" / "crosswalk.yaml"

_XW = yaml.safe_load(CROSSWALK.read_text(encoding="utf-8"))
CATS = tuple(sorted(
    {c.lower() for c in _XW["categories"]} | {a.lower() for a in _XW["aliases"]},
    key=lambda s: (-len(s), s)))

# Measured by running `uv run python -m psm.cause_task`; see docs/findings.md.
N_TASK = 406


@pytest.fixture(scope="module")
def task():
    return build_cause_task(REAL_ONLY)


@pytest.fixture(scope="module")
def subcats():
    """Parsed subcategories seen at least twice across real_only/causes.csv."""
    with (REAL_ONLY / "causes.csv").open(encoding="utf-8", newline="") as fh:
        counts = Counter()
        for r in csv.DictReader(fh):
            sub = parse_statement(r["Cause Description"]).subcategory
            if sub:
                counts[sub.lower()] += 1
    return {s for s, n in counts.items() if n >= 2}


# ---- strip_category -------------------------------------------------------

def test_strip_examples():
    assert strip_category("Equipment failure – Crane – hydraulic hose burst") == "hydraulic hose burst"
    assert strip_category("Communication: Inadequate job instructions provided.") == ""
    assert strip_category("plain prose with no head") == "plain prose with no head"
    # long first sentence is description, not subcategory (GC-478-20240502-1620 #1 pattern)
    assert "sprag clutches" in strip_category(
        "Equipment Failure – The structural limits of the sprag clutches were exceeded causing them to shear. "
        "This led to the uncontrolled descent of the water bag.")


def test_strip_removes_short_subcategory_even_when_seen_once():
    # <= 6 words counts as a subcategory regardless of frequency.
    out = strip_category("Management Systems: Inadequate hazards analysis. The JSEA missed the hot work.")
    assert out == "The JSEA missed the hot work"


def test_strip_keeps_long_unrepeated_span_as_description():
    long_span = "The crew continued lifting although the sling showed visible wear and fraying"
    out = strip_category(f"Human Error - {long_span}. Nobody stopped the job.")
    assert long_span in out


def test_strip_removes_long_subcategory_when_repeated():
    long_sub = "Inadequate communication between the crane operator and the signalman"   # 9 words
    counts = {long_sub.lower(): 2}
    out = strip_category(f"Communication: {long_sub}. Radio was out of range.", sub_counts=counts)
    assert out == "Radio was out of range"
    # ...but not when it was seen once.
    kept = strip_category(f"Communication: {long_sub}. Radio was out of range.", sub_counts={long_sub.lower(): 1})
    assert long_sub in kept


def test_strip_removes_long_once_seen_span_with_no_description():
    # Head plus one long sentence and nothing after it: the whole statement is
    # the subcategory (the 55 "long, once-seen, no description" labelled rows).
    assert strip_category("Communication: Inadequate communication between the operator "
                          "and the contractor personnel on deck.", sub_counts={}) == ""


def test_stem_shared_by_two_subcategories_is_stripped():
    counts = {"alpha beta gamma one": 1, "alpha beta gamma two": 1}
    assert subcategory_stems(counts) >= {"alpha beta", "alpha beta gamma"}
    assert "alpha" not in subcategory_stems(counts)
    raw = ("Communication - alpha beta gamma three delta epsilon zeta. "
           "The crew did not confirm the lift.")
    out = strip_category(raw, sub_counts=counts)
    assert not out.lower().startswith("alpha")
    assert out.startswith("three delta epsilon zeta")
    # A stem seen under only one subcategory is left alone.
    solo = strip_category(raw, sub_counts={"alpha beta gamma one": 1})
    assert solo.lower().startswith("alpha beta gamma three")


def test_strip_handles_bullet_and_leading_separators():
    assert strip_category("• Equipment Failure – Inadequate Equipment Inspection: visual only.") == "visual only"
    assert strip_category("Supervision - - : the lead did not stop work") == "the lead did not stop work"


# ---- task file ------------------------------------------------------------

def test_columns(task):
    assert list(task[0]) == ["Incident Number", "Cause number", "src_year", "src_category",
                             "src_text_stripped", "xw_element", "syn_regime", "syn_fold"]
    assert list(task[0]) == list(TASK_COLUMNS)


def test_category_relabel_is_one_to_one(task):
    m = defaultdict(set)
    for r in task:
        m[r["src_category"]].add(r["xw_element"])
    assert all(len(v) == 1 for v in m.values())


def test_xw_element_comes_from_the_crosswalk_yaml(task):
    aliases = {k.lower(): v for k, v in _XW["aliases"].items()}
    for r in task:
        canon = aliases[r["src_category"]]
        assert r["xw_element"] == str(_XW["categories"][canon]["primary_element"])


def test_no_row_starts_with_category_or_subcategory(task, subcats):   # subcats: parsed, seen >= 2
    for r in task:
        t = r["src_text_stripped"].lstrip(":–—- ").lower()
        assert not t.startswith(CATS), r["Incident Number"]
        assert not t.startswith(tuple(s.lower() for s in subcats)), r["Incident Number"]


def _lookup_hits(rows):
    """Out-of-fold sentinel: learn {first 3 words -> modal xw_element} on four
    folds, apply to the fifth; count rows where the lookup fires AND is right."""
    hits = 0
    for fold in "01234":
        votes = defaultdict(Counter)
        for r in rows:
            if r["syn_fold"] != fold:
                votes[tuple(r["src_text_stripped"].lower().split()[:3])][r["xw_element"]] += 1
        for r in rows:
            if r["syn_fold"] == fold:
                v = votes.get(tuple(r["src_text_stripped"].lower().split()[:3]))
                if v and v.most_common(1)[0][0] == r["xw_element"]:
                    hits += 1
    return hits


def test_subcategory_lookup_sentinel(task):
    hits = _lookup_hits(task)
    assert hits <= 0.02 * len(task), (hits, len(task))


def test_sentinel_catches_a_planted_subcategory_leak(task):
    """A sentinel that cannot fail proves nothing: leave the subcategory head on
    the text (one phrase per class) and it must trip."""
    head = {el: f"Inadequate {el}x process {el}y" for el in {r["xw_element"] for r in task}}
    leaky = [dict(r, src_text_stripped=head[r["xw_element"]] + " " + r["src_text_stripped"]) for r in task]
    assert _lookup_hits(leaky) > 0.02 * len(leaky)


def test_folds_grouped_and_populated(task):
    by_inc = defaultdict(set)
    for r in task:
        by_inc[r["Incident Number"]].add(r["syn_fold"])
    assert all(len(f) == 1 for f in by_inc.values())
    m6 = [r for r in task if r["syn_regime"] == "modern_six"]
    cells = Counter((r["syn_fold"], r["xw_element"]) for r in m6)
    for fold in "01234":
        for el in {r["xw_element"] for r in m6}:
            # >= 1, not >= 3: elements 6 and 9 are sparse in modern_six (28 and
            # 31 rows over five folds), so a floor of 3 per fold is not reachable
            # without changing the salt. Every fold still sees every class.
            assert cells[(fold, el)] >= 1, (fold, el)


def test_fold_is_the_documented_hash(task):
    for r in task:
        h = hashlib.sha256(f"{r['Incident Number']}|cause-task-v1".encode()).hexdigest()
        assert r["syn_fold"] == str(int(h[:8], 16) % 5)


def test_regime_comes_from_splits_json(task):
    ids = json.loads((REAL_ONLY / "splits.json").read_text(encoding="utf-8"))["incident_ids"]
    inv = {i: reg for reg, lst in ids.items() for i in lst}
    for r in task:
        assert r["syn_regime"] == inv.get(r["Incident Number"], "")
    assert {r["syn_regime"] for r in task} <= set(ids) | {""}


def test_every_row_is_a_labelled_cause_and_long_enough(task):
    with (REAL_ONLY / "causes.csv").open(encoding="utf-8", newline="") as fh:
        labelled = {(r["Incident Number"], r["Cause number"]) for r in csv.DictReader(fh)
                    if r[" Failed PSM Framework Element"].strip()}
    keys = [(r["Incident Number"], r["Cause number"]) for r in task]
    assert len(keys) == len(set(keys))
    assert set(keys) <= labelled
    assert all(len(r["src_text_stripped"].split()) >= 3 for r in task)


def test_committed_csv_matches_a_fresh_build(task, tmp_path):
    out = tmp_path / "cause_task.csv"
    write_task(task, out)
    assert out.read_bytes() == TASK_CSV.read_bytes()


def test_row_count_pinned(task):
    assert len(task) == N_TASK   # N_TASK: measured in Step 3, written into the test, stated in report


# ---- exclusions on a synthetic real_only dir (deliberate violations) -------

def _mini_real_only(tmp_path, causes):
    cols = ["Incident Number", "Cause number", "Cause Description", "Cause type",
            "Risk Management Cause", "Human Factors  Cause", " Failed PSM Framework Element"]
    d = tmp_path / "real_only"
    d.mkdir()
    with (d / "causes.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for i, (text, el) in enumerate(causes, 1):
            w.writerow({"Incident Number": "XX-1-20240101-0000", "Cause number": str(i),
                        "Cause Description": text, " Failed PSM Framework Element": el})
    with (d / "incidents.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["Incident Number", "Date of Incident"])
        w.writeheader()
        w.writerow({"Incident Number": "XX-1-20240101-0000", "Date of Incident": "2024-01-01"})
    (d / "splits.json").write_text(json.dumps(
        {"incident_ids": {"modern_six": ["XX-1-20240101-0000"]}}), encoding="utf-8")
    return d


def test_short_and_multi_category_rows_are_excluded(tmp_path):
    d = _mini_real_only(tmp_path, [
        ("Equipment Failure – The hose burst under load and sprayed the deck. The crew had not inspected it", "15"),
        ("Equipment Failure – The hose burst under load and sprayed the deck", "15"),                  # no description after a long span: the subcategory
        ("Communication: Inadequate job instructions provided.", "9"),                 # empty after strip
        ("Equipment Failure – Crane – hose burst", "15"),                              # 2 words
        ("Supervision – The lead missed the hazard. Work environment – Rain made the deck slick", "17"),
        ("The crew did the job without a permit and nobody noticed", ""),              # unlabelled
    ])
    rows = build_cause_task(d)
    assert [r["Cause number"] for r in rows] == ["1"]
    assert rows[0]["src_category"] == "equipment failure"
    assert rows[0]["xw_element"] == "15"
    assert rows[0]["src_year"] == "2024"
    assert rows[0]["syn_regime"] == "modern_six"
