"""One-off loader for test_data.TXT (a Quicken 'Register Report' export) into one stash,
purely so there's realistic data to look at while building the UI.

Not the CSV/OFX importer — this is a different, tab-delimited report format with a
header banner and summary rows mixed in, so it gets its own small parser. Run with:
    .venv\\Scripts\\python.exe scripts\\load_test_data.py --stash test

It WIPES every finance row in that stash first (see wipe_placeholder_data()), so it lists
what it's about to delete and asks you to type the stash's name before touching anything.
--yes skips that question.
"""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stashy.settings.base")

import django  # noqa: E402

django.setup()

from django.db import transaction as db_transaction  # noqa: E402

from audit.context import system_action  # noqa: E402
from finance import db_router  # noqa: E402
from finance.models import Account, Category, ImportBatch, Payee, Reminder, Transaction  # noqa: E402
from finance.money import parse_amount  # noqa: E402
from finance.stashes import registry as stashes  # noqa: E402

SOURCE = ROOT / "test_data.TXT"
DATE_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")
INCOME_TOP_LEVEL = {"Work Income", "Other Income"}
# The report header says "(in Euro)" — every account in this file is EUR (D2's per-account currency).
CURRENCY = "EUR"
WIPED_MODELS = (Transaction, Reminder, ImportBatch, Payee, Category, Account)

# Best-effort account type for each account name found in the report, using Phase 3's
# liquidity taxonomy (Physical Liquid / Digital Liquid / Illiquid).
ACCOUNT_TYPES = {
    "Debit Card": Account.AccountType.DEBIT_CARD,
    "Online Bank": Account.AccountType.DEBIT_CARD,
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
            name=name,
            defaults={"account_type": ACCOUNT_TYPES.get(name, Account.AccountType.DEBIT_CARD), "currency": CURRENCY},
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


def select_stash(name):
    """Sends every query in this script to the named stash. Since stashes switch live (Phase 12),
    Django's `default` database is only the picker's empty placeholder - without this, the load
    would quietly land there instead of in any stash."""
    if not stashes.stash_exists(name):
        available = ", ".join(stashes.list_stashes()) or "none yet"
        raise SystemExit(f'No stash named "{name}". Existing stashes: {available}.')
    stashes.ensure_registered(name)
    db_router.set_current_stash_alias(name)


def confirm_wipe(name):
    """Lists what the wipe would delete and asks for the stash's own name back - typing it is a
    deliberate act, unlike pressing Enter or "y" on autopilot."""
    existing = [(model._meta.verbose_name_plural, model.objects.count()) for model in WIPED_MODELS]
    existing = [(label, count) for label, count in existing if count]
    if not existing:
        return True
    print(f'This deletes everything below from the "{name}" stash before loading {SOURCE.name}:')
    for label, count in existing:
        print(f"  {count} {label}")
    try:
        answer = input(f'Type the stash name ("{name}") to continue, or anything else to stop: ')
    except EOFError:
        return False
    return answer.strip() == name


def wipe_placeholder_data():
    """Clears every finance row in the selected stash before reloading test_data.TXT from scratch.

    STOP AND THINK BEFORE RUNNING THIS against a stash with anything you care about in it: it
    deletes ALL accounts, transactions, categories, payees AND reminders unconditionally -
    including anything typed into the app by hand since the last load. That already happened
    once (a real reminder was lost this way - see DEVLOG.md, 13/09/2026), which is why the script
    now asks for the stash's name first. Only rerun this deliberately, after checking with the
    user."""
    for model in WIPED_MODELS:
        count = model.objects.count()
        if count:
            print(f"  clearing {count} existing {model._meta.verbose_name_plural}")
            model.objects.all().delete()


def load(stash_name):
    rows = list(parse_rows())
    created, skipped = 0, 0

    # atomic() opens its transaction on `default` unless told otherwise - the router only picks
    # where each query goes, not which connection a transaction block wraps - so name the stash.
    with (
        db_transaction.atomic(using=stash_name),
        system_action(f"Loaded from {SOURCE.name} (Quicken register export) for visual testing"),
    ):
        wipe_placeholder_data()
        for row in rows:
            amount = parse_amount(row["amount"])
            if amount is None:
                print(f"  skipping unparseable amount: {row}")
                skipped += 1
                continue

            account = get_account(row["account"])
            is_reconciled = row["clr"] == "R"

            # A bracketed category, e.g. "[Online Bank]", marks a transfer leg (D7) — both legs
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
                reconciled=is_reconciled,
            )
            created += 1

    print(f'Loaded into the "{stash_name}" stash: {created} transactions ({skipped} rows skipped).')
    print(
        f"Accounts: {Account.objects.count()}, Categories: {Category.objects.count()}, Payees: {Payee.objects.count()}"
    )
    for account in Account.objects.all():
        kind = account.get_account_type_display()
        print(f"  {account.name:14} {kind:24} balance {account.balance:>12} {account.currency}")


def main():
    parser = argparse.ArgumentParser(description=f"Wipe one stash's finance data and reload it from {SOURCE.name}.")
    parser.add_argument("--stash", required=True, help="the stash to load into (a folder name under stashes/)")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation question")
    args = parser.parse_args()

    select_stash(args.stash)
    if not args.yes and not confirm_wipe(args.stash):
        print("Stopped - nothing was changed.")
        return 1
    load(args.stash)
    return 0


if __name__ == "__main__":
    sys.exit(main())
