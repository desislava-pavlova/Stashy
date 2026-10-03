# CLAUDE.md — working on Stashy

This file is for Claude: the rules, map and pitfalls of this project.
`DEVLOG.md` is for the user: what changed and *why*, written to teach.
`plans/` holds what is planned. Keep all three current. Don't copy one
into another. `CLAUDE.local.md` (gitignored) holds facts true only on the
user's computer: which stash holds real data, and the port. Read it too.

## What Stashy is

A local, single-user personal finance app, a simplified Quicken with a
squirrel mascot, aimed at younger users. It uses Django 6.1 on Python 3.12
with SQLite, and Waitress serves it (started by `Stashy.bat`). Each
**stash** is a fully separate database with its own 4-digit PIN and owner
name. The user is learning from this project, so explain the "why" in plain
terms.

## Hard rules

- **Stay inside this folder** (the scratchpad is fine for throwaway scripts).
  **Never run git on your own**, not even read-only commands that change
  state. The one exception: when the user says "go commit", stage the
  listed files and commit them with the agreed message (since 03/10/2026).
  Push only when the user asks for it. Never run any other git step: tell
  the user the exact command instead.
  **Nothing that costs money.** Free PyPI and CDN downloads are fine.
- **One stash holds real data** (named in `CLAUDE.local.md`). Back it up
  before any schema change or data fix with
  `finance.backup.backup_now(<stash dir>/db.sqlite3, <stash dir>, tag="before_…")`
  (it names the file `<stash>_backup_<tag>_<time>`, so two stashes never get
  the same name; the user asked, 03/10/2026), or use Settings → Back up now.
  Never a plain file copy. Never write to it for a
  demo. The `test` stash is disposable.
- **Everything committed stays free of personal data.** `DEVLOG.md`,
  `plans/`, `README.md`, this file, code comments and tests never name real
  balances or totals, payees, banks, places, account names or the real
  stash/owner. Write "the real stash", and use made-up examples ("Alex",
  "Lidl", "Groceries"). Personal specifics go in `CLAUDE.local.md` or
  nowhere.
- **Never rerun `scripts/load_test_data.py`.** It wipes a stash (plan 02
  deletes it; its input file is already gone).
- **Don't kill running processes.** The user's own Stashy runs on the
  `PORT` from `.env` (see `CLAUDE.local.md`). Test servers use a spare
  port, e.g. 8765.
- `.env` is the user's own file. Suggest changes to it, never make them.

## Commands

```
.venv\Scripts\python.exe manage.py test                          # full suite, ~20 s (633 tests on 03/10/2026)
.venv\Scripts\python.exe manage.py test finance.test_phase15     # one module
.venv\Scripts\python.exe manage.py migrate --database <stash>    # one stash (Stashy.bat does all, with backups)
.venv\Scripts\python.exe manage.py reset_pin <owner name>        # real, visible action - ask first
.venv\Scripts\ruff.exe check .  /  ruff.exe format .      # must stay clean (pyproject.toml)
.venv\Scripts\coverage.exe run manage.py test; .venv\Scripts\coverage.exe report
```

- `Stashy.bat` → `scripts/bootstrap.py`: install requirements, create
  `.env`, back up and migrate every stash, run `collectstatic`, take the
  quarterly backup, then serve with Waitress. It opens the browser at
  `http://stashy.localhost:<PORT>/` (browsers resolve `*.localhost` themselves,
  and `base.py` always allows that name). Python can't resolve it, so code
  that calls the server itself uses `127.0.0.1`.
- Waitress doesn't auto-reload, so remind the user to restart Stashy.bat
  after code changes.
- A plain `manage.py shell` sees the empty placeholder database. To reach a
  stash, run first: `from finance import stashes, db_router;
  stashes.ensure_registered('test'); db_router.set_current_stash_alias('test')`.
- To read real data safely, open SQLite read-only:
  `sqlite3.connect('file:stashes/<stash>/db.sqlite3?mode=ro', uri=True)`.

