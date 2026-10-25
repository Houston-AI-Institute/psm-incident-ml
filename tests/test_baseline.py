"""Tests for the reproducible cause-classification baseline.

The baseline trains on ``src_text_stripped`` and predicts ``xw_element`` (a 1:1
crosswalk relabel of the investigator's cause category), pooled out-of-fold
over ``syn_fold``. Scores measure agreement with the investigator's category.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

pytest.importorskip("sklearn")

from psm import baseline
from psm.baseline import run_baseline

REPO = Path(__file__).resolve().parents[1]
TASK_CSV = REPO / "data" / "processed" / "e19" / "cause_task.csv"

# Measured by `uv run python -m psm.baseline` (seed 20261018, n_boot 1000).
PINNED = {"n": 374, "accuracy": 0.471, "macro_f1": 0.349,
          "ci": (0.293, 0.399)}


@pytest.fixture(scope="module")
def res():
    return run_baseline(TASK_CSV)


def test_beats_majority(res):
    assert res["accuracy"] > res["majority_accuracy"]
    assert res["macro_f1"] > res["majority_macro_f1"]


def test_ci_brackets_point(res):
    lo, hi = res["macro_f1_ci"]
    assert lo < res["macro_f1"] < hi


def test_n_is_regime_rows(res):
    with TASK_CSV.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["syn_regime"] == "modern_six"]
    assert res["n"] == len(rows)
    assert sum(v["n"] for v in res["per_class"].values()) == res["n"]
    assert res["n_classes"] == len(res["labels"]) == len(res["per_class"])


def test_deterministic(res):
    assert run_baseline(TASK_CSV) == res


def test_pinned(res):
    assert res["n"] == PINNED["n"]
    assert res["accuracy"] == pytest.approx(PINNED["accuracy"], abs=0.02)
    assert res["macro_f1"] == pytest.approx(PINNED["macro_f1"], abs=0.02)
    lo, hi = res["macro_f1_ci"]
    assert lo == pytest.approx(PINNED["ci"][0], abs=0.03)
    assert hi == pytest.approx(PINNED["ci"][1], abs=0.03)


def test_main_refuses_dirty_inputs(tmp_path):
    out = tmp_path / "baseline.md"

    def dirty(*args):
        return " M src/psm/baseline.py\n" if args[0] == "status" else "abc1234\n"

    with pytest.raises(RuntimeError, match="uncommitted"):
        baseline.main(out=out, git=dirty)
    assert not out.exists()


def test_main_stamps_last_input_commit(tmp_path):
    out = tmp_path / "baseline.md"
    calls = []

    def clean(*args):
        calls.append(args)
        return "" if args[0] == "status" else "abc1234\n"

    baseline.main(out=out, git=clean)
    text = out.read_text(encoding="utf-8")
    assert "Inputs last changed at commit `abc1234`" in text
    assert baseline.STAMP_COMMAND in text
    # Both git lookups are scoped to exactly the baseline's inputs.
    assert all(a[-2:] == baseline.INPUTS for a in calls)


# ---- other-regimes summary (descriptive only) --------------------------------

def _independent_summary(path):
    """Recompute regime, n, classes and majority rate straight from the CSV text."""
    with Path(path).open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for r in rows:
        reg = r["syn_regime"].strip()
        if reg and reg != "modern_six":
            out.setdefault(reg, []).append(r["xw_element"])
    return {reg: (len(v), len(set(v)), max(v.count(x) for x in set(v)) / len(v))
            for reg, v in out.items()}


def test_regime_summary_matches_an_independent_recount():
    got = baseline.regime_summary(TASK_CSV)
    want = _independent_summary(TASK_CSV)
    assert want, "the fixture has no other regimes, so this test could not fail"
    assert {g["regime"] for g in got} == set(want)
    assert "modern_six" not in {g["regime"] for g in got}
    for g in got:
        n, k, rate = want[g["regime"]]
        assert (g["n"], g["n_classes"]) == (n, k)
        assert g["majority_rate"] == pytest.approx(rate)
        assert set(g) == {"regime", "n", "n_classes", "majority_rate"}


def test_regime_summary_is_deterministic():
    assert baseline.regime_summary(TASK_CSV) == baseline.regime_summary(TASK_CSV)
    names = [g["regime"] for g in baseline.regime_summary(TASK_CSV)]
    assert names == sorted(names)


def test_regime_summary_skips_blank_regimes_and_modern_six(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("Incident Number,xw_element,syn_regime\n"
                 "a,1,modern_six\nb,1,\nc,2,old\nd,2,old\ne,3,old\nf,4,  \n", encoding="utf-8")
    assert baseline.regime_summary(p) == [
        {"regime": "old", "n": 3, "n_classes": 2, "majority_rate": pytest.approx(2 / 3)}]


def test_render_shows_other_regimes_without_the_word_accuracy():
    others = baseline.regime_summary(TASK_CSV)
    text = baseline.render(run_baseline(TASK_CSV, n_boot=20), "abc1234", others, n_boot=20)
    assert "## Other regimes" in text
    section = text.split("## Other regimes")[1].split("\n## ")[0]
    for g in others:
        assert f"| {g['regime']} | {g['n']} | {g['n_classes']} | {g['majority_rate']:.3f} |" in section
    assert "accuracy" not in section.lower()
    assert "no model" in section.lower()


def test_main_writes_the_other_regimes_table(tmp_path):
    out = tmp_path / "baseline.md"
    baseline.main(out=out, git=lambda *a: "" if a[0] == "status" else "abc1234\n")
    text = out.read_text(encoding="utf-8")
    for g in baseline.regime_summary(TASK_CSV):
        assert f"| {g['regime']} | {g['n']} | {g['n_classes']} |" in text


def _committed_stamp(text: str) -> str:
    import re
    m = re.search(r"Inputs last changed at commit `([0-9a-f]{7,})`", text)
    assert m, "docs/baseline.md has no input-commit stamp"
    return m.group(1)


def test_committed_baseline_md_matches_a_fresh_render(res):
    # PR #4 review: editing a number in docs/baseline.md passed every test.
    # Re-render from the committed inputs, reusing the file's own stamp so the
    # check does not depend on that commit still being reachable (squash merge).
    committed = baseline.DEFAULT_OUT.read_text(encoding="utf-8")
    fresh = baseline.render(res, _committed_stamp(committed),
                            baseline.regime_summary(TASK_CSV))
    assert committed == fresh, "docs/baseline.md is stale: run uv run python -m psm.baseline"
