"""Rules the app's forms enforce also hold in the database (constraints, model validation)
and in the Django admin: transfer categories, reminder amounts and intervals, and an
account's currency once it has transactions."""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections, transaction
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

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


class AdminAccountListTests(StashTestCase):
    """8.15 (F56): the admin's account list computes every balance in the same query."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(settings.TEST_STASH, password="not-a-real-password-123")

    def setUp(self):
        self.client.force_login(self.user)

    def queries_with(self, count):
        for i in range(count):
            account = Account.objects.create(name=f"Account {count}-{i}")
            Transaction.objects.create(account=account, date=date(2026, 1, 5), amount=Decimal("10.00"))
        url = reverse("admin:finance_account_changelist")
        self.client.get(url)  # the first page of a session also creates its Profile
        with CaptureQueriesContext(connections[settings.TEST_STASH]) as queries:
            response = self.client.get(url)
        self.assertEqual({a.balance for a in response.context["cl"].result_list}, {Decimal("10.00")})
        return len(queries)

    def test_more_accounts_need_no_more_queries(self):
        self.assertEqual(self.queries_with(1), self.queries_with(4))

    def test_deleting_from_the_list_still_works(self):
        account = Account.objects.create(name="Old wallet")
        response = self.client.post(
            reverse("admin:finance_account_changelist"),
            {"action": "delete_selected", "_selected_action": [account.pk], "post": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Account.objects.filter(pk=account.pk).exists())
