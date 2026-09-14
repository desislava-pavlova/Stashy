# Stashy

A personal finance web app built with Django — accounts, transactions with
memorized-payee autofill, categories/subcategories, bill & income reminders,
CSV/OFX import, and reports (spending by category, income vs. expense, current
vs. average spending, net worth), all filterable by date range and interval.

Runs locally, single-user, backed by SQLite — no separate database server to
install or configure.

## Quick start (Windows)

1. Install Python 3.12 or newer from https://www.python.org/downloads/ —
   on the installer's first screen, tick **"Add python.exe to PATH"**.
2. Double-click **`Stashy.bat`**.

The first run sets everything up (private Python environment, components,
settings file with a random secret key, database) and asks you to pick a
username and password. After that, double-clicking `Stashy.bat` just starts
Stashy and opens it in your browser. Keep the black window open while you use
Stashy; closing it stops Stashy.

Stashy runs on Waitress, a production web server, rather than Django's
development server - so start it with `Stashy.bat`, not
`python manage.py runserver`. After updating Stashy's code, close the window
and start `Stashy.bat` again.

By default Stashy serves on port 8000. If that's already taken by something
else on your computer (another app, or a `manage.py runserver` you're running
for development — Django's dev server also defaults to 8000), open `.env` and
add or change a `PORT=` line (e.g. `PORT=8001`), then run `Stashy.bat` again.

If an update changes the database, `Stashy.bat` backs up `db.sqlite3` into
`backups/` before applying the change.

Once installed, Stashy needs no internet connection: every style, font, icon
and script ships inside the app. It follows your Windows light/dark setting,
and the sidebar toggle switches it (your choice is remembered).

## Manual setup (macOS/Linux, or if you prefer the command line)

### 1. Set up the environment

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `SECRET_KEY` to any long random string.

### 2. Run migrations and create your login

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 3. Run it

```bash
python manage.py runserver
```

Visit http://127.0.0.1:8000/ and log in with the superuser account you just
created. There's no public signup — this is meant for one user (you).

## Using it

- **Accounts** — grouped as Physical Liquid, Digital Liquid or Illiquid
  (inactive accounts always sort last). Add an opening balance + date when
  creating one; Stashy turns it into a real, editable transaction rather
  than a hidden number, so every account's balance is a pure sum of its
  transactions. Each account has its own currency; **Settings** sets your
  main currency and lets you fetch exchange rates (needs internet) to
  convert everything for net worth. The **Transfer** button on an account
  page moves money to another account as a paired transaction.
- **Categories** — set up income/expense categories; give a category a
  "parent" to make it a subcategory.
- **Transactions** — add manually, or use **Import** for CSV/OFX/QFX bank
  exports. When you type a payee name, Stashy looks up that payee's last
  category/memo/amount and autofills the form (memorized payees) — this
  updates automatically every time you save a transaction against a payee, no
  separate setup needed. Positive amounts are income/deposits, negative
  amounts are expenses.
- **Reminders** — set up recurring bills or income with a frequency. Hit
  "Enter" on a due reminder to create the actual transaction and roll the due
  date forward.
- **Reports** — spending by category, income vs. expense over time, current
  vs. 12-period average spending, and net worth over time. Filter by date
  range (presets or custom), interval (day/week/month/quarter/year), account,
  or category.
- **Admin** — the full Django admin is available at `/admin/` for bulk edits,
  data cleanup, or anything the UI doesn't cover yet.
- **Audit log** — every change to your data, including automatic ones (imports,
  memorized-payee updates, reminder dates moving forward), is recorded with
  before/after values, the reason, and everything else the same click changed.
  See it in the admin under *Audit log*, or press **History** on any record in
  the admin. Changes made by editing `db.sqlite3` directly with another tool
  can't be seen by the log.

## CSV import format

Any CSV with columns roughly matching `Date, Payee/Description, Memo, Amount`
should work — column names are matched case-insensitively and a few common
bank export variants are recognized. Dates are read day-first: `DD/MM/YYYY`,
`DD.MM.YYYY`, `DD-MM-YYYY`, `DD/MM/YY`, `DD.MM.YY`, or ISO `YYYY-MM-DD`
(American month-first dates are deliberately not accepted, since `03/04/2026`
would be ambiguous). Amounts are read exactly (never as floating point) in
either style — `1 234.56`, `1,234.56`, `1.234,56`, `12,50`, or `(12.00)` for a
negative. Duplicate rows (same account, date, amount, and payee) are
automatically skipped on re-import.

OFX/QFX files exported directly from your bank are also supported.

## Project structure

```
stashy/
├── stashy/         # project settings, urls
└── finance/          # the app: models, views, forms, templates
    ├── models.py      # Account, Category, Payee, Transaction, Reminder, etc.
    ├── views.py        # dashboard, CRUD views, import, reports, autofill API
    ├── forms.py
    ├── reporting.py     # date-range/interval helpers for reports
    └── templates/finance/
```

## Notes / next steps you might want

- **Split transactions** (one purchase across multiple categories) aren't in
  yet — the model would need a `TransactionSplit` child model if you want this.
- **Budgets** (monthly targets vs. actuals) weren't in your initial feature
  list but would slot in cleanly next to Reminders if you want them later.
- **Backups**: since this holds your financial data, back up `db.sqlite3`
  regularly — it's just a file, so a periodic copy is enough.
- This is configured for local/single-user use (`DEBUG=True` by default).
  If you ever want to access it from your phone too, you'd want to deploy it
  properly (real `SECRET_KEY`, `DEBUG=False`, HTTPS, a real `ALLOWED_HOSTS`)
  rather than just opening a port on your home network.
