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

from stashy.admin_mixins import HugeNumberInAddressMixin

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


class SuperuserAdminTestCase(StashTestCase):
    """Logged in to the admin as the test stash's superuser."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(settings.TEST_STASH, password="not-a-real-password-123")

    def setUp(self):
        self.client.force_login(self.user)


class AdminHugeNumberTests(SuperuserAdminTestCase):
    """S112: a value the database or the calendar can't hold, typed into an admin list's
    address, used to be a 500 page. Django's own answer to a filter it can't use is a redirect
    to the plain list."""

    HUGE = "9" * 30

    def assert_redirected_to_the_plain_list(self, list_name, query):
        url = reverse(list_name)
        response = self.client.get(url, query)
        self.assertRedirects(response, f"{url}?e=1")

    def test_a_huge_number_in_a_filter_goes_back_to_the_plain_list(self):
        for query in ({"id__in": self.HUGE}, {"date__year": self.HUGE}, {"id__in": f"1,{self.HUGE}"}):
            with self.subTest(query=query):
                self.assert_redirected_to_the_plain_list("admin:finance_transaction_changelist", query)

    def test_the_audit_log_list_is_covered_too(self):
        for query in ({"id__in": self.HUGE}, {"timestamp__year": self.HUGE}):
            with self.subTest(query=query):
                self.assert_redirected_to_the_plain_list("admin:audit_auditlogentry_changelist", query)

    def test_an_ordinary_filter_still_works(self):
        account = Account.objects.create(name="Checking")
        Transaction.objects.create(account=account, date=date(2026, 9, 1), amount=Decimal("10"))
        response = self.client.get(reverse("admin:finance_transaction_changelist"), {"date__year": "2026"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["cl"].result_list), 1)

    def test_every_admin_has_the_guard_ahead_of_djangos_own_list_code(self):
        # Behind ModelAdmin in the bases, the mixin would be an instance check that passes and
        # a method that never runs: ModelAdmin's own get_changelist_instance would answer first.
        for model, model_admin in admin.site._registry.items():
            with self.subTest(model=model._meta.label):
                bases = type(model_admin).__mro__
                self.assertIn(HugeNumberInAddressMixin, bases)
                self.assertLess(bases.index(HugeNumberInAddressMixin), bases.index(admin.ModelAdmin))

    def test_a_refusal_is_logged_with_the_parameter_names_and_not_what_was_typed(self):
        with self.assertLogs("stashy.admin_mixins", "WARNING") as logged:
            self.client.get(reverse("admin:finance_transaction_changelist"), {"id__in": self.HUGE})
        message = logged.output[0]
        self.assertIn("id__in", message)
        self.assertNotIn(self.HUGE, message)

    def test_a_year_or_an_iso_year_the_calendar_cant_hold_goes_back_to_the_plain_list(self):
        """Not a number SQLite refuses, but the same family: Django finds the other end of a year
        by adding one to it, and the iso-year lookups check their value themselves. Both raise a
        ValueError, which the mixin turns into the same redirect."""
        for list_name, field in (
            ("admin:finance_transaction_changelist", "date"),
            ("admin:audit_auditlogentry_changelist", "timestamp"),
        ):
            for query in (
                {f"{field}__year": "9999"},
                {f"{field}__iso_year": "0"},
                {f"{field}__iso_year": "10000"},
                {f"{field}__iso_year__gt": self.HUGE},
            ):
                with self.subTest(list=list_name, query=query):
                    self.assert_redirected_to_the_plain_list(list_name, query)

    def test_a_date_past_the_end_of_the_calendar_goes_back_to_the_plain_list(self):
        """Not a number SQLite refuses, but the same family. To find the other end of a date
        range, Django adds a day or a month to the date typed, and the answer can fall past the
        year 9999: that is an OverflowError too, and the mixin's second job. Year 0 and year
        10000 are the two edges of what a date can hold, and Django refuses them itself."""
        for list_name, field in (
            ("admin:finance_transaction_changelist", "date"),
            ("admin:audit_auditlogentry_changelist", "timestamp"),
        ):
            for query in (
                {f"{field}__year": "0"},
                {f"{field}__year": "10000"},
                {f"{field}__year": "9999", f"{field}__month": "12"},
                {f"{field}__year": "9999", f"{field}__month": "12", f"{field}__day": "31"},
                {f"{field}__year": "2026", f"{field}__month": "2", f"{field}__day": "31"},
            ):
                with self.subTest(list=list_name, query=query):
                    self.assert_redirected_to_the_plain_list(list_name, query)

    def test_the_first_and_the_last_but_one_year_of_the_calendar_still_list(self):
        for year in ("1", "9998"):
            with self.subTest(year=year):
                response = self.client.get(reverse("admin:finance_transaction_changelist"), {"date__year": year})
                self.assertEqual(response.status_code, 200)

    def test_an_address_the_error_flag_is_already_on_gets_a_page_not_another_redirect(self):
        """Django sends a refused address to `?e=1`. If that address is refused too, something
        else is wrong, and Django shows its "Database error" page instead of redirecting forever:
        the mixin must not get in the way of that."""
        response = self.client.get(reverse("admin:finance_transaction_changelist"), {"e": "1", "id__in": self.HUGE})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/invalid_setup.html")


class AdminAccountListTests(SuperuserAdminTestCase):
    """8.15 (F56): the admin's account list computes every balance in the same query."""

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
