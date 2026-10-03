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
| R2 | 02/10/2026 | bug? (needs your call) | Reports → the vs-average table (Spending/Income vs average) | A main category whose subcategory is used for the first time this period shows an average of **0.00** (and a difference equal to this period's amount), while the subcategory's own row correctly shows **no** average. Example: "Food" was never used before, and "Food › Groceries" appears for the first time this month. Groceries has no average, but Food shows 0.00. The settled rule says a category with no earlier activity has no average, never 0. The roll-up gives the main category an empty "0" entry in every earlier period. Expected (probably): no average for the main category either. Found by Claude during plan 02 Phase 4. That phase kept the numbers exactly as they were on purpose. | open |
| R3 | 03/10/2026 | change | Import page | Remove the Account field from the Import form: the file itself must have an Account column. Required columns: **Date, Account and Amount**. If the file has no Payee, Category or Memo at all, ask before importing, because there'd be no way to tell the transactions apart ("are you sure you want to import this?"). **Open questions (Claude, for planning):** (1) OFX/QFX files have no Account column (they carry the bank's own account number), so they'd need another way to choose the account, or the change applies to CSV/Excel only. (2) Does "no Payee, Category or Memo" mean the columns are missing from the file, or that they're present but empty in every row? Or should it also warn per row? (3) A row whose Account cell is blank: skip it and count it, like an unknown account today? | open |

<!-- Next number: R4 -->
