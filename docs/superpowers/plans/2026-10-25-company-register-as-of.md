# Company Register As-Of Date Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the fictional company registers an extraction date, so actions not finished by then are open, and use the workbook's own Schedule Status values, so a process-health dashboard has an open backlog to show.

**Architecture:** One constant, `scenario.AS_OF = WINDOW_END` (2025-12-31), is the date the registers were "extracted". Every generated date after it is unknown and left blank, except a due date (`Agreed Completion Date`), which may lie in the future. `Schedule Status` is derived from the as-of comparison. The KPI layer gets the same `as_of` so overdue counts open actions past their due date. All draws stay unchanged: only how drawn dates are written changes.

**Tech Stack:** Python 3.11, uv, pytest, pandas (bundle and notebook checks only).

**Spec:** No separate spec. The plan argues from `deliverables/review-2026-10-18/synthesis.md` item 2.10 ("No as-of date: 0 of 490 actions open; completions run to 2027-11"), the workbook vocabulary in `schema/e19_labels.yaml:650-662`, and the owner's 2026-10-25 decisions in chat:
- do the as-of fix;
- drop the recommendation-text fix (item 2.9) and instead tell advising-agent teams that `core/` pairs each real incident with its real BSEE recommendation.

Rulings without a spec are provisional.

## Global Constraints

- `AS_OF = date(2025, 12, 31)`, defined as `AS_OF = WINDOW_END` in `src/psm/scenario.py`. No other date constant.
- Schedule Status values, verbatim from `schema/e19_labels.yaml`:
  - `Completed`: `Date Completed` ≤ AS_OF.
  - `Behind Original Agreed Schedule`: open, and `Agreed Completion Date` < AS_OF.
  - `On Schedule`: open, and `Agreed Completion Date` ≥ AS_OF.
  - `Deleted` is never generated.
- A blank cell has an empty provenance token. Generated values stay `syn`. No new token.
- No `random`, no wall clock: `test_engine_has_no_wall_clock_or_random_dependence` must still pass.
- **Do not change any draw.** Don't change hash keys, `SALT`, knob values in `scenarios/*.yaml`, or the order draws are made. The fix only changes which drawn dates are written.
- **Do not loosen any assertion in `tests/test_scenarios.py`** (planted thresholds, `tol()`, negative controls). If one fails after regeneration, stop and report BLOCKED with the measured values.
- **Never reveal or hint at the planted company patterns in team-facing text.** Planted patterns are which company is slow, behind, admin/PPE-heavy, and which are negative controls. Team-facing text is the guide, the brief, the notebook, and the `docs/findings.md` entry, which is public. Commit messages and the findings entry give totals across all companies, never per-company open or behind counts.
- `PARTICIPANT_GUIDE.md` template prose stays under 1350 words (`test_participant_guide.py:320`). Stamped readme ≤ 1600.
- Data and scoring:
  - No metric against `llm_` columns. Never write `gold_*`.
  - `docs/findings.md` is append-only.
  - Never commit the workbook.
  - `deliverables/` is gitignored, so files there are edited but not committed.
- Git and process:
  - `git add` names files only. No co-author or attribution lines.
  - Commits are local only: no push, PR or merge.
  - Don't delete files. Shell `ls` is `command ls`. Never prefix commands with `export PATH=`.
  - Implementers never dispatch subagents.