## Architecture map

- `stashy/`: `settings/base.py` (the app; every folder path is named once
  at its top: `STASHES_DIR`, `STATIC_ROOT`, `LOG_DIR`) and `settings/test.py`
  (picked by `manage.py test` itself: MD5 hasher, plain static storage, no
  log file). Also URLs, `storage.py` (fingerprinted static names that skip
  the vendored `.map` references) and `formats/en` (European formats).
- `DEBUG` defaults to off. Errors go to the console and to `logs/stashy.log`
  (rotating). `finance/templates/404.html` and `500.html` are the friendly
  pages; the 500 page gets no context, so it uses only static files and
  plain links.
- `finance/`: the whole app.
  - `models.py`: Profile, Account, Category, Payee, Transaction,
    ImportBatch, Reminder, ExchangeRate, Reconciliation.
    `TransactionQuerySet` (`in_range`, `reportable`, `of_kind`,
    `for_account`) is what every report query is built from. The reminder
    rules live on the model: `mark_entered()`, `skip()`,
    `apply_to_series()`.
  - `services/`: `importing.py` (`import_file()` → `ImportResult`, raises
    `ImportFileError`) and `reconciliation.py` (`reconcile()`, raises
    `UnacceptedDifferenceError`). Plain functions, no HTTP. Each runs in its
    own `atomic(using=<stash>)`.
  - `views/`: one module per area (`dashboard`, `accounts`, `categories`,
    `payees`, `transactions`, `reminders`, `imports`, `exports`, `reports`,
    `settings`, `api`, `auth` for picker/login/forgot-PIN, `welcome` for the
    4-step wizard), plus `_helpers.py` (`_sorted`, `_safe_next`, the export
    writers). `finance/urls.py` imports the modules, not the views; there
    are no re-exports in `views/__init__.py`.
  - Report exports: one view, `exports.export_report`, at
    `reports/export/<slug>/`. A new one is one line in `REPORT_EXPORTS`.
  - Settings: the page saves preferences; `check_rates`, `create_backup`
    and `save_watched_expenses` are their own POST URLs.
  - `content.py`: the help-card tips (`*_TIPS`).
  - `forms.py`, `auth_forms.py`.
  - `reporting.py`: all report maths. `currency.py`: RateTable,
    Frankfurter. `money.py`: Decimal helpers, `parse_amount`.
    `regional.py`: date and number formats per user.
  - `stashes.py`: stash folders, aliases, PIN and lockout, and
    `restore_stash()` (restore + migrate). `db_router.py`, `middleware.py`,
    `backup.py` (standard library only, since bootstrap imports it before
    Django; copies go through SQLite's backup API, never a file copy).
  - `starter_categories.py` reads `starter_categories.xlsx`.
  - `templatetags/stashy_format.py`: the `money`, `moneyc`, `sdate` and
    `diff_tint` filters.
- `audit/`: signal-based audit log (every save and delete of finance
  models). `audit.context.system_action(reason)` marks automatic changes.
- **Multi-stash routing:** the session's `stash` → `StashSelectionMiddleware`
  → thread-local → `StashRouter`.
  - `default` is `_picker.sqlite3`, an empty placeholder.
  - Every stash is its own connection alias named after its folder.
  - Sessions are signed cookies, because they must work before a stash is
    chosen.
  - `transaction.atomic()` and `on_commit()` go to `default` unless given
    `using=<stash alias>`; use `router.db_for_write(Model)`. The router
    doesn't choose a transaction's connection.
  - Stash aliases have `ATOMIC_REQUESTS=False`. `StashAtomicRequestMiddleware`
    (last in the chain, in `process_view`) wraps the view in a transaction on
    the *active* stash only. A view that must not hold the write lock (network
    calls, backup, restore) uses `@transaction.non_atomic_requests` and wraps
    any writes of its own in `atomic(using=...)` (see `check_rates`).
- **Login by default:** Django's `LoginRequiredMiddleware`. A page that must
  work without a login (picker, PIN login, forgot PIN, logout) is marked
  `@login_not_required`. A class view that overrides `dispatch()` loses the
  mark its parent had, so give it again with `method_decorator`. A new view
  needs no login decorator.
- Middleware chain: StashSelection → Auth → LoginRequired → RequireStash (picker or the
  last-stash cookie) → AuditContext → RegionalPreferences (sets
  `request.stashy_profile`) → RequireSetup (welcome wizard) → … →
  StashAtomicRequest.
- Exports: pass real values (`Decimal`, `date`, `None`) to
  `_export_response()`, never `str()`. The writers format them, escape
  formula-like text in CSV and keep text as text in XLSX.
- `/admin/login/` redirects to the stash PIN login (`admin_login`), so the
  lockout covers every login. Secrets shown once (a new recovery code) go in
  the local-memory cache, never the signed-cookie session.
- Tests: `finance/test_phase*.py`, `finance/test_*.py`, `audit/tests.py`.
  `settings.TESTING` (True only in `settings/test.py`) currently bypasses
  the stash and setup middleware. `finance/apps.py` still checks `sys.argv`
  itself, so a test run never registers the real stashes. Plan 02 phase 7
  removes both.
  `test_phase13.py` belongs to Phase 12.1; Phase 13's tests are in
  `test_phase13_reports.py`. Plan 02's tests are `test_cleanup_phase<N>.py`.
  `TemporaryStashesMixin` (in `test_cleanup_phase1.py`) gives a test real
  stash aliases in a temp folder, registered the way the app does it.

## Domain rules (settled with the user; don't change them silently)

- Money is always `Decimal`. SQLite sums decimals as floats, so quantize
  aggregates with `money.to_cent()`. Floats only at the JSON/chart boundary.
- An opening balance is a real transaction (D1), so a balance is simply the
  sum of transactions. Every account owns one `[Account name]` Transfer
  category (D7).
- Transfers and opening balances never count as income or spending. **A
  category's type decides income vs. spending, never the amount's sign**
  (R1).
- Transaction numbers are one sequence shared by every account and restart
  each year (D9). They are editable, and duplicates are allowed.
- Payees and categories that differ only in upper/lower case are the same
  one. The spelling already saved wins (decided 27/09/2026). The database
  enforces it through a hidden `name_key` column (`models.name_key()`, a
  `casefold()`, set by `save()`). Look names up with
  `Payee.objects.named()`/`.resolve()` or `name_key=name_key(x)`, never
  `name__iexact` or `Lower()`: SQLite folds only A-Z, so Cyrillic slips past.
- The one-level subcategory rule and the duplicate-name messages live in
  `Category.clean()`/`Payee.clean()`, so forms and the admin share them.
- Reports convert through `RateTable` (main currency, latest known rate).
  The vs-average reports need at least two earlier periods ("No earlier
  data to compare with." otherwise). Within them, empty periods count as
  real zeros, and a category with no earlier activity averages **0**
  (plan 03 R6, 03/10/2026, replacing the 16/09 "no average" rule). Rows
  carry `no_earlier_activity`: the page shows "—" for that average but
  fills in the difference and draws the dot at 0, and the export writes
  0.00. For a main category, its subcategories' earlier activity counts.
- Code that changes audited data goes through model `save()`/`delete()`,
  never `queryset.update()`/`bulk_create()`, and automatic logic runs inside
  `system_action("reason")`.

## UI conventions

- Dates are DD/MM/YYYY, weeks start on Monday, amounts look like
  `10 000.50`, and the time zone is Europe/Sofia. These are defaults: each
  stash's Profile can override them, so always format through `regional` or
  the template filters (`sdate`, `moneyc`), never with a hard-coded
  `strftime`. Show times only where they matter (import history, audit
  log).
- **Each page keeps its own agreed filter defaults.**
  - Transactions: "This year".
  - Reports: This month · no subtotal · Last 3 periods · All accounts.
  - Reminders: "This month".
  Never change or harmonize them without agreement.
- Charts colour by meaning: green income, red expense, blue net
  (`--stashy-net`). Stacked pieces are split by `--stashy-chart-line`, which
  also draws the average dots. There's no category palette except where
  series need their own identity (Watched-expenses lines, the dashboard
  donut). A category with no earlier activity gets its average dot at 0
  (R6); only the "No earlier data" case draws no dots.
- The report titles in `reports.html` are the user's own wording. Never
  rewrite them.
- Use `stashy.css` tokens only. Everything must work in light and dark mode,
  and no asset may load from the internet (a test enforces this).
  Popovers open on hover and focus.
- Polish (empty states, copy, spacing, symbols vs. codes) is Claude's to fix
  without asking. Ask only about genuine forks that change behaviour or data.

## How the user works with Claude

- **Plans:** `plans/NN_*.md` are numbered and never deleted, and
  `plans/README.md` indexes them. `plans/` and `DEVLOG.md` are committed
  (since 27/09/2026), so the personal-data rule above applies to them.
  - 01, upgrade plan: done.
  - **02, architecture cleanup:** F1–F58 findings, 8 phases, all questions
    answered. Status is at the top of the file.
  - **03, fixes and changes plan:** collects the user's bugs and changes
    while 02 runs. "Add to 03: …" means file it and don't start it, unless
    the user says "fix R<n> now".
  - **04, Windows .exe packaging:** idea stage, starts after 02. Don't
    start it, but keep new folder paths (data, logs) defined in one place
    in the settings, so moving them out of the program folder stays easy.
- **Big requests:** fold them into the plan first, and ask genuine forks
  as a short numbered list. Implement only once the user names the phase.
  When the user hands a technical choice back ("your call"), decide it, mark
  it "Claude's call" with a one-line reason in the plan, and mention it
  once.
- **After each piece of work:**
  - Tick the plan's checkboxes.
  - Add a DEVLOG.md entry: what changed, why, and a short teaching note.
  - Keep README.md accurate for anything the user can see.
  - Update this file if the architecture or the rules changed.
  - List the new files the user needs to `git add`. Migrations are kept
    out of git for now (the user's choice, 01/10/2026; `.gitignore` has
    `**/migrations/*.py`), so don't list them. Discussion parked in plan 02,
    item 3.6.
  - Report, and let the user choose what comes next. The report ends with
    **"What to check"**: every change the user can see or should try
    themselves (page, what to do, what they should see). Write "nothing
    visible" when that's true. (Asked for 03/10/2026.)
  - Suggest a commit: a short **Summary** line and a short **Description**
    (Copilot-style: one sentence or a few short bullets). The description
    starts with "Plan N, Phase M:", plus ", Part X" only when the phase
    really was split into parts. Never add a `Co-Authored-By` line. List
    the exact files the commit will include right with the message. Run
    `git` only after the user says "go commit" (see the git rule above).
- **The user writes dates as DD/MM/YYYY**, and so should plans and DEVLOG.

## Verification (there is no browser here)

- Run the full suite and report its real result, and keep `ruff check .`
  and `ruff format --check .` clean.
- A change to static files or `STORAGES` needs the real `collectstatic`
  (Stashy.bat runs it at every launch; if it fails, Stashy doesn't start).
  Never into the live `staticfiles/`: use a scratch `STATIC_ROOT`.
- For front-end work, fetch the rendered HTML with the test client and read
  it. Look for leaked `{#`, `#}`, `{%` or `%}` (Django's `{# #}` comments
  are single-line only; a multi-line one renders as visible text). Never
  claim a visual result: say it's untested visually and ask the user to
  look. Bootstrap's table-cell background beats a lone class, so scope
  rules like `.table td.x`.
- "Everywhere" claims need a grep for the raw pattern (e.g.
  `Category.objects`), not just for callers of the shared helper.
- Line numbers in plans drift. Search by function name.
