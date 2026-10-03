"""Reconciling an account against a bank statement (Phase 8's Quicken-style flow)."""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from django.db import router, transaction

from audit.context import system_action

from .. import regional
from ..models import Account, Payee, Reconciliation, Transaction
from ..money import CENT, ZERO


class UnacceptedDifferenceError(Exception):
    """The checked transactions don't add up to the statement balance, and the user hasn't
    agreed to an adjustment for the gap. Nothing was saved."""

    def __init__(self, difference: Decimal):
        super().__init__(f"Unaccepted reconciliation difference of {difference}")
        self.difference = difference


def reconcile(
    account: Account,
    statement_date: date,
    statement_balance: Decimal,
    transactions: Iterable[Transaction],
    accept_difference: bool = False,
) -> Reconciliation:
    """Mark `transactions` reconciled and record the reconciliation.

    A gap between the statement and the checked transactions raises UnacceptedDifferenceError,
    unless `accept_difference` is set: then an adjustment transaction closes the gap.
    """
    transactions = list(transactions)
    opening_balance = account.reconciled_balance()
    checked_total = sum((txn.amount for txn in transactions), ZERO)
    gap = (statement_balance - (opening_balance + checked_total)).quantize(CENT)
    if gap != ZERO and not accept_difference:
        raise UnacceptedDifferenceError(gap)

    adjustment = None
    reason = f"Account '{account.name}' reconciled to {regional.format_date(statement_date)}"
    with transaction.atomic(using=router.db_for_write(Reconciliation)), system_action(reason):
        for txn in transactions:
            txn.reconciled = True
            txn.save(update_fields=["reconciled"])
        if gap != ZERO:
            # Not real income/spending - excluded from reports the same way a transfer or
            # opening balance is (D1/D7), since it's a bookkeeping correction, not a purchase.
            adjustment = Transaction.objects.create(
                account=account,
                date=statement_date,
                payee=Payee.system(Payee.RECONCILIATION_ADJUSTMENT),
                category=account.ensure_transfer_category(),
                memo="Reconciliation adjustment",
                amount=gap,
                reconciled=True,
            )
        return Reconciliation.objects.create(
            account=account,
            statement_date=statement_date,
            statement_balance=statement_balance,
            opening_balance=opening_balance,
            adjustment_transaction=adjustment,
        )
