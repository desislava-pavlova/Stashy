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
| R2 | 02/10/2026 | bug? (needs your call) | Reports → the vs-average table (Spending/Income vs average) | A main category whose subcategory is used for the first time this period shows an average of **0.00** (and a difference equal to this period's amount), while the subcategory's own row correctly shows **no** average. Example: "Food" was never used before, and "Food › Groceries" appears for the first time this month. Groceries has no average, but Food shows 0.00. The settled rule says a category with no earlier activity has no average, never 0. The roll-up gives the main category an empty "0" entry in every earlier period. Expected (probably): no average for the main category either. Found by Claude during plan 02 Phase 4. That phase kept the numbers exactly as they were on purpose. | **done with R6** (03/10/2026): the main category now averages 0 and shows "—", like its new subcategory. |
| R3 | 03/10/2026 | change | Import page | Remove the Account field from the Import form: the file itself must have an Account column. Required columns: **Date, Account and Amount**. If the file has no Payee, Category or Memo at all, ask before importing, because there'd be no way to tell the transactions apart ("are you sure you want to import this?"). **Open questions (Claude, for planning):** (1) OFX/QFX files have no Account column (they carry the bank's own account number), so they'd need another way to choose the account, or the change applies to CSV/Excel only. (2) Does "no Payee, Category or Memo" mean the columns are missing from the file, or that they're present but empty in every row? Or should it also warn per row? (3) A row whose Account cell is blank: skip it and count it, like an unknown account today? | open |

| R4 | 03/10/2026 | change | Every export (lists and reports) | Add the time to the exported file's name, so several exports on one day get their own names. | **done 03/10/2026** (you left the timing to Claude; it was small). Names now end like `-2026-10-03_14-32-05`, the same style as backups. |
| R5 | 03/10/2026 | change | Backups | Put the stash's name in a backup's file name: the backups Claude takes before a plan phase had identical names in each stash's folder. | **done 03/10/2026.** New backups are named `<stash>_backup_<tag>_<time>.sqlite3`. `Stashy.bat`'s before-update backup now goes through the same function, so it also uses SQLite's backup API instead of a plain file copy. Existing backups keep their names; they still list, restore and count for the quarterly check. |
| R6 | 03/10/2026 | change (answered 03/10/2026, clarified the same day) | Reports → the vs-average reports (3, 4, 6), their charts and exports | **Unchanged:** with fewer than two earlier periods of data in the whole stash (or account), the reports still say "No earlier data to compare with.", and periods before the first transaction still don't count. **The change:** when those earlier periods exist, a category with no activity in any of them averages **0**. A month without a category's spending is a real zero ("I haven't been on vacation"), not missing data. A category with some activity keeps today's average: its total over the earlier periods ÷ the number of periods (600 in one of 3 months → 200). **Page:** that 0 average still shows as "—", but the **Difference** shows the real number with its colour, and the chart gets an **average dot at 0**. **Export:** Average 0.00 and the Difference filled in. **Difference:** stays current − average. So it's −583.50 where expenses are negative (Income/Expense vs average) and +583.50 in red in Spending vs average, where amounts are positive. **Overrules** only the 16/09/2026 rule "a category with no earlier activity has no average and no dot". Folds in R2, and settles plan 02's 8.8. | **done 03/10/2026**, in the same commit as plan 02 Phase 5 (your call). Rows carry `no_earlier_activity` for the "—"; for a main category its subcategories' earlier activity counts. |
| R7 | 03/10/2026 | change | Reports → Income/Expense by category (report 5), table and export | Add an **Income total** and an **Expense total** row. Rename the current **Total** row to **Net total**. Below it add a **Savings rate** row with the percentage for every period, so a year-to-date run subtotaled by month shows how it moves. (Claude: a period with no income has no savings rate and shows "—", like elsewhere.) | open |
| R8 | 03/10/2026 | change (decided 03/10/2026) | Reports → Income/Expense by category chart (report 5), when subtotaled | **A net-total line** across the periods, on the same money scale as the bars, in the net colour (blue, `--stashy-net`). It shows only when the report is subtotaled into two or more periods. **The savings rate isn't a line:** it goes in R7's new table row and in the chart's hover tooltip for each period. (A second, percentage axis would be easy to misread, and a month with very little income can swing to something like −400% and squash the chart.) | open |
| R9 | 03/10/2026 | bug | `manage.py reset_pin <name>` (the last-resort PIN reset, run in a terminal) | Giving the **owner name** when it differs from the stash's folder name only in upper/lower case (owner "Alex", folder `alex`) crashes instead of resetting the PIN. Windows ignores case in folder names, so "Alex" is taken as the folder name; then the stash's user, whose name is the lower-case `alex`, isn't found. Workaround today: type the name in lower case. Expected: either spelling works. Likely fix: compare against the folder names `list_stashes()` returns instead of asking the file system. Found by Claude during plan 02 Phase 6, which only moved the function. | open |

<!-- Next number: R10 -->
