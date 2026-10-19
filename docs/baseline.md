# Cause-classification baseline

> Target: the BSEE investigator's own cause-category head, removed from the text, shown as the repo's crosswalk element (a 1:1 relabel; the crosswalk is the repo's opinion). Scores measure recovery of the investigator's category, not PSM-element correctness. No independent (gold) labels exist. The CI covers evaluation-sample variance for this one fold assignment, not training or fold variance.

A text-only baseline: TF-IDF (word 1-2 grams, `min_df=2`) into a class-balanced logistic regression. It reads the cause statement with the investigator's category head removed (`src_text_stripped`) and predicts the crosswalk element (`xw_element`). Predictions are pooled out-of-fold over `syn_fold`: each fold is predicted by a model trained on the other four. The majority baseline is also out-of-fold (it predicts the most frequent label of each fold's training rows).

Regime: `modern_six`. Seed 20261018, 1000 bootstrap resamples.

## Result

| measure | value |
|---|---|
| n (rows scored) | 374 |
| classes (elements present) | 6 |
| accuracy (agreement with the investigator's category) | 0.471 |
| macro-F1 | 0.349 |
| macro-F1 95% CI | 0.293 to 0.399 |
| majority baseline, accuracy (agreement with the investigator's category) | 0.334 |
| majority baseline, macro-F1 | 0.084 |

Macro-F1 is averaged over the fixed list of elements present in this regime, with `zero_division=0`, including inside every bootstrap resample. The interval resamples incidents with replacement (taking all of each sampled incident's rows) over the fixed pooled predictions; the model is not retrained per resample.

## Per element (pooled out-of-fold)

Pooled over all folds. No per-fold per-class scores are reported: some elements are too sparse for that. Small n means a noisy F1.

| element | n | F1 |
|---|---|---|
| 3 | 125 | 0.531 |
| 6 | 27 | 0.182 |
| 8 | 64 | 0.418 |
| 9 | 26 | 0.182 |
| 15 | 104 | 0.610 |
| 17 | 28 | 0.170 |

## Reproduce

```bash
uv sync --extra dev
uv run python -m psm.baseline
```

Inputs last changed at commit `78a2fd1` (`git log -1 --format=%h -- src/psm/baseline.py data/processed/e19/cause_task.csv`). Re-running at any later commit reproduces this file byte-for-byte unless those inputs change.

## Not comparable to earlier figures

Earlier figures (macro-F1 about 0.42 on n=438) came from a different row set and are not comparable to the numbers above.
