# Stashy Structure Review

Plan 03, started 10/10/2026. Source: a second full pass over the code after
plan 02 finished, on 10/10/2026. You asked for: dead code, tests written for
one plan step that no longer test anything, SOLID, too little or too much
documentation, security discrepancies, and anything else a normal, fully
working Django app should have. The review itself changed no code.

**Where it sits:** this plan is the developer's side, making the code as fit
as possible. Plan 04 is the user's side: updates and fixes to what you see
and use. Plan 05 gives the admin Stashy's look, and plan 06 packages
Stashy as an .exe. The plan was written as plan 05 and renumbered the same
day (your call), so the plans read in the order the work happens. The old
plan 03 (fixes and changes) is now 04, and the old plan 04 (.exe) is now 06
(it was 05 until 23/10/2026, when the admin's look took that number).

**Status:** Q1 answered (yes, 10/10/2026). Q2 is waiting: you asked for more
detail, which is below. You're reading the code yourself and may add
findings (see "Your additions"). A phase starts when you name it.
*13/11/2026:* your read added S22–S49, and a whole-code review added
S50–S67 (section G); every one is in its phase's checklist.

**Baseline (10/10/2026):** 723 tests green. `ruff check .` and
`ruff format --check .` are clean, `manage.py check` finds nothing, and
`makemigrations --check` finds no missing migration.

**Guiding idea:** the same as plan 02's C3. No new features, and nothing on
screen changes unless the finding is itself a bug. Every bug fix gets a test
that fails on today's code. No phase changes the database schema, so none
needs a migration or a backup of the real stash. If that changes while
working, the backup comes first, as usual. *13/11/2026:* S46 (Phase 1)
changes a field's default, which needs one small migration, so Phase 1
backs up the real stash before it.

Finding numbers start with **S** so they can't be mixed up with plan 01's
D/R/L/A, plan 02's F/C/Q or plan 04's R items.

---

## What's already in good shape

Checked, so nobody has to check again:

- **No dead code at function level.** A scanner compared every function,
  method and constant name with every use in Python, templates and
  JavaScript. Plan 02's cleanup held: the only leftovers are the few in
  S12.
- **Every template, URL name and template filter is in use.**
- **No script injection path:** no `|safe` in any template, and the few
  `innerHTML` uses only ever receive numbers and a currency symbol.
- **The basics hold:** every form that posts is CSRF-protected, and every
  page needs a login unless it says otherwise. The "next" redirect refuses
  other sites, and a backup name can't point outside its stash's folder.
  The admin login goes through the PIN lockout, and a new recovery code
  never travels in the cookie.
- **Money stays `Decimal`,** every change is audited, and the tests run
  through the real middleware.

## Questions for you

- ~~**Q1 (S7):** Should **New recovery code** ask for the current PIN, like
  **Change PIN** already does? And should wrong current-PINs on both pages
  count toward the 5-tries lockout, with a lockout logging you out to the
  PIN screen?~~ **Answered 10/10/2026 (you): yes to all of it.** Today
  anyone at an unlocked Stashy can make a new recovery code without knowing
  the PIN, then use it to set a new PIN. That makes the Change PIN check
  pointless. Item 1.9 does it.
- **Q2 (S9):** After Phase 5 moves every script out of the HTML, should
  Stashy send a **Content-Security-Policy** header? *You asked for more
  detail on 10/10/2026.*
  - **What it is:** one extra line Stashy sends with every page, a list
    of where the browser may load things from. Stashy's would say: run
    only script files that come from Stashy itself. Never run a
    `<script>` block or an `onclick=` written inside the page, and never
    load anything from the internet.
  - **What it protects:** the first lock is Django's escaping. Every
    payee, memo or category name, including text from an imported bank
    file, is shown as plain text, never run as code. CSP is a second lock
    behind it. If a future mistake ever let such text through as HTML,
    the browser still wouldn't run it. Without that second lock, script
    running inside Stashy could read and change everything your session
    can.
  - **How big the risk is:** small. The review found no hole today (no
    `|safe`, and `innerHTML` only gets numbers). Stashy only runs on your
    computer, and the data comes from you and your bank's files. CSP is
    insurance against future mistakes, not a fix for a current bug.
  - **What it costs:**
    - Phase 5 moves the scripts out of the HTML anyway, for structure
      (S19). After that, the header itself is a few lines in the settings
      (Django 6.1 has `ContentSecurityPolicyMiddleware` built in).
    - From then on, every script must live in a static file. A test can
      enforce that.
    - Styles would stay relaxed: inline styles are far less dangerous, and
      Bootstrap needs some.
  - **The risk of the change:** if a spot is missed, that one button or
    chart silently stops working. Only the browser's console (F12) says
    why, and there's no browser here.
  - **Options:**
    - **(a) Turn it on straight away.**
    - **(b) "Report-only" first, then on. Recommended.** For a while the
      browser only *logs* what it would have blocked and blocks nothing.
      You click through each page once with F12 open and tell Claude about
      any red "Content-Security-Policy" line. Once nothing shows up, it's
      switched to blocking.
    - **(c) No header.** Phase 5 still moves the scripts out, for
      structure.

---

## Findings

### A. Bugs (each one confirmed by running it)

- **S1 — A transfer of exactly 0.01 is refused.** `TransferForm.amount`
  has `min_value=0.01`, a float. Python compares `Decimal` and `float`
  exactly, and the float 0.01 is really 0.01000000000000000020…, so
  `Decimal("0.01")` counts as smaller. Checked: the form answers "Ensure
  this value is greater than or equal to 0.01." Fix: `min_value=CENT` (a
  `Decimal`).
- **S2 — Two hand-edited URLs crash with "Something went wrong"**
  instead of "page not found":
  - `transactions/add/?reminder=abc`;
  - the mass re-categorize on a category's usage page, posted with a
    `move_to` that isn't a number.

  Both send unchecked text to the database lookup. Only a hand-edited URL
  or form gets there, but it fills `logs/stashy.log` with tracebacks. Fix:
  check the value first and answer 404 or show a message. A test covers
  each case.
- **S3 — The Income & expense vs average export lacks two rows that the
  page shows.** The page adds an "Income total" and an "Expense total" row
  (built inside `reports_view`). The export builds the table on its own
  and never gets them. Checked: page groups `income, expense, summary`;
  export rows `Salary, Food, Total`. That breaks the rule "what you export
  matches what's on screen". Fix: `reporting.py` builds those two rows
  once, and both the page and the export use that. S14 then does this for
  every report.
- **S4 — Name sorting ignores case only for A-Z.**
  - **Dropdowns** (`categories_alphabetical()`) sort with SQLite's
    `LOWER()`, which folds only A-Z. Checked: "Аптека, Вода, бензин, яйца"
    instead of "Аптека, бензин, Вода, яйца".
  - **The Payee and Category columns on Transactions and the account
    register** sort on the plain name. That puts "lidl" after "Zara" in
    any alphabet.

  This is R1's cause (plan 04) in sorting instead of searching. Fix: sort
  on the `name_key` column that already exists for exactly this.
- **S5 — An internal code shows on screen.** The Number field's help text
  on the transaction form ends in "…starting over each year, **D9**)". Fix:
  drop "D9".
  *12/11/2026 (template review): the form page no longer shows this help
  text at all; its template renders the Number box without it. Templates
  are yours to word, so it isn't put back: the fix becomes deleting the
  unused help text in `TransactionForm.__init__`.*
- **S6 — Edit mode's "saved" flash ignores dark mode.** The animation uses
  `--stashy-success-rgb`, which is never defined, so its fallback, Bootstrap's
  fixed green, always applies. Meanwhile `--stashy-positive-soft` (a pale
  green with a dark-mode twin) is defined and used nowhere. Fix: use it.

### B. Security

- **S7 — Inside the app, getting a new recovery code is easier than
  changing the PIN.** See Q1. Also: `ChangePinForm.clean_current_pin()`
  checks the PIN without counting wrong tries, so all 10,000 PINs can be
  tried there with no lockout. **Decided (Q1, 10/10/2026):**
  - New recovery code gets a current-PIN box, like Change PIN.
  - A wrong PIN on either page counts toward the same 5-tries lockout as
    the PIN screen.
  - Reaching the lockout logs you out to the PIN screen, which shows the
    usual "try again in about N minutes" message.
