"""Tests for: removing Reminder.name, and previewing the next transaction number on Add."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import ReminderForm
from .models import Account, Category, Payee, Reminder, Transaction


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


class ReminderNameRemovedTests(TestCase):
    def test_reminder_has_no_name_field(self):
        self.assertFalse(hasattr(Reminder(), "name"))

    def test_reminder_form_has_no_name_field(self):
        self.assertNotIn("name", ReminderForm().fields)

    def test_str_uses_payee_when_present(self):
        account = Account.objects.create(name="Checking")
        payee = Payee.objects.create(name="Landlord")
        reminder = Reminder.objects.create(
            account=account,
            payee=payee,
            amount=Decimal("500.00"),
            next_due_date=date(2026, 10, 1),
        )
        self.assertIn("Landlord", str(reminder))

    def test_str_falls_back_to_category_then_a_generic_label(self):
        account = Account.objects.create(name="Checking")
        category = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        with_category = Reminder.objects.create(
            account=account,
            category=category,
            amount=Decimal("500.00"),
            next_due_date=date(2026, 10, 1),
        )
        self.assertIn("Rent", str(with_category))

        bare = Reminder.objects.create(account=account, amount=Decimal("10.00"), next_due_date=date(2026, 10, 1))
        self.assertIn("Reminder", str(bare))


class ReminderListShowsPayeeTests(LoggedInTestCase):
    def test_list_shows_payee_instead_of_a_name_column(self):
        payee = Payee.objects.create(name="Landlord")
        Reminder.objects.create(
            account=self.account, payee=payee, amount=Decimal("500.00"), next_due_date=date(2026, 10, 1)
        )
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        self.assertContains(response, "Landlord")
        self.assertContains(response, "<th>Payee</th>", html=True)
        self.assertNotContains(response, "<th>Name</th>", html=True)


class NewTransactionNumberPreviewTests(LoggedInTestCase):
    def test_add_form_prefills_the_next_available_number(self):
        Transaction.objects.create(
            account=self.account, date=date.today(), transaction_number=41, amount=Decimal("-1.00")
        )
        response = self.client.get(reverse("finance:transaction_add"))
        self.assertEqual(response.context["form"].initial.get("transaction_number"), 42)

    def test_edit_form_shows_the_transactions_own_number_not_a_fresh_preview(self):
        txn = Transaction.objects.create(
            account=self.account, date=date.today(), transaction_number=7, amount=Decimal("-1.00")
        )
        Transaction.objects.create(
            account=self.account, date=date.today(), transaction_number=8, amount=Decimal("-1.00")
        )
        response = self.client.get(reverse("finance:transaction_edit", args=[txn.pk]))
        # The highest number right now is 8, so a fresh "next available" preview would wrongly
        # show 9 here - editing must show this transaction's own number (7) untouched instead.
        self.assertEqual(response.context["form"].initial.get("transaction_number"), 7)
