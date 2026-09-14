"""One-off loader for test_data.TXT (a Quicken 'Register Report' export) into the real
database, purely so there's realistic data to look at while building the UI.

Not the CSV/OFX importer — this is a different, tab-delimited report format with a
header banner and summary rows mixed in, so it gets its own small parser. Run with:
    .venv\\Scripts\\python.exe scripts\\load_test_data.py
"""
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stashy.settings")

import django  # noqa: E402

django.setup()

from django.db import transaction as db_transaction  # noqa: E402

from audit.context import system_action  # noqa: E402
from finance.models import Account, Category, ImportBatch, Payee, Reminder, Tag, Transaction  # noqa: E402
from finance.money import parse_amount  # noqa: E402

SOURCE = ROOT / "test_data.TXT"
DATE_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")
INCOME_TOP_LEVEL = {"Work Income", "Other Income"}
# The report header says "(in Euro)" — every account in this file is EUR (D2's per-account currency).
CURRENCY = "EUR"

# Best-effort account type for each account name found in the report, using Phase 3's
# liquidity taxonomy (Physical Liquid / Digital Liquid / Illiquid).
ACCOUNT_TYPES = {
    "Debit Card": Account.AccountType.DEBIT_CARD,
    "Revolut": Account.AccountType.DEBIT_CARD,
    "Cash": Account.AccountType.CASH,
    "Ozone Wallet": Account.AccountType.E_WALLET,
    "Gift Cards": Account.AccountType.GIFT_CARD,
    "Fuel Coupons": Account.AccountType.COUPONS,
}

_account_cache = {}
_category_cache = {}
_payee_cache = {}


def get_account(name):
    if name not in _account_cache:
        _account_cache[name] = Account.objects.get_or_create(
            name=name, defaults={"account_type": ACCOUNT_TYPES.get(name, Account.AccountType.DEBIT_CARD), "currency": CURRENCY}
        )[0]
    return _account_cache[name]


def get_category(raw):
    if not raw:
        return None
    if raw in _category_cache:
        return _category_cache[raw]
    parent_name, _, child_name = raw.partition(":")
    kind = Category.CategoryType.INCOME if parent_name in INCOME_TOP_LEVEL else Category.CategoryType.EXPENSE
    parent, _ = Category.objects.get_or_create(name=parent_name, parent=None, defaults={"category_type": kind})
    category = parent
    if child_name:
        category, _ = Category.objects.get_or_create(name=child_name, parent=parent, defaults={"category_type": kind})
    _category_cache[raw] = category
    return category


def get_payee(name):
    if not name or name == "-":
        return None
    if name not in _payee_cache:
        _payee_cache[name] = Payee.objects.get_or_create(name=name)[0]
    return _payee_cache[name]


def parse_rows():
    text = SOURCE.read_text(encoding="utf-8")
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) < 10 or not DATE_RE.match(fields[1].strip()):
            continue
        yield {
            "date": fields[1].strip(),
            "account": fields[2].strip(),
            "num": fields[3].strip(),
            "description": fields[4].strip(),
            "memo": fields[5].strip(),
            "category": fields[6].strip(),
            "clr": fields[8].strip(),
            "amount": fields[9].strip(),
        }


def wipe_placeholder_data():
    """Clears every finance row before reloading test_data.TXT from scratch.

    STOP AND THINK BEFORE RUNNING THIS against the real db.sqlite3: it deletes ALL accounts,
    transactions, categories, payees AND reminders unconditionally - including anything typed
    into the real app by hand since the last load (a reminder, a manually added account,
    anything). This was written back when the database only held a handful of throwaway rows
    from early manual testing; it stopped being safe to run casually the moment the user
    started actually using the app between sessions, which is exactly what happened once (a
    real reminder was lost this way - see DEVLOG.md, 13/09/2026). Only rerun this deliberately,
    after checking with the user, when a schema change genuinely requires reloading the dataset."""
    for model in (Transaction, Reminder, ImportBatch, Payee, Category, Account, Tag):
        count = model.objects.count()
        if count:
            print(f"  clearing {count} existing {model._meta.verbose_name_plural}")
            model.objects.all().delete()


def load():
    rows = list(parse_rows())
    created, skipped = 0, 0

    with db_transaction.atomic(), system_action(f"Loaded from {SOURCE.name} (Quicken register export) for visual testing"):
        wipe_placeholder_data()
        for row in rows:
            amount = parse_amount(row["amount"])
            if amount is None:
                print(f"  skipping unparseable amount: {row}")
                skipped += 1
                continue

            account = get_account(row["account"])
            is_reconciled = row["clr"] == "R"

            # A bracketed category, e.g. "[Revolut]", marks a transfer leg (D7) — both legs
            # already exist as separate rows in this report, each using its own account's
            # transfer category, so no pairing/synthesizing is needed here.
            if row["category"].startswith("[") and row["category"].endswith("]"):
                payee = Payee.system(Payee.TRANSFER_MONEY)
                category = account.ensure_transfer_category()
            else:
                payee = get_payee(row["description"])
                category = get_category(row["category"])

            transaction_number = int(row["num"]) if row["num"].isdigit() else None

            Transaction.objects.create(
                account=account,
                date=datetime.strptime(row["date"], "%d.%m.%Y").date(),
                transaction_number=transaction_number,
                payee=payee,
                category=category,
                memo=row["memo"],
                amount=amount,
                cleared=is_reconciled,
                reconciled=is_reconciled,
            )
            created += 1

    print(f"Created {created} transactions ({skipped} rows skipped).")
    print(f"Accounts: {Account.objects.count()}, Categories: {Category.objects.count()}, Payees: {Payee.objects.count()}")
    for account in Account.objects.all():
        print(f"  {account.name:14} {account.get_account_type_display():24} balance {account.balance:>12} {account.currency}")


if __name__ == "__main__":
    load()
