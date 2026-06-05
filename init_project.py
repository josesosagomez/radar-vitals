#!/usr/bin/env python3
"""Initialize a new research-project skeleton.

Reusable across projects. Run from inside an empty (or new) repo:
    python init_project.py

Creates the standard directory layout + .gitkeep placeholders. It does NOT overwrite
existing files. Drop your CLAUDE.md / AGENTS.md / SESSION.md / .mcp.json / environment.yml
in afterward (or copy them from a template repo).
"""
from pathlib import Path

DIRS = [
    "data/raw",
    "data/processed",
    "src",
    "experiments",
    "results",
    "figures",
    "notes",
    "tests",
    "paper",
]

ROOT = Path(__file__).resolve().parent


def main() -> None:
    for d in DIRS:
        p = ROOT / d
        p.mkdir(parents=True, exist_ok=True)
        keep = p / ".gitkeep"
        if not any(p.iterdir()):
            keep.touch()
        print(f"ok  {d}/")
    print("\nSkeleton ready. Next:")
    print("  1. Add CLAUDE.md, AGENTS.md, SESSION.md, .mcp.json, environment.yml")
    print("  2. conda env create -f environment.yml")
    print("  3. git init && git add -A && git commit -m 'init project skeleton'")


if __name__ == "__main__":
    main()
