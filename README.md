# Stashy

A personal finance web app built with Django — accounts, transactions with
memorized-payee autofill, categories/subcategories, bill & income reminders,
CSV/OFX import, and reports (spending by category, income vs. expense, current
vs. average spending, net worth), all filterable by date range and interval.

Runs locally, single-user, backed by SQLite — no separate database server to
install or configure.

## 1. Set up the environment

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `SECRET_KEY` to any long random string.

## 2. Run migrations and create your login

```bash
python manage.py migrate
python manage.py createsuperuser
```

## 3. Run it

```bash
python manage.py runserver
```

Visit http://127.0.0.1:8000/ and log in with the superuser account you just
created. There's no public signup — this is meant for one user (you).

## Using it

- **Accounts** — add your checking/savings/credit card/cash accounts with a
  starting balance. Each account's balance is computed live from its
  transactions.
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

## CSV import format

Any CSV with columns roughly matching `Date, Payee/Description, Memo, Amount`
should work — column names are matched case-insensitively and a few common
bank export variants are recognized. Dates are parsed as `YYYY-MM-DD`,
`MM/DD/YYYY`, `MM/DD/YY`, or `DD/MM/YYYY`. Duplicate rows (same account, date,
amount, and payee) are automatically skipped on re-import.

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