- Hard constraints (verbatim from `~/.claude/CLAUDE.md`):
  - Never modify files with `dg-publish: true` in frontmatter. These are published to the digital garden (recursiveintelligence.xyz) by the Obsidian Digital Garden plugin. Applies both to vault notes and to the generated mirrors in `~/code/ri/the-recursive-garden`. An override applies only to the literal file(s) named in that message — no other dg-publish file, even minutes later, even under the same auto-mode grant, without a fresh explicit yes naming that file. Any verification checkpoint set up mid-session (e.g. "audit diff before publish") is a standing rule for the rest of that session.
  - Never delete. Move deletion candidates to `_Review-for-Deletion/` in that station's `04` archive — `04_CONTAINMENT/` (iCloud), `04_ARCHIVES/` (GDrive personal), `04_INACTIVE/` (GDrive RI), `04-COLD-STORAGE/` (vault) — with a note saying why.
  - Never force-push to main/master without explicit confirmation.
  - Opening a PR, merging a PR, and any action that auto-triggers a deploy are three separate authorizations — each needs its own fresh yes, never inherited from an earlier one, even if CI is green and the plan was stated out loud beforehand.
  - Before any `gh pr create`, paste the actual diff or a complete file-by-file summary into chat. No exception for how much of the change is agent-authored.
  - Run a full-history secret scan (not just the working tree — e.g. `gitleaks detect --source . --log-opts="--all"` or `trufflehog git file://. --since-commit <first-commit>`) before any repo goes public or before pushing to an existing public remote. Secret remediation is not complete until you've confirmed via the provider's API that no dangling blob containing the secret is still fetchable by hash — "removed from the current file" and "purged from history" are different claims; don't relay the first as if it were the second.

## Review Focus

1. **A planted recurrence pair whose first incident is no longer closed.**
   - Detection needs the first incident's `Close out Date`.
   - It holds by construction: that date is before the second incident, which is on or before `WINDOW_END = AS_OF`.
   - Test: `test_planted_pairs_first_member_is_closed_by_as_of` (Task 2).
2. **An incident with an open action still carrying a `Close out Date` or a close-out approver.**
   - Expected: both blank whenever any of its actions is open.
   - Test: `test_open_action_means_no_close_out` (Task 2).
3. **Any generated date after AS_OF leaking into a table.**
   - Expected: none, except `Agreed Completion Date`.
   - Test: `test_no_generated_date_after_as_of` (Task 2), over all four tables of all three committed companies.
4. **A KPI reading an open action as finished.**
   - Item 3.6 in the review: missing dates read as a best-in-class 0.0.
   - Expected: open actions are left out of `median_closeout_days`, and counted as overdue only when past their due date.
   - Test: `test_kpis_with_open_actions` (Task 1).
5. **The bundle's company tables lacking an open or a late action after the fatality-row drop.**
   - That would make the guide's new Track 6 example false for one company.
   - Test: `test_track_6_gaps_exist_in_every_company` (Task 3).

---

### Task 1: KPIs read open actions correctly

**Files:**
- Modify: `src/psm/kpi.py:31-84`
- Test: `tests/test_kpi.py`

**Interfaces:**
- Consumes: `psm.scenario.AS_OF`. Add `AS_OF = WINDOW_END` to `src/psm/scenario.py` directly under `WINDOW_DAYS` in this task, with the comment `# registers are "extracted" on this date; later generated dates are unknown`.
- Produces: `compute_kpis(tables: dict[str, list[dict]], window_days: int = 365, as_of: date = AS_OF) -> dict[str, float | int]`. Same keys as today.

- [ ] **Step 1: Write the failing test** in `tests/test_kpi.py`

```python
def test_kpis_with_open_actions():
    from datetime import date
    from psm.kpi import compute_kpis
    inc = {"Incident Number": "X-1", "Date of Incident": "2025-10-01",
           "Date of Report": "2025-10-02", "Investigation leader - Name": "SYN-a",
           "Health & Safety - Risk Score": "", "Close out Date": "", "Work Group": "Maintenance"}
    recs = [{"Incident Number": "X-1", "Recommendation Number": str(i),
             "Recommendation Description": t, "Agreed Completion Date": a,
             "Responsible Owner - Name": "SYN-b"}
            for i, (t, a) in enumerate([(T0, "2025-11-01"), (T0, "2025-11-01"),
                                        (T0, "2026-02-01")], start=1)]
    close = [{"Incident Number": "X-1", "Recommendation Number": "1",
              "Schedule Status": "Completed", "Date Completed": "2025-11-11"},   # late, done
             {"Incident Number": "X-1", "Recommendation Number": "2",
              "Schedule Status": "Behind Original Agreed Schedule", "Date Completed": ""},
             {"Incident Number": "X-1", "Recommendation Number": "3",
              "Schedule Status": "On Schedule", "Date Completed": ""}]
    k = compute_kpis({"incidents": [inc], "causes": [], "recommendations": recs,
                      "closeout": close}, as_of=date(2025, 12, 31))
    assert k["median_closeout_days"] == 40.0          # only the completed action
    assert k["overdue_rate"] == 2 / 3                  # late-done + open-past-due
```

