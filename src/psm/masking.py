"""Mask real operator company names in the fictional company registers.

The company narratives are real BSEE text. Their process records are generated,
but a real operator's name next to a generated process failure would read as a
claim about that operator, so every real operator name in company text becomes
``[company]``. The ``core/`` and ``advanced/`` BSEE tables keep the names.

The pattern is built from two sources:
  * names derived from the operator part of the ``bsee_operator`` column of
    ``bsee_unmapped.csv`` (the CONTRACTOR part is never used: contractors and rigs
    stay named), legal suffixes stripped, and
  * the short forms in ``schema/operator_aliases.yaml``.

Deterministic: alternatives are sorted by (-len, str), so the pattern text does
not depend on set or dict order (see test_pattern_stable_across_hash_seeds).
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ALIASES = REPO / "schema" / "operator_aliases.yaml"
BSEE_UNMAPPED = REPO / "data" / "processed" / "e19" / "bsee_unmapped.csv"

MASK = "[company]"

_PLACEHOLDERS = frozenset({"-", "not listed", "company not listed", "production",
                           "production services"})
_LEGAL_SUFFIXES = frozenset({"llc", "l.l.c.", "inc.", "inc", "company", "corporation",
                             "corp.", "corp", "usa", "co."})
_DANGLING = frozenset({"&", "of", "and", "-"})        # the sidecar truncates mid-name
_JUNK_CHARS = "\xad\x01"

# Optional corporate tail consumed after a match. Title-case and upper-case only
# (plus lowercase inc/llc), so ordinary prose ("the BP company man") is not
# swallowed. Every alternative ends at a word boundary: "McMoRan Incident" keeps
# "Incident", "Talos LPG" keeps "LPG".
_GROUP_WORDS = ("Group", "Partners", "Holdings", "Operations", "Energy Partners")
_LEGAL_TAIL = ("LLC", "L.L.C.", "Inc", "Inc.", "Corp", "Corp.", "Corporation",
               "Company", "Co.", "Incorporated", "USA", "U.S.A.", "LP",
               "L.P.")
_LOWER_TAIL = ("inc", "inc.", "llc")


def _cased(words) -> list[str]:
    out = set()
    for w in words:
        out.update((w, w.upper()))
    return sorted(out, key=lambda s: (-len(s), s))


def _tail() -> str:
    group = "|".join(re.escape(w) for w in _cased(_GROUP_WORDS))
    legal = "|".join(re.escape(w) for w in
                     sorted(set(_cased(_LEGAL_TAIL)) | set(_LOWER_TAIL),
                            key=lambda s: (-len(s), s)))
    # ", LLC", " LLC" and " - USA" all count as a separator
    return (rf"(?:\s+(?:{group})(?!\w))?"
            rf"(?:(?:,\s*|\s+-\s+|\s+)(?:{legal})(?!\w))*")


def _name_regex(name: str) -> str:
    """Whitespace-flexible regex for a name; '&' may or may not carry spaces."""
    name = re.sub(r"\s*&\s*", "&", name.strip())
    tokens = []
    for tok in name.split():
        tokens.append(r"\s*&\s*".join(re.escape(p) for p in tok.split("&")))
    return r"\s+".join(tokens)


def _clean(s: str) -> str:
    s = re.sub(r"\(cid:\d+\)", "", s)         # PDF font-encoding debris
    for ch in _JUNK_CHARS:
        s = s.replace(ch, "")
    return " ".join(s.split())


def _strip_name(raw: str) -> str:
    """Drop dangling connectors and legal suffixes from either end of a derived name."""
    toks = _clean(raw).strip(" ,").split()
    changed = True
    while toks and changed:
        changed = False
        last = toks[-1].rstrip(",")
        if last.lower() in _DANGLING or last.lower() in _LEGAL_SUFFIXES:
            toks.pop()
            changed = True
            continue
        toks[-1] = last
    return " ".join(toks)


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[\s&]+", name.lower()) if t.strip(".,()")]


@lru_cache(maxsize=1)
def _alias_config() -> dict:
    return yaml.safe_load(ALIASES.read_text(encoding="utf-8"))


def derived_names() -> list[str]:
    """Operator names derived from bsee_operator (operator part only, never the
    CONTRACTOR part), deduplicated case-insensitively."""
    stop = set(_alias_config()["stoplist"])
    never = {n.lower() for n in _alias_config().get("derived_exclude", [])}
    seen: dict[str, str] = {}
    with BSEE_UNMAPPED.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            # operator part only: a CONTRACTOR part is a contractor or rig, which stays
            # named by owner decision
            for part in re.split(r"CONTRACTOR:", row["bsee_operator"] or "")[:1]:
                name = _strip_name(part)
                toks = _tokens(name)
                if len(toks) < 2 or sum(ch.isalpha() for ch in name) < 3:
                    continue
                if name.lower() in _PLACEHOLDERS or name.lower() in never:
                    continue
                if all(t.strip(".,()") in stop for t in toks):
                    continue
                seen.setdefault(re.sub(r"\s*&\s*", "&", name.lower()), name)
    return sorted(seen.values(), key=lambda s: (-len(s), s))


def first_token_forms() -> dict[str, list[str]]:
    """Short forms derived from each OPERATOR part of bsee_operator: its first token.

    Rules (all in code and yaml, none tuned per name):
      * operator parts only; the first token of a CONTRACTOR part is a contractor's
        and is never masked (contractors and rigs stay named by owner decision);
      * letters only, at least 4 of them, not on the stoplist;
      * not on ``first_token_exclude`` in the yaml (words that are also ordinary
        words, places, platforms or rigs; reasons are written there);
      * not already an alias, or the first word of one (the alias carries the flags).
    A form matches only as written, Capitalized or UPPER, never lowercase.
    Returns {lowercase token: sorted forms}.
    """
    cfg = _alias_config()
    stop = set(cfg["stoplist"])
    exclude = {w.lower() for w in cfg.get("first_token_exclude", [])}
    alias_heads = {a["name"].split()[0].lower() for a in cfg["aliases"]}
    alias_heads |= {re.sub(r"\s*&\s*", "&", a["name"].lower()) for a in cfg["aliases"]}
    raw: dict[str, set[str]] = {}
    contractor: set[str] = set()
    with BSEE_UNMAPPED.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            for i, part in enumerate(re.split(r"CONTRACTOR:", row["bsee_operator"] or "")):
                toks = _strip_name(part).split()
                if not toks:
                    continue
                tok = toks[0].strip(",.()")
                if i:
                    contractor.add(tok.lower())
                elif tok.isalpha() and tok.isascii() and len(tok) >= 4:
                    raw.setdefault(tok.lower(), set()).add(tok)
    out = {}
    for low, spellings in raw.items():
        if low in stop or low in exclude or low in contractor or low in alias_heads:
            continue
        out[low] = sorted(spellings | {low.upper(), low.capitalize()})
    return dict(sorted(out.items()))


@lru_cache(maxsize=1)
def operator_pattern() -> re.Pattern:
    cfg = _alias_config()
    suffix = "|".join(_name_regex(w) for w in sorted(cfg["suffix_words"],
                                                       key=lambda s: (-len(s), s)))
    alts: dict[str, str] = {}              # sort key (name) -> regex alternative
    alias_keys = set()
    for a in cfg["aliases"]:
        body = _name_regex(a["name"])
        flag = "-i" if a["case_sensitive"] else "i"
        if a["require_suffix"]:
            with_suffix = rf"(?i:{body}\s+(?:{suffix}))"
            # the exact-case form also stands alone ("Shell reported")
            bare = rf"(?-i:{body})" if a.get("bare_capitalized") else None
            alts[a["name"]] = (rf"(?:{with_suffix}|{bare})" if bare else with_suffix)
        else:
            alts[a["name"]] = rf"(?{flag}:{body})"
        alias_keys.add(re.sub(r"\s*&\s*", "&", a["name"].lower()))
    for name in derived_names():
        if re.sub(r"\s*&\s*", "&", name.lower()) in alias_keys:
            continue                       # the alias entry carries the flags
        alts[name] = rf"(?i:{_name_regex(name)})"
    for low, forms in first_token_forms().items():
        body = "|".join(re.escape(f) for f in sorted(forms, key=lambda f: (-len(f), f)))
        alts[low] = rf"(?-i:{body})"
    ordered = [alts[k] for k in sorted(alts, key=lambda s: (-len(s), s))]
    return re.compile(rf"(?<!\w)(?:{'|'.join(ordered)})(?!\w){_tail()}")


def mask_operators(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(MASK, text)


def masked_strings(texts, pattern: re.Pattern) -> Counter:
    """Counter of the strings mask_operators would replace (whitespace collapsed,
    original case kept), for the owner's pre-release review of what gets masked."""
    c: Counter = Counter()
    for t in texts:
        for m in pattern.finditer(t or ""):
            c[" ".join(m.group(0).split())] += 1
    return c


def top_masked(texts, pattern: re.Pattern, n: int = 20) -> list[tuple[str, int]]:
    return sorted(masked_strings(texts, pattern).items(),
                  key=lambda kv: (-kv[1], kv[0]))[:n]
