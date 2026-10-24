# psm-incident-ml

A semi-synthetic process-safety incident dataset built from US federal offshore
incident reports (BSEE MMS Form 2010), projected onto the Energy Institute PSM
Framework **Element 19** (Incident Reporting & Investigation) template.

Every cell records its own origin. Nothing is filled unless the source supplies
it, a versioned rule derives it, or a generator fabricates it. Fabricated values
are marked per cell (`syn`); real text is never altered except where marked
`mod`, and except the BSEE sidecar's removed contact blocks.

## Read this before modelling

Three properties will change what you build. Each is measured, not asserted;
`docs/findings.md` has the method for all of them.

1. **7 of the 20 PSM elements are reachable.** BSEE's six cause categories map to
   elements 3, 6, 8, 9, 15, 17, plus 11 via secondaries. In `real_only/` and
   `enriched/` the other 13 cannot appear at any coverage. `filled/` carries 18
   elements, because its ` Failed PSM Framework Element` is also filled from
   `llm` and `syn` labels; those 18 are not evidence that more elements are
   reachable. A model scored on "PSM element" is running a 7-class problem under
   a 20-class label.
2. **Expect modest scores on cause-category recovery, and report macro-F1 with
   n and an interval.** A text-only baseline (TF-IDF word 1-2 grams into a
   class-balanced logistic regression, pooled out-of-fold over `syn_fold`,
   regime `modern_six`) scores accuracy 0.471 and macro-F1 0.349 (95% CI 0.293
   to 0.399, bootstrap over incidents) on n = 374 rows and 6 classes. The
   majority baseline scores accuracy 0.334 and macro-F1 0.084. Two elements carry
   most of the signal (15: F1 0.610; 3: F1 0.531); 6, 9 and 17 sit near F1 0.17
   to 0.18 at n 26 to 28. The target is the BSEE investigator's own cause-category
   head, removed from the text, shown as the repo's crosswalk element (a 1:1
   relabel; the crosswalk is the repo's opinion). Scores measure recovery of the
   investigator's category, not PSM-element correctness. No independent (gold)
   labels exist. Reproduce with `uv run python -m psm.baseline`; method, caveats
   and the per-element table are in [`docs/baseline.md`](docs/baseline.md).
3. **The label vocabulary is non-stationary in four regimes.**
   `data/processed/e19/real_only/splits.json` is a reporting-era partition; for
   classification use `cause_task.csv` `syn_fold` (grouped by incident). Pooling
   regimes lets part of a score come from telling the decade, so score within one
   regime, as the baseline does.

| regime | years | incidents | cause vocabulary |
|---|---|---|---|
| `free_prose` | ≤2006 | 161 | none; 0% of statements map |
| `human_error` | 2007-2009 | 258 | one head, `Human Error` |
| `ad_hoc` | 2010-2018 | 438 | 68 investigator-invented heads |
| `modern_six` | 2019+ | 321 | the modern six; adoption jumps 5→17 between 2018 and 2019 |

`splits.json` also lists 36 undated incidents as excluded. Of the 406 rows of
`data/processed/e19/cause_task.csv`, 374 (92.1%) are `modern_six`, 19 `ad_hoc`
and 13 `human_error`; it holds no `free_prose` rows.

## Tables

Byte-exact E19 column labels, including the template's own typos
(`Incident Classificatioin`, a leading space on ` Failed PSM Framework Element`).
`incidents.csv` and `causes.csv` are in `data/processed/e19/enriched/` (and
`filled/`); `recommendations.csv` and `closeout.csv` are in
`data/processed/e19/real_only/` and, verbatim, at `data/processed/e19/`.

| table | rows | grain |
|---|---|---|
| `incidents.csv` | 1,214 | one per incident |
| `causes.csv` | 3,572 | one per cause statement |
| `recommendations.csv` | 1,230 | one per recommendation |
| `closeout.csv` | 1,230 | one per recommendation |

Sidecars, all keyed on `Incident Number` (+ `Cause number` where applicable):

