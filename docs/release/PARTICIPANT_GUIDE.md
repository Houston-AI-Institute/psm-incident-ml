# PSM incident dataset, release {version}

Built from commit `{commit}` ({commit_date}).

## What this is

This bundle holds real US offshore accident reports, laid out as tables in the style of the Energy Institute's PSM Framework, Element 19. It is for practice. This project makes no claims about any operator or person. The reports name real operators, so do not judge them.

The data mixes real and generated values. Every cell says which: see **Provenance tokens**. Read the **Content note**<!-- text --> before any narrative text<!-- /text -->. `TERMS.md` has the rules.

<!-- text -->
**Narrative text.** Incident narratives, cause descriptions and `src_text_stripped` are included.

<!-- /text -->
<!-- no-text -->
**Text-light bundle.** The narrative and cause text are withheld in this bundle.

<!-- /no-text -->
<!-- companies -->
**Company tables.** They are fictional.

<!-- /companies -->

Track numbers are the same in every bundle, so a track can be missing from yours. Row counts:

{counts}

## Content note

> These are real US offshore incident reports, written in the plain, factual style of an investigation. Some describe workplace injuries such as cuts, broken bones, burns and lost fingers. Incidents in which someone died were removed from this bundle. Names that follow a title (Mr., Mrs., Ms., Miss, Dr.) were replaced with `[name]`, along with the surname wherever it repeats. Other names may remain, because redaction is pattern-based. The source PDFs linked in `bsee_source_url` are not redacted. `content_flags.csv` marks rows that describe a serious injury or someone losing consciousness. The flags are pattern-based too, so they can miss some rows. If anything here is a problem, talk to a mentor.

You can stop reading at any time.

## Choosing tools

Under-18 participants do not use Google AI Studio or the Gemini API themselves.

<!-- organiser text: under-18 tool guidance -->

<!-- text -->
Tracks marked `[AI tool]` use an AI model. Follow the tool rules above, and ask a mentor if unsure.
<!-- /text -->

## Folders

- `core/` is the main data. `real_only/` has the four tables with generated cells left blank. `enriched/` fills them in, with a `provenance` file that tags every cell. <!-- text -->`cause_task.csv` is for Tracks 3 and 4. <!-- /text --><!-- no-text -->`cause_task.csv` has the labels and folds but no text. <!-- /no-text -->`cells_long.csv` is one row per cell. `content_flags.csv` marks sensitive rows. `redactions.csv` lists the redacted cells in tables with no provenance file. In the others, redacted cells are marked `mod` in the provenance file. `data_dictionary.csv` describes every column.
- `advanced/` has extra cause files, `bsee_unmapped.csv` and `schema/`, the crosswalk files. The crosswalk is an opinion.
<!-- companies -->
- `companies/`: one folder per fictional company.
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
- `llm`: assigned by a language model. Never ground truth. None in this bundle.
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
- **`real_only` vs `enriched`**: `real_only` has real values, rule-derived (`xw`) values, pseudonyms (`pseud`) and keys (`key`), with generated cells left blank. `enriched` adds the generated (`syn`) cells, and in `core/`, only `enriched` has provenance files.

## Quick start

Run from the folder that holds `core/`.

```python
import pandas as pd

df = pd.read_csv("core/cause_task.csv", encoding="utf-8")

# Keep only this regime.
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
<!-- text -->
- Cause text can drop words. This affects `Cause Description` and `src_text_stripped`.
- The leak: in `causes.csv`, `Cause Description` begins with its own category, which is the label. For classification use `cause_task.csv`, where the category is removed.
<!-- /text -->
- In `bsee_operator`, representative, city, state and telephone blocks have been removed. A "CONTRACTOR:" label and the name after it can remain.
- `syn_regime` comes from a real year (`src_year`), using the splits in `core/real_only/splits.json`.

## Tracks

Tags: `[no-code ok]` works in a spreadsheet. `[code]` needs programming.<!-- text --> `[AI tool]` uses an AI model.<!-- /text -->

1. **Explore what goes wrong offshore** `[no-code ok]`. Look for patterns.
2. **Provenance detective** `[no-code ok]`. Use the `enriched` tables, their provenance files and `cells_long.csv`. Which parts of a record are real?
<!-- text -->
3. **Cause classification** `[code]`. Predict `xw_element` from `src_text_stripped`. Protocol:
   - Filter `syn_regime == modern_six`.
   - Use 5-fold cross-validation, grouped by `syn_fold`.
   - Pool the out-of-fold predictions.
   - Report macro-F1 over all labels.
   - Report n, a bootstrap confidence interval over incidents, and per-class n and F1.
   - Compare against `BASELINE.md`.
   - Don't use `src_year`, `syn_regime`, `src_category` or `Incident Number` as features.
4. **Era drift** `[code]`. A check, not a contest. Train on `modern_six` and test on the earlier regimes. `BASELINE.md` prints n, classes and majority rate for each. The regime boundaries were drawn where the label vocabulary changed, so a change in label mix between regimes is not a finding. The earlier regimes have very small n (19 and 13).
5. **LLM structured extraction** `[code]` `[AI tool]`. Choose the field and the rows (at least 30, by a stated rule) before you run anything. Two teammates label the rows independently, then compare and resolve. Only then run the model. Report "agreement with our n hand labels: k/n" with an interval. Never call it accuracy. It shows agreement with your own labels, not truth.
<!-- /text -->
<!-- companies -->
6. **Company register audit** `[no-code ok]`. Look for gaps or oddities in a fictional company's generated tables, such as close-outs marked "Behind".
<!-- /companies -->

## Do not attempt

- Ranking real operators or naming individuals.
- Judging any real person's conduct.
<!-- companies -->
- Treating the fictional companies' records as real.
<!-- /companies -->
- Reporting a score without n.
- Contacting anyone named in a source PDF.

<!-- companies -->
## About the companies

Alder, Hazel and Hornbeam are fictional. Their process records (dates, owners, actions) are generated. Recommendations are generic templates, not linked to their incident.
<!-- text -->
Their narratives are real BSEE text from other operators, with dates shifted and operator names pattern-masked as `[company]`, so a few may remain. Contractor and rig names are not masked. The same unmasked narratives appear in `core/`, so the masking is about attribution, not secrecy.
<!-- /text -->
They describe no real company's conduct.
<!-- /companies -->

## How to report a result

Say four things:

1. The claim.
2. The columns you used, and their provenance tokens.
3. n.
4. One limitation.
