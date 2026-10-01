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
  **Never run git**, not even read-only commands that change state. The
  user runs any git step themselves, so tell them the exact command.
  **Nothing that costs money.** Free PyPI and CDN downloads are fine.
- **One stash holds real data** (named in `CLAUDE.local.md`). Back it up
  before any schema change or data fix: copy its `db.sqlite3` into its own
  `backups/` folder, or use Settings → Back up now. Never write to it for a
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
.venv\Scripts\python.exe manage.py test                          # full suite, ~5.5 min (540 tests on 01/10/2026)
.venv\Scripts\python.exe manage.py test finance.test_phase15     # one module
.venv\Scripts\python.exe manage.py migrate --database <stash>    # one stash (Stashy.bat does all, with backups)
.venv\Scripts\python.exe manage.py reset_pin <owner name>        # real, visible action - ask first
```

- `Stashy.bat` → `scripts/bootstrap.py`: install requirements, create
  `.env`, back up and migrate every stash, run `collectstatic`, take the
  quarterly backup, then serve with Waitress.
- Waitress doesn't auto-reload, so remind the user to restart Stashy.bat
  after code changes.
- A plain `manage.py shell` sees the empty placeholder database. To reach a
  stash, run first: `from finance import stashes, db_router;
  stashes.ensure_registered('test'); db_router.set_current_stash_alias('test')`.
- To read real data safely, open SQLite read-only:
  `sqlite3.connect('file:stashes/<stash>/db.sqlite3?mode=ro', uri=True)`.

## Architecture map

- `stashy/`: settings (a single file for now; plan 02 phase 3 splits it),
  URLs, and `formats/en` (European formats).
- `finance/`: the whole app.
  - `models.py`: Profile, Account, Category, Payee, Transaction,
    ImportBatch, Reminder, ExchangeRate, Reconciliation.
  - `views.py` (about 2,000 lines, split in plan 02 phase 5), plus
    `auth_views.py` (picker/login/forgot-PIN) and `welcome_views.py`
    (4-step setup wizard).
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
    calls, restore) uses `@transaction.non_atomic_requests` and wraps its own
    writes (see `_stash_atomic()` in `settings_view`).
- Middleware chain: StashSelection → Auth → RequireStash (picker or the
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
  `settings.TESTING` (set from `sys.argv`) currently bypasses the stash and
  setup middleware and stash registration. Plan 02 phase 7 removes that.
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
  one. The spelling already saved wins (decided 27/09/2026).
- Reports convert through `RateTable` (main currency, latest known rate).
  A category with no earlier activity has **no** average (never 0). The
  vs-average reports need at least two earlier periods.
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
  donut). A missing average means no dot.
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
  - Report, and let the user choose what comes next.
- **The user writes dates as DD/MM/YYYY**, and so should plans and DEVLOG.

## Verification (there is no browser here)

- Run the full suite and report its real result.
- For front-end work, fetch the rendered HTML with the test client and read
  it. Look for leaked `{#`, `#}`, `{%` or `%}` (Django's `{# #}` comments
  are single-line only; a multi-line one renders as visible text). Never
  claim a visual result: say it's untested visually and ask the user to
  look. Bootstrap's table-cell background beats a lone class, so scope
  rules like `.table td.x`.
- "Everywhere" claims need a grep for the raw pattern (e.g.
  `Category.objects`), not just for callers of the shared helper.
- Line numbers in plans drift. Search by function name.
