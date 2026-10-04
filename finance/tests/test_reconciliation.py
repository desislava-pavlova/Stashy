"""Reconciling an account against a statement: the button, the two steps, the checklist and
the adjustment transaction."""

from datetime import date
from decimal import Decimal

from django.urls import reverse

from ..models import Account, Payee, Reconciliation, Transaction
from ..services import reconciliation
from .base import LoggedInWithAccountTestCase, StashTestCase


class ReconciledBalanceTests(LoggedInWithAccountTestCase):
    def test_only_reconciled_transactions_count(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00"), reconciled=True
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 2), amount=Decimal("-20.00"), reconciled=False
        )
        self.assertEqual(self.account.reconciled_balance(), Decimal("100.00"))


class ReconcileStartTests(LoggedInWithAccountTestCase):
    def test_shows_the_reconciled_balance_as_the_opening_balance(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("50.00"), reconciled=True
        )
        response = self.client.get(reverse("finance:account_reconcile", args=[self.account.pk]))
        self.assertEqual(response.context["opening_balance"], Decimal("50.00"))

    def test_posting_the_statement_shows_the_checklist(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), amount=Decimal("-30.00"))
        response = self.client.post(
            reverse("finance:account_reconcile", args=[self.account.pk]),
            {
                "statement_date": "31/01/2026",
                "statement_balance": "-30.00",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "finance/account_reconcile_review.html")
        self.assertEqual(len(response.context["payments"]), 1)
        self.assertEqual(len(response.context["deposits"]), 0)


class ReconcileChecklistSplitTests(LoggedInWithAccountTestCase):
    def test_deposits_and_payments_split_by_sign_and_exclude_already_reconciled_and_future(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 2), amount=Decimal("-40.00"))
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 3), amount=Decimal("-10.00"), reconciled=True
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 2, 15), amount=Decimal("-5.00")
        )  # after the statement
        response = self.client.post(
            reverse("finance:account_reconcile", args=[self.account.pk]),
            {
                "statement_date": "31/01/2026",
                "statement_balance": "60.00",
            },
        )
        deposit_amounts = [t.amount for t in response.context["deposits"]]
        payment_amounts = [t.amount for t in response.context["payments"]]
        self.assertEqual(deposit_amounts, [Decimal("100.00")])
        self.assertEqual(payment_amounts, [Decimal("-40.00")])

    def test_checklist_starts_with_nothing_checked(self):
        t1 = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("10.00"))
        t2 = Transaction.objects.create(account=self.account, date=date(2026, 1, 2), amount=Decimal("5.00"))
        response = self.client.post(
            reverse("finance:account_reconcile", args=[self.account.pk]),
            {
                "statement_date": "31/01/2026",
                "statement_balance": "15.00",
            },
        )
        by_pk = {t.pk: t.was_checked for t in response.context["deposits"]}
        self.assertFalse(by_pk[t1.pk])
        self.assertFalse(by_pk[t2.pk])


class ReconcileFinishTests(LoggedInWithAccountTestCase):
    def _finish(self, statement_date, statement_balance, checked, accept=False):
        data = {
            "stage": "finish",
            "statement_date": statement_date,
            "statement_balance": statement_balance,
            "transaction": [str(t.pk) for t in checked],
        }
        if accept:
            data["accept_difference"] = "1"
        return self.client.post(reverse("finance:account_reconcile", args=[self.account.pk]), data)

    def test_exact_match_marks_checked_transactions_reconciled(self):
        t1 = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00"))
        t2 = Transaction.objects.create(account=self.account, date=date(2026, 1, 2), amount=Decimal("-40.00"))
        response = self._finish("2026-01-31", "60.00", [t1, t2])
        self.assertRedirects(response, reverse("finance:account_detail", args=[self.account.pk]))
        t1.refresh_from_db()
        t2.refresh_from_db()
        self.assertTrue(t1.reconciled)
        self.assertTrue(t2.reconciled)
        self.assertEqual(Reconciliation.objects.count(), 1)
        rec = Reconciliation.objects.get()
        self.assertEqual(rec.statement_balance, Decimal("60.00"))
        self.assertEqual(rec.opening_balance, Decimal("0.00"))
        self.assertIsNone(rec.adjustment_transaction)

    def test_unchecked_transactions_are_left_alone(self):
        t1 = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00"))
        t2 = Transaction.objects.create(account=self.account, date=date(2026, 1, 2), amount=Decimal("-40.00"))
        self._finish("2026-01-31", "100.00", [t1])
        t2.refresh_from_db()
        self.assertFalse(t2.reconciled)

    def test_a_difference_not_accepted_shows_an_error_and_changes_nothing(self):
        t1 = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00"))
        response = self._finish("2026-01-31", "90.00", [t1])
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "finance/account_reconcile_review.html")
        t1.refresh_from_db()
        self.assertFalse(t1.reconciled)
        self.assertEqual(Reconciliation.objects.count(), 0)

    def test_an_accepted_difference_creates_an_adjustment_transaction(self):
        t1 = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00"))
        response = self._finish("2026-01-31", "90.00", [t1], accept=True)
        self.assertRedirects(response, reverse("finance:account_detail", args=[self.account.pk]))
        t1.refresh_from_db()
        self.assertTrue(t1.reconciled)
        rec = Reconciliation.objects.get()
        self.assertIsNotNone(rec.adjustment_transaction)
        adjustment = rec.adjustment_transaction
        self.assertEqual(adjustment.amount, Decimal("-10.00"))
        self.assertEqual(adjustment.payee.name, Payee.RECONCILIATION_ADJUSTMENT)
        self.assertTrue(adjustment.reconciled)
        self.assertEqual(adjustment.category, self.account.ensure_transfer_category())
        # The account now balances exactly to the statement.
        self.assertEqual(self.account.balance, Decimal("90.00"))

    def test_reconciliation_adjustment_is_excluded_from_autofill_suggestions(self):
        t1 = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00"))
        self._finish("2026-01-31", "90.00", [t1], accept=True)
        response = self.client.get(reverse("finance:api_payee_list"))
        self.assertNotIn(Payee.RECONCILIATION_ADJUSTMENT, response.json()["payees"])