| file | where | contents |
|---|---|---|
| `provenance.csv`, `causes_provenance.csv` | `enriched/` | per-cell `src` / `xw` / `syn` / empty, same shape as the table |
| `causes_confidence.csv` | `enriched/` | per-cell confidence where a mapping is graded |
| `causes_secondary_element.csv` | `enriched/` | `also_touches` element from `crosswalk.yaml` |
| `bsee_unmapped.csv` | `data/processed/e19/` | BSEE fields with no E19 counterpart, `bsee_` prefixed |
| `causes_source_field.csv` | `data/processed/e19/` | whether a statement came from form field 18 or 19 |
| `cause_task.csv` | `data/processed/e19/` | cause rows with the category head removed, `syn_regime` and `syn_fold` |

`Incident Number` is constructed (`{AREA}-{BLOCK}-{YYYYMMDD}-{HHMM}`) because
BSEE publishes no incident identifier. It is **variable arity**: components are
dropped when the source lacks them. 1,002 keys have four components, 129 have
three, 79 have two, 4 carry a content-hash suffix for colliding groups. 162 carry
no time. All 1,214 are unique.

## Provenance

| mark | meaning |
|---|---|
| `src` | read verbatim from a BSEE PDF or CSV |
| `xw` | derived by a versioned rule in `schema/xw_*.yaml` |
| `llm` | assigned by a language model; never treated as ground truth (`filled/` layer only, written by `src/psm/fill.py`) |
| `syn` | fabricated -- in `enriched/`, by `src/psm/synth.py` under `schema/synth_rules.yaml`; in `filled/`, also by `src/psm/fill.py` under the same rules file |
| `key` | constructed join identifier (e.g. `Incident Number`); BSEE publishes no incident id, the whole column is built by this repo |
| `pseud` | salted pseudonym of a real value (`Investigation leader`/`Approver` name columns); a stable privacy transform of a real person's name -- de-amplification, not fabrication |
| `mod` | real source text that this project altered: dates shifted, or a company name, person name or contact detail masked; otherwise verbatim. May only replace `src` |
| empty | not filled; see `gap_policy` in `schema/e19_disposition.yaml` |

`key` and `pseud` are classified by column purpose (`src/psm/provenance.py`),
not by string pattern. Precedence is `src`/`key`/`pseud` > `xw` > `syn`: a
`key` or `pseud` cell is still a real, non-blank source cell reclassified by
column purpose, and -- like `src` -- is never displaced by `xw`, `llm`, or
`syn`. A `syn` value never displaces a real one. Enforced by
`tests/test_conventions.py` for `enriched/` and `tests/test_fill_outputs.py`
for `filled/`.

### The `filled/` layer

`data/processed/e19/filled/` is a second projection on top of `enriched/`,
built by `uv run python -m psm.fill` and turned into an SME-reviewable
workbook by `uv run python -m psm.export_e19` (writes
`deliverables/e19_filled.xlsx` -- gitignored, never commit it). It fills the
remaining gaps in `Work Group`, the two Likelihood columns, and `Cause type`
(all `syn`), plus ` Failed PSM Framework Element` (kept where `enriched/`
already has a crosswalk value, else `llm` from a labelling run, else a
deterministic `syn` fallback). `filled/` carries its own parallel provenance
files, same shape and token convention as `enriched/`'s.

Composition across the two tables `filled/` actually provenances
(`incidents.csv` + `causes.csv`; `recommendations.csv`/`closeout.csv` are not
part of the `filled/` layer -- see [`psm.fill`](src/psm/fill.py)'s docstring
-- so a "four tables" figure has no reconstructable denominator): summing
per-cell tokens over both provenance files (52,202 + 25,004 = 77,206 cells),
**33.5% real** (`src`+`xw`), 31.7% fabricated (`llm`+`syn`), 9.1%
`key`+`pseud` (constructed identifiers / pseudonyms, neither real nor
fabricated), 25.7% blank by policy. These are counts of the tokens in
`filled/provenance.csv` and `filled/causes_provenance.csv`; the generated
`docs/e19_field_ledger.md` reports the same kind of composition for `enriched/`.

Incidents table alone (`filled/provenance.csv`, 1,214 rows × 43 columns =
52,202 cells): 19.0% `src`, 15.1% `xw`, 2.3% `key`, 4.3% `pseud`, 34.2%
`syn`, 25.2% blank. (Phase 0 added `key`/`pseud`; the pre-Phase-0 figure was
25.6% `src` with those cells folded into `src`.)

Synthetic identities are hash tokens (`SYN-Approver-da5b09`,
`Synthetic Role — Investigation Acceptor`), never plausible names. A test asserts
they stay distinguishable from real values.

