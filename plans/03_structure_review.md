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

**Baseline (10/10/2026):** 723 tests green. `ruff check .` and
`ruff format --check .` are clean, `manage.py check` finds nothing, and
`makemigrations --check` finds no missing migration.

**Guiding idea:** the same as plan 02's C3. No new features, and nothing on
screen changes unless the finding is itself a bug. Every bug fix gets a test
that fails on today's code. No phase changes the database schema, so none
needs a migration or a backup of the real stash. If that changes while
working, the backup comes first, as usual.

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
    hard-codes "7 days" instead of reading `Reminder.DUE_SOON_DAYS`.
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
| S28 | 12/11/2026 | `finance/templates/finance/` (34 pages and an `includes/` folder, all side by side) | You're uneasy that every template sits in one folder; you'd usually split them by area (transactions, reminders, …). **Claude:** both are normal in Django. One flat folder named `<thing>_<action>.html` is Django's own default (its generic views look for `finance/transaction_list.html`), and it's fine for small apps. At Stashy's size, folders per area are clearer, and the code is already split that way: `views/` has one module per area and the tests one file per area. So the templates follow the same map: `accounts/` (list, detail, delete, transfer, both reconcile steps), `categories/`, `payees/`, `transactions/`, `reminders/`, `imports/`, `reports/` (with its three report-only pieces from `includes/`), `settings/`, `dashboard/`, `welcome/`. Shared pages stay at the top of `finance/`: `base.html`, `error_page.html`, and S19's `form_page.html` and `delete_page.html`; `includes/` keeps only pieces several areas use. Two folders stay where they are, because Django looks for them by name: `registration/` (the picker and PIN pages; Django's login view looks there) and `404.html`/`500.html` at the top. Claude's call: files keep their full names (`transactions/transaction_list.html`, not `transactions/list.html`), so PyCharm's tabs and search results never show five files all called `list.html`. The work is mechanical: move the files, update each view's `template_name`, the `{% extends %}`/`{% include %}` paths and the tests that name a template. Proposed phase: 5, as its first step, so S19's new files land straight in their folders. *12/11/2026 (you): "folders it is", and you accept Claude's other suggestions here and in S19, S25 and S27.* | open (decided 12/11/2026) |

<!-- Next number: S29 -->

---

## Phase 1 — Bugs and quick security fixes  (S1–S8, S10, S24 with plan 04's R9)

- [ ] **1.1** S1: `min_value=CENT` on the transfer amount; test with 0.01.
- [ ] **1.2** S2: both bad URLs answer 404 or show a message; a test each.
- [ ] **1.3** S3: the two summary rows built once in `reporting.py`, used by
  the page and the export; a test that the export has them.
- [ ] **1.4** S4: dropdowns and the Payee/Category columns sort by
  `name_key`; a test with Cyrillic names in mixed case.
- [ ] **1.5** S5: "D9" out of the help text.
- [ ] **1.6** S6: the saved flash uses `--stashy-positive-soft`.
- [ ] **1.7** S8: empty `SECRET_KEY` in `.env.example`; `bootstrap.py`
  creates `.env` from it.
- [ ] **1.8** S10: silence the four HTTPS checks by name; `httponly` on the
  last-stash cookie.
- [ ] **1.9** S7 (Q1, yes): current PIN for a new recovery code; wrong
  current-PINs on both pages count toward the lockout; a lockout logs out.
- [ ] **1.10** S24 and plan 04's R9, together (your call, 11/11/2026), both in
  `manage.py reset_pin`: a `*` for each typed digit, and the owner name
  works in any upper/lower case. A test each.

**Done when:** each fix has a test that fails on today's code, the suite is
green, and the render check of the changed pages shows no leaked template
syntax.

## Phase 2 — Leftovers and documentation  (S11, S12, S13, S21)

- [ ] **2.1** S11: delete the 21 history-only tests, trim three, rename the
  kept classes.
- [ ] **2.2** S12: remove the dead bits.
- [ ] **2.3** S13: history notes out of the 12 files, R-codes in code and
  CLAUDE.md replaced by words, boilerplate docstrings and `.gitignore`
  trimmed.
- [ ] **2.4** S21: module docstrings, `resolve_range()`, `Profile`, the
  reminder tip.

**Done when:** the suite is green (723 − 21 tests), and a grep for the
history patterns finds nothing outside `plans/` and `DEVLOG.md`.

## Phase 3 — Python structure  (S15, S16, S17, S18, S20)

- [ ] **3.1** S15: `services/accounts.py`; the forms, the wizard and the
  admin use it.
- [ ] **3.2** S16: `filters.py`, the writers into `exports.py`, public
  helper names.
- [ ] **3.3** S17: `services/rates.py`; no more "Imported here" in
  `currency.py`.
- [ ] **3.4** S18: the shared helpers (Python parts; the JavaScript waits
  for Phase 5).
- [ ] **3.5** S20: the small habits and rule slips.

**Done when:** the suite is green with no test edited except for moved
imports, and every page renders the same as before.

## Phase 4 — One builder per report  (S14)

- [ ] **4.1** One builder per report, shared by the page and the export
  registry.
- [ ] **4.2** `reports_view` becomes a short loop.

**Done when:** a test renders Reports and every export on a varied data
set, before and after, and compares them; the only difference is S3's
rows, which Phase 1 already added.

## Phase 5 — Templates and JavaScript  (S19, S18's JavaScript part; S9 if Q2 is yes)

- [ ] **5.1** One shared frame for the seven picker, PIN, welcome and error
  pages.
- [ ] **5.2** `forms.js` and `data-` attributes instead of the scripts in
  `generic_form.html`; a real Cancel link.
- [ ] **5.3** `data-auto-submit` instead of `onchange=`; the remaining
  inline scripts into static files.
- [ ] **5.4** One shared money formatter in a small `format.js`; Chart.js
  loads only on pages with charts.
- [ ] **5.5** S9 (only if Q2 is yes): the Content-Security-Policy header,
  then you check each page once with the console open.

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
