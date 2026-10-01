"""Tests for the transactions list's Edit mode (2026-09-22) - inline row editing that autosaves
each field via transaction_quick_edit, without leaving the list page."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Account, Category, Payee, Transaction


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)


class TransactionListEditModeRenderTests(LoggedInTestCase):
    def test_list_page_renders_edit_mode_controls_for_a_transaction(self):
        account = Account.objects.create(name="Checking")
        category = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=account,
            date=date(2026, 9, 20),
            category=category,
            memo="Lidl",
            amount=Decimal("-12.50"),
        )
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('id="edit-mode-toggle"', content)
        self.assertIn('data-field="category"', content)
        self.assertIn('data-field="reconciled"', content)
        self.assertIn(reverse("finance:transaction_quick_edit", args=[0]), content)

    def test_a_transfer_categorys_own_transaction_still_offers_it_in_the_dropdown(self):
        # Transfer categories are excluded from the shared options list (like the full edit
        # form) but a transaction already filed under one must still show it, selected.
        source = Account.objects.create(name="Checking")
        destination = Account.objects.create(name="Savings")
        transfer_category = destination.transfer_category
        Transaction.objects.create(
            account=source,
            date=date(2026, 9, 20),
            category=transfer_category,
            amount=Decimal("-100"),
        )
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertContains(response, f'value="{transfer_category.pk}" selected')


class TransactionQuickEditTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.account = Account.objects.create(name="Checking")
        self.category = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        self.txn = Transaction.objects.create(
            account=self.account,
            date=date(2026, 9, 20),
            category=self.category,
            memo="Lidl",
            amount=Decimal("-12.50"),
        )

    def _post(self, **overrides):
        data = {
            "account": self.account.pk,
            "date": "20/09/2026",
            "transaction_number": self.txn.transaction_number,
            "payee_name": "Lidl",
            "category": self.category.pk,
            "memo": "Lidl",
            "amount": "-12.50",
        }
        data.update(overrides)
        return self.client.post(reverse("finance:transaction_quick_edit", args=[self.txn.pk]), data)

    def test_editing_the_memo_saves_and_returns_the_updated_row(self):
        response = self._post(memo="Lidl - weekly shop")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["row"]["memo"], "Lidl - weekly shop")
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.memo, "Lidl - weekly shop")

    def test_switching_to_a_deposit_flips_the_sign(self):
        response = self._post(amount="40.00")
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.amount, Decimal("40.00"))
        self.assertEqual(payload["row"]["payment"], "")
        self.assertNotEqual(payload["row"]["deposit"], "")

    def test_changing_the_payee_resolves_or_creates_a_payee_like_the_full_form(self):
        response = self._post(payee_name="Rewe")
        self.assertTrue(response.json()["ok"])
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.payee.name, "Rewe")
        self.assertTrue(Payee.objects.filter(name="Rewe").exists())

    def test_invalid_date_is_rejected_without_saving(self):
        response = self._post(date="not-a-date")
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertIn("date", payload["errors"])
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.date, date(2026, 9, 20))

    def test_moving_to_an_inactive_account_is_blocked_with_a_clear_error(self):
        inactive = Account.objects.create(name="Old account", is_active=False)
        response = self._post(account=inactive.pk)
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertIn("account", payload["errors"])
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.account_id, self.account.pk)

    def test_toggling_reconciled_on(self):
        response = self._post(reconciled="on")
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["row"]["reconciled"])
        self.txn.refresh_from_db()
        self.assertTrue(self.txn.reconciled)

    def test_get_is_not_allowed(self):
        response = self.client.get(reverse("finance:transaction_quick_edit", args=[self.txn.pk]))
        self.assertEqual(response.status_code, 405)
