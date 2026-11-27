# Plans

Every plan Stashy has followed, in the order the work happens. A plan is
never deleted or rewritten once finished - it stays here as history. New
plans get the next number. Reasoning and day-by-day progress live in
`DEVLOG.md` (project root).

| # | Plan | Started | Status |
|---|------|---------|--------|
| 01 | [Upgrade plan](01_upgrade_plan.md) — Quicken-style features, phases 1–15 | 12/09/2026 | Phases 1–15 done |
| 02 | [Architecture cleanup](02_architecture_cleanup.md) — data safety, constraints, tooling, service layer, views split, tests | 25/09/2026 | All 8 phases done (05/10/2026); the migrations question (3.6) moved to plan 03's last phase on 10/10/2026 |
| 03 | [Structure review](03_structure_review.md) — the developer's side: bugs, dead tests, history notes, SOLID, docs, security | 10/10/2026 | Planned (S1–S21, 6 phases), plus S22–S49 from your code read (S22–S23 done) and S50–S67 from a whole-code review and S68–S70 from a visual check and a folder tidy-up (13/11/2026), S71–S74 from the dependency upgrade and S75–S91 from part A's reviews and S92–S96 from part B's and S97–S103 from part C's (14/11/2026) and S104–S109 from part D's (15/11/2026) and S110–S112 from part E's (16/11/2026); Q1, Q3 and Q4 answered, Q2 open; Phase 1 part A (security and the PIN, with its reviews' S75–S79, S83, S86, S87) done 14/11/2026; S88 and S91 done after your yes (two small S88 edges wait for Phase 3) for your answer; Phase 1 part B (money and reports: S1, S3, S33, S50) done 14/11/2026; Phase 1 part C (reminders and transactions: S38, S39, S51, S52, S55, with S97–S99 from its reviews) done 14/11/2026; part D (15 small fixes and the phase's migration, with S104–S109 from its reviews) done 15/11/2026; part E (S105–S107, with S110–S112 from its reviews) done 16/11/2026, which completes Phase 1; Phase 2 is built in three parts (A small standalone items, B the tests, C dead bits and comments) and part A (S68, S70, S72, S73, S74, S110, S112) and part B (the tests: S11, S25, S34, S56–S62, S85, S90, S104) are done 16/11/2026; part C (dead bits, history notes out of the code, module docstrings, comments rewrapped to 72 columns and ruff's W505 switched on: S12, S13, S21, S44 and S43's comments) done 17/11/2026, which left the admin items 2.19 to 2.21 of Phase 2; part D (those three, S113–S115, with S116–S119 from its reviews) done 19/11/2026, part E (2.22 and 2.23, S117, S119, S108, S111, S118: the admin's other 500 pages and limits on stored dates, numbers and amounts, with S120–S124 from its reviews) done 19/11/2026, and S120 (Critical: a Reconcile gap too big for an amount) fixed on your answer, which completes Phase 2; Phase 3 is built in five parts (A the two tool settings, B code moves into new modules, C the forms package and shared helpers, D the small fixes, E the views pass) and part A, the two tool settings (S40, Django's own import group and two blank lines after the imports; S43, code lines wrapped at 99 instead of 119), is done 20/11/2026, with S125–S126 from its review; part B (code moves into new modules: S15, S16, S17, S47, S66; `services/accounts.py`, `services/rates.py`, `filters.py`, `ids.py`, `tips.py`, `payee_summaries()`) is done 25/11/2026, with S127–S132 from its reviews; part C (the forms package, the shared helpers and the small habits: S48, S18's Python parts, S20, with S125–S127, S129 and S132 closed on the way) is done 26/11/2026, with S133–S135 from its reviews; part D (the small fixes: S63, S64, S65, S82, S88's edges, S89, S101, S109, S116, S124, S133, S134) is done 26/11/2026, with 3.15 (our own OFX reader, ofxparse gone) after your choice, and S136–S138 from its reviews; part E (the views pass and the banners: S42, S45, S49, with S131 from part B's reviews) is done 27/11/2026, with S139–S141 from its reviews, which completes Phase 3; Phase 4 (one builder per report: S14, S94, S96, with S141 and the reports part of S140) is done 27/11/2026, with S142–S144 from its reviews and plan 04's R37–R39 |
| 04 | [Fixes and changes plan](04_fixes_and_changes_plan.md) — the user's side: your bugs and change requests, planned into phases after 03 | 27/09/2026 | Collecting items (R1–R39 so far; R22–R24 and R30 done 13/11/2026; R9 done in plan 03 on 14/11/2026) |
| 05 | [The admin's look](05_admin_look.md) — Stashy's fonts and colours for the admin, still recognisable as the admin; one light/dark switch for both | 23/10/2026 | Planned (2 phases), plus AD1 (dropdown filters, 13/11/2026); waiting for a phase to start |
| 06 | [Stashy as a Windows .exe](06_exe_packaging.md) — runs without installing Python; data kept outside the program | 02/10/2026 | Idea stage, starts after 05 |
| 07 | [Dependency upgrade](07_dependency_upgrade.md) — Python 3.12 → 3.14, Django 6.1 → 6.1.3, every library pinned | 13/11/2026 | Done 14/11/2026 (ran before 03, so its number is out of order) |

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

**Plan 07 ran before plan 03** (14/11/2026). Plans get their number when
they're written, and the dependency upgrade was written after 03–06 but
done first. Renumbering them all again would muddle the history, so 07
keeps its number.
