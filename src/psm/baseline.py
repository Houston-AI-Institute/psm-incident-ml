"""Reproducible cause-classification baseline.

Trains on ``src_text_stripped`` and predicts ``xw_element`` from
``data/processed/e19/cause_task.csv``. ``xw_element`` is the crosswalk relabel
of the BSEE investigator's own cause category, so every score here is agreement
with the investigator's category, not PSM-element correctness.

Predictions are pooled out-of-fold over ``syn_fold`` (each fold is predicted by
a model trained on the other folds). Metrics are pooled over those predictions;
nothing is scored per fold. The majority baseline is also out-of-fold: it
predicts the most frequent label of each fold's training rows.

The macro-F1 interval is a 95% bootstrap over *incidents* (all rows of each
sampled incident are taken) applied to the fixed pooled out-of-fold
predictions. The model is not retrained per resample.

:func:`run_baseline` is pure: a path in, a dict out, no global state.

Run::

    uv run python -m psm.baseline
"""

from __future__ import annotations

import csv
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.pipeline import make_pipeline

REPO = Path(__file__).resolve().parents[2]
TASK_CSV = REPO / "data" / "processed" / "e19" / "cause_task.csv"
DEFAULT_OUT = REPO / "docs" / "baseline.md"
COMMAND = "uv run python -m psm.baseline"

DEFAULT_SEED = 20261018

TARGET_TEXT = (
    "Target: the BSEE investigator's own cause-category head, removed from the "
    "text, shown as the repo's crosswalk element (a 1:1 relabel; the crosswalk "
    "is the repo's opinion). Scores measure recovery of the investigator's "
    "category, not PSM-element correctness. No independent (gold) labels exist. "
    "The CI covers evaluation-sample variance for this one fold assignment, not "
    "training or fold variance."
)


def _label_key(label: str) -> tuple[int, str]:
    """Numeric elements in numeric order ("8" before "15"), anything else last."""
    return (int(label), label) if label.isdigit() else (10**9, label)


def _model(seed: int):
    return make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2), min_df=2),
        LogisticRegression(class_weight="balanced", max_iter=2000, random_state=seed),
    )


def _macro_f1(y_true, y_pred, labels: list[str]) -> float:
    return float(f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0))


def run_baseline(task_csv: Path, regime: str | None = "modern_six",
                 n_boot: int = 1000, seed: int = DEFAULT_SEED) -> dict:
    """Pooled out-of-fold baseline over ``syn_fold`` for one regime (None = all rows)."""
    with Path(task_csv).open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if regime is None or r["syn_regime"] == regime]
    if not rows:
        raise ValueError(f"no rows for regime {regime!r} in {task_csv}")

    text = np.array([r["src_text_stripped"] for r in rows], dtype=object)
    y = np.array([r["xw_element"] for r in rows], dtype=object)
    fold = np.array([r["syn_fold"] for r in rows], dtype=object)
    incident = [r["Incident Number"] for r in rows]
    labels = sorted(set(y.tolist()), key=_label_key)

    pred = np.empty(len(rows), dtype=object)
    maj = np.empty(len(rows), dtype=object)
    for k in sorted(set(fold.tolist())):
        test = fold == k
        counts = Counter(y[~test].tolist())
        top = max(counts.values())
        maj[test] = min((lab for lab in counts if counts[lab] == top), key=_label_key)
        model = _model(seed).fit(text[~test], y[~test])
        pred[test] = model.predict(text[test])

    by_incident: dict[str, list[int]] = defaultdict(list)
    for i, inc in enumerate(incident):
        by_incident[inc].append(i)
    groups = [np.array(by_incident[inc]) for inc in sorted(by_incident)]
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(groups), size=len(groups))
        idx = np.concatenate([groups[g] for g in pick])
        boots.append(_macro_f1(y[idx], pred[idx], labels))
    lo, hi = np.percentile(boots, [2.5, 97.5])

    f1s = f1_score(y, pred, labels=labels, average=None, zero_division=0)
    support = Counter(y.tolist())
    return {
        "n": len(rows),
        "n_classes": len(labels),
        "labels": labels,
        "accuracy": float(np.mean(y == pred)),
        "macro_f1": _macro_f1(y, pred, labels),
        "majority_accuracy": float(np.mean(y == maj)),
        "majority_macro_f1": _macro_f1(y, maj, labels),
        "macro_f1_ci": (float(lo), float(hi)),
        "per_class": {lab: {"n": support[lab], "f1": float(f)} for lab, f in zip(labels, f1s)},
    }


