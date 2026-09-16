# Stashy

A personal finance web app built with Django — accounts, transactions with
memorized-payee autofill, categories/subcategories, bill & income reminders,
reconciliation, CSV/OFX import, CSV/Excel export, backups and reports.

Runs locally on your own computer, backed by SQLite — no separate database
server to install or configure. Each person (or each set of books) gets their
own **stash**: a completely separate database with its own PIN.

## Quick start (Windows)

1. Install Python 3.12 or newer from https://www.python.org/downloads/ —
   on the installer's first screen, tick **"Add python.exe to PATH"**.
2. Double-click **`Stashy.bat`**.

The first run sets everything up (private Python environment, components,
settings file with a random secret key) and opens Stashy in your browser.
There you **create your first stash**: pick a name and a 4-digit PIN. Stashy
then shows a one-time **recovery code** — write it down, it's the only way to
reset a forgotten PIN from inside the app and it won't be shown again.

After that, double-clicking `Stashy.bat` just starts Stashy: pick your stash
and enter its PIN. Keep the black window open while you use Stashy; closing
it stops Stashy.

Stashy runs on Waitress, a production web server, rather than Django's
development server - so start it with `Stashy.bat`, not
`python manage.py runserver`. After updating Stashy's code, close the window
and start `Stashy.bat` again.

By default Stashy serves on port 8000. If that's already taken by something
else on your computer (another app, or a `manage.py runserver` you're running
for development — Django's dev server also defaults to 8000), open `.env` and
add or change a `PORT=` line (e.g. `PORT=8001`), then run `Stashy.bat` again.

If an update changes the database, `Stashy.bat` backs up **every** stash into
its own `stashes/<name>/backups/` folder before applying the change.

Once installed, Stashy needs no internet connection: every style, font, icon
and script ships inside the app (the only optional online feature is fetching
exchange rates). It follows your Windows light/dark setting, and the sidebar
toggle switches it (your choice is remembered).

## Stashes and PINs

- **Switching stashes** needs no restart: use **Switch stash** to log out and
  go back to the stash list, then pick another one and enter its PIN.
- **Wrong PINs**: after 5 wrong tries in a row, that stash locks for 5 minutes.
- **Forgot your PIN?** Use "Forgot PIN?" on the login screen with your
  recovery code. Setting a new PIN gives you a **new** recovery code (the old
  one stops working). You can also change your PIN any time in **Settings**.
- **Lost the recovery code too?** From the Stashy folder run
  `python manage.py reset_pin <stash name>` (optionally `--pin 1234`).
- The PIN locks the **app**, not the data: each stash is a plain SQLite file
  (`stashes/<name>/db.sqlite3`) that anyone with access to your Windows account
  could open with another tool. Protect the computer itself (your own Windows
  login, BitLocker) if that matters to you.

## Manual setup (macOS/Linux, or if you prefer the command line)

### 1. Set up the environment

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `SECRET_KEY` to any long random string.

### 2. Prepare the database and run it

```bash
python manage.py migrate
python manage.py runserver
```

Visit http://127.0.0.1:8000/ and create your first stash (name + PIN). There's
no public signup — each stash belongs to one person.

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
  subcategories. **Add starter categories** creates any missing names listed in
  `basic_categories.txt` (edit that file to change the list). Every category
  dropdown lists Income first, then Expense, then Transfer, each main category
  followed by its own subcategories.
- **Transactions** — add manually, or use **Import** for CSV/OFX/QFX bank
  exports. When you type a payee name, Stashy looks up that payee's last
  category/memo/amount and autofills the form (memorized payees) — this
  updates automatically every time you save a transaction against a payee.
  Payee names are matched regardless of upper/lower case, so an import reuses
  the payee you already have. The list can be filtered by date, status,
  account, several categories at once and text, and exported to CSV/Excel.
- **Reminders** — recurring or one-time bills and income. **Enter** opens a
  pre-filled transaction; **Skip** moves a reminder to its next date without
  creating one.
- **Reports** — Spending by category, Income by category, two vs.-average
  reports (current vs. earlier full periods of the same kind — e.g. this
  month vs. earlier months), Income & expense and its own vs.-average
  report, Net worth, and Watched expenses (any set of expense categories
  chosen in Settings, e.g. bills — the report's own title is editable
  there too). A category's own type decides income vs.
  spending everywhere, never the sign of the amount. A vs.-average report
  needs at least two earlier periods to exist at all (e.g. two full months
  before this one) and shows "No earlier data to compare with" otherwise;
  within that, a category with no activity in any of those periods has no
  average of its own rather than a misleading zero. Filter by date range
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
  yet.
- **Audit log** — every change to your data, including automatic ones
  (imports, memorized-payee updates, reminder dates moving forward), is
  recorded with before/after values, the reason, and everything else the same
  click changed. See it in the admin under *Audit log*, or press **History** on
  any record in the admin. Changes made by editing a stash's database file
  directly with another tool can't be seen by the log.

## CSV import format

Any CSV with columns roughly matching `Date, Payee/Description, Memo, Amount`
should work — column names are matched case-insensitively and a few common
bank export variants are recognized. Optional `Account` and `Category`
columns are matched by name (`Parent:Child` for a subcategory); a category
Stashy doesn't have yet is created for you and marked as auto-generated.
Stashy detects each file's date format from its own dates (day-first formats
like `DD/MM/YYYY`, `DD.MM.YYYY`, `DD-MM-YYYY`, or ISO `YYYY-MM-DD`), and you can
override it on the import page. Amounts are read exactly (never as floating
point) in either style — `1 234.56`, `1,234.56`, `1.234,56`, `12,50`, or
`(12.00)` for a negative. Duplicate rows (same account, date, amount, and
payee) are automatically skipped on re-import.

OFX/QFX files exported directly from your bank are also supported.

## Project structure

```
Stashy/
├── Stashy.bat            # one-click setup + launch (Windows)
├── scripts/bootstrap.py  # what Stashy.bat runs: install, migrate + back up every stash, start Waitress
├── basic_categories.txt  # names used by "Add starter categories"
├── stashy/               # project settings, urls
├── audit/                # the audit log (records every data change)
├── finance/              # the app
│   ├── models.py         # Account, Category, Payee, Transaction, Reminder, etc.
│   ├── views.py          # dashboard, CRUD views, import/export, reports, backups
│   ├── forms.py
│   ├── reporting.py      # report calculations
│   ├── stashes.py        # creating stashes, PINs, lockout, recovery codes
│   ├── auth_views.py     # stash picker, PIN login, forgot PIN
│   ├── db_router.py      # sends each request's queries to its session's stash
│   ├── middleware.py
│   ├── backup.py
│   └── templates/, static/
└── stashes/<name>/       # one folder per stash: db.sqlite3 + backups/
```

## Notes / next steps you might want

- **Split transactions** (one purchase across multiple categories) aren't in
  yet — the model would need a `TransactionSplit` child model if you want this.
- **Budgets** (monthly targets vs. actuals) weren't in your initial feature
  list but would slot in cleanly next to Reminders if you want them later.
- This is configured for local/single-user use (`DEBUG=True` by default).
  If you ever want to access it from your phone too, you'd want to deploy it
  properly (real `SECRET_KEY`, `DEBUG=False`, HTTPS, a real `ALLOWED_HOSTS`)
  rather than just opening a port on your home network.
