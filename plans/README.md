# Plans

Every plan Stashy has followed, in the order the work happens. A plan is
never deleted or rewritten once finished - it stays here as history. New
plans get the next number. Reasoning and day-by-day progress live in
`DEVLOG.md` (project root).

| # | Plan | Started | Status |
|---|------|---------|--------|
| 01 | [Upgrade plan](01_upgrade_plan.md) — Quicken-style features, phases 1–15 | 12/09/2026 | Phases 1–15 done |
| 02 | [Architecture cleanup](02_architecture_cleanup.md) — data safety, constraints, tooling, service layer, views split, tests | 25/09/2026 | All 8 phases done (05/10/2026); the migrations question (3.6) moved to plan 03's last phase on 10/10/2026 |
| 03 | [Structure review](03_structure_review.md) — the developer's side: bugs, dead tests, history notes, SOLID, docs, security | 10/10/2026 | Planned (S1–S21, 6 phases), plus S22–S49 from your code read (S22–S23 done) and S50–S67 from a whole-code review (13/11/2026); Q1 answered, Q2 open; waiting for a phase to start |
| 04 | [Fixes and changes plan](04_fixes_and_changes_plan.md) — the user's side: your bugs and change requests, planned into phases after 03 | 27/09/2026 | Collecting items (R1–R20 so far) |
| 05 | [The admin's look](05_admin_look.md) — Stashy's fonts and colours for the admin, still recognisable as the admin; one light/dark switch for both | 23/10/2026 | Planned (2 phases), plus AD1 (dropdown filters, 13/11/2026); waiting for a phase to start |
| 06 | [Stashy as a Windows .exe](06_exe_packaging.md) — runs without installing Python; data kept outside the program | 02/10/2026 | Idea stage, starts after 05 |

`01_upgrade_plan.md` was called `UPGRADE_PLAN.md` in the project root until
25/09/2026; older DEVLOG entries still use that name.

**Renumbered on 10/10/2026** (your call), so the numbers follow the order of
the work: the structure review, written that day as plan 05, became 03; the
fixes and changes plan went from 03 to 04; the .exe plan from 04 to 05.
Older DEVLOG entries and plan 02 still use the old numbers: there, "plan 03"
means today's plan 04, and "plan 04" today's plan 06.

**The .exe plan moved again on 23/10/2026** (your call), from 05 to 06, so
the admin's look could be plan 05 and come before it. In DEVLOG entries
written from 10/10 to 22/10/2026, "plan 05" means the .exe.