- **S8 — `.env.example` ships a usable secret key.** It says
  `SECRET_KEY=change-me-to-a-long-random-string`. README's manual setup
  copies it, and Stashy then runs with a key that's public on GitHub. The
  settings say there's deliberately "no fallback" for exactly this reason.
  Fix: leave the value empty, so Django refuses to start until it's set.
  Related: `bootstrap.py` writes `.env` from its own text, not from
  `.env.example`. The two have already drifted, and README says
  `.env.example` "is copied to .env on first run", which isn't true.
  Fix (Claude's call): `bootstrap.py` copies `.env.example` and fills in a
  new key and the port. One template, and README becomes true. Your own
  `.env` is never touched: this only runs when there's no `.env` at all.
- **S9 — Inline scripts block a Content-Security-Policy.** There are 8
  inline `<script>` blocks, 6 `onchange=`/`onclick=` attributes and one
  `javascript:history.back()` link. See Q2 and S19.
  *12/11/2026 (template review): `history.back()` is in 4 templates, not 1:
  `generic_form`, `generic_confirm_delete`, `reminder_edit_scope` and
  `transaction_form`. S19's real Cancel link covers all four.*
- **S10 — Small hardening odds and ends.**
  - `manage.py check --deploy` shows four HTTPS warnings that can never
    apply to a localhost-only app. Silence those four by name, with a
    comment, so a real new warning stands out.
  - The "last stash" cookie can be read by page scripts. Nothing secret
    is in it, but `httponly` costs nothing.

### C. Leftovers: dead tests, dead code, history notes

- **S11 — 21 tests only check that something removed stays removed.**
  They can't catch a bug: they'd fail only if someone added the old thing
  back on purpose. They guard history, not behaviour.
  - Fields and models: Category `is_active` (2), Reminder
    `auto_enter_days_before`, `is_active`, `name` (4), "biweekly", tags (5),
    `Transaction.payee_name`, payee default fields.
  - Removed text: the old starter-categories button, "top5-data", two old
    helper texts on the account form (2), two old Settings cards (2).
  - `WatchedExpensesMigrationPrefillTests` says it checks plan 01's Phase
    13 migration but never runs it. It only sets a many-to-many field.

  Fix: delete those 21. Three more tests mix a real check with "old label
  gone" lines; keep the check and drop the old-label lines. Where a deleted
  test shares its class with a real one (Skip, the reminder's label,
  weekly-every-2-weeks, payee search), keep the real one under a name that
  says what it checks. **Claude's call:** negative checks that protect
  behaviour stay (no pager for a short range, a secret never shown twice,
  nothing leaked into a page).
- **S12 — Dead bits.**
  - `stashyDate()` in `charts.js` is never called.
  - `CheckRatesForm` in `forms.py`: the settings view passes it as
    `check_rates_form`, and `settings.html` never uses it (found
    12/11/2026).
  - `.inline-add-row` styles a register row that no longer exists.
  - `db_router.clear_current_stash_alias()` is used only by tests; they
    can use `use_stash()`. `set_current_stash_alias()` stays, since
    CLAUDE.md uses it for the shell.
  - `STATICFILES_DIRS` points at a `static/` folder that doesn't exist.
  - `ReminderForm.Meta.help_texts["amount"]` repeats the model's own help
    text word for word.
  - `dashboard.REMINDER_LOOKAHEAD_DAYS` is only another name for
    `Reminder.DUE_SOON_DAYS`.
  - A stale comment says export files end in `<YYYYMMDD>`. Since plan 04's
    R4 they also carry the time.
  - `mascot-full-360.webp` is never used. **Claude's call:** use it in
    S19's shared page as the small-screen image (it was made for that),
    instead of deleting it.
- **S13 — History notes still in code comments.** Plan 02's C2 moved
  them to DEVLOG for the files it touched and left the rest "for when a
  file is next edited". 48 lines in 12 files still read like a changelog
  ("2026-09-14 follow-up", "Phase 12:", "the user's own call,
  16/09/2026").
  - **Number clash:** code comments say "R3", "R4", "R5", "R8" meaning
    plan 01's report decisions. Plan 04 has its own R1–R9, so `forms.py`'s
    "R3: reports default to This month" reads as plan 04's R3 (the import
    change). CLAUDE.md's "(R1)" for the category-type rule has the same
    clash with plan 04's R1 (the search bug).
  - **Fix:** one pass. Describe the rule in words, and keep the "why".
    D1–D10 stay: CLAUDE.md uses them as the names of settled rules.
  - **Boilerplate:** Django's template docstrings in `stashy/urls.py`,
    `wsgi.py` and `asgi.py`, and `.gitignore`, which is GitHub's 200-line
    Python template (Flask, Scrapy, Celery, SageMath…).
    **Claude's call:** cut `.gitignore` down to what Stashy really has,
    keeping every entry that matters here.

### D. Structure (SOLID and Django conventions)

- **S14 — The Reports page is one 231-line function.** `reports_view`
  computes all eight reports inline. Part of report 6's logic (the summary
  rows) lives only there, which is how S3 happened. The export registry
  builds its own versions of the same tables.
  - **Fix:** one builder per report in `reporting.py` (or a small
    `reports` module) that returns that report's table and chart data. The
    page loops over them, and `REPORT_EXPORTS` points at the same
    builders.
  - **Behaviour:** identical, apart from S3. It also makes "only compute
    what's on screen" possible later, if Reports ever feels slow.
- **S15 — Domain rules live in forms.**
  - **What:** `AccountCreateForm.save()` creates the opening-balance
    transaction, and `TransferForm.save()` creates both transfer legs.
    Because the rule sits in a form, the admin's **Add account** skips the
    opening balance, which D1 says every account has. Plan 02's F54 fixed
    the same kind of gap for other rules.
  - **Fix:** `services/accounts.py` with `open_account()` and
    `transfer()`. The forms, the welcome wizard and the admin's add page
    all call it.
- **S16 — View modules borrow each other's private helpers.**
  - `exports.py` imports `_report_filters` from `reports.py` and
    `_filtered_transactions` from `transactions.py`. Every view imports
    `_`-named functions from `_helpers.py`.
  - The CSV/XLSX writers sit in `_helpers.py` but only `exports.py` uses
    them.
  - `forms.py` holds two helpers that aren't forms
    (`watched_expenses_picker_context`, `convertible_currencies`).
  - **Fix:** a `finance/filters.py` with public `transaction_filters()`
    and `report_filters()`. The writers move into `exports.py`. The helper
    names lose the underscore, since the module name `_helpers` already
    says "internal". The two helpers move next to their callers.
- **S17 — `currency.py` does three jobs.** It holds the currency list and
  money formatting (no database), `RateTable` (database reads) and the
  Frankfurter fetch (network and writes). That's why `models.py` and
  `currency.py` import each other, with two "Imported here" workarounds.
  Fix: move `RateTable`, `convert_balances()` and `fetch_latest_rates()`
  to `services/rates.py`. `currency.py` keeps only plain data and
  formatting, and both workarounds go.
- **S18 — The same thing written in several places.**
  - **Category totals in `reporting.py`:** "sum each category's converted
    amounts over a range" is written five times. One helper.
  - **Reminder copies:** the same 8-field copy is written twice in
    `Reminder` (`record_period`, `regenerate_occurrences`), although
    `SERIES_FIELDS` already lists the fields. The "who is it for" label is
    also written twice.
  - **Category type order:** both a dict and a `Case/When` copy of it.
  - **Stash folder layout:** "backups" is named three times
    (`backup.BACKUP_DIR_NAME`, `registry.BACKUPS_DIR_NAME`, a bare string
    in `views/settings.py`), and "db.sqlite3" four times. `bootstrap.py`
    repeats `list_stashes()` with its own hard-coded `stashes` folder.
    Plan 06 (the .exe) wants every folder path in one place.
  - **JavaScript:** the money formatter is copied in `dashboard.js` and
    `account_reconcile.js`. The number helpers live in `charts.js`, so
    every page loads Chart.js (about 200 KB) even with no chart on it.
  - **Plurals:** written four different ways (`_plural()`, inline
    `'s' if`, `categor{'y'…}`).
  - **Form styling:** `BootstrapFormMixin` exists, but four forms still
    set their CSS classes by hand.
  - **Dates in imports:** `importing._parse_date()` repeats
    `regional.parse_date()`.
- **S19 — Templates repeat themselves, and the generic form isn't
  generic.**
  - **Shared page frame:** seven pages (the 5 picker and PIN pages, the
    welcome wizard, the error page) each repeat the same 25-line frame:
    head, mascot panel, wordmark, theme button. One shared base template.
    *12/11/2026 (you, reading `registration/`): one base for the left side,
    so changing the image means changing one file. This is that item: the
    mascot image is in all 7 files today.*
    *Later on 12/11/2026 (you, after noticing `welcome_base.html` repeats
    the login frame): two frames with names that say what they are. Your
    choice: `base.html` stays (inside the app, with the sidebar; it's the
    name every Django developer looks for first) and the new frame is
    `mascot_base.html` (squirrel on the left, card on the right), both at
    the top of `finance/`. Three levels, as Django's own docs suggest: the
    two frames; thin area layouts that sit with their pages (S28 "by use"):
    `welcome/welcome_base.html` adds the step counter and the date-picker
    scripts, `error_base.html` beside `404.html`/`500.html` adds S30's
    error tag and the Back to Stashy button; then the pages. The five
    picker and PIN pages extend `mascot_base.html` directly. Today's
    differences become switches in the frame: the "Stash smarter." line
    under the squirrel stays on login, picker and create stash only. You
    chose the light/dark switch on all seven (today forgot PIN, recovery
    code and both error pages lack it); the frame loads `stashy.js`, which
    is a static file, so the 500 page can still do without any context.*
  - **Generic form:** `generic_form.html` carries Reminder-only and
    Category-only scripts. Two of them are the same code with different
    field names. Move them into one static `forms.js`, driven by
    `data-` attributes on the fields, the way `data-range-date` already
    works.
    *12/11/2026 (you, reading templates): "generic forms seems really
    random", maybe the name, maybe what's inside. Both: the name says how
    it's used, not what it is, and 100 of its 143 lines are five scripts for
    one form each (one for categories, four for reminders), plus the
    reminder-only "whole series" banner. Claude's additions to this item:
    rename it `form_page.html` (and `generic_confirm_delete.html` to
    `delete_page.html`), and give reminders a small `reminder_form.html`
    that extends it and adds the series banner, so the shared page holds
    only what every form has: title, fields, Save/Cancel, tips. You
    accepted these the same day.*
  - **Auto-submit:** `onchange="this.form.submit()"` (5 places) becomes
    a `data-auto-submit` attribute handled in `stashy.js`.
  - **Cancel button:** it becomes a real link back to the list instead
    of `javascript:history.back()`, which leaves Stashy if the page was
    opened directly.
