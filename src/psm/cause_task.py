"""Leak-free cause-classification task file.

A BSEE cause statement opens with the category (and usually the subcategory)
that *is* the label, so a model can read the answer off the first words. This
module writes ``data/processed/e19/cause_task.csv``: one row per labelled cause
in ``real_only/causes.csv``, with that opening removed
(:func:`psm.causes.strip_category`) and a grouped fold column so an entrant can
cross-validate without splitting an incident across train and test.

Columns carry the repo's provenance prefixes. ``Incident Number`` and
``Cause number`` are the E19 join keys and keep their exact labels.

Rows excluded, and counted:

* ``multi_category`` -- the statement names a second category head after a
  sentence or dash boundary, so one row would carry two labels' text;
* ``too_short`` -- fewer than 3 words remain once the opening is stripped.

Run::

    uv run python -m psm.cause_task
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import yaml

from psm.causes import (
    SEP_RE,
    normalise_category,
    parse_statement,
    strip_category,
    subcategory_counts,
)

REPO = Path(__file__).resolve().parents[2]
E19 = REPO / "data" / "processed" / "e19"
REAL_ONLY = E19 / "real_only"
CROSSWALK = REPO / "schema" / "crosswalk.yaml"
DEFAULT_OUT = E19 / "cause_task.csv"

PSM_COLUMN = " Failed PSM Framework Element"
FOLD_SALT = "cause-task-v1"
N_FOLDS = 5
MIN_WORDS = 3

TASK_COLUMNS = ("Incident Number", "Cause number", "src_year", "src_category",
                "src_text_stripped", "xw_element", "syn_regime", "syn_fold")


def fold_of(incident: str) -> str:
    """Grouped fold: every cause of an incident lands in the same fold."""
    digest = hashlib.sha256(f"{incident}|{FOLD_SALT}".encode()).hexdigest()
    return str(int(digest[:8], 16) % N_FOLDS)


def _crosswalk() -> tuple[dict[str, str], dict[str, str]]:
    """``(alias -> canonical category, canonical category -> element)``."""
    spec = yaml.safe_load(CROSSWALK.read_text(encoding="utf-8"))
    aliases = {k.lower(): v for k, v in (spec.get("aliases") or {}).items()}
    elements = {c: str(v["primary_element"]) for c, v in spec["categories"].items()}
    return aliases, elements


def _second_head_re(names) -> re.Pattern:
    """A category name or alias opening a clause, followed by a separator."""
    alt = "|".join(re.escape(n) for n in sorted(names, key=lambda s: (-len(s), s)))
    return re.compile(
        rf"(?:^|(?<=[.!?])\s+|\s[–—-]\s|[:–—]\s*)(?:[•●▪·*]\s*)?(?:{alt})\s*(?:[:–—]|-(?=\s))",
        re.IGNORECASE)


def _read(path: Path) -> list[dict]:
    csv.field_size_limit(10 ** 9)
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _build(real_only_dir: Path) -> tuple[list[dict], Counter]:
    aliases, elements = _crosswalk()
    second_head = _second_head_re(set(aliases) | {c.lower() for c in elements})
    counts = subcategory_counts(real_only_dir / "causes.csv")

    ids = json.loads((real_only_dir / "splits.json").read_text(encoding="utf-8"))["incident_ids"]
    regime = {inc: reg for reg, lst in ids.items() for inc in lst}
    year = {}
    for r in _read(real_only_dir / "incidents.csv"):
        m = re.match(r"(\d{4})-", r.get("Date of Incident", "") or "")
        year[r["Incident Number"]] = m.group(1) if m else ""

    rows: list[dict] = []
    excluded: Counter = Counter()
    for c in _read(real_only_dir / "causes.csv"):
        if not (c[PSM_COLUMN] or "").strip():
            continue
        raw = c["Cause Description"] or ""
        st = parse_statement(raw)
        head = st.category
        canon = aliases.get(normalise_category(head or ""))
        if canon is None:
            raise ValueError(
                f"{c['Incident Number']} #{c['Cause number']}: labelled cause whose head "
                f"is not in crosswalk.yaml aliases")
        element = elements[canon]
        if element != c[PSM_COLUMN].strip():
            raise ValueError(
                f"{c['Incident Number']} #{c['Cause number']}: crosswalk element "
                f"{element} != labelled {c[PSM_COLUMN]!r}")

        sep = SEP_RE.search(st.raw)
        tail = st.raw[sep.end():] if sep else ""
        text = strip_category(raw, counts)
        multi = bool(second_head.search(tail)) or bool(second_head.match(text))
        short = len(text.split()) < MIN_WORDS
        if multi:
            excluded["multi_category"] += 1
        if short:
            excluded["too_short"] += 1
        if multi or short:
            excluded["rows_excluded"] += 1
            if multi and short:
                excluded["both"] += 1
            continue

        inc = c["Incident Number"]
        rows.append({
            "Incident Number": inc,
            "Cause number": c["Cause number"],
            "src_year": year.get(inc, ""),
            "src_category": normalise_category(head),
            "src_text_stripped": text,
            "xw_element": element,
            "syn_regime": regime.get(inc, ""),
            "syn_fold": fold_of(inc),
        })
    return rows, excluded


def build_cause_task(real_only_dir: Path) -> list[dict]:
    """Task rows from the labelled causes of ``real_only_dir``."""
    return _build(Path(real_only_dir))[0]


def write_task(rows: list[dict], path: Path) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(TASK_COLUMNS))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    rows, excluded = _build(REAL_ONLY)
    write_task(rows, DEFAULT_OUT)
    m6 = [r for r in rows if r["syn_regime"] == "modern_six"]
    print(f"wrote {len(rows)} rows ({len(m6)} modern_six) -> {DEFAULT_OUT}")
    print("excluded:", dict(sorted(excluded.items())))


if __name__ == "__main__":
    main()
