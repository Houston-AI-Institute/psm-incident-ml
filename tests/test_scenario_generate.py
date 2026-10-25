# tests/test_scenario_generate.py
import json
from datetime import date
from pathlib import Path

import pytest

from psm import scenario as sc
from psm.kpi import load_company
from psm.templates import classify_action

ROOT = Path(__file__).resolve().parents[1] / "data" / "companies"


def test_generate_alder_shape_and_closed_provenance():
    res = sc.generate("alder")
    t = res["tables"]
    assert set(t) == {"incidents", "causes", "recommendations", "closeout"}
    cols, rows, prov = t["incidents"]
    assert len(rows) == 150 and len(prov) == 150
    for name in t:
        _, rws, prv = t[name]
        assert len(rws) == len(prv)
        for p in prv:
            assert set(p.values()) <= {"", "src", "xw", "mod", "syn", "key"}, name


def test_every_rec_has_a_closeout_row_and_registry_text():
    res = sc.generate("alder")
    _, recs, _ = res["tables"]["recommendations"]
    _, close, _ = res["tables"]["closeout"]
    assert len(recs) == len(close)
    keys = {(r["Incident Number"], r["Recommendation Number"]) for r in recs}
    assert {(c["Incident Number"], c["Recommendation Number"])
            for c in close} == keys
    for r in recs:
        classify_action(r["Recommendation Description"])   # KeyError = failure
    for c in close:
        assert c["Schedule Status"] in STATUSES
        if c["Date Completed"]:
            date.fromisoformat(c["Date Completed"])


# Schedule Status values verbatim from schema/e19_labels.yaml ("Deleted" is never generated)
STATUSES = ("Completed", "On Schedule", "Behind Original Agreed Schedule")


def _all_tables(company):
    return {n: rows for n, (_, rows, _) in sc.generate(company)["tables"].items()}


@pytest.mark.parametrize("company", sc.COMPANY_ORDER)
def test_no_generated_date_after_as_of(company):
    """The registers are extracted on AS_OF: only a due date may lie after it."""
    t = _all_tables(company)
    date_cols = {"incidents": ("Date of Incident", "Date of Report", "Approval Date",
                               "Close out Date"),
                 "closeout": ("Date Completed",)}
    for table, cols in date_cols.items():
        for r in t[table]:
            for c in cols:
                if r[c]:
                    assert date.fromisoformat(r[c]) <= sc.AS_OF, (table, c, r[c])


@pytest.mark.parametrize("company", sc.COMPANY_ORDER)
def test_schedule_status_follows_as_of(company):
    t = _all_tables(company)
    agreed = {(r["Incident Number"], r["Recommendation Number"]):
              date.fromisoformat(r["Agreed Completion Date"]) for r in t["recommendations"]}
    for c in t["closeout"]:
        a = agreed[(c["Incident Number"], c["Recommendation Number"])]
        if c["Date Completed"]:
            assert c["Schedule Status"] == "Completed"
        else:
            assert c["Schedule Status"] == ("Behind Original Agreed Schedule"
                                            if a < sc.AS_OF else "On Schedule")


@pytest.mark.parametrize("company", sc.COMPANY_ORDER)
def test_open_action_means_no_close_out(company):
    t = _all_tables(company)
    open_ids = {c["Incident Number"] for c in t["closeout"] if not c["Date Completed"]}
    for r in t["incidents"]:
        if r["Incident Number"] in open_ids:
            assert r["Close out Date"] == ""
            assert r["Close out Approval - Name"] == ""
            assert r["Close out Approval - Position"] == ""


@pytest.mark.parametrize("company", sc.COMPANY_ORDER)
def test_unknown_approval_means_no_approver(company):
    for r in _all_tables(company)["incidents"]:
        if not r["Approval Date"]:
            assert r["Investigation Acceptor/Approver (Owner) - Name"] == ""
            assert r["Investigation Acceptor/Approver (Owner)- Position"] == ""   # sic: no space


@pytest.mark.parametrize("company", ("hazel", "hornbeam"))
def test_planted_pairs_first_member_is_closed_by_as_of(company):
    res = sc.generate(company)
    by_id = {r["Incident Number"]: r for r in res["tables"]["incidents"][1]}
    for a, _ in res["planted_pairs"]:
        assert by_id[a]["Close out Date"], a


def test_hazel_plants_eight_maintenance_pairs_and_detector_finds_them():
    res = sc.generate("hazel")
    pairs = res["planted_pairs"]
    assert len(pairs) == 8
    _, incs, _ = res["tables"]["incidents"]
    _, causes, _ = res["tables"]["causes"]
    by_id = {r["Incident Number"]: r for r in incs}
    for a, b in pairs:
        assert by_id[a]["Work Group"] == by_id[b]["Work Group"] == "Maintenance"
    detected = set(map(tuple, sc.detect_recurrence_pairs(incs, causes, 365)))
    assert set(map(tuple, pairs)) <= detected


def test_hornbeam_planted_recurrence_pairs_are_a_subset_of_detected():
    """Mirrors the hazel detector test above for Hornbeam (its `>= 6`
    margin over the coincidence bound of 5 had no equivalent planted-vs-
    detected check). Reads the committed manifest's documented plant rather
    than a fresh sc.generate() call, so this validates what the manifest
    actually claims against the committed register."""
    manifest = json.loads((ROOT / "hornbeam" / "manifest.json")
                          .read_text(encoding="utf-8"))
    recurrence_plant = next(p for p in manifest["plants"]
                            if p["kpi"] == "recurrence_rate")
    planted = {tuple(pair) for pair in recurrence_plant["affected_ids"]}
    tables = load_company(ROOT / "hornbeam")
    detected = set(map(tuple,
                       sc.detect_recurrence_pairs(tables["incidents"],
                                                   tables["causes"], 365)))
    assert planted <= detected


def test_planted_pair_ordering_invariants():
    res = sc.generate("hazel")
    _, incs, _ = res["tables"]["incidents"]
    by_id = {r["Incident Number"]: r for r in incs}
    for a, b in res["planted_pairs"]:
        doi_a = date.fromisoformat(by_id[a]["Date of Incident"])
        doi_b = date.fromisoformat(by_id[b]["Date of Incident"])
        close_a = date.fromisoformat(by_id[a]["Close out Date"])
        assert doi_b > close_a
        assert (doi_b - doi_a).days <= 365
        assert sc.WINDOW_START <= doi_b <= sc.WINDOW_END


def test_generate_is_deterministic_in_process():
    a = sc.generate("alder")
    b = sc.generate("alder")
    assert a["tables"] == b["tables"]


def test_element_override_touches_only_the_first_cause_row():
    res = sc.generate("hazel")
    planted = {s for pair in res["planted_pairs"] for s in pair}
    _, causes, cprov = res["tables"]["causes"]
    for r, pr in zip(causes, cprov):
        if r["Incident Number"] in planted and r["Cause number"] != "1":
            # rows past the first keep their donor-sourced element untouched
            assert pr[" Failed PSM Framework Element"] in ("", "xw")
