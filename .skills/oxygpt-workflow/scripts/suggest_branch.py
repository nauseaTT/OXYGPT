#!/usr/bin/env python3
"""
suggest_branch.py — Derive a contextual branch name from the working tree.

Purpose
-------
This project's contribution workflow forbids generic, tool-flavoured branch
names. Every branch must describe *what the current change actually is*. This
script inspects the staged/unstaged diff and produces a short, kebab-case
branch name derived from the change itself.

Rules
-----
1. The branch name is built from the files touched and (when available) the
   nature of the change (docs, help text, handler, database, tools, journal,
   watcher, chart, config, etc.).
2. If nothing meaningful can be inferred (no changes, or the change is too
   ambiguous to name), the script falls back to the fixed name ``nausea``.
3. The output NEVER contains the two-letter token that stands for "artificial
   intelligence" nor the vendor prefix commonly attached to automated tooling.
   Any such token is scrubbed from the final name.

Usage
-----
    python .skills/oxygpt-workflow/scripts/suggest_branch.py
    # -> prints a single branch name to stdout, e.g. "help-text-revamp"

    python .skills/oxygpt-workflow/scripts/suggest_branch.py --create
    # -> also runs `git checkout -b <name>`
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter

FALLBACK = "nausea"

# Tokens that must never appear anywhere in a branch name for this project.
FORBIDDEN_TOKENS = {"ai", "genspark", "gpt", "llm", "bot"}

# Map top-level paths / filename fragments to human topic words.
TOPIC_MAP = [
    (re.compile(r"help", re.I), "help"),
    (re.compile(r"menu", re.I), "menu"),
    (re.compile(r"shortcut", re.I), "shortcuts"),
    (re.compile(r"handlers?/admin", re.I), "admin"),
    (re.compile(r"handlers?/commands", re.I), "commands"),
    (re.compile(r"handlers?/windows", re.I), "windows"),
    (re.compile(r"handlers?/verify", re.I), "verify"),
    (re.compile(r"handlers?/misc", re.I), "misc"),
    (re.compile(r"database", re.I), "database"),
    (re.compile(r"tools", re.I), "tools"),
    (re.compile(r"skills", re.I), "skills"),
    (re.compile(r"system_prompt", re.I), "prompt"),
    (re.compile(r"chart", re.I), "chart"),
    (re.compile(r"trade_journal", re.I), "journal"),
    (re.compile(r"channel_watcher", re.I), "watcher"),
    (re.compile(r"constants", re.I), "constants"),
    (re.compile(r"animator", re.I), "animator"),
    (re.compile(r"\.md$", re.I), "docs"),
    (re.compile(r"requirements|\.env|\.gitignore|config", re.I), "config"),
    (re.compile(r"api_http", re.I), "service"),
]


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, check=False
        ).stdout.strip()
    except Exception:
        return ""


def changed_files() -> list[str]:
    """Return files changed vs HEAD (staged + unstaged + untracked)."""
    files: set[str] = set()
    for args in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--name-only", "--cached"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        out = _run(args)
        if out:
            files.update(line.strip() for line in out.splitlines() if line.strip())
    return sorted(files)


def scrub(word: str) -> str:
    """Remove forbidden tokens and normalise a candidate name segment."""
    parts = [p for p in re.split(r"[^a-z0-9]+", word.lower()) if p]
    parts = [p for p in parts if p not in FORBIDDEN_TOKENS]
    return "-".join(parts)


def derive_name(files: list[str]) -> str:
    if not files:
        return FALLBACK

    topics: Counter[str] = Counter()
    for f in files:
        for pattern, topic in TOPIC_MAP:
            if pattern.search(f):
                topics[topic] += 1

    if not topics:
        # Fall back to the most common base filename fragment.
        for f in files:
            base = re.sub(r"\.[a-z0-9]+$", "", f.split("/")[-1], flags=re.I)
            cleaned = scrub(base)
            if cleaned:
                topics[cleaned] += 1

    if not topics:
        return FALLBACK

    ordered = [t for t, _ in topics.most_common(3)]
    name = scrub("-".join(ordered))

    # Add an intent suffix if the change looks doc/help-only.
    if set(ordered) <= {"docs", "help", "menu", "shortcuts", "constants"}:
        name = f"{name}-update"

    name = re.sub(r"-+", "-", name).strip("-")
    return name or FALLBACK


def main() -> int:
    files = changed_files()
    name = derive_name(files)

    # Final safety scrub — guarantee no forbidden token slips through.
    name = scrub(name) or FALLBACK

    if "--create" in sys.argv:
        rc = subprocess.run(["git", "checkout", "-b", name]).returncode
        if rc != 0:
            print(name)
            return rc

    print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
