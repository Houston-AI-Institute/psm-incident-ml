"""Content screen for the public bundle: fatality ids, name/contact redaction,
and per-cell content flags. Pure library, no I/O, no pipeline wiring.

Matched text is never emitted by `flags`; `redact` is the only function that
returns (altered) source text.
"""
import re

_T = r"(?:Mr|Mrs|Ms|Miss|Dr|MR|MRS|MS|MISS|DR)"
_W = r"(?:[A-Z]\.|[A-Z][A-Za-z'’\-]+)"
HONORIFIC_RE = re.compile(rf"\b{_T}\.?\s+{_W}(?:\s+{_W}){{0,2}}")          # case-sensitive
CONTACT_RE = re.compile(r"[\w.]+@[\w.]+\.\w+|\(\d{3}\)\s*\d{3}-\d{4}|\b\d{3}-\d{3}-\d{4}\b")
DEATH_RE = re.compile(r"\b(died|dies|death|deceased|killed|fatal(ly|ity)?|pronounced dead|"
                      r"lost (his|her) life|body was recovered)\b", re.I)
DEATH_EXCLUDE_RE = re.compile(r"\b(well (was )?killed|kill(ed)? the well|kill (weight|mud|line|fluid|pill)|"
                              r"engines? died|(motor|pump|generator) died)\b", re.I)
GRAPHIC_RE = re.compile(r"\b(amputat\w*|severed|crush\w*|decapitat\w*|fractur\w*|lacerat\w*|"
                        r"burns? to (his|her)|degree burns?|impal\w*|deglov\w*|avuls\w*|"
                        r"punctur\w* (lung|\w+ (hand|finger|leg|arm|eye))|"
                        r"broke(n)? (his |her |a |the )?(arm|leg|rib|ribs|pelvis|nose|wrist|ankle|finger|hand|jaw|back|neck)|"
                        r"concussion|aneurysm|skull)\b", re.I)
# Widened under R15: brief had only "found (him|her)? ?(face down|in the water)".
NEAR_FATAL_RE = re.compile(r"\b(unconscious\w*|lost consciousness|not breathing|no pulse|CPR|"
                           r"resuscitat\w*|cardiac arrest|(found|noticed|saw|spotted|observed) (him|her)? ?(face down|in the water))\b", re.I)

_TYPE_COLS = ("Incident Type A", "Incident Type B", "Incident Type C", "Incident Type D")


def fatal_incidents(rows: list[dict]) -> set[str]:
    """Incident Numbers of rows where any Incident Type A-D is 'Injury Fatality'."""
    return {r["Incident Number"] for r in rows
            if any(r.get(c) == "Injury Fatality" for c in _TYPE_COLS)}


def redact(text: str) -> str:
    """Replace titled names with [name] (full span, then each collected word
    wherever it recurs alone), then contact details with [contact]."""
    words: set[str] = set()

    def _span(m: re.Match) -> str:
        for tok in m.group(0).split()[1:]:
            if not tok.endswith("."):          # initials carry no identity
                words.add(tok)
        return "[name]"

    text = HONORIFIC_RE.sub(_span, text)
    if words:
        alt = "|".join(re.escape(w) for w in sorted(words, key=lambda w: (-len(w), w)))
        text = re.sub(rf"\b(?:{alt})\b", "[name]", text)
    return CONTACT_RE.sub("[contact]", text)


def _has_death_mention(text: str) -> bool:
    excluded = [m.span() for m in DEATH_EXCLUDE_RE.finditer(text)]
    return any(not any(a <= m.start() and m.end() <= b for a, b in excluded)
               for m in DEATH_RE.finditer(text))


def flags(table: str, rows: list[dict], text_cols: list[str],
          key_cols: list[str]) -> list[dict]:
    """One record per (row, column, flag). Matched text is never emitted."""
    out = []
    for row in rows:
        key = "|".join(str(row.get(k, "")) for k in key_cols)
        for col in text_cols:
            text = row.get(col) or ""
            if not text:
                continue
            hits = (
                ("death_mention", _has_death_mention(text)),
                ("near_fatal", bool(NEAR_FATAL_RE.search(text))),
                ("graphic_injury", bool(GRAPHIC_RE.search(text))),
                ("name_redacted", bool(HONORIFIC_RE.search(text))),
                ("contact_redacted", bool(CONTACT_RE.search(text))),
            )
            for flag, hit in hits:
                if hit:
                    out.append({"Incident Number": row.get("Incident Number", ""),
                                "table": table, "row_key": key,
                                "column": col, "flag": flag})
    return out
