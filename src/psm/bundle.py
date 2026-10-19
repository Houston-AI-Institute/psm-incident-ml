"""Allowlist release bundle for the hackathon: the minor-safe CSV set participants get.

Only the files named in the allowlist (``_specs``) are read, transformed and written;
nothing else under ``data/`` can reach the bundle. Transforms, in this order:

1. fatal drop    rows of every fatality incident (and every ``bundle_exclusions.yaml``
                 incident, and every company incident minted from a dropped donor) are
                 removed from every table of their scope; provenance files and
                 ``splits.json`` follow the same mask;
2. flags         ``content_flags.csv``, over all bundled text, after the drop and before
                 redaction (only columns that ship are scanned);
3. redact        titled names and contact details in free-text cells whose provenance
                 token is ``src`` or ``mod`` (a table with no provenance file counts as
                 ``src``); a changed cell becomes ``mod`` or, with no provenance file,
                 a row in ``redactions.csv``;
4. reduce        withheld columns and the ``bsee_unmapped`` reduction.

The builder refuses to run on a dirty or untracked input tree, and while
``docs/release/TERMS.md`` is a placeholder. Time comes only from the commit
(``git log -1 --format=%ct``), converted with ``time.gmtime``; the output is
byte-identical for the same commit and mode.

Run::

    uv run python -m psm.bundle [--no-companies] [--text-light]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
import tempfile
import time
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from psm import scenario
from psm.baseline import regime_summary, render, run_baseline
from psm.content import (CONTACT_RE, HONORIFIC_RE, fatal_incidents, flags, redact,
                         titled_name_words)
from psm.provenance import TOKENS

csv.field_size_limit(10**8)

REPO = Path(__file__).resolve().parents[2]
VERSION = "v1"
E19 = "data/processed/e19"
SCHEMA = "schema"
GUIDE = "docs/release/PARTICIPANT_GUIDE.md"
TERMS = "docs/release/TERMS.md"
NOTES = "schema/data_dictionary_notes.yaml"
EXCLUSIONS = "schema/bundle_exclusions.yaml"
DISPOSITION = "schema/e19_disposition.yaml"
SPLITS = f"{E19}/real_only/splits.json"
PLACEHOLDER_FIRST_LINE = "PLACEHOLDER — terms pending organiser"

# Columns withheld from incidents tables (and their provenance files) until Task 10 is
# committed; Task 10 empties the set.
DROP_COLUMNS = frozenset({"Description"})

NARRATIVE = (
    "What happened?  ", "What was the outcome?", "How did the incident occur",
    "Description", "incident Title", "Detail",
    "What was the worst outcome that could reasonably be expected to have happened?",
)
BSEE_KEEP = ("Incident Number", "bsee_operator", "bsee_water_depth", "bsee_panel_formed",
             "bsee_source_url")

# Free-text columns by table kind. recommendations: only "Recommendation Description" is
# free text (Responsible Owner - Name/Position are pseud/syn or empty everywhere).
# closeout: Schedule Status and Date Completed are categorical/date, so no free text.
REDACT_COLS = {
    "incidents": NARRATIVE,
    "causes": ("Cause Description",),
    "cause_task": ("src_text_stripped",),
    "recommendations": ("Recommendation Description",),
    "closeout": (),
}
FLAG_COLS = {**REDACT_COLS, "bsee_unmapped": ("bsee_operator",)}
KEY_COLS = {
    "incidents": ("Incident Number",),
    "bsee_unmapped": ("Incident Number",),
    "causes": ("Incident Number", "Cause number"),
    "cause_task": ("Incident Number", "Cause number"),
    "aux_cause": ("Incident Number", "Cause number"),
    "recommendations": ("Incident Number", "Recommendation Number"),
    "closeout": ("Incident Number", "Recommendation Number"),
}
DISPOSITION_KIND = {"incidents": "incidents", "causes": "causes",
                    "recommendations": "recommendations", "closeout": "closeout"}

TOKEN_ORDER = ("src", "xw", "llm", "gold", "syn", "key", "pseud", "mod")
assert set(TOKEN_ORDER) | {""} == set(TOKENS), "provenance tokens changed: update TOKEN_ORDER"

SCHEMA_FILES = ("crosswalk", "e19_disposition", "synth_rules", "xw_cause_qualifiers",
                "xw_consequence_tiers", "xw_incident_type", "xw_outcome")
ZIP_MIN_YEAR = 1980


@dataclass
class Spec:
    rel: str                 # path inside the bundle
    src: str                 # repo-relative source
    kind: str
    scope: str = "core"      # "core" or a company slug
    prov_rel: str | None = None
    prov_src: str | None = None


def _specs(include_companies: bool) -> list[Spec]:
    s = []
    for n in ("incidents", "causes", "recommendations", "closeout"):
        s.append(Spec(f"core/real_only/{n}.csv", f"{E19}/real_only/{n}.csv", n))
    s.append(Spec("core/enriched/incidents.csv", f"{E19}/enriched/incidents.csv", "incidents",
                  prov_rel="core/enriched/provenance.csv", prov_src=f"{E19}/enriched/provenance.csv"))
    s.append(Spec("core/enriched/causes.csv", f"{E19}/enriched/causes.csv", "causes",
                  prov_rel="core/enriched/causes_provenance.csv",
                  prov_src=f"{E19}/enriched/causes_provenance.csv"))
    s.append(Spec("core/cause_task.csv", f"{E19}/cause_task.csv", "cause_task"))
    s.append(Spec("advanced/causes_confidence.csv", f"{E19}/enriched/causes_confidence.csv", "aux_cause"))
    s.append(Spec("advanced/causes_secondary_element.csv",
                  f"{E19}/enriched/causes_secondary_element.csv", "aux_cause"))
    s.append(Spec("advanced/causes_source_field.csv", f"{E19}/causes_source_field.csv", "aux_cause"))
    s.append(Spec("advanced/bsee_unmapped.csv", f"{E19}/bsee_unmapped.csv", "bsee_unmapped"))
    if include_companies:
        for co in scenario.COMPANY_ORDER:
            for n, p in (("incidents", "provenance"), ("causes", "causes_provenance"),
                         ("recommendations", "recommendations_provenance"),
                         ("closeout", "closeout_provenance")):
                s.append(Spec(f"companies/{co}/{n}.csv", f"data/companies/{co}/{n}.csv", n, scope=co,
                              prov_rel=f"companies/{co}/{p}.csv", prov_src=f"data/companies/{co}/{p}.csv"))
    return s


def source_paths(specs: list[Spec]) -> list[str]:
    """Every repo file the bundle reads, sorted."""
    out = {SPLITS, GUIDE, TERMS, NOTES, EXCLUSIONS, DISPOSITION}
    out |= {f"{SCHEMA}/{n}.yaml" for n in SCHEMA_FILES}
    for sp in specs:
        out.add(sp.src)
        if sp.prov_src:
            out.add(sp.prov_src)
    return sorted(out)


# ---- guards ----------------------------------------------------------------

def _run_git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout


def input_problems(repo: Path, sources: list[str]) -> list[str]:
    """Why the inputs cannot be trusted to equal HEAD; empty when they can.

    Names only the source files the bundle reads (plus src, schema, docs/release), so
    untracked scratch folders elsewhere under data/ do not trip it.
    """
    problems: list[str] = []
    status = _run_git(repo, "status", "--porcelain", "--untracked-files=all", "--",
                      "src", "schema", "docs/release", *sources).decode("utf-8")
    problems += [ln for ln in status.splitlines() if ln.strip()]
    tracked = set(_run_git(repo, "ls-files", "-z").decode("utf-8").split("\0"))
    for rel in sources:
        if rel not in tracked:
            problems.append(f"not tracked by git: {rel}")
            continue
        path = repo / rel
        if not path.is_file():
            problems.append(f"missing from the working tree: {rel}")
            continue
        try:
            head = _run_git(repo, "show", f"HEAD:{rel}")
        except subprocess.CalledProcessError:
            problems.append(f"not in HEAD: {rel}")
            continue
        if path.read_bytes() != head:
            problems.append(f"differs from HEAD: {rel}")
    return problems


def require_clean_inputs(repo: Path, sources: list[str]) -> None:
    problems = input_problems(repo, sources)
    if problems:
        raise SystemExit("refusing to build: inputs are not the committed tree:\n  "
                         + "\n  ".join(problems))


def terms_text(repo: Path = REPO) -> str:
    return (repo / TERMS).read_text(encoding="utf-8")


def check_terms(text: str, allow_placeholder: bool) -> None:
    first = text.split("\n", 1)[0].strip()
    if first == PLACEHOLDER_FIRST_LINE and not allow_placeholder:
        raise SystemExit(f"refusing to build: {TERMS} is still the placeholder "
                         "(the organiser's terms have not been supplied)")


def load_exclusions(repo: Path = REPO) -> list[str]:
    data = yaml.safe_load((repo / EXCLUSIONS).read_text(encoding="utf-8")) or {}
    return [str(i) for i in (data.get("incidents") or [])]


# ---- tables ----------------------------------------------------------------

@dataclass
class Tbl:
    spec: Spec
    cols: list[str]
    rows: list[dict]
    prov: list[dict] | None = None
    n_in: int = 0
    redacted: Counter = field(default_factory=Counter)
    withheld: list[str] = field(default_factory=list)

    @property
    def rel(self) -> str:
        return self.spec.rel

    @property
    def kind(self) -> str:
        return self.spec.kind

    @property
    def dropped(self) -> int:
        return self.n_in - len(self.rows)


def _read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = list(reader.fieldnames or [])
        return cols, list(reader)


def _csv_bytes(cols: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([r[c] for c in cols])
    return buf.getvalue().encode("utf-8")


def _load(repo: Path, sp: Spec) -> Tbl:
    cols, rows = _read_csv(repo / sp.src)
    prov = None
    if sp.prov_src:
        pcols, prov = _read_csv(repo / sp.prov_src)
        if pcols != cols or len(prov) != len(rows):
            raise SystemExit(f"{sp.src}: provenance file does not match its value table in shape")
        bad = {v for p in prov for v in p.values()} - TOKENS
        if bad:
            raise SystemExit(f"{sp.prov_src}: unknown provenance tokens {sorted(bad)}")
    return Tbl(sp, cols, rows, prov, n_in=len(rows))


def _donor_map(company: str) -> dict[str, str]:
    """Company Incident Number -> real donor Incident Number, from psm.scenario's own
    functions (the partition and the id mint), never by matching text."""
    return {scenario.scenario_incident_number(company, d): d
            for d in scenario.donor_partition(company)}


def _fatal_sets(tables: dict[str, Tbl], exclusions: list[str],
                include_companies: bool) -> dict[str, set[str]]:
    enriched = tables["core/enriched/incidents.csv"].rows
    real = tables["core/real_only/incidents.csv"].rows
    known = {r["Incident Number"] for r in enriched}
    unknown = sorted(set(exclusions) - known)
    if unknown:
        raise SystemExit(f"{EXCLUSIONS}: not enriched incidents: {unknown}")
    core = fatal_incidents(enriched) | fatal_incidents(real) | set(exclusions)
    out = {"core": core}
    if include_companies:
        for co in scenario.COMPANY_ORDER:
            rows = tables[f"companies/{co}/incidents.csv"].rows
            ids = {r["Incident Number"] for r in rows}
            dm = _donor_map(co)
            if not set(dm) <= ids:
                raise SystemExit(f"{co}: the donor map names {len(set(dm) - ids)} ids that are not "
                                 "company incidents; cannot drop donor-linked rows safely")
            out[co] = fatal_incidents(rows) | {s for s, d in dm.items() if d in core}
    return out


def _drop_rows(t: Tbl, ids: set[str]) -> None:
    keep = [r["Incident Number"] not in ids for r in t.rows]
    t.rows = [r for r, k in zip(t.rows, keep) if k]
    if t.prov is not None:
        t.prov = [p for p, k in zip(t.prov, keep) if k]      # provenance stores `key`, so by position


def transform_splits(text: str, drop: set[str]) -> str:
    d = json.loads(text)
    for reg, ids in d["incident_ids"].items():
        d["incident_ids"][reg] = [i for i in ids if i not in drop]
        d["regimes"][reg]["n_incidents"] = len(d["incident_ids"][reg])
    return json.dumps(d, indent=2, ensure_ascii=False)     # the source has no trailing newline


def parse_panel_formed(text: str) -> str:
    """YES, NO or blank. The raw cell is the form label plus whatever followed it
    (often a person's name); only an unambiguous upper-case YES/NO survives."""
    found = set(re.findall(r"\b(YES|NO)\b", text))
    return found.pop() if len(found) == 1 else ""


def _drop_cols(kind: str, text_light: bool) -> set[str]:
    drop: set[str] = set()
    if kind == "incidents":
        drop |= DROP_COLUMNS
        if text_light:
            drop |= set(NARRATIVE)
    elif text_light and kind in ("causes", "cause_task", "recommendations"):
        drop |= set(REDACT_COLS[kind])
    return drop


def _shipped_cols(t: Tbl, text_light: bool) -> list[str]:
    if t.kind == "bsee_unmapped":
        missing = [c for c in BSEE_KEEP if c not in t.cols]
        if missing:
            raise SystemExit(f"{t.spec.src}: missing columns {missing}")
        return list(BSEE_KEEP)
    drop = _drop_cols(t.kind, text_light)
    return [c for c in t.cols if c not in drop]


def _row_key(t: Tbl, row: dict) -> str:
    return "|".join(row[k] for k in KEY_COLS[t.kind])


def _is_real_text(t: Tbl, i: int, c: str) -> bool:
    """A cell redaction may touch: tagged src or mod, or in a table with no provenance file."""
    return t.prov is None or t.prov[i][c] in ("src", "mod")


def _incident_names(tbls: list[Tbl], cols_of: dict[str, list[str]]) -> dict[str, set[str]]:
    """R27. Per Incident Number, the words that follow a title in ANY redactable free-text
    cell of that incident, across every table of the scope (passed in by the caller)."""
    names: dict[str, set[str]] = {}
    for t in tbls:
        for i, row in enumerate(t.rows):
            for c in cols_of[t.rel]:
                if row[c] and _is_real_text(t, i, c):
                    found = titled_name_words(row[c])
                    if found:
                        names.setdefault(row["Incident Number"], set()).update(found)
    return names


def _redact_table(t: Tbl, cols: list[str], names: dict[str, set[str]] | None = None) -> list[dict]:
    """Redact in place. Returns redactions.csv rows (only for tables with no provenance).
    ``names`` maps Incident Number to the incident's name words (see `_incident_names`);
    without it each cell is redacted on its own words only."""
    out = []
    for i, row in enumerate(t.rows):
        scoped = names.get(row["Incident Number"]) if names else None
        for c in cols:
            old = row[c]
            if not old or not _is_real_text(t, i, c):
                continue                                   # xw/syn/key/pseud/llm pass through
            new = redact(old, scoped) if names is not None else redact(old)
            if new == old:
                continue
            row[c] = new
            t.redacted[c] += 1
            if t.prov is not None:
                t.prov[i][c] = "mod"
                continue
            kinds = [k for k, rx in (("name", HONORIFIC_RE), ("contact", CONTACT_RE)) if rx.search(old)]
            for k in kinds or ["name"]:
                out.append({"table": t.rel, "Incident Number": row["Incident Number"],
                            "row_key": _row_key(t, row), "column": c, "kind": k})
    return out


def redact_scope(tbls: list[Tbl], shipped: dict[str, list[str]]) -> list[dict]:
    """Redact every table of ONE scope (core, or one company), with incident-scoped names."""
    cols_of = {t.rel: [c for c in REDACT_COLS.get(t.kind, ()) if c in shipped[t.rel]] for t in tbls}
    names = _incident_names(tbls, cols_of)
    out = []
    for t in tbls:
        out += _redact_table(t, cols_of[t.rel], names)
    return out


# ---- generated files -------------------------------------------------------

def _cells_long(tables: dict[str, Tbl]) -> list[dict]:
    out = []
    for name, rel in (("incidents", "core/enriched/incidents.csv"), ("causes", "core/enriched/causes.csv")):
        t = tables[rel]
        keys = set(KEY_COLS[t.kind])
        for row, prov in zip(t.rows, t.prov):
            for c in t.cols:
                if c in keys or not row[c]:
                    continue
                out.append({"Incident Number": row["Incident Number"], "table": name,
                            "Cause number": row.get("Cause number", "") if name == "causes" else "",
                            "column": c, "value": row[c], "provenance": prov[c]})
    return out


def _pct(n: int, total: int) -> str:
    return f"{100 * n / total:.1f}" if total else "n/a"


def _data_dictionary(tables: list[Tbl], repo: Path) -> list[dict]:
    disp = yaml.safe_load((repo / DISPOSITION).read_text(encoding="utf-8"))["fields"]
    notes_doc = yaml.safe_load((repo / NOTES).read_text(encoding="utf-8"))
    notes, meanings = notes_doc.get("notes") or {}, notes_doc.get("meanings") or {}
    out = []
    for t in tables:
        for c in [*t.cols, *t.withheld]:
            if t.kind == "bsee_unmapped" and c not in BSEE_KEEP:
                continue
            f = disp.get(DISPOSITION_KIND.get(t.kind, ""), {}).get(c)
            if f:
                meaning = "; ".join(x for x in (
                    f.get("note"), f"disposition: {f['disposition']}",
                    f"gap policy: {f['gap_policy']}" if f.get("gap_policy") else None,
                    f"blank reason: {f['blank_reason']}" if f.get("blank_reason") else None) if x)
            else:
                meaning = meanings.get(c, "")
            row = {"table": t.rel, "column": c, "meaning": meaning}
            shipped = c in t.cols
            if shipped and t.prov is not None:
                counts = Counter(p[c] for p in t.prov)
                total = len(t.prov)
                for tok in TOKEN_ORDER:
                    row[f"pct_{tok}"] = _pct(counts[tok], total)
                row["pct_blank"] = _pct(counts[""], total)
            else:
                for tok in TOKEN_ORDER:
                    row[f"pct_{tok}"] = "n/a"
                row["pct_blank"] = "n/a"
            if not shipped:
                meaning_prefix = "WITHHELD from this release (not in the file)"
                row["meaning"] = "; ".join(x for x in (meaning_prefix, meaning) if x)
            row["notes"] = notes.get(c, "")
            out.append(row)
    return out


DICT_COLUMNS = ["table", "column", "meaning", *[f"pct_{t}" for t in TOKEN_ORDER], "pct_blank", "notes"]


def _fence(text: str, name: str, keep: bool) -> str:
    pat = re.compile(rf"<!-- {name} -->\n?(.*?)<!-- /{name} -->\n?", re.S)
    return pat.sub((lambda m: m.group(1)) if keep else "", text)


def _counts_table(rows: list[tuple[str, int, str, str]], copies: int, listed: int, marked: int) -> str:
    lines = ["| table | rows | dropped (fatality rows) | redacted cells |", "|---|---|---|---|"]
    lines += [f"| `{r[0]}` | {r[1]} | {r[2]} | {r[3]} |" for r in rows]
    lines += [
        "",
        (f"The redacted-cells column counts cell copies: a cell that ships in two tables "
         f"(`real_only` and `enriched`) is counted once per table. Total shipped: {copies} cell "
         f"copies. {listed} of them are in tables with no provenance file and are listed, one row "
         f"per kind, in `core/redactions.csv` (that file has more rows than cells if a cell had "
         f"two kinds); the other {marked} are in tables with a provenance file and are marked "
         f"`mod` there."),
    ]
    return "\n".join(lines)


def _readme(template: str, *, label: str, commit_date: str, counts: str,
            include_companies: bool, text_light: bool) -> str:
    for ph in ("{version}", "{commit}", "{commit_date}", "{counts}"):
        if ph not in template:
            raise SystemExit(f"{GUIDE}: template lacks the placeholder {ph}")
    text = _fence(template, "companies", include_companies)
    text = _fence(text, "text", not text_light)
    return (text.replace("{version}", VERSION).replace("{commit}", label)
            .replace("{commit_date}", commit_date).replace("{counts}", counts))


def _baseline_md(full_task_bytes: bytes, short: str, text_light: bool) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "cause_task.csv"
        p.write_bytes(full_task_bytes)
        res = run_baseline(p)
        others = regime_summary(p)
    text = render(res, short, others)
    note = [
        "## About this copy",
        "",
        (f"Bundle `{short}`. The scores above were measured on the cause task as it ships in "
         "this bundle: rows of fatality incidents are dropped and titled names are redacted, "
         "so the numbers can differ slightly from the repository's `docs/baseline.md`. "
         "The stamp above is this bundle's commit."),
        "",
    ]
    if text_light:
        note += [("This is the text-light bundle: `core/cause_task.csv` here has no "
                  "`src_text_stripped` column, so these scores were measured on the full-text "
                  "version of the same rows, which this bundle does not ship."), ""]
    return text + "\n" + "\n".join(note)


def _commit(repo: Path) -> tuple[str, int]:
    short, ct = _run_git(repo, "log", "-1", "--format=%h%n%ct").decode("utf-8").split()
    return short, int(ct)


def _sha_lines(files: dict[str, bytes]) -> bytes:
    return "".join(f"{hashlib.sha256(files[n]).hexdigest()}  {n}\n"
                   for n in sorted(files)).encode("utf-8")


def _write(out_dir: Path, zip_path: Path, files: dict[str, bytes], gm: time.struct_time) -> None:
    for rel in sorted(files):
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(files[rel])
    date_time = (max(gm.tm_year, ZIP_MIN_YEAR), gm.tm_mon, gm.tm_mday, gm.tm_hour, gm.tm_min, gm.tm_sec)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(files):
            zi = zipfile.ZipInfo(f"{out_dir.name}/{rel}", date_time=date_time)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3
            zi.external_attr = 0o644 << 16
            z.writestr(zi, files[rel])


# ---- the builder -----------------------------------------------------------

def build_bundle(out_root: Path = Path("deliverables/release"),
                 include_companies: bool = True, text_light: bool = False,
                 allow_dirty: bool = False, allow_placeholder_terms: bool = False) -> Path:
    repo = REPO
    check_terms(terms_text(repo), allow_placeholder_terms)
    specs = _specs(include_companies)
    sources = source_paths(specs)
    problems = input_problems(repo, sources)
    if problems and not allow_dirty:
        raise SystemExit("refusing to build: inputs are not the committed tree:\n  "
                         + "\n  ".join(problems))
    short, ct = _commit(repo)
    gm = time.gmtime(ct)
    label = f"{short}+dirty" if problems else short
    out_dir = Path(out_root) / f"psm-hackathon-{VERSION}-{short}"
    zip_path = out_dir.parent / (out_dir.name + ".zip")
    if out_dir.exists() or zip_path.exists():
        raise SystemExit(f"refusing to overwrite {out_dir} / {zip_path}: move them aside first")

    tbls = [_load(repo, sp) for sp in specs]
    by_rel = {t.rel: t for t in tbls}

    # 1. fatal drop
    fatal = _fatal_sets(by_rel, load_exclusions(repo), include_companies)
    for t in tbls:
        _drop_rows(t, fatal[t.spec.scope])

    shipped = {t.rel: _shipped_cols(t, text_light) for t in tbls}
    for t in tbls:
        t.withheld = [c for c in t.cols if c in _drop_cols(t.kind, text_light)]

    # 2. flags (before redaction; shipped columns only)
    flag_rows = []
    for t in tbls:
        cols = [c for c in FLAG_COLS.get(t.kind, ()) if c in shipped[t.rel]]
        if cols:
            flag_rows += flags(t.rel, t.rows, cols, list(KEY_COLS[t.kind]))

    # 3. redact (shipped columns only)
    redaction_rows = []
    for scope in dict.fromkeys(t.spec.scope for t in tbls):
        redaction_rows += redact_scope([t for t in tbls if t.spec.scope == scope], shipped)

    # the full-text cause task, for the baseline (identical to the shipped one unless text-light)
    task = by_rel["core/cause_task.csv"]
    full_rows = task.rows
    if text_light:      # the column is not redacted in place (it does not ship); redact a copy
        full_rows = [dict(r, src_text_stripped=redact(r["src_text_stripped"])) for r in task.rows]
    full_task = _csv_bytes(task.cols, full_rows)

    # 4. column reduction
    for t in tbls:
        keep = shipped[t.rel]
        if t.kind == "bsee_unmapped":
            for r in t.rows:
                r["bsee_panel_formed"] = parse_panel_formed(r["bsee_panel_formed"])
        t.cols = keep

    files: dict[str, bytes] = {}
    for t in tbls:
        files[t.rel] = _csv_bytes(t.cols, t.rows)
        if t.prov is not None:
            files[t.spec.prov_rel] = _csv_bytes(t.cols, t.prov)

    files["core/real_only/splits.json"] = transform_splits(
        (repo / SPLITS).read_text(encoding="utf-8"), fatal["core"]).encode("utf-8")
    for n in SCHEMA_FILES:
        files[f"advanced/schema/{n}.yaml"] = (repo / SCHEMA / f"{n}.yaml").read_bytes()
    files["TERMS.md"] = (repo / TERMS).read_bytes()

    cells = _cells_long(by_rel)
    files["core/cells_long.csv"] = _csv_bytes(
        ["Incident Number", "table", "Cause number", "column", "value", "provenance"], cells)
    files["core/content_flags.csv"] = _csv_bytes(
        ["Incident Number", "table", "row_key", "column", "flag"], flag_rows)
    files["core/redactions.csv"] = _csv_bytes(
        ["table", "Incident Number", "row_key", "column", "kind"], redaction_rows)
    dd = _data_dictionary(tbls, repo)
    files["core/data_dictionary.csv"] = _csv_bytes(DICT_COLUMNS, dd)

    counts_rows = [(t.rel, len(t.rows), str(t.dropped), str(sum(t.redacted.values()))) for t in tbls]
    counts_rows += [(rel, len(r), "-", "-") for rel, r in (
        ("core/cells_long.csv", cells), ("core/content_flags.csv", flag_rows),
        ("core/redactions.csv", redaction_rows), ("core/data_dictionary.csv", dd))]
    files["README_FIRST.md"] = _readme(
        (repo / GUIDE).read_text(encoding="utf-8"), label=label,
        commit_date=time.strftime("%Y-%m-%d", gm),
        counts=_counts_table(
            counts_rows,
            copies=sum(sum(t.redacted.values()) for t in tbls),
            listed=sum(sum(t.redacted.values()) for t in tbls if t.prov is None),
            marked=sum(sum(t.redacted.values()) for t in tbls if t.prov is not None)),
        include_companies=include_companies, text_light=text_light).encode("utf-8")
    files["BASELINE.md"] = _baseline_md(full_task, short, text_light).encode("utf-8")
    files["SHA256SUMS"] = _sha_lines(files)

    out_dir.mkdir(parents=True, exist_ok=False)
    _write(out_dir, zip_path, files, gm)
    return out_dir


def _tree_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Build the allowlist release bundle.")
    ap.add_argument("--no-companies", action="store_true", help="omit the company tables")
    ap.add_argument("--text-light", action="store_true",
                    help="drop every narrative column, Cause Description and src_text_stripped")
    args = ap.parse_args(argv)
    out = build_bundle(out_root=REPO / "deliverables" / "release",
                       include_companies=not args.no_companies, text_light=args.text_light)
    z = out.parent / (out.name + ".zip")
    print(f"bundle {out} ({_tree_size(out)} bytes)")
    print(f"zip    {z} ({z.stat().st_size if z.exists() else 0} bytes)")


if __name__ == "__main__":
    main()
