#!/usr/bin/env python3
"""
guard_naming.py — Pre-commit guard for this project's naming policy.

Checks two things and exits non-zero (with a clear report) if either fails:

1. Branch name policy
   - The current branch must NOT be a generic tooling name
     (e.g. the default automated-developer branch).
   - The current branch must NOT contain any forbidden token
     (the two-letter "artificial intelligence" token, vendor prefixes, etc.).
   - ``main`` / ``master`` are exempt (you commit onto a feature branch).

2. Vocabulary policy for user-facing strings
   - Scans the given files for the standalone forbidden token used as a word
     in *user-facing* text. By default it only flags obvious whole-word hits
     to avoid false positives on identifiers.

Usage
-----
    python .skills/oxygpt-workflow/scripts/guard_naming.py [file ...]

If no files are passed, it scans the project's user-facing help/menu strings.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN_BRANCH_SUBSTRINGS = ("genspark", "_ai_", "ai-developer", "ai_developer")
FORBIDDEN_WORD = re.compile(r"(?<![A-Za-z])ai(?![A-Za-z])", re.I)

# Files whose *visible* text (Persian/English help, menus) must stay clean.
DEFAULT_TARGETS = [
    "telegram/handlers/shortcuts.py",
    "telegram/handlers/menu.py",
    "telegram/handlers/commands.py",
]

ROOT = Path(__file__).resolve().parents[3]


def current_branch() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
    except Exception:
        return ""


def check_branch() -> list[str]:
    problems: list[str] = []
    branch = current_branch()
    if branch in ("", "main", "master", "HEAD"):
        return problems
    low = branch.lower()
    for bad in FORBIDDEN_BRANCH_SUBSTRINGS:
        if bad in low:
            problems.append(
                f"Branch '{branch}' uses a forbidden tooling name fragment '{bad}'."
            )
    if re.search(r"(?<![a-z])ai(?![a-z])", low):
        problems.append(f"Branch '{branch}' contains the forbidden 'ai' token.")
    return problems


def _visible_strings(text: str) -> list[tuple[int, str]]:
    """Return (line_no, snippet) for lines whose *displayed* text contains the
    forbidden token.

    The check targets text a user actually reads: Persian display strings and
    Latin words that stand alone inside a quoted string. It deliberately
    ignores:
      - Python comments (``# ...``)
      - byte-string callback identifiers such as ``b"trading_ai_panel"``
      - snake_case identifiers / dict keys such as ``"ask_ai"`` where the
        token is glued to other letters or underscores.
    """
    hits: list[tuple[int, str]] = []
    persian = re.compile(r"[\u0600-\u06FF]")
    # A forbidden token surrounded by whitespace/punctuation inside display text
    # (not part of a snake_case identifier, not inside a b"..." literal).
    display_hit = re.compile(r"(?<![A-Za-z0-9_])ai(?![A-Za-z0-9_])", re.I)

    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        # Strip byte-string literals (callback keys) — not user-facing.
        scanned = re.sub(r"""b(['"]).*?\1""", "", line)

        # A line is "display text" if it contains Persian, OR contains a quoted
        # phrase with a space (a real sentence rather than an identifier).
        has_persian = bool(persian.search(scanned))
        has_phrase = bool(re.search(r"""(['"])[^'"]*\s[^'"]*\1""", scanned))
        if not (has_persian or has_phrase):
            continue

        if display_hit.search(scanned):
            hits.append((i, stripped[:120]))
    return hits


def check_vocab(targets: list[str]) -> list[str]:
    problems: list[str] = []
    for rel in targets:
        p = ROOT / rel
        if not p.exists():
            continue
        for line_no, snippet in _visible_strings(p.read_text(encoding="utf-8")):
            problems.append(f"{rel}:{line_no}: possible 'ai' in user-facing text -> {snippet}")
    return problems


def main() -> int:
    targets = sys.argv[1:] or DEFAULT_TARGETS
    problems = check_branch() + check_vocab(targets)
    if problems:
        print("Naming/vocabulary policy violations:")
        for pr in problems:
            print(f"  - {pr}")
        return 1
    print("OK: branch name and user-facing vocabulary comply with project policy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
