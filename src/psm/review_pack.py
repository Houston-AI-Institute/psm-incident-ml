"""Personal-info review sample: the cells a human reads before the release data stop.

Reads a built bundle (so the text is post-redaction, as shipped) and writes one CSV of
cells to read, one row per (cell, reason). The check itself is human; this only picks cells.

Reasons:
  random            ``n_random`` narrative cells ranked by sha256(f"{seed}|{file}|{row}|{col}")
  name_redacted, death_mention, near_fatal, contact_redacted
                    every such row of the bundle's ``core/content_flags.csv``
  capitalised_pair  one row per DISTINCT Capitalised Capitalised pair within 40 characters of
                    a role word (injured, employee, supervisor, operator, worker, inspector,
                    investigator, team, email, or the IP), at least one of whose words never
                    appears in lower case in the narrative pool; ``text`` is a +-60 character window around the first
                    occurrence and ``n_cells`` counts the cells where the pair occurs near a role
  capitalised_pair_common
                    the same, for pairs whose two words both appear in lower case in the pool
  Pairs overlap ("Then Mark Smith" gives "Then Mark" and "Mark Smith"); each distinct pair is
  one row. Any whitespace run (including a newline) may separate the two words; the pair key
  is normalised to one space, while ``text`` keeps the cell's own characters. The pair regex
  misses all-caps, McX, hyphenated, initial-only and single-word names; only the random
  sample covers those.

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

COLUMNS = ["file", "Incident Number", "row_key", "column", "reason", "text", "n_cells"]
FLAG_REASONS = ("name_redacted", "death_mention", "near_fatal", "contact_redacted")
REASONS = ("random", *FLAG_REASONS, "capitalised_pair", "capitalised_pair_common")
DEFAULT_OUT = REPO / "deliverables/release/review_sample.csv"
FLAGS_REL = "core/content_flags.csv"
GAP = 40
WINDOW = 60

# Lookahead: overlapping pairs are all found ("Then Mark Smith" gives "Then Mark" and "Mark Smith").
# Any whitespace run joins the two words, so a name split across a newline is still a pair.
_PAIR_RE = re.compile(r"\b(?=([A-Z][a-z]+\s+[A-Z][a-z]+)\b)")
_TRIGGER_RE = re.compile(
    r"\b(?:injured|employee|supervisor|operator|crane operator|worker"
    r"|inspector|investigator|team|email)\b", re.I)
_TRIGGER_IP_RE = re.compile(r"\bIP\b")           # case-sensitive: "ip" is not the injured person
_LOWER_WORD_RE = re.compile(r"\b[a-z]+\b")


def near_pairs(text: str) -> list[tuple[int, int]]:
    """Spans of every Capitalised Capitalised pair within 40 characters of a role word, on
    either side. Pairs may overlap each other. The gap is measured between the pair and the
    role word; a role word that overlaps the pair doesn't count."""
    pairs = [m.span(1) for m in _PAIR_RE.finditer(text)]
    if not pairs:
        return []
    trig = [m.span() for rx in (_TRIGGER_RE, _TRIGGER_IP_RE) for m in rx.finditer(text)]
    out = []
    for ps, pe in pairs:
        for ts, te in trig:
            if ts >= pe:
                gap = ts - pe
            elif te <= ps:
                gap = ps - te
            else:
                continue
            if gap <= GAP:
                out.append((ps, pe))
                break
    return out


def capitalised_pair(text: str) -> bool:
    return bool(near_pairs(text))


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
    if not cells:
        raise SystemExit(f"no narrative cells to review in {bundle_dir} "
                         "(text-light bundle, or no narrative table found)")

    rows: list[tuple] = []        # (reason, file, row_key, column, Incident Number, text, n_cells)

    pool = sorted(cells, key=lambda k: _rank(seed_text, *k))
    for k in pool[:max(n_random, 0)]:
        rows.append(("random", *k[:3], cells[k][0], cells[k][1], ""))

    _, flag_rows = _read_csv(flags_path)
    seen: set[tuple[str, str, str, str]] = set()
    for f in flag_rows:
        if f["flag"] not in FLAG_REASONS:
            continue
        k = (f["table"], f["row_key"], f["column"])
        if k not in cells:
            raise SystemExit(f"{FLAGS_REL}: flagged cell not found in the bundle: "
                             f"{f['table']} row_key={f['row_key']!r} column={f['column']!r}")
        if (f["flag"], *k) not in seen:
            seen.add((f["flag"], *k))
            rows.append((f["flag"], *k, cells[k][0], cells[k][1], ""))

    # capitalised pairs: one row per distinct pair string, first occurrence in cell sort order
    vocab = {w for _, text in cells.values() for w in _LOWER_WORD_RE.findall(text)}
    first: dict[str, tuple[tuple[str, str, str], int, int]] = {}
    where: dict[str, set[tuple[str, str, str]]] = {}
    for k in sorted(cells):
        text = cells[k][1]
        for s, e in near_pairs(text):
            pair = " ".join(text[s:e].split())     # "A\nB" and "A B" are one pair
            first.setdefault(pair, (k, s, e))
            where.setdefault(pair, set()).add(k)
    for pair, (k, s, e) in first.items():
        text = cells[k][1]
        reason = ("capitalised_pair_common" if all(w.lower() in vocab for w in pair.split())
                  else "capitalised_pair")
        rows.append((reason, *k, cells[k][0], text[max(0, s - WINDOW):e + WINDOW], str(len(where[pair]))))

    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for reason, file, row_key, col, inc, text, n in sorted(rows):
        w.writerow([file, inc, row_key, col, reason, text, n])
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
