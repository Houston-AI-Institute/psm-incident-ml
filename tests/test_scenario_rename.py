"""The 2026-10-18 company rename (northstar/meridian/coastal -> alder/hazel/hornbeam)
changed only the visible slug, label and ID prefix. Every draw is keyed on the scenario's
`seed_key` / `seed_prefix`, so the generated registers must equal the pre-rename ones
(read from a pinned commit) once the old ID prefixes are swapped for the new ones.
"""
from __future__ import annotations

import csv
import io
import re
import subprocess
from pathlib import Path

import pytest

from psm import scenario

REPO = Path(__file__).resolve().parents[1]
PRE_RENAME = "d41622e"          # the commit before the rename
RENAMES = {"northstar": ("alder", "NS", "AL"),
           "meridian": ("hazel", "MR", "HZ"),
           "coastal": ("hornbeam", "CP", "HB")}
VALUE_FILES = ("incidents.csv", "causes.csv", "recommendations.csv", "closeout.csv")
# Free-text columns are excluded so this stays valid when later tasks mask or reword them.
IGNORED = set(scenario._FREE_TEXT) | {"Cause Description"}
ID_RE = re.compile(r"\b(NS|MR|CP)-(?=[0-9A-F]{10}\b)")


def _commit_available(sha: str, cwd: Path = REPO) -> bool:
    """True if `sha` is a commit in this repo. False only when the commit is missing (after a
    squash merge, or in a shallow clone); any other git failure raises."""
    if subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=cwd,
                      capture_output=True).returncode == 0:
        return True
    subprocess.run(["git", "rev-parse", "--git-dir"], cwd=cwd, check=True, capture_output=True)
    return False


needs_pinned_commit = pytest.mark.skipif(
    not _commit_available(PRE_RENAME),
    reason=f"the pinned pre-rename commit {PRE_RENAME} is unavailable (after a squash merge or in "
           "a shallow clone); the one-off result is recorded in docs/findings.md 2026-10-24")


def _old_rows(old: str, name: str) -> list[dict]:
    blob = subprocess.run(["git", "show", f"{PRE_RENAME}:data/companies/{old}/{name}"],
                          cwd=REPO, check=True, capture_output=True).stdout.decode("utf-8")
    return list(csv.DictReader(io.StringIO(blob, newline="")))


def _new_rows(new: str, name: str) -> list[dict]:
    with (scenario.OUT_ROOT / new / name).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@needs_pinned_commit
@pytest.mark.parametrize("old", sorted(RENAMES))
def test_rename_preserves_draws(old):
    new, old_prefix, new_prefix = RENAMES[old]
    swap = {"NS": "AL", "MR": "HZ", "CP": "HB"}
    for name in VALUE_FILES:
        before, after = _old_rows(old, name), _new_rows(new, name)
        assert before and len(before) == len(after), (new, name)
        assert list(before[0]) == list(after[0]), (new, name)
        compared = 0
        for b, a in zip(before, after):
            for col in b:
                if col in IGNORED:
                    continue
                want = ID_RE.sub(lambda m: swap[m.group(1)] + "-", b[col])
                assert a[col] == want, (new, name, col)
                compared += 1
        assert compared > 100, (new, name)
    ids = {r["Incident Number"] for r in _new_rows(new, "incidents.csv")}
    assert len(ids) == 150 and all(i.startswith(new_prefix + "-") for i in ids)


@needs_pinned_commit
def test_rename_check_catches_a_redraw(monkeypatch):
    """Deliberate violation: key the draws on the NEW slug instead of the seed and the
    generated register no longer matches the pre-rename one."""
    def dates(rows):
        return sorted(r["Date of Incident"] for r in rows)

    before = dates(_old_rows("northstar", "incidents.csv"))
    assert dates(scenario.generate("alder")["tables"]["incidents"][1]) == before
    monkeypatch.setattr(scenario, "_seed", lambda company: company)
    monkeypatch.setattr(scenario, "_seed_sid", lambda sid: sid)
    assert dates(scenario.generate("alder")["tables"]["incidents"][1]) != before


def test_seed_helpers():
    assert scenario._seed("alder") == "northstar"
    assert scenario._seed("hazel_nt") == "meridian_nt"
    assert scenario._seed_sid("AL-0000000000") == "NS-0000000000"
    assert scenario._seed_sid("HZT-0000000000") == "MNT-0000000000"


def test_missing_pinned_commit_is_reported_unavailable(tmp_path):
    assert _commit_available("0" * 40) is False
    with pytest.raises(subprocess.CalledProcessError):      # not a repo: a real failure, not a skip
        _commit_available("0" * 40, cwd=tmp_path)
