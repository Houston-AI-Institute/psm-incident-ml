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
# "dies" is the equipment noun here (tong dies, slip dies), so it is not a death word.
DEATH_RE = re.compile(r"\b(died|death|deceased|killed|fatal(ly|ity)?|pronounced dead|"
                      r"lost (his|her) life|body was recovered)\b", re.I)
# Spans that are not a death: drilling and equipment jargon, and counterfactual lines.
DEATH_EXCLUDE_RE = re.compile(
    r"\b(kill(ed)?\s+the\s+well|kill\s+(weight|mud|line|fluid|pill)|"
    r"well\s+(was\s+|has\s+been\s+)?(\w+\s+)?killed|it\s+(was\s+|has\s+been\s+)?killed|"
    r"killed\s+(the\s+well\s+)?uneventfully|"
    r"((\d+|all|both|the)\s+)?(engine|motor|pump|generator)s?\s+died|"
    r"could\s+have\s+(been|caused|resulted\s+in)[^.]{0,40}fatal\w*|fatal\s+potential|"
    r"potential(ly)?\s+(for\s+)?(a\s+)?fatal\w*|(serious\s+)?injury\s+(and/)?or\s+(death|fatality)|"
    r"possibility\s+of[^.]{0,40}fatal\w*|result\s+could\s+have\s+been\s+fatal)\b", re.I)
SERIOUS_INJURY_RE = re.compile(r"\b(amputat\w*|severed|crush\w*|decapitat\w*|fractur\w*|lacerat\w*|"
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


# Closed list of function words (and a few sentence openers seen after a nameless title).
# Why it exists: the source text has titles whose name was already removed upstream, so a
# title can be followed by the first word of the next sentence ("... Mr. The crane ...").
# Collected as a "name", such a word would be masked wherever it recurs in the incident.
# Matched case-insensitively. Used only when redacting with incident-scoped names (see
# `titled_name_words` and `redact(names=...)`); plain `redact(text)` is unchanged.
NAME_STOPWORDS = frozenset({
    "The", "There", "This", "That", "These", "Those", "Then", "Thus", "A", "An", "In", "On", "At",
    "It", "Its", "He", "She", "They", "We", "I", "You", "And", "But", "Or", "If", "When", "While",
    "After", "Before", "During", "As", "For", "From", "By", "With", "Of", "To", "Was", "Were",
    "Is", "Are", "Not", "No", "Also", "However", "Investigation", "Personnel",
    "Moreover", "Furthermore", "Additionally", "Therefore", "Although", "Because", "Since",
    "Once", "Upon", "Following", "According", "Both", "All", "Each", "Another", "Other",
    "Within", "Without", "Between", "Through", "Per", "Via",
})
_STOP_LOWER = frozenset(w.lower() for w in NAME_STOPWORDS)


def _span_words(span: str) -> set[str]:
    """The name words of one matched title span (initials carry no identity)."""
    return {tok for tok in span.split()[1:] if not tok.endswith(".")}


def titled_name_words(text: str) -> set[str]:
    """Words that follow a title in ``text`` (same spans `redact` finds), minus NAME_STOPWORDS."""
    out: set[str] = set()
    for m in HONORIFIC_RE.finditer(text):
        out |= {w for w in _span_words(m.group(0)) if w.lower() not in _STOP_LOWER}
    return out


def redact(text: str, names: set[str] | None = None) -> str:
    """Replace titled names with [name] (full span, then each collected word
    wherever it recurs alone), then contact details with [contact].

    ``names=None``: only words collected from this text recur-mask (the original rule).
    With ``names`` (the incident's name words from `titled_name_words` over all of its
    cells): those words, plus this text's own non-stopword words, recur-mask here too, so
    a surname that follows a title in one cell is masked in the other cells of the incident.
    """
    words: set[str] = set()

    def _span(m: re.Match) -> str:
        found = _span_words(m.group(0))
        words.update(found if names is None else {w for w in found if w.lower() not in _STOP_LOWER})
        return "[name]"

    text = HONORIFIC_RE.sub(_span, text)
    if names:
        words |= names
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
                ("serious_injury", bool(SERIOUS_INJURY_RE.search(text))),
                ("name_redacted", bool(HONORIFIC_RE.search(text))),
                ("contact_redacted", bool(CONTACT_RE.search(text))),
            )
            for flag, hit in hits:
                if hit:
                    out.append({"Incident Number": row.get("Incident Number", ""),
                                "table": table, "row_key": key,
                                "column": col, "flag": flag})
    return out
