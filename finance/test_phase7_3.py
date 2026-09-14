"""Tests for the three questions asked and answered on 2026-09-14: reminder Edit's
"this occurrence / whole series" scope prompt, the redesigned delete-confirmation page, and
faded reconciled rows on the all-accounts transaction list."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Account, Category, Payee, Reminder, Transaction


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


class ReminderEditScopeTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.root = Reminder.objects.create(
            account=self.account, amount=Decimal("10.00"), next_due_date=date(2026, 9, 20),
            frequency="monthly", end_date=date(2026, 12, 20),
        )
        self.november = self.root.occurrences.get(next_due_date=date(2026, 11, 20))

    def test_a_standalone_reminder_skips_the_prompt(self):
        solo = Reminder.objects.create(account=self.account, amount=Decimal("5.00"), next_due_date=date(2026, 9, 25))
        response = self.client.get(reverse("finance:reminder_edit", args=[solo.pk]))
        self.assertNotContains(response, "Just this occurrence")
        self.assertContains(response, 'id="id_amount"')

    def test_a_series_head_shows_the_scope_prompt(self):
        response = self.client.get(reverse("finance:reminder_edit", args=[self.root.pk]))
        self.assertContains(response, "Just this occurrence")
        self.assertContains(response, "The whole series")

    def test_an_occurrence_also_shows_the_scope_prompt(self):
        response = self.client.get(reverse("finance:reminder_edit", args=[self.november.pk]))
        self.assertContains(response, "Just this occurrence")

    def test_this_occurrence_only_changes_that_one_row(self):
        payee = Payee.objects.create(name="New Payee")
        response = self.client.post(reverse("finance:reminder_edit", args=[self.root.pk]) + "?scope=this", {
            "scope": "this", "reminder_type": "expense", "account": self.account.pk, "payee": payee.pk,
            "frequency": "monthly", "interval": "1", "next_due_date": "20/09/2026",
            "end_date": "20/12/2026", "amount": "10.00",
        })
        self.assertRedirects(response, reverse("finance:reminder_list"))
        self.november.refresh_from_db()
        self.assertIsNone(self.november.payee_id)

    def test_whole_series_propagates_shared_fields_to_open_occurrences(self):
        payee = Payee.objects.create(name="New Payee")
        response = self.client.post(reverse("finance:reminder_edit", args=[self.root.pk]) + "?scope=series", {
            "scope": "series", "reminder_type": "expense", "account": self.account.pk, "payee": payee.pk,
            "frequency": "monthly", "interval": "1", "next_due_date": "20/09/2026",
            "end_date": "20/12/2026", "amount": "12.00",
        })
        self.assertRedirects(response, reverse("finance:reminder_list"))
        self.november.refresh_from_db()
        self.assertEqual(self.november.payee_id, payee.pk)
        self.assertEqual(self.november.amount, Decimal("12.00"))

    def test_whole_series_from_an_occurrence_updates_the_root_and_siblings(self):
        payee = Payee.objects.create(name="New Payee")
        october = self.root.occurrences.get(next_due_date=date(2026, 10, 20))
        response = self.client.post(reverse("finance:reminder_edit", args=[self.november.pk]) + "?scope=series", {
            "scope": "series", "reminder_type": "expense", "account": self.account.pk, "payee": payee.pk,
            "frequency": "one_time", "interval": "1", "next_due_date": "20/11/2026", "amount": "20.00",
        })
        self.assertRedirects(response, reverse("finance:reminder_list"))
        self.root.refresh_from_db()
        october.refresh_from_db()
        self.assertEqual(self.root.payee_id, payee.pk)
        self.assertEqual(october.payee_id, payee.pk)
        self.assertEqual(october.amount, Decimal("20.00"))

    def test_whole_series_never_touches_a_resolved_occurrence(self):
        october = self.root.occurrences.get(next_due_date=date(2026, 10, 20))
        october.skipped = True
        october.save(update_fields=["skipped"])
        payee = Payee.objects.create(name="New Payee")
        self.client.post(reverse("finance:reminder_edit", args=[self.root.pk]) + "?scope=series", {
            "scope": "series", "reminder_type": "expense", "account": self.account.pk, "payee": payee.pk,
            "frequency": "monthly", "interval": "1", "next_due_date": "20/09/2026",
            "end_date": "20/12/2026", "amount": "12.00",
        })
        october.refresh_from_db()
        self.assertIsNone(october.payee_id)


class DeleteConfirmationRedesignTests(LoggedInTestCase):
    def test_shows_an_icon_and_a_clear_warning(self):
        Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        cat = Category.objects.get(name="Groceries")
        response = self.client.get(reverse("finance:category_delete", args=[cat.pk]))
        self.assertContains(response, "stat-icon-danger")
        self.assertContains(response, "This can't be undone.")
        self.assertContains(response, "Yes, delete it")


class ReconciledRowStylingTests(LoggedInTestCase):
    def test_reconciled_rows_get_the_faded_class(self):
        Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("-1.00"), reconciled=True)
        Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("-2.00"), reconciled=False)
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertEqual(response.content.decode().count("row-reconciled"), 1)
