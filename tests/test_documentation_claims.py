"""Executable checks on the manuscripts and project records.

Three failure classes found by the 2026-09-29 discrepancy audit are guarded here:

* citation closure: every `[Rn]` cited in a manuscript (including groups such as `[R7, R11]` and
  ranges such as `[R5–R8]`) is defined in its reference list, and the Ahmed/Tang/Kotte entries keep
  their numbers. The reverse (every listed reference is cited) is deliberately not required yet:
  both files are planning documents whose reference lists double as a surveyed bibliography;
* CLAUDE.md §4 wording: a timing claim ("pre-registered", "pre-specified", "frozen before data",
  "confirmatory", "deposited", "registered <date>") may appear only inside a prohibition, i.e. with
  a negation nearby, or in one of the few allowlisted lines below;
* HANDOFF section numbers: HANDOFF.md is rewritten every session, so a reference like
  "HANDOFF §N" rots. Point at the owning note, CLAUDE.md or a dated HISTORY entry instead.

`plans/` is outside the wording check on purpose: plans are historical records of what was
believed at the time.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MANUSCRIPTS = ("JOURNAL_PAPER.md", "THIRD_CHAPTER.md")

REFERENCES_HEADING = re.compile(r"^## \d+\. References\s*$", re.MULTILINE)
REFERENCE_DEFINITION = re.compile(r"^- \*\*\[R(\d+)\]", re.MULTILINE)
CITATION_GROUP = re.compile(r"\[(R\d+(?:\s*[,–-]\s*R\d+)*)\]")
AHMED_CITED_AS_TANG = re.compile(r"Ahmed[^\[\n]{0,80}\[R1\]")

FORBIDDEN_TIMING_WORDS = re.compile(
    r"pre-?regist|pre-?specifi|frozen before data|confirmatory|\bdeposit"
    r"|\bregistered (on )?\d{4}-\d{2}-\d{2}",
    re.IGNORECASE,
)
NEGATION = re.compile(
    r"\b(not|no|never|nothing|void|removed|forbid\w*|unavailable|retired|purged|obsolete|dead)\b",
    re.IGNORECASE,
)
NEGATION_WINDOW_LINES = 2  # a prohibition may wrap onto the neighbouring lines

# Lines that use a timing word legitimately without a nearby negation.
TIMING_WORD_ALLOWLIST = {
    # A specification's own title, not a claim about when it was written.
    ("notes/comparator_prespec_br.md", "# Comparator pre-specification"),
    # As-submitted ethics text; that file must not be edited (it carries a header note instead).
    ("notes/ethics_amendment_hr_recovery.md", "our pre-specified quality criteria"),
}

HANDOFF_SECTION_NUMBER = re.compile(r"HANDOFF(\.md)?`? ?(§|section |S)\s?\d")
SECTION_NUMBER_EXEMPT = ("HISTORY.md", "HANDOFF.md", "reports/", "plans/")


def _tracked_files(*patterns: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--", *patterns],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _split_at_references(relative_path: str) -> tuple[str, str]:
    text = _read(relative_path)
    match = REFERENCES_HEADING.search(text)
    assert match, f"{relative_path}: no '## N. References' heading"
    return text[: match.start()], text[match.start():]


def _cited_numbers(text: str) -> set[int]:
    """Reference numbers cited in `text`, expanding `[R5–R8]` ranges and `[R7, R11]` groups."""
    cited: set[int] = set()
    for group in CITATION_GROUP.findall(text):
        for part in re.split(r"\s*,\s*", group):
            bounds = [int(n) for n in re.findall(r"\d+", part)]
            cited.update(range(bounds[0], bounds[-1] + 1))
    return cited


@pytest.mark.parametrize("manuscript", MANUSCRIPTS)
def test_every_citation_is_defined(manuscript: str) -> None:
    body, references = _split_at_references(manuscript)
    cited = _cited_numbers(body)
    defined = {int(n) for n in REFERENCE_DEFINITION.findall(references)}
    assert cited, f"{manuscript}: no citations found; the pattern has drifted"
    assert cited - defined == set(), f"{manuscript}: cited but not defined"


def test_citation_groups_and_ranges_are_expanded() -> None:
    assert _cited_numbers("see [R7, R11] and [R5–R8]") == {5, 6, 7, 8, 11}


@pytest.mark.parametrize("manuscript", MANUSCRIPTS)
@pytest.mark.parametrize(
    "entry_start",
    [
        r"- \*\*\[R1\]\*\* Tang et al\.",  # ECA + AHET
        r"- \*\*\[R21\]\*\* Kotte, V\.V\.",  # joint Doppler
        r"- \*\*\[R22\]\*\* Ahmed, S\.",  # harmonic accumulation, "Discovering the Unseen"
    ],
)
def test_key_references_keep_their_numbers(manuscript: str, entry_start: str) -> None:
    _, references = _split_at_references(manuscript)
    assert re.search("^" + entry_start, references, re.MULTILINE)


@pytest.mark.parametrize("manuscript", MANUSCRIPTS)
def test_ahmed_is_never_cited_as_tang(manuscript: str) -> None:
    offenders = [
        line for line in _read(manuscript).splitlines() if AHMED_CITED_AS_TANG.search(line)
    ]
    assert offenders == []


def _is_allowlisted(relative_path: str, line: str) -> bool:
    return any(
        relative_path == path and fragment in line for path, fragment in TIMING_WORD_ALLOWLIST
    )


def test_timing_words_appear_only_inside_prohibitions() -> None:
    files = [
        *MANUSCRIPTS,
        "HANDOFF.md",
        *_tracked_files("notes/*.md", "src/*.py", "scripts/*.py", "steps/*.py"),
    ]
    offenders = []
    for relative_path in files:
        lines = _read(relative_path).splitlines()
        for index, line in enumerate(lines):
            if not FORBIDDEN_TIMING_WORDS.search(line) or _is_allowlisted(relative_path, line):
                continue
            lo = max(0, index - NEGATION_WINDOW_LINES)
            window = " ".join(lines[lo: index + NEGATION_WINDOW_LINES + 1])
            if not NEGATION.search(window):
                offenders.append(f"{relative_path}:{index + 1}: {line.strip()}")
    assert offenders == [], "timing wording outside a prohibition:\n" + "\n".join(offenders)


def test_no_section_numbered_handoff_references() -> None:
    files = [
        path
        for path in _tracked_files("*.md", "*.py", "*.yaml")
        if not path.startswith(SECTION_NUMBER_EXEMPT)
    ]
    offenders = []
    for relative_path in files:
        for index, line in enumerate(_read(relative_path).splitlines()):
            if HANDOFF_SECTION_NUMBER.search(line):
                offenders.append(f"{relative_path}:{index + 1}: {line.strip()}")
    assert offenders == [], "point at a durable owner, not a HANDOFF section:\n" + "\n".join(
        offenders
    )