`T0` is any template text from `schema/action_templates.yaml`, for example `psm.templates.load_templates()[0]["text"]`, defined at module level in the test.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_kpi.py::test_kpis_with_open_actions -q`
Expected: FAIL. `compute_kpis` has no `as_of` keyword, and `overdue_rate` is 1/3.

- [ ] **Step 3: Implement** the `as_of` keyword in `compute_kpis`.
  - A closeout row counts as overdue when `done and agreed and done > agreed`, or when `not done and agreed and agreed < as_of`.
  - Rows with no `Date Completed` stay out of `closeout_days`. That already holds; keep it.
  - Nothing else changes.

- [ ] **Step 4: Run the KPI and scenario tests**

Run: `uv run pytest tests/test_kpi.py tests/test_scenarios.py -q`
Expected: all pass. Committed registers have no open actions yet, so every KPI value is unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/psm/kpi.py src/psm/scenario.py tests/test_kpi.py
git commit -m "feat(kpi): overdue counts open actions past their due date as of the register date"
```

### Task 2: The generator writes the register as of AS_OF; regenerate the companies

**Files:**
- Modify: `src/psm/scenario.py`. In `IncidentPlan`, change `close_out_date`. Change `build_incident_row`, `build_closeout_rows` and `build_manifest`.
- Modify: `tests/test_scenario_generate.py:25-51`, `tests/test_scenario_incidents.py:37-48`
- Regenerate: `data/companies/{alder,hazel,hornbeam}/*` (all nine files each, including `manifest.json`)
- Test: `tests/test_scenario_generate.py` (new tests below)

**Interfaces:**
- Consumes: `AS_OF` (Task 1).
- Produces:
  - `known(d: date | None) -> date | None` in `scenario.py`. It returns `d` if `d` is not None and `d <= AS_OF`, else `None`.
  - `IncidentPlan.close_out_date` returns `max(completed_dates())` only when every completed date is ≤ AS_OF, else `None`.
  - Manifest gains the key `"as_of": "2025-12-31"`.

Rules the code implements:

| Cell | Written when | Otherwise |
|---|---|---|
| `Date of Report` | `known(report_date)` | blank |
| `Approval Date` | `known(approval_date)` | blank; approver name and position also blank |
| `Close out Date` | `close_out_date` (as above) | blank; close-out approver name and position also blank |
| `Date Completed` | `known(done[i])` | blank |
| `Schedule Status` | `Completed` if done is known; else `Behind Original Agreed Schedule` if `agreed[i] < AS_OF`; else `On Schedule` | — |
| `Agreed Completion Date` | always (a due date may be in the future) | — |

Change the approver gate in `_PEOPLE` from `"investigated"` to a new gate `"approved"`: populated when `known(plan.approval_date)` is not None. The investigation leader keeps `"investigated"`. `plant_recurrence` and `completion_span` keep using raw drawn dates. Do not change them.

- [ ] **Step 1: Write the failing tests** in `tests/test_scenario_generate.py`, and update the two existing closeout tests.

