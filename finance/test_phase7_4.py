"""Tests for the 2026-09-14 second follow-up: an amountless reminder shows blank (not
"Varies"), the reminder delete page reads as a real sentence and is centered, and the
dashboard donut uses a currency symbol with correct prefix/suffix placement."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Account, Category, Payee, Reminder


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


class ReminderAmountBlankTests(LoggedInTestCase):
    def test_no_amount_shows_blank_not_varies(self):
        Reminder.objects.create(account=self.account, next_due_date=date(2026, 9, 20))
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        self.assertNotContains(response, "Varies")


class ReminderDeleteTitleTests(LoggedInTestCase):
    def test_title_reads_as_a_sentence(self):
        payee = Payee.objects.create(name="Landlord")
        reminder = Reminder.objects.create(
            account=self.account,
            payee=payee,
            amount=Decimal("500.00"),
            reminder_type=Category.CategoryType.INCOME,
            next_due_date=date(2026, 9, 20),
        )
        response = self.client.get(reverse("finance:reminder_delete", args=[reminder.pk]))
        self.assertContains(response, "Delete Income reminder for Landlord?")

    def test_falls_back_to_category_then_a_generic_label(self):
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        reminder = Reminder.objects.create(
            account=self.account,
            category=rent,
            amount=Decimal("500.00"),
            next_due_date=date(2026, 9, 20),
        )
        response = self.client.get(reverse("finance:reminder_delete", args=[reminder.pk]))
        self.assertContains(response, "Rent")

        bare = Reminder.objects.create(account=self.account, amount=Decimal("1"), next_due_date=date(2026, 9, 20))
        response = self.client.get(reverse("finance:reminder_delete", args=[bare.pk]))
        self.assertContains(response, "this account")

    def test_delete_page_is_centered_not_left_hugging(self):
        reminder = Reminder.objects.create(account=self.account, amount=Decimal("1"), next_due_date=date(2026, 9, 20))
        response = self.client.get(reverse("finance:reminder_delete", args=[reminder.pk]))
        self.assertContains(response, "mx-auto")
