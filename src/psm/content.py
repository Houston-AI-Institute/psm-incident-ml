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

# ---- untitled staff names (2026-10-24) --------------------------------------------------
# Three shapes, each confirmed in the BSEE text, that name a person with no honorific:
#   role      an agency role word used as a title: "Inspector"/"Investigator" followed by
#             1-3 Capitalised words, either directly or as "(Name Name)" right after it;
#   email     "email from" / "e-mail from" followed by exactly First [I.] Last;
#   team      an investigation-team or panel header followed by a bracketed list of
#             First [I.] Last members separated by commas, semicolons or "and".
# The name part is strict: a capital then lowercase only (optional Mc/Mac/O' prefix, one
# capitalised hyphen part), so acronyms (ERT, IMT), all-caps words and "Third-party" or
# "Fieldwood's" are not names. Only Capitalised role words count: lower-case "crane
# inspector (X Y)" is how the text names contractors and companies. A match is dropped if
# any of its words is in UNTITLED_NOT_NAME (the role direct form keeps its leading run of
# good words instead). Only the name part becomes [name]; the role word, header and
# brackets stay.
_NAME = r"(?:(?:Mc|Mac|O['’])?[A-Z][a-z]+(?:-[A-Z][a-z]+)?(?![A-Za-z]))"
_INIT = r"(?:[A-Z]\.)"
_NAMES13 = rf"{_NAME}(?:\s+(?:{_INIT}|{_NAME})){{0,2}}"
_FIRST_LAST = rf"{_NAME}(?:\s+{_INIT})?\s+{_NAME}"
ROLE_TITLE_RE = re.compile(
    rf"\b(?:Inspector|Investigator|INSPECTOR|INVESTIGATOR)s?"
    rf"(?:\s*\(\s*(?P<paren>{_NAMES13})\s*\)|\s+(?P<direct>{_NAMES13}))")
EMAIL_SENDER_RE = re.compile(rf"(?i:\be-?mail(?:ed)?\s+from)\s+(?P<name>{_FIRST_LAST})(?!\s+[A-Z][a-z])")
_MEMBER = rf"(?:\[name\]|{_FIRST_LAST}(?:\s+{_NAME})?)"
_SEP = r"\s*(?:,\s*and\b|,|;|\band\b)\s*"
TEAM_LIST_RE = re.compile(
    r"(?i:\b(?:(?:investigat(?:ion|ive)|accident|review)\s+(?:team|panel)|panel))"
    rf"\s*\(\s*(?P<list>{_MEMBER}(?:{_SEP}{_MEMBER})*)\s*\)")
_MEMBER_RE = re.compile(_MEMBER)
# Words that, inside a candidate span, mean it names an office, role, company or crew and
# not a person. Matched case-insensitively. Membership only (never joined into a regex).
UNTITLED_NOT_NAME = NAME_STOPWORDS | frozenset({
    "General", "Inspector", "Inspectors", "Investigator", "Investigators", "Lead", "Senior",
    "Chief", "Accident", "Incident", "Safety", "District", "Region", "Regional", "Office",
    "Field", "Team", "Panel", "Training", "Report", "Program", "Supervisor", "Manager",
    "Engineer", "Operator", "Representative", "Captain", "Company", "Corporation", "Inc",
    "Energy", "Offshore", "Oil", "Gas", "Petroleum", "Exploration", "Production", "Drilling",
    "Marine", "Services", "Engineering", "Operating", "Resources", "Pumps", "Group",
    "International", "Holdings",
})
_NOT_NAME_LOWER = frozenset(w.lower() for w in UNTITLED_NOT_NAME)


def _bad(word: str) -> bool:
    return word.rstrip(".").lower() in _NOT_NAME_LOWER


def untitled_name_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of each untitled-name span in ``text`` (role, email and team rules),
    sorted and non-overlapping. Each team member is its own span; ``[name]`` is skipped."""
    spans: list[tuple[int, int]] = []
    for m in ROLE_TITLE_RE.finditer(text):
        g = "paren" if m.group("paren") else "direct"
        s = m.start(g)
        toks = list(re.finditer(r"\S+", m.group(g)))
        if g == "paren":
            if not any(_bad(t.group(0)) for t in toks):
                spans.append((s, m.end(g)))
            continue
        good = []
        for t in toks:
            if _bad(t.group(0)):
                break
            good.append(t)
        while good and re.fullmatch(_INIT, good[-1].group(0)):
            good.pop()                                     # never end on a bare initial
        if good:
            spans.append((s, s + good[-1].end()))
    for m in EMAIL_SENDER_RE.finditer(text):
        if not any(_bad(w) for w in m.group("name").split()):
            spans.append(m.span("name"))
    for m in TEAM_LIST_RE.finditer(text):
        base = m.start("list")
        members = [(base + mm.start(), base + mm.end(), mm.group(0))
                   for mm in _MEMBER_RE.finditer(m.group("list"))]
        if any(_bad(w) for _s, _e, txt in members for w in txt.split()):
            continue                                       # a role/crew list, not people
        spans += [(s, e) for s, e, txt in members if txt != "[name]"]
    out: list[tuple[int, int]] = []
    for s, e in sorted(set(spans)):
        if out and s < out[-1][1]:
            out[-1] = (out[-1][0], max(e, out[-1][1]))
        else:
            out.append((s, e))
    return out


def has_untitled_name(text: str) -> bool:
    return bool(untitled_name_spans(HONORIFIC_RE.sub("[name]", text)))


def has_name(text: str) -> bool:
    """True when `redact` would mask a name in ``text`` (titled or untitled)."""
    return bool(HONORIFIC_RE.search(text)) or has_untitled_name(text)


def _untitled_words(text: str, spans: list[tuple[int, int]]) -> set[str]:
    return {w for s, e in spans for w in text[s:e].split()
            if not w.endswith(".") and w.lower() not in _STOP_LOWER}


def _span_words(span: str) -> set[str]:
    """The name words of one matched title span (initials carry no identity)."""
    return {tok for tok in span.split()[1:] if not tok.endswith(".")}


def titled_name_words(text: str) -> set[str]:
    """Words that follow a title in ``text`` (same spans `redact` finds), minus NAME_STOPWORDS,
    plus the words of the untitled-name spans `redact` finds (role, email, team rules)."""
    out: set[str] = set()
    for m in HONORIFIC_RE.finditer(text):
        out |= {w for w in _span_words(m.group(0)) if w.lower() not in _STOP_LOWER}
    masked = HONORIFIC_RE.sub("[name]", text)
    return out | _untitled_words(masked, untitled_name_spans(masked))


def redact(text: str, names: set[str] | None = None) -> str:
    """Replace titled names with [name] (full span), then untitled-name spans (role, email,
    team rules; see `untitled_name_spans`), then each collected word wherever it recurs
    alone, then contact details with [contact].

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
    spans = untitled_name_spans(text)
    if spans:
        words |= _untitled_words(text, spans)
        for s, e in reversed(spans):
            text = text[:s] + "[name]" + text[e:]
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
                ("name_redacted", has_name(text)),
                ("contact_redacted", bool(CONTACT_RE.search(text))),
            )
            for flag, hit in hits:
                if hit:
                    out.append({"Incident Number": row.get("Incident Number", ""),
                                "table": table, "row_key": key,
                                "column": col, "flag": flag})
    return out
