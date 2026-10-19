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

from psm.baseline import run_baseline

REPO = Path(__file__).resolve().parents[1]
TASK_CSV = REPO / "data" / "processed" / "e19" / "cause_task.csv"

# Measured by `uv run python -m psm.baseline` (seed 20261018, n_boot 1000).
PINNED = {"n": 374, "accuracy": 0.471, "macro_f1": 0.349}


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
