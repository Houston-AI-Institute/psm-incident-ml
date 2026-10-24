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
    want = ["file", "Incident Number", "row_key", "column", "reason", "text", "n_cells"]
    with out.open(encoding="utf-8", newline="") as fh:
        assert next(csv.reader(fh)) == want
    assert COLUMNS == want
    assert len(REASONS) == 7
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
    got = {(r["reason"], r["row_key"]) for r in rows if not r["reason"].startswith("capitalised_pair")}
    assert got == {("near_fatal", "A2"), ("name_redacted", "A2"),
                   ("death_mention", "A3"), ("contact_redacted", "A3")}   # serious_injury is not a reason
    a2 = [r for r in rows if r["row_key"] == "A2" and r["reason"] == "name_redacted"][0]
    assert a2["text"] == "[name] was found unconscious near the tank."     # as shipped
    assert a2["Incident Number"] == "A2" and a2["file"] == "core/real_only/incidents.csv"
    assert a2["n_cells"] == ""


def test_capitalised_pair_violation(bundle, tmp_path):
    rows = _read(build_review_pack(bundle, n_random=0, out_path=tmp_path / "o.csv"))
    cp = [r for r in rows if r["reason"] == "capitalised_pair"]
    zork = [r for r in cp if "Zork Quux" in r["text"]]
    assert len(zork) == 1
    z = zork[0]
    assert (z["file"], z["row_key"], z["column"]) == ("core/real_only/incidents.csv", "A1", NARR)
    assert z["n_cells"] == "1" and z["Incident Number"] == "A1"
    assert not any(r["file"].startswith("advanced/") for r in rows if r["reason"].startswith("capitalised_pair"))


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


def _mini(tmp_path, cells, flags=True):
    b = tmp_path / "mini"
    _w(b / "core/real_only/incidents.csv", ["Incident Number", NARR],
       [{"Incident Number": k, NARR: v} for k, v in cells.items()])
    if flags:
        _w(b / "core/content_flags.csv", ["Incident Number", "table", "row_key", "column", "flag"], [])
    return b


def _pairs(b, tmp_path, name="o.csv"):
    rows = _read(build_review_pack(b, n_random=0, out_path=tmp_path / name))
    return [r for r in rows if r["reason"].startswith("capitalised_pair")]


def test_common_pair_goes_to_its_own_reason(tmp_path):
    b = _mini(tmp_path, {"A1": "Hit by a Crane Operator who was injured.",
                         "A2": "Then the crane operator waved."})
    rows = _pairs(b, tmp_path)
    assert [(r["reason"], r["n_cells"]) for r in rows] == [("capitalised_pair_common", "1")]
    assert "Crane Operator" in rows[0]["text"]


def test_pair_in_two_cells_is_one_row(tmp_path):
    b = _mini(tmp_path, {"B2": "Zork Quux was injured again.", "B1": "Zork Quux was injured."})
    rows = _pairs(b, tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["reason"] == "capitalised_pair" and r["n_cells"] == "2"
    assert r["row_key"] == "B1" and r["Incident Number"] == "B1"      # first in sort order


def test_pair_row_text_is_a_60_char_window(tmp_path):
    text = "x" * 100 + " Zork Quux was injured " + "y" * 100
    b = _mini(tmp_path, {"C1": text})
    (r,) = _pairs(b, tmp_path)
    s = text.index("Zork Quux")
    assert r["text"] == text[s - 60: s + len("Zork Quux") + 60]
    short = _mini(tmp_path / "s", {"C1": "Zork Quux was injured.\nNext line."})
    (r,) = _pairs(short, tmp_path / "s", "p.csv")
    assert r["text"] == "Zork Quux was injured.\nNext line."      # cut at cell bounds, newline kept


def test_distinct_pairs_in_one_cell_each_get_a_row(tmp_path):
    b = _mini(tmp_path, {"D1": "Zork Quux and Blarg Fnord were injured."})
    rows = _pairs(b, tmp_path)
    assert len(rows) == 2 and {r["n_cells"] for r in rows} == {"1"}


def test_one_row_per_cell_and_reason(bundle, tmp_path):
    rows = _read(build_review_pack(bundle, n_random=10_000, out_path=tmp_path / "o.csv"))
    keys = [(r["reason"], r["file"], r["row_key"], r["column"]) for r in rows
            if not r["reason"].startswith("capitalised_pair")]
    assert len(keys) == len(set(keys))


def test_default_out_path_is_gitignored_deliverables():
    import inspect
    from psm import review_pack
    assert inspect.signature(review_pack.build_review_pack).parameters["out_path"].default is None
    assert review_pack.DEFAULT_OUT.as_posix().endswith("deliverables/release/review_sample.csv")


def test_overlapping_pairs_are_all_considered(tmp_path):
    # "Then Zork" would absorb "Zork" under left-to-right matching and hide "Zork Quux".
    b = _mini(tmp_path, {"E1": "Then Zork Quux was injured.", "E2": "Zork Quux was injured."})
    rows = _pairs(b, tmp_path)
    by_n = {r["n_cells"] for r in rows}
    assert len(rows) == 2 and by_n == {"1", "2"}               # "Then Zork" x1 and "Zork Quux" x2
    assert [r for r in rows if r["n_cells"] == "2"][0]["reason"] == "capitalised_pair"


def test_empty_pool_fails_loud(tmp_path):
    b = tmp_path / "empty"
    _w(b / "core/real_only/incidents.csv", ["Incident Number"], [{"Incident Number": "A1"}])  # text-light
    _w(b / "core/content_flags.csv", ["Incident Number", "table", "row_key", "column", "flag"], [])
    out = tmp_path / "o.csv"
    with pytest.raises(SystemExit, match="no narrative cells"):
        build_review_pack(b, out_path=out)
    assert not out.exists()
    blank = _mini(tmp_path / "blank", {"A1": ""})
    with pytest.raises(SystemExit, match="no narrative cells"):
        build_review_pack(blank, out_path=tmp_path / "o2.csv")


def test_flag_for_missing_cell_fails_loud(tmp_path):
    b = _mini(tmp_path, {"A1": "Fine."})
    _w(b / "core/content_flags.csv", ["Incident Number", "table", "row_key", "column", "flag"], [
        {"Incident Number": "ZZ", "table": "core/real_only/incidents.csv", "row_key": "ZZ",
         "column": NARR, "flag": "near_fatal"}])
    with pytest.raises(SystemExit, match="flagged cell not found"):
        build_review_pack(b, out_path=tmp_path / "o.csv")


def test_missing_flags_file_fails_loud(tmp_path):
    b = _mini(tmp_path, {"A1": "Fine."}, flags=False)
    with pytest.raises(SystemExit, match="not a built bundle"):
        build_review_pack(b, out_path=tmp_path / "o.csv")


def test_output_independent_of_hash_seed(bundle, tmp_path):
    import os
    import subprocess
    import sys
    outs = []
    for seed in ("1", "2"):
        o = tmp_path / f"seed{seed}.csv"
        subprocess.run([sys.executable, "-m", "psm.review_pack", str(bundle), "--n-random", "5",
                        "--out", str(o)], check=True, capture_output=True,
                       env={**os.environ, "PYTHONHASHSEED": seed})
        outs.append(o.read_bytes())
    assert outs[0] == outs[1] and len(outs[0]) > 0
