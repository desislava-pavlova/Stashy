"""Tests for Phase 5: transaction list filters/sorting, transaction numbers, inline add row."""
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import TransactionForm
from .models import Account, Category, Transaction

TODAY = date(2026, 9, 13)


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


@mock.patch("finance.reporting.date")
class DefaultViewTests(LoggedInTestCase):
    """The transaction list defaults to this year, until you explicitly ask otherwise."""

    def setUp(self):
        super().setUp()
        Transaction.objects.create(account=self.account, date=date(2026, 3, 1), amount=Decimal("-1.00"))
        Transaction.objects.create(account=self.account, date=date(2025, 12, 31), amount=Decimal("-1.00"))

    def shown(self, mock_date, **params):
        mock_date.today.return_value = TODAY
        response = self.client.get(reverse("finance:transaction_list"), params)
        return {t.pk for t in response.context["transactions"]}, response

    def test_no_filters_at_all_defaults_to_this_year(self, mock_date):
        shown, response = self.shown(mock_date)
        self.assertEqual(len(shown), 1)
        self.assertEqual(response.context["range"], "this_year")

    def test_explicit_all_time_shows_everything(self, mock_date):
        shown, response = self.shown(mock_date, range="")
        self.assertEqual(len(shown), 2)
        self.assertEqual(response.context["range"], "")

    def test_a_named_preset_is_respected(self, mock_date):
        shown, response = self.shown(mock_date, range="last_year")
        self.assertEqual(len(shown), 1)

    def test_explicit_custom_dates_are_marked_as_custom(self, mock_date):
        shown, response = self.shown(mock_date, start="2025-01-01", end="2025-12-31")
        self.assertEqual(response.context["range"], "custom")
        self.assertEqual(len(shown), 1)


class ReconciledFilterTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.reconciled = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("-1.00"), reconciled=True,
        )
        self.unreconciled = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 2), amount=Decimal("-1.00"), reconciled=False,
        )

    def shown(self, **params):
        params.setdefault("range", "")  # don't let the this-year default interfere
        response = self.client.get(reverse("finance:transaction_list"), params)
        return {t.pk for t in response.context["transactions"]}

    def test_reconciled_only(self):
        self.assertEqual(self.shown(status="reconciled"), {self.reconciled.pk})

    def test_unreconciled_only(self):
        self.assertEqual(self.shown(status="unreconciled"), {self.unreconciled.pk})

    def test_no_status_filter_shows_both(self):
        self.assertEqual(self.shown(), {self.reconciled.pk, self.unreconciled.pk})


class SortingTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.other = Account.objects.create(name="Savings")
        self.big = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("-50.00"))
        self.small = Transaction.objects.create(account=self.other, date=date(2026, 1, 2), amount=Decimal("-5.00"))

    def ordered(self, sort):
        response = self.client.get(reverse("finance:transaction_list"), {"range": "", "sort": sort})
        return [t.pk for t in response.context["transactions"]]

    def test_sort_by_amount_ascending(self):
        self.assertEqual(self.ordered("amount"), [self.big.pk, self.small.pk])

    def test_sort_by_amount_descending(self):
        self.assertEqual(self.ordered("-amount"), [self.small.pk, self.big.pk])

    def test_sort_by_account_name(self):
        # "Checking" sorts before "Savings".
        self.assertEqual(self.ordered("account"), [self.big.pk, self.small.pk])

    def test_an_unknown_sort_column_is_ignored_not_a_crash(self):
        response = self.client.get(reverse("finance:transaction_list"), {"range": "", "sort": "nonsense"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort"], "")

    def test_account_register_sorts_within_one_account_too(self):
        older = Transaction.objects.create(account=self.account, date=date(2025, 1, 1), amount=Decimal("-1.00"))
        response = self.client.get(reverse("finance:account_detail", args=[self.account.pk]), {"sort": "date"})
        pks = [t.pk for t in response.context["transactions"]]
        self.assertEqual(pks, [older.pk, self.big.pk])


class TransactionNumberFormTests(TestCase):
    def test_form_includes_transaction_number(self):
        self.assertIn("transaction_number", TransactionForm().fields)

    def test_clearing_the_number_on_edit_reassigns_the_next_one(self):
        account = Account.objects.create(name="Checking")
        Transaction.objects.create(account=account, date=date(2026, 1, 1), amount=Decimal("-1.00"))
        txn = Transaction.objects.create(account=account, date=date(2026, 1, 2), amount=Decimal("-1.00"))
        self.assertEqual(txn.transaction_number, 2)

        form = TransactionForm({
            "account": account.pk, "date": "02/01/2026", "payee_name": "", "category": "",
            "memo": "", "amount": "-1.00", "transaction_number": "",
        }, instance=txn)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        # "Next" is computed fresh from what's in the database, including this record's own
        # not-yet-saved old number (2) - so clearing it lands one past that, at 3, not back at 2.
        self.assertEqual(saved.transaction_number, 3)

    def test_a_chosen_number_is_kept(self):
        account = Account.objects.create(name="Checking")
        txn = Transaction.objects.create(account=account, date=date(2026, 1, 1), amount=Decimal("-1.00"))
        form = TransactionForm({
            "account": account.pk, "date": "01/01/2026", "payee_name": "", "category": "",
            "memo": "", "amount": "-1.00", "transaction_number": "500",
        }, instance=txn)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().transaction_number, 500)


class PaymentDepositColumnTests(LoggedInTestCase):
    def test_payment_and_deposit_show_in_separate_columns(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("-20.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 2), amount=Decimal("30.00"))
        response = self.client.get(reverse("finance:account_detail", args=[self.account.pk]))
        html = response.content.decode()
        # Payment shows unsigned (the column itself already means "money out") - no "-20.00".
        self.assertIn('amount-negative">20.00', html.replace(" ", ""))
        self.assertNotIn('amount-negative">-20.00', html.replace(" ", ""))
        self.assertIn('amount-positive">30.00', html.replace(" ", ""))