class ReconcileButtonTests(LoggedInWithAccountTestCase):
    def test_reconcile_button_on_account_page(self):
        response = self.client.get(reverse("finance:account_detail", args=[self.account.pk]))
        self.assertContains(response, reverse("finance:account_reconcile", args=[self.account.pk]))


class ReconcileChecklistDisplayTests(LoggedInWithAccountTestCase):
    def test_payments_show_with_a_negative_sign(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("-40.00"))
        response = self.client.post(
            reverse("finance:account_reconcile", args=[self.account.pk]),
            {
                "statement_date": "31/01/2026",
                "statement_balance": "-40.00",
            },
        )
        self.assertContains(response, "-40.00")

    def test_column_totals_shown(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 2), amount=Decimal("30.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 3), amount=Decimal("-40.00"))
        response = self.client.post(
            reverse("finance:account_reconcile", args=[self.account.pk]),
            {
                "statement_date": "31/01/2026",
                "statement_balance": "90.00",
            },
        )
        self.assertEqual(response.context["deposits_total"], Decimal("130.00"))
        self.assertEqual(response.context["payments_total"], Decimal("-40.00"))
        self.assertContains(response, "130.00")

    def test_mark_all_button_present(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00"))
        response = self.client.post(
            reverse("finance:account_reconcile", args=[self.account.pk]),
            {
                "statement_date": "31/01/2026",
                "statement_balance": "100.00",
            },
        )
        self.assertContains(response, 'id="mark-all-btn"')
        self.assertContains(response, "Mark all")


class ReconcileFinishFormTests(LoggedInWithAccountTestCase):
    """1.6 (F5): a garbled finish step gets a message, never a server error."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.txn = Transaction.objects.create(account=cls.account, date=date(2026, 9, 1), amount=Decimal("-20.00"))
        cls.url = reverse("finance:account_reconcile", args=[cls.account.pk])

    def finish(self, **data):
        return self.client.post(self.url, {"stage": "finish", **data})

    def test_missing_or_garbled_values_restart_with_a_message(self):
        for data in (
            {},
            {"statement_date": "yesterday", "statement_balance": "-20.00"},
            {"statement_date": "2026-09-30", "statement_balance": "lots"},
            {"statement_date": "2026-09-30", "statement_balance": "-20.00", "transaction": "abc"},
        ):
            with self.subTest(data=data):
                response = self.finish(**data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "didn&#x27;t come through")
        self.txn.refresh_from_db()
        self.assertFalse(self.txn.reconciled)

    def test_a_valid_finish_still_reconciles(self):
        response = self.finish(statement_date="2026-09-30", statement_balance="-20.00", transaction=self.txn.pk)
        self.assertRedirects(response, reverse("finance:account_detail", args=[self.account.pk]))
        self.txn.refresh_from_db()
        self.assertTrue(self.txn.reconciled)


class ReconcileTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.account = Account.objects.create(name="Wallet")
        cls.pay = Transaction.objects.create(account=cls.account, date=date(2026, 1, 5), amount=Decimal("100.00"))
        cls.buy = Transaction.objects.create(account=cls.account, date=date(2026, 1, 6), amount=Decimal("-30.00"))

    def test_a_matching_statement_marks_the_transactions_reconciled(self):
        done = reconciliation.reconcile(self.account, date(2026, 1, 31), Decimal("70.00"), [self.pay, self.buy])
        self.assertIsNone(done.adjustment_transaction)
        self.assertEqual(done.opening_balance, Decimal("0"))
        self.assertEqual(self.account.transactions.filter(reconciled=True).count(), 2)

    def test_an_unaccepted_difference_saves_nothing(self):
        with self.assertRaises(reconciliation.UnacceptedDifferenceError) as caught:
            reconciliation.reconcile(self.account, date(2026, 1, 31), Decimal("75.00"), [self.pay, self.buy])
        self.assertEqual(caught.exception.difference, Decimal("5.00"))
        self.assertFalse(Reconciliation.objects.exists())
        self.assertFalse(self.account.transactions.filter(reconciled=True).exists())

    def test_an_accepted_difference_gets_an_adjustment_outside_the_reports(self):
        done = reconciliation.reconcile(
            self.account, date(2026, 1, 31), Decimal("65.00"), [self.pay], accept_difference=True
        )
        adjustment = done.adjustment_transaction
        self.assertEqual((adjustment.amount, adjustment.reconciled), (Decimal("-35.00"), True))
        self.assertEqual(adjustment.category, self.account.ensure_transfer_category())
        self.assertEqual(self.account.reconciled_balance(), Decimal("65.00"))
        self.assertFalse(Transaction.objects.reportable().filter(pk=adjustment.pk).exists())
