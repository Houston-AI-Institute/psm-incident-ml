# PSM incident dataset, release {version}

Built from commit `{commit}` ({commit_date}).

## What this is

This bundle holds real US offshore accident reports, laid out as tables in the style of the Energy Institute's PSM Framework, Element 19. It is for practice. It says nothing about any company or person.

The data mixes real and generated values. Every cell says which: see **Provenance tokens**. Read the **Content note** before any narrative text. `TERMS.md` has the rules.

<!-- text -->
**Narrative text.** Incident narratives, cause descriptions and `src_text_stripped` are included.

<!-- /text -->
<!-- companies -->
**Company tables.** The company registers are fictional.

<!-- /companies -->

Track numbers are the same in every bundle, so a track can be missing from yours. Row counts:

{counts}

## Content note

> These are real US offshore incident reports. Injuries are described plainly. Fatal incidents were removed from this bundle. Names that follow a title (Mr., Ms., Dr.) were replaced with `[name]`, along with the surname wherever it repeats. Other names may remain: redaction is pattern-based. The source PDFs linked in `bsee_source_url` are not redacted. `content_flags.csv` marks rows that mention death, near-fatal events or serious injury. The flags are pattern-based too, so unflagged rows can still describe serious injury. If any of this is a problem, talk to a mentor.

You can stop reading at any time.

## Choosing tools

Under-18 participants do not use Google AI Studio or the Gemini API themselves.

<!-- organiser text: under-18 tool guidance -->

Tracks marked `[AI tool]` use an AI model. Follow the tool rules above, and ask a mentor if unsure.

## Folders

- `core/` is the main data. `real_only/` has the four tables with generated cells left blank. `enriched/` fills them in, with a `provenance` file that tags every cell. `cause_task.csv` is for Track 3. `cells_long.csv` is one row per cell. `content_flags.csv` marks sensitive rows. `redactions.csv` lists cells changed to hide names or contacts. `data_dictionary.csv` describes every column.
- `advanced/` has extra cause files, `bsee_unmapped.csv` and `schema/`, the crosswalk files. The crosswalk is an opinion.
<!-- companies -->
- `companies/` has one folder for each fictional company.
<!-- /companies -->
- `BASELINE.md` is a simple model to compare against.

## Provenance tokens

Each cell in a `provenance` file holds one token. Empty means an empty cell.

- `src`: copied unchanged from a BSEE source.
- `mod`: real source text that this project altered: dates shifted, or a company name, person name or contact detail masked; otherwise verbatim.
- `xw`: set by rule from a BSEE category, using the crosswalk in `advanced/schema/`.
- `syn`: generated filler. It matches nothing real.
- `key`: a join identifier this project built (`Incident Number`).
- `pseud`: a stable pseudonym for a real person's name.
- `llm`: assigned by a language model. Never ground truth.
- `gold`: assigned by a human. None in this bundle.

There are no independent (gold) labels: nothing in this bundle is validated. The cause labels are the investigator's own category, relabelled through the repo's crosswalk, which is an opinion.

## Glossary

- **BSEE**: Bureau of Safety and Environmental Enforcement, the US regulator behind these reports.
- **OCS**: Outer Continental Shelf, US federal waters offshore.
- **PSM**: process safety management.
- **EI**: Energy Institute.
- **E19**: Element 19 of the EI PSM Framework, incident reporting and investigation. The tables follow its layout.
- **Incident, cause, recommendation and close-out tables**: one row per incident, cause statement, recommended action, and recommendation closing.
- **TLI / LTA**: injury levels. LTA is BSEE's code for an injury with days away from work. This project does not know what TLI stands for.
- **`real_only` vs `enriched`**: `real_only` holds only real values, with generated cells blank. `enriched` adds crosswalk (`xw`) and generated (`syn`) cells.

## Quick start

Run from the folder that holds `core/`.

