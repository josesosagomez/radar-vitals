"""Repository-wide line-ending provenance guard."""

from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_tracked_text_files_are_materialized_with_lf_endings() -> None:
    """Working-tree text must hash identically across supported platforms."""
    result = subprocess.run(
        ["git", "ls-files", "--eol"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    offenders = [
        line
        for line in result.stdout.splitlines()
        if "w/crlf" in line or "w/mixed" in line
    ]
    assert offenders == [], "tracked files with non-LF working-tree endings:\n" + "\n".join(
        offenders
    )
