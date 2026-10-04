"""Rules the app's forms enforce also hold in the database (constraints, model validation)
and in the Django admin: transfer categories, reminder amounts and intervals, and an
account's currency once it has transactions."""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory

from ..forms import ReminderForm
from ..models import Account, Category, Reminder, Transaction
from .base import StashTestCase

EXPENSE = Category.CategoryType.EXPENSE


class AdminRuleTests(StashTestCase):
    """2.8 (F54): rules the forms enforce now hold for the admin and the database too."""

    def test_a_transfer_category_needs_an_account(self):
        with self.assertRaises(IntegrityError), transaction.atomic(using=settings.TEST_STASH):
            Category.objects.create(name="Moving money", category_type=Category.CategoryType.TRANSFER)
        with self.assertRaises(ValidationError) as caught:
            Category(name="Moving money", category_type=Category.CategoryType.TRANSFER).full_clean()
        self.assertIn("category_type", caught.exception.message_dict)

    def test_an_accounts_transfer_category_stays_a_transfer(self):
        category = Account.objects.create(name="Cash").transfer_category
        category.category_type = EXPENSE
        with self.assertRaises(IntegrityError), transaction.atomic(using=settings.TEST_STASH):
            category.save()

    def test_a_reminder_amount_cant_be_negative(self):
        account = Account.objects.create(name="Checking")
        with self.assertRaises(IntegrityError), transaction.atomic(using=settings.TEST_STASH):
            Reminder.objects.create(account=account, amount=Decimal("-5"), next_due_date=date(2026, 10, 1))
        form = ReminderForm(
            data={
                "reminder_type": EXPENSE,
                "account": account.pk,
                "amount": "-5",
                "frequency": "one_time",
                "next_due_date": "01/10/2026",
                "no_end_date": True,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("amount", form.errors)

    def test_a_reminder_interval_is_at_least_one(self):
        account = Account.objects.create(name="Checking")
        with self.assertRaises(IntegrityError), transaction.atomic(using=settings.TEST_STASH):
            Reminder.objects.create(account=account, interval=0, next_due_date=date(2026, 10, 1))

    def test_currency_cant_change_once_transactions_exist(self):
        account = Account.objects.create(name="Checking", currency="EUR")
        Transaction.objects.create(account=account, date=date(2026, 9, 1), amount=Decimal("10"))
        account.currency = "USD"
        with self.assertRaises(ValidationError) as caught:
            account.full_clean()
        self.assertIn("currency", caught.exception.message_dict)

    def test_currency_can_change_while_the_account_is_empty(self):
        account = Account.objects.create(name="Checking", currency="EUR")
        account.currency = "USD"
        account.full_clean()

    def test_the_admin_shows_currency_read_only_once_transactions_exist(self):
        account = Account.objects.create(name="Checking")
        model_admin = admin.site._registry[Account]
        request = RequestFactory().get("/")
        self.assertNotIn("currency", model_admin.get_readonly_fields(request, account))
        Transaction.objects.create(account=account, date=date(2026, 9, 1), amount=Decimal("10"))
        self.assertIn("currency", model_admin.get_readonly_fields(request, account))