```python
import pandas as pd

df = pd.read_csv("core/cause_task.csv", encoding="utf-8")

# Track 3 uses only this regime.
task = df[df["syn_regime"] == "modern_six"]
print(len(task), "cause rows from", task["Incident Number"].nunique(), "incidents")

# Folds. Every row of one incident is in the same fold.
print(task.groupby("syn_fold").size())

# The label is xw_element, an element number from the crosswalk.
print(task.groupby("syn_fold")["xw_element"].value_counts().unstack(fill_value=0))
```

Excel: use Data → From Text/CSV and choose UTF-8, or accented characters and dashes will display wrongly.

## Known defects

- `Approval Date` is generated (`syn`), not a real approval date.
- `Description` is not shipped, and where BSEE text exists it is partial.
- Investigator and approver names are pseudonyms per field string, not per person: the same real person can get different pseudonyms in different fields.
- The three consequence columns (Health & Safety, Environment & Reputation, Financial Cost & Business Interruption) come from an assumed matrix, `advanced/schema/xw_consequence_tiers.yaml`, not from BSEE.
- Cause text can drop words: in a sample of 61, the median share of words kept was about 0.98, and 8 kept fewer than 0.80. This affects `Cause Description` and `src_text_stripped`.
- The leak: in `causes.csv`, `Cause Description` begins with its own category, which is the label. For classification use `cause_task.csv`, where the category is removed.
- In `bsee_operator`, contact blocks have been removed.
- `syn_regime` comes from a real year (`src_year`), using the splits in `core/real_only/splits.json`.

## Tracks

Tags: `[no-code ok]` works in a spreadsheet. `[code]` needs programming. `[AI tool]` uses an AI model.

1. **Explore what goes wrong offshore** `[no-code ok]`. Look for patterns.
2. **Provenance detective** `[no-code ok]`. Use the `enriched` tables, their provenance files and `cells_long.csv`. Which parts of a record are real?
3. **Cause classification** `[code]`. Predict `xw_element` from `src_text_stripped` (the text-light bundle does not have it). Protocol:
   - Filter `syn_regime == modern_six`.
   - Use 5-fold cross-validation, grouped by `syn_fold`.
   - Pool the out-of-fold predictions.
   - Report macro-F1 over all labels.
   - Report n, a bootstrap confidence interval over incidents, and per-class n and F1.
   - Compare against `BASELINE.md`.
   - Don't use `src_year`, `syn_regime`, `src_category` or `Incident Number` as features.
4. **Era drift** `[code]`. A check, not a contest. Train on `modern_six` and test on the earlier regimes. `BASELINE.md` prints n, classes and majority rate for each. The regime boundaries were drawn where the label vocabulary changed, so a change in label mix between regimes is not a finding.
<!-- text -->
5. **LLM structured extraction** `[code]` `[AI tool]`. Choose the field and the rows (at least 30, by a stated rule) before you run anything. Two teammates label the rows independently, then compare and resolve. Only then run the model. Report "agreement with our n hand labels: k/n" with an interval. That is not a measure of being right.
<!-- /text -->
<!-- companies -->
6. **Company register audit** `[no-code ok]`. Look for gaps and odd entries in a fictional company's tables, such as recommendations with no close-out. The records are generated, so say so.
<!-- /companies -->

## Do not attempt

- Ranking real operators or naming individuals.
- Judging any real person's conduct.
- Treating the fictional companies' records as real.
- Reporting a score without n.
- Contacting anyone named in a source PDF.

<!-- companies -->
## About the companies

Alder, Hazel and Hornbeam are fictional. Their process records (dates, owners, actions, recommendations) are generated. Their narratives are real BSEE text from other operators, with dates shifted and real company names masked as `[company]`. The same unmasked narratives appear in `core/`, so the masking is about attribution, not secrecy. They describe no real company's conduct.
<!-- /companies -->

## How to report a result

Say four things:

1. The claim.
2. The columns you used, and their provenance tokens.
3. n.
4. One limitation.
