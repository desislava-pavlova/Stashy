"""Reconciling an account against a statement: the button, the two
steps, the checklist and the adjustment transaction."""

from datetime import date
from decimal import Decimal

from django.urls import reverse

from ..models import Account, Payee, Reconciliation, Transaction
from ..services import reconciliation
from .base import AMOUNTS_THAT_CANCEL_OUT, LoggedInWithAccountTestCase, StashTestCase


class ReconciledBalanceRoundingTests(LoggedInWithAccountTestCase):
    """SQLite adds decimals as floats, so -0.10, -0.20 and 0.30 come
    back as a tiny negative number. The balance shows exactly 0.00."""

    def test_amounts_that_cancel_out_give_a_plain_zero(self):
        for amount in AMOUNTS_THAT_CANCEL_OUT:
            Transaction.objects.create(
                account=self.account,
                date=date(2026, 1, 5),
                amount=Decimal(amount),
                reconciled=True,
            )
        self.assertEqual(str(self.account.reconciled_balance()), "0.00")

    def test_a_balance_that_is_not_zero_has_exactly_two_decimals(self):
        for amount in ("0.10", "0.20", "0.30"):
            Transaction.objects.create(
                account=self.account,
                date=date(2026, 1, 5),
                amount=Decimal(amount),
                reconciled=True,
            )
        self.assertEqual(str(self.account.reconciled_balance()), "0.60")

    def test_with_nothing_reconciled_it_is_0_00(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), amount=Decimal("5.00")
        )
        self.assertEqual(str(self.account.reconciled_balance()), "0.00")


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
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), amount=Decimal("-30.00")
        )
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
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 2), amount=Decimal("-40.00")
        )
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
        t1 = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("10.00")
        )
        t2 = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 2), amount=Decimal("5.00")
        )
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
        t1 = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00")
        )
        t2 = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 2), amount=Decimal("-40.00")
        )
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
        t1 = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00")
        )
        t2 = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 2), amount=Decimal("-40.00")
        )
        self._finish("2026-01-31", "100.00", [t1])
        t2.refresh_from_db()
        self.assertFalse(t2.reconciled)

    def test_a_difference_not_accepted_shows_an_error_and_changes_nothing(self):
        t1 = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00")
        )
        response = self._finish("2026-01-31", "90.00", [t1])
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "finance/account_reconcile_review.html")
        t1.refresh_from_db()
        self.assertFalse(t1.reconciled)
        self.assertEqual(Reconciliation.objects.count(), 0)

    def test_a_refused_difference_keeps_the_ticks_and_says_how_big_it_is(self):
        """The checklist comes back with the same rows ticked, so one
        wrong statement balance doesn't cost the ticking."""
        ticked = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00")
        )
        not_ticked = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 2), amount=Decimal("5.00")
        )

        response = self._finish("2026-01-31", "90.00", [ticked])

        by_pk = {t.pk: t.was_checked for t in response.context["deposits"]}
        self.assertEqual(by_pk, {ticked.pk: True, not_ticked.pk: False})
        messages = [str(message) for message in response.context["messages"]]
        self.assertTrue(any(m.startswith("That leaves a difference of") for m in messages))

    def test_an_accepted_difference_creates_an_adjustment_transaction(self):
        t1 = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00")
        )
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
        t1 = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00")
        )
        self._finish("2026-01-31", "90.00", [t1], accept=True)
        response = self.client.get(reverse("finance:api_payee_list"))
        self.assertNotIn(Payee.RECONCILIATION_ADJUSTMENT, response.json()["payees"])


class ReconcileButtonTests(LoggedInWithAccountTestCase):
    def test_reconcile_button_on_account_page(self):
        response = self.client.get(reverse("finance:account_detail", args=[self.account.pk]))
        self.assertContains(response, reverse("finance:account_reconcile", args=[self.account.pk]))