## Two artifacts

```bash
uv run python -m psm.ledger --real-only
```

- `data/processed/e19/enriched/` is the full sheet, including synthetic
  scaffolding. Use it to demo the E19 workflow end to end.
- `data/processed/e19/real_only/` is the same tables with all `syn` cells
  blanked, plus `splits.json`. Use it to train. Rows are blanked, not dropped,
  so joins hold.

## Why cells are blank

`schema/e19_disposition.yaml` gives every column a `gap_policy`, and every
`leave_blank` column a `blank_reason`.

| reason | columns | rule |
|---|---|---|
| `would_dominate` | 8 | under 50% real; fabrication would be the majority of the column |
| `no_generator` | 13 | no honest way to produce the value. `Date of Incident` is 97.0% real and still unfillable: inventing a date asserts when a real incident happened |
| `degenerate_fill` | 4 | a generator exists and its output carries no information. `syn_hs_risk_score` emits {2,5,9} against a real 1-25 consequence×likelihood product; the value sets are almost disjoint |

This policy governs `enriched/`. The `filled/` layer above deliberately
reverses it for one `would_dominate` column: ` Failed PSM Framework Element`
is filled anyway, to 85.3% non-real (2,008 `llm` + 1,040 `syn` of 3,572) --
precisely the outcome the policy exists to prevent in `enriched/`. The
reversal is scoped to `filled/`, per-cell provenanced, and disclosed in
`schema/e19_disposition.yaml`'s note on that column.

`docs/e19_field_ledger.md` is generated from this file joined to measured
coverage. `tests/test_ledger.py` fails the build if any claim in it stops being
true.

## Validity

Coverage is not correctness. Eight columns declare a shape check; 94.3% of
checked cells pass.

| valid | column | failures |
|---|---|---|
| 69.1% | `Recommendation Description` | truncated 355, form_label 18 |
| 94.6% | `Cause Description` | too_short 186, form_label 6 |
| 96.9% | `Incident Number` | bad_pattern 38 (time with no date) |

Checks are opt-in per column. A global rule fails every code, key and picklist
value in the dataset.

## Known bias

BSEE convenes a panel investigation for the most severe events and publishes
those separately. **54.1% of fatalities in the incident index are panel cases**
and are excluded from this corpus, which is district reports only. Severity is
thinned at the top. Do not read the distribution as representative of offshore
incident severity.

## Pipeline

Python 3.11+, `uv`. Entry points are modules:

```bash
uv run python -m psm.harvest     # index -> data/manifest.csv (1,302 rows, SHA256 per file)
uv run python -m psm.fetch       # manifest -> data/raw/
uv run python -m psm.extract     # PDFs -> data/interim/*.json
```

To regenerate every committed table from `data/interim/`, in order:

```bash
uv run python -m psm.project && uv run python -m psm.crosswalk && uv run python -m psm.ledger --real-only && uv run python -m psm.fill && for c in alder hazel hornbeam; do uv run python -m psm.scenario $c; done && uv run python -m psm.cause_task
```

`psm.project` writes the verbatim tables, `psm.crosswalk` adds `xw` and `syn`
(`enriched/`), `psm.ledger --real-only` writes `real_only/`, `splits.json` and the
ledger, `psm.fill` writes `filled/`, `psm.scenario` writes one company register,
and `psm.cause_task` writes `cause_task.csv` (the leak-free cause task). Then:

```bash
uv run python -m psm.baseline    # docs/baseline.md
uv run python -m psm.cluster     # the six-category partition check
uv run python -m psm.bundle [--no-companies] [--text-light]   # the participant bundle
uv run python -m psm.review_pack <bundle_dir>                 # personal-info review sample
```

`psm.bundle` writes under `deliverables/release/` (gitignored). It refuses to run on
a dirty or uncommitted input tree, so commit first. `psm.review_pack` reads a built
bundle and writes `deliverables/release/review_sample.csv` by default (`--out` to change).

`data/raw/` and `data/interim/` are gitignored. `data/manifest.csv` is committed
with a SHA256 per file, so a fresh clone rebuilds byte-identical inputs.