- **S20 — Small Django habits and rule slips.**
  - `reminder_skip` checks for POST by hand (a GET redirects); everything
    else uses `@require_POST` (a GET gets "405 not allowed").
  - `SystemManagedCategoryGuard` loads the category, then the edit view
    loads it again.
  - The admin's Transactions, Reminders and Reconciliations lists load
    each row's payee and category one row at a time. Fix:
    `list_select_related`.
  - **Money rule:** CLAUDE.md says aggregates go through `to_cent()`.
    The Transactions total and the payee totals don't, and
    `Account.balance` and `reconciled_balance()` round with
    `.quantize(CENT)`, a different rounding mode.
  - `transaction_form_view` is 104 lines. Pre-filling from a reminder
    becomes its own helper, and "the memo used last time with this
    category" becomes a `Payee` method.
  - An unknown `?range=` value on Transactions silently becomes "Last 12
    months" (Reports' fallback). **Claude's call:** treat it as the
    page's own default, "This year".

### E. Documentation

- **S21 — Too little where it matters, too much where it doesn't.**
  - **Too little:** no module docstring in `models.py`, `forms.py`,
    `middleware.py`, `money.py` or the view modules. `resolve_range()`,
    the date-range rules every report and the Transactions page rely on,
    has none. `reporting.py`'s module docstring describes only half the
    module. `Profile`'s docstring says "preferences", but it also holds
    the owner name, the wizard step, the PIN lockout and the recovery
    code.
  - **Too much:** the history notes (S13), Django boilerplate, and long
    narrative comments that retell what the next line says.
  - **Wrong:** README's `.env.example` line (S8). The reminder tip
    hard-codes "7 days" instead of reading `Reminder.DUE_SOON_DAYS`. So does
    the dashboard's empty Due-soon message ("Nothing due this week", "in
    the next 7 days"), while the line above it reads the real value (found
    12/11/2026).
  - **Fix:** one short paragraph at the top of each module (what it's for
    and what it must not do), docstrings on the few central functions,
    and trim the rest.

### F. Your additions

Things you find strange while reading the code yourself go here. Write
them in yourself, or tell Claude "add to 03: …" in chat. Claude files each
one as the next S number (S22, S23, …), checks it against the code, and
then proposes which phase it joins. A new item doesn't start on its own.
Anything you ask for while reading the code goes here, even a change you'd
see on screen (your call, 24/10/2026). Claude fixes nothing from it until
you say so. Plan 04 ("add to 04: …") stays for what you notice while using
Stashy.

