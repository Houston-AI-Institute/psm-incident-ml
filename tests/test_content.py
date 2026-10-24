# tests/test_content.py
"""Content screen: fatality ids, full-span name redaction, content flags."""
import csv
import re
from pathlib import Path

import pytest

from psm.content import fatal_incidents, flags, redact

REAL = Path(__file__).resolve().parents[1] / "data" / "processed" / "e19" / "real_only"

TEXT_COLS = [
    "What happened?  ",
    "What was the outcome?",
    "How did the incident occur",
    "Description",
    "incident Title",
    "Detail",
    "What was the worst outcome that could reasonably be expected to have happened?",
]

_TITLES = {"Mr", "Mrs", "Ms", "Miss", "Dr", "MR", "MRS", "MS", "MISS", "DR"}


def _rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _titled_words(text):
    """Independent tokenizer: whitespace split; after a title token, collect the
    following capitalised words (up to 3, matching the span cap), stopping at the
    first lowercase word or at a word that ends in punctuation (a name list or
    clause boundary); initials (single letters) are not collected."""
    toks = text.split()
    out = set()
    # Stop rule: a comma/period-terminated word ends the span (words after it are
    # employer, place or role words, not part of the titled name).
    for i, t in enumerate(toks):
        if t.strip(".,;:()\"") not in _TITLES:
            continue
        for nxt in toks[i + 1:i + 4]:
            w = nxt.strip(".,;:()\"")
            if not w or not w[0].isupper():
                break
            if len(w) > 1:
                out.add(w)
                if nxt[-1] in ".,;:)":
                    break
    return out


def _assert_no_titled_words_survive(text):
    words = _titled_words(text)
    out = redact(text)
    for w in words:
        assert not re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", out), (len(w),)
    return len(words)


def test_fatal_ids_real_only():
    assert fatal_incidents(_rows(REAL / "incidents.csv")) == {
        "SP-38-20071229-1550", "ST-168-20080220-0915", "VR-318-20080413-0730"}


def test_redact_full_span_and_repeat():
    out = redact("Mr. John Q. Smith fell. Smith was taken to the clinic by Mrs. O'Brien-Hale.")
    assert "Smith" not in out and "John" not in out and "O'Brien" not in out
    assert out.startswith("[name] fell. [name] was taken")


def test_redact_uppercase():
    out = redact("MR. SMITH LOST FOOTING")   # all-caps text over-redacts up to 2 following words; accepted
    assert out.startswith("[name]") and "SMITH" not in out


def test_redact_leaves_ordinary_words():
    assert redact("did not miss the step") == "did not miss the step"


def test_redact_contact():
    assert redact("call (555) 555-0100 or a@b.com") == "call [contact] or [contact]"


def test_redact_contact_dashed_phone():
    assert redact("ring 555-555-0100 now") == "ring [contact] now"


def test_tokenizer_collects_titled_words_only():
    assert _titled_words("Mr. John Q. Smith fell") == {"John", "Smith"}
    assert _titled_words("did not miss the step") == set()
    assert _titled_words("Mr. Smith, Jones saw it") == {"Smith"}


def test_real_named_cells_fully_redacted():
    inc_rows = _rows(REAL / "incidents.csv")
    checked = 0
    for inc, col in [("EI-330-20061011-2345", "What happened?  "),
                     ("SM-106-20060529-1530", "What happened?  ")]:
        cells = [r[col] for r in inc_rows if r["Incident Number"] == inc]
        assert cells, inc
        for cell in cells:
            n = _assert_no_titled_words_survive(cell)
            assert n > 0, (inc, "pinned cell has no titled name")
            checked += n
    causes = [r for r in _rows(REAL / "causes.csv")
              if r["Incident Number"] == "PN-891-20051205-2200"]
    assert causes
    n_causes = 0
    for r in causes:
        n_causes += _assert_no_titled_words_survive(r["Cause Description"])
    assert n_causes > 0
    assert checked > 0


@pytest.mark.parametrize("inc,flag", [
    ("HI-A 379-20230904-0900", "near_fatal"),
    ("SS-33-20060906-0800", "near_fatal"),
    ("EI-330-20061011-2345", "serious_injury")])
def test_must_flag(inc, flag):
    rows = [r for r in _rows(REAL / "incidents.csv") if r["Incident Number"] == inc]
    assert rows, inc
    assert flag in {f["flag"] for f in flags("incidents", rows, TEXT_COLS, ["Incident Number"])}


def test_near_fatal_widened_verbs():
    for verb in ("found", "noticed", "saw", "spotted", "observed"):
        rows = [{"Incident Number": "X", "t": f"a crew member {verb} him in the water"}]
        assert "near_fatal" in {f["flag"] for f in flags("incidents", rows, ["t"], ["Incident Number"])}, verb
    rows = [{"Incident Number": "X", "t": "a crew member waved to him in the water tank"}]
    assert "near_fatal" not in {f["flag"] for f in flags("incidents", rows, ["t"], ["Incident Number"])}


def test_death_exclusions():
    f = flags("incidents", [{"Incident Number": "X", "t": "the well was killed; engines died"}], ["t"], ["Incident Number"])
    assert "death_mention" not in {r["flag"] for r in f}


def _death(text):
    f = flags("incidents", [{"Incident Number": "X", "t": text}], ["t"], ["Incident Number"])
    return "death_mention" in {r["flag"] for r in f}


@pytest.mark.parametrize("text", [
    "the worker died at the scene",
    "the worker was killed in the fall",
    "the worker was fatally injured",
    "the worker was pronounced dead",
])
def test_death_mention_still_flags_real_death_wording(text):
    assert _death(text)


@pytest.mark.parametrize("text", [
    "the slip dies marked the pipe",
    "the well was killed uneventfully",
    "all 4 engines died",
    "the pump died and the generator died",
    "this could have caused a fatality",
    "the event had fatal potential",
    "there was potentially a fatal outcome",
    "the label warns of serious injury or death",
    "the label warns of injury or fatality",
    "there was a possibility of a fatal fall",
    "the result could have been fatal",
    # wrapped lines, an adverb before "killed", and "and/or" appear in the real text
    "the well was successfully killed",
    "this could have caused a\nfatality",
    "the event had fatal\npotential",
    "the label warns of serious injury and/or fatality",
])
def test_death_mention_ignores_equipment_jargon_and_counterfactuals(text):
    assert not _death(text)


def test_death_exclusion_does_not_swallow_a_real_death_in_the_same_text():
    assert _death("the engines died and then the worker died")
    assert _death("the well was killed. The worker was killed")


def test_serious_injury_flag_name():
    f = flags("incidents", [{"Incident Number": "X", "t": "a laceration to the hand"}], ["t"], ["Incident Number"])
    assert {r["flag"] for r in f} == {"serious_injury"}


def test_death_mention_flags_and_never_emits_text():
    rows = [{"Incident Number": "X", "k": "a", "t": "the worker died. Mr. Jones called 555-555-0100"}]
    f = flags("incidents", rows, ["t"], ["Incident Number", "k"])
    assert {r["flag"] for r in f} == {"death_mention", "name_redacted", "contact_redacted"}
    for r in f:
        assert set(r) == {"Incident Number", "table", "row_key", "column", "flag"}
        assert r["row_key"] == "X|a" and r["table"] == "incidents" and r["column"] == "t"
        assert "Jones" not in "".join(r.values()) and "555" not in "".join(r.values())
