"""Mask real operator company names in the fictional company registers.

The company narratives are real BSEE text. Their process records are generated,
but a real operator's name next to a generated process failure would read as a
claim about that operator, so every real operator name in company text becomes
``[company]``. The ``core/`` and ``advanced/`` BSEE tables keep the names.

The pattern is built from two sources:
  * names derived from the ``bsee_operator`` column of ``bsee_unmapped.csv``
    (operator and CONTRACTOR parts, legal suffixes stripped), and
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

# Optional corporate tail consumed after a match. Title-case and upper-case only,
# so ordinary prose ("the BP company man") is not swallowed.
_GROUP_WORDS = ("Group", "Partners", "Holdings", "Energy Partners")
_LEGAL_TAIL = ("LLC", "L.L.C.", "Inc", "Inc.", "Corp", "Corp.", "Corporation",
               "Company", "Co.", "USA", "U.S.A.", "LP", "L.P.")


def _cased(words) -> list[str]:
    out = set()
    for w in words:
        out.update((w, w.upper()))
    return sorted(out, key=lambda s: (-len(s), s))


def _tail() -> str:
    group = "|".join(re.escape(w) for w in _cased(_GROUP_WORDS))
    legal = "|".join(re.escape(w) for w in _cased(_LEGAL_TAIL))
    return rf"(?:\s+(?:{group}))?(?:,?\s*(?:{legal}))*"


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
    """Company names derived from bsee_operator, deduplicated case-insensitively."""
    stop = set(_alias_config()["stoplist"])
    seen: dict[str, str] = {}
    with BSEE_UNMAPPED.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            for part in re.split(r"CONTRACTOR:", row["bsee_operator"] or ""):
                name = _strip_name(part)
                toks = _tokens(name)
                if len(toks) < 2 or sum(ch.isalpha() for ch in name) < 3:
                    continue
                if name.lower() in _PLACEHOLDERS:
                    continue
                if all(t.strip(".,()") in stop for t in toks):
                    continue
                seen.setdefault(re.sub(r"\s*&\s*", "&", name.lower()), name)
    return sorted(seen.values(), key=lambda s: (-len(s), s))


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
    ordered = [alts[k] for k in sorted(alts, key=lambda s: (-len(s), s))]
    return re.compile(rf"(?<!\w)(?:{'|'.join(ordered)})(?!\w){_tail()}")


def mask_operators(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(MASK, text)


def masked_strings(texts, pattern: re.Pattern) -> Counter:
    """Counter of the strings mask_operators would replace (whitespace collapsed,
    case folded), for the owner's pre-release review of what gets masked."""
    c: Counter = Counter()
    for t in texts:
        for m in pattern.finditer(t or ""):
            c[" ".join(m.group(0).split()).casefold()] += 1
    return c


def top_masked(texts, pattern: re.Pattern, n: int = 20) -> list[tuple[str, int]]:
    return sorted(masked_strings(texts, pattern).items(),
                  key=lambda kv: (-kv[1], kv[0]))[:n]
