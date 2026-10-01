"""Tests for the 2026-09-15 Reports/Accounts/Payees follow-up batch: inactive accounts must
stay at a zero balance, entering a transaction on an inactive account asks first instead of
silently blocking or silently reactivating, the payee list's category filter is ordered like
every other category dropdown, the transaction list's Total carries a currency symbol, and a
couple of pieces of now-removed helper text stay gone."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import AccountCreateForm, AccountForm
from .models import Account, Category, Transaction


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)


class InactiveAccountBalanceValidationTests(LoggedInTestCase):
    def test_cannot_deactivate_an_account_with_a_nonzero_balance(self):
        account = Account.objects.create(name="Checking")
        Transaction.objects.create(account=account, date=date(2026, 1, 1), amount=Decimal("50.00"))
        form = AccountForm(
            {"name": "Checking", "account_type": "cash", "currency": "USD", "institution": "", "is_active": False, "notes": ""},
            instance=account,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("is_active", form.errors)

    def test_can_deactivate_an_account_with_a_zero_balance(self):
        account = Account.objects.create(name="Checking")
        Transaction.objects.create(account=account, date=date(2026, 1, 1), amount=Decimal("50.00"))
        Transaction.objects.create(account=account, date=date(2026, 1, 2), amount=Decimal("-50.00"))
        form = AccountForm(
            {"name": "Checking", "account_type": "cash", "currency": "USD", "institution": "", "is_active": False, "notes": ""},
            instance=account,
        )
        self.assertTrue(form.is_valid())

    def test_can_deactivate_an_account_with_no_transactions_at_all(self):
        account = Account.objects.create(name="Checking")
        form = AccountForm(
            {"name": "Checking", "account_type": "cash", "currency": "USD", "institution": "", "is_active": False, "notes": ""},
            instance=account,
        )
        self.assertTrue(form.is_valid())

    def test_a_new_account_cannot_start_inactive_with_an_opening_balance(self):
        form = AccountCreateForm({
            "name": "Checking", "account_type": "cash", "currency": "USD", "institution": "", "is_active": False,
            "notes": "", "opening_balance": "100.00", "opening_balance_date": "01/01/2026",
        })
        self.assertFalse(form.is_valid())

    def test_a_new_account_can_start_inactive_with_no_opening_balance(self):
        form = AccountCreateForm({
            "name": "Checking", "account_type": "cash", "currency": "USD", "institution": "", "is_active": False,
            "notes": "", "opening_balance": "", "opening_balance_date": "",
        })
        self.assertTrue(form.is_valid())


class RemovedHelperTextTests(LoggedInTestCase):
    def test_opening_balance_field_has_no_helper_text_on_create(self):
        response = self.client.get(reverse("finance:account_add"))
        self.assertNotContains(response, "What this account held")

    def test_currency_field_has_no_helper_text_on_edit(self):
        account = Account.objects.create(name="Checking")
        response = self.client.get(reverse("finance:account_edit", args=[account.pk]))
        self.assertNotContains(response, "Set when the account was created")


class InactiveAccountTransactionConfirmTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.account = Account.objects.create(name="Old Wallet", is_active=False)

    def test_posting_without_confirmation_asks_first_and_does_not_save(self):
        response = self.client.post(reverse("finance:transaction_add"), {
            "account": self.account.pk, "date": "01/03/2026", "payee_name": "Shop",
            "category": "", "memo": "", "amount": "-10.00",
        })
        self.assertContains(response, "is inactive")
        self.assertFalse(Transaction.objects.exists())
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_active)

    def test_confirming_saves_the_transaction_and_reactivates_the_account(self):
        response = self.client.post(reverse("finance:transaction_add"), {
            "account": self.account.pk, "date": "01/03/2026", "payee_name": "Shop",
            "category": "", "memo": "", "amount": "-10.00", "confirm_inactive_account": "1",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Transaction.objects.filter(account=self.account).exists())
        self.account.refresh_from_db()
        self.assertTrue(self.account.is_active)

    def test_an_already_active_account_never_shows_the_prompt(self):
        active = Account.objects.create(name="Checking")
        response = self.client.post(reverse("finance:transaction_add"), {
            "account": active.pk, "date": "01/03/2026", "payee_name": "Shop",
            "category": "", "memo": "", "amount": "-10.00",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Transaction.objects.filter(account=active).exists())


class PayeeListCategoryOrderTests(LoggedInTestCase):
    def test_category_filter_is_ordered_like_other_category_dropdowns(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Category.objects.create(name="Supermarket", parent=groceries, category_type=Category.CategoryType.EXPENSE)
        Category.objects.create(name="Bakery", category_type=Category.CategoryType.EXPENSE)
        response = self.client.get(reverse("finance:payee_list"))
        # "Bakery" sorts before "Groceries", and "Supermarket" (indented) sits right after
        # "Groceries" rather than in a separate block of subcategories.
        body = response.content.decode().replace("&nbsp;", " ")
        self.assertLess(body.index("Bakery"), body.index(">Groceries<"))
        self.assertLess(body.index(">Groceries<"), body.index(">    Supermarket<"))


class TransactionListTotalCurrencySymbolTests(LoggedInTestCase):
    def test_total_shows_a_currency_symbol(self):
        account = Account.objects.create(name="Checking", currency="USD")
        Transaction.objects.create(account=account, date=date.today(), amount=Decimal("100.00"))
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertContains(response, "$100.00")


class ReportsCollapseButtonLabelTests(LoggedInTestCase):
    def test_expand_button_relabels_to_collapse_not_contract(self):
        # The relabel happens client-side (reports.js), not in the server-rendered HTML, which
        # always starts on "Expand" - check the script itself rather than a page fetch.
        from pathlib import Path

        from django.conf import settings

        js = (Path(settings.BASE_DIR) / "finance" / "static" / "finance" / "js" / "reports.js").read_text(encoding="utf-8")
        self.assertIn("'Collapse'", js)
        self.assertNotIn("Contract", js)
