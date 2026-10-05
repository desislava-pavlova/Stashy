# Stashy

A personal finance web app built with Django — accounts, transactions with
memorized-payee autofill, categories/subcategories, bill & income reminders,
reconciliation, CSV/Excel/OFX import, CSV/Excel export, backups and reports.

Runs locally on your own computer, backed by SQLite — no separate database
server to install or configure. Each person (or each set of books) gets their
own **stash**: a completely separate database with its own PIN.

## Quick start (Windows)

1. Install Python 3.12 or newer from https://www.python.org/downloads/ —
   on the installer's first screen, tick **"Add python.exe to PATH"**.
2. Double-click **`Stashy.bat`**.

The first run sets everything up (private Python environment, components,
settings file with a random secret key) and opens Stashy in your browser.
There you **create your first stash**: say whose it is (a person, a
business, a family — this is what Stashy calls it everywhere, e.g. "Good
evening, Alex") and set a 4-digit PIN. Stashy then shows a one-time
**recovery code** — write it down, it's the only way to reset a forgotten
PIN from inside the app and it won't be shown again.

Logging in for the first time walks you through a short setup: your money
& region preferences (guessed from this computer, change anything that
doesn't match), a starting list of categories, which expenses to watch
closely, and your first account — each optional except the categories,
and each with a **Back** button if you want to revise an earlier answer.
Closing Stashy partway through picks up right where you left off next
time you log in.

After that, double-clicking `Stashy.bat` opens straight on your last-used
stash's PIN screen. Keep the black window open while you use Stashy;
closing it stops Stashy.

Stashy's address is **http://stashy.localhost:8000** (with your own port if
you changed it), which you can bookmark. It needs no setup: every browser
sends names ending in `.localhost` to your own computer.
`http://127.0.0.1:8000` still works too.

Stashy runs on Waitress, a production web server, rather than Django's
development server - so start it with `Stashy.bat`, not
`python manage.py runserver`. After updating Stashy's code, close the window
and start `Stashy.bat` again.

By default Stashy serves on port 8000. If that's already taken by something
else on your computer (another app, or a `manage.py runserver` you're running
for development — Django's dev server also defaults to 8000), open `.env` and
add or change a `PORT=` line (e.g. `PORT=8001`), then run `Stashy.bat` again.

If an update changes the database, `Stashy.bat` backs up **every** stash into
its own `stashes/<name>/backups/` folder before applying the change. A backup's
name says which stash, why and when, like
`alex_backup_quarterly_2026-10-03_09-15-00.sqlite3`.

Once installed, Stashy needs no internet connection: every style, font, icon
and script ships inside the app (the only optional online feature is fetching
exchange rates). It follows your Windows light/dark setting, and the sidebar
toggle switches it (your choice is remembered).

## Stashes and PINs

- **Each stash belongs to someone** — a person, a business or a family —
  and that name is what Stashy shows everywhere (the dashboard greeting,
  "Log out (Alex)", the picker, Settings) instead of an internal name you
  never see. It's set once, when the stash is created, and can't be
  changed afterwards.
- **No switching while logged in.** To use a different stash, log out
  first ("Choose a different stash" on the PIN screen, or Log out from the
  sidebar) — Stashy remembers which stash you used last and opens straight
  on its PIN screen next time, so this is rarely more than one click.
- **Wrong PINs**: after 5 wrong tries in a row, that stash locks for 5 minutes.
- **Forgot your PIN?** Use "Forgot PIN?" on the login screen with your
  recovery code. Setting a new PIN gives you a **new** recovery code (the old
  one stops working). You can also change your PIN any time in **Settings**.
- **Lost the recovery code, but not your PIN?** **Settings** has "Get a new
  recovery code" — confirms once, then shows a fresh code (the old one stops
  working, same as using "Forgot PIN?" does).
- **Lost the recovery code too?** From the Stashy folder run
  `python manage.py reset_pin <owner name>` (optionally `--pin 1234`).
- The PIN locks the **app**, not the data: each stash is a plain SQLite file
  (`stashes/<internal name>/db.sqlite3`) that anyone with access to your
  Windows account could open with another tool. Protect the computer itself
  (your own Windows login, BitLocker) if that matters to you.

## Manual setup (macOS/Linux, or if you prefer the command line)

### 1. Set up the environment

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `SECRET_KEY` to any long random string. Stashy won't
start without one.

### 2. Prepare the database and run it

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py runserver
```

`collectstatic` prepares the CSS, JavaScript and images; run it again after
an update. (With `DEBUG=True` in `.env`, `runserver` serves them straight
from the code instead.)

Visit http://stashy.localhost:8000/ (or http://127.0.0.1:8000/) and create your first stash (whose it is + PIN).
There's no public signup — each stash belongs to one person, business or
family.

`migrate` above only prepares Stashy's small placeholder database; a new stash
is fully set up when you create it. After a code update that changes the
database, bring each existing stash up to date with
`python manage.py migrate --database <stash name>` (`Stashy.bat` does this for
every stash automatically).

## Using it

- **Accounts** — grouped as Physical Liquid, Digital Liquid or Illiquid
  (inactive accounts always sort last; an account can only be made inactive
  once its balance is zero). Every new account gets an opening balance
  transaction — the amount and date you enter, or 0 dated today if you leave
  them blank — so every account's balance is a pure sum of its transactions.
  Each account has its own currency; **Settings** sets your main currency and
  lets you fetch exchange rates (needs internet) to convert everything for net
  worth. On an account's page, **Transfer** moves money to another account as
  a paired transaction, and **Reconcile** checks it off against a bank
  statement.
- **Categories** — income/expense categories, with one level of
  subcategories. A new stash's setup offers a starting list (Basic: 26
  top-level categories, or Extended: those plus all their subcategories,
  read from `starter_categories.xlsx` — edit that file to change the
  list); after that, add, edit or delete them by hand from the Categories
  page. Two main categories, or two subcategories of the same one, can't
  share a name, even in a different case. Every category dropdown lists Income first, then Expense, then
  Transfer, each main category followed by its own subcategories.
- **Transactions** — add them by hand (**Save and add another** keeps the
  account and date for the next one), or use **Import** for CSV, Excel
  (.xlsx), OFX or QFX files. When you type a payee, Stashy fills in the
  category, memo and amount you used with it before, worked out from your
  past transactions; if you've used it with several categories, it offers a
  short pick list. Payee names are matched regardless of upper/lower case
  (in any alphabet, Cyrillic included), so typing or importing "lidl" reuses
  the "Lidl" you already have, and two payees can never differ only in case. The list can be filtered
  by date, status, account, several categories at once and text, exported
  to CSV/Excel, and switched into **Edit mode**, where every row's fields
  can be changed in place and save themselves. A date range longer than a
  year (or "All time") shows 1,000 transactions per page, with page links
  above and below the list; the count and the total always cover every
  page, and the export always holds everything the filters match. An
  account's own register pages the same way.
- **Reminders** — recurring or one-time bills and income. A recurring
  reminder creates its upcoming dates ahead of time. **Enter** opens a
  pre-filled transaction; **Skip** moves on without creating one; either way
  that date stays listed as Paid or Skipped. The list opens on this month.
- **Reports** — Spending by category, Income by category, two vs.-average
  reports (current vs. earlier full periods of the same kind — e.g. this
  month vs. earlier months), Income & expense and its own vs.-average
  report, Net worth, and Watched expenses (any set of expense categories
  chosen in Settings, e.g. bills — the report's own title is editable
  there too). A category's own type decides income vs.
  spending everywhere, never the sign of the amount. A vs.-average report
  needs at least two earlier periods to exist at all (e.g. two full months
  before this one) and shows "No earlier data to compare with" otherwise;
  within that, a month without a category's spending counts as a real zero.
  A category with no activity in any of those periods averages 0: its
  Average shows "—", but its Difference shows the full gap and the chart
  draws its average dot at 0 (the export writes 0.00). Filter by date range
  (including "Earliest to date"), subtotal
  (week to year), average interval (how many earlier periods to use) and
  account; every amount converts to your main currency. **Expand** a
  report to see its table; once expanded, **Show all/Hide all** opens or
  closes every subcategory row, and you can export it.
- **Backups** — **Settings** has **Back up now**, the list of recent backups
  and **Restore** (which takes a safety backup of the current state first).
  Stashy also makes an automatic backup once a quarter, checked when it
  starts.
- **Admin** — the full Django admin is available at `/admin/` (while logged in
  to a stash) for bulk edits, data cleanup, or anything the UI doesn't cover
  yet. Users and Groups are hidden (there's one shared login per stash, no
  group permissions to manage) and Profile (your regional preferences) is
  view-only there — change it from **Settings** instead.
- **Audit log** — every change to your data, including automatic ones
  (imports, memorized-payee updates, reminder dates moving forward), is
  recorded like a database audit log: one row naming a new or deleted record,
  or one row per changed column with its raw old and new value (stored
  choice keys, exact decimals, ISO dates — not reformatted for display), plus
  who/why/when and the request that caused it. See it in the admin under
  *Audit log*, or press **History** on any record in the admin, to jump to
  every row for that one record. A deleted or newly created record keeps its
  full set of values in that one row so nothing is lost. Your stash's PIN
  recovery code is never logged, even hashed — wrong-PIN attempts still are.
  Changes made by editing a stash's database file directly with another tool
  can't be seen by the log.

## Import format

CSV and Excel (.xlsx) files use the same columns: `Date, Payee/Description,
Memo, Amount`, plus optional `Num`, `Account`, `Category` and `Reconciled`
(the column set of a Quicken transaction report). Column names are matched
case-insensitively and a few common bank export variants are recognized.
Only `Date` and `Amount` need a value. `Account` and `Category` are matched
by name (`Parent:Child` for a subcategory); a category Stashy doesn't have
yet is created for you and marked as auto-generated. `Num` becomes the
transaction number, and a `Reconciled` value marks the row reconciled.
Stashy detects each file's date format from its own dates (day-first formats
like `DD/MM/YYYY`, `DD.MM.YYYY`, `DD-MM-YYYY`, or ISO `YYYY-MM-DD`), and you can
override it on the import page. Amounts are read exactly (never as floating
point) in either style — `1 234.56`, `1,234.56`, `1.234,56`, `12,50`, or
`(12.00)` for a negative. Duplicate rows (same account, date, amount, and
payee) are automatically skipped on re-import. CSV files can be UTF-8 or
Windows-1251 (Cyrillic); the summary says which was used, and how many rows
were skipped because their date or amount couldn't be read. A file Stashy
can't read at all gets a message and imports nothing.

Exports (CSV or Excel) can be opened straight in Excel: Excel files keep
amounts as numbers and dates as dates, and CSV files show Cyrillic
correctly. Each file's name ends with the date and time it was made, like
`stashy-net-worth-2026-10-03_14-32-05.csv`, so several exports on one day
never overwrite each other.

OFX/QFX files exported directly from your bank are also supported.

## Project structure

```
Stashy/
├── Stashy.bat            # one-click setup + launch (Windows)
├── scripts/bootstrap.py  # what Stashy.bat runs: install, migrate + back up every stash, start Waitress
├── requirements.txt      # what Stashy needs to run
├── requirements-dev.txt  # extra tools for working on Stashy (ruff, coverage)
├── pyproject.toml        # settings for those tools
├── .env.example          # copied to .env (secret key, DEBUG, PORT) on first run
├── starter_categories.xlsx  # Basic/Extended starting category lists, read by the welcome wizard
├── plans/                # numbered development plans (index in plans/README.md)
├── DEVLOG.md             # what changed and why, entry by entry
├── CLAUDE.md             # working notes for the AI assistant used on this project
├── stashy/               # urls, and settings/ (base.py for the app, test.py for tests)
├── audit/                # the audit log (records every data change)
├── finance/              # the app
│   ├── models.py         # Account, Category, Payee, Transaction, Reminder, etc.
│   ├── views/            # one file per area: accounts.py, transactions.py, reports.py,
│   │                     #   exports.py, settings.py, auth.py (picker, PIN login),
│   │                     #   welcome.py (the setup wizard), and so on
│   ├── content.py        # the "Stashy tips" text shown beside forms
│   ├── services/         # importing a file, reconciling an account
│   ├── forms.py
│   ├── reporting.py      # report calculations
│   ├── stashes/          # registry.py: creating and finding stashes, owner names;
│   │                     #   security.py: PINs, lockout, recovery codes
│   ├── db_router.py      # sends each request's queries to its session's stash
│   ├── middleware.py
│   ├── backup.py
│   ├── tests/            # one file per area (test_accounts.py, test_reminders.py, …);
│   │                     #   base.py: the setup the tests share
│   ├── migrations/
│   └── templates/, static/
├── stashes/<name>/       # one folder per stash: db.sqlite3 + backups/ (not in git)
└── logs/stashy.log       # errors with their details, if anything goes wrong (not in git)
```

## Development

- Run the tests with `python manage.py test` (on Windows:
  `.venv\Scripts\python.exe manage.py test`). They run inside a throwaway
  test stash (a temporary folder and an in-memory database), never touch
  your own stashes, and take about 25 seconds. Run one area with
  `python manage.py test finance.tests.test_reminders`.
- Install the tools with `pip install -r requirements-dev.txt`. Check the
  code with `ruff check .`, tidy its layout with `ruff format .`, and see
  how much of it the tests run with `coverage run manage.py test` followed
  by `coverage report`.
- After changing a model, run `python manage.py makemigrations`.
  `Stashy.bat` applies the new migration to every stash (after a backup) on
  its next launch. Migration files aren't in git for now (see plan 02, item
  3.6).
- Planned work lives in `plans/`, and the reasoning behind past changes in
  `DEVLOG.md`.

## Notes / next steps you might want

- **Split transactions** (one purchase across multiple categories) aren't in
  yet — the model would need a `TransactionSplit` child model if you want this.
- **Budgets** (monthly targets vs. actuals) aren't built; they would slot in
  next to Reminders.
- This is configured for local/single-user use. If you ever want to access
  it from your phone too, you'd want to deploy it properly (HTTPS, a real
  `ALLOWED_HOSTS`) rather than just opening a port on your home network.

## If something goes wrong

A page that crashes shows "Something went wrong". The details (what failed,
and where in the code) are saved in `logs\stashy.log` in the Stashy folder,
so keep that file if you want the problem fixed. Setting `DEBUG=True` in
`.env` (then restarting Stashy) shows the details in the browser instead.
That's meant only for working on Stashy itself.
