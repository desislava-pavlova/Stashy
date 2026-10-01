# Stashy Upgrade Plan

Source: user's full requirements dump on 2026-09-12, clarified 2026-09-12.
Progress is tracked with the checkboxes below (Phases 1, 1B and 2 done
2026-09-12; Phases 3, 4, 5, 6, 6.1, 6.2 and 7 done 2026-09-13; Phases
7.1, 7.2, 8, 9, 10 and 11 done 2026-09-14; Phases 12, 12.1 and 13 done
2026-09-15). **Next:** Phase 14 (admin, tags, audit log), then Phase 15
(login and new stash setup, needs Phase 13 - done) — both planned
15/09/2026, not started.
The database currently holds only test data (confirmed 2026-09-12), so later
migrations may reset or rebuild data instead of carefully converting it.
Work through it phase by phase; each phase is independently shippable so we
can stop/resume anywhere.

**Guiding idea:** Quicken is the reference, but simplified for one person's
actual use, with a UI that feels like *Stashy* (a squirrel that stashes
things) rather than a generic bank-style dashboard, and setup that requires
zero Python/Django/SQL knowledge.

Checkboxes track progress across sessions — tick them off as we land each
piece.

---

## Decisions log (all open questions resolved)

- **D1 — Opening balance is a real transaction, not a stored field.**
  Creating an account no longer sets a `starting_balance` number on the
  Account itself. Instead, account creation asks for an opening-balance
  amount + date, and the system generates one real `Transaction`:
  `date` = the date you enter, `transaction_number` = next in sequence,
  `payee` = "Opening Balance" (shared system payee, auto-get-or-created),
  `category` = that account's own auto-created transfer-type category
  (see D4), `memo` = "Opening Balance". Like every transfer transaction
  (D7 below), this is a **regular transaction in every respect** —
  editable, deletable, shows identically to any other row — the dedicated
  flow just makes it *easier to create correctly*, it doesn't make it
  special or locked afterward. `Account.balance` becomes a pure sum of
  its transactions — no separate starting-balance field to keep in sync.
  **Migration note:** existing accounts' current `starting_balance`
  values need to be converted into one such synthetic transaction each
  during the migration that removes the field, so historical balances
  don't shift.
- **D2 — Currency: real conversion, rates fetched from Frankfurter.**
  Multi-currency is real (net worth converts everything to your main
  currency). Rates come from the free, no-API-key **Frankfurter** API
  (ECB-based), triggered by an explicit "Check rates" button — not
  automatic/background — with a clear on-screen disclaimer that it needs
  an internet connection. Architected as its own small `ExchangeRate`
  model (currency pair + date + rate) that's also manually editable, so
  the app still works fully offline (you just don't get a fresh rate) and
  a fetch failure just leaves the last known rate in place. Each account
  shows its balance converted to the main currency using the latest known
  rate at calculation time (not historical per-transaction rates) for v1 —
  flag if you'd rather have historical accuracy from day one, that's a
  bigger feature.
- **D3 — Transactions show separate Payment / Deposit columns.**
  Confirmed. Underlying `amount` stays signed in the database; this is a
  display-only change.
- **D4 — Top-level categories can still be used directly on transactions.**
  No leaf-only enforcement. People who want a flat, unsplit category list
  can keep using it that way.
- **D5 — Reminders: "end of period" due dates.**
  A reminder can be set to auto-resolve its due date to the *last day* of
  its frequency's period instead of a fixed day: monthly → last calendar
  day of that month (28–31, whichever applies), weekly → last day of that
  week (Sunday, since weeks start Monday per your convention — used
  consistently anywhere else "week" matters, e.g. reports), biweekly →
  last day of that 2-week span, quarterly → last day of the quarter
  (31 Mar/30 Jun/30 Sep/31 Dec), yearly → 31 Dec. This is a
  per-reminder toggle; recalculated fresh each time the reminder advances,
  rather than a fixed date +interval.
- **D6 — Reminders: no `is_active`, add "Skip".**
  Deleting a reminder only removes the reminder — it was never linked to
  the transactions it previously generated (the model doesn't store that
  link today, and won't), so history is untouched. To pause without
  deleting, add a **Skip** action next to Enter: advances `next_due_date`
  forward one period (recalculated per D5 if applicable) without creating
  any transaction.
- **D7 — Transfers: paired transactions via a "Transfer" button on the
  account page.** Confirmed: this replaces the existing `transfer_account`
  field (confirmed not working correctly today) — it will be removed from
  `TransactionForm` and the `Transaction` model in Phase 3. New flow: on
  an account's page, a **Transfer** button opens a
  small form — Transfer To (account), Amount, Date. Submitting creates
  **two** transactions:
    - In the source account: negative amount, `category` = the source
      account's own auto-created transfer category (D4-in-spirit, but
      transfer-type not income/expense), `payee` = "Transfer Money"
      (shared system payee), `memo` = "Transfer to {destination account}".
    - In the destination account: positive amount, `category` = the
      *destination* account's own transfer category, same `payee`,
      `memo` = "Transfer from {source account}".
  Both resulting transactions are regular transactions — same look, same
  edit/delete behavior as anything entered by hand; only their *creation*
  is streamlined. Each account has exactly one auto-created transfer-type
  category, auto-created with the account and auto-deleted with it. Per
  your P4 feedback, these transfer categories **do** show up in the
  normal category list/management UI (not hidden) — they're just always
  excluded from Income/Expense reports so they can't inflate those
  numbers. This same per-account transfer category is what D1's opening-
  balance transaction uses.
- **D8 — Quarterly backup checked only at app launch.** Confirmed, no
  background scheduler/OS task needed.
- **D9 — Transaction numbers: plain integer, no 10001 offset needed.**
  The Quicken problem you're remembering (order-by-number going
  1, 10, 100, 1000, 2, 20… instead of 1, 2, 3…100…2746) happens when the
  number is stored/sorted as *text* — string sort compares character by
  character, so "10" sorts before "2". Storing `transaction_number` as a
  real integer column and always sorting with `ORDER BY transaction_number`
  (numeric, not text) makes that bug structurally impossible here. Once
  it's a true integer, the 10001-offset trick isn't needed either — that
  was only working around Excel stripping *leading zeros* from a
  zero-padded text value like "00001"; a plain "1", "2", "3" has no
  leading zero to strip, so no offset is required. Scoped per
  **(account, year)**, starting at 1 each calendar year (by the
  transaction's own date) — check-register style, one sequence per
  account rather than one shared across all accounts, since that matches
  how a physical checkbook works. If you ever want the "always looks like
  a 5-digit code" cosmetic, that's a display-only zero-pad (e.g. show
  "00001") layered on top of the real integer — doesn't reintroduce
  either bug, and CSV/XLSX export can force that column to be treated as
  text (Excel supports this cleanly) so it round-trips safely too.
  Editing a transaction's number is manual/free-text in the UI (no
  strict uniqueness enforced), and moving a transaction to a different
  account does **not** auto-renumber it — you keep whatever number it had
  and can fix it by hand if it collides, so an edit never silently
  clobbers a number you set on purpose.
  **Revised 13/09/2026:** the "scoped per (account, year)" part above is
  no longer current — after seeing two new accounts each start at 1, the
  user asked for one sequence **per year only, shared across every
  account** instead (so a second account's first transaction continues
  the running count rather than restarting it). `Transaction.next_number()`
  now looks at the year alone, dropping the account filter. Everything
  else about D9 (real integer column, no offset, freely editable, no
  strict uniqueness) is unchanged.