```python
STATUSES = ("Completed", "On Schedule", "Behind Original Agreed Schedule")

def _all_tables(company):
    return {n: rows for n, (_, rows, _) in sc.generate(company)["tables"].items()}

@pytest.mark.parametrize("company", sc.COMPANY_ORDER)
def test_no_generated_date_after_as_of(company):
    t = _all_tables(company)
    date_cols = {"incidents": ("Date of Incident", "Date of Report", "Approval Date", "Close out Date"),
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
            assert r["Close out Approval - Name"] == "" and r["Close out Approval - Position"] == ""

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
```

Existing tests to update:
- In `test_every_rec_has_a_closeout_row_and_registry_text`, the status check becomes `assert c["Schedule Status"] in STATUSES`. `Date Completed` is parsed only when non-empty.
- `test_schedule_status_matches_the_date_comparison` is replaced by `test_schedule_status_follows_as_of`.
- In `tests/test_scenario_incidents.py::test_date_chain_orders_and_derives_from_the_plan`, assert the report and approval differences only when the cell is non-empty.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_scenario_generate.py -q`
Expected:
- FAIL on `test_schedule_status_follows_as_of`: today's values are `On Schedule` and `Behind`.
- FAIL on `test_no_generated_date_after_as_of`: dates after 2025-12-31 exist today.

- [ ] **Step 3: Implement** the table above in `scenario.py`, plus `"as_of": AS_OF.isoformat()` in `build_manifest`'s return dict.

- [ ] **Step 4: Regenerate the committed registers**

Run: `for c in alder hazel hornbeam; do uv run python -m psm.scenario $c; done`
Expected: three `wrote data/companies/<c>: {...}` lines, with row counts unchanged from before. Check with `git diff --stat data/companies`: row counts must not change, and the `wc -l` of every CSV is equal before and after.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: everything passes, with the count at 942 + new tests, and 2 skipped.
- `test_committed_register_is_byte_identical_to_a_fresh_generate` must pass. If it fails, the regeneration was not from the current code.
- If a `tests/test_scenarios.py` planted-pattern or analytic-overdue assertion fails, stop: report BLOCKED with the measured KPI values and do not edit the test.
- Other expected breakages: tests outside `test_scenario*` / `test_kpi` that read company closeout values, such as `tests/test_participant_guide.py::test_track_6_gaps_exist_in_every_company`. That one is Task 3's. Mark it with `@pytest.mark.xfail(reason="Task 3 rewrites Track 6", strict=True)` in this commit, and list any other breakage in the report.

- [ ] **Step 6: Commit**

```bash
git add src/psm/scenario.py tests/test_scenario_generate.py tests/test_scenario_incidents.py tests/test_participant_guide.py data/companies
git commit -m "feat(scenario): registers are as of 2025-12-31; open actions and workbook Schedule Status values"
```

### Task 3: Guide Track 6, findings entry, bundle check

**Files:**
- Modify: `docs/release/PARTICIPANT_GUIDE.md:136`
- Modify: `tests/test_participant_guide.py:660-673`. Remove the Task 2 xfail.
- Append: `docs/findings.md`

**Interfaces:**
- Consumes: the regenerated registers (Task 2), `sc.AS_OF`.

- [ ] **Step 1: Rewrite the test** `test_track_6_gaps_exist_in_every_company` as below.
  - In the Tracks section, assert `"open or late actions"` and `f"registers end {sc.AS_OF.isoformat()}"` are present and `'"Behind"'` is absent.
  - For every company in the built bundle `b`, join `recommendations.csv` to `closeout.csv` on (`Incident Number`, `Recommendation Number`).
  - Assert at least one open action (empty `Date Completed`).
  - Assert at least one late action (`Date Completed` > `Agreed Completion Date`).
  - Keep `assert inc - rec, co`.
  - Update the docstring to name the new example.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_participant_guide.py::test_track_6_gaps_exist_in_every_company -q`
Expected: FAIL on the guide phrase.

- [ ] **Step 3: Edit the guide.** In Track 6, replace `such as close-outs (records of closing a recommendation) marked "Behind".` with `such as open or late actions (registers end 2025-12-31).` That is 8 words to 7.

- [ ] **Step 4: Run the guide tests**