class ReconcileChecklistDisplayTests(LoggedInWithAccountTestCase):
    def test_payments_show_with_a_negative_sign(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("-40.00")
        )
        response = self.client.post(
            reverse("finance:account_reconcile", args=[self.account.pk]),
            {
                "statement_date": "31/01/2026",
                "statement_balance": "-40.00",
            },
        )
        # The row's own cell: "-40.00" is also in the statement balance
        # box, a hidden field.
        self.assertContains(response, 'text-nowrap amount-negative">-40.00', count=1)

    def test_column_totals_shown(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 2), amount=Decimal("30.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 3), amount=Decimal("-40.00")
        )
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
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("100.00")
        )
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
    """1.6 (F5): a garbled finish step gets a message, never a server
    error."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.txn = Transaction.objects.create(
            account=cls.account, date=date(2026, 9, 1), amount=Decimal("-20.00")
        )
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

    def test_a_garbled_finish_shows_the_first_step_again_with_a_fresh_form(self):
        response = self.finish(statement_date="yesterday", statement_balance="lots")

        self.assertTemplateUsed(response, "finance/account_reconcile_start.html")
        self.assertTemplateNotUsed(response, "finance/account_reconcile_review.html")
        self.assertFalse(response.context["form"].is_bound)
        self.assertEqual(response.context["account"], self.account)

    def test_a_transaction_id_too_big_for_the_database_restarts_with_a_message(self):
        """S106: Django's multi-choice field put the number into the
        query, and SQLite refuses one over 2**63 - 1 with an
        OverflowError, which was a 500 page. The ticked row beside it
        must not be reconciled either: a garbled finish saves nothing.
        (2**63 - 1 itself is the boundary: it fits, so it never
        overflowed, and is an ordinary id that isn't a choice.)"""
        for too_big in ("9" * 30, str(2**63), str(2**63 - 1)):
            with self.subTest(too_big=too_big):
                response = self.finish(
                    statement_date="2026-09-30",
                    statement_balance="-20.00",
                    transaction=[self.txn.pk, too_big],
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "didn&#x27;t come through")
        self.txn.refresh_from_db()
        self.assertFalse(self.txn.reconciled)

    def test_a_transaction_that_is_not_open_on_this_account_restarts_with_a_message(self):
        """The ticked ids are looked up among this account's
        unreconciled transactions only: a hand-made request can't
        reconcile another account's transaction through this page, count
        one that is already reconciled, or name one that doesn't exist.
        """
        savings = Account.objects.create(name="Savings")
        elsewhere = Transaction.objects.create(
            account=savings, date=date(2026, 9, 1), amount=Decimal("5.00")
        )
        done = Transaction.objects.create(
            account=self.account, date=date(2026, 8, 1), amount=Decimal("1.00"), reconciled=True
        )
        for foreign_id in (elsewhere.pk, done.pk, 10**9):
            with self.subTest(foreign_id=foreign_id):
                response = self.finish(
                    statement_date="2026-09-30",
                    statement_balance="-20.00",
                    transaction=[self.txn.pk, foreign_id],
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "didn&#x27;t come through")
        elsewhere.refresh_from_db()
        self.txn.refresh_from_db()
        self.assertFalse(elsewhere.reconciled)
        self.assertFalse(self.txn.reconciled)
        self.assertFalse(Reconciliation.objects.exists())

    def test_nothing_ticked_is_a_finish_like_any_other_not_a_garbled_one(self):
        """The ticked transactions are optional: with none ticked, the
        statement is simply compared with what is already reconciled
        (here, nothing)."""
        agreeing = self.finish(statement_date="2026-09-30", statement_balance="0.00")
        self.assertRedirects(agreeing, reverse("finance:account_detail", args=[self.account.pk]))
        self.assertEqual(Reconciliation.objects.count(), 1)
        self.txn.refresh_from_db()
        self.assertFalse(self.txn.reconciled)

    def test_nothing_ticked_and_a_statement_that_disagrees_shows_the_difference(self):
        response = self.finish(statement_date="2026-09-30", statement_balance="-20.00")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "finance/account_reconcile_review.html")
        self.assertNotContains(response, "didn&#x27;t come through")
        self.assertFalse(Reconciliation.objects.exists())

    def test_a_valid_finish_still_reconciles(self):
        response = self.finish(
            statement_date="2026-09-30", statement_balance="-20.00", transaction=self.txn.pk
        )
        self.assertRedirects(response, reverse("finance:account_detail", args=[self.account.pk]))
        self.txn.refresh_from_db()
        self.assertTrue(self.txn.reconciled)