| # | Added | Where | What | Status |
|---|-------|-------|------|--------|
| S22 | 24/10/2026 | `audit/admin.py`, `format_timestamp()` | The audit log's time showed milliseconds and the offset (`2026-09-15 15:06:48.930 +03:00`): more than you need, and the `+03:00` kept confusing you. Now `2026-09-15 15:06:48`, still in your own time zone and year first (you chose that over the stash's date format). Overrules part of plan 01's A3 (15/09/2026). | **done 24/10/2026**, fixed before it was filed. |
| S23 | 24/10/2026 | `audit/tests.py`, `OnCommitUsesTheStashTests` | PyCharm showed a red "Unresolved reference 'db_for_write'" in a test that runs fine: `mock.patch("audit.signals.router.db_for_write")` names a path PyCharm can't follow. Now `mock.patch.object(audit_signals.router, "db_for_write", …)`. | **done 24/10/2026**, fixed before it was filed. |
| S24 | 11/11/2026 | `finance/management/commands/reset_pin.py`, `handle()` and the `--pin` help text | Without `--pin`, the command asks for the new PIN with `getpass`, which shows nothing at all while you type ("Omit to be prompted (input is hidden)"). You'd like a `*` for each digit, so you can see how many you've typed. Python 3.14's `getpass` can do that (`echo_char="*"`), but Stashy runs on 3.12. Fix: a small helper that reads one key at a time on the Windows console (`msvcrt.getwch()`), prints `*` per key, takes Backspace back, finishes on Enter and stops on Ctrl+C; anywhere else (input piped in, or not Windows) it falls back to plain `getpass`. The help text becomes "Leave it out to be asked (each digit shows as *)". A test feeds it keys. Proposed phase: 1, next to S7, the other PIN-entry item. Note: plan 04's R9 (the same command crashing when the owner name's case differs from the folder's) is in this file too. *11/11/2026 (you): do them together. Both are Phase 1, item 1.10.* | open (Phase 1) |
| S25 | 11/11/2026 | `finance/services/importing.py`, `_detect_date_format()` and the comment above it | You'd like date detection to cover the whole file: one date like `1/13/2022` means every date in it is month-first. **It already works that way (Claude checked the code):** every row's date is looked at before any row is read, and the first date that can only be read one way settles the whole file, so with `1/13/2022` anywhere, `03/04/2022` is read as 4 March. Two things make it look otherwise. The comment says "Ambiguous rows … are skipped", which means skipped *while guessing*, not left out of the import. And today's tests have only one row each, so nothing proves an earlier ambiguous row follows a later deciding one. Fix: reword the comment, and add a test with ambiguous rows before the deciding one. Claude's find: a file mixing both ways (`13/01/2022` and `01/13/2022`) trusts whichever comes first. Under plan 04's R3 (a date Stashy can't read fails the import, listing the rows), such a file will fail and say which rows, which is enough. Proposed phase: 2 (documentation and tests). | open |
| S26 | 11/11/2026 | `finance/templates/finance/account_reconcile_review.html`, the reconcile page | **Mark all** sits on its own line above the two tables, on the right, while **Finish reconciling** and **Cancel** are at the very bottom, on the left. You'd like one line: Mark all on the left, Finish reconciling and Cancel on the right, on the line where Mark all is now. Proposed phase: 5 (templates). | open |
| S27 | 12/11/2026 | `finance/templates/finance/account_reconcile_start.html`, the reconcile page's first step | Today only **Opening balance** is a row with its label on the left and the amount on the right. The two statement boxes have their labels above them, fill the card's width, and the balance box has the hint "The ending balance shown on your bank or card statement." under it. You'd like all three as rows, labels on the left and values on the right, with the hint centred below. **Claude's suggestion (your choice):** the three rows as you describe, with the two boxes only as wide as a date or an amount, right-aligned, so the three values line up under each other like on a statement. The hint goes under its own label, on the left, small and grey, instead of centred below: it stays next to what it explains, and the right side stays a clean column of values. Start reconciling and Cancel go on the right, the same side as on the next page after S26. On a phone the rows fall back to the label above its box. Proposed phase: 5, with S26. *12/11/2026 (you): the hint under its label, on the left, on one line, not two. So the wording stays short ("as shown on your bank or card statement") and the label side is wide enough to hold it on one line on a normal screen; only a phone may wrap it.* | open (decided 12/11/2026) |
| S28 | 12/11/2026 | `finance/templates/finance/` (34 pages and an `includes/` folder, all side by side) | You're uneasy that every template sits in one folder; you'd usually split them by area (transactions, reminders, …). **Claude:** both are normal in Django. One flat folder named `<thing>_<action>.html` is Django's own default (its generic views look for `finance/transaction_list.html`), and it's fine for small apps. At Stashy's size, folders per area are clearer, and the code is already split that way: `views/` has one module per area and the tests one file per area. So the templates follow the same map: `accounts/` (list, detail, delete, transfer, both reconcile steps), `categories/`, `payees/`, `transactions/`, `reminders/`, `imports/`, `reports/` (with its three report-only pieces from `includes/`), `settings/`, `dashboard/`, `welcome/`. Shared pages stay at the top of `finance/`: `base.html`, `error_page.html`, and S19's `form_page.html` and `delete_page.html`; `includes/` keeps only pieces several areas use. Two folders stay where they are, because Django looks for them by name: `registration/` (the picker and PIN pages; Django's login view looks there) and `404.html`/`500.html` at the top. Claude's call: files keep their full names (`transactions/transaction_list.html`, not `transactions/list.html`), so PyCharm's tabs and search results never show five files all called `list.html`. The work is mechanical: move the files, update each view's `template_name`, the `{% extends %}`/`{% include %}` paths and the tests that name a template. Proposed phase: 5, as its first step, so S19's new files land straight in their folders. *12/11/2026 (you): "folders it is", and you accept Claude's other suggestions here and in S19, S25 and S27.* *Later on 12/11/2026 (you): each layout sits with the pages that use it ("by use"), not all layouts together. So `welcome_base.html` goes into `welcome/` with the five steps, and the error pages' layout leaves `finance/` to sit beside `404.html` and `500.html` at the top of `finance/templates/`, renamed `error_base.html` (Stashy names its layouts `*_base.html`, like `welcome_base.html`). `404.html` and `500.html` themselves don't move: Django looks them up by exact name at the top, and moving them would need our own error handlers in `stashy/urls.py`. The only cost: `error_base.html` is outside the app's own `finance/` folder, which normally keeps names from clashing with another app's, and nothing Stashy installs has a template of that name.* | open (decided 12/11/2026) |
| S29 | 12/11/2026 | `finance/templates/finance/settings.html` (Watched expenses card) and `welcome_watched_expenses.html`, with `includes/category_checkbox_filter.html` | Found while checking the two red "Invalid id reference" marks PyCharm shows in `settings.html`. **Those two are false alarms (Claude checked the rendered page):** every label points at a box that exists (`id_main_currency`, …, `id_watched_expenses_title`) and no id repeats. PyCharm reads the template as plain HTML, so it takes `{{ field.id_for_label }}` as a literal id; nothing to change in the code. The real find: the **Categories** label next to the tick-some-categories dropdown has no `for`, and the dropdown's button has no id, so clicking the word does nothing and a screen reader doesn't connect the two. Fix: the include gives its button an id built from `field_name`, and both pages' Categories labels point at it with `for` (clicking the label then opens the dropdown). Transactions has no visible label there and needs none: its button text already says "All categories" or "3 categories selected". Proposed phase: 5 (templates). | open |
| S30 | 12/11/2026 | `finance/templates/404.html`, `500.html` and their shared `finance/error_page.html` (`error_base.html` beside them after S28) | You like how the two error pages look, but they're hard to tell apart from the PIN login page: same squirrel, same card, nothing that says "error". You chose a small coloured tag above the heading, with a warning icon (`bi-exclamation-triangle-fill`, from the Bootstrap Icons Stashy already ships): **Error 404** in soft gold (a missing page is a mild problem, usually an old link) and **Error 500** in soft red (something really broke). Everything else stays as it is: the squirrel, the wordmark, the headings, the text and the button. How: `error_page.html` gets a block for the tag and each page fills in its code and colour. The 500 page gets no context, and the tag needs none (plain HTML and a class). Claude's call: the colours come from the tokens the reminders' Overdue and Today badges already use (`--stashy-negative(-soft)`, `--stashy-gold-soft`/`--stashy-gold-ink`), as one shared pair of pill classes that those badges switch to as well, so the colours are defined once and dark mode comes for free. A test checks that each page shows its tag. Proposed phase: 5 (templates). | open (decided 12/11/2026) |
| S31 | 12/11/2026 | `finance/views/dashboard.py`, `dashboard.html` (Net worth tile) | Found by the template review you asked for (12/11/2026), like S32-S37. The dashboard's net worth quietly leaves out every account whose currency has no known exchange rate. The Accounts page warns about this ("No known rate for USD — not included above"), and the dashboard view gets the same list (`missing_rates` from `convert_balances()`) but the page never shows it, so the total is simply lower, with no hint why. Fix: the same warning under the Net worth tile, from one small include that the Accounts page uses too (no copy-paste). A test with a USD account and no USD rate. Proposed phase: 1. | open |
| S32 | 12/11/2026 | `finance/templates/finance/settings.html`, the Exchange rates table | The "As of" date is `{{ known.date }}` without `sdate`, so it ignores the stash's date format (a month-first stash still sees day-first). "Source" shows the stored code (`manual`, `Frankfurter`) instead of its label from `ExchangeRate.Source` ("Typed in", "Frankfurter (ECB)"): the same kind of slip as S5. Fix: `sdate`, and the label instead of the code. A test with a month-first stash and a typed-in rate. Proposed phase: 1. | open |
| S33 | 12/11/2026 | `dashboard.html` ("Saved in" tile), `reports.html` (report 5's "Savings rate"), `percent_fmt()` in `finance/views/reports.py` | The savings rate is printed the Python way, so a stash with the `10.000,50` number format sees "71.2%" next to "1.200,00 €". Fix: through the stash's number format (`money:1` in the templates, the same `regional` formatting in `percent_fmt()`). A test with the `10.000,50` format. Proposed phase: 1. | open |
| S34 | 12/11/2026 | `finance/tests/test_pages.py`, `RenderCheckTests` | The test that catches leaked template code renders only 14 pages and looks only for `{#`, `#}`, `{%`, `%}`. Not covered: the transfer page, both reconcile steps, every add/edit/delete page, category usage, payee detail, the reminder scope page, backup restore, the welcome steps, login, picker, create stash, forgot PIN, 404 and 500. The review rendered all of them and none leaks today, so this is a safety gap, not a bug. Your call (12/11/2026): "fix whatever you need here", so Claude's call on the details: every page Stashy has, plus a check that fails when a new page in `finance/urls.py` is missing from the list (with a short, commented skip list for POST-only URLs and exports), so a new page can't be forgotten; it also looks for `{{`/`}}` outside `<script>` blocks; and the test's `date.today()` becomes `timezone.localdate()`, as the rules say. Proposed phase: 2. | open |
| S35 | 12/11/2026 | Templates across `finance/` and `registration/`; `transaction-quick-edit.js`, `reports.js` | Things a screen reader or keyboard user trips over; nothing changes on screen except that clicking a label now puts you in its box. **Labels not linked to their box** (the S29 kind): Import (Account, File, Date format), the Reports filters (Date range, Subtotal by, Average interval, Account, Start, End); on Reports the "?" icon moves out of the label, so clicking it doesn't jump into the box. **Controls with no name:** the login PIN box, the reconcile tick boxes ("Reconcile Lidl 05/11/2026"), Edit mode's controls in `<template id="quick-edit-controls">`, and the pencil and bin buttons on the Accounts, Categories, Payees and Reminders lists (`aria-label="Edit"`/`"Delete"`, as Transactions already has). **Not reachable with Tab:** Reports' show/hide-subcategories arrow (`report-toggle-icon`, in both report tables and the Net worth table) becomes a real button that says whether it's open, looking the same; the "?" help icons get a name ("About Date range"). **Headings:** 15 pages use `<h3>` for their page title; they get `<h1 class="h3">` like the rest (same look). **Charts:** the 8 report charts and the dashboard donut get `role="img"` and a short description. **Transactions' Rec. tick** gets hidden "Reconciled" text. Your call (12/11/2026): none of this is yours to decide. Proposed phase: 5. | open |
| S36 | 12/11/2026 | `account_list.html`, `transaction_form.html`, `reports.html`, `includes/sidebar_contents.html`, the transaction counts | Small on-screen slips, Claude's to fix (polish): Accounts' warning repeats a currency once per left-out account ("No known rate for USD, USD"), so each shows once (with S31's shared warning); the transaction form's browser tab says "New transaction" while its heading says "Enter reminder" (the title gets the same if/elif); Reports' error box uses internal field names ("Interval: Select a valid choice…" for the box labelled "Average interval"), so it uses the labels; the sidebar highlights Settings on Change PIN, New recovery code and Restore backup too; counts like "2500 transactions · showing 1001–2000" get the stash's thousands separator (worked out from the code, check when building). Proposed phase: 5. | open |
| S37 | 12/11/2026 | `reports.html`, `welcome_categories.html`, `includes/head_assets.html`, `account_reconcile_review.html`, three pages with an inline width, `settings.html` | Leftovers in the templates, nothing you'd notice today: Reports' "View them" link puts display-format dates in the URL (`start|sdate`), while Stashy's own links carry YYYY-MM-DD (`regional.parse_date()`), so `date:"Y-m-d"`; "26 top-level categories" on the welcome step is typed in, so the view counts them from the spreadsheet; the `theme-color` tag is the light colour in both modes, so it gets a dark twin; reconcile's hidden balance and `data-amount` use the raw value where Transactions uses `unlocalize`, so they do too; `style="max-width: 560px;"` written into `generic_confirm_delete`, `backup_restore_confirm` and `reminder_edit_scope` becomes one class in `stashy.css` (with S19's `delete_page.html`); Settings' Restore link gets `urlencode` on the backup name (safe today: the names are plain letters, digits, `-`, `_` and `.`). Proposed phase: 5. | open |
| S38 | 12/11/2026 | `finance/templates/finance/account_detail.html`, the register's Status column | Rows that aren't reconciled show "—" in a `badge text-bg-light text-muted border` pill. Bootstrap keeps `text-bg-light` near-white in dark mode, while `text-muted` turns the dash pale, so every such row gets a bright pill with a barely visible dash. Same kind of bug as S6. You looked at it and said fix it (12/11/2026). Claude's call: a plain muted "—" without the pill, as Transactions' Rec. column already does; it reads the same in both modes and needs no new CSS. The green **R** pill for reconciled rows stays. A test that the register no longer uses `text-bg-light`. Proposed phase: 1, next to S6. | open (decided 12/11/2026) |
| S39 | 12/11/2026 | `finance/views/transactions.py` (`TRANSACTION_LIST_SORT_FIELDS`), `finance/views/accounts.py` (`ACCOUNT_REGISTER_SORT_FIELDS`), `transaction_list.html`, `account_detail.html` | The Payment and Deposit headers both sort by `amount`, so clicking either does the same thing and both show the arrow. Your call (12/11/2026, yes to Claude's suggestion): each sorts its own side. Payment: the first click puts the biggest payment first, the second the smallest; rows with no payment (deposits) stay at the bottom either way. Deposit works the same way for deposits. Only the clicked header shows the arrow. On both Transactions and the account register. A test for each column and each direction. Proposed phase: 1 (two headers doing the same is a bug). | open (decided 12/11/2026) |

| S40 | 13/11/2026 | `pyproject.toml` (`[tool.ruff.lint.isort]`) and the 14 files whose imports it reorders | You group imports as Python's own, then Django, then the project's (your SpotiCode habit). Stashy already groups and sorts them, through ruff's `I` rule (`ruff check .` fails on a wrong order, `ruff check --fix .` sorts), but Django shares one group with the other outside libraries (dateutil, ofxparse, openpyxl, whitenoise, tzlocal), sorted alphabetically, so `dateutil` comes before `django`. Fix: Django gets its own group, after Python's and before the other libraries (`section-order` plus a `django` section, three lines in `pyproject.toml`), then `ruff check --fix .` rearranges the 14 files that mix Django with another library: 9 app files and 5 test files (a dry run, 13/11/2026; nothing else changes). No group labels like `# Django`: the sorter keeps a comment attached to the import below it, so after sorting the labels land in the wrong place and split the groups (Claude tried it on a copy), and the blank lines and the module names already say which group is which. Your call (13/11/2026): Claude's way. **Also (your habit, added 13/11/2026): two blank lines after the imports in every file.** PEP 8 asks for two before a top-level `def` or `class` and gives no number before anything else. Ruff's sorter decides by what follows: two before a `def` or `class` (36 files today), one before a constant (38 files, e.g. `PAYEE_SORT_KEYS` in `payees.py`). `lines-after-imports = 2` in the same `[tool.ruff.lint.isort]` block makes it two everywhere (Claude tried it on scratch files), and `ruff check --fix` adds the missing line in those 38 files. Proposed phase: 3, as its first step (3.0), so the moves after it are written in the new order and the reshuffle stays out of their diffs. | open (decided 13/11/2026) |
| S41 | 13/11/2026 | `finance/services/importing.py` (`_RECONCILED_FALSE_VALUES`, `_import_rows()`), `finance/tests/test_imports.py` | You'd like an empty Reconciled cell to count as "no". **It already works that way (Claude checked the code):** an empty cell, a cell holding only spaces and a file with no Reconciled column all become `""`, and `""` is in `_RECONCILED_FALSE_VALUES`, so the transaction comes in unreconciled. The same goes for `n`, `no`, `false`, `0`, `u`, `unreconciled` and `uncleared`, and for Excel's FALSE and 0. Anything else counts as reconciled (`R`, `yes`, TRUE, `1`, …). What's missing: no test proves it. `test_an_excel_file_with_real_dates` has a row with an empty Reconciled cell but checks only its payee, and no CSV test has the column at all. Fix: one test with an empty cell, a spaces-only cell and a missing column (CSV and Excel) that all come in unreconciled, next to one marked `R` that comes in reconciled; and the docstring says what a blank means ("blank means not reconciled"), not only that it may be blank. **Claude's find, your call (13/11/2026: "yes, file it"):** `c` and `cleared` count as reconciled today, but in Quicken `c` means the bank has the transaction and you haven't reconciled it against a statement yet; only `R` means reconciled. Imported as reconciled, a cleared transaction never shows up in the next Reconcile checklist and is already counted in the opening balance Reconcile starts from. So `c`, `cleared` and `*` (older Quicken files mark cleared with `*`) join the "no" list and come in unreconciled, while `R`, `X` and `reconciled` still come in reconciled. Transactions already imported stay as they are. Claude's call: the value decides, not the column's heading, so a `Yes` in a column headed "Cleared" still counts as reconciled (rare in bank files). The same test covers the new values. Proposed phase: 1, since it's now a behaviour fix and the test goes with it. | open (decided 13/11/2026) |
| S42 | 13/11/2026 | `finance/views/*.py`: the 26 `render()` calls that build their dictionary inside the call, the 2 that pass `_reconcile_review_context(...)`, and `CLAUDE.md` | Every function view builds `context = {...}` first and ends with `return render(request, "<template>", context)`, the way you write views elsewhere. Today Stashy has no rule: 26 calls build the dictionary inside `render()`, and only the three biggest views (dashboard, Reports, the transaction list) use a `context` variable. Why (agreed 13/11/2026): the `return` line looks the same everywhere and stays short, the context reads as its own block, a key added only sometimes is a plain `if`, and it's what Django's tutorial teaches. Per file: accounts 4 (+2 helper calls), auth 5, categories 1, imports 1, payees 2, reminders 3, settings 4, transactions 2, welcome 4. **Your find:** `import_transactions`'s `render()` line is longer than PEP 8's "Maximum Line Length" (79). Claude checked them all: 7 `render()` lines are over 79 only because the dictionary sits on the same line, and this change brings each to 75 or less: `accounts.py` (`account_transfer`), `auth.py` (`stash_picker`), `imports.py` (`import_transactions`), `payees.py` (`payee_detail`), `reminders.py` (`reminder_edit`'s scope page), `settings.py` (`recovery_code_view`, `backup_restore`). Claude's calls: (1) a value that is a calculation is worked out in a named variable above the context, not inside it, so no context line runs long either: `lockout_minutes` in `forgot_pin`, `recent_backups` in `settings_view`, the payee list's `categories`, and the two `cleaned_data` values reconcile passes to `_reconcile_review_context()`; (2) `account_reconcile` builds the same start-page context twice, so one `_reconcile_start_context(account, form)` builds it; (3) `context = _reconcile_review_context(...)` also counts as "context first". Six `render()` lines stay over 79 afterwards, because a long template name sits deep inside an `if` (both reconcile steps, both recovery-code pages, the transaction form): fine under Stashy's 119 limit, the only limit ruff checks. Nothing changes on screen, and no test changes. CLAUDE.md gets the rule. Proposed phase: 3, item 3.6. | open (decided 13/11/2026) |
| S43 | 13/11/2026 | `pyproject.toml` (`line-length`), then every file `ruff format` rewraps; plan 02's decision C1 | Lines may be 99 characters, not 119. PEP 8's "Maximum Line Length" is 79, and it lets a team agree on up to 99, no more. Plan 02's C1 (25/09/2026) chose 119 and gave as its reason that PEP 8 allows "up to 99/119", which misreads PEP 8. You asked for PEP 8 then, and again on 13/11/2026. Measured on a scratch copy (13/11/2026): with `line-length = 99`, `ruff format` rewraps 59 files by itself, and 63 lines are left for hand fixing (long strings and comments ruff can't split). The other 30 files don't change. **Comments and docstrings (your call, 13/11/2026):** PEP 8's 99 comes with a second condition, that comments and docstrings stay within 72. That's PEP 8's number for comments at any code width; you said 79, and 72 is what PEP 8 means. 1,162 comment and docstring lines are longer than 72 today, and 1,043 are longer than 79, so the work is about the same either way. Claude first suggested skipping it as too much hand work. You pointed out that S21's pass reads every comment and docstring anyway, which makes the extra work small, so they're rewrapped to 72 in that pass (Phase 2, item 2.5). At the end of it, ruff's `W505` is switched on with `max-doc-length = 72`, so it stays true. (Pick 79 instead if you'd rather: same work, one number in `pyproject.toml`.) When built, plan 02's C1 gets a revision note pointing here. Proposed phase: 3, item 3.0, with S40: both are tool settings that reshuffle many files, so they go first, each as its own commit, and the structural moves after them start from the new layout. | open (decided 13/11/2026) |
| S44 | 13/11/2026 | `finance/views/payees.py` (`payee_list`), and the rule for the whole code | You find `payees.py` heavy with comments. You want docstrings and comments, but only ones that explain something a reader wouldn't see from the code, not ones that retell common logic. Your rule: trim, don't strip. Claude checked each of the four comments: all four explain a real "why", so none goes, but two are wordier than they need to be. (1) The 5-line comment above the three queries: it says the page asks the database once per kind of data rather than once per payee, because the list can hold hundreds of payees. That's worth keeping, in 2 lines; the part that lists what each query fetches repeats the code below it. (2) The 3-line comment on `.order_by()`: Transaction's default ordering by date would slip into the `DISTINCT` and repeat each category once per date. A real Django trap; keep it, in 2 lines. (3) "sort key only - the template shows one per row": keep. (4) The currency-blind `total_sort_value`: keep, in 1 line, since adding up different currencies is deliberate and only good for ordering. `payee_list`, the file's longest function, has no docstring; it gets a one-line one. Note: the whole-code review of 13/11/2026 (not filed yet) suggests moving this maths out of the view into a helper; if that happens, the comments go with the code, and comment (1) becomes the helper's docstring. The same rule applies to every file in S21's "trim the rest" pass (Phase 2). *Later on 13/11/2026:* the comment in `welcome.py`'s `_step_watched_expenses` ("The model's "Bills & Utilities" default…") read to you like a category that doesn't exist. It's right and it stays: it means `Profile.watched_expenses_title`'s default (`default="Bills & Utilities"` in `models.py`), and it explains why the first visit blanks the title box. It gets reworded to name the field, e.g. "Don't pre-fill the title's model default ("Bills & Utilities") on a first visit; coming Back keeps what was saved." *(Superseded the same day by S46: the default becomes "Watched expenses", and that line and its comment go.)* Proposed phase: 2, with S21. | open (decided 13/11/2026) |
| S45 | 13/11/2026 | 19 files of app code (tests not counted); the worst listed below | You see places where a lot of code sits in one block, with no blank line between its steps. **Not overstated (Claude measured, 13/11/2026):** 47 stretches of 15 or more lines holding 8 or more statements with no blank line, in 19 files. PEP 8: "Use blank lines in functions, sparingly, to indicate logical sections." Rule (Claude's call): one blank line between a function's steps (read the request, query, build, respond), none inside one step. So an `if`/`elif` chain that makes one decision stays together: `resolve_range()`, `period_bucket()` and `_zone_table()` look dense but are lookup tables, and blank lines would only break them up. The ones that need it: `transaction_form_view` (about 90 lines with one blank line; it needs more than blank lines: the initial values for a reminder's Enter and for a new transaction each become a small helper, by your rule of one job per function), `account_reconcile` (40 lines; S42's start-page helper shortens it too), `reminder_list` (30 lines, its comments also trimmed by S44's rule), `audit/signals.py`'s `log_save` and `log_m2m`, `Reminder.regenerate_occurrences()`, `Category.clean()` (to check: it may be one chain of rules), `_report_filters`, `_import_ofx_rows`, the welcome steps, `stash_create`, `forgot_pin`, `category_usage`, `transaction_quick_edit`, `fetch_latest_rates`, and the rest of the 47 by the same rule. `reporting.py`'s (`category_vs_average_report_rows` and seven more) and `reports_view` wait for S14, which rewrites them in Phase 4, so they're touched once. Nothing changes on screen. Proposed phase: 3, item 3.7, in the same pass as S42, which already opens every function view. | open (decided 13/11/2026) |
| S46 | 13/11/2026 | `finance/models.py` (`Profile.watched_expenses_title`), `finance/views/welcome.py` (`_step_watched_expenses`), a migration, `test_reports.py`, `test_welcome.py` | The Watched expenses title's default becomes **"Watched expenses"** instead of "Bills & Utilities" (your call, 13/11/2026). Why: the old default dates from Phase 13 (September 2026), when Watched expenses replaced a fixed "Bills & Utilities" report and the matching title meant "nothing looks different on day one" for the stashes that existed then. That reason has passed. Today a new stash that skips the welcome step gets a "Bills & Utilities" title on Settings and on Reports' card that nobody chose. Existing stashes keep the title they have saved (no data change): one showing "Bills & Utilities" may have kept it on purpose, and it can be renamed in Settings. **Claude's call:** the welcome step stops emptying the Title box on a first visit. It did that only to hide the old default; a neutral "Watched expenses" is a sensible starting title, so the box shows it and you can keep it or type over it. That line and its comment go, so S44's note about rewording that comment falls away. Tests: `test_default_title_is_bills_and_utilities` becomes the new default, and `test_title_starts_empty_…` becomes "starts with Watched expenses". A small migration records the new default; nothing in the data changes, but it is a migration, so the real stash is backed up first (`Stashy.bat` does that before it migrates). Proposed phase: 1. | open (decided 13/11/2026) |
| S47 | 13/11/2026 | `finance/content.py` → `finance/tips.py`; the six views that import it; `test_pages.py`, `CLAUDE.md`, `README.md` | You'd like the module's name to say what's in it. `content.py` holds only the six `*_TIPS` lists (the "Stashy tips" beside the forms), 42 lines, nothing else, so `tips.py` (Claude's call: plain and short; `from ..tips import ACCOUNT_TIPS` reads well). The six imports in `views/` (accounts, categories, imports, payees, reminders, transactions), `TipsMovedOutOfTheViewsTests`' docstring in `test_pages.py`, CLAUDE.md's architecture map and README's folder tree follow. Nothing changes on screen. The tips' wording is yours to review and maybe change, another day; that's separate from the rename. Proposed phase: 3, item 3.8 (with 3.2's other moves and renames). | open (decided 13/11/2026) |
| S48 | 13/11/2026 | `finance/forms.py` (542 lines) and `finance/auth_forms.py` (116) → a `finance/forms/` package; the 21 import lines that name them (views, admin, tests) | One `forms.py` per app is Django's default and normal, but in Stashy it's the odd one out: views are split by area (`views/`), tests are one module per area, and templates get folders per area (S28). Your call (13/11/2026): split forms the same way. `forms/_base.py`: `DateInput`, `BootstrapFormMixin` (shared by all). `forms/accounts.py`: `AccountForm`, `AccountCreateForm`, `TransferForm`, `ReconcileStartForm`, `ReconcileFinishForm`. `forms/categories.py`: `CategoryForm`, `CategoryParentSelect`. `forms/payees.py`, `forms/transactions.py`, `forms/reminders.py`, `forms/imports.py`: one form each. `forms/settings.py`: `ProfileForm`, `CheckRatesForm`, `WatchedExpensesForm`. `forms/auth.py`: everything in today's `auth_forms.py` (`PinField`, `PinConfirmMixin` and the PIN forms), so its odd name goes too. Claude's call: `DateRangeFilterForm` goes to `forms/filters.py`, since both Transactions and Reports use it, matching S16's `finance/filters.py`, which turns its answers into dates. As with `views/`, `forms/__init__.py` re-exports nothing, so every import names the real module (`from ..forms.accounts import AccountForm`). The tests stay as they are: each form is already tested in its area's module, so only their imports change. Nothing changes on screen. CLAUDE.md's architecture map follows. Proposed phase: 3, item 3.9, after 3.2 (S16 moves the two non-form helpers out of `forms.py` first). | open (decided 13/11/2026) |
| S49 | 13/11/2026 | `finance/models.py` (1,000 lines) | You don't like one file for every model. Claude advised against splitting it: one `models.py` is Django's default, and the models refer to each other a lot (a transfer category belongs to an account, a reminder fills in a transaction), so a `models/` package would invite import loops for little gain. It would also be the one package that must re-export everything (Django finds models through `models`). Your call (13/11/2026): one file, with visual sections. A section banner before each group, in the style `views/exports.py`, `views/_helpers.py` and `services/importing.py` already use (a `# ----` line, the name, a `# ----` line). The file's order already matches, so nothing moves: Names (`name_key()` and its helper), Profile, Accounts (`AccountType`, the groups, `AccountQuerySet`, `Account`, `grouped_accounts()`), Categories (`CategoryQuerySet`, `Category`, `categories_alphabetical()`, `grouped_categories()`), Payees, Transactions, Imports (`ImportBatch`), Reminders, Exchange rates, Reconciliations. The same rule for `reporting.py`, the other long module, when S14 rewrites it (Phase 4). Nothing changes on screen. Proposed phase: 3, item 3.14 (numbered 3.10 when filed; moved to the end on 13/11/2026 when the whole-code review's items joined the phase), last, after any item that moves code in or out of `models.py`. | open (decided 13/11/2026) |

Also from that review, 12/11/2026: you settled the date rule (complete dates always go through `sdate`/`regional`; short decorative dates such as the dashboard's "Thursday, 12 November" and Reports' "28 Sep" may keep a fixed style), now written in CLAUDE.md, so nothing to fix there. The last two items from the review are S38 and S39 above.

### G. Whole-code review (13/11/2026)

At your request (12/11/2026), a `code-reviewer` agent read all the code
except the templates, which had their own review: the Python, the
JavaScript and CSS, `Stashy.bat`, `pyproject.toml`, and the tests, standing
in for your own read of them. ruff, `manage.py check` and the 723 tests
were all clean. Claude checked the two worst findings (S50, S51) in the
code itself. You went through the findings on 13/11/2026, agreed with all
of them, and left it to Claude which plan each goes into: all of them go
here, by phase. Findings that extend an older item are notes at the end.

**Phase 1 (bugs):**
- **S50 — Reports add refunds to spending.** `category_report_rows()`
  makes each transaction positive before adding them up, for the
  Spending, Income and Watched expenses tables, their exports and the
  stacked charts. So Groceries with −100.00 and a +30.00 refund shows
  130.00 in the table and 70.00 in the chart. `income_and_spending()`
  takes `abs()` of the total, so a period with only a +50 refund counts
  as "spent 50" on the dashboard's Spending tile, the net and the savings
  rate.
  - **Fix:** add the signed amounts, roll subcategories up to their
    parent, then flip the sign once per row.
  - **Your call (13/11/2026):** a category whose refunds beat its
    spending shows negative, the dashboard's rule since 27/09/2026 (plan
    02, item 1.9). **Stays:** a normal refund shrinks its category (spent
    100, refunded 30 → 70, in the table and the chart). **Changes:** that
    table's 130 becomes 70; spent 40, refunded 100 shows −60 (140 today);
    a refund-only period's spending is −50, not 50.
  - **Tests:** a refund in `CategoryReportRowsTests`, a category that
    nets to a refund, and a refund-only period in `income_and_spending()`.
- **S51 — An unpaid reminder period can vanish.** Delete the transaction
  that paid an occurrence of a recurring reminder, and the occurrence
  correctly turns overdue. But the next Enter, Skip or full save of the
  series deletes it: `Reminder.regenerate_occurrences()` removes every
  occurrence that is neither paid nor skipped and isn't a wanted future
  date. **Fix:** only previews dated after the series' `next_due_date`
  are removed. A test.
- **S52 — Entering a recurring reminder names the new date twice.**
  `transaction_form_view` formats `{reminder}` after `mark_entered()` has
  moved it on: "Entered 01/11/2026 | Landlord | -500.00 and moved it to
  01/11/2026." `reminder_skip` already takes the label first. **Fix:**
  the same. A test. (Plan 04's R20 assumes the old date, which this
  restores.)
- **S53 — System payees are matched by exact spelling.** The payee
  autocomplete list (`views/api.py`) and `export_payees` check
  `name in Payee.SYSTEM_NAMES`. `Payee.system()` reuses a payee typed in
  another case ("opening balance"), which then shows in the autocomplete
  and exports as not system-managed. **Fix:** one `Payee.is_system`
  through `name_key`, and a `name_key__in` exclude. Tests.
- **S54 — The time-zone setting accepts any text.** `ProfileForm`'s
  `time_zone` is a `CharField` with a `Select` widget, so a hand-made
  POST saves anything, and `regional` quietly falls back to the default
  zone. **Fix:** a `ChoiceField` over the same list. A test.
- **S55 — Two Edit-mode slips on Transactions**
  (`transaction-quick-edit.js`, `saveRow()`). Each change posts the whole
  row with no queue. Waitress runs 8 threads, so two quick changes to one
  row can finish out of order, and the older one overwrites the newer: a
  lost edit. And with both amount boxes empty it sends `0`, so the
  transaction is saved as 0.00 without a word. **Fix:** one queue per row
  (each save waits for the previous one), and an empty amount is sent as
  empty, so the form answers "This field is required". A test for the
  empty amount; the queue is JavaScript only, so it's checked by hand.

**Phase 2 (tests and leftovers):**
- **S56 — Tests that build what the rules forbid.** `CategoryDeleteTests`
  (`test_categories.py`) and `SpendingTreeTests` (`test_dashboard.py`)
  build a three-level category tree with `objects.create()`, which skips
  `clean()`, so they pin behaviour the one-level rule makes impossible.
  **Fix:** a valid tree.
- **S57 — Tests that can't fail, or check something other than their
  name.**
  - `test_imports.py`: looks for "auto", which every page has
    (`ms-auto`).
  - `test_reconciliation.py`: "-40.00" is also in a hidden field.
  - `test_settings.py`: named "no Generate button reappears", but checks
    that a Generate warning does.
  - `test_transactions.py`: the "total in a flex row" test only finds a
    CSS class somewhere, and "search finds payees and memos" checks only
    payees.
  - `test_reminders.py`: `initial or ONE_TIME` passes with no default at
    all, plus two weak string checks.

  **Fix:** each one checks the real thing.
- **S58 — Near-duplicate tests.**
  - `test_reminders.py`: two identical bodies, and two classes that both
    check "a fresh visit defaults to this month".
  - `test_payees.py`: `CaseInsensitivePayeeTests` repeats
    `PayeeResolutionViewTests`.
  - `test_imports.py`: the category guess, twice.
  - `test_auth.py`: the PIN rule, twice.
  - `test_pages.py`: three tests that newer ones cover.
  - One pair each in `test_backup.py` and `test_middleware.py`, and one
    across `audit/tests.py` and `test_imports.py`.

  **Fix:** keep the clearer one of each. Then measure `test_reminders.py`
  (1,194 lines today) again: if it's still that big, split the reminder
  rules from the page, the way `test_reporting.py` and `test_reports.py`
  are split.
- **S59 — Test names and docstrings that mislead.**
  `StashPathHelperTests` tests PINs; `LockoutTests`' docstring mentions
  the `default` database, which tests can't use; `OfflineAndThemeTests`
  also holds a tips test; `test_admin.py`'s module docstring misses a
  class. (`OneTimeIntervalTests` is already in S11.)
- **S60 — Test rule slips.** A test in `test_transactions.py` logs in
  inside the test (the rule: in `setUp`). `name__iexact` in
  `test_categories.py`, `test_imports.py` and `test_payees.py` (the rule:
  `name_key` or `.named()`). One class in `test_reports.py` never touches
  the database, so it should be a `SimpleTestCase`.
- **S61 — Copied test set-up and small leftovers.**
  - Copied set-up: the starter-spreadsheet temp file is written three
    times, CSV replies are read two different ways, the upload helper has
    five shapes, `queries_with` is in four modules, the leaked-syntax list
    is in five, and admin tests log in by hand. These become shared
    helpers in `base.py`, including a `LoggedInAsSuperuserTestCase`.
  - Leftovers: a blank line right after `def` in about nine places,
    `__import__("datetime")` where `timedelta` is already imported, an
    unused `INCOME` alias, and `set("01OI-") - {"-"}`.
- **S62 — Exchange-rate fetching has no test.** Not tested at all:
  - `currency.fetch_latest_rates()`: reading the reply, BGN, what it
    stores, the unavailable list;
  - `RateTable`'s rule that a typed-in rate beats a fetched one on the
    same date.

  **Fix:** tests in `test_currency.py`, with the network call mocked.

**Phase 3 (structure):**
- **S63 — `bootstrap.py`'s imports inside functions.** Three have a real
  reason (they run before Django is installed) but no
  `# Imported here: <why>` comment. The guard test in `test_project.py`
  checks only `finance/` and `audit/`, and only imports directly in a
  function's body. **Fix:** the comments, and the test also checks
  `scripts/` and imports inside `if`/`try`/`with`.
- **S64 — Small slips.**
  - The four regional settings are copied into a Profile in two places
    (`registry.py`, `models.py`): one helper.
  - `savings_rate()` rounds the Python way (half to even), while money
    rounds half up: the same as money.
  - A variable named `messages` in `transaction_quick_edit` hides
    Django's `messages`.
  - `Reminder.apply_to_series()` saves the series head outside
    `system_action()` and its occurrences inside, so the audit log labels
    them differently.
  - `Stashy.bat` tries `py -3.14` before 3.12, the version Stashy
    targets. Check whether that's deliberate (plan 01's upgrade) before
    changing it.
- **S65 — Work done again and again.**
  - The import guesses a category for every row by loading the payee's
    whole history (`category_history()`) each time: it remembers it per
    payee for one import.
  - The dashboard and Reminders load every paid or skipped occurrence
    there has ever been and filter them in Python: they filter in the
    database.
- **S66 — `payee_list`'s maths into a helper.** About 95 lines of
  grouping and totals sit in the view (the rule: thin views), and it
  rebuilds "Parent > Child" by hand instead of using
  `Category.full_name`. **Fix:** one `payee_summaries()` helper next to
  the model, whose docstring takes over S44's first comment. With S16.

**Phase 5 (JavaScript and CSS):**
- **S67 — JavaScript and CSS tidy-ups.**
  - `payee-autofill.js`: neither `fetch` handles a failed or non-JSON
    reply, and an older answer can arrive after a newer one; stale
    answers get ignored.
  - `charts.js`: its "Read once" comment is wrong, and it swaps the
    no-break thousands space for a normal one, against `regional`'s "an
    amount never wraps". Keep the swap with a reason, or drop it.
  - `dashboard.js`: the Uncategorized link branch can never run.
  - `stashy.css`: `.diff-good`/`.diff-bad` use raw colours instead of
    tokens, and the sidebar has hard-coded whites.
  - `reports.js`: a hex alpha that works only while the token is a
    six-digit hex colour.

**Notes on older items:**
- **S2:** also `transaction_id=abc` on category usage (`category_usage`)
  gives a 500; keep only digit ids.
- **S4:** `Category.Meta.ordering = ["parent__name", "name"]` adds a join
  to every Category query and sorts upper and lower case apart.
- **S10:** the last-stash cookie isn't signed, and any folder holding a
  `db.sqlite3` is accepted, absolute paths included. Accept only names
  from `registry.list_stashes()`, with a test in `test_middleware.py`.
- **S11:** seven more history-only checks: three in `test_reports.py`,
  two in `test_reminders.py`, the old admin label in `test_pages.py`, and
  "same as before the merge" in `test_exports.py`.
- **S13:** comments that are wrong today:
  - `settings/base.py`: "plan 05" for the .exe (it's 06), and
    `finance.stashes.ensure_registered()` (it's in `registry`).
  - `forms.py`: "same granularities as Interval", and "base.html's
    flatpickr" (it's `stashyDatepicker`).
  - `services/importing.py`: "D9" (D9 is transaction numbers).
  - `formats/en/formats.py`: "settings.py" (it's `settings/base.py`).
  - Two bare codes in tests: "Folds in R2" and "(R1)".
- **S15:** `TransferForm(from_account=None)` uses it straight away;
  make it required.
- **S24 (item 1.10):** `reset_pin_directly()` changes the audited Profile
  with `.update()`, so no audit row is written. Use `save()` inside
  `use_stash()` and `system_action()`.
- **S34:** `date.today()` in about 40 lines of eight more test modules
  (not `test_backup.py`, which is right to use the computer's clock).
- **S35:** `.usage-empty` colours its "0" with the border token, about
  1.3:1 contrast on white.
- **Phase 6.1:** `test_backup.py` migrates to `finance` 0016/0017, which
  aren't in git, so a fresh copy from GitHub fails that test.

<!-- Next number: S68 -->

---

## Phase 1 — Bugs and quick security fixes  (S1–S8, S10, S24 with plan 04's R9, S31–S33, S38, S39, S41, S46, S50–S55)

- [ ] **1.1** S1: `min_value=CENT` on the transfer amount; test with 0.01.
- [ ] **1.2** S2: both bad URLs answer 404 or show a message; a test each.
  Also category usage's `transaction_id=abc` (G's note).
- [ ] **1.3** S3: the two summary rows built once in `reporting.py`, used by
  the page and the export; a test that the export has them.
- [ ] **1.4** S4: dropdowns and the Payee/Category columns sort by
  `name_key`; a test with Cyrillic names in mixed case. Also
  `Category.Meta.ordering` (G's note).
- [ ] **1.5** S5: the unused Number help text (with its "D9") out of the
  form.
- [ ] **1.6** S6: the saved flash uses `--stashy-positive-soft`.
- [ ] **1.7** S8: empty `SECRET_KEY` in `.env.example`; `bootstrap.py`
  creates `.env` from it.
- [ ] **1.8** S10: silence the four HTTPS checks by name; `httponly` on the
  last-stash cookie, which also accepts only a real stash name (G's
  note).
- [ ] **1.9** S7 (Q1, yes): current PIN for a new recovery code; wrong
  current-PINs on both pages count toward the lockout; a lockout logs out.
- [ ] **1.10** S24 and plan 04's R9, together (your call, 11/11/2026), both in
  `manage.py reset_pin`: a `*` for each typed digit, and the owner name
  works in any upper/lower case. A test each. Also `reset_pin_directly()`
  saves the Profile through `save()`, so the audit log sees it (G's note).
- [ ] **1.11** S31: the dashboard warns about accounts left out of net
  worth, from one include shared with the Accounts page.
- [ ] **1.12** S32: Settings' rate date through `sdate`, and Source shows
  its label.
- [ ] **1.13** S33: the savings rate in the stash's number format.
- [ ] **1.14** S38: the register's "—" without the pill.
- [ ] **1.15** S39: Payment and Deposit sort separately, each biggest
  first.
- [ ] **1.16** S41: blank Reconciled cells and `c`/`cleared`/`*` import as
  not reconciled; a test.
- [ ] **1.17** S46: "Watched expenses" as the default title. A migration:
  the real stash is backed up first.
- [ ] **1.18** S50: refunds net against their own category in Reports and
  on the dashboard; a category that nets to a refund shows negative.
- [ ] **1.19** S51: an unpaid reminder period survives regeneration.
- [ ] **1.20** S52: the reminder message shows the date it was due.
- [ ] **1.21** S53: system payees recognised through `name_key`.
- [ ] **1.22** S54: the time zone is a `ChoiceField`.
- [ ] **1.23** S55: Edit mode saves a row's changes in order, and an empty
  amount is refused.

**Done when:** each fix has a test that fails on today's code, the suite is
green, and the render check of the changed pages shows no leaked template
syntax.

## Phase 2 — Leftovers and documentation  (S11, S12, S13, S21, S44, S43's comments, S25, S34, S56–S62)

- [ ] **2.1** S11: delete the 21 history-only tests, trim three, rename the
  kept classes; plus the seven more from G's note.
- [ ] **2.2** S12: remove the dead bits.
- [ ] **2.3** S13: history notes out of the 12 files, R-codes in code and
  CLAUDE.md replaced by words, boilerplate docstrings and `.gitignore`
  trimmed; plus the comments that are wrong today (G's note).
- [ ] **2.4** S21: module docstrings, `resolve_range()`, `Profile`, the
  reminder tip.
- [ ] **2.5** S44: `payees.py`'s comments trimmed (none removed) and a
  docstring for `payee_list`; your rule for S21's pass in every file:
  keep a comment that explains something you can't see from the code,
  trim one that retells it, and never strip docstrings. In the same pass,
  S43's comment part: every comment and docstring wrapped at 72, then
  ruff's `W505` (`max-doc-length = 72`) switched on so it stays that way.
- [ ] **2.6** S25: the import's date-detection comment reworded, and a
  test with ambiguous rows before the deciding one.
- [ ] **2.7** S34: the render check covers every page; `date.today()`
  becomes `timezone.localdate()` in every test module that compares with
  the app's dates (G's note).
- [ ] **2.8** S56–S61: the tests' findings: valid category trees, tests
  that check the real thing, duplicates removed, clear names, the test
  rules, shared set-up in `base.py`.
- [ ] **2.9** S62: tests for fetching exchange rates and for typed-in
  rates beating fetched ones.

**Done when:** the suite is green (fewer tests than 723, since history-only
tests and duplicates go, and a few new ones come), and a grep for the
history patterns finds nothing outside `plans/` and `DEVLOG.md`.

## Phase 3 — Python structure  (S40, S43, S15, S16, S17, S18, S20, S42, S45, S47, S48, S63–S66, S49)

- [ ] **3.0** First, the two tool settings, each as its own commit:
  S40, Django gets its own import group and `lines-after-imports = 2` in
  `pyproject.toml`, then `ruff check --fix .` reorders the 14 files and
  adds the second blank line in 38; S43, `line-length = 99`,
  then `ruff format .` rewraps 59 files and the 63 lines left over are
  fixed by hand. Numbered 3.0 so the items below keep their numbers.
- [ ] **3.1** S15: `services/accounts.py`; the forms, the wizard and the
  admin use it. `TransferForm`'s `from_account` becomes required (G's
  note).
- [ ] **3.2** S16: `filters.py`, the writers into `exports.py`, public
  helper names.
- [ ] **3.3** S17: `services/rates.py`; no more "Imported here" in
  `currency.py`.
- [ ] **3.4** S18: the shared helpers (Python parts; the JavaScript waits
  for Phase 5).
- [ ] **3.5** S20: the small habits and rule slips.
- [ ] **3.6** S42: every function view builds `context` first and ends
  with `return render(request, "<template>", context)`; calculations go
  in named variables above it; one helper for reconcile's start page.
  Last in the phase, so it touches the views in their final places.
- [ ] **3.7** S45, in the same pass: a blank line between a function's
  steps in the dense stretches (not inside one `if`/`elif` decision);
  `transaction_form_view`'s two initial-value blocks become helpers.
  `reporting.py` and `reports_view` wait for S14 (Phase 4).
- [ ] **3.8** S47: `content.py` becomes `tips.py` (six imports, a test
  docstring, CLAUDE.md, README). Best done with 3.2's moves.
- [ ] **3.9** S48: `forms.py` and `auth_forms.py` become a `forms/`
  package split by area, like `views/`, with no re-exports; the 21
  import lines follow. After 3.2.
- [ ] **3.10** S63: `bootstrap.py`'s in-function imports get their
  comment, and the guard test checks `scripts/` and nested imports.
- [ ] **3.11** S64: the small slips (one regional-settings helper, the
  savings rate's rounding, `messages` shadowed, `apply_to_series()`
  inside `system_action()`, `Stashy.bat`'s Python version checked).
- [ ] **3.12** S65: the import remembers each payee's history; the
  dashboard and Reminders filter resolved occurrences in the database.
- [ ] **3.13** S66: `payee_list`'s maths into `payee_summaries()`. With
  3.2.
- [ ] **3.14** S49: section banners in `models.py` (Names, Profile,
  Accounts, Categories, Payees, Transactions, Imports, Reminders, Exchange
  rates, Reconciliations). Last, once nothing else moves code in or out.

**Done when:** the suite is green with no test edited except for moved
imports, and every page renders the same as before.

## Phase 4 — One builder per report  (S14)

- [ ] **4.1** One builder per report, shared by the page and the export
  registry.
- [ ] **4.2** `reports_view` becomes a short loop.

**Done when:** a test renders Reports and every export on a varied data
set, before and after, and compares them; the only difference is S3's
rows, which Phase 1 already added.

## Phase 5 — Templates and JavaScript  (S28, S19, S18's JavaScript part, S26, S27, S29, S30, S35–S37, S67; S9 last, if Q2 is yes)

- [ ] **5.0** S28 first: the templates move into folders per area, so
  S19's new files land straight in their folders.
- [ ] **5.1** One shared frame, `mascot_base.html`, for the seven picker,
  PIN, welcome and error pages, with the light/dark switch on all seven.
- [ ] **5.2** `forms.js` and `data-` attributes instead of the scripts in
  `generic_form.html`; a real Cancel link.
- [ ] **5.3** `data-auto-submit` instead of `onchange=`; the remaining
  inline scripts into static files.
- [ ] **5.4** One shared money formatter in a small `format.js`; Chart.js
  loads only on pages with charts.
- [ ] **5.5** S26 and S27: the two reconcile steps' layout (Mark all on
  the left, the buttons on the right; labels on the left, values lined up
  on the right).
- [ ] **5.6** S29 and S35: labels linked to their boxes, names for the
  controls a screen reader can't name, page headings, charts described,
  and `.usage-empty` readable (G's note).
- [ ] **5.7** S30: the error pages' tag (gold 404, red 500).
- [ ] **5.8** S36 and S37: the small on-screen slips and the template
  leftovers.
- [ ] **5.9** S67: the JavaScript and CSS tidy-ups.
- [ ] **5.10** S9 (only if Q2 is yes), last: the Content-Security-Policy
  header, then you check each page once with the console open. (It was
  5.5 until 13/11/2026; it stays last because it needs every inline script
  gone first.)

**Done when:** the suite is green, `collectstatic` runs clean into a
scratch folder, the rendered HTML of every changed page is read, and you've
clicked through the changed pages. This phase can't be checked visually
here.

## Phase 6 — Last: migrations in git  (moved from plan 02, item 3.6)

**This phase always stays the last one in this plan.** Any phase added
later goes before it. Moved here on 10/10/2026 (your call), when plan 02's
item 3.6 was closed as saved for later.

- [ ] **6.1** Decide with you whether migrations go into git. Today they're
  kept out (`**/migrations/*.py` in `.gitignore`, your choice on
  01/10/2026), so git holds only `finance/migrations/0001_initial.py`. A
  fresh copy from GitHub therefore gets models that no committed migration
  builds. The options:
  - **(a) Commit them all, as they are.**
  - **(b) Keep them local,** and accept that the repository alone can't
    set up a new copy.

  Either way, `test_backup.py` migrates to `finance` 0016/0017, so a
  fresh copy without those migrations fails that test (G's note).
  - **(c) Start fresh with one new `0001_initial` per app.** Then every
    older backup loses its automatic upgrade path (plan 06's Q6).

  Plan 06 (the .exe) needs the program to carry exactly the migrations
  each stash has already run, so the answer matters there too.
- [ ] **6.2** Your own step from the same item: `git rm -r --cached
  staticfiles .idea`. Both folders are ignored, but some of their files
  are still tracked. Claude doesn't run git; you do.

**Done when:** 6.1 is decided and carried out, and 6.2 is done.

---

## Noticed, not planned

- Reports computes all eight reports on every visit. That's fine at
  today's size, and S14 makes "only what's on screen" easy later.
- When an import creates a category the file names, it guesses income or
  expense from the amount's sign. That's a reasonable first guess for a
  category Stashy didn't know. The rule that the type decides is about
  reports, not about this.
- Owner names are compared with `lower()`, not `casefold()`. Python's
  `lower()` already handles Cyrillic; they differ only for letters like
  "ß".
