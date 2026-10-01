# Stashy Fixes and Changes Plan

Plan 03, started 27/09/2026. It follows plan 02 (architecture cleanup).
**Status:** collecting items. No phases yet.

It works in two stages:

1. **Collecting (now).** While plan 02 is in progress, the bugs and changes
   you notice go into the table below. **Nothing here is worked on during
   plan 02** unless you say "fix R<n> now".
2. **Planning (after 02).** We go through the list together. Unclear items
   get questions, and the rest become phases with checkboxes, a decisions
   log and a DEVLOG entry, the same way as plans 01 and 02. The item table
   then stays at the bottom as the plan's source.

## How to add something

Either write it below yourself, or tell Claude "add to 03: …" in chat. Claude
then only files it here and doesn't start on it. One line is enough. Details
help but aren't required:

- **Where:** which page or feature
- **What happens:** what you saw (a screenshot path in `images/` is fine)
- **What you expected:** how it should be
- **Kind:** bug (something is wrong) or change (works, but should be
  different)
- **Urgent?** Mark it urgent if it blocks you. Claude will then ask whether
  to fix it before plan 02 finishes.

## Collected items

| # | Added | Kind | Where | What | Status |
|---|-------|------|-------|------|--------|
| R1 | 01/10/2026 | bug | Search boxes (Transactions, Categories; check the others too) | Searching ignores upper/lower case only for A-Z: "лидл" doesn't find "ЛИДЛ". The search uses `icontains`, which SQLite runs as `LIKE`, and `LIKE` only folds English letters. Expected: case is ignored in every alphabet, like payee and category names since plan 02 Phase 2 (`name_key`). Found by Claude during that phase. | open |

<!-- Next number: R2 -->