def _git_head() -> str:
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, check=True,
                          capture_output=True, text=True).stdout.strip()


def render(res: dict, head: str, regime: str = "modern_six", n_boot: int = 1000,
           seed: int = DEFAULT_SEED) -> str:
    lo, hi = res["macro_f1_ci"]
    out = [
        "# Cause-classification baseline",
        "",
        f"> {TARGET_TEXT}",
        "",
        ("A text-only baseline: TF-IDF (word 1-2 grams, `min_df=2`) into a class-balanced "
         "logistic regression. It reads the cause statement with the investigator's category "
         "head removed (`src_text_stripped`) and predicts the crosswalk element "
         "(`xw_element`). Predictions are pooled out-of-fold over `syn_fold`: each fold is "
         "predicted by a model trained on the other four. The majority baseline is also "
         "out-of-fold (it predicts the most frequent label of each fold's training rows)."),
        "",
        f"Regime: `{regime}`. Seed {seed}, {n_boot} bootstrap resamples.",
        "",
        "## Result",
        "",
        "| measure | value |",
        "|---|---|",
        f"| n (rows scored) | {res['n']} |",
        f"| classes (elements present) | {res['n_classes']} |",
        f"| accuracy (agreement with the investigator's category) | {res['accuracy']:.3f} |",
        f"| macro-F1 | {res['macro_f1']:.3f} |",
        f"| macro-F1 95% CI | {lo:.3f} to {hi:.3f} |",
        (f"| majority baseline, accuracy (agreement with the investigator's category) "
         f"| {res['majority_accuracy']:.3f} |"),
        f"| majority baseline, macro-F1 | {res['majority_macro_f1']:.3f} |",
        "",
        ("Macro-F1 is averaged over the fixed list of elements present in this regime, with "
         "`zero_division=0`, including inside every bootstrap resample. The interval resamples "
         "incidents with replacement (taking all of each sampled incident's rows) over the "
         "fixed pooled predictions; the model is not retrained per resample."),
        "",
        "## Per element (pooled out-of-fold)",
        "",
        ("Pooled over all folds. No per-fold per-class scores are reported: some elements are "
         "too sparse for that. Small n means a noisy F1."),
        "",
        "| element | n | F1 |",
        "|---|---|---|",
    ]
    out += [f"| {lab} | {v['n']} | {v['f1']:.3f} |" for lab, v in res["per_class"].items()]
    out += [
        "",
        "## Reproduce",
        "",
        "```bash",
        "uv sync --extra dev",
        COMMAND,
        "```",
        "",
        (f"Generated at repository HEAD `{head}` (from `git rev-parse --short HEAD`). This "
         "is the code commit the numbers were produced from: the commit that adds this file "
         "and `src/psm/baseline.py` sits directly on top of it, and this file cannot name "
         "its own commit."),
        "",
        "## Not comparable to earlier figures",
        "",
        ("Earlier figures (macro-F1 about 0.42 on n=438) came from a different row set and "
         "are not comparable to the numbers above."),
        "",
    ]
    return "\n".join(out)


def main(out: Path = DEFAULT_OUT) -> None:
    res = run_baseline(TASK_CSV)
    out.write_text(render(res, _git_head()), encoding="utf-8")
    lo, hi = res["macro_f1_ci"]
    print(f"n={res['n']} classes={res['n_classes']} acc={res['accuracy']:.3f} "
          f"macroF1={res['macro_f1']:.3f} [{lo:.3f}, {hi:.3f}] "
          f"majority acc={res['majority_accuracy']:.3f} F1={res['majority_macro_f1']:.3f}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