Extraction is coordinate-aware (`pdfplumber` bounding boxes). Text-stream order
is not visual order on this form; naive `extract_text()` produces confidently
wrong field assignments. Anchors resolve by **label**, not by printed number:
revision B renumbers the form face, and two-column linearisation drops digits
(`13. SEA STATE` arrives as `3. SEA STATE`).

## Company registers

`data/companies/{alder,hazel,hornbeam}/` hold three fictional companies' E19 registers
(four tables each, with per-table provenance files). Their incidents are real BSEE
reports from other operators, donated by a deterministic partition; their process
records (dates, owners, actions) are generated. Provenance tokens in these files are
`src`, `xw`, `syn`, `key` and `mod`. Each scenario YAML keeps its pre-rename name as
`seed_key` (`northstar`, `meridian`, `coastal`), so no draw changed when the companies
were renamed. Each `manifest.json` records how the register was generated, with SHA256
hashes of its inputs.

Operator names in a company's free text are masked as `[company]`. The masking is
pattern-based, so a few may remain. Contractor and rig names are **not** masked, and
`data/processed/e19/` keeps real operator names throughout, as do the `core/` and
`advanced/` folders of the release bundle. Masking here is about attribution, not
secrecy: the narratives are verbatim real text, and a determined reader can recover
the operator.

## Rules are data

| file | decides |
|---|---|
| `bsee_form2010.yaml` | field map, furniture, label-bleed patterns, length caps |
| `crosswalk.yaml` | BSEE cause category → PSM element |
| `xw_incident_type.yaml` | accident-type atoms → E19 Type A/B/C/D |
| `xw_consequence_tiers.yaml` | hazard energy, likelihood, risk score |
| `xw_cause_qualifiers.yaml` | subcategory → Risk Management / Human Factors cause |
| `xw_outcome.yaml` | outcome atoms → prose |
| `e19_projection.yaml` | E19 label → BSEE source, or a blank reason |
| `e19_disposition.yaml` | per-column disposition, gap policy, validity checks |
| `synth_rules.yaml` | every synthetic field, with a frozen `reference_date` |

Each carries its reasoning and, where relevant, the alternatives that were
tested and rejected. `crosswalk.yaml` in particular is an opinion and is meant to
be argued with.

## Not validated

`gold/gold_labels.csv` has 100 sampled cause statements and **0 hand labels**.
Nothing in this repo has been scored against an independent human judgement. The
crosswalk's six category→element mappings have never been checked. Any accuracy
number you see elsewhere in these docs is model-vs-model or model-vs-rule.

Also outstanding: ~37 unexplained missing fatalities in the spine join; 145
records where form field 30 is not located because linearisation transposes its
label and number; `e19_schema.py`, `evidence.py`, `fetch.py` and `spine.py` at 0%
test coverage.

## Sources and licensing

Source documents are US federal government works (BSEE), public domain. The
derived tables, schema files and code in this repo are released under the
repository licence. The Energy Institute PSM Framework element *names* are
referenced with attribution. The E19 workbook's field labels, picklist vocabularies and cell references (`schema/e19_labels.yaml`) are published with the permission of Houston AI Institute, which owns the workbook schema (confirmed 2026-10-18). The workbook file itself is not committed.

## Repo layout

```
data/manifest.csv           committed; SHA256 per source PDF
data/processed/e19/         the dataset (verbatim tables, cause_task.csv, sidecars)
  enriched/ filled/ real_only/   the three projections
data/companies/             three synthetic-company E19 registers: alder, hazel,
                             hornbeam (deterministic scenario engine output); each
                             <company>/manifest.json records how it was generated
                             (scenario, knobs, donor partition, input hashes)
scenarios/                  process-rate knob YAMLs consumed by src/psm/scenario.py
gold/                       sampling frame for hand labels (unlabelled)
schema/                     every rule, as data
src/psm/                    pipeline
tests/                      861 tests (2 skip: no enriched/ recommendations or closeout)
docs/findings.md            append-only log: what was verified, by what method
docs/baseline.md            generated by psm.baseline
docs/e19_field_ledger.md    generated
docs/release/               participant guide and terms for the release bundle
```

## Arguing with this

`docs/findings.md` records negative results and reversals as well as successes,
including several cases where a test corrected the rule it was written to
encode. If a mapping looks wrong, the file that decides it is named above and the
reasoning is inline. Open an issue against the schema file, not the CSV.