Run: `uv run pytest tests/test_participant_guide.py -q`
Expected: all pass, including the word-cap tests (template under 1350, every stamped mode ≤ 1600).
If the bundle assertion finds no open action in a company, stop and report BLOCKED with the per-company counts. Don't change the guide wording to hide it.

- [ ] **Step 5: Append a findings entry** to `docs/findings.md` titled `## 2026-10-25 — Company registers as of 2025-12-31`. It states, as a checkable stranger-facing record:
  - **What changed:** the as-of rule table from Task 2, and the workbook's Schedule Status values with their source (`schema/e19_labels.yaml`).
  - **Totals across all three companies:** open actions, open and past due, and blanked report, approval and close-out dates. Give totals only, no per-company split.
  - **Known limit:** `median_closeout_days` now excludes open actions (right-censored), so it understates a slow closer.
  - **What was verified, by what command:** the full suite count, and that no draw changed. For the draws, compare `Date of Incident`, `Agreed Completion Date` and every non-date column before and after regeneration: `git diff 10ee5dc -- data/companies` shows changes only in `Date of Report`, `Approval Date`, `Close out Date`, `Date Completed`, `Schedule Status`, the blanked approver and close-out people cells, their provenance cells, and `manifest.json`. State the command used.
  - **Recommendation-text item 2.9 is deferred.** Say why: 949 of 1,230 real recommendations are written in the regulator's voice, so few company incidents have an operator-voice one to reuse. Say where linked real recommendations are: `core/real_only/recommendations.csv`.

- [ ] **Step 6: Run the full suite**, then commit.

Run: `uv run pytest -q`
Expected: all pass, 2 skipped, no xfail left.

```bash
git add docs/release/PARTICIPANT_GUIDE.md tests/test_participant_guide.py docs/findings.md
git commit -m "docs(guide): Track 6 names open or late actions as of the register date"
```

### Task 4: Rebuild the bundles, update the brief and the notebook check (controller, not committed)

**Files:**
- Modify (gitignored): `deliverables/challenge/psm-challenge-brief-draft-2026-10-25.md` (Advisor route, and the organiser check at lines 110-111)
- Build to the scratchpad: four bundles at the new HEAD

- [ ] **Step 1: Edit the brief.**
  - After the Advisor route's last sentence (line 47), add: `For real recommendations linked to their own incident, use `core/real_only/recommendations.csv`; the company recommendations are generic templates.`
  - Replace the organiser check at lines 110-111 with: `- The brief names no planted pattern, and does not quote the guide's Track 6 example.`

- [ ] **Step 2: Build all four modes** from the new HEAD, using `bundle.build_bundle(out_root, include_companies=…, text_light=…, allow_dirty=False, allow_placeholder_terms=False)` for (1,0), (1,1), (0,0), (0,1). Put each in `<scratchpad>/rc-<short-sha>/c{c}t{t}/`.
Expected: four `psm-hackathon-v1-<sha>.zip`. Run `diff -r` against the `rc-10ee5dc` folders. Differences should be limited to:
  - the company tables and their provenance;
  - README_FIRST (the Track 6 line and the stamp);
  - BASELINE (the stamp only);
  - SHA256SUMS;
  - possibly `core/data_dictionary.csv`, if it samples Schedule Status values.
  List anything else.

- [ ] **Step 3: Run the starter notebook** against each bundle.

Run: `uv run --no-project --with nbformat --with nbconvert --with ipykernel --with pandas --with scikit-learn jupyter nbconvert --to notebook --execute` on a copy of `deliverables/challenge/psm_starter.ipynb` placed next to each unzipped bundle.
Expected: exit 0 in all four modes. Section 5's token table now shows `(empty)` for open `Date Completed` cells.

- [ ] **Step 4: Ledger.** Record SHA256 and size for each zip in the plan's SDD ledger. Note that the Colab copy's outputs are from the old bundle, so it needs a re-run after the Nov 2 gate, not now.