- **D10 — Regional settings: detected from the computer once, then yours
  to change.** (Decided by Claude at the user's request, 2026-09-12.) When
  the user profile is first created (Phase 3), Stashy reads the computer's
  time zone (as an IANA name like `Europe/Sofia`), date format, number
  format and first day of week, and saves them as profile preferences
  editable on a settings page. They are *not* re-read on every launch:
  Windows' display language often differs from the format a person
  actually wants, and imported data must never silently change meaning
  because an OS setting changed. Consequences:
    - The audit log stores raw values (ISO dates, plain decimals, UTC
      timestamps) and formats them when displayed, using the viewer's
      preferences.
      **Revised 15/09/2026:** superseded by Phase 14 (A3) - the audit log
      shows raw, database-style values (ISO dates, stored keys) in the
      user's own time zone instead of formatting them per viewer.
    - CSV import gets a per-file date format: auto-detected from the file
      when unambiguous (any day above 12 settles it), otherwise defaulting
      to the profile's format — a bank file's format has nothing to do
      with the computer's.
    - Template filters, date inputs, the date picker and chart number
      formatting read the profile; `stashy/formats/` becomes the fallback.
    - A detected default can differ from what someone wants (e.g.
      Bulgarian regional settings use a comma decimal, `10 000,50`, while
      this user prefers `10 000.50`) — that's what the settings page is for.

---

## Cross-phase dependency notes

- D1 (opening balance) and D7 (transfers) both need: a per-account
  auto-created transfer-type category, and the transaction-numbering
  scheme. To avoid building Accounts twice, **the transfer-category model
  work and the `transaction_number` field are pulled into Phase 3** rather
  than waiting for the later Categories/Transactions phases; those later
  phases then build their fuller UI on top of what already exists.

---

## Phase 1 — Foundation (setup + confirmed bugs + global formatting)

**Goal:** safe to build everything else on top of. No visible feature
changes beyond formatting and bug fixes.

- [x] One-file Windows setup: `Stashy.bat` that creates the venv, installs
      `requirements.txt`, generates `.env` with a real `SECRET_KEY`, runs
      migrations, creates the single login, and launches the server +
      opens the browser. **Limitation to flag honestly:** this still
      requires Python 3 already installed on the machine (can't silently
      install Python from a `.bat` without extra risk/complexity) — the
      `.bat` will detect a missing Python and print clear instructions
      instead of failing silently. A fully zero-install version (bundling
      a portable embeddable Python) is possible later as a stretch goal.
- [x] Bug 1 — money parsed as `float` on CSV/OFX import → change to
      `Decimal` everywhere money is parsed or touched.
- [x] Bug 2 — deleting a parent category silently cascades to its
      subcategories → confirm-delete screen must list every subcategory
      that would be deleted, so you can cancel and move them first.
- [x] Bug 3 — custom report date range with a missing start/end silently
      falls back to the default range → make it a validation error instead.
- [x] Bug 5 — memorized-payee defaults get refreshed on *every* transaction
      save, including unrelated edits (e.g. toggling reconciled) → only
      refresh the payee's default when `category`, `memo`, or `amount`
      actually changed (or the transaction is new).
- [x] Bug 4 — transaction list/account register performance at ~4,500+ rows
      with no pagination: keep no traditional page-numbers UI (per your
      preference), shrink row height, and if rendering ~4,500 rows turns
      out sluggish in practice, add "load more"/incremental rendering
      rather than numbered pages. Will test with your real row count
      before deciding it's needed.
- [x] Global formatting helpers, used everywhere from here on:
      - Dates: `DD/MM/YYYY` display everywhere (template filter/helper).
      - Numbers: `10 000.50` style (space thousands separator, period
        decimals, 2 decimal places) via one shared template filter.

**Done 2026-09-12.** Notes from implementation:
- Launcher is `Stashy.bat` (thin) + `scripts/bootstrap.py` (does the real
  work) — one double-click for both first-time setup and daily launching.
- Bug 4 measured: 4,468 transactions render in ~450 ms server-side with a
  fixed 5 queries (~2.4 MB of HTML) after fixing an N+1 query, so no
  "load more" was needed. Revisit only if it feels slow in the browser.
- Also fixed while testing the launcher on a clean install:
  `requirements.txt` was UTF-16 (pip can't read that) and pinned a
  `python-dateutil` version that doesn't exist.
- Tests: `.venv\Scripts\python manage.py test finance` (19 tests).

---

## Phase 1B — Audit log

**Goal:** infrastructure that every later phase automatically benefits
from, so it goes in early rather than being bolted on afterward.

- [x] Track every create/update/delete across the real data models
      (Account, Category, Payee, Transaction, Reminder, and anything else
      that follows), including changes made by automated/system logic
      (e.g. a payee's memorized defaults refreshing, a reminder auto-
      advancing, an auto-generated import category), not just manual
      edits through a form.
- [x] Each entry records: what changed, old → new values, when, and
      whether it was a manual action or an automatic/system one.
- [x] Surfaced in the Django admin only for now (a read-only log view per
      object, e.g. via `django-simple-history` or an equivalent
      lightweight custom model + signals) — no separate front-end UI
      needed yet.
- [x] Purpose: forensics when something looks wrong ("why is this balance
      off") — not a undo/redo system, just visibility into history.

**Done 2026-09-12.** Notes from implementation:
- Built as a small custom `audit` app (one chronological table + signals)
  rather than `django-simple-history`, which keeps a separate history table
  per model - one timeline answers "what happened?" in one place.
- Every entry has a Source (You / Automatic), a reason for automatic ones,
  the request that caused it, and an operation id grouping everything one
  click changed. Admin's History button on any record opens its trail.
- Rows blanked by on_delete=SET_NULL (which Django does silently) are logged
  too. Not visible: edits made directly in db.sqlite3 with another tool, and
  queryset.update()/bulk_create() - later phases must avoid those for
  audited data (relevant for Phase 4 mass re-categorize).
- Shipped alongside: Waitress + WhiteNoise instead of the development
  server, every request atomic, Sofia time zone, account delete warning.

---

## Phase 2 — Visual refresh (Stashy branding + dashboard redesign)

**Goal:** establish the new look once, so every later phase is built on it
instead of restyled twice.

- [x] Design direction: fresher, younger-skewing but still simple — light
      color palette built around the existing navy/green, squirrel mascot
      as a recurring character, less "corporate bank dashboard," more
      approachable.
- [x] Use the real mascot artwork from the `images/` folder (three renders
      of the same backpack-and-acorn-coin squirrel) instead of the
      emoji placeholder: the rounded-square teal version as the real
      favicon/app icon, the full-body white-background version for empty
      states and the login screen, the close-up face for small
      spots (e.g. a loading state or a tucked-away corner badge).
- [x] Light/dark mode toggle button (Bootstrap 5.3's `data-bs-theme`
      already supports this cleanly — `base.html` just hardcodes
      `data-bs-theme="light"` today).
- [x] Fix the generic form layout — forms currently sit in a narrow column
      on the left with the entire right side empty (`generic_form.html`);
      redesign to use the space properly (e.g. a two-column layout with
      contextual help/preview/related info on the right, or just a wider
      centered form) as part of this pass.
- [x] Replace CDN-hosted Bootstrap/Chart.js with locally vendored copies
      (needed anyway for true offline/no-internet local use — flagged
      earlier when discussing the `.exe` question).
- [x] Dashboard rebuild to fit one viewport, no scrolling, on a typical
      laptop screen:
      - Net worth, month spending, month income, accounts, spending-by-
        category — kept, made smaller/denser.
      - Spending-by-category pie/donut becomes clickable — clicking a
        slice drills into that category's transactions for the period.
      - Remove "recent transactions."
      - Keep "upcoming reminders," retargeted to explicitly surface
        anything due within the next 7 days (bills or income), satisfying
        your "reminder somewhere on the dashboard" ask without a separate
        duplicate widget.
      - New tile: **last month's savings rate** — (income − expense) /
        income for the previous calendar month, not the current one,
        since most of your spending lands near month-end and a live
        current-month number would be misleadingly low most of the time.

**Done 2026-09-12.** Notes from implementation:
- Everything loads locally now (Bootstrap, icons, Chart.js, flatpickr, fonts)
  — a test fails if any page references an internet URL.
- Fonts: Nunito (headings) + Manrope (text and numbers, tabular digits),
  both with Cyrillic. Colors are light/dark token pairs in
  `finance/static/finance/css/stashy.css`; theme follows Windows until the
  toggle is used, then the choice is remembered in the browser.
- Mascot web images are generated from `images/` into
  `finance/static/finance/img/` (favicon 3 KB, face 15/50 KB, full body
  WebP 11/32 KB); `images/` stays the gitignored source art.
- Dashboard verified to fit one screen at 1280x720, 1366x768 and
  1920x1080. Drill-down links into the transaction list using new
  `start`/`end`/`category=none`/`with_subcategories` filters — groundwork
  that Phase 5's date presets build on.
- Mobile now has a menu (before: no navigation at all below 768 px).
- Fixed along the way: a category set as its own parent (or under its own
  subcategory) hung the app; error messages weren't red; chart axes showed
  ISO dates.

---

## Phase 3 — Accounts (+ pulled-forward: transfer categories, txn numbering)

- [x] Account taxonomy, using your naming:
      - **Physical Liquid** — cash and cash-like items you physically
        hold: Cash, Coupons/Vouchers.
      - **Digital Liquid** — spendable balances with no physical form:
        Checking/Debit Card, Gift Cards (digital), E-Wallet/Prepaid Card.
      - **Illiquid** — not immediately spendable, or a liability: Savings,
        Investment, Retirement, Loan, Credit Card, Property/Other Asset.
      - Each group gets a short 2-3 word explainer shown in the UI (the
        italic phrases above) so it's clear at a glance without having to
        guess from the label alone.
      - Plus the existing `is_active` flag drives a 4th display bucket:
        **Inactive**, always last regardless of its original type.
- [x] Account list always ordered: Physical Liquid → Digital Liquid →
      Illiquid → Inactive, with a small visual divider between groups, name
      ascending within each group.
- [x] `institution` stays optional/blank by default, only filled in when a
      real bank is behind the account (already mostly true — minor cleanup
      only).
- [x] Remove `starting_balance` field; account creation instead takes an
      opening-balance amount + date and generates the synthetic
      "Opening Balance" transaction per **D1**. Existing accounts migrated
      the same way (one synthetic transaction each, preserving current
      balances).
- [x] `transaction_number`: add the field to `Transaction` now per **D9**
      (auto-generated, editable, plain integer, resets to 1 each new
      calendar year per account) since D1's opening-balance transaction
      needs one. Full filtering/sorting UI for it lands in Phase 5, but
      the field and numbering logic are built here.
- [x] Auto-created/auto-deleted per-account transfer-type category (new
      `CategoryType.TRANSFER`), hidden from normal category management UI,
      used by both the opening-balance transaction (D1) and the Transfer
      button (see below, D7).
- [x] **Transfer button** on the account page — Transfer To, Amount, Date
      → generates the paired transactions exactly as described in D7.
- [x] Currency: main/default currency lives on your user profile (a small
      `Profile` model, one-to-one with the login `User`), not as a bare
      app-wide settings constant — per-account currency field defaults to
      it. `ExchangeRate` model (manually editable) feeds net-worth
      conversion.
- [x] All dates on this page in `DD/MM/YYYY` (Phase 1's global helper).
- [x] "Check rates" button (Frankfurter API, per D2) — fetches the latest
      rate for each account's currency against the main currency, shows a
      "requires internet" disclaimer, and displays each account's balance
      converted to the main currency (feeding into net worth). Manual
      rate entry stays available as the offline fallback/override.
- [x] Regional preferences on the profile per **D10**: time zone, date
      format (`DD/MM/YYYY`, `MM/DD/YYYY`, `YYYY-MM-DD`), number format
      (`10 000.50`, `10,000.50`, `10.000,50`, `10 000,50`), first day of
      week — detected when the profile is created, changeable on a
      settings page, activated per request by middleware.
- **Moved to Phase 14 (15/09/2026):** Audit log switches to storing raw values and formatting them at
      display time (D10). Existing test-data entries can be reset.
      **Partly done:** the audit admin's own timestamp column now reads
      the viewer's preferences (a raw `DateTimeField`, easy to reformat at
      display time). The `changes` JSON itself (amounts, dates, category
      names inside a diff) is still formatted once at write time using the
      global DD/MM/YYYY + space-dot style — correct for this one user
      today, but would freeze old entries in their old format if
      preferences ever changed. Redoing that storage to keep raw values
      alongside enough type information to reformat every field later is a
      real refactor (touches `audit/signals.py`'s `_display`/`_snapshot`
      and every assertion in `audit/tests.py` that checks exact diff
      text) — left as a backlog item below rather than rushed.

**Done 2026-09-13.** Notes from implementation:
- New account taxonomy replaces the old 6 flat `AccountType` values with 11
  types grouped into Physical Liquid / Digital Liquid / Illiquid (plus the
  always-last Inactive bucket from `is_active`); `Account.AccountType` and
  `ACCOUNT_GROUPS` in `finance/models.py` are the source of truth, and
  `grouped_accounts()` does the ordering the account list and dashboard
  both use.
- `starting_balance` is gone; `AccountCreateForm` (create-only — editing an
  account never asks for it again) takes an opening-balance amount + date
  and creates one real `Transaction` for it, exactly like a Transfer (D1).
  Since the database held only test data, existing accounts weren't
  data-migrated field-by-field — the schema migration just drops the
  column, and `scripts/load_test_data.py` was re-run to rebuild everything
  including the new transfer categories and transaction numbers.
- Every account gets its own transfer-type `Category` automatically
  (`Account.ensure_transfer_category()`, called from `Account.save()`),
  renamed when the account is renamed and cascade-deleted with it — no
  shared cross-account transfer link exists, which turned out to simplify
  account deletion too (the old "N other transactions point here as a
  transfer" warning is gone; nothing outside the account can reference its
  transfer category). `CategoryForm`/`CategoryUpdateView`/
  `CategoryDeleteView` all refuse to touch a system-managed one by hand.
- **Transfer** button lives on the account detail page
  (`finance:account_transfer`); `TransferForm.save()` creates the two
  paired transactions inside one `system_action()` so they share an audit
  operation id.
- `Transaction.transaction_number`: a plain nullable `PositiveIntegerField`,
  auto-assigned via `Transaction.next_number()` (highest existing number
  for that account+year, plus one) only when left blank on create — an
  explicit number (including from historical import data) is always kept
  as-is, never renumbered. Not yet surfaced as an editable form field or
  sortable column; that's explicitly Phase 5's job per the plan.
- New `finance/currency.py` (`RateTable`, `convert_balances`,
  `fetch_latest_rates`) and `finance/regional.py` (`Preferences`,
  `detect_from_computer`, `format_number`/`format_date`). A new `Profile`
  model (one-to-one with `User`) holds main currency + regional
  preferences, created once via `Profile.for_user()` on first request
  (`finance/middleware.py`'s `RegionalPreferencesMiddleware` activates them
  for every request; `finance/context_processors.py` exposes them to
  templates as `{{ regional }}`). New Settings page
  (`finance:settings`) edits them and hosts the "Check rates" button.
- Regional detection reads the Windows registry (`sShortDate`, `sThousand`,
  `sDecimal`, `iFirstDayOfWeek`, `LocaleName`) plus `tzlocal` for the time
  zone — on this machine that's `dmy` / `10 000.50` / Monday /
  Europe/Sofia, matching what you'd already set by hand. `tzlocal` names
  zones like "FLE Standard Time" after their first city (`Europe/Kiev`),
  so `regional.zone_for_country()` cross-checks the computer's own country
  against `tzdata`'s `zone.tab` and prefers a same-UTC-offset zone actually
  in that country (`Europe/Sofia` for `bg-BG`). On non-Windows, or if
  anything can't be read, it falls back to the DD/MM/YYYY defaults.
  New dependency: `tzlocal` (added to `requirements.txt`).
- Frankfurter (`https://api.frankfurter.dev`) is called only from the
  Settings page's "Check rates" button — never automatically — and only
  writes new `ExchangeRate` rows on success; a failed/offline fetch leaves
  existing rates untouched and shows an error instead. Rates can also be
  entered by hand in the admin.
- Reports/dashboard now exclude transfer-type categories from income and
  spending (`finance/reporting.py`'s `excluding_transfers()`) — this was
  the exact "Transfer Money shows up as Uncategorized, inflating spending"
  problem flagged when the test data was first loaded; it's fixed now that
  every account has a real transfer category to put those rows in.
- `Account.balance` now quantizes to cents before returning (fixes the
  `9.66000000000000`-style trailing zeros from SQLite's floating-point
  `SUM()`, a backlog item from Phase 2's data load) and, with
  `.with_balances()`, can be computed for every account in one query
  instead of one query each.
- Money and date template filters (`money`, new `sdate`/`sdatetime` in
  `stashy_format.py`) now read the viewer's `Profile` instead of a single
  hardcoded style; so do the date picker and Chart.js number/date
  formatting (`data-*` attributes on `<body>` in `base.html`, read by
  `stashy.js`/`charts.js`). `stashy/formats/` remains the fallback for
  anything not yet running through a request (management commands, tests).
- Tests: 75 (25 new — opening balance, transfer categories, the Transfer
  button, transaction numbering, transfer exclusion from reports, currency
  conversion, account grouping, regional preference detection). Verified
  against the real reloaded dataset too (779 transactions, 6 accounts): all
  main pages and the new admin models render without error.

---

## Phase 4 — Categories

- [x] Add/Edit form: selecting category type (Income/Expense) filters the
      Parent dropdown to only show parents of that same type (small JS/
      dynamic-form piece).
- [x] Remove `is_active` from categories entirely (field + UI).
- [x] Category list: search by name; show usage (all transactions in that
      category, with a count); ordering — Income group first, then
      Expense, each group ordered by parent name ascending, subcategories
      ascending within their parent. Transfer-type categories (Phase 3)
      **do** appear in this list (clearly marked as system-managed —
      not user-creatable/deletable by hand), so you can see they exist
      and check their usage; they're just always excluded from
      Income/Expense reports (Phase 11) so they can't inflate those
      numbers.
- [x] Mass re-categorize tool: from a category's usage view, select some/
      all of its transactions and bulk-move them to a different category.

**Done 13/09/2026.** Notes from implementation:
- The Parent dropdown's type filtering is pure client-side JS
  (`generic_form.html`'s new `extra_js` block, gated by a
  `category_type_filter` flag the Category views pass): each `<option>`
  carries a `data-category-type` attribute (`CategoryParentSelect` in
  `forms.py`), and picking Income/Expense hides the non-matching ones,
  resetting the selection if it's now hidden. This is guidance, not a
  hard rule — the database doesn't force a subcategory to share its
  parent's type, matching what was asked (a UI nudge, not a constraint).
- Category list grouping/ordering lives in `finance/models.py`'s new
  `grouped_categories()` (same shape as Phase 3's `grouped_accounts()`):
  Income → Expense → Transfer, each bucket sorted by (top-level) parent
  name then subcategory name. Transfer categories land in their own
  group at the end, clearly marked "system-managed"; their Edit/Delete
  buttons are hidden entirely now (the redirect-with-error from Phase 3's
  `SystemManagedCategoryGuard` still exists underneath, but a disabled/
  hidden button is a better first line of defense than an error message).
- "Usage" is a click-through to a new `category_usage` view/page — the
  category's own transactions (not its subcategories' — deleting cascades
  to subcategories on purpose, but re-categorizing one category at a time
  seemed clearer than an implicit subtree rollup) with checkboxes and a
  "move selected to" dropdown. Moves are looped, one `transaction.save()`
  per row (never `queryset.update()`), so each move is individually
  audited and the memorized-payee-refresh logic still runs normally;
  every move in one submit shares one audit operation id since it's all
  inside one request. A transfer category can never be a move-to target
  (moving a transaction there needs the Transfer button, not a manual
  recategorize, so it gets the paired transaction and correct payee too).
- Tests: 89 (14 new). Also checked against the real 779-transaction
  dataset — search, usage counts, and the system-managed guard all
  behaved correctly; one smoke-test POST accidentally moved a real
  transaction's category, caught immediately and undone by re-running
  `scripts/load_test_data.py`.

**Follow-up tweaks, requested after first seeing Phase 4 (done 13/09/2026):**

- [x] Transfer categories are named `[Cash]`, `[Debit Card]`, etc. instead
      of "Cash transfers" — matches the bracket notation `test_data.TXT`
      itself already used for transfers, and reads better as a label.
      `Account.ensure_transfer_category()` is the only place that builds
      the name, so this was a one-line change.
- [x] Categories can only be nested one level deep: a category that's
      already a subcategory can never be chosen as someone else's parent
      (the Parent dropdown's queryset is now `parent__isnull=True` — top-
      level only), and a category that already has its own subcategories
      can't be demoted into becoming one (`CategoryForm.clean_parent`
      checks `self.instance.subcategories.exists()`). Matches "most people
      only need ~10 main categories" rather than an open-ended tree.
      Enforced in `CategoryForm`, the same place every other category
      business rule lives (not a database constraint) — consistent with
      how this app already treats the admin as an unconstrained escape
      hatch, and existing test data already fit this shape (Quicken's own
      `Parent:Child` categories are exactly one level).
- [x] Category list visually distinguishes a main category (bold) from a
      subcategory (indented, muted, with a small return-arrow connector
      icon) — new `.category-main`/`.category-sub` styles in
      `stashy.css`.
- [x] Wherever a category's full name is shown, it now reads
      "Parent > Subcategory" instead of "Parent: Subcategory" — a one-line
      change to `Category.full_name`, which every other display (dropdowns,
      the delete-confirmation list, the audit log's category diffs, the
      category list's subcategory rows) already builds on, so it took
      effect everywhere at once.

Tests: 95 (6 more). Re-verified against the real dataset — the bracket
names and the new "Parent > Child" format render correctly, and the
Parent dropdown on Add/Edit category now offers exactly the 21 real
top-level categories (down from all 109 rows, since subcategories and the
6 transfer categories are excluded).

---

## Phase 5 — Transactions

- [x] Date filter presets on the transaction list (reuse the pattern
      already built for Reports): This month, Last month, This year,
      Custom, plus the extra presets Reports already has (This quarter,
      YTD, Last 12 months, Last year) since they're already built.
- [x] Reconciled / unreconciled filter.
- [x] Transaction-number UI: editable in the transaction form, shown as a
      column, usable as a manual sort/reorder tool for same-day ordering
      (field itself was added in Phase 3).
- [x] Payment/Deposit two-column display instead of a single signed
      Amount column (per D3) — underlying data keeps its signed `amount`,
      this is a display-only change.
- [x] Inline "add row" at the bottom of the register to add a transaction
      directly in the list (not a separate form page), autofilling
      memo/category/amount from that payee's last transaction (ties into
      Phase 6's richer per-category payee memory once that lands).
- [x] Default view: this year, all accounts, all categories, ordered by
      date then transaction number (assuming newest-first, matching
      current behavior — flag if you want oldest-first instead); sortable
      by clicking column headers.
- [x] Number/date formatting per Phase 1 helpers.

**Done 13/09/2026.** Notes from implementation:
- Went with newest-first as the default (per the plan's own assumption) —
  flag if you'd rather have oldest-first; it's one constant to flip
  (`Transaction.Meta.ordering`) plus the sort-arrow defaults.
- The transaction list adds an "All time" option on top of Reports'
  existing date-range presets — a register naturally wants to see
  everything sometimes, unlike a chart that needs a bounded range.
  "This year" only kicks in when *nothing* was requested at all (a fresh
  visit); once you've picked anything from the Range dropdown — including
  "All time" — that stays sticky rather than snapping back, since Django
  can tell "range=" (explicitly chosen) apart from no `range` param at all.
- Sortable columns click through ascending → descending → (next click
  goes back to ascending); a small shared `_sorted()` helper in
  `views.py` handles both the transaction list and the account register,
  each with their own allowed columns (the register has no Account
  column to sort by, obviously). Uses Django 5.1+'s `{% querystring %}`
  tag to toggle `?sort=` while preserving every other filter already in
  the URL.
- Payment/Deposit columns show the amount **unsigned** (a new `abs_value`
  template filter) — putting a "-" in a column already labeled "Payment"
  would say the same thing twice. The red/green coloring still applies,
  so direction is still obvious at a glance.
- The inline add row lives on the account register (`account_detail.html`)
  specifically, not the all-accounts transaction list — it needs one
  definite account for autofill and defaults, and "the register" in
  Quicken terms has always meant one account's own page. It reuses the
  same payee autofill JS/API endpoints as the full transaction form,
  just wired to different element ids.
- Clearing a transaction's number on edit now re-numbers it (previously
  only new transactions got auto-numbered) — found while wiring up the
  editable field: leaving it blank on an edit would otherwise have wiped
  it to nothing instead of assigning a fresh one.
- Tests: 115 (20 new). Verified against the real 779-transaction dataset
  (filters, sorting, the Number field) read-only, and the inline add row
  against a scratch copy of the database — not the real one, learning
  from the Phase 4 mishap — to confirm it creates a correctly-numbered
  transaction without touching real data.

---

## Phase 6 — Payees

- [x] Move memorization from one single default (category/memo/amount) per
      payee to one per **(payee, category)** pair, computed from each
      payee's actual transaction history (last transaction per category) —
      no fragile cache to keep in sync, always accurate.
- [x] Payee detail view: show all categories ever used with that payee,
      each with its last memo and last amount.
- [x] Transaction entry: typing a payee shows all of that payee's
      historical category/memo/amount combos (not just one default), so
      you can pick the matching one for repeat stores with multiple
      purchase types.
- [x] Payee list: search by name, filter by category/memo; default sort by
      payee name; sortable by category, memo, or amount too.
- [x] "Opening Balance" and "Transfer Money" system payees (Phase 3) stay
      out of normal autofill suggestions/noise where practical.

**Done 13/09/2026.** Notes from implementation:
- `Payee.default_category`/`default_memo`/`default_amount` are gone —
  migration `0005_payee_history_not_cached.py`. In their place,
  `Payee.category_history()` walks that payee's transactions newest-first
  and keeps the first (most recent) one per category — no cache, no
  refresh step, always matches reality. `Transaction.save()` got
  noticeably simpler as a direct result: the whole "did a memorized field
  change, and if so refresh the payee" dance (`MEMORIZED_FIELDS`,
  `_memorized_fields_changed()`) is gone, since there's nothing left to
  refresh.
- Typing a payee with **one** category on record still autofills
  silently, exactly like before. With **more than one**, a small pick
  list appears under the field instead of guessing — new shared
  `finance/static/finance/js/payee-autofill.js`, used by both the full
  transaction form and the account register's inline add row (they were
  two near-identical inline `<script>` blocks before this; factoring them
  out removed the duplication while adding the pick-list behavior once).
- Payee list: gave up on Django's `PayeeListView` (a plain `ListView`
  can't express "sort by the payee's most recent transaction's amount"),
  so it's a function-based view now. Filtering by category/memo searches
  *any* of a payee's transactions; the list columns and sorting are based
  on each payee's single most-recent transaction — computed with one
  correlated subquery for the whole page, not one query per payee, since
  this page can list well over a hundred payees at once.
- Payee detail page shows the *complete* per-category breakdown (unlike
  the list, which only shows the latest) — this is where "categories: 2"
  actually becomes useful information, with a link straight into the
  transaction list pre-filtered to that payee + category.
- The two system payees are excluded from `/api/payees/` (the typing
  suggestions) and, structurally, can never appear in `category_history()`
  combos for *themselves* being looked up oddly — but they still show up
  normally in the Payee list itself, same as Phase 4 kept transfer
  categories visible-but-marked rather than hidden outright. "Where
  practical" per the plan text meant: hide from autofill noise, not from
  a deliberate lookup page.
- CSV/OFX import's category guess now calls the same `category_history()`
  (via a small `_guessed_category()` helper) instead of reading a cached
  default — same behavior as before from the importer's point of view,
  just no longer backed by a field that could drift out of sync.
- Tests: 134 (18 new). Checked every new filter/sort/detail page against
  the real dataset read-only; found a real multi-category payee in it
  (a local bookshop) and confirmed its pick list renders both entries
  correctly.

---

## Phase 6.1 — fixes and small requests after using Phase 3–6 for real

Not part of the original plan — a batch of corrections and polish
requested after actually clicking through what Phases 3–6 shipped.

- [x] Payee list: **All categories** and **Total amount** replace the old
      "last category"/"last amount" summary, plus a **Usage** count (same
      pattern as the category list's usage column). Amounts are totaled
      per currency (a payee can span accounts in different currencies) —
      shown as one line per currency, e.g. an amount in EUR and one in
      USD side by side, never added together. Computed with 3 queries for
      the whole page (categories used, usage counts, per-currency totals),
      not one per payee.
- [x] Manual payee entry and CSV/OFX import now match case-insensitively
      ("Lidl" reuses "lidl") instead of creating a near-duplicate — the
      exact Quicken-export problem flagged in the Phase 3/9 backlog,
      fixed now rather than waiting for Phase 9's own import work.
- [x] The payee field on the transaction form has a styled, in-app
      dropdown (typing "li" shows matching payee names as a themed list)
      instead of the browser's native, unstyleable `<datalist>` popup.
- [x] Editing a transaction now returns you to wherever you opened it
      from — the account register or the all-accounts list, filters and
      sort intact — instead of always jumping to the transaction's own
      account page. A `next` parameter carried through the edit link and
      the form; falls back to the old behavior if nothing knows better
      (e.g. opening the edit page directly).
- [x] **Reverted:** the inline "add a transaction" row from Phase 5's
      account register. The dedicated New Transaction page covers it
      well enough on its own; `InlineTransactionForm` and its view/
      template code are removed rather than left unused.
- [x] Sortable column headers gained **Memo** (transaction list and the
      account register) and a sort link on **Deposit** too (previously
      only **Payment** was clickable, even though both come from the
      same signed `amount` field).
- [x] Checked the "new accounts all show transaction number 1" report:
      not a bug — `Transaction.next_number()` was scoped per account per
      year by design (D9), so two unrelated brand-new accounts each
      starting their own numbering at 1 was exactly what was specified,
      the same way two different physical checkbooks both start at
      check #1. **Follow-up, same day:** after hearing this, the user
      asked to actually change that design — see D9's revision note
      above and the "global numbering" entry below.
- [x] **D9 revised:** transaction numbering is now one sequence per
      **year only**, shared across every account, instead of one
      sequence per (account, year). `Transaction.next_number()` dropped
      its account filter; a second account's first transaction now
      continues the running count instead of restarting it. Migration
      `0006_transaction_number_help_text.py` is metadata-only (the
      field's help text changed) — the numbering rule itself lives in
      Python, not the schema, so no data migration was needed. Verified
      against a scratch copy of the real database: two brand-new test
      accounts now get sequential numbers (10779, 10780) instead of both
      landing on 1.
- [x] Investigated "where did my reminder go": a reminder due 14/09/2026
      existed right before the Phase 3 migration and was gone
      immediately after. Cause found: `scripts/load_test_data.py` wipes
      **all** finance data (including reminders) before every reload, and
      it was rerun several times since (Phase 3's migration, the
      `AccountType` rename, Phase 4, Phase 6) as a convenience for
      picking up schema/naming changes — each run silently deleted
      anything typed into the real app since the load before it. The
      script's own docstring is now a loud warning instead of the old
      "this only holds throwaway data" comment, which stopped being true
      once the app was being used for real between phases. Going forward:
      no more casual reruns without checking first.
- [x] "Admin & audit log" renamed to "Admin & Audit Log" and opens in a
      new tab instead of navigating away from the current page.
- [x] Account create/edit: the Notes field is 2 rows instead of Django's
      10-row textarea default, so the form (now longer, with the opening-
      balance fields on create) fits on one screen again.
- [x] Category list's and payee list's usage counts show **paler** when
      the count is 0, so an unused category or payee stands out at a
      glance (spotting mistakes, or categories created but never used).

Tests: 144 (28 new). Verified everything against the real dataset
read-only (no repeat of the Phase 4 mishap).

---

## Phase 6.2 — more fixes after Phase 6.1

- [x] **Reminder's `name` field removed entirely** (model, form, admin,
      migration `0007_remove_reminder_name.py`), per the user's direct
      answer to Phase 7's open question. A reminder is now identified
      like a pending transaction — payee first, category as a fallback,
      "Reminder" if it has neither — the same idea `Transaction.__str__`
      already uses, applied to `Reminder.__str__` too. The reminder list
      and the dashboard's "Due soon" panel show **Payee** where **Name**
      used to be; nothing else on either page changed yet (the rest of
      the list redesign is still Phase 7, not done here).
- [x] The New Transaction form now **prefills the transaction number**
      with whatever would be auto-assigned anyway (today's date's next
      available number), instead of leaving the field blank until save.
      Editing an existing transaction is unaffected — it still shows
      that transaction's own number, never a fresh preview. Since
      numbering is global-per-year (D9, revised earlier today), the
      preview doesn't depend on which account is selected.

Tests: 151 (7 new). Verified against the real database (read-only): a
real reminder now displays as "16/09/2026 | Landlord | 10.00", and a fresh
Add Transaction page correctly prefills 10779, matching
`Transaction.next_number()`'s own answer.

- [x] **Payee list "Categories" column showed the same category
      repeatedly** instead of once — a real bug, not a display
      preference: `Transaction`'s default ordering was silently riding
      along into a `values().distinct()` query, so the database compared
      rows on (category, date, transaction number, created-at) instead of
      just (payee, category), making "distinct" nearly meaningless for
      any payee used more than once in the same category. Confirmed on
      the real data — e.g. the water company's payee listed "Bills & Utilities > Water"
      eight times, once per transaction. Fixed with one `.order_by()` to
      clear the ordering before `.distinct()`. Categories now also render
      **one per row** instead of comma-joined, per the request.
- [x] Dashboard's Accounts panel now uses the **same grouping and order**
      as the Accounts page (Physical Liquid → Digital Liquid → Illiquid),
      with a plain rule between groups — no repeated group names/
      explainers, to keep the compact panel compact. It already excluded
      inactive accounts (the dashboard's own account list is filtered to
      `is_active=True`); that part needed no change.
- [x] The spending donut's center figure now shows the main currency
      (e.g. "1 234.56 EUR") instead of a bare number, in a smaller
      sub-label under the amount. The category legend's per-row amounts
      stay unlabeled, to avoid repeating it on every line.

Tests: 156 (5 more). The category-dedup fix is a genuine bug fix, not
just styling — re-verified directly against the real, previously-broken
data (that payee now shows "Water" once, 8 transactions, one summed
total) before calling it done.

---

## Phase 7 — Reminders

- [x] `amount` becomes optional (nullable) for variable bills.
- [x] Remove `auto_enter_days_before` entirely — confirmed dead (only ever
      referenced in the model field and `ReminderForm`'s field list, never
      read anywhere) before deleting it.
- [x] Remove `is_active`; add **Skip** action per **D6**.
- [x] Add a computed status: **Due soon** / **Paid** / *(blank = pending)*
      — "due soon" threshold 7 days (`Reminder.DUE_SOON_DAYS`, the same
      constant the dashboard's own lookahead window now reads).
- [x] "Enter" no longer silently creates the transaction — it opens a
      pre-filled transaction form (account/payee/category/amount from the
      reminder) so you can review/adjust before saving, which also covers
      reminders with no fixed amount.
- [x] Filter buttons: next 15/30/90 days, yearly; plus an include/
      exclude-paid toggle.
- [x] "End of period" due-date mode per **D5** — per-reminder toggle,
      recalculated each time the reminder advances.
- [x] `end_date` gets an explicit "No end date" affordance in the UI
      instead of just leaving the field blank.
- [x] Add/Edit form: picking Income/Expense filters the Category dropdown
      to that type only — the same type-filtered-parent JS pattern
      `CategoryForm` already uses (Phase 4), applied here to Reminder's
      `reminder_type` → `category` instead of `category_type` → `parent`.
- [x] Drop **Name** from Reminder entirely — done early (13/09/2026,
      see Phase 6.2). The fuller list redesign (**Status, Due, Type,
      Account, Payee, Amount** + action buttons) is done in this phase.

**Done 13/09/2026.** Notes from implementation:
- **"Paid" needed a real link, not a guess** — Stashy deliberately keeps
  no record of which transactions a reminder generated (D6), so there was
  nothing to compute "paid" from. Asked the user directly; they confirmed
  adding a one-way FK: `Reminder.last_entered_transaction` (nullable,
  `on_delete=SET_NULL`), set whenever **Enter** is used, so deleting that
  transaction automatically un-marks the reminder as paid, while deleting
  the *reminder* never touches the transaction (the FK only points one
  way). **Status priority**, in order: `next_due_date` in the past →
  **Overdue**; within `DUE_SOON_DAYS` → **Due soon**; otherwise, if
  `last_entered_transaction` is still set → **Paid**; otherwise blank
  (pending). Splitting "Overdue" out from "Due soon" as its own visible
  label was a small addition beyond the plan's 3 named states, since the
  data already distinguishes them and a red "Overdue" reads more clearly
  than an amber "Due soon" for something already late.
- **Skip clears the paid link.** Pressing Skip means "not paying this
  one" — it advances `next_due_date` (same as before) and also clears
  `last_entered_transaction`, so a reminder you skip doesn't sit showing
  "Paid" from whatever you entered last cycle.
- **"Paid" fades back into "Due soon"/"Overdue" on its own** — status
  priority checks the date *before* the paid link, so a reminder you paid
  early naturally stops showing "Paid" once its real next occurrence
  actually approaches again, with no extra bookkeeping needed.
- **"Enter" is now a normal GET into the transaction form**, not a POST
  that mutates anything — `?reminder=<id>` prefills account/payee/
  category/amount/date/memo from the reminder (amount left blank if the
  reminder has none) and carries a hidden `reminder` field through to the
  POST. On save, the created transaction is linked, the reminder advances,
  and you land back on the reminder list — otherwise the form behaves
  exactly like a normal new transaction. This also happens to close a
  backlog item: the old `reminder_enter` view accepted a plain GET and
  created a transaction on the spot, so merely visiting its URL wrote
  data; that view is gone.
- **Old bug fixed in passing:** `advance_due_date()`'s audit message still
  referenced `self.name`, which stopped existing when Name was removed in
  Phase 6.2 — every real Enter since then would have raised
  `AttributeError` before this phase's own changes replaced that method
  body. Fixed as part of rewriting `advance_due_date()`.
- **"Yearly" filter** reads as `next 15/30/90 days` extended to `365`
  days, matching the increasing-horizon pattern of the other three
  buttons — a judgment call since the plan text didn't spell out a number.
- **End-of-period biweekly** anchors "that 2-week span" to the Monday of
  the due date's own week (matching D5's Monday-start convention already
  used for weekly), landing on the Sunday 13 days later.
- **Category dropdown filtering** reuses `CategoryParentSelect` (Phase 4's
  widget that tags each `<option>` with `data-category-type`) directly —
  Reminder's category field already only listed non-transfer categories,
  so no new widget class was needed, just the same generic_form.html JS
  block pattern under a new `reminder_type_filter` flag.
- Migration `0008_phase7_reminders.py`: drops `is_active` and
  `auto_enter_days_before`, adds `end_of_period` and
  `last_entered_transaction`, makes `amount` nullable. Applied to the real
  database after checking its one real reminder first (active, default
  `auto_enter_days_before=0`) — nothing meaningful was lost.
- Tests: 188 (32 new, `finance/test_phase7.py`). Verified against the real
  database (read-only): dashboard, reminder list, reminder edit and the
  Enter flow (`?reminder=3`) all render; the one real reminder
  ("16/09/2026 | Landlord | 10.00") computes as `due_soon`, as expected three
  days out from today (13/09/2026).

---

## Phase 7.1 — auto-generate future occurrences, add a real One-time frequency

Not part of the original plan — requested 14/09/2026 after using Phase 7's
recurring reminders for real: entering a monthly reminder only ever
advanced its single due date, so you never had more than one future
occurrence visible at a time, unlike Quicken (one series definition,
many materialized future instances you can act on independently). Also
flagged: most reminders you'd actually create are genuinely one-off, but
there was no way to say that — every reminder had to be forced into some
recurring frequency.

- [x] **New `Frequency.ONE_TIME`**, made the default frequency (matching
      "the current idea of reminders are done to serve only one-time
      reminders but they cannot be pushed as one-time").
- [x] **A recurring reminder auto-generates its own future occurrences**
      as ordinary `Reminder` rows (`frequency=one_time`, linked back via a
      new `series` field) — each shows, sorts, and can be entered/skipped/
      edited/deleted independently in the reminder list, so paying March
      doesn't touch April.
- [x] **Generation horizon:** through the `end_date` if one is set
      (regenerated whenever it changes - occurrences beyond a shortened
      date are removed, new ones added if it's pushed out); otherwise
      through `max(today + 90 days, 31 December this year)` — confirmed
      with the user this is genuinely non-trivial (not always 365 days):
      early in the year year-end is further out and wins; from roughly
      October onward, 90 days spills into next year and wins instead.
      Matches the reminder list's own "Yearly" filter button, which was
      corrected in the same pass (see below).
- [x] **Entering or skipping the current (nearest) occurrence** still
      advances the series head forward one period exactly as in Phase 7,
      then re-tops-up the generated set: the now-redundant occurrence at
      that date is removed, and generation extends further if the horizon
      allows.
- [x] **One-time reminders (real one-offs, and auto-generated
      occurrences) never disappear on Enter/Skip** — confirmed with the
      user: keep the row, marked **Paid** (Enter) or **Skipped** (Skip),
      rather than deleting it. New `Reminder.skipped` field for this;
      `status()` treats both as permanent for a one-time reminder (unlike
      a recurring reminder's "paid", which fades back once its next real
      occurrence approaches).
- [x] Reminder list's **"Yearly" filter button corrected** to mean
      "through 31 December this year" (what the user assumed it already
      meant), not a rolling 365-day window as Phase 7 had built it as.
      "Show paid" is now **"Show paid/skipped"**, hiding both by default.
- [x] Add/Edit form hides End date/No end date/End-of-period for a
      one-time reminder (nothing there applies to it), and refuses to let
      an auto-generated occurrence become its own new recurring series.

**Done 14/09/2026.** Notes from implementation:
- **Materialized rows, not computed-on-the-fly.** Considered generating
  occurrences as virtual/computed rows shown only in the list (nothing
  persisted beyond the recurring reminder itself), which would have
  needed no regeneration bookkeeping at all - but the user's own framing
  ("auto-create reminders... the records change") and wanting to act on
  one future month independently of the others both pointed at real,
  separately-editable rows, so each occurrence is an ordinary `Reminder`.
- **Regeneration hooks into `Reminder.save()` itself**, not just the
  form - the same pattern `Account.save()` already uses for
  `ensure_transfer_category()` (Phase 3), so direct ORM use (admin,
  scripts, shell) keeps a recurring reminder's occurrences in sync too,
  not only edits made through the Add/Edit form. `advance_due_date()`'s
  own `self.save(...)` triggers it the same way.
- **Regeneration never touches an already-resolved occurrence** (paid or
  skipped), even if a shortened end date would otherwise drop it, and
  never rewrites the fields of an occurrence that already exists for a
  wanted date - so hand-editing one instance (e.g. a higher amount one
  particular month) survives the next regeneration untouched. It only
  adds occurrences for newly-wanted dates and removes unresolved ones
  that are no longer wanted.
- **Deleting the whole series cascades to its occurrences** (`series`
  is `on_delete=CASCADE`) - including, at the time this was written,
  resolved (paid/skipped) ones. **Revised the next day (Phase 7.2,
  14/09/2026):** the delete-confirmation work surfaced that this silently
  destroyed real paid/skipped history, not just unused previews -
  `Reminder.delete()` now detaches (`series` -> null) any already-resolved
  occurrence first, so only still-open ones are removed with the series.
  This still differs from `last_entered_transaction`'s `SET_NULL`
  (Phase 7): that link exists so deleting a *transaction* doesn't strand
  a reminder in a wrong status, but deleting the whole recurring *series*
  is read as "get rid of this and everything still open," matching how
  deleting an account cascades to its transactions elsewhere in the app -
  just not to history that's already been recorded.
- Migration `0009_reminder_auto_generated_occurrences.py`: adds `series`
  and `skipped`, and the `frequency` choices/default change. Applied to
  the real database after backing it up; the one real recurring reminder
  (monthly, due 16/10/2026, ending 24/12/2026) had no occurrences yet
  since it predated this change and migrations don't trigger model
  signals - ran `reminder.regenerate_occurrences()` once by hand
  afterward, which correctly created its November and December
  occurrences (December 16, not January, since the series ends
  24/12/2026).
- Tests: 203 (15 new, `finance/test_phase7_1.py`; a few Phase 7 tests
  updated where they'd relied on the old MONTHLY default frequency or
  the renamed `show_paid` -> `show_done` parameter). Verified against the
  real database (read-only, plus the one deliberate `regenerate_occurrences()`
  call above): the reminder list shows the real reminder's two generated
  occurrences with the auto-generated marker icon, and the dashboard
  still renders correctly.

---

## Phase 7.2 — reminder/dashboard/transaction polish after using Phase 7.1

Requested 14/09/2026, a mixed batch — implemented the unambiguous items
directly; three genuinely open design questions were asked in chat first
(see below) rather than guessed at, per the user's explicit reminder that
day to ask about vague things instead of deciding alone.

- [x] Reminders panel defaults to **next 15 days** on a fresh visit
      instead of showing everything; **All** is now its own explicit
      choice (`window=all`) rather than "no window param," so it's
      distinguishable from a first visit and stays sticky once picked -
      same pattern the transaction list's own default range already uses.
- [x] **Last day of period** (renamed from "End of period," help text
      removed) moved between Next due date and End date on the Add/Edit
      form, matching the order you actually read the fields in.
- [x] **Category dropdowns now order alphabetically with subcategories
      next to their own parent** (`Personal Care`, then
      `Personal Care > Haircut`, ...) instead of two separate blocks -
      all top-level categories first, then all subcategories after. Only
      dropdowns changed; the category *list page*'s own Income/Expense
      grouping (Phase 4) wasn't touched, as asked.
- [x] **`Every 2 weeks` replaced with a free interval**: a new `Every`
      field next to Frequency, with the unit word (week(s)/month(s)/
      quarter(s)/year(s)) shown next to it and updating live as you
      change Frequency. `weekly` + interval 2 now covers what the old
      fixed `BIWEEKLY` choice did; `interval` defaults to 1 everywhere
      else so nothing else changed behavior.
- [x] **Enter's Memo now prefers what you actually wrote last time** for
      this exact payee + category combo (Phase 6's memorization),
      falling back to the reminder's own Notes only if no such history
      exists.
- [x] **Reminder amounts show signed** (negative for an expense)
      everywhere a reminder displays one - the list, the dashboard's Due
      soon widget, and the admin.
- [x] Removed the small auto-generated marker icon from the reminder
      list - not needed.
- [x] **Reminder delete confirmation is now explanatory**, not just
      "delete this?" - a series head's confirm page names how many
      upcoming occurrences will also be deleted; an occurrence's own
      confirm page notes it's one part of a recurring reminder and
      deleting it won't affect the others.
- [x] **Dashboard donut chart shows the currency everywhere it shows an
      amount** - the legend's per-row totals and the hover tooltip, not
      just the center total (Phase 6.2).
- [x] **Transactions list ("all view") gets a Delete button**, next to
      Edit, with the same "return to where you were" behavior Edit
      already had.
- [x] Transaction status filter relabeled: "Reconciled or not" ->
      **All Transactions**, "Reconciled only" -> **Reconciled**,
      "Not reconciled" -> **Unreconciled**.

**Asked instead of guessed, then answered and implemented:**
- [x] Reminder **Edit**, when a series connection exists (it's a series
      head with occurrences, or it's an occurrence itself), now asks
      **"just this occurrence" or "the whole series"** before opening the
      form. Answered: "the whole series" updates the root's own
      definition *and* every currently-generated, still-open occurrence
      to match - only the shared identity fields (account, payee,
      category, amount, type, notes), never schedule fields
      (frequency/interval/dates, which stay per-row) and never an
      already-paid/skipped occurrence. A standalone reminder with no
      series connection skips the prompt entirely and edits exactly as
      before.
- [x] Delete-confirmation page redesigned: answered "too plain/abrupt" -
      added a danger-toned icon badge (matching the dashboard's own
      `.stat-icon` pattern, new `.stat-icon-danger`), turned the
      "can't be undone" line into a proper red warning with an icon
      instead of small gray text, and relabeled the button "Yes, delete
      it" for clearer intent. `finance/templates/finance/
      generic_confirm_delete.html` is shared by every delete flow
      (accounts, categories, payees, reminders, transactions), so this
      landed everywhere at once.
- [x] Reconciled vs. unreconciled transactions now look different on the
      **all-accounts transaction list** specifically (the account
      register already had its own R/C/— badge column, untouched):
      answered "muted/faded" - a reconciled row gets `opacity: .6`
      (`.row-reconciled`), dimming everything on it proportionally
      (including the red/green payment/deposit coloring) rather than
      fighting that color with a flat muted text color.

**A design fix found along the way, not asked for:** building the
delete-warning surfaced that deleting a recurring reminder used to
cascade-delete *every* occurrence it had ever generated (`series` is
`on_delete=CASCADE`), including ones you'd already paid or skipped - a
real record, not a preview. `Reminder.delete()` now detaches
(`series` -> null) any already-resolved occurrence first, so only
still-open ones are removed with the series. The delete confirmation's
occurrence count reflects this (only counts what would actually be
deleted).

Tests: 231 (28 new across `finance/test_phase7_2.py` and
`finance/test_phase7_3.py`). Verified against the real database:
migration applied after a fresh backup, all key pages (dashboard,
reminder list, reminder edit, transaction list, a delete-confirmation
page) render correctly, the real recurring reminder's edit page shows
the scope prompt as expected, and the transaction list's reconciled rows
(726 of the real dataset's ~779) render faded.

**Immediate polish, requested right after seeing the above (done same
day, no questions needed this time):**

- [x] A reminder with no amount shows **blank**, not "Varies" — the
      `|moneyc` filter already returns "" for `None`; the template's own
      `|default:"Varies"` fallback was overriding that unnecessarily.
- [x] Reminder delete now reads as a real sentence — **"Delete Income
      reminder for Landlord?"** (type + payee, falling back to category,
      then "this account") — instead of the reminder's dry `__str__`
      ("16/10/2026 | Landlord | 10.00"), which is fine for identifying a row
      in a list but reads oddly stitched into "Delete X?". New
      `delete_title` block on `generic_confirm_delete.html` so this
      overrides cleanly without duplicating the rest of the page; every
      other object's delete page (Account/Category/Payee/Transaction)
      keeps the default `Delete {{ object }}?`, since their own
      `__str__` already reads fine there.
- [x] Delete confirmation page centered (`mx-auto`) instead of hugging
      the top-left corner of a wide content column.
- [x] Donut chart's currency is now the actual **symbol** ($, €, ...),
      not the 3-letter code, in the legend, the hover tooltip and the
      center total — matches every other amount on the page (all
      rendered through `|moneyc`, which already used the symbol). New
      `|currency_symbol` template filter (same `CURRENCY_SYMBOLS` table
      `|moneyc` uses) plus a `currency_is_suffix` flag so the JS places
      it before or after the number the same way `|moneyc` decides
      server-side ("$9.66" vs "9.66 €") instead of always suffixing.

Tests: 235 (4 more, `finance/test_phase7_4.py`); one Phase 6.2 test
updated for the symbol change. Verified against the real database
(EUR): dashboard shows "€", the real recurring reminder's delete page
reads "Delete Income reminder for Landlord?", and an amountless reminder's
row is blank.

---

## Phase 8 — Reconciliation

New workflow, roughly modeled on Quicken's:

- [x] Pick an account — a **Reconcile** button on the account page, next
      to Transfer.
- [x] Opening balance auto-computed as the account's net balance up to
      (not including) the first unreconciled transaction.
- [x] You enter a statement ending balance + ending date.
- [x] Show all unreconciled transactions up to that date in two columns —
      deposits (left) and payments (right) — with checkboxes.
- [x] Checking/unchecking updates a running total live; when it matches
      your stated ending balance, you're done and everything checked gets
      marked `reconciled`.
- [x] If there's a leftover difference you accept, auto-create a single
      adjustment transaction for that difference so the account balances
      exactly, tagged clearly as a reconciliation adjustment.
- [x] `Reconciliation` model (account, statement date, statement balance,
      opening balance, resulting adjustment transaction if any) to keep
      an audit trail of past reconciliations.

**Done 14/09/2026.** Notes from implementation:
- **Opening balance** = the sum of everything already `reconciled=True`
  for the account, rather than literally "the balance up to the date of
  the first unreconciled transaction" as the plan phrased it — the two
  give the same number in the normal case (you reconcile chronologically)
  but the sum-of-reconciled version stays correct even if transactions
  were reconciled out of order for any reason, and it's a one-line
  aggregate (`Account.reconciled_balance()`) instead of a "find the
  earliest unreconciled row, then sum everything before its date" query.
- **Two-step flow, one view, nothing persisted until Finish.** Step 1
  (`ReconcileStartForm`: statement date + balance) renders step 2 (the
  checklist) directly rather than redirecting — the checklist itself is
  a single form POST at the end (`stage=finish`) carrying the checked
  transaction ids, so navigating away mid-session simply discards it;
  nothing is written to the database until you actually finish. Matches
  how the rest of the app already treats in-progress state (no draft
  rows, e.g. the Transfer form).
- **Already-`cleared` transactions are pre-checked** on first load of the
  checklist — the register's existing R/C/— status column previously had
  no real workflow behind "C" (cleared but not reconciled); this gives it
  one, matching Quicken's own convention of treating "cleared" as "already
  confirmed once, just needs the final reconcile pass."
- **The adjustment transaction** uses a new third system payee,
  `Payee.RECONCILIATION_ADJUSTMENT` (same `Payee.system()` get-or-create
  pattern as Opening Balance and Transfer Money), and the account's own
  transfer category (D7) — so it's excluded from Income/Expense reports
  the same way an opening balance or transfer is, since it's a bookkeeping
  correction rather than real income or spending.
- **A difference isn't silently absorbed.** Finishing with a non-zero
  gap between checked transactions and the statement balance requires an
  explicit "accept the difference and create an adjustment" checkbox
  (revealed by the JS running-total once there is a difference); without
  it, the server rejects the finish with an error and re-renders the
  checklist with your selections intact, rather than either blocking you
  entirely or quietly creating an adjustment you didn't confirm.
- Running total, and the accept-difference reveal, are plain client-side
  JS (`account_reconcile.js`) reusing the shared `stashyNumber` helper and
  the same symbol/prefix-suffix currency logic the dashboard donut uses
  (Phase 7.2 follow-up) — no server round-trip while checking boxes.
- Tests: 246 (11 new, `finance/test_phase8.py`). Verified against the
  real database: full flow (start → checklist → finish) run against a
  real test account with a genuine unreconciled transaction, confirmed
  it ends up `reconciled=True`/`cleared=True` and a `Reconciliation` row
  was created; migration applied after a fresh backup.

**Requested right after seeing the checklist (done same day):**

- [x] **Mark all** button above the two columns, toggling to **Unmark
      all** once everything's checked (and back, if you manually check
      every box by hand too) — pure client-side, no server round-trip.
- [x] Payments now show with a **negative sign** on this page
      specifically, rather than unsigned the way the Payment column
      does elsewhere — a deliberate difference from that convention,
      since here the sign is what tells checked deposits and payments
      apart quickly rather than needing to read the column header.
- [x] Each column gets its own **total** row (sum of everything listed,
      not just what's checked — the live running total in the summary
      bar above already covers "checked so far").

Tests: 249 (3 more). Verified against the real database: the checklist
page for a real account with several unreconciled transactions renders
the Mark all button and both totals correctly.

---

## Phase 9 — Import

- [x] **Accounts** — must already exist; a transaction row referencing a
      missing account fails (that row, or the whole import — deciding the
      exact granularity when I build this). Matches your "fail until it's
      created" call, since a wrong/typo'd account is the worst kind of
      import mistake to auto-correct silently (money ends up in the wrong
      place).
- [x] **Payees** — auto-create/update freely on import, no failure. Low
      risk since they're just reference data.
- [x] **Categories** — going with **auto-create over fail**, here's why:
      the main real-world use of this importer is a big one-time
      migration (your several years of transactions), and requiring
      every category to be hand-created first before a historical import
      can even start is exactly the kind of friction that makes migrations
      painful. Refining your "AutoGenerated" idea slightly: instead of
      permanently baking `(AutoGenerated)` into the category's *name*
      (which then shows up forever in every transaction row, dropdown,
      and report), auto-created categories get an `is_auto_generated`
      flag and a small badge in the category list UI — same visibility
      for later cleanup/merging, without cluttering the name everywhere
      it's displayed. Matching is case-insensitive against existing
      categories first, so "Groceries" from the file doesn't create a
      duplicate of an existing "groceries" category.
- [x] Transaction import gets extended to resolve `category` by name using
      the above rule (currently import only ever sets category via an
      existing payee's remembered default, never directly from the file).
- [x] Per-file date format for CSV import (D10): detect it from the file's
      rows when unambiguous, otherwise default to the profile's format, and
      let the user override it before importing.

**Done 14/09/2026.** Notes from implementation:
- **The "Accounts must already exist" bullet turned out to need real
  work, not just confirmation.** The importer already required picking
  one existing account for the whole file via a dropdown, which trivially
  satisfies "can't import into a missing account" — but your own
  `test_data.TXT` (a Quicken register export, the shape your eventual
  real multi-year migration will most likely come in) has its
  own **Account column per row**, for a single combined multi-account
  export. So the CSV importer now reads an optional `Account` column: if
  present, each row resolves against Stashy's existing accounts
  (case-insensitive name match, including inactive ones — a real
  migration might target one), falling back to the dropdown-picked
  account for any row where the cell is blank. **Granularity decision**
  (left open in the plan): an unmatched account name skips *that row*
  and keeps going, not the whole import — the same pattern already used
  for duplicate rows, and far less painful to recover from on a
  multi-thousand-row historical file than restarting after one typo.
  The success message reports how many rows were skipped this way.
- **Category resolution** (`_resolve_category()`) understands both
  Quicken's `Parent:Child` and this app's own `Parent > Child` — case-
  insensitive matching against existing categories first, auto-creating
  only what's genuinely missing. A brand-new top-level category's type
  (Income/Expense) is inferred from the row's own amount sign; a new
  *subcategory* always inherits its existing parent's type instead,
  since a mismatched pair would only confuse reports for no benefit. An
  explicit `Category` column value takes priority over the existing
  payee-history guess (Phase 6) when both are available, since a file
  saying what something is is more authoritative than a guess from
  unrelated history.
- **Date format detection** (`_detect_date_format()`) scans the file's
  own date column for the first value where a day-value over 12 (or a
  4-digit first component) settles day-first vs. month-first vs. ISO
  unambiguously; if nothing in the whole file is ever unambiguous, it
  falls back to the viewer's own profile setting (D10). The Import
  form's new **Date format** field defaults to "Detect automatically" —
  picking a specific format there is the override the plan asked for,
  upfront in the same form rather than a separate preview step, and the
  success message always states which format was actually used so a
  wrong guess is visible immediately rather than silently misdating
  everything.
- Reused `regional.py`'s own `DATE_FORMAT_CHOICES`/`Preferences.date_input_formats`
  for the parsing formats instead of a second hardcoded list — the old
  `CSV_DATE_FORMATS` constant (day-first only, no month-first support at
  all) is gone; losing its 2-digit-year variants was an accepted, minor
  trade-off for having one canonical source of accepted date formats.
- Tests: 268 (19 new, `finance/test_phase9.py`). Verified against the
  real database: imported a small CSV with an Account and a
  `Parent:Child` Category column, confirmed the row landed in the named
  account with an auto-generated, correctly-nested category — then
  removed that smoke-test row and category afterward, since it wasn't
  meant to be kept.

---

## Phase 10 — Backup & Export

- [x] "Back up now" button — since the DB is a single SQLite file, this is
      just a timestamped copy into a `backups/` folder.
      Caveat: a plain file copy is only safe while SQLite runs in its default
      journal mode (as now). If WAL mode is ever enabled, back up with
      SQLite's backup API instead, or recent changes can be missing.
- [x] Automatic quarterly backup, checked at app launch only (**D8**).
- [x] Export: CSV (built-in, no new dependency) and XLSX (via `openpyxl`,
      one new dependency) — proposing transactions as the primary export
      target, with the same available for accounts/categories/payees if
      useful. Skipping a separate `.txt` format as redundant with CSV
      unless you want a tab-delimited variant specifically.

**Done 14/09/2026.** Notes from implementation:
- New `finance/backup.py` — pure standard library on purpose (no Django
  import), since `scripts/bootstrap.py` needs it *before* the rest of the
  app is necessarily importable and the launcher already does its own
  plain `shutil.copy2` backup today (`migrate_database()`, before a
  schema change). `backup_now(db_path, project_root, tag)` does the copy;
  `quarterly_backup_if_due(...)` is the D8 launch-time check, comparing
  today's date against the newest `db_quarterly_*.sqlite3` filename's own
  embedded timestamp (not the file's mtime, which a copy/restore could
  disturb) rather than keeping any separate "last backup" record.
  `scripts/bootstrap.py`'s `main()` calls it once, right after the login
  check, alongside the existing pre-migration backup.
- **Settings page** gets a new Backups card: "Back up now" (manual,
  calls `backup_now` directly) plus the 5 most recent backups (any tag —
  manual, quarterly, or the launcher's own pre-migration ones) with their
  file size, so you can see backups are actually happening without
  opening the folder.
- **Export** lives as CSV/XLSX buttons on each relevant list page rather
  than one separate Export page: Transactions (on the transaction list,
  exporting whatever the list is *currently filtered and sorted to* —
  factored the list's own filter logic into `_filtered_transactions()`,
  shared by both, so what you export always matches what you're looking
  at), Accounts, Categories (excludes the system-managed transfer
  categories — plumbing, not something you'd manage in a spreadsheet) and
  Payees (excludes the 3 system payees, same reasoning). All four share
  small `_csv_response()`/`_xlsx_response()` helpers.
- Skipped a separate `.txt` export as agreed (redundant with CSV).
- Tests: 281 (13 new, `finance/test_phase10.py`) — the backup helper's
  file-copying logic is tested directly against isolated temp
  directories (deterministic, no real filesystem impact); the Settings
  view's button is tested by patching `backup_now` itself, since the
  test database is SQLite's `:memory:` and has no file to actually copy.
  Verified against the real project: ran the quarterly check for real
  (nothing existed yet, so it created the project's first
  `db_quarterly_*.sqlite3`), and exercised all 8 export endpoints
  (4 data types × CSV/XLSX) against the real data successfully.

**Follow-up 14/09/2026** (same-day polish batch after first use):
- Backup filenames are now `backup_<tag>_2026-09-14_11-57-36.sqlite3` —
  readable at a glance, instead of `db_<tag>_20260914_115736.sqlite3`.
  Existing backups are **not** renamed; `_parse_stamp()` in
  `finance/backup.py` recognizes both naming schemes (falls back to file
  mtime for anything neither pattern matches, e.g. the old ad-hoc
  `db_before_phaseN_*` copies made by hand before migrations).
- Added a **Restore** button per backup row on Settings. Restoring is a
  confirm-then-act page (`backup_restore_confirm.html`, same visual
  language as a delete confirmation) since it overwrites the live
  database — it takes its own safety backup (tag `before_restore`) of
  whatever's currently there first, so a wrong pick is itself
  recoverable the same way. New `finance/backup.py` functions:
  `list_backups()` (shared by the Settings list and the quarterly-due
  check) and `restore_from()`.
- Settings layout: removed the "Good to know" aside (folded its 3 lines
  into the preferences card instead) and swapped the column widths so
  Exchange rates + Backups — the content-heavier cards — sit in the
  wider right-hand column rather than being squeezed under a short form
  on the left.
- Export changed from two buttons (CSV / XLSX) to one "Export" button
  with a dropdown, on all four list pages — same choice, less visual
  noise. New shared include `includes/export_dropdown.html`.
- Categories/Payees export is now a **full** export: system-managed rows
  (transfer categories, system payees) are included rather than left
  out, each marked with a new "System-managed" column, so "export
  everything" actually means everything.
- Payees list: the Usage column is now sortable (ascending first click),
  for finding rarely-used payees worth deleting.
- Import page: rewrote the dense inline help text under the File field
  into a short one-liner, and moved the fuller explanation (recognized
  columns, auto-created categories, the Account-column override, when to
  touch Date format) into a "Stashy tips" panel on the right — the same
  two-column layout every other form in the app already uses.
- Tests: 289 total (added restore-flow tests, both naming-scheme
  parsing, full-export assertions, and payee usage-sort).

---

## Phase 11 — Reports

Originally scoped as "small additions only" (savings rate + confirming
transfers stay excluded), then expanded into a full redesign once the
user picked the actual report set they wanted.

- [x] Add a savings-rate percentage — (income − expense) / income — to
      the income/expense report.
- [x] Exclude transfer-type categories/transactions from all Income/
      Expense reports (structurally true since `CategoryType.TRANSFER`
      exists, from Phase 3 onward — applied consistently to every new
      report function via `excluding_transfers()`).
- [x] Redesigned the report set to exactly six, two per row: Spending by
      category, Current spending by category vs. average, Income &
      expense by category (savings rate lives here), Income & expense
      by category vs. average, Net worth, Bills & Utilities. Dropped the
      old "income vs. expense over time" chart — its role is covered by
      the by-category pair instead.
- [x] Each report card gets an "Expand" button: hides every other
      report and reveals a data table below that one chart; one "Back
      to all reports" button at the top undoes it.
- [x] "Showing X to Y, bucketed Z" moved to sit under the filter form
      (matching the transaction list), with dates through `|sdate` now
      (previously unformatted — a pre-existing bug this touched anyway).
- [x] Short explanatory text under Date range and Interval, since
      Interval only affects the two "vs. average" reports (which ignore
      the selected date range entirely, comparing the current period to
      trailing history instead) while Date range affects everything
      else — worth spelling out since it's not obvious from the form.

**Done 14/09/2026.** Notes from implementation:
- New `finance/reporting.py` functions: `category_totals()` (plain
  per-category totals; expense-only magnitudes for the spending chart,
  signed for income+expense), `category_totals_vs_average()` (per
  category: current interval-bucket total vs. the average of the
  trailing 12 same-length buckets — periods with no activity for that
  category don't pull its average toward zero, mirroring how the old
  single current-vs-average metric already worked), `current_interval_
  start()` (moved here from `views.py`, now shared), and
  `bills_and_utilities_totals()` (looks up a top-level category named
  exactly "Bills & Utilities", case-insensitive; returns `None` if it
  doesn't exist rather than guessing at one from a keyword match).
- **The "Bills & Utilities" report needs that exact category to exist**
  — there's no "this is a bill" flag anywhere else in the data model. If
  it's missing, the report card shows a message and a link to create it
  instead of an empty chart. The user's longer-term idea (Stashy
  offering a small set of starter categories, Bills & Utilities among
  them, instead of starting from nothing) became Phase 12.
- **Account filtering only applies where it always did** — Spending by
  category and Income & expense by category (both are "totals for the
  selected range", same as the account-filtered charts before this
  redesign). The two "vs. average" reports and Net worth compare against
  trailing history / all accounts' full history respectively, same as
  the pre-redesign current-vs-average metric and net worth chart — an
  account filter doesn't have an obvious meaning there without also
  filtering the historical side, which the UI doesn't ask for.
- **Expand/Back is pure client-side JS** (`finance/js/reports.js`) —
  chart data was already being sent to the browser for Chart.js, and the
  Decimal-accurate table rows are rendered server-side (via `|moneyc`,
  same as everywhere else) and simply hidden by default, so switching
  views needs no extra request. Chart JSON now travels via Django's
  `|json_script` filter (matching the dashboard's existing pattern)
  instead of the old `{{ x|safe }}` inline-script approach, which is the
  safer way to embed user-editable text (category names) in a script
  tag.
- Tests: 306 total (17 new in `finance/test_phase11.py`) — reports had
  no dedicated test file before this phase. Covers the new reporting.py
  functions directly (signed vs. expense-only totals, trailing-average
  math, transfer exclusion, account filtering, Bills & Utilities
  matching/missing/subcategories) and the view's rendering (savings
  rate, the missing-category message, per-report chart presence).
  Verified against the real project via `manage.py shell` + `Client`:
  all six charts and the expand buttons render, the summary line reads
  "Showing 01/10/2025 to 14/09/2026, bucketed month." in the new
  location with correctly formatted dates, and the real database's
  existing "Bills & Utilities" category (from the original
  `test_data.TXT` import) renders its chart rather than the missing
  message.

**Follow-up 14/09/2026** (same-day polish/expansion batch after first use):
- [x] Expand became a toggle on the same button ("Expand" ⇄ "Contract")
      instead of a separate "Back to all reports" button elsewhere.
- [x] Every report's table is now exportable (CSV/XLSX), via the same
      Export-dropdown pattern used elsewhere, scoped to that one
      report's current filters.
- [x] Both "vs. average" tables gained a Difference column
      (current − average).
- [x] Removed the explanatory text under Date range/Interval added
      earlier that day (see previous entry) - reverted per follow-up
      feedback.
- [x] Interval relabeled "Average interval" (it only sizes the
      trailing-average window, never the selected date range).
- [x] New "Subtotal tables by" filter (week/month/quarter/half-year/
      year) pivots the three "totals for the range" tables (Spending by
      category, Income & expense by category, Bills & Utilities) into
      one column per period plus a row-level Total column.
- [x] Category rows in every table now follow the category list page's
      own grouping/ordering exactly (`grouped_categories()`: Income
      group then Expense group, a parent immediately followed by its
      own subcategories) with a parent row rolled up to include its
      subcategories' amounts - not just sorted by amount any more.
- [x] Income & expense by category: Income group listed before Expense:
      a "Total" stat (income − expense) inserted between Expense and
      Savings rate; a second savings-rate figure showing the trailing
      average alongside the current one.
- [x] Net worth: rows are now individual accounts, columns are the
      Subtotal period (defaulting to month if Subtotal is off), with a
      Total row summing every account per period - the single combined
      line the old chart showed. No Total *column*, since summing a
      balance across time isn't a meaningful number, unlike a flow.
- [x] Transaction list gained a running "Total" in its summary line.

**Done 14/09/2026.** Notes from this batch:
- New `finance/reporting.py` functions: `period_bucket()`/
  `_period_columns()` (plain Python, not a DB `TruncXxx` - half-year has
  no Django equivalent, and doing every granularity the same way keeps
  the table pivot and Net worth's columns in sync), `category_report_
  rows()` (the pivot/hierarchy table builder, shared by Spending,
  Income & expense, and Bills & Utilities), `category_vs_average_
  report_rows()` (hierarchy + Difference column, kept separate from the
  existing flat `category_totals_vs_average()` so the chart's
  amount-sorted order didn't need to change), `net_worth_by_account()`,
  `income_and_spending_vs_average()`.
- **Found and fixed a real display bug while writing tests for this**:
  `Sum()` aggregates on SQLite can come back with far more decimal
  places than the underlying amounts actually have (e.g.
  `Decimal("-132.370000000000")` for a sum that's really -132.37) -
  invisible through `|moneyc`'s own rounding, but showed up as ugly
  trailing zeros in the new raw CSV exports. Fixed at the source with a
  new `money.to_cent()` helper applied to every aggregate result in
  `reporting.py`, not just patched at the export boundary.
- **Charts stayed simple on purpose** - only the tables got the
  category-hierarchy/pivot treatment; the charts keep using the
  original flat, amount-sorted functions unchanged, since a chart reads
  better ranked by size than grouped alphabetically.
- Tests: 331 total (25 new in `finance/test_phase11_follow_up.py`) -
  the abs-value bug above was actually caught by
  `test_parent_rolls_up_its_subcategory_total`, which is exactly why
  this batch got dedicated tests for the new pivot/hierarchy logic
  rather than relying on the page rendering without errors.

**11.1 follow-up 14/09/2026** (same-day, after the first two follow-up rounds):
- [x] Transaction list's Total moved to the right edge of its summary
      row instead of trailing after the count/date-range text.
- [x] Filter form: Date range / Average interval / Subtotal / Account /
      Apply on one row; Start/End on their own row directly below Date
      range.
- [x] Spending by category **chart** now shows one bar per main
      category only (subcategories rolled up into their parent) -
      the table is unaffected and still shows the full hierarchy.
      `category_totals_top_level()`.
- [x] Every report table: a parent category's name/values are styled
      differently from its subcategories' (`.category-main`/
      `.category-sub`, reused from the category list page, plus a new
      `.report-value-sub` for a lighter shade on child values) - and
      subcategories can be collapsed/expanded via a chevron toggle
      (shared JS, works the same way on Net worth's account groups).
- [x] Income & expense charts (plain and vs.-average) changed from
      horizontal bars to vertical columns. The plain one also rolls up
      to main categories only, and gains three extra columns: Total
      income, Total expense, Total.
- [x] Net worth's table now groups accounts the way the accounts page
      itself does (Physical Liquid / Digital Liquid / Illiquid /
      Inactive) - each group is a bold, collapsible rollup row (playing
      the "parent" role categories have), its accounts collapsible
      underneath. No change to the Net worth chart itself.
- [x] Bills & Utilities chart changed from a per-subcategory bar to a
      monthly spending-trend line, matching Net worth's chart style -
      the table (still per-subcategory, with Subtotal support) is
      unaffected. `bills_and_utilities_series()`.

**Done 14/09/2026.** New `finance/reporting.py` functions:
`category_totals_top_level()` (chart-only rollup to main categories),
`bills_and_utilities_series()` (monthly flow, not a running balance -
there's nothing to accumulate for a spending line the way Net worth
accumulates a balance). `category_report_rows()` and
`category_vs_average_report_rows()` rows now carry `id`/`parent_id`/
`has_children`, needed for the collapse toggle to target the right
rows. `net_worth_by_account()` restructured around `grouped_accounts()`
groups instead of a flat account list. Tests: 340 total (8 new in
`finance/test_phase11_1.py`, plus updates to the net-worth tests in
`test_phase11_follow_up.py` for the new grouped shape). Verified
against the real project: collapsible toggles and the parent/child
classes render, the income & expense chart's embedded JSON includes
the three Total entries, and the net worth export shows group rollup
rows above their own indented accounts.

---

## Phase 12 — Starter categories, multi-stash login

Starter categories done 14/09/2026; multi-stash login done 15/09/2026 —
first restart-based, then rewritten the same day to switch live (see
the revision note at the end of this phase).

- [x] Offer a small set of pre-saved main categories (Bills & Utilities
      among them) instead of starting from a completely empty category
      list. **Not hardcoded** - the user created `basic_categories.txt`
      at the project root themselves (one name per line, editable
      without touching code, per their explicit request) and it's read
      at runtime by `finance/starter_categories.py`. A name containing
      "income" (case-insensitive) becomes an Income category,
      everything else Expense - simple enough to infer from the name
      itself rather than needing a second column in the file. Mechanism
      chosen: an explicit "Add starter categories" button on the
      Category list page (not an automatic first-run seed) - safer for
      a project whose database already holds real data most of the
      time, and re-runnable any time the user adds a line to the file.
      Existing categories (matched by name, case-insensitive) are
      skipped, so clicking it twice creates nothing the second time.
      Wrapped in `audit.context.system_action(...)`, matching how every
      other automatic/bulk write in this app is attributed. Verified
      against the real project: of the file's 26 names, 24 already
      existed from the original `test_data.TXT` import; clicking the
      button created exactly the 5 missing ones (Education, Fees &
      Taxes, Kids, Pets, Smoking), all correctly typed as Expense, and
      a second click correctly reported nothing left to add. Tests: 10
      new in `finance/test_phase12.py` (full suite now 350).
- [x] **Multi-"stash" login**, scoped 14/09/2026, **built and shipped
      15/09/2026**. Each "stash" is its own self-contained SQLite
      database plus its own single superuser - not multiple Django
      users sharing one database, separate installations living side
      by side.

      **Layout:** `stashes/<name>/db.sqlite3`, with that stash's own
      `backups/` subfolder next to it (so Settings only ever lists that
      stash's own backups) - filenames drop the `<username>` prefix
      floated earlier, since the folder itself already scopes them;
      `backup_manual_2026-09-14_12-15-25.sqlite3` inside `stashes/
      <name>/backups/` is unambiguous on its own.

      *(Superseded 15/09/2026 - switching is now live, see the revision
      note at the end of this phase.)*
      **Switching is restart-based, not live** (user's choice over a
      live in-process DB switch, which Django isn't well-suited to
      anyway): picking a stash and entering its PIN relaunches the app
      pointed at that stash's `db.sqlite3`, the same way `Stashy.bat` /
      `scripts/bootstrap.py` already start it up. A logout returns to
      the picker screen.

      **The picker screen can't query for stashes** - at that point no
      database has been chosen yet, so "which stashes exist" comes from
      scanning `stashes/` for subdirectories, not a User query. A
      stash's existence *is* its folder existing. The Name field is a
      dropdown of those folder names; PIN is a 4-digit field next to it.

      **Creating a stash:** a "Create new stash" button/link on the
      picker prompts for Name + a 4-digit PIN (entered twice), then:
      makes `stashes/<name>/`, runs migrations to build a fresh
      `db.sqlite3` there, creates the one superuser with that PIN as
      its (hashed, normal Django auth) password. Django's default
      password validators (min length, common-password checks) need
      swapping for a 4-digits-only validator on this one form - they'd
      otherwise reject every PIN outright.

      **Forgotten PIN — decided 14/09/2026:** a one-time recovery code
      (e.g. `K7QM-4XPD-9RTA`), shown once at stash-creation time with an
      explicit "write this down, it won't be shown again" prompt, never
      stored in the clear (hashed the same way a password is). The
      login screen gets a "Forgot PIN?" link: enter the recovery code,
      set a new PIN. Wrong recovery-code attempts count toward the same
      lockout as wrong PINs. Last resort if the code is also lost: a
      `manage.py reset_pin <name>` command run by hand - available to
      anyone who already has file access to the stash, so it adds no
      new exposure.

      **Security note for the record, given directly to the user:**
      reasonable for this app's actual threat model - it only binds to
      `127.0.0.1` (`scripts/bootstrap.py`), the only outbound network
      call is fetching exchange rates, and the realistic risk is only
      ever someone else with a login on the same physical machine - but
      only paired with a lockout/backoff after a handful of wrong PINs,
      since 4 digits is 10,000 combinations and Django's login view has
      no rate-limiting of its own. **The PIN locks the app, not the
      data** - each stash is a plain SQLite file, readable by anyone
      with OS-level access to it in any SQLite viewer, so this is
      access control, not encryption. Actual file-level protection
      (BitLocker, a Windows login of the user's own, an encrypted
      database) is a separate, bigger decision the user hasn't asked
      for and this feature shouldn't be assumed to cover.
      Lockout specifics (attempt count, cooldown length) not pinned to
      exact numbers by the user - a reasonable default (something like
      5 wrong attempts, then a short cooldown) is fine to pick at
      build time without checking back first.

      **The current database is test data and stays that way** (user's
      own words, 2026-09-14) - a real stash gets created "at some point"
      before real data goes in, separately from this feature. When this
      ships, the existing `db.sqlite3` moves into `stashes/test/` (a
      PIN gets set for it once, then it's just another entry in the
      dropdown, named "test") rather than being folded into whatever
      the user's real first stash ends up being - keeps the throwaway
      data clearly labeled as throwaway, and the user deletes that
      stash folder whenever they're done with it.

**Done 15/09/2026.** Notes from implementation:
- New `finance/stashes.py` - the only module that reads/writes
  `current_stash.txt` (also read once by `stashy/settings.py`, at
  import time, to compute `DATABASES['default']['NAME']` and the new
  `CURRENT_STASH` setting) or touches `stashes/<name>/`. Deliberately
  never imports Django's ORM at module level for its *reading*
  functions (`list_stashes()` is a folder scan), but stash *creation*
  needs to build a real schema in a file this process isn't normally
  connected to - `create_stash()` and `reset_pin_directly()` do that by
  registering a temporary extra database alias (copying `default`'s own
  settings dict as a template, so it doesn't skip keys Django fills in
  automatically - a first attempt that hand-wrote the dict instead hit
  `KeyError: 'TIME_ZONE'` deep inside the migration machinery), running
  `migrate` against just that alias, then dropping it again.
- **Nothing in the app is reachable without a stash chosen first** -
  `finance.middleware.RequireStashMiddleware` redirects every URL
  except the picker's own, `/static/`, and `/admin/` to `/stashes/`
  whenever `settings.CURRENT_STASH` is empty.
- **The picker can't query for stashes** (no database is connected to
  any particular one yet) - `stashes.list_stashes()` scans `stashes/`
  for subdirectories holding a `db.sqlite3`; a stash's existence *is*
  its folder existing.
- *(Superseded - see the revision note below.)*
  **Switching is restart-based**, exactly as decided: picking a stash
  (or creating one) writes its name to `current_stash.txt` and shows a
  "close this window and run Stashy.bat again" page - no attempt to
  make the running process relaunch itself. `scripts/bootstrap.py`
  computes the active stash's db/backups paths the same way
  `stashy/settings.py` does (duplicated on purpose - it runs several
  steps, like installing requirements, before Django can be imported at
  all) and no longer runs `createsuperuser` itself; that's the picker's
  job now.
- **Login asks for a PIN only** - `StashLoginView` (in the new
  `finance/auth_views.py`) subclasses Django's own `LoginView`, fixes
  `username` to the current stash's name (hidden field, never typed),
  and adds the lockout Django's stock view doesn't provide
  (`finance.stashes.LOCKOUT_THRESHOLD` = 5 wrong attempts, then a
  5-minute cooldown - stored on `Profile`, which gained
  `recovery_code_hash`/`failed_pin_attempts`/`locked_until` fields,
  migration `0013_profile_stash_fields`). A wrong PIN, a wrong recovery
  code, and a lockout that isn't extended by continuing to guess while
  it's active are all the same counter.
- **Forgot PIN** (`finance/auth_views.py::forgot_pin`) checks the
  recovery code, sets a new PIN, and immediately issues a **new**
  recovery code (shown once, same as at creation) - the old one stops
  working the moment it's used, so it's a single-use credential, not a
  permanent backdoor.
- **Backups became per-stash** as a consequence, not a separate task -
  `finance/views.py`'s `_stash_backup_root()` now points "Back up now",
  the recent-backups list, and Restore at `stashes/<name>/backups/`
  instead of the shared top-level `backups/`; falls back to `BASE_DIR`
  under `manage.py test` (see testing note below) or before any stash
  has ever been chosen.
- **Change PIN** added to Settings (doesn't need the recovery code -
  that's reserved for when the PIN itself is what's forgotten) and
  `manage.py reset_pin <name> [--pin ####]` as the documented last
  resort if the recovery code is lost too - it needs no confirmation
  prompt of its own, since running it already requires file-level
  access to the stash.
- **A real test-isolation trap, found and fixed while verifying this**:
  the existing test suite logs in via `Client.force_login()` and never
  goes through the picker, so `RequireStashMiddleware` would otherwise
  redirect every single existing test to `/stashes/`. Fixed with a new
  `settings.TESTING` flag (`'test' in sys.argv`) the middleware checks
  first. That flag alone wasn't quite enough, though: `_stash_backup_
  root()` and `StashLoginView` both also read real state
  (`current_stash.txt`, `settings.CURRENT_STASH`) that's true or false
  depending on what's actually on the machine running the suite -
  after this phase's own "adopt the real database as the test stash"
  step below made `current_stash.txt` real for the first time, several
  previously-passing tests broke because backups suddenly looked for
  files in `stashes/test/backups/` instead of the top-level `backups/`
  the tests' own fixtures wrote to. Both were fixed to treat `TESTING`
  as authoritative over whatever's really on disk, not just gate on it
  once at the top.
- **`create_stash()`/`reset_pin_directly()` aren't covered by
  `finance/test_phase12_stash_login.py`** - Django's test framework
  blocks connections to any database alias not declared in
  `settings.DATABASES` up front, and `databases = "__all__"` on a
  TestCase only covers aliases already known there, not ones a test
  registers dynamically at runtime. There's no supported way around
  that from inside TestCase. Verified for real instead (not under the
  test runner): created a stash against an isolated temp directory,
  confirmed the folder/db/backups layout, that the superuser can
  authenticate with the PIN, that the recovery code's hash matches,
  duplicate-name and unknown-stash rejection, and that
  `reset_pin_directly()` correctly changes the password - all via
  `manage.py shell` directly. Also exercised the actual HTTP views end
  to end in a fresh process each time (mirroring a real restart):
  created a throwaway stash through the picker, restarted, logged in
  and confirmed the app connected to that stash's own database,
  triggered a real lockout at 5 wrong PINs (even the correct PIN then
  fails until the cooldown), reset the PIN with the real recovery code
  and confirmed the old code stopped working, changed the PIN from
  Settings, and reset it again via the CLI command - then deleted the
  throwaway stash.
- **The real project's own database was adopted as the "test" stash**,
  as agreed - copied (original left in place, untouched, as a safety
  net - also backed up separately first) into `stashes/test/db.sqlite3`
  and migrated there; the existing superuser was
  renamed to `test` to match the stash-name convention the login screen
  relies on, given a PIN, and a recovery code was generated - both
  handed to the user directly in chat (not written here), since this
  file isn't the place for real credentials. `current_stash.txt` now
  says `test`, so the next normal launch goes straight to this stash's
  login screen with all 783 existing transactions intact (verified).
  **A Stashy instance was already running (port 8001, started before
  this session's changes) while this migration happened** - its own
  already-loaded process kept using the original top-level `db.sqlite3`
  untouched throughout (Python doesn't hot-reload settings.py), so nothing
  it does before being closed and restarted is affected either way; it
  needs restarting to pick up *any* of today's changes regardless, the
  same as after any other code change.
- Tests: 364 total (14 new in `finance/test_phase12_stash_login.py`) -
  lockout math, the recovery-code reset flow, name/PIN validation, the
  recovery code's format, and the require-a-stash middleware, all
  against the normal test database.

**Revised 15/09/2026 — stash switching became live (no restart).** The
same day it shipped, the restart-based switching described above was
rewritten so a stash can be picked, created or switched without closing
`Stashy.bat`. The bullets marked *Superseded* above describe the first
version; this is what's true now:
- Nothing reads `current_stash.txt` any more. `DATABASES['default']` is
  an inert placeholder (`_picker.sqlite3`) that never holds app data.
- Every stash is registered as its own Django connection alias, named
  after the stash (`finance.stashes.ensure_registered()`) - at process
  start for stashes that already exist, and again whenever a session
  picks or creates one.
- `finance.middleware.StashSelectionMiddleware` reads the chosen stash
  from the session and sets a per-thread alias that
  `finance.db_router.StashRouter` sends every query to; it's cleared after
  each request, so a worker thread never carries one session's stash into
  another request.
- Sessions are signed cookies (`SESSION_ENGINE`), since the session has to
  exist before any stash database has been chosen.
- `RequireStashMiddleware` checks the session, not a setting. **Switch
  stash** (`auth_views.switch_stash`) logs out and returns to the picker;
  the old "close this window and run Stashy.bat again" page is gone.
- `scripts/bootstrap.py` has no single "current" stash any more: it
  migrates the placeholder database plus **every** stash (backing each up
  into its own `backups/` first) and runs the quarterly backup check for
  every stash.
- `create_stash()` suspends the audit signals while it builds a new
  database - otherwise they'd write into the requesting session's own
  stash.
- **Resolves the parked "PIN or password-only login" idea** (removed from
  Parked ideas): login asks for the PIN only, built exactly the way that
  note recommended - Django's own `User` kept underneath, plus lockout and
  a recovery code.
- This rewrite wasn't recorded here or in DEVLOG.md at the time; both
  were caught up retroactively on 15/09/2026.

---

## Phase 12.1 — follow-ups 15/09/2026

Two same-day fix batches after using Phases 11–12. Both were built and
tested at the time but only written into this plan afterwards
(15/09/2026).

**Batch 1** (tests in `finance/test_phase13.py` - named before this
phase's number existed; Phase 13's own tests go in
`test_phase13_reports.py`):
- [x] An account can't be made inactive while its balance isn't zero, and
      a new account can't start inactive with a non-zero opening balance.
- [x] Entering a transaction on an inactive account asks first ("save
      anyway and make it active again?") instead of silently blocking it
      or silently reactivating the account.
- [x] Payee list's category filter ordered like every other category
      dropdown.
- [x] Transactions list Total shows a currency symbol.
- [x] Removed the helper text under Opening balance (new account) and
      Currency (edit account).
- [x] Reports: the Expand button relabels to "Collapse" (was "Contract").

**Batch 2:**
- [x] Reports: Export shows only on the expanded report. The earlier
      version never actually hid it - Bootstrap's `d-inline-block`
      (`display: inline-block !important`) overrode the `hidden`
      attribute.
- [x] Reports: Net worth and Bills & Utilities tables start fully
      expanded; the other reports' tables stay collapsed.
- [x] Reports: "Last 9 periods" added to Average interval (3/6/9/12).
- [x] Dashboard: "New transaction" button removed.
- [x] Account page register: Delete button next to Edit on every
      transaction.
- [x] Every new account gets an opening balance transaction - 0 dated
      today when the fields are left blank.
- [x] Every category dropdown lists Income → Expense → Transfer, each main
      category directly followed by its own subcategories
      (`models.categories_alphabetical()`).
- [x] Transactions list category filter: fixed its order (it built its own
      query that bypassed the shared helper) and turned it into a tick-box
      dropdown - looks like a normal closed dropdown, allows several
      categories at once, includes transfer categories, and ticking a main
      category ticks its subcategories
      (`finance/static/finance/js/category-filter.js`).
- [x] Removed the unused `MAILERS` setting from `settings.py` (from the
      backlog; Django 6.1 doesn't recognize it and Stashy sends no email).

**Done 15/09/2026.** Full suite: 375 tests passing. Reasoning for each
change is in DEVLOG.md's 15/09/2026 entries.

---

## Phase 13 — Reports redesign

Planned 15/09/2026 over several rounds with the user. Decisions are the
user's unless marked as Claude's call. **Done 15/09/2026.**

### Decisions
- **R1 — What counts as spending and income, everywhere** (Reports plus
  the dashboard's tiles and spending donut). Transactions in **expense**
  categories are spending; transactions in **income** categories are
  income - **the category decides, never the sign of the amount** (e.g.
  anything under Sells is income, anything under Clothing & Accessories
  is spending). Expenses are expected to be negative; one that isn't just
  nets against its own category - not worth special handling. Transfers
  stay excluded. **Uncategorized transactions are excluded** from every
  report (Category stays optional, since some imports can't supply one);
  when any exist in the selected range, a notice at the top of Reports
  says they aren't included because they're neither income nor expense,
  and links to them in Transactions.
  *Revised 15/09/2026:* an earlier draft counted positive amounts in
  expense categories as income; the user corrected it - the category's
  type alone decides.
- **R2 — Charts show main categories only**, never subcategories (tables
  keep them). If a chart isn't useful at that level, the categories
  themselves should change.
- **R3 — Reports defaults:** This month · no subtotal · Last 3 periods ·
  All accounts. Every page keeps its own defaults (Transactions keeps
  "This year"); never change a page's defaults without agreeing first.
- **R4 — How "vs. average" works.** A calendar Date range is compared with
  the average of **whole earlier periods of the same kind**: "This month"
  with Last 3 = the average of the 3 previous full months (the current
  month may still be partial - intended, it's used to predict how much
  more will be spent). A quarter → earlier quarters; year to date / this
  year / last year → earlier full years; Last 12 months → earlier 12-month
  blocks; Custom range → earlier windows of the same length (today's
  behavior); Earliest to date → no average, since nothing comes before it.
  - **Periods with no activity count as zero**, for categories and totals
    alike (Asset Purchases: 1 800.00 once in the last 6 months → average
    300.00). Fixes today's inconsistency, where only the per-category
    averages skipped empty periods.
  - **Periods before the data begins don't count**, whatever Average
    interval says: a period that ends before the first transaction (within
    the Account filter) is left out. If fewer periods exist than asked
    for, the average simply uses the ones there are (data for only 2
    months with Last 3 → the average of those 2), and the report's heading
    names the real count ("vs. average of last 2 months") rather than
    adding a warning; with none, the vs-average reports
    say there's no earlier data to compare with.
  **Refined 15-16/09/2026** (see "Second"/"Third"/"Fourth round" below):
  the code hadn't actually implemented calendar-aligned periods until the
  third round (it used same-length windows for every range, a real bug);
  the "report's heading names the real count" line above was dropped
  instead (the user didn't want that text at all); "with none" was
  widened from "zero earlier periods" to "fewer than two", since one
  earlier period isn't an average either; and a *category* with zero
  activity in every earlier period now gets no average of its own (not
  the report-wide zero-fill this box describes, which still applies to a
  category that has *some* earlier activity).
- **R5 — Subtotal limit: 36 periods** (agreed 15/09/2026). Past it, the
  table and chart show a short notice instead, in the same style as the
  app's other messages ("Too many wrong PINs. Try again in about…"),
  pointing at the next size up - no count:
  - "Too many weeks to show here. Try subtotaling by month instead."
  - months → "…by quarter instead."; quarters → "…by half-year
    instead."; half-years → "…by year instead."
  - years → "Too many years to show here. Try a shorter date range."
  Not mentioned in the help text.
- **R6 — Colors in split columns:** the **5 biggest categories in the
  selected range** get distinct bright colors, so colors follow the range
  (intended - a one-off Asset Purchase shouldn't hold a color forever);
  every other category shares one muted shade, with thin gaps so each
  piece's edges stay visible. No legend, no stripes/dots/patterns -
  hovering shows name, amount and share.
  **Superseded twice, 15-16/09/2026, after seeing it rendered** - see
  "Second round" and "Third round" below. Final rule: no category colors
  at all in Reports - green/red/blue for income/expense/net, split by
  visible dark-brown dividing lines. The top-5 palette survives only for
  charts where each series needs its own identity (Watched expenses'
  separate lines; the dashboard donut, untouched by this phase).
- **R7 — Orientation** (Claude's call; the user left it open): a chart of
  categories uses horizontal bars (26 main categories with long names; rows
  grow taller when expanded), a chart of dates uses vertical columns.
- **R8 — Bills & Utilities becomes a Settings choice** ("Watched
  expenses"), not a report filter.

### Filters and page
- [x] Defaults per R3.
- [x] Filter order: Date range · Subtotal by · Average interval · Account.
      "Subtotal tables by" renamed **"Subtotal by"** (it now drives some
      charts too).
- [x] New Date range option **"Earliest to date"** (from the first
      transaction, within the Account filter).
- [x] A `?` next to each filter opens a short explanation (Bootstrap
      popover - already bundled, works offline, click works on touch):
      - Date range: "Which dates the reports cover. Affects every report.
        For the vs-average reports, this is the "current" period."
      - Subtotal by: "Splits tables into one column per period plus a
        Total, and switches the Spending, Income and Income & expense
        charts to one column per period. Doesn't affect the vs-average
        reports."
      - Average interval (user's wording): "How many earlier periods
        before your Date range, the average is built from. Affects the
        three vs-average reports and the comparison stats."
      - Account: "Limits reports to one account. Affects every report
        except Net worth, which always includes all accounts."
- [x] Subtotal limit per R5.
- [x] **Invalid filters** (moved from the backlog): show a clear warning,
      and make the dropdowns show the defaults actually in use rather
      than the rejected choices.
- [x] Uncategorized notice per R1.
- [x] Spending/income per R1 in `reporting.py` (replacing today's
      "negative amount = spending" filter), applied to the dashboard too.
- [x] Average periods per R4.

### Every report card
- [x] Expanded charts size themselves from their data (a set height per
      bar row, a minimum width per column), never skip labels, and scroll
      sideways only when columns still don't fit. Collapsed cards keep a
      small preview.
- [x] **Show all / Hide all** button next to Export (expanded view): opens
      or closes every subcategory row in that report's table (on Net
      worth, every group's accounts).
- [x] Card markup moves into one shared `report_card.html` include.
- [x] Page order, two per row: Spending by category | Spending vs average ·
      Income by category | Income vs average · Income & expense | Income &
      expense vs average · Net worth | Watched expenses.

### The reports
- [x] **1–2. Spending by category / Income by category** (Income is new -
      the same report for income categories, with its own export). No
      subtotal: horizontal bars, one per main category, biggest first.
      With a subtotal: vertical columns, one per period, each split by
      category with the R6 colors. Tables work as today.
- [x] **3–4. Spending vs average / Income vs average** (Income is new,
      with its own export). Horizontal bars for the current period, each
      with a thick, dark average marker that's easy to see. Subtotal
      doesn't apply.
- [x] **5. Income & expense.** Vertical columns per Subtotal period (no
      subtotal = one Income/Expense pair for the whole range), both
      columns split by category with the R6 colors. Stat row: "Total"
      renamed **"Net"**; the small "avg" savings-rate hint is removed
      (report 6 owns comparisons now).
- [x] **6. Income & expense vs average.** Three columns - Income (split
      by category), Expense, Net - each with an average marker. Between
      the chart and the table (where report 5 has its stats): Income,
      Expense, Net and Savings rate, each as current / average /
      difference, with ▲▼ and a sign so it's never color alone (green
      means good, so spending less is green). The table gains Income
      total, Expense total and Net rows.
- [x] **7. Net worth.** Chart and table flow unchanged. A single data point
      (e.g. This month) shows a visible dot plus a flat line across the
      chart. **Convert every account to the main currency** using the
      latest known rate, the way the dashboard and accounts page already
      do (D2), naming any account with no known rate (moved from the
      backlog). **Every other report converts the same way** (agreed
      15/09/2026) - today they also add different currencies together
      without converting.
- [x] **8. Watched expenses** (was Bills & Utilities). **Settings** gets a
      Title field and a Categories tick dropdown (expense categories only),
      reusing the Transactions filter's dropdown and its
      tick-the-subcategories behavior (moved into a shared include + JS).
      The migration pre-fills the existing "Bills & Utilities" category and
      its subcategories with that title, so nothing looks different on day
      one. The chart draws **one line per chosen category**; a single data
      point gets the same dot + flat line as Net worth. With nothing
      chosen, the card says "No categories chosen, please go to Settings
      to change that."

### Wrap-up
- [x] Chart colors checked for colorblind safety in light and dark mode
      (with a palette validator, not by eye).
- [x] Tests in a new `finance/test_phase13_reports.py`; update the
      existing report tests this changes (the income chart's three Total
      columns, "Bills & Utilities must exist", the Average interval
      default).
- [x] Every stash backed up before the Watched expenses migration
      (`Stashy.bat` already does this automatically).
- [x] DEVLOG.md entry; README's Reports paragraph updated.

**Done 15/09/2026.** Notes from implementation:
- `finance/reporting.py` rewritten around R1 (category type decides, via
  `Category.CategoryType` filters instead of `amount__gt/lt 0`) and
  currency conversion (`rates=` a `currency.RateTable`, threaded through
  every aggregation - `None` skips conversion, so every pre-Phase-13 test
  that never touched a second currency kept passing unchanged).
  `income_and_spending()`/`spending_tree()` (the dashboard's own tiles and
  donut) went through the same R1 rewrite, satisfying "no matter reports
  or dashboard" for free.
- **R4 in code:** `trimmed_average_periods(start, end, lookback, account)`
  computes the real trailing period list once per page load (earliest-date
  trimmed), and every vs.-average function (`category_totals_vs_average`,
  `category_vs_average_report_rows`, `income_and_spending_vs_average`)
  takes that list instead of a raw lookback count, averaging by
  `len(periods)` always (zero-filling), with `average=None` on every row
  when `periods` is empty ("no earlier data to compare with").
- **R5 in code:** `subtotal_overflow_message()` / `SUBTOTAL_LIMIT = 36`;
  `category_report_rows()` and `net_worth_by_account()` return an
  `"overflow"` key the template checks before rendering a chart/table.
- **R8:** `Profile.watched_expenses_title` +
  `watched_expenses_categories` (M2M); migration `0014` pre-fills from any
  existing "Bills & Utilities" category. Settings gained a "Watched
  expenses" card reusing `includes/category_checkbox_filter.html` (the
  Transactions category dropdown, extracted into a shared include +
  `category-filter.js`, parameterized by field name).
- **Found while wiring up R3:** the actual Reports defaults (This month,
  Last 3 periods) hadn't been applied anywhere yet - `resolve_range()`'s
  own fallback and `DateRangeFilterForm`'s field `initial=` were both
  still "ytd"/"12" from before Phase 13 was planned. Fixed in both places
  (`DEFAULT_RANGE_KEY`, `DEFAULT_LOOKBACK_PERIODS = 3`) before considering
  the phase done - this only affects Reports; Transactions keeps "This
  year" (its own default, untouched, per
  `feedback-page-defaults-need-agreement`).
- **Colors (R6):** the old `--stashy-chart-1..8` tokens (used by the
  dashboard donut too) failed the dataviz skill's lightness/chroma/CVD
  checks outright, and had no dark-mode values at all (dark mode silently
  reused the light ones). Replaced with the skill's own validated 8-hue
  categorical theme, stepped for both `#ffffff` (light) and `#1f1a17`
  (dark) - Stashy's real surfaces, not the skill's generic defaults -
  confirmed via a Python port of `validate_palette.js` (no Node on this
  machine). Slots 1-5 are R6's "top 5" bright colors, always assigned in
  that fixed order to the range's biggest categories by rank (not
  per-chart-instance) so a filter change never repaints who's who by
  accident; everything else shares `--stashy-chart-other` (=
  `--stashy-muted`). Validated on the *adjacent*-pairs gate, the correct
  one here (Chart.js stacked/grouped bars, not scatter/small-multiples)
  since categories always render in the same biggest-first rank order -
  a free improvement to the pre-existing donut chart too, since it reads
  the same tokens.
- **Chart shapes:** reports 1-2 switch between a horizontal-bar Chart.js
  dataset (no subtotal) and a stacked-column one (subtotal set) by reading
  a `data-subtotal` attribute off the canvas; report 5 draws two stacks
  (Income/Expense) side by side per period; report 6 places Income
  (stacked), Expense (stacked) and Net as three sparse datasets on a
  shared 3-slot x-axis. The "thick, dark average marker" on reports 3/4/6
  is a second, marker-only dataset (`type:'line', showLine:false,
  pointStyle:'line'`) overlaid on the same axis, not a second bar -
  Chart.js supports mixed dataset types natively, no plugin needed.
  Net worth's and Watched expenses' "single point → dot + flat line"
  duplicates the one point across both x-edges (`flatLineIfSingle()`),
  since Chart.js can't draw a line through a single x-position on its own.
- **Claude's call**, not spelled out in the plan text: report 6 also kept
  a per-category vs.-average table (the old `income_expense_vs_average_
  table`, which R6's own line "the table gains Income total, Expense
  total and Net rows" implied without fully specifying) - built from
  `category_vs_average_report_rows()` plus two synthetic "Income total"/
  "Expense total" rows from the same numbers the stat row shows, with the
  existing "Total" footer relabeled "Net" (a combined category's own
  current total already *is* net, since income is positive and expense
  negative) - avoids a redundant third "Net" row. The CSV/XLSX export for
  this report exports the plain per-category breakdown only (no synthetic
  summary rows) - a deliberate simplification, since the numbers are
  already on screen and duplicating the injection logic into the export
  path wasn't worth it for two rows.
- **Claude's call:** "Card markup moves into one shared `report_card.html`
  include" - only the header chrome (title, Show all/Hide all, Export,
  Expand) is actually identical across all 8 cards; the chart/table body
  differs enough per report (bar vs. stacked vs. line, one table vs. two,
  the stat rows) that unifying it too would need a lot of indirection for
  little real duplication removed. Shared as `includes/
  report_card_header.html`; each card's body stays in `reports.html`.
- Expanded charts size from their own data: a horizontal-bar chart grows
  taller (34px/row, `Chart.defaults.scales.category.ticks.autoSkip =
  false` so a label is never dropped); a column chart keeps a roomy fixed
  height and gets a minimum width per column (`.report-chart-inner`,
  60px/column) inside a `overflow-x: auto` wrapper, so it only scrolls
  sideways once that minimum genuinely doesn't fit.
  `Chart.defaults.maintainAspectRatio = false` had to become a real global
  default here (previously only set per-chart on the dashboard donut) -
  every report chart now lives in an explicitly-sized wrapper div instead
  of a bare `<canvas height="220">`, which Chart.js only fills correctly
  once told not to preserve its own aspect ratio.
- Verified: full suite green (408 tests, `finance` + `audit`) after every
  change in this phase, plus `manage.py check`. Not verified: an actual
  browser render (no visual/browser tooling available in this session) -
  the user should look over the real page after restarting `Stashy.bat`
  (which also applies migration `0014` to the "test" stash, prefilling
  Watched expenses from its real "Bills & Utilities" category, and runs
  `collectstatic` so the new CSS/JS actually ship) before calling this
  fully checked off.

**Bug found by the user right after this, same day:** two new includes
(`report_card_header.html`, `category_checkbox_filter.html`) opened with
a `{# ... #}` comment spanning several lines - Django's `{# #}` only
matches within one line, so a multi-line one falls through as literal
page text instead of being stripped. It showed up on every report card
and in the Watched expenses picker, which is exactly why the user's first
reaction was "scrap it and start over." Fixed (both shortened to one
line), plus the Watched expenses row was too wide (it sat in Settings'
wide column, unlike Transactions' narrow one) - narrowed to match. Added
`NoLeakedTemplateSyntaxTests` so this exact mistake fails a test next
time. None of the 408 tests written for this phase would have caught it -
see `DEVLOG.md`'s "Phase 13 bug" entry for why, and
`feedback-render-check-when-no-browser` for the standing lesson.

**Second round of fixes after the user used the real page (15/09/2026):**
- **Watched expenses showed "No categories chosen" even after choosing
  some** - the card checked whether anything was *spent* in the range,
  not whether categories were *chosen*; a range with no spending in those
  categories looked exactly like nothing chosen. Now checks the chosen
  categories; an empty range shows the chart and table at zero.
- **Subtotal never changed the Spending/Income charts** - the page never
  passed `subtotal` to the template, so every chart saw "no subtotal".
- **Net worth and Watched expenses charts now follow Subtotal** (month
  when none is chosen) - Net worth's chart plots its own table's Total row;
  Watched expenses buckets by the same periods, with the 36-period notice
  in place of the chart when that granularity is too fine for the range.
- **Report names** (user: "wrong report name"): Spending by category vs.
  average, Income by category vs. average, Income & expense by category,
  Income & expense by category vs. average - matching the two by-category
  reports the user didn't flag.
- **Vs.-average charts showed subcategories** - now rolled up to main
  categories (R2); their tables keep subcategories.
- **Average markers** are x-y dot markers instead of short lines, and the
  Current/Average legend is gone.
- **R6 revised (user, 15/09/2026):** the top-5 color scheme is **only for
  categories stacked inside one column** (Spending/Income with a subtotal,
  Income & expense, Income & expense vs. average). Separate bars get one
  color (Spending red, Income green); separate lines (Watched expenses) get
  one distinct color each. Categories outside the top 5 in a stack are now
  **striped** instead of one flat muted shade - the user's own call after
  seeing the flat version ("too blurry"), replacing the earlier
  "no stripes" decision.
- **Income & expense by category** gained a note under the chart: in each
  period, the left column is income and the right column is expense.

**Third round (15/09/2026), after the user looked again:**
- **R6 revised again - no category colors at all.** Stacked pieces are
  green (income), red (expense) or blue (net, new `--stashy-net` token),
  told apart by clear dark dividing lines. The top-5 palette and the stripes
  are gone ("by striped i meant one black, one white, not like this ...
  actually ditch the different colors"). Watched expenses lines keep one
  color each, since they're separate series, not pieces of a stack.
- **R4 actually implemented:** the averages had kept the pre-Phase-13
  behavior - earlier windows the same *length* as the Date range (This
  month on the 15th was compared with 15-day blocks). Now a calendar range
  compares with whole earlier periods of its kind, as R4 always said: This
  or Last month → previous full months, This quarter → earlier quarters,
  year to date/this year/last year → earlier full years, Last 12 months →
  earlier 12-month blocks, Custom → same-length windows.
- **No "No earlier data to compare with." text** - with nothing earlier,
  the average simply counts as 0 (user: "just make it work with what you
  have"). Earliest to date has nothing before it, so its average is 0.
- **Average dots at 0 were cut off** at the chart's edge - now drawn whole.
- **Titles:** "Current spending by category vs. average" and "Current
  income by category vs. average" (the user's choice); the "vs. average of
  last N months" line under the vs-average titles is removed.

**Fourth round (16/09/2026):**
- **The user renamed every report themselves** in `reports.html`
  ("Spending by Category", "Current Spending vs. Average Spending by
  Category", "Income/Expense by Category", "Net Worth", ...). Those titles
  are the user's own wording - keep them as they are.
- **An average of 0 was misleading.** A category with no earlier activity
  now has *no* average at all (None, no dot) instead of a dot at 0, which
  read as "spent nothing" - the user saw a whole chart of zero dots on
  Year to date. A category that does have earlier activity still averages
  over every earlier period, counting the empty ones as zero (R4).
- **Fewer than two earlier periods isn't an average:** all three
  vs-average reports show "No earlier data to compare with." in place of
  the report (chart, stats and table) - `has_average` in the view.
- **Help popovers** open on hover (and on focus, for keyboard and touch),
  only one at a time, and close on leaving or clicking elsewhere.
- **Lines and dots** (stacked-piece dividers and the average markers) are
  dark brown in light mode, pale in dark mode - `--stashy-chart-line`.
- **Show all/Hide all** appears only once a report is expanded (like
  Export) - it acts on the table, which isn't on screen before that - and
  only when that report actually has rows to open or close. Its label now
  follows the table's real state, so Net worth and Watched expenses, whose
  rows start open, read "Hide all" instead of offering to show what's
  already shown.

---

## Phase 14 — Admin, tags, audit log

Planned 15/09/2026 (split out of the earlier "Audit log rewrite" draft at
the user's request). **Not started.** Doesn't depend on Phase 13.

### Decisions
- **A1 — The admin is used by regular people**, not just a developer, so it
  only shows what's useful to them: **Users and Groups hidden, Profiles
  view-only.**
- **A2 — Tags are removed entirely.** Never used (0 tags in the test stash).
- **A3 — The audit log reads like a database audit log**, not a friendly
  summary ("4 fields" told you nothing). One row per event:
  - **INSERT** - one row naming the new record: `12 (Savings)`.
  - **UPDATE** - one row **per changed column**, with its old and new value.
  - **DELETE** - one row naming the removed record.
  - Raw column names (`category_id`) and raw values: ISO dates, exact
    decimals, `true`/`false`, stored choice keys (`debit_card`) - with the
    related record's name at that moment next to every id
    (`44 (Groceries)`).
  - Times in the user's own time zone, with milliseconds and the offset:
    `2026-09-15 15:06:48.930 +03:00`.
  - Every row also carries: table (`finance_transaction`), source (user or
    system), reason, request (`POST /transactions/781/edit/`) and an
    operation id shared by every row from the same click.
  ```
  id     time (+03:00)            op      table                row           column       old             new
  13001  2026-09-15 15:04:10.201  INSERT  finance_account      12 (Savings)
  13003  2026-09-15 15:06:48.930  UPDATE  finance_transaction  781 (Lidl)    amount       -12.50          -15.00
  13004  2026-09-15 15:06:48.930  UPDATE  finance_transaction  781 (Lidl)    category_id  44 (Groceries)  51 (Take-Outs)
  13005  2026-09-15 15:09:02.117  DELETE  finance_payee        160 (Lidll)
  ```
- **A4 — Created and deleted records keep their full values** inside that
  one INSERT/DELETE entry (Claude's call - the user asked for whatever is
  soundest): the list stays one row, but opening the entry shows every
  column, so a deleted transaction's amount, date and account are never
  lost for good.
- **A5 — Existing audit entries are cleared** (12 981 in the test stash,
  old format), so there's no code for displaying the old format.
- **A6 — PIN secrets stay out of the log:** a changed recovery code shows
  as `(hidden)`, never its hash. Wrong-PIN counters stay logged - the only
  record of someone guessing a PIN.
- Replaces the earlier Phase 14 draft (versioned entries, values formatted
  per viewer, an in-app History page, currency symbols in the log) and
  D10's "audit log formats values per viewer" consequence - see D10's
  revision note.

### Checklist
- [x] Admin: hide Users and Groups.
- [x] Admin: Profiles view-only (no add/change/delete), showing only the
      preferences - recovery-code hash and lockout fields hidden.
- [x] Remove tags: the `Tag` model and `Transaction.tags` (migration), the
      transaction form field, the admin, `scripts/load_test_data.py`, and
      the audit test that used them. Many-to-many logging stays (Phase
      13's Watched expenses categories use it): one UPDATE row per item
      added or removed.
- [x] New audit storage per A3 (operation, table, row id + name, column,
      old/new raw value + related names), full values for INSERT/DELETE
      per A4.
- [x] Signal handlers rewritten to store raw values (no `DD/MM/YYYY`, no
      labels in place of stored keys).
- [x] Admin list: id · time · op · table · row · column · old · new ·
      source; filters by table, operation, source, column and date; search
      by row id or value; reason, request and every row of the same
      operation shown when an entry is opened.
- [x] Migration clears the old entries (A5); every stash backed up first
      (`Stashy.bat` does this automatically).
- [x] Tests (`audit/tests.py`, `finance/test_phase14.py`): row shapes for
      insert/update/delete, raw values of every kind, related names, a
      deleted record's full values, many-to-many rows, hidden recovery
      code, time format; the admin hides Users/Groups and keeps Profiles
      read-only; no tags left anywhere.
- [x] DEVLOG.md entry; README's Admin and Audit log paragraphs.

**Done 16/09/2026.** Implementation notes:
- The old `AuditLogEntry` had one `model`/`object_id`/`changes` shape; it's
  now `table` (raw db table, e.g. `finance_transaction`) + `row_id` +
  `row_label`, with `column`/`old_value`/`new_value` for UPDATE rows and a
  `values` JSON snapshot for INSERT/DELETE rows (A4). *Claude's call:* the
  `AuditHistoryAdminMixin` "History" button and the object-history lookups
  now key off `table` + `row_id` instead of `app_label.modelname` +
  `object_id` - one less field, and it's what's shown on screen anyway.
- Every row from the same `save()`/m2m-change/cascade-clear shares one
  timestamp and one operation id, even outside a request (raw model calls
  in scripts/tests have no ambient operation id to inherit, so `_resolved_
  context()` mints one once per event and reuses it for every row of that
  event - otherwise each column would get its own random id).
- A6 is enforced at the field level (`HIDDEN_FIELDS` in `audit/signals.py`,
  currently just `finance.profile.recovery_code_hash`) so it's masked
  everywhere that field could appear: an UPDATE's old/new, and a Profile
  INSERT/DELETE's full `values` snapshot.
- The audit log's own "time" column is always `YYYY-MM-DD HH:MM:SS.mmm
  ±HH:MM` (`regional.format_audit_timestamp`) in the viewer's time zone -
  the one place in the app that deliberately ignores the date_format
  preference, since it's meant to read like a raw database log.
- Verified with a throwaway Django-test-client script (scratchpad only, not
  the repo) reading real rendered HTML: no leaked template syntax, Users/
  Groups gone from the admin index, Profile's change page has no Save
  button and never mentions `recovery_code_hash`/`failed_pin_attempts`, a
  changed recovery code shows as `(hidden)` with the real hash nowhere on
  the page, and the transaction form no longer mentions Tags.
- **Real-stash migration bug, caught only when actually run against the
  'test' stash** (the in-memory test suite has just one `default` alias,
  so it couldn't surface this): the migration's `RunPython` step queried
  the ORM with no `.using(...)`, so `StashRouter` (which reads the target
  database from a thread-local the web middleware sets, but a bare
  `migrate --database <stash>` process never does) silently routed it to
  `default` - the picker's always-empty placeholder, already migrated to
  the new schema by the time any real stash's turn comes. Fixed by binding
  to `schema_editor.connection.alias`. Found and fixed the same latent bug
  in Phase 13's `0014_phase13_watched_expenses.py` while checking for
  others of its kind (harmless there in practice - see DEVLOG). Reproduced
  and verified the fix against a throwaway copy of the real stash's
  `db.sqlite3` before touching the real file.

---

## Phase 15 — Login and new stash setup

Planned 15/09/2026. **Done 16/09/2026.** Needed Phase 13 first (done
15/09/2026) - the welcome page's Watched expenses step uses Phase 13's
setting, which now exists (`Profile.watched_expenses_title`/
`watched_expenses_categories`).

### Decisions
- **L1 — A stash belongs to someone.** Each stash has an **owner name** - a
  person ("Alex"), a business ("Acme Ltd") or a family ("The Smiths") -
  and that's what Stashy shows everywhere instead of the stash's internal
  name: the dashboard greeting ("Good evening, Alex"), the sidebar's Log
  out, the stash picker, the PIN screen, Forgot PIN, the recovery-code page
  and Settings ("You're in Alex's stash"; a name ending in s gets just an
  apostrophe: "The Smiths' stash").
  *Claude's call:* creating a stash asks only **whose stash it is** + PIN;
  the internal folder name is generated from it (`the-smiths`) and never
  shown. Two stashes can't share an owner name (case-insensitive), so the
  picker never shows duplicates. **The owner name is set once, when the
  stash is created, and can't be changed afterwards** (revised 16/09/2026 -
  the user chose not to have it anywhere in Settings). The existing "test"
  stash starts with owner name "test".
- **L2 — No switching while logged in.** Settings' "Switch stash" is
  removed; to use another stash, log out and log in to that one.
- **L3 — Login screens:** Stashy opens on the **last used stash's PIN
  screen** (the browser remembers only which stash - never the PIN) with
  "Forgot PIN?" and "Choose a different stash". The picker is a dropdown +
  Continue, with **"Create a new stash" as a button** leading to its own
  page. With no stashes at all, Stashy opens straight on that page. Logging
  out lands back on that stash's PIN screen.
  ```
  Choose a stash                Create a new stash            Welcome back, Alex
  [ Alex             ▾ ]        Whose stash? [          ]     [ • • • • ]
  [ Continue ]                  PIN          [ • • • •  ]     [ Log in ]
  ──────── or ────────          Confirm      [ • • • •  ]     Forgot PIN?
  [ Create a new stash ]        [ Create stash ]   Back       Choose a different stash
  ```
- **L4 — Welcome steps for a new stash**, right after its recovery code:
  1. **Money & region** - main currency, time zone, date format, number
     format, first day of week (pre-filled from this computer).
  2. **Categories - required:** Basic or Extended.
  3. **Watched expenses** - tick categories, or Skip.
  4. **Create your first account** - or Skip (its currency starts as the
     main currency).
  Each step saves on Next, Back works, and closing Stashy midway resumes
  at that step after the next login. Existing stashes count as already
  set up.
- **L5 — Starter categories come only from the welcome step**, from
  `starter_categories.xlsx` (project root): two sheets, **Basic** and
  **Extended**, columns **Type | Category | Subcategory**. Built 15/09/2026
  from the user's Quicken category list (`categories.txt`, deleted
  afterwards): Basic = the 26
  main categories (identical to `basic_categories.txt`); Extended = those
  plus all their subcategories. Left out on purpose: the per-account
  `[Transfer]` categories (created with each account) and the personal
  "Not Sure" / "Not Sure, Biz" categories (old and no longer wanted - the
  user confirmed they should go; neither exists in the test stash); Quicken's truncated "Other
  Clothing & Accesso..." restored to "Other Clothing & Accessories". The
  Categories page's "Add starter categories" button is removed - delete
  what you don't want instead.
- **L6 — Settings layout (revised 16/09/2026, the user's exact layout -
  build it as drawn, don't improvise).** The Login card is gone.
  - "You're in Alex's stash" sits **right below the "Settings" heading**.
  - **Left column:** "Currency & regional preferences" card unchanged, except
    the three bullet points under Save are removed. Below it, a **new card
    of its own** (same card style as the others) titled **"PIN & recovery
    code"**, holding two buttons **stacked one on top of the other**:
    **Change PIN** and **Get a new recovery code**.
  - **Right column:** Exchange rates, Watched expenses, Backups - exactly as
    they are now.
  - No owner name field anywhere in Settings (L1: set once at creation).
  ```
  Settings
  You're in Alex's stash
  ┌───────────────────────────────┐  ┌────────────────────────────┐
  │ Currency & regional prefs     │  │ Exchange rates (unchanged) │
  │ Main currency     [EUR     ▾] │  └────────────────────────────┘
  │ Time zone         [Sofia   ▾] │  ┌────────────────────────────┐
  │ Date format       [DD/MM/YY▾] │  │ Watched expenses (unchanged│
  │ Number format     [10 000.50▾]│  └────────────────────────────┘
  │ First day of week [Monday  ▾] │  ┌────────────────────────────┐
  │ [ Save ]                      │  │ Backups (unchanged)        │
  └───────────────────────────────┘  └────────────────────────────┘
  ┌───────────────────────────────┐
  │ PIN & recovery code           │
  │ [ Change PIN              ]   │
  │ [ Get a new recovery code ]   │
  └───────────────────────────────┘
  ```
- **L7 — Change PIN is its own page**: current PIN, new PIN, confirm new
  PIN, a **Change PIN** button and a **Back** button (to Settings). Same
  form as today's, minus "You're in stash ... Switch stash". Success goes
  back to Settings with "PIN changed."
  ```
  Change PIN
  ┌───────────────────────────────┐
  │ Current PIN      [••••      ] │
  │ New PIN          [••••      ] │
  │ Confirm new PIN  [••••      ] │
  │ [ Change PIN ]  [ Back ]      │
  └───────────────────────────────┘
  ```
- **L8 — Get a new recovery code is its own page**, replacing today's
  browser `confirm()` pop-up + green message banner (too easy to miss).
  First it shows a warning with **Generate** and **Back**. Generate reloads
  the same page with the new code shown on it and **only Back** left (no
  second Generate, so a second code can't be made by accident). Nothing in
  the messages banner.
  ```
  Get a new recovery code               Get a new recovery code
  ┌─────────────────────────────────┐   ┌─────────────────────────────────┐
  │ ⚠ This replaces your recovery   │   │ ⚠ Write this down now. It won't │
  │   code. The old one will stop   │   │   be shown again.               │
  │   working as soon as you click  │   │      ┌─────────────────┐        │
  │   Generate.                     │   │      │ YV87-6MPN-WMW2  │        │
  │ [ Generate ]  [ Back ]          │   │      └─────────────────┘        │
  └─────────────────────────────────┘   │ [ Back ]                        │
                                        └─────────────────────────────────┘
  ```

### Checklist
- [x] Owner name on the profile (migration; existing stashes start with
      their current name), shown everywhere per L1, with the possessive
      rule for names ending in s.
- [x] Create-a-stash page: owner name + PIN + confirm; folder name
      generated and made unique; duplicate owner names rejected.
- [x] Picker lists owner names (read from each stash); dropdown +
      Continue; "Create a new stash" button.
- [x] Remember the last stash after a successful login (a browser cookie
      holding only the stash's internal name); open on its PIN screen with
      "Choose a different stash"; fall back to the picker if that stash no
      longer exists.
- [x] Remove "Switch stash" from Settings; Log out returns to the
      remembered stash's PIN screen.
- [x] Settings layout per L6 (bullets under Save removed, "PIN & recovery
      code" card, "You're in ... stash" under the heading).
- [x] Change PIN page per L7.
- [x] Get a new recovery code page per L8; remove today's `confirm()` +
      banner version from Settings.
- [x] Welcome steps per L4, with a "setup finished" marker on the profile
      so an unfinished setup resumes after login.
- [x] Categories step reads `starter_categories.xlsx`: Type must be Income
      or Expense, a main category is created from its subcategory rows,
      existing names are matched case-insensitively. If the file or a
      sheet is missing, that choice is disabled with a clear note (and if
      *neither* sheet is available, an explicit Skip replaces "required" -
      Claude's call, see the note below).
- [x] Remove "Add starter categories" (button, view, URL,
      `finance/starter_categories.py`'s text-file reading) with its tests
      in `finance/test_phase12.py`; delete `basic_categories.txt`.
- [x] `manage.py reset_pin` also accepts the owner name.
- [x] Tests (`finance/test_phase15.py`), DEVLOG.md, README (Quick start,
      Stashes and PINs, Categories).

**Done 16/09/2026, all of L1-L8, including the welcome wizard (L4).**
Implementation notes:
- `create_stash(owner_name, pin)` now returns `(folder_name, recovery_code)` -
  the folder name is generated (`_folder_name_from_owner()`, slugified and
  de-duplicated) rather than typed. `stash_choices()`/`owner_name_taken()`
  open every other stash's own database to read its Profile - there's no
  single table a normal unique constraint could cover, since owner names
  live one-per-database.
- `finance.stashes.possessive(name)` is the one place the "ends in s -> just
  an apostrophe" rule lives; `Profile.owner_possessive` calls it so both a
  real Profile and a plain owner-name string (before one exists, e.g. right
  after creating a stash) use the exact same rule.
- **Verified against a real, fully isolated temp directory** (`finance.
  stashes.stashes_dir` monkeypatched away from the project's own
  `stashes/`), never the real `stashes/test/` - create_stash, duplicate/
  slug-collision handling, stash_choices, owner_name_taken, resolve_stash_
  name and reset_pin_directly all confirmed working this way, plus a full
  create -> picker -> login -> dashboard -> logout -> cookie-remembered-PIN-
  screen round trip through the real Django test Client (with `settings.
  TESTING` deliberately left `False` for that last check, so the
  TESTING-mode shortcuts in `StashLoginView`/the routing middleware don't
  mask anything) - same reasoning as test_phase12_stash_login.py's own
  documented limitation on testing these two functions under TestCase.
- **The "remember last stash" cookie fix that mattered:** the first version
  set `request.session['stash']` from the cookie and just continued -
  looked right, but `StashSelectionMiddleware` (which sets the DB-routing
  thread-local) runs *before* `RequireStashMiddleware` in the chain, so it
  had already read the old, empty session by the time the fallback set it.
  Fixed by redirecting back to the same URL instead of continuing in-
  request, so the *next* request is the one that sees the session already
  populated - caught by the round-trip check above, not by a unit test
  (the unit tests assert what the middleware returns, not what a second
  middleware earlier in the chain already did with the session).
- `RegionalPreferencesMiddleware` now stashes the Profile it already fetches
  on `request.stashy_profile`; the new owner-name context processor
  (`finance.context_processors.stash_owner`) reuses it instead of a second
  query - caught by an existing Phase-10 test asserting a fixed query count
  on the transaction list, which would otherwise have quietly grown by one
  query per page load.
- Settings' "You're in ... stash" line and the PIN & recovery code card
  buttons are plain links now (`{% url %}`), not the old inline forms -
  Change PIN and Get a new recovery code are real pages (L7, L8).
- **L4, the welcome wizard**, confirmed against an ASCII mockup in chat
  before building (same two-panel layout as login/create-stash, a step
  counter, a wider card variant - `.login-card-wide` in stashy.css - for
  the Categories and Create-an-account steps). `Profile.welcome_step`
  (0-3 = next step to show, 4 = `WELCOME_STEPS_DONE` = finished) tracks
  progress; `finance/welcome_views.py` keys a step's handler off the URL's
  `<int:step>` (1-4, matching what's shown on screen) rather than the
  0-indexed internal counter. Visiting a step beyond `welcome_step + 1`
  bounces back to wherever setup actually is; visiting an *already*
  finished step is allowed, so Back genuinely lets you revise an earlier
  answer, not just look at it.
- **`RequireSetupMiddleware`** enforces this everywhere else in the app
  (redirects to `/welcome/<step>/` for anyone logged in with `welcome_step
  < 4`), placed *after* `RegionalPreferencesMiddleware` in settings.py so
  it can reuse `request.stashy_profile` instead of a third `Profile.
  for_user()` query this request.
- **Claude's call: a missing `starter_categories.xlsx` gets an explicit
  Skip on the Categories step**, even though L4 calls that step
  "required." A *single* missing sheet just disables that one button with
  a note (per the plan) - but with the file itself gone, both buttons
  would be disabled and there'd be no way to ever move past step 2, which
  is worse than the "required" choice being enforced. Only shown when
  `available_sheets()` returns nothing at all.
- Step 4 (create an account) reuses the exact `AccountCreateForm` the
  regular Accounts page uses, unchanged - same fields, just "Finish"
  instead of "Save" and a "Skip" beside it.
- **Verified two ways**, per [[feedback-render-check-when-no-browser]]:
  the automated tests above, and a full click-through via the real Django
  test client with `settings.TESTING` left `False` - all four steps in
  order, the real project `starter_categories.xlsx` (26 Basic categories
  actually created), the date picker's markup present on step 4, and
  landing back on the dashboard (not the wizard) once finished.

---

## Backlog — found during implementation, not scheduled yet

- [x] Deleting an **account** silently deletes all of its transactions
      (same class of problem as Bug 2) — needs at least a warning with a
      transaction count. Probably fits Phase 3.
- [x] Imports aren't atomic — a malformed file part-way through leaves a
      partial import behind. Wrap the import in a database transaction
      (Phase 9).
- [x] `TIME_ZONE` is `UTC`, so import timestamps show UTC rather than your
      local time. Needs your actual time zone.
- [x] Moved to Phase 13 (done 15/09/2026): Reports: after an invalid
      filter submission, the dropdowns echoed the rejected values instead
      of the defaults actually in use - fixed (`_report_filters()` swaps
      in a fresh, unbound form on an invalid submission).
- [x] `settings.py` has a `MAILERS` setting that Django doesn't recognize
      (the real one is `EMAIL_BACKEND`) — dead config, remove or replace.
      **Removed 15/09/2026** (confirmed Django 6.1 doesn't know it, and
      Stashy sends no email).
- [x] `reminder_enter` accepted a plain GET, so merely visiting
      `/reminders/<id>/enter/` created a transaction. Resolved by Phase 7's
      rework of Enter — that view is gone; Enter is now a GET into the
      transaction form (which creates nothing until you submit it).
- [x] Admin shows "Import batchs" — `ImportBatch` needs
      `verbose_name_plural = "import batches"` (cosmetic).
- [x] Payee matching is exact-case, so `Lidl`/`LIdl` and a couple
      of other pairs became separate payees when
      `test_data.TXT` was loaded. Needs case-insensitive matching on entry
      and a "merge payees" tool (fits Phase 6, alongside Phase 9's
      case-insensitive import matching).
      **Done:** manual entry and import both match payees regardless of
      letter case and reuse the existing payee's own spelling (tests in
      `finance/test_phase6_1.py`); the test stash has no case-duplicate
      payees left. **No merge tool** - the user decided 15/09/2026 it
      isn't needed: the duplicates came from Quicken changing letter case
      on export, and matching an existing payee regardless of case
      already prevents them.
- [x] `Account.balance` (and other SQLite `SUM()`s) return Decimals with
      trailing zeros like `9.66000000000000`; harmless on screen because
      the `money` filter re-quantizes, but quantize to cents in the model
      if raw values are ever used in further calculations.
- [x] `test_data.TXT` has a Quicken `Num` column (transaction numbers) that
      `scripts/load_test_data.py` ignores because `transaction_number`
      doesn't exist yet — once Phase 3 adds it, extend the loader (or
      re-run it) to carry the numbers over. Transfers in that file are
      also uncategorized until Phase 3's transfer categories exist.
- **Moved to Phase 14 (15/09/2026):** Audit log's `changes` JSON is still formatted once at write time
      (global DD/MM/YYYY + space-dot style) rather than storing raw values
      and formatting them per-viewer at display time — D10 asked for the
      latter; only the admin's own timestamp column does that so far (see
      Phase 3's note). Only matters once regional preferences actually
      change after data exists.
- [x] Moved to Phase 13 (done 15/09/2026): Net worth over time (the
      Reports page chart), and every other report, now converts through
      the main currency's latest known rate the same way the dashboard
      and account list already did (D2's "historical accuracy is a bigger
      feature" note still stands - it's the latest rate, not a per-day one).
- [x] Leftovers from Phase 12 that nothing uses any more: the original
      top-level `db.sqlite3` (kept as a safety net when the data was
      copied into `stashes/test/`) and `current_stash.txt` (unused since
      the live-switching rewrite). Safe to delete once you're happy with
      the stash setup.
      **Deleted 15/09/2026** at the user's request, after confirming the
      test stash's own data was intact.
- [x] `scripts/load_test_data.py` still loads into Django's `default`
      database, which since the live-switching rewrite is the empty
      placeholder `_picker.sqlite3`, not a stash. It needs a stash name
      (keeping its "deletes everything first" warning) before it's used
      again - e.g. for loading real data.
      **Fixed 15/09/2026:** `--stash <name>` is now required; the script
      lists what it's about to delete and asks for the stash's name typed
      back (`--yes` skips that), and its all-or-nothing transaction now
      wraps the stash's own connection instead of the placeholder's.
      Tested against a throwaway stash (full load, refused confirmation,
      unknown name), then deleted it.
- [x] **Settings: self-service recovery code regeneration**, requested
      16/09/2026 during the Phase 14 follow-up conversation about admin
      Profile rights. `Settings` → Login now has "Get a new recovery
      code" — a plain JS `confirm()` (matching the one the recovery-code
      page's own "I've saved it" button already uses, not a dedicated
      confirm page, since nothing destructive happens until you confirm)
      then a fresh code shown once via the messages banner, replacing
      (invalidating) the old one exactly like a successful "Forgot PIN"
      reset does. `finance.stashes.regenerate_recovery_code()`.
- [x] **`Transaction.cleared` removed entirely**, requested 16/09/2026 -
      "I don't think I'm going to use it... I want the app to be as
      simple as possible." Dropped the field, the register's `C` badge
      (now just `R`/`—`), the transaction form checkbox, the CSV/XLSX
      export column, the admin filter, and the reconcile checklist's
      "pre-check anything already Cleared" convenience (the checklist now
      always starts blank - "Mark all" is still there for a statement
      that covers everything). `Reconciled` and the Reconcile workflow
      itself are unaffected - only the informal, day-to-day flag went.

---

## Parked ideas — discussed, not decided or deliberately deferred

- **Native window / `.exe` instead of a browser tab.** Agreed 2026-09-12 to
  stay browser-based for now. Options when revisited: `pywebview` (Stashy
  in its own window) and/or PyInstaller (one `.exe`, ~60-150 MB, bundles
  Python). A zero-install `Stashy.bat` bundling a portable embeddable
  Python is the lighter alternative.
