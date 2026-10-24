"""Personal-info review sample: the cells a human reads before the release data stop.

Reads a built bundle (so the text is post-redaction, as shipped) and writes one CSV of
cells to read, one row per (cell, reason). The check itself is human; this only picks cells.

Reasons:
  random            ``n_random`` narrative cells ranked by sha256(f"{seed}|{file}|{row}|{col}")
  name_redacted, death_mention, near_fatal, contact_redacted
                    every such row of the bundle's ``core/content_flags.csv``
  capitalised_pair  a Capitalised Capitalised pair within 40 characters of a role word

``serious_injury`` flags are not a reason. Narrative columns and row keys come from
``psm.bundle`` (``FLAG_COLS``, ``KEY_COLS``); the table kind of a bundle file comes from
``psm.bundle._specs``. Output is sorted by (reason, file, row_key, column), so the same
bundle gives byte-identical bytes.

Run::

    uv run python -m psm.review_pack <bundle_dir> [--n-random 150] [--out PATH]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
from pathlib import Path

from psm.bundle import FLAG_COLS, KEY_COLS, REPO, _specs

csv.field_size_limit(10**8)

COLUMNS = ["file", "Incident Number", "row_key", "column", "reason", "text"]
FLAG_REASONS = ("name_redacted", "death_mention", "near_fatal", "contact_redacted")
REASONS = ("random", *FLAG_REASONS, "capitalised_pair")
DEFAULT_OUT = REPO / "deliverables/release/review_sample.csv"
FLAGS_REL = "core/content_flags.csv"
GAP = 40

_PAIR_RE = re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b")
_TRIGGER_RE = re.compile(r"\b(?:injured|employee|supervisor|operator|crane operator|worker)\b", re.I)
_TRIGGER_IP_RE = re.compile(r"\bIP\b")           # case-sensitive: "ip" is not the injured person


def capitalised_pair(text: str) -> bool:
    """True when a Capitalised Capitalised pair lies within 40 characters of a role word,
    on either side. The gap is measured between the two spans; overlapping spans don't count."""
    pairs = [m.span() for m in _PAIR_RE.finditer(text)]
    if not pairs:
        return False
    trig = [m.span() for rx in (_TRIGGER_RE, _TRIGGER_IP_RE) for m in rx.finditer(text)]
    for ps, pe in pairs:
        for ts, te in trig:
            if ts >= pe:
                gap = ts - pe
            elif te <= ps:
                gap = ps - te
            else:
                continue
            if gap <= GAP:
                return True
    return False


def _read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def _cells(bundle_dir: Path) -> dict[tuple[str, str, str], tuple[str, str]]:
    """Every non-empty narrative cell of every shipped table:
    (file, row_key, column) -> (Incident Number, text)."""
    kinds = {sp.rel: sp.kind for sp in _specs(include_companies=True)}
    out: dict[tuple[str, str, str], tuple[str, str]] = {}
    for rel in sorted(kinds):
        path = bundle_dir / rel
        if not path.is_file():
            continue
        kind = kinds[rel]
        cols, rows = _read_csv(path)
        narrative = [c for c in FLAG_COLS.get(kind, ()) if c in cols]
        if not narrative:
            continue
        for row in rows:
            key = "|".join(row[k] for k in KEY_COLS[kind])
            for c in narrative:
                if row[c]:
                    out[(rel, key, c)] = (row.get("Incident Number", ""), row[c])
    return out


def _rank(seed_text: str, file: str, row: str, col: str) -> tuple[str, str, str, str]:
    h = hashlib.sha256(f"{seed_text}|{file}|{row}|{col}".encode("utf-8")).hexdigest()
    return (h, file, row, col)


def build_review_pack(bundle_dir: Path, n_random: int = 150, seed_text: str = "review-v1", *,
                      out_path: Path | None = None) -> Path:
    bundle_dir = Path(bundle_dir)
    out_path = Path(out_path) if out_path is not None else DEFAULT_OUT
    flags_path = bundle_dir / FLAGS_REL
    if not flags_path.is_file():
        raise SystemExit(f"not a built bundle: {flags_path} is missing")
    cells = _cells(bundle_dir)

    picked: set[tuple[str, str, str, str]] = set()          # (reason, file, row_key, column)

    pool = sorted(cells, key=lambda k: _rank(seed_text, *k))
    picked |= {("random", *k) for k in pool[:max(n_random, 0)]}

    _, flag_rows = _read_csv(flags_path)
    for f in flag_rows:
        if f["flag"] not in FLAG_REASONS:
            continue
        k = (f["table"], f["row_key"], f["column"])
        if k not in cells:
            raise SystemExit(f"{FLAGS_REL}: flagged cell not found in the bundle: "
                             f"{f['table']} row_key={f['row_key']!r} column={f['column']!r}")
        picked.add((f["flag"], *k))

    picked |= {("capitalised_pair", *k) for k, (_, text) in cells.items() if capitalised_pair(text)}

    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for reason, file, row_key, col in sorted(picked):
        inc, text = cells[(file, row_key, col)]
        w.writerow([file, inc, row_key, col, reason, text])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(buf.getvalue().encode("utf-8"))
    return out_path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Write the personal-info review sample for a bundle.")
    ap.add_argument("bundle_dir", type=Path)
    ap.add_argument("--n-random", type=int, default=150)
    ap.add_argument("--seed-text", default="review-v1")
    ap.add_argument("--out", type=Path, default=None,
                    help=f"default: {DEFAULT_OUT.relative_to(REPO)}")
    args = ap.parse_args(argv)
    out = build_review_pack(args.bundle_dir, args.n_random, args.seed_text, out_path=args.out)
    print(f"review sample {out}")


if __name__ == "__main__":
    main()
