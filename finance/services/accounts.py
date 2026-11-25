"""Opening an account and moving money between accounts (decisions D1
and D7). The rules every way in shares, so the Add account page, the
welcome wizard, the admin and the Transfer button all behave the same.

Plain functions, no HTTP. Each runs in its own transaction on the
stash's database, so it either does all of its work or none of it.
"""

from django.db import router, transaction
from django.utils import timezone

from audit.context import system_action

from ..models import Account, Payee, Transaction
from ..money import ZERO


def open_account(account, opening_balance=ZERO, opening_date=None):
    """Save the new `account` and make its opening-balance transaction
    (D1): the balance is then simply the sum of the account's
    transactions.

    Every account gets one, even a zero one dated today, so a new
    account's register never starts empty and reconciling always has a
    first transaction to start from. Returns the saved account.
    """
    opening_date = opening_date or timezone.localdate()
    with transaction.atomic(using=router.db_for_write(Account)):
        account.save()
        with system_action(f"Opening balance transaction created for account '{account.name}'"):
            Transaction.objects.create(
                account=account,
                date=opening_date,
                payee=Payee.system(Payee.OPENING_BALANCE),
                category=account.ensure_transfer_category(),
                memo="Opening Balance",
                amount=opening_balance,
            )
    return account


def transfer(from_account, to_account, amount, on):
    """Move `amount` out of `from_account` and into `to_account`, as two
    paired transactions dated `on` (D7). Returns (the payment, the
    deposit).

    Nothing is converted, so both accounts must share a currency:
    otherwise "100" would leave as 100 EUR and arrive as 100 USD,
    inventing or destroying money. A pair that doesn't, the same
    account twice, or an amount that isn't above zero (it would turn
    the payment into a deposit) is a ValueError. The Transfer page
    only ever sends valid ones.
    """
    if from_account.pk == to_account.pk:
        raise ValueError("A transfer needs two different accounts.")
    if amount <= 0:
        raise ValueError("A transfer needs an amount above zero.")
    if from_account.currency != to_account.currency:
        raise ValueError(
            f"Can't transfer from {from_account.currency} to {to_account.currency}: "
            "no conversion happens, so both accounts need the same currency."
        )
    reason = f"Transfer from '{from_account.name}' to '{to_account.name}'"
    with transaction.atomic(using=router.db_for_write(Transaction)):
        payee = Payee.system(Payee.TRANSFER_MONEY)
        with system_action(reason):
            payment = Transaction.objects.create(
                account=from_account,
                date=on,
                payee=payee,
                category=from_account.ensure_transfer_category(),
                memo=f"Transfer to {to_account.name}",
                amount=-amount,
            )
            deposit = Transaction.objects.create(
                account=to_account,
                date=on,
                payee=payee,
                category=to_account.ensure_transfer_category(),
                memo=f"Transfer from {from_account.name}",
                amount=amount,
            )
    return payment, deposit