class ReconcileSizeTests(LoggedInWithAccountTestCase):
    """A gap is the difference of two figures that are each inside
    the limit for an amount, and so is an opening balance (a sum):
    either can be too big. SQLite stores such a number, but Django
    can't read it back, so the account's register, the Transactions
    page and the delete pages all failed until the row was mended
    by hand."""

    def finish(self, statement_balance):
        return self.client.post(
            reverse("finance:account_reconcile", args=[self.account.pk]),
            {
                "stage": "finish",
                "statement_date": "2026-02-01",
                "statement_balance": statement_balance,
                "accept_difference": "on",
            },
        )

    def reconciled(self, amount):
        return Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal(amount), reconciled=True
        )

    def test_a_gap_too_big_for_an_amount_is_refused_even_when_accepted(self):
        self.reconciled("-500")
        with self.assertRaises(reconciliation.AmountTooBigError) as caught:
            reconciliation.reconcile(
                self.account,
                date(2026, 2, 1),
                Decimal("9999999999.00"),
                [],
                accept_difference=True,
            )
        self.assertEqual(caught.exception.amount, Decimal("10000000499.00"))
        self.assertFalse(Reconciliation.objects.exists())
        self.assertEqual(Transaction.objects.count(), 1)

    def test_an_opening_balance_too_big_for_an_amount_is_refused(self):
        for _ in range(2):
            self.reconciled("9999999999.99")
        with self.assertRaises(reconciliation.AmountTooBigError) as caught:
            reconciliation.reconcile(self.account, date(2026, 2, 1), Decimal("19999999999.98"), [])
        self.assertEqual(caught.exception.amount, Decimal("19999999999.98"))
        self.assertFalse(Reconciliation.objects.exists())

    def test_the_biggest_gap_that_fits_is_still_accepted_and_one_cent_more_is_not(self):
        done = reconciliation.reconcile(
            self.account, date(2026, 2, 1), Decimal("9999999999.99"), [], accept_difference=True
        )
        self.assertEqual(done.adjustment_transaction.amount, Decimal("9999999999.99"))
        Reconciliation.objects.all().delete()
        Transaction.objects.all().delete()
        self.reconciled("-0.01")
        with self.assertRaises(reconciliation.AmountTooBigError):
            reconciliation.reconcile(
                self.account,
                date(2026, 2, 1),
                Decimal("9999999999.99"),
                [],
                accept_difference=True,
            )

    def test_the_page_says_so_and_saves_nothing_and_the_account_still_opens(self):
        self.reconciled("-500")
        response = self.finish("9999999999.00")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "finance/account_reconcile_review.html")
        self.assertContains(response, "more than Stashy can store")
        # The two figures in the message: the one that is too big, and
        # the largest that fits.
        self.assertContains(
            response,
            "A figure in that reconciliation, 10\N{NO-BREAK SPACE}000\N{NO-BREAK SPACE}000"
            "\N{NO-BREAK SPACE}499.00 \N{EURO SIGN}, is more than Stashy can store "
            "(9\N{NO-BREAK SPACE}999\N{NO-BREAK SPACE}999\N{NO-BREAK SPACE}999.99 "
            "\N{EURO SIGN}).",
        )
        self.assertFalse(Reconciliation.objects.exists())
        self.assertEqual(Transaction.objects.count(), 1)
        register = self.client.get(reverse("finance:account_detail", args=[self.account.pk]))
        self.assertEqual(register.status_code, 200)


class ReconcileTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.account = Account.objects.create(name="Wallet")
        cls.pay = Transaction.objects.create(
            account=cls.account, date=date(2026, 1, 5), amount=Decimal("100.00")
        )
        cls.buy = Transaction.objects.create(
            account=cls.account, date=date(2026, 1, 6), amount=Decimal("-30.00")
        )

    def test_a_matching_statement_marks_the_transactions_reconciled(self):
        done = reconciliation.reconcile(
            self.account, date(2026, 1, 31), Decimal("70.00"), [self.pay, self.buy]
        )
        self.assertIsNone(done.adjustment_transaction)
        self.assertEqual(done.opening_balance, Decimal("0"))
        self.assertEqual(self.account.transactions.filter(reconciled=True).count(), 2)

    def test_an_unaccepted_difference_saves_nothing(self):
        with self.assertRaises(reconciliation.UnacceptedDifferenceError) as caught:
            reconciliation.reconcile(
                self.account, date(2026, 1, 31), Decimal("75.00"), [self.pay, self.buy]
            )
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
