---
name: oxygpt-workflow
description: >-
  The contribution and change-management workflow for the OxyGPT Telegram
  assistant project. Use this skill whenever you modify code in this repo and
  need to branch, commit, and open a pull request. It enforces this project's
  house rules: branches are named after the actual change (never a generic
  tooling name), the reserved fallback branch is "nausea", user-facing text
  and metadata never reference the two-letter "artificial intelligence" token,
  and every change ships through a clean squashed PR.
---

# OxyGPT Workflow Skill

This skill is the single source of truth for how changes land in the OxyGPT
repository. Follow it end-to-end for every task that touches the codebase.

## When to use

- Any task that edits, adds, or deletes files in this project.
- Whenever you are about to create a branch, commit, or pull request here.
- Before pushing, to verify branch naming and vocabulary policy.

## Hard rules (non-negotiable)

1. **Branch names describe the change, not the tool.**
   The branch name must reflect *what this update actually does at this
   moment* — e.g. `help-text-revamp`, `journal-stats-fix`, `watcher-classify`.
   Never use generic automated-developer names such as
   `genspark_ai_developer`, `ai-developer`, or anything of that shape.

2. **Reserved fallback branch is `nausea`.**
   If — and only if — no meaningful, change-specific name can be derived
   (empty diff, or a change too ambiguous to name), create the branch as
   `nausea`.

3. **No mention of the two-letter "artificial intelligence" token anywhere
   the user or repo metadata can see it.**
   This applies to branch names, commit subjects/bodies, PR titles/bodies,
   and — most importantly — any user-facing string in the bot (help text,
   menus, button labels, notifications). Describe the product by what it does
   ("smart assistant", "دستیار هوشمند", "web search", "market analysis"),
   never by that acronym. Existing internal callback keys like `ask_ai` are
   implementation details and are out of scope, but do not introduce new
   user-visible occurrences.

4. **One squashed commit per PR.** Combine local commits into a single,
   comprehensive commit before opening/updating the PR.

## Step-by-step procedure

### 1. Derive and create the branch

Run the helper to get a change-specific name, or create the branch directly:

```bash
cd /home/user/webapp
# Print a suggested name derived from the current diff:
python .skills/oxygpt-workflow/scripts/suggest_branch.py
# Or derive AND create in one step:
python .skills/oxygpt-workflow/scripts/suggest_branch.py --create
```

The script inspects staged/unstaged/untracked files, maps them to topic words
(help, menu, database, tools, journal, watcher, chart, prompt, config, …),
scrubs forbidden tokens, and prints a kebab-case name. It returns `nausea`
when nothing meaningful can be inferred.

If you prefer to name it yourself, pick a short kebab-case phrase describing
the change and run `git checkout -b <name>`.

### 2. Make the change

Implement the task. Keep edits focused and idiomatic. For user-facing strings,
follow rule 3 above.

### 3. Guard before committing

```bash
cd /home/user/webapp
python .skills/oxygpt-workflow/scripts/guard_naming.py
```

This fails (non-zero) if the current branch violates the naming policy or if a
user-facing help/menu string contains the forbidden token. Fix any reported
issue before continuing.

### 4. Commit

Use Conventional Commits. Keep the subject free of the forbidden token.

```bash
git add -A
git commit -m "feat(help): rewrite the /help guide with a professional tone"
```

### 5. Sync, squash, and push

```bash
git fetch origin main
git rebase origin/main        # resolve conflicts preferring remote unless local is essential
# Squash all local commits into one (N = number of local commits):
git reset --soft $(git merge-base HEAD origin/main)
git commit -m "feat(help): rewrite the /help guide with a professional tone"
git push -u origin <branch> --force-with-lease
```

### 6. Open / update the PR

Open a pull request from `<branch>` into `main`. The PR title and body must
also comply with rule 3. Include a concise summary and testing notes. Share
the PR URL back to the user.

## Optional convenience: git hook

To enforce the guard automatically on every commit, install a pre-commit hook:

```bash
cd /home/user/webapp
cat > .git/hooks/pre-commit <<'HOOK'
#!/usr/bin/env bash
python .skills/oxygpt-workflow/scripts/guard_naming.py || {
  echo "Commit blocked by oxygpt-workflow naming/vocabulary guard." >&2
  exit 1
}
HOOK
chmod +x .git/hooks/pre-commit
```

## Files in this skill

- `scripts/suggest_branch.py` — derives a change-specific branch name (falls
  back to `nausea`) and can create the branch.
- `scripts/guard_naming.py` — verifies branch naming and user-facing
  vocabulary policy; suitable for a pre-commit hook.
