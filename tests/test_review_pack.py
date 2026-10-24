"""Personal-info review pack: a fixture bundle built in tmp_path, never the real one."""
import csv
import hashlib
from pathlib import Path

import pytest

from psm import scenario
from psm.review_pack import COLUMNS, REASONS, build_review_pack

NARR = "What happened?  "
INC = ["Incident Number", NARR, "What was the outcome?", "Description"]
CO = scenario.COMPANY_ORDER[0]


def _w(path: Path, cols, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") for c in cols])


@pytest.fixture
def bundle(tmp_path):
    b = tmp_path / "bundle"
    _w(b / "core/real_only/incidents.csv", INC, [
        {"Incident Number": "A1", NARR: "Zork Quux was injured while rigging the load.",
         "What was the outcome?": "Treated on site."},
        {"Incident Number": "A2", NARR: "[name] was found unconscious near the tank.",
         "What was the outcome?": "Fine."},
        {"Incident Number": "A3", NARR: "The crew reported the worker died. Call [contact].",
         "What was the outcome?": "Near miss."},
        {"Incident Number": "A4", NARR: "", "What was the outcome?": ""},
        {"Incident Number": "A5", NARR: "Valve leaked; the IP was not hurt.",
         "What was the outcome?": "None."},
    ])
    _w(b / "core/cause_task.csv", ["Incident Number", "Cause number", "src_text_stripped"], [
        {"Incident Number": "A1", "Cause number": "1", "src_text_stripped": "Poor lifting plan."},
        {"Incident Number": "A2", "Cause number": "1", "src_text_stripped": "Lack of supervision."},
    ])
    _w(b / f"companies/{CO}/incidents.csv", INC, [
        {"Incident Number": "C1", NARR: "Pump tripped.", "What was the outcome?": "Restarted."},
        {"Incident Number": "C2", NARR: "Hose burst; no one hurt.", "What was the outcome?": "Replaced."},
    ])
    _w(b / "advanced/causes_confidence.csv", ["Incident Number", "Cause number", "note"], [
        {"Incident Number": "A1", "Cause number": "1", "note": "Zork Quux was injured"},
    ])
    _w(b / "core/content_flags.csv", ["Incident Number", "table", "row_key", "column", "flag"], [
        {"Incident Number": "A2", "table": "core/real_only/incidents.csv", "row_key": "A2",
         "column": NARR, "flag": "near_fatal"},
        {"Incident Number": "A2", "table": "core/real_only/incidents.csv", "row_key": "A2",
         "column": NARR, "flag": "name_redacted"},
        {"Incident Number": "A3", "table": "core/real_only/incidents.csv", "row_key": "A3",
         "column": NARR, "flag": "death_mention"},
        {"Incident Number": "A3", "table": "core/real_only/incidents.csv", "row_key": "A3",
         "column": NARR, "flag": "contact_redacted"},
        {"Incident Number": "A1", "table": "core/real_only/incidents.csv", "row_key": "A1",
         "column": NARR, "flag": "serious_injury"},
    ])
    return b


def _read(p):
    with p.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_deterministic(bundle, tmp_path):
    a = build_review_pack(bundle, n_random=3, out_path=tmp_path / "a.csv")
    b = build_review_pack(bundle, n_random=3, out_path=tmp_path / "b.csv")
    assert a.read_bytes() == b.read_bytes()


def test_columns_and_reasons(bundle, tmp_path):
    out = build_review_pack(bundle, n_random=3, out_path=tmp_path / "o.csv")
    with out.open(encoding="utf-8", newline="") as fh:
        assert next(csv.reader(fh)) == ["file", "Incident Number", "row_key", "column", "reason", "text"]
    assert COLUMNS == ["file", "Incident Number", "row_key", "column", "reason", "text"]
    assert len(REASONS) == 6
    assert {r["reason"] for r in _read(out)} <= set(REASONS)


def test_random_count_and_pool(bundle, tmp_path):
    # pool: 4 narrative cells in A1,A2,A3,A5 (x2 cols each, all non-empty) + C1,C2 (x2) + 2 cause_task
    rows = _read(build_review_pack(bundle, n_random=5, out_path=tmp_path / "o.csv"))
    rnd = [r for r in rows if r["reason"] == "random"]
    assert len(rnd) == 5
    big = _read(build_review_pack(bundle, n_random=10_000, out_path=tmp_path / "p.csv"))
    rnd = [r for r in big if r["reason"] == "random"]
    assert len(rnd) == 4 * 2 + 2 * 2 + 2          # every non-empty narrative cell
    files = {r["file"] for r in rnd}
    assert f"companies/{CO}/incidents.csv" in files           # company tables are in the pool
    assert "advanced/causes_confidence.csv" not in files      # non-narrative table is not
    assert all(r["text"] for r in rnd)


def test_random_follows_rank(bundle, tmp_path):
    rows = _read(build_review_pack(bundle, n_random=2, seed_text="s", out_path=tmp_path / "o.csv"))
    rnd = {(r["file"], r["row_key"], r["column"]) for r in rows if r["reason"] == "random"}
    big = _read(build_review_pack(bundle, n_random=10_000, seed_text="s", out_path=tmp_path / "p.csv"))
    pool = {(r["file"], r["row_key"], r["column"]) for r in big if r["reason"] == "random"}
    best = sorted(pool, key=lambda k: hashlib.sha256("|".join(("s",) + k).encode()).hexdigest())[:2]
    assert rnd == set(best)


def test_flag_reasons(bundle, tmp_path):
    rows = _read(build_review_pack(bundle, n_random=0, out_path=tmp_path / "o.csv"))
    got = {(r["reason"], r["row_key"]) for r in rows if r["reason"] != "capitalised_pair"}
    assert got == {("near_fatal", "A2"), ("name_redacted", "A2"),
                   ("death_mention", "A3"), ("contact_redacted", "A3")}   # serious_injury is not a reason
    a2 = [r for r in rows if r["row_key"] == "A2" and r["reason"] == "name_redacted"][0]
    assert a2["text"] == "[name] was found unconscious near the tank."     # as shipped
    assert a2["Incident Number"] == "A2" and a2["file"] == "core/real_only/incidents.csv"


def test_capitalised_pair_violation(bundle, tmp_path):
    rows = _read(build_review_pack(bundle, n_random=0, out_path=tmp_path / "o.csv"))
    cp = [r for r in rows if r["reason"] == "capitalised_pair"]
    assert ("core/real_only/incidents.csv", "A1") in {(r["file"], r["row_key"]) for r in cp}
    assert not any(r["file"].startswith("advanced/") for r in cp)


def test_capitalised_pair_rules(tmp_path):
    from psm.review_pack import capitalised_pair
    assert capitalised_pair("Zork Quux was injured.")
    assert capitalised_pair("The injured man was Zork Quux.")
    assert capitalised_pair("Zork Quux" + " " * 30 + "worker")                 # gap 30
    assert not capitalised_pair("Zork Quux" + " " * 45 + "worker")             # gap 45
    assert capitalised_pair("The IP was Zork Quux.")
    assert not capitalised_pair("The ip was Zork Quux.")                       # IP is case-sensitive
    assert capitalised_pair("Zork Quux was INJURED.")                          # others are not
    assert not capitalised_pair("Zork Quux was uninjured.")                    # word boundary
    assert not capitalised_pair("Zork Quux took the call.")                    # no trigger
    assert not capitalised_pair("zork quux was injured.")                      # no capitals


def test_sorted_output(bundle, tmp_path):
    rows = _read(build_review_pack(bundle, n_random=10_000, out_path=tmp_path / "o.csv"))
    keys = [(r["reason"], r["file"], r["row_key"], r["column"]) for r in rows]
    assert keys == sorted(keys)


def test_one_row_per_cell_and_reason(bundle, tmp_path):
    rows = _read(build_review_pack(bundle, n_random=10_000, out_path=tmp_path / "o.csv"))
    keys = [(r["reason"], r["file"], r["row_key"], r["column"]) for r in rows]
    assert len(keys) == len(set(keys))


def test_default_out_path_is_gitignored_deliverables():
    import inspect
    from psm import review_pack
    assert inspect.signature(review_pack.build_review_pack).parameters["out_path"].default is None
    assert review_pack.DEFAULT_OUT.as_posix().endswith("deliverables/release/review_sample.csv")
