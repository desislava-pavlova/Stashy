"""Accounts: opening balances, transfers between accounts and their transfer categories,
grouping, inactive accounts, the account register's columns, the account forms, and saving
and deleting an account."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.urls import reverse

from ..forms import AccountCreateForm, AccountForm, CategoryForm
from ..models import Account, Category, Payee, Reminder, Transaction, grouped_accounts
from .base import LoggedInTestCase, LoggedInWithAccountTestCase, StashTestCase


class OpeningBalanceTests(LoggedInTestCase):
    def test_creating_an_account_with_an_opening_balance_makes_a_real_transaction(self):
        response = self.client.post(
            reverse("finance:account_add"),
            {
                "name": "Savings",
                "account_type": "savings",
                "currency": "EUR",
                "is_active": "on",
                "opening_balance": "500.00",
                "opening_balance_date": "01/01/2026",
            },
        )
        self.assertEqual(response.status_code, 302)
        account = Account.objects.get(name="Savings")
        self.assertEqual(account.balance, Decimal("500.00"))

        opening = Transaction.objects.get(account=account)
        self.assertEqual(opening.amount, Decimal("500.00"))
        self.assertEqual(opening.date, date(2026, 1, 1))
        self.assertEqual(opening.payee.name, Payee.OPENING_BALANCE)
        self.assertEqual(opening.category, account.transfer_category)
        self.assertEqual(opening.memo, "Opening Balance")

    def test_an_opening_balance_is_a_regular_editable_transaction(self):
        self.client.post(
            reverse("finance:account_add"),
            {
                "name": "Cash",
                "account_type": "cash",
                "currency": "EUR",
                "is_active": "on",
                "opening_balance": "20",
                "opening_balance_date": "01/01/2026",
            },
        )
        opening = Transaction.objects.get(account__name="Cash")
        response = self.client.post(
            reverse("finance:transaction_edit", args=[opening.pk]),
            {
                "account": opening.account_id,
                "date": "02/01/2026",
                "payee_name": "Opening Balance",
                "category": opening.category_id,
                "memo": "Fixed",
                "amount": "25.00",
            },
        )
        self.assertEqual(response.status_code, 302)
        opening.refresh_from_db()
        self.assertEqual(opening.amount, Decimal("25.00"))

    def test_no_opening_balance_still_makes_a_zero_dated_today_transaction(self):
        """2026-09-15 follow-up: every new account always gets an opening balance transaction,
        even a zero one dated today when the fields are left blank."""
        self.client.post(
            reverse("finance:account_add"),
            {
                "name": "Wallet",
                "account_type": "cash",
                "currency": "EUR",
                "is_active": "on",
            },
        )
        account = Account.objects.get(name="Wallet")
        self.assertEqual(account.balance, Decimal("0.00"))
        opening = Transaction.objects.get(account=account)
        self.assertEqual(opening.amount, Decimal("0.00"))
        self.assertEqual(opening.date, date.today())
        self.assertEqual(opening.payee.name, Payee.OPENING_BALANCE)


class TransferCategoryTests(StashTestCase):
    def test_every_account_gets_its_own_transfer_category(self):
        account = Account.objects.create(name="Checking")
        category = account.transfer_category
        self.assertEqual(category.category_type, Category.CategoryType.TRANSFER)
        self.assertTrue(category.is_system_managed)
        self.assertIn("Checking", category.name)

    def test_renaming_the_account_renames_its_transfer_category(self):
        account = Account.objects.create(name="Checking")
        account.name = "Everyday"
        account.save()
        account.transfer_category.refresh_from_db()
        self.assertIn("Everyday", account.transfer_category.name)

    def test_deleting_the_account_deletes_its_transfer_category(self):
        account = Account.objects.create(name="Checking")
        category_id = account.transfer_category.pk
        account.delete()
        self.assertFalse(Category.objects.filter(pk=category_id).exists())

    def test_category_form_rejects_a_system_managed_category(self):
        account = Account.objects.create(name="Checking")
        form = CategoryForm({"name": "Renamed", "category_type": "expense"}, instance=account.transfer_category)
        self.assertFalse(form.is_valid())

    def test_transfer_type_is_not_an_available_choice_when_creating_a_category(self):
        form = CategoryForm()
        self.assertNotIn(Category.CategoryType.TRANSFER, dict(form.fields["category_type"].choices))


class TransferButtonTests(LoggedInTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.checking = Account.objects.create(name="Checking")
        cls.savings = Account.objects.create(name="Savings")

    def test_transfer_creates_two_paired_transactions(self):
        response = self.client.post(
            reverse("finance:account_transfer", args=[self.checking.pk]),
            {
                "to_account": self.savings.pk,
                "amount": "100.00",
                "date": "05/01/2026",
            },
        )
        self.assertEqual(response.status_code, 302)

        out = Transaction.objects.get(account=self.checking)
        into = Transaction.objects.get(account=self.savings)
        self.assertEqual(out.amount, Decimal("-100.00"))
        self.assertEqual(into.amount, Decimal("100.00"))
        self.assertEqual(out.category, self.checking.transfer_category)
        self.assertEqual(into.category, self.savings.transfer_category)
        self.assertEqual(out.payee.name, Payee.TRANSFER_MONEY)
        self.assertEqual(out.payee_id, into.payee_id)
        self.assertIn("Savings", out.memo)
        self.assertIn("Checking", into.memo)

    def test_cannot_transfer_to_the_same_account(self):
        form_response = self.client.get(reverse("finance:account_transfer", args=[self.checking.pk]))
        options = form_response.context["form"].fields["to_account"].queryset
        self.assertNotIn(self.checking, options)


class AccountGroupingTests(StashTestCase):
    def test_groups_follow_the_liquidity_order_then_name(self):
        Account.objects.create(name="Zeta savings", account_type=Account.AccountType.SAVINGS)
        Account.objects.create(name="Alpha cash", account_type=Account.AccountType.CASH)
        Account.objects.create(name="Beta checking", account_type=Account.AccountType.DEBIT_CARD)
        Account.objects.create(name="Old wallet", account_type=Account.AccountType.CASH, is_active=False)

        groups = grouped_accounts(Account.objects.all())
        self.assertEqual([g["key"] for g in groups], ["physical", "digital", "illiquid", "inactive"])
        self.assertEqual([a.name for a in groups[0]["accounts"]], ["Alpha cash"])
        self.assertEqual([a.name for a in groups[-1]["accounts"]], ["Old wallet"])


class PaymentDepositColumnTests(LoggedInWithAccountTestCase):
    def test_payment_and_deposit_show_in_separate_columns(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("-20.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 2), amount=Decimal("30.00"))
        response = self.client.get(reverse("finance:account_detail", args=[self.account.pk]))
        html = response.content.decode()
        # Payment shows unsigned (the column itself already means "money out") - no "-20.00".
        self.assertIn('amount-negative">20.00', html.replace(" ", ""))
        self.assertNotIn('amount-negative">-20.00', html.replace(" ", ""))
        self.assertIn('amount-positive">30.00', html.replace(" ", ""))


class AccountFormNotesSizeTests(StashTestCase):
    def test_notes_widget_is_two_rows(self):
        form = AccountForm()
        self.assertEqual(form.fields["notes"].widget.attrs.get("rows"), 2)


class InactiveAccountBalanceValidationTests(LoggedInTestCase):
    def test_cannot_deactivate_an_account_with_a_nonzero_balance(self):
        account = Account.objects.create(name="Checking")
        Transaction.objects.create(account=account, date=date(2026, 1, 1), amount=Decimal("50.00"))
        form = AccountForm(
            {
                "name": "Checking",
                "account_type": "cash",
                "currency": "USD",
                "institution": "",
                "is_active": False,
                "notes": "",
            },
            instance=account,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("is_active", form.errors)

    def test_can_deactivate_an_account_with_a_zero_balance(self):
        account = Account.objects.create(name="Checking")
        Transaction.objects.create(account=account, date=date(2026, 1, 1), amount=Decimal("50.00"))
        Transaction.objects.create(account=account, date=date(2026, 1, 2), amount=Decimal("-50.00"))
        form = AccountForm(
            {
                "name": "Checking",
                "account_type": "cash",
                "currency": "USD",
                "institution": "",
                "is_active": False,
                "notes": "",
            },
            instance=account,
        )
        self.assertTrue(form.is_valid())

    def test_can_deactivate_an_account_with_no_transactions_at_all(self):
        account = Account.objects.create(name="Checking")
        form = AccountForm(
            {
                "name": "Checking",
                "account_type": "cash",
                "currency": "USD",
                "institution": "",
                "is_active": False,
                "notes": "",
            },
            instance=account,
        )
        self.assertTrue(form.is_valid())

    def test_a_new_account_cannot_start_inactive_with_an_opening_balance(self):
        form = AccountCreateForm(
            {
                "name": "Checking",
                "account_type": "cash",
                "currency": "USD",
                "institution": "",
                "is_active": False,
                "notes": "",
                "opening_balance": "100.00",
                "opening_balance_date": "01/01/2026",
            }
        )
        self.assertFalse(form.is_valid())

    def test_a_new_account_can_start_inactive_with_no_opening_balance(self):
        form = AccountCreateForm(
            {
                "name": "Checking",
                "account_type": "cash",
                "currency": "USD",
                "institution": "",
                "is_active": False,
                "notes": "",
                "opening_balance": "",
                "opening_balance_date": "",
            }
        )
        self.assertTrue(form.is_valid())


class RemovedHelperTextTests(LoggedInTestCase):
    def test_opening_balance_field_has_no_helper_text_on_create(self):
        response = self.client.get(reverse("finance:account_add"))
        self.assertNotContains(response, "What this account held")

    def test_currency_field_has_no_helper_text_on_edit(self):
        account = Account.objects.create(name="Checking")
        response = self.client.get(reverse("finance:account_edit", args=[account.pk]))
        self.assertNotContains(response, "Set when the account was created")


class AccountDeleteTests(LoggedInWithAccountTestCase):
    def test_confirmation_counts_everything_that_would_be_deleted(self):
        for day in range(1, 4):
            Transaction.objects.create(account=self.account, date=date(2026, 1, day), amount=Decimal("-1.00"))
        Reminder.objects.create(account=self.account, amount=Decimal("500.00"), next_due_date=date(2026, 2, 1))
        response = self.client.get(reverse("finance:account_delete", args=[self.account.pk]))
        self.assertContains(response, "3 transactions")
        self.assertContains(response, "1 reminder")

    def test_empty_account_shows_no_data_loss_warning(self):
        response = self.client.get(reverse("finance:account_delete", args=[self.account.pk]))
        self.assertNotContains(response, "will also delete")


class AccountSaveTests(StashTestCase):
    """6.1 (F12): only a new account or a new name touches its transfer category."""

    @classmethod
    def setUpTestData(cls):
        cls.account = Account.objects.create(name="Wallet")

    def test_a_new_account_gets_its_transfer_category(self):
        self.assertEqual(self.account.transfer_category.name, "[Wallet]")

    def test_an_update_fields_save_without_the_name_skips_the_transfer_category(self):
        self.account.is_active = False
        with mock.patch.object(Account, "ensure_transfer_category") as ensure:
            self.account.save(update_fields=["is_active"])
        ensure.assert_not_called()

    def test_a_full_save_with_the_same_name_skips_it(self):
        account = Account.objects.get(pk=self.account.pk)
        account.notes = "Pocket money"
        with mock.patch.object(Account, "ensure_transfer_category") as ensure:
            account.save()
        ensure.assert_not_called()

    def test_a_rename_renames_the_transfer_category(self):
        account = Account.objects.get(pk=self.account.pk)
        account.name = "Piggy bank"
        account.save()
        self.assertEqual(Category.objects.get(account=account).name, "[Piggy bank]")

    def test_a_rename_through_update_fields_renames_it_too(self):
        self.account.name = "Piggy bank"
        self.account.save(update_fields=["name"])
        self.assertEqual(Category.objects.get(account=self.account).name, "[Piggy bank]")

    def test_a_second_save_after_a_rename_skips_it(self):
        self.account.name = "Piggy bank"
        self.account.save()
        with mock.patch.object(Account, "ensure_transfer_category") as ensure:
            self.account.save()
        ensure.assert_not_called()
