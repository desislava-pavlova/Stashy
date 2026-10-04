# Stashy Architecture Cleanup Plan

Source: architecture review on 25/09/2026 (senior-Django review of the whole
project: architecture, SOLID, PEP 8, Django conventions). No code was changed
by the review itself. Reviewed again on 27/09/2026 against the code: 18
findings added (F41–F58), five items dropped or moved (see "Plan review,
27/09/2026" below). Test baseline that day: 510 tests, all green, 243 s.
Progress is tracked with the checkboxes below. **Status:** Phase 1 done
(01/10/2026, 540 tests green); Phase 2 done (01/10/2026, 572 tests green);
Phase 3 done (02/10/2026, 580 tests green in 15 s) except the migrations
question in 3.6, which waits until the cleanup is done; Phase 4 done
(02/10/2026, 610 tests green in 17 s, coverage 89% → 90%); Phase 5 done
(03/10/2026, 633 tests green in 22 s, coverage 90% → 91%); Phase 6 done
(03/10/2026, 675 tests green in 23 s, coverage 94%); Phase 7 done in two
parts (04/10/2026: 7.1-7.3 and the shared bases of 7.4; 05/10/2026: the
rest of 7.4; 683 tests green in 24 s, coverage 94%); next: Phase 8.

Work through it phase by phase. Every phase leaves the app working and the
full test suite green, so we can stop and resume anywhere. Line numbers in
the findings are as of 25/09/2026 and will drift once work starts - search
for the function name if a line number no longer matches.

**Guiding idea:** no new features and no visible behaviour changes (except
where a finding is itself a bug). The goal is code that is safer, easier to
change, and follows common Django/Python conventions.

**Before any phase that touches migrations or database files:** one stash
holds real data since 16/09/2026 - take a manual backup of every stash
first (Settings → Back up now, or copy `stashes/<name>/db.sqlite3`).

---

## Decisions log

- **C1 — Line length is 119, not 79.** PEP 8 allows teams to agree on up to
  99/119; 119 is Django's own project style and fits this code's long,
  descriptive names. Enforced by ruff, not by hand.
- **C2 — History leaves code comments.** Dates, phase numbers and "follow-up"
  notes move to DEVLOG.md (where the "why" already lives); code comments keep
  only what explains the code as it is now.
- **C3 — Behaviour stays identical.** Refactoring phases (3–8) must not change
  what any page shows or does. Tests are the proof; where a test only covers
  something through a helper that moves, the test moves with it.
- **C4 — Services are plain functions in modules**, not classes or a DI
  framework - the smallest thing that separates HTTP handling from rules.
- **C5 — `plans/` is the home for plans.** The original upgrade plan is
  `plans/01_upgrade_plan.md` (renamed from `UPGRADE_PLAN.md` on 25/09/2026,
  content unchanged). New plans get the next number.

Open questions (decide when the phase is reached, not before):

- **Q1 (Phase 2):** ~~If duplicate top-level categories or case-duplicate
  payees already exist in a stash, merge them automatically or list them for a
  hand decision?~~ **Answered 27/09/2026 (user):** names that differ only in
  case are the same category or payee, and duplicates must never exist. When
  you type an existing name in a different case (transaction form, import,
  quick edit), the spelling already saved wins. The Add/Edit forms reject a
  case-duplicate with a clear message. A scan the same day found **no**
  duplicates in the real stash or "test", so 2.2 has nothing to merge. Re-run the
  scan right before migrating anyway.
- **Q2 (Phase 6):** ~~Split the PIN/lockout/recovery fields off `Profile`
  into their own model, or leave them?~~ **Resolved 27/09/2026 (Claude's
  call): leave them.** A migration on real data buys no behaviour and no
  readability that 6.5 doesn't already give.
- **Q3 (Phase 8):** ~~Paginate the Transactions page, or keep one long list
  and only make it lighter?~~ **Answered 27/09/2026 (user):** do both.
  Always make it lighter (8.10). Keep one page for any date range of a year or
  less. For a range longer than a year (including "All time"), show 1,000
  rows per page. The total always covers every page,
  not just the one on screen. For scale: every year in the real stash has fewer
  than 1,000 rows, so today only multi-year ranges ever get a second page.

### Plan review, 27/09/2026

A second pass over the code and this plan. Kept as a record of what changed
and why:

- **Added F41–F58** (section H below; F55–F58 came from a second sweep). The most important: the Django admin
  login skips the PIN lockout (F41); `atomic()`/`on_commit()` calls without
  `using=` point at the placeholder database, which becomes a real bug the
  moment 1.1 lands (F42); restoring an older backup can bring back an older
  database schema (F44).
- **Dropped 2.5 (F7, transaction-number constraint).** Numbers are editable
  by design and Quicken imports reuse them, so a unique constraint would
  reject real data. The `MAX()+1` race can't happen either: every write
  request starts with `BEGIN IMMEDIATE`, which 1.1 keeps for the active stash.
- **Moved 8.2 (F28, reports in the database) to the backlog.** Nothing is
  measured as slow at today's size. SQLite sums decimals as floats, and
  converting a per-currency total rounds differently from converting row by
  row, so "must match to the cent" can't be promised with several
  currencies. F53 gets the readability part without that risk.
- **Dropped 8.9 (`%b` month names).** Nothing calls `locale.setlocale`, so
  Python stays in the "C" locale and `%b` is always English.
- **Dropped 6.6 (Q2).** See Q2.
- **Moved 1.5 (F4) into 5.2.** Every caller of `_export_rates` is already
  login-protected, so nothing is broken today. The helper goes away with the
  export registry anyway.
- **Changed 3.5 (Claude's call):** two settings modules (`base.py`,
  `test.py`), not three. The development-only values already come from `.env`,
  so a `dev.py` would hold nothing.
- **Changed 3.1 (Claude's call):** ruff also enforces `N` (PEP 8 naming),
  `SIM`, `C4` and `RUF`. You asked for PEP 8, and naming is part of it.
- **Type hints** moved from the backlog into Phase 4 for the new service
  modules only (4.9). Old code gets them only when it is touched anyway.
- F27 was out of date: `all_data.xlsx` and `test_data.TXT` are already gone
  from disk. It also missed that `.idea/` is ignored but five of its files are
  still tracked.

---

## Findings

Each finding has an ID used by the phases below.

### A. Correctness and data safety

- **F1 — Every request opens a transaction on every stash database.**
  `ATOMIC_REQUESTS=True` is copied into each stash alias by
  `stashes.ensure_registered()` (`finance/stashes.py`). Django wraps each view
  in `atomic()` for every alias with that flag - verified on 25/09/2026:
  `{'default': True, '<real stash>': True, 'test': True}`. With
  `transaction_mode: IMMEDIATE` that is a `BEGIN IMMEDIATE` write lock on
  every stash file for every request, so separate stashes block each other.
- **F2 — Restore copies over a live, locked SQLite file.** `backup_restore`
  (`views.py`) → `backup.restore_from()` uses `shutil.copy2` while the request
  itself holds a transaction on that database (F1) and other threads may hold
  open connections. Risk of a corrupted database. Plain-copy backup has the
  same weakness during a write.
- **F3 — Category uniqueness misses top-level categories.**
  `UniqueConstraint(fields=["name", "parent"])` never fires when `parent` is
  NULL (NULLs are distinct in SQL), so two top-level "Food" categories are
  allowed. `Payee.name` is unique only case-sensitively; case-insensitive
  de-duplication is re-implemented by hand in three places.
- **F4 — `@login_required` on a helper.** `_export_rates` (`views.py`) is not
  a view; for an unauthenticated user it would return a redirect response that
  callers then use as a `RateTable`.
- **F5 — Reconcile "finish" doesn't validate input.**
  `date.fromisoformat(request.POST["statement_date"])` and
  `Decimal(request.POST["statement_balance"])` in `account_reconcile` raise a
  500 on missing/bad values.
- **F6 — Logout over GET.** `switch_stash` (`auth_views.py`) logs out on any
  GET, so any link or image can log you out (CSRF logout). Django's own
  `LogoutView` is POST-only since 5.0.
- **F7 — Transaction numbers can collide.** `Transaction.next_number()` is a
  `MAX()+1` read-then-write with no constraint behind it.
- **F8 — Two different "today"s.** 18 non-test call sites use `date.today()`
  (server clock) while others use `timezone.localdate()` (the user's time zone
  preference). Some messages hard-code `%d/%m/%Y` instead of the regional
  date format (`transaction_form_view`, `reminder_skip`).

### B. SOLID and separation of concerns

- **F9 — `finance/views.py` is 2,011 lines.** Ten feature areas plus the
  import engine, reconciliation, reminder rules, export helpers and UI tip
  text in one module (single responsibility).
- **F10 — Business logic lives in views.** The CSV/XLSX/OFX import pipeline
  (~300 lines: `_import_rows`, `_resolve_category`, date sniffing,
  fingerprints), reconciliation, and the reminder "enter" sequence (record
  period → clear link → advance), which is duplicated between
  `transaction_form_view` and `reminder_skip`.
- **F11 — Same rule, two implementations.** Payee resolution:
  `TransactionForm._resolve_payee` (audited with `system_action`) vs.
  `views._get_or_create_payee` (not). The Watched-expenses button label is
  copied between `settings_view` and `welcome_views._watched_expenses_context`.
- **F12 — Hidden side effects in `save()`.** `Account.save()` always runs
  `ensure_transfer_category()` (extra queries on every save, even
  `update_fields` saves); `Reminder.save()` regenerates up to 500 occurrence
  rows.
- **F13 — Invariants live only in forms.** "One level of subcategories" is
  enforced only by `CategoryForm`; admin, import, scripts and shell bypass it.
- **F14 — Modules/models with several jobs.** `Profile` = preferences +
  wizard progress + PIN lockout + recovery hash + report config.
  `stashes.py` = filesystem registry + DB alias registration + PIN security +
  CLI helpers.
- **F15 — Exports aren't open for extension.** Eight near-identical
  `export_report_*` views and eight URL patterns; adding a report means
  editing three places.
- **F16 — `settings_view` dispatches four forms** by submit-button name
  (`check_rates`, `backup_now`, `save_watched_expenses`, preferences).
- **F17 — Cross-app coupling.** `stashes._audit_signals_suspended()` reaches
  into the audit app's `dispatch_uid`s and reconnects its handlers by name
  (and runs a needless 5×5 disconnect loop).
- **F18 — Two request-scoped-state mechanisms.** The DB router uses
  `threading.local`; audit uses `ContextVar` (correct, async-safe).

### C. Tests

- **F19 — Production code branches on `settings.TESTING`.** Derived from
  `sys.argv` (`settings.py`, duplicated in `finance/apps.py`) and checked in
  three middlewares, `StashLoginView`, `_stash_backup_root` and
  `_current_stash_db_path`. The suite therefore never runs the real
  stash-selection/auth/setup middleware.
- **F20 — Tests organised by phase, not feature.** 30 `test_phaseN*.py` files
  plus `tests.py` at the app root.

### D. PEP 8 and style

- **F21 — No linter/formatter; long lines everywhere.** Lines over 79 chars:
  `views.py` 414 (43 over 119), `models.py` 199, `reporting.py` 161,
  `forms.py` 103. No `pyproject.toml`/ruff/flake8 config.
- **F22 — Smaller PEP 8 issues.** Lambdas assigned to names with
  `# noqa: E731` (`reports_view`); stray/missing blank lines (`views.py`
  around `CategoryCreateView`, `reporting.py` before `resolve_range`);
  single-letter names in large scopes (`f`, `t`); mixed quote styles; an
  `if/else` with identical branches in `TransferForm.__init__`.
- **F23 — Comments used as a changelog** ("2026-09-14 follow-up",
  "Phase 13 R5", "user, 16/09/2026") - see C2.
- **F24 — Hand-rolled versions of Django built-ins.** `_safe_next()` →
  `url_has_allowed_host_and_scheme`; upload extension check in the view →
  `FileExtensionValidator` on `ImportUploadForm`; `_import_ofx`'s
  missing-`ofxparse` branch is dead code (it's a pinned requirement).

### E. Settings, dependencies, repo hygiene

- **F25 — One settings file with unsafe defaults:** hard-coded insecure
  `SECRET_KEY` fallback, `DEBUG=True` by default.
- **F26 — Requirements drift.** Installed but not listed: `psycopg2-binary`,
  `dj-database-url`, `beautifulsoup4`, `lxml`. No dev requirements file.
- **F27 — Generated/data files in the tree.** `staticfiles/` (collectstatic
  output) is tracked; `_picker.sqlite3`, `all_data.xlsx`, `test_data.TXT`,
  `backups/` sit in the project root. *(27/09/2026: `all_data.xlsx` and
  `test_data.TXT` are already gone; `_picker.sqlite3` and `backups/` are
  already ignored. Still open: `staticfiles/` tracked, and `.idea/` is in
  `.gitignore` but five of its files are still tracked.)*
  *(Also 27/09/2026: `.gitignore` had `**/migrations/*.py`, so git held only
  `finance/migrations/0001_initial.py` - a fresh clone couldn't build the
  database. The ignore lines were removed that day, then put back by the
  user on 01/10/2026: migrations stay out of git for now - see 3.6.)*

### F. Performance

- **F28 — Reports aggregate in Python.** `reporting.py` iterates `.values()`
  rows to total them; `reports_view` runs ~15 of these per page load.
- **F29 — Avoidable queries.** One fingerprint `exists()` query per imported
  row; `Profile.for_user()` re-queried in views though the middleware already
  set `request.stashy_profile`.

### G. Found in the second pass (25/09/2026)

- **F30 — Dashboard mixes currencies.** `dashboard` calls
  `income_and_spending()` and `spending_tree()` without `rates`, so the
  month's income/spending tiles, the savings rate and the donut add amounts
  from EUR/BGN/USD accounts together unconverted - while Reports converts the
  same numbers through `RateTable`. Net worth on the same page *is* converted.
- **F31 — Donut breaks the R1 rule.** `spending_tree()` takes `abs()` of each
  category's net total, so an expense category that nets positive (a refund
  bigger than the spend) is *added* to spending. Everywhere else in
  `reporting.py` it nets against its own total.
- **F32 — Import crashes on bad files.** A CSV that isn't UTF-8 (e.g. a
  Windows-1251 bank export), a damaged XLSX or a malformed OFX raises a 500.
  Nothing in `import_transactions` catches parse errors and shows a message.
- **F33 — Spreadsheet formula injection in exports.** Payee/memo/category
  text starting with `=`, `+`, `-` or `@` is written raw to CSV/XLSX. Imported
  bank data can contain such text, and Excel may run it as a formula when the
  export is opened. Standard fix: prefix those cells with `'`.
- **F34 — Net worth report runs accounts × periods queries.**
  `net_worth_by_account()` does one `aggregate()` per account per column (up
  to 36 columns), instead of one grouped query plus a running sum.
- **F35 — PIN rules copied five times.** The `PIN_RE` check is in four form
  `clean_*` methods plus `stashes.validate_pin()`, and the "PINs don't match"
  `clean()` is copied three times (`auth_forms.py`). One `PinField` and one
  confirm-mixin would cover all of them.
- **F36 — Circular dependency between apps.** `finance` imports
  `audit.context` (fine), but `audit/admin.py` imports `finance.regional`, so
  the audit app can't exist without finance. Timestamp formatting for the
  audit admin should be passed in or live in a shared module.
- **F37 — Everyday launches run with `DEBUG=True`.**
  `scripts/bootstrap.py` writes `DEBUG=True` into every new `.env`, so the
  normal Stashy.bat app shows full tracebacks (settings, paths, local
  variables) on any error. It should write `DEBUG=False`; development turns
  it on by hand.
- **F38 — Two rules for a subcategory's type.** `starter_categories.py`
  gives a subcategory the sheet row's type; the importer gives it the
  parent's type. The starter import's top-level lookup also includes
  accounts' `[Transfer]` categories.
- **F39 — One `|safe` JSON injection.** `generic_form.html` renders
  `unit_words_json|safe` into a `<script>`; every other page uses
  `json_script`. Safe today (a server constant) but inconsistent and easy to
  make unsafe later.
- **F40 — Dead branch in a report total.**
  `category_vs_average_report_rows()` starts `totals["average"]` at `ZERO`,
  so its `is not None` checks never fail, and the total average silently
  leaves out rows that have no average.

### H. Found in the plan review (27/09/2026)

- **F41 — The admin login skips the PIN lockout.** `/admin/login/` is
  Django's stock login view, and each stash's user is a superuser, so the
  4-digit PIN can be guessed there without limit. The lockout only lives in
  `StashLoginView`.
- **F42 — Transaction calls without `using=` point at the wrong database.**
  `currency.fetch_latest_rates()` opens `atomic()` and `audit/signals.py`
  registers `transaction.on_commit()`. Both default to the `default` alias
  (the picker's placeholder), not the stash the router writes to. Today
  `ATOMIC_REQUESTS` on every alias hides this. Once 1.1 lands, that
  `atomic()` protects nothing and the audit's "after commit" logging runs at
  the wrong moment.
- **F43 — "Check rates" holds the write lock while waiting on the
  internet.** The request's `BEGIN IMMEDIATE` starts before the view runs,
  so the stash stays locked for up to 10 s during the Frankfurter call. The
  docstring says the opposite. 1.1 alone doesn't fix this, because the
  active stash stays atomic.
- **F44 — Restore can bring back an older schema.** A backup taken before an
  update (`db_before_update_*`, an old quarterly one) has fewer migrations
  applied. The running server never migrates, so pages crash until the next
  launch. Smaller issue in the same area: `_STAMP_RE` doesn't match tags that
  contain `_` (`before_restore`, `before_update`), so those backups quietly
  sort by file modification time instead of their name.
- **F45 — The recovery code travels in a cookie.** `recovery_code_view` puts
  the plain code into the session. Sessions are signed cookies, which are
  signed but not encrypted, so the code sits readable in the browser's cookie
  until the next page load.
- **F46 — Exports don't open well in Excel.** Every XLSX cell is text
  (`str(amount)`, ISO date strings), so Excel can't add up the amounts. CSV
  files have no UTF-8 BOM, so Excel shows Cyrillic payee names as garbage
  (the importer already reads `utf-8-sig`). 1.11 assumed amounts were
  numeric. They aren't.
- **F47 — Import drops rows without saying so.** Rows with an unreadable date
  or amount are skipped and not counted. The summary only mentions duplicates
  and unknown accounts. A negative `Num` crashes with a database error, and
  `inf` raises `OverflowError`; neither is caught.
- **F48 — The Transactions page is heavy.** It has no pagination, and Edit
  mode renders two hidden `<select>`s on every row (all accounts, about 150
  categories). On "Earliest to date" in the real stash (about 4,500 rows), that's roughly
  700,000 `<option>` elements (an estimate from the row and category counts).
- **F49 — Login protection is opt-in per view.** Every view carries
  `@login_required` or `LoginRequiredMixin`, so a new view that forgets it is
  public. Django 5.1+ has `LoginRequiredMiddleware` plus
  `@login_not_required` for the picker, login and forgot-PIN pages. The
  middlewares' `EXEMPT_PREFIXES` are also hard-coded URL strings.
- **F50 — Circular imports hidden inside functions.** `Profile.owner_possessive`
  imports `stashes`, and `stashes` imports `models` inside functions.
  `currency` imports `models` inside functions. `stashes` imports
  `get_user_model` and the hashers inside functions. These imports work
  around cycles instead of removing them.
- **F51 — Dead code and data.** `Transaction.payee_name` is always blank (0
  non-empty rows in the real stash; the form writes "" and the importer never sets
  it), yet it's still searched on the Transactions page and in the admin.
  `reporting.category_totals()` is used only by tests. `scripts/load_test_data.py`
  reads `test_data.TXT`, which no longer exists. `AUTH_PASSWORD_VALIDATORS`
  never apply, because PINs are set with `set_password()` and
  `create_superuser()` directly.
- **F52 — The test suite takes 4 minutes.** 510 tests take 243 s. About 70
  test classes use `setUp` rather than `setUpTestData`, and they create users
  with the default PBKDF2 hasher (over a million iterations each).
- **F53 — Report queries are built by hand every time.** "In the date range,
  has a category, not a transfer, of this type, for this account" is written
  out in nine `reporting.py` functions. The parent roll-up block is copied
  between `category_report_rows()` and `category_vs_average_report_rows()`.
- **F54 — The admin can break rules the forms enforce.** Beyond F13, the
  admin can change `Account.currency` after transactions exist (the form
  locks it), create a Transfer-type category with no account, and save a
  reminder with a negative amount or a zero interval.
- **F55 — Open redirect in `_safe_next()`** (second sweep, 27/09/2026).
  `/\evil.com` passes its check (it starts with `/` but not `//`), and
  browsers read `/\` as `//`, which sends you off-site. F24 treated this as
  style only; it's a real bug.
- **F56 — Small N+1 queries.** The dashboard's "due soon" list and the
  reminder list fetch each reminder's payee and category (and the category's
  parent) one query at a time. `export_accounts` and the admin's account list
  run one balance query per account.
- **F57 — Static files have no cache-busting.** Plain
  `CompressedStaticFilesStorage` keeps file names the same after an update,
  so browsers either re-check every file or run stale JS for a while.
- **F58 — With `DEBUG=False`, errors vanish.** There's no `LOGGING` config
  and no `404.html`/`500.html`. Once 3.7 turns `DEBUG` off, a crash shows
  Django's bare error page, and the traceback isn't saved anywhere.

---

## Phase 1 — Data safety and wrong numbers  (F1, F2, F5, F6, F30–F33, F41–F47, F55)

**Why first:** these can lose or corrupt data, crash on bad input, or show
wrong totals.

- [x] **1.1** Stop atomic requests on inactive stashes (F1): set
  `ATOMIC_REQUESTS=False` in the alias template in `ensure_registered()`, and
  wrap the view call in `transaction.atomic(using=alias)` for the *active*
  alias only (in `StashSelectionMiddleware` or a small dedicated middleware).
  Keep `default` behaving as today. **Same step (F42):** every explicit
  `atomic()`/`on_commit()` gets `using=` the routed alias
  (`router.db_for_write(Model)`); grep for bare `atomic(` and `on_commit(`.
  *Done 01/10/2026 (Claude's call on the how):* `StashAtomicRequestMiddleware`,
  last in `MIDDLEWARE`, wraps the view in `atomic(using=<active stash>)` from
  `process_view` - a plain middleware wrapping `get_response` would see a 500
  *response*, not the exception, and commit half a change. A view marked
  `@transaction.non_atomic_requests` (no `using`) skips it, since a stash's
  alias isn't known when the decorator runs.
- [x] **1.2** Test: a request on stash A must not open a connection or
  transaction on stash B (inspect `connections[...].in_atomic_block` /
  `connection` state from inside a test view).
- [x] **1.3** Backups via SQLite's own API (F2): `backup.backup_now()` uses
  `sqlite3.connect(src).backup(dst)`; `restore_from()` does the reverse into
  the live file after the safety backup. Mark `backup_restore` with
  `@transaction.non_atomic_requests`, and close the stash's connection on
  *all* threads' handlers where possible (at minimum document that the server
  should be restarted if a stale connection is suspected). **Also (F44):**
  run `migrate --database=<stash>` right after a restore, so an older backup
  comes back on today's schema. Test this by restoring a copy taken before the
  last migration. Fix `_STAMP_RE` so tags with `_` in them parse.
  *Done 01/10/2026:* SQLite's backup API keeps other threads' open
  connections valid (a test proves an open connection sees the restored
  data), so no connection-closing across threads is needed.
  `stashes.restore_stash()` restores, closes this thread's connection and
  migrates.
- [x] **1.4** Keep `scripts/bootstrap.py`'s quarterly backup working - it
  imports `backup.py` before Django is set up, so stay standard-library only.
- [x] ~~**1.5** Remove `@login_required` from `_export_rates` (F4); rename it
  `_rates_for(request)`.~~ Moved into 5.2 on 27/09/2026 (nothing is broken
  today; the helper goes away with the export registry).
- [x] **1.6** Add `ReconcileFinishForm` (statement_date, statement_balance,
  transaction ids, accept_difference) and use it in the finish stage (F5).
  Test: missing/garbled values re-render with an error, never a 500.
  *Claude's call:* the fields are hidden, so a bad submission can only come
  from a tampered page or transactions reconciled meanwhile - it goes back to
  step 1 with "Something in that reconciliation didn't come through".
- [x] **1.7** Make `switch_stash` POST-only (`@require_POST`) and change the
  sidebar/login "Choose a different stash" links into small POST forms (F6).
  Render-check the pages afterwards (no browser here).
- [x] **1.8** Dashboard converts through `RateTable(main_currency)` like
  Reports: pass `rates` to `income_and_spending()` and give `spending_tree()`
  a `rates` parameter (F30). Test with two accounts in different currencies.
- [x] **1.9** `spending_tree()` follows R1: net each category against its own
  total and leave out (or show as zero) a category that nets positive,
  instead of `abs()` (F31). Agree the exact display with the user first -
  it changes what the donut shows. **Answered 27/09/2026 (user):** only
  expense categories, each one its own net total. A category that nets to a
  refund shows as a negative amount (spent 40, refunded 100 → −60), not +60.
  **Display detail (Claude's call):** a donut can't draw a negative wedge,
  so such a category gets no wedge. It is still listed in the donut's legend
  and drill-down with its −60, and the centre total nets it (the total then
  equals the Spending tile). The same applies one level down, to a
  subcategory inside a main category's drill-down. Test with a refund bigger
  than the spend.
- [x] **1.10** Import error handling (F32): catch decode/parse errors and
  show a clear message ("This file isn't UTF-8 - save it as CSV UTF-8 and try
  again"); optionally try `cp1251`/`cp1252` as a fallback. *Claude's call:*
  UTF-8 first, then Windows-1251 only (Bulgarian bank exports), and the
  import summary says so, since a Windows-1252 file would get Cyrillic letters
  in place of its accents. Tests with a
  Windows-1251 CSV and a garbled OFX. **Also (F47):** count skipped rows and
  say so ("3 rows skipped - unreadable date or amount"); a bad `Num` (negative,
  huge, `inf`) becomes "no number" instead of a crash.
- [x] **1.11** Exports escape cells starting with `= + - @` (F33) - but not
  real negative numbers: only text columns (payee, memo, category, account
  name) are escaped, amounts stay numeric. **Also (F46):** amounts go into
  XLSX as real numbers with a 2-decimal format and dates as real dates; CSV
  starts with a UTF-8 BOM so Excel reads Cyrillic correctly. Test: open the
  XLSX with openpyxl and check the cell types.
- [x] **1.12** Admin login goes through the stash login (F41): send
  `/admin/login/` to `StashLoginView` with `?next=`, or count failures in one
  place every login path shares (a `user_login_failed` receiver). Test: five
  wrong PINs via `/admin/login/` lock the stash. *Claude's call:* the
  redirect - `/admin/login/` now sends you to the PIN screen with `?next=`,
  so no PIN is ever checked there at all (tested: posting a PIN to it logs
  nobody in).
- [x] **1.13** "Check rates" doesn't hold the lock during the network call
  (F43): that endpoint is `@transaction.non_atomic_requests` (per alias), and
  `fetch_latest_rates()` opens its own short `atomic(using=...)` only for the
  writes, after the download. Fix the docstring. Easiest after 5.3 gives it
  its own URL; if Phase 1 comes first, mark `settings_view` itself.
- [x] **1.14** The recovery code never goes into the cookie (F45). Claude's
  call: keep the code server-side in Django's local-memory cache under a
  random one-time key, and store only that key in the session. The page reads
  the code once and deletes it. Stashy runs as a single process, so that
  cache is enough.
- [x] **1.15** Fix the open redirect in `_safe_next()` (F55, brought forward
  from 4.6): replace it with `url_has_allowed_host_and_scheme(url,
  allowed_hosts={request.get_host()})`. Test: `next=/\evil.com` is refused.

**Done when:** full suite green; the new tests above exist; a manual backup
and restore on a throwaway stash round-trips.

## Phase 2 — Database constraints  (F3, F13, F51, F54)

**Needs:** a backup of every stash first; Q1 answered if duplicates exist.

- [x] **2.1** Write a read-only check script (scratchpad, not the repo) that
  lists, per stash: duplicate top-level category names (case-insensitive),
  case-duplicate payees, duplicate `(year, transaction_number)` pairs.
  *Done 01/10/2026:* it compares names with Python's `casefold()` (see 2.3)
  and also counts every row 2.6, 2.8 and 2.9 would reject. Both stashes:
  nothing to fix. The only finding is 3 duplicate `(year, number)` pairs in
  "test", which are allowed (2.5).
- [x] **2.2** Resolve anything found (per Q1) before migrating. None was
  found on 27/09/2026. If some appear later: keep the oldest row's spelling,
  move its transactions, reminders and Watched-expenses links over, then
  delete the rest. *01/10/2026:* nothing found again, so nothing to merge.
- [x] **2.3** Migration: `UniqueConstraint(Lower("name"),
  condition=Q(parent__isnull=True), name="unique_top_level_category_name")`
  and change the existing per-parent one to be case-insensitive too.
  **Changed (Claude's call, 01/10/2026): a `name_key` column instead of
  `Lower("name")`.** SQLite's `lower()` and `LIKE` only fold A-Z, so
  `Lower()` would let "ЛИДЛ" and "лидл" both exist (and the old `iexact`
  lookups didn't match them either). Payee and Category now keep a hidden
  `name_key` = `name.strip().casefold()`, set by `save()`, and the unique
  constraints sit on it: `unique_subcategory_name_ci` (parent, name_key) and
  `unique_top_level_category_name_ci` (name_key, where parent and account are
  NULL). An account's transfer category is left out of the top-level rule,
  because account names may repeat. The migration fills the key for
  existing rows. The audit log skips `name_key` (it only repeats `name`).
- [x] **2.4** Migration: `UniqueConstraint(Lower("name"),
  name="unique_payee_name_ci")`; then replace the three hand-written
  `name__iexact` lookups with one `Payee.objects.resolve(name)` (see 4.2).
  Payee and Category Add/Edit forms show "A payee/category with this name
  already exists" (Django's ModelForm validates `UniqueConstraint`s, so this
  mostly comes for free - check the message reads well). Test: typing "lidl"
  on a transaction when "Lidl" exists saves the payee as "Lidl".
  *Done 01/10/2026:* `unique_payee_name_ci` is on `name_key` (see 2.3).
  `Payee.objects.named(name)` finds, `Payee.objects.resolve(name)` finds or
  creates (audited), and the transaction form, the importer, autofill and
  `Payee.system()` all use them; the category lookups in the importer and
  the starter categories use `name_key` too. The friendly messages come
  from `Payee.clean()`/`Category.clean()`, because a constraint on a column
  the form doesn't show is skipped by form validation.
- [x] ~~**2.5** Transaction numbers (F7): decide between a unique constraint
  on (year, number) with retry-on-conflict, or accepting duplicates as
  user-editable data and documenting it.~~ **Dropped 27/09/2026:**
  duplicates are legitimate (editable numbers, Quicken imports), and
  `BEGIN IMMEDIATE` already serialises `next_number()`. Replaced by one
  comment on `next_number()` saying so (written 01/10/2026).
- [x] **2.6** Move the one-level-subcategory rule into `Category.clean()`
  (F13); `CategoryForm` keeps its friendly messages by calling through.
  *Done 01/10/2026:* `CategoryForm.clean_parent()` is gone; a ModelForm
  runs the model's `clean()` itself, so the form and the admin show the same
  messages. New ones the form could never trigger: a subcategory or a
  transfer category as the parent.
- [x] **2.7** Stashy.bat migrates every stash on launch - verify on a
  throwaway stash that the migration applies cleanly. *Done 01/10/2026:*
  migrated scratchpad copies of both stashes (integrity check ok, every key
  filled, a case-duplicate payee and a negative reminder refused), then
  deleted the copies. The real stashes are migrated by Stashy.bat on the
  next launch; both were backed up first (`backup_before_plan02_phase2_*`).
- [x] **2.8** Rules the admin can break (F54): `CheckConstraint`s for
  reminder amount ≥ 0 and interval ≥ 1, and "type is Transfer exactly when
  the category belongs to an account". `Account.clean()` refuses a currency
  change once transactions exist, and the admin form shows it read-only.
  Check the stashes for violating rows first (add these checks to 2.1).
  *Done 01/10/2026:* the reminder amount also has a field validator, so the
  form shows the message under Amount.
- [x] **2.9** Drop the dead `Transaction.payee_name` column (F51), and remove
  its uses in the Transactions search and the admin. Re-check that every
  stash has 0 non-empty values first (the real stash had 0 on 27/09/2026).
  *Done 01/10/2026* (0 in both stashes). The admin searches `payee__name`.

**Done when:** constraints exist in every stash; tests prove duplicates are
rejected at the database level.

## Phase 3 — Tooling and settings  (F21, F22, F25, F26, F27, F37, F52, F57, F58)

- [x] **3.1** Add `pyproject.toml` with ruff config: line length 119 (C1),
  rules `E, W, F, I, N, B, UP, DJ, SIM, C4, RUF`; exclude migrations.
  *Claude's call:* double quotes (the formatter's default); RUF012 (Django's
  class-level lists) and SIM108 (commented if/else beats a long ternary)
  ignored; the no-break space, en dash and Cyrillic "г" allowed as intended
  characters.
- [x] **3.2** Add ruff and `coverage` to `requirements-dev.txt` (both free,
  both run locally). Record the coverage baseline in DEVLOG.md before
  Phase 4, so the refactors can show they didn't lose any tested lines.
  Run `ruff check --fix` and `ruff format` as a **separate, formatting-only
  step** so behaviour changes never hide inside a reformat diff.
  *Done 02/10/2026: the reformat was committed on its own; baseline 89%
  (3,696 statements, 425 missed).*
- [x] **3.3** Fix what ruff can't: E731 lambdas → small `def`s, identical
  if/else in `TransferForm`, single-letter names in large scopes (F22).
- [x] **3.4** Requirements: `requirements.txt` = what the app imports;
  `requirements-dev.txt` = ruff + test-only packages (bs4/lxml if tests use
  them, otherwise uninstall). Remove `psycopg2-binary`/`dj-database-url` from
  the venv if nothing uses them (F26).
  *Correction found while doing it: `beautifulsoup4` and `lxml` are
  `ofxparse`'s own dependencies, so they stay (pip installs them with it).
  Only the two Postgres packages were removed.*
- [x] **3.5** Settings package: `stashy/settings/base.py` and `test.py`
  (changed 27/09/2026: no `dev.py`, since `.env` already carries the dev
  values). No insecure `SECRET_KEY` fallback; `DEBUG` defaults to False.
  `manage.py`/`bootstrap.py` use `base`; the test runner uses `test.py`
  (prepares Phase 7) (F25). `test.py` sets
  `PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]`
  (F52), which is safe because it's test-only. Record the new suite time.
  Delete `AUTH_PASSWORD_VALIDATORS` (F51, never applies to PINs).
  *Done: `manage.py` picks `test.py` for the `test` command. The suite went
  from 298 s to 15 s. Folder locations (`STASHES_DIR`, `STATIC_ROOT`,
  `LOG_DIR`) sit together at the top of `base.py` for plan 04.*
- [ ] **3.6** Repo hygiene (F27): *(`staticfiles/` and `logs/` ignored on
  02/10/2026; the `git rm` and the migrations question below still open)* add `staticfiles/` to `.gitignore`
  (`_picker.sqlite3` and `backups/` are already there; `all_data.xlsx` and
  `test_data.TXT` are gone). Untracking needs
  `git rm -r --cached staticfiles .idea`. **The user runs that** (Claude
  doesn't run git).
  **To discuss with the user once plan 02 is finished (their choice,
  01/10 and 02/10/2026):** migrations
  are kept out of git (`**/migrations/*.py` in `.gitignore`), so git only has
  `finance/migrations/0001_initial.py`. A fresh clone gets models that no
  committed migration builds. Options: commit them all, or keep them local
  and accept that the repo alone can't set up a new copy.
- [x] **3.7** `bootstrap.py` writes `DEBUG=False` into new `.env` files
  (F37). Existing `.env` files are the user's to change - tell them, don't
  edit them. Check that WhiteNoise still serves static files with
  `DEBUG=False` (it does from `STATIC_ROOT`, which Stashy.bat fills).
  **Must land together with 3.9 (F58)**, or errors stop being visible
  anywhere.
- [x] **3.8** `CompressedManifestStaticFilesStorage` (F57): file names carry
  a content hash, so the browser can cache CSS/JS for good and never runs an
  outdated script after an update. `test.py` keeps the plain storage, so
  tests don't need `collectstatic`. Render-check that every page still loads
  its CSS/JS.
  *Found while render-checking: Bootstrap and Chart.js end with a
  `sourceMappingURL` comment naming a `.map` file Stashy doesn't ship, and
  the plain storage made `collectstatic` (so every launch) fail on it.
  `stashy/storage.py` subclasses it to skip those comments; a test runs the
  real `collectstatic`.*
- [x] **3.9** Errors when `DEBUG=False` (F58): a `LOGGING` config writing
  errors with full tracebacks to `logs/stashy.log` (rotating, a few MB; add
  `logs/` to `.gitignore`), plus friendly `404.html` and `500.html` templates
  in Stashy's style. The 500 page says what happened and where the log is.
  Test: with `DEBUG=False`, a view that raises writes to the log and shows
  the friendly page.

**Done when:** `ruff check` is clean; suite green; app launches via
Stashy.bat.

## Phase 4 — Service layer  (F10, F11, F24, F53)

- [x] **4.1** `finance/services/importing.py`: move `_import_csv`,
  `_import_xlsx`, `_import_ofx`, `_import_rows`, `_resolve_category`,
  `_detect_date_format`, `_parse_date`, `_fingerprint`, `_guessed_category`.
  Public entry point: `import_file(upload, account, date_format) ->
  ImportResult` (a small dataclass). Drop the dead `ofxparse` ImportError
  branch (F24).
  *Done 02/10/2026. The dead branch was already gone. `ImportResult` also
  carries the Windows-1251 note, and `import_file()` runs in its own
  transaction, so it is safe to call outside a web request too. The summary
  message stays in the view, since it's wording.*
- [x] **4.2** One payee resolver, audited, used by the form, the importer and
  anywhere else (F11) - e.g. a `PayeeQuerySet.resolve(name)` manager method.
  *Mostly done in 2.4 (01/10/2026):* `Payee.objects.resolve()` exists and
  every caller uses it. Left for here: the importer moves into the service.
  *Done with 4.1.*
- [x] **4.3** `finance/services/reconciliation.py`: `reconcile(account,
  statement_date, statement_balance, transaction_ids, accept_difference)`.
  *Claude's call: it takes the Transaction objects, not ids. The finish
  form has already checked they belong to the account and aren't
  reconciled yet, and taking ids would mean checking that twice.* A gap
  without "accept" raises `UnacceptedDifferenceError` (carrying the
  amount), and nothing is saved.
- [x] **4.4** `Reminder.mark_entered(transaction)` and `Reminder.skip()` on
  the model; `transaction_form_view` and `reminder_skip` call them (F10).
  *Also moved: the "whole series" edit (`Reminder.apply_to_series()`, was
  `views._propagate_series_edit`). Entering a recurring reminder no longer
  saves a link and then clears it straight away: two audit rows that
  cancelled each other out. The "moved it to <date>" messages now follow the
  stash's date format instead of a hard-coded DD/MM/YYYY.*
- [x] **4.5** One helper for the Watched-expenses picker context, used by
  both Settings and the welcome wizard (F11).
  *`forms.watched_expenses_picker_context(profile)`.*
- [x] **4.6** Replace `_safe_next()` with `url_has_allowed_host_and_scheme`;
  move the upload extension check to `FileExtensionValidator` (F24).
  *`_safe_next()` already calls Django's check since Phase 1 (F55). It stays
  as a 3-line wrapper because its three callers all need the request's own
  host. A wrong file type is now a form error shown under the File field
  (before, it was a message at the top after a redirect). The Import page
  now shows every field's errors, which it never did.*
- [x] **4.7** Services get their own unit tests (no HTTP), existing view
  tests stay as they are.
  *`finance/test_cleanup_phase4.py`, 28 tests. They include the first
  tests of a successful XLSX and OFX import.*
- [x] **4.8** `TransactionQuerySet` (F53): `.in_range(start, end)`,
  `.reportable()` (has a category, not a transfer), `.of_kind(expense_only,
  income_only)`, `.for_account(account)`. Every `reporting.py` function builds
  its queryset from these, and one `_rollup_to_parents()` replaces the copied
  roll-up block. Totals stay Python `Decimal` sums, so every number stays
  exactly the same.
  *Verified: a throwaway probe saved the output of every report function
  (two currencies, refunds, transfers, uncategorized rows, subcategories,
  every kind/subtotal/account combination) before and after: 140 results,
  byte-for-byte identical. The vs-average table used SQLite's float `SUM`
  when there was one currency. It now sums `Decimal`s like everything else,
  which can't differ after rounding to cents. The probe also showed a quirk
  that was kept as is: plan 03, R2.*
- [x] **4.9** Type hints on the new service modules' public functions (moved
  from the backlog). Old code gets hints only when it's touched anyway.

**Done when:** no business rule remains in a view function body; suite green.
*Left in the views on purpose: reactivating an inactive account when you
enter a transaction on it (three lines that belong to the "are you sure?"
step), and the dashboard's "due soon" filter (only what the page shows).*

## Phase 5 — Split the views module  (F4, F9, F15, F16, F49)

- [x] **5.1** `finance/views/` package: `dashboard.py`, `accounts.py`,
  `categories.py`, `payees.py`, `transactions.py`, `reminders.py`,
  `imports.py`, `exports.py`, `reports.py`, `settings.py`, `api.py`, plus
  `_helpers.py` (`_sorted`, export response builders). Move `auth_views.py`
  and `welcome_views.py` in as `views/auth.py`, `views/welcome.py`.
  `views/__init__.py` re-exports so `finance/urls.py` changes minimally.
  URL **names** stay identical (templates and tests depend on them).
  *Done 03/10/2026. Claude's call: no re-exports in `views/__init__.py`.
  `finance/urls.py` imports the modules (`accounts.account_detail`), so the
  URL list shows which file each page is in, and a new view isn't listed in
  two places. Report exports went into `exports.py` with the list exports,
  which kept `reports.py` under 400 lines. The largest module is now
  `reports.py` at 370 lines (was one file of 2,035). History notes in the
  moved code's comments were dropped on the way (8.5, file by file).*
- [x] **5.2** Report exports (F15): one registry dict
  `{slug: (builder, table_kind, filename)}`, one view, one URL
  `reports/export/<slug:report>/`. Keep the old URL names as aliases if
  templates reference them, or update the templates. `_export_rates` (F4,
  from 1.5) becomes a plain helper inside that one view.
  *Done. `exports.REPORT_EXPORTS` maps each slug to a builder, a layout,
  the file name and the amount column's label. The slugs are the old URL
  paths, so every export address is unchanged; only the eight URL names
  became one (`finance:export_report`). Templates and tests updated, no
  aliases. `_export_rates` is gone: the view makes the `RateTable` itself.*
- [x] **5.3** Settings (F16): separate POST endpoints -
  `settings/rates/check/`, `settings/backups/create/`,
  `settings/watched-expenses/`, `settings/` (preferences). Each redirects
  back to Settings. Update the template's form `action`s.
  *Done. URL names `check_rates`, `create_backup`, `save_watched_expenses`;
  each takes POST only. An invalid Watched-expenses form shows the Settings
  page with its errors, as before. Only `check_rates` and `create_backup`
  still run outside the request's transaction, so `_stash_atomic()` is
  gone.*
- [x] **5.4** Move the `*_TIPS` text out of Python into the templates (or a
  `finance/content.py`), since it is presentation.
  *Claude's call: `finance/content.py`. Six forms share `generic_form.html`,
  so templates would have needed one include per page.*
- [x] **5.5** Render-check every page touched (test client, read the HTML).
- [x] **5.6** Login by default (F49): add Django's `LoginRequiredMiddleware`
  after `AuthenticationMiddleware`, mark the picker, stash create, switch,
  login and forgot-PIN views `@login_not_required`, and remove the per-view
  decorators and mixins. The middlewares' `EXEMPT_PREFIXES` come from
  `reverse()`/settings instead of hard-coded strings. Test: every URL in
  `finance/urls.py` redirects an anonymous client to the login page.
  *Done. Also public: the admin-login redirect and logout (an expired
  session still logs out cleanly). `StashLoginView` needs the mark again
  through `method_decorator`, because it overrides `dispatch()`, where
  Django's `LoginView` keeps it. Without it the login page redirected to
  itself; the suite caught it. The exempt prefixes are built by
  `exempt_prefixes()` and a test checks they equal the old strings. Checked
  on a test server (port 8765, `test` stash, GET only): no stash → picker,
  stash chosen → PIN page, every app page → PIN page with `?next=`.*

**Done when:** no views module over ~400 lines; suite green; render-check
clean.

## Phase 6 — Side effects and boundaries  (F12, F14, F17, F18, F35, F36, F38, F50)

Done 03/10/2026. Tests: `finance/test_cleanup_phase6.py` (27).

- [x] **6.1** Make `Account.save()`'s transfer-category sync explicit: only
  when created or when `name` changed (skip on `update_fields` that don't
  include `name`), or move it to the account-creation/rename service.
  *Done in `save()` itself: `from_db()` remembers the saved name, and the
  sync runs only for a new account or a changed name that is being saved.*
- [x] **6.2** `Reminder.save()`: regenerate occurrences only when a schedule
  field changed (frequency, interval, next_due_date, end_date,
  end_of_period), not on every save (F12).
  *Claude's call: a save with `update_fields` regenerates only if one of
  them is in `Reminder.SCHEDULE_FIELDS`; a full save (every form and admin
  edit) still always regenerates, because that's also what moves the
  "90 days or year-end" horizon forward as time passes. Skipping it would
  have been a visible change.*
- [x] **6.3** Audit app exports `audit.suspended()` (context manager owning
  its own dispatch_uids); `stashes.create_stash()` uses it (F17).
  *Claude's call: `audit.context.audit_suspended()` sets a flag on the
  audit context (a ContextVar) that the handlers check, instead of
  disconnecting them. Disconnecting is process-wide: while one request
  built a new stash, every other request's changes went unlogged too.*
- [x] **6.4** `db_router`: `threading.local` → `ContextVar`, same as
  `audit.context` (F18). Low priority: waitress is a threaded, synchronous
  server, so `threading.local` is correct today. This only makes the two
  mechanisms consistent.
- [x] **6.5** Split `stashes.py` into `stashes/registry.py` (folders, aliases,
  create) and `stashes/security.py` (PIN, lockout, recovery codes) (F14).
  *`StashError` lives in `stashes/__init__.py`. `registry` uses `security`,
  never the other way round. `create_stash()` and `reset_pin_directly()`
  are now tested on a real temporary stash.*
- [x] ~~**6.6** Q2: optionally move lockout/recovery fields to a
  `StashSecurity` model (needs a migration + backup).~~ Dropped 27/09/2026,
  see Q2.
- [x] **6.7** `PinField` (4-digit validation) and a `PinConfirmMixin` in
  `auth_forms.py`; `stashes.validate_pin()` reuses the same validator (F35).
  *Claude's call (polish): every bad PIN now gets the one message "PIN must
  be exactly 4 digits.", where a too-short one used to get Django's "Ensure
  this value has at least 4 characters". The rule also rejects a trailing
  line break and non-ASCII digits.*
- [x] **6.8** Break the audit → finance import (F36): the audit admin formats
  timestamps with its own helper or a setting-provided callable, not
  `finance.regional`.
  *Its own helper, `audit.admin.format_timestamp()`; it never used a
  finance preference, only the active time zone.*
- [x] **6.9** One rule for a new subcategory's type (parent's type, as the
  importer does) shared by `starter_categories.py` and the import service;
  exclude transfer categories from the starter lookup (F38).
  *`Category.create_subcategory()` and `Category.objects.main_categories()`.
  The shipped `starter_categories.xlsx` has no row whose type differs from
  its main category, so new stashes get the same categories as before.*
- [x] **6.10** Remove the import cycles instead of hiding them (F50):
  `possessive()` moves to a small `finance/text.py` that both `models` and
  `stashes` import. After 6.5, `stashes/security.py` imports models,
  `get_user_model` and the hashers at the top. Any function-level import that
  remains gets a one-line comment saying why it has to stay (for example,
  `backup.py` must stay standard-library only).
  *openpyxl, ofxparse and tzlocal moved to the top too. Four stay inside
  functions, each with a "# Imported here: ..." comment (the two `apps.py`
  `ready()` imports, `winreg`, and `currency.py`'s `ExchangeRate`, since
  `models` imports `currency`). A test checks the comment is there.*

**Done when:** a test proves a plain `update_fields` save doesn't run the
side effect; suite green. ✔ (`AccountSaveTests`, `ReminderSaveTests`)

## Phase 7 — Tests restructure  (F19, F20, F52)

Part 1 done 04/10/2026 (7.1-7.3, and 7.4 for the shared base classes).
Part 2 done 05/10/2026 (the rest of 7.4). Phase 7 is done.

- [x] **7.1** Remove every `settings.TESTING` branch from production code.
  Tests instead create a throwaway stash (temp dir via
  `override_settings(BASE_DIR=...)`/`STASHES_DIR`) and select it in the
  session, so the real middleware runs. `finance/apps.py`'s own `sys.argv`
  check goes too (the test settings module handles it).
  *Claude's call on how: `settings/test.py` makes a new temporary
  `STASHES_DIR` per run holding one stash, `TEST_STASH` ("tester"), which
  `apps.py` registers like any stash; its database is the test runner's
  in-memory one. `StashTestCase` (`finance/tests/base.py`) sends the test's
  own queries there and its client starts with that stash chosen. `default`
  is off limits to these tests unless a class asks for it, so code that
  forgets its stash fails loudly. `db_router.use_stash()` puts back the
  previous stash after a request instead of clearing it. A test run started
  with the app's own settings stops at once (`finance/tests/__init__.py`).
  `TESTING` is gone everywhere, the test settings included.*
- [x] **7.2** Add tests for the middleware chain itself: no stash → picker;
  remembered cookie → PIN page; unfinished setup → wizard.
  *`MiddlewareChainTests` (6), through a real test client; also picker +
  PIN → dashboard, and no query on `default` once a stash is chosen. Each
  fails if its middleware is switched off (checked).*
- [x] **7.3** Move tests into `finance/tests/` by feature (`test_accounts.py`,
  `test_reminders.py`, `test_import.py`, …), mirroring Phase 5's modules.
  Move, don't rewrite: every existing test keeps its assertions. Record the
  old → new file mapping in DEVLOG.md.
  *23 modules named after the code they test (so `test_imports.py`, after
  `views/imports.py`). A syntax-tree comparison proved every test method
  unchanged apart from listed edits (DEVLOG 04/10/2026). Claude's calls: the
  25 copies of `LoggedInTestCase` became shared bases in `base.py`; the four
  audit-log tests that lived in finance moved to `audit/tests.py`.*
- [x] **7.4** While moving (F52): classes whose tests only read shared data
  switch their fixtures from `setUp` to `setUpTestData`. Record the suite
  time before and after. *(02/10/2026: the fast test hasher in 3.5 already
  took the suite from 298 s to 15 s, so this is now about tidiness more
  than speed.)*
  *04/10/2026, part 1: the shared bases (`LoggedInTestCase` and its two
  subclasses, used by about 130 classes) build their data in
  `setUpTestData`; 23.0 s → 22.4 s. Still to do: the 67 classes with their
  own `setUp`. Claude's call: a class whose tests change that data
  qualifies too, because Django undoes each test's database changes and
  gives every test its own copy of the objects.*
  *05/10/2026, part 2: 58 of the 67 classes build their data in
  `setUpTestData`; logging in, temp files and mocks stay in `setUp`. The
  other 9 have nothing to move: 7 are `SimpleTestCase` (no database, so no
  `setUpTestData`), and 2 only make a temp file and a mock. A syntax-tree
  comparison proved all 683 test methods unchanged; the suite also passes
  in reverse and shuffled order. Median of three alternating runs: 25.2 s →
  24.3 s.*

**Done when:** `grep -r "TESTING" finance audit stashy` finds only the test
settings; test count is the same or higher than before. ✔ No app code
mentions `TESTING` (a test in `test_project.py` checks); 675 → 683 tests.

## Phase 8 — Polish and performance  (F8, F23, F29, F34, F39, F40, F48, F51, F56)

- [ ] **8.1** Replace `date.today()` with `timezone.localdate()` everywhere
  in app code (forms' `initial=date.today` → a small callable); messages use
  `regional.format_date()` instead of `%d/%m/%Y` (F8).
- [x] ~~**8.2** Reports (F28): aggregate in the database grouped by
  `(category, account__currency)` (and period bucket where needed), then
  convert the grouped totals.~~ Moved to the backlog on 27/09/2026 (see
  "Plan review" above). 4.8 does the readability part.
- [ ] **8.3** Import (F29): load existing fingerprints into a set once per
  import instead of one query per row.
- [ ] **8.4** Views read `request.stashy_profile` instead of calling
  `Profile.for_user()` again (F29).
- [ ] **8.5** Comments (F23, C2): move history notes to DEVLOG.md, keep the
  "why" of the current code. Do it file by file, alongside other edits to
  that file, not as one giant pass.
  *03/10/2026: done for everything in `finance/views/`, `finance/middleware.py`
  and `stashy/urls.py` during Phase 5.*
- [ ] **8.6** Net worth: one query grouped by (account, date) plus a running
  sum per column, instead of accounts × periods queries (F34).
- [ ] **8.7** `generic_form.html`: `unit_words_json|safe` → `json_script`
  (F39).
- [ ] **8.8** Fix the dead `None` branch in
  `category_vs_average_report_rows()` totals and decide what the total
  average means when some rows have none (F40) - agree with the user, it's
  a number on screen. **Resolved 27/09/2026 (Claude's call), no question
  needed:** a row has no average only when it had no activity in *any* earlier
  period, so it adds 0 to every earlier period's total. Summing the averages
  that exist therefore already equals the average of the earlier periods'
  grand totals, which is the right number. So: delete the dead `None` checks,
  add a comment saying why, and add a test proving the total equals the
  average of the period totals. Nothing on screen changes.
  *03/10/2026: plan 03's R6 (done the same day: a category with no earlier
  activity averages 0) leaves `None` only for "no earlier periods at all".
  What remains of 8.8: the test proving the total equals the average of the
  period totals.*
- [x] ~~**8.9** Period labels (`%b` in `period_bucket()`) depend on the OS
  locale; use a fixed English month table like the rest of the UI.~~
  Dropped 27/09/2026: nothing sets a locale, so `%b` is always English.
- [ ] **8.10** Transactions page weight (F48). The slowness is in View mode
  too: every row always carries its hidden Edit-mode controls (six inputs
  and two `<select>`s, one with about 150 categories), whichever mode is
  showing. Fix: rows render only their View cells plus `data-` attributes
  with the raw values. One shared `<template>` holds the controls, and
  `transaction-quick-edit.js` builds each row's controls from it when Edit
  mode turns on. Looks and works the same. Render-check it, and record the
  page size and load time before and after on "Earliest to date" in DEVLOG.
- [ ] **8.10b** Edit mode locks the filters (user, 27/09/2026). While Edit
  mode is on, the filter bar (range, dates, status, account, categories,
  search, sort headers) and the page links from 8.11 are disabled, with a
  short hint: "Finish editing to change filters". Today nothing locks them:
  changing one reloads the page and silently leaves Edit mode, and a field
  still being typed in can be lost.
- [ ] **8.11** Transactions pagination (Q3): Django's `Paginator` with 1,000
  rows per page, only when the range is longer than a year (or unbounded).
  Sorting happens in the database before paging, so page 2 continues the same
  order. The total is still computed on the whole filtered queryset. Page
  links keep every filter and sort parameter. Export still exports everything
  the filter matches. The account register (`account_detail`) follows the
  same rule. Tests: a range over a year with 1,001 rows gives two pages and
  the same total on both; a one-year range never paginates. Render-check.
- [ ] **8.12a** Do 8.10 and 8.11 early if the page keeps getting slower.
  Neither depends on the phases before it.
- [ ] **8.13** Transactions range list: keep "All time" and remove "Earliest
  to date" (user, 27/09/2026, changed the same day from the opposite). "All
  time" has no date limits, so it also shows a transaction dated in the
  future by mistake, and nothing can hide. **Reports keeps "Earliest to date"
  (Claude's call):** it has no "All time", and its charts need a real start
  date. An old bookmark with `range=earliest` on the Transactions page lands
  on "All time". For 8.11, "All time" counts as longer than a year.
  **Answered 01/10/2026 (user):** the option is renamed **"All dates"** (on
  Transactions; the URL value can stay as it is). Everything above that says
  "All time" means this option.
- [ ] **8.14** Typing a Start/End date switches the Range dropdown to "Custom
  range" (user, 27/09/2026). Today, a typed date is silently ignored
  whenever a preset is selected. The switch happens without reloading the
  page. The page updates **only when Filter (Transactions) or Apply (Reports)
  is clicked**, never while you're still typing or correcting a date.
  Changing the Range dropdown or another dropdown keeps reloading straight
  away, as it does now. **Same on Reports (Claude's call):** it has the same
  two fields and the same trap. Tests cover the server side (a custom
  start/end is applied); render-check both pages.
- [ ] **8.15** Small repeated queries (F56): the dashboard's "due soon" list
  and the reminder list load each reminder's payee and category, and the
  category's parent, one row at a time. Add `select_related("payee",
  "category__parent")`. `export_accounts` and the admin's account list
  compute each balance with its own query; use `with_balances()`.
- [ ] **8.12** Dead code (F51): delete `scripts/load_test_data.py` (its
  input file is gone) and `reporting.category_totals()` together with the
  tests that only cover it.

**Done when:** reports produce identical numbers; suite green.

---

## Backlog (noticed, not planned)

- ~~Type hints on service-layer functions~~ - now 4.9.
- Reports aggregating in the database (was 8.2, F28). Do it only if Reports
  is measured as slow, and then sum in the database *per currency* but keep
  the conversion and rounding in Python, and compare every number on a copy
  of real data.
- The audit log grows forever (about 5,000 rows in the real stash on 27/09/2026). That's
  fine for years at this rate. Add a "prune older than N years" command only
  if the file size ever matters.
- A 4-digit PIN with 5 tries / 5 minutes is fine for a local app, but worth
  revisiting if Stashy is ever reachable from another machine.
- The import fingerprint (account + date + amount + payee) can't tell two
  honestly identical purchases apart in one file - see DEVLOG 16/09/2026.
- The audit log's `pre_save` handler runs one extra SELECT per update of a
  tracked model, so bulk actions (mass re-categorize) cost two queries per
  row. Fine at today's sizes; revisit if a bulk action ever feels slow.
- `transaction_list`'s total adds mixed currencies unconverted (a known,
  commented simplification) - could reuse the F30 conversion later.
