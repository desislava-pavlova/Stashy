"""Tests for: auto-generating future occurrences of a recurring reminder (2026-09-14
follow-up), the new One-time frequency, and the Paid/Skipped end states that go with it."""
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import ReminderForm
from .models import Account, Category, Payee, Reminder, Transaction

TODAY = date(2026, 9, 14)


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


class OneTimeFrequencyTests(LoggedInTestCase):
    def test_one_time_is_the_default_frequency(self):
        self.assertEqual(ReminderForm().fields["frequency"].initial or Reminder.Frequency.ONE_TIME, "one_time")

    def test_entering_a_one_time_reminder_does_not_crash_and_marks_it_paid(self):
        reminder = Reminder.objects.create(
            account=self.account, amount=Decimal("50.00"), next_due_date=date(2026, 9, 20), frequency="one_time",
        )
        response = self.client.post(reverse("finance:transaction_add"), {
            "reminder": reminder.pk, "account": self.account.pk, "date": "20/09/2026",
            "transaction_number": "", "payee_name": "", "category": "", "memo": "", "amount": "-50.00",
        })
        self.assertRedirects(response, reverse("finance:reminder_list"))
        reminder.refresh_from_db()
        self.assertEqual(reminder.next_due_date, date(2026, 9, 20))  # unchanged - nothing to advance to
        self.assertEqual(reminder.status(), "paid")

    def test_skipping_a_one_time_reminder_marks_it_skipped_instead_of_advancing(self):
        reminder = Reminder.objects.create(
            account=self.account, amount=Decimal("50.00"), next_due_date=date(2026, 9, 20), frequency="one_time",
        )
        self.client.post(reverse("finance:reminder_skip", args=[reminder.pk]))
        reminder.refresh_from_db()
        self.assertTrue(reminder.skipped)
        self.assertEqual(reminder.next_due_date, date(2026, 9, 20))
        self.assertEqual(reminder.status(), "skipped")


class GenerationHorizonTests(TestCase):
    @mock.patch("django.utils.timezone.localdate", return_value=date(2026, 3, 1))
    def test_early_in_the_year_year_end_is_further_than_90_days(self, _):
        self.assertEqual(Reminder.default_generation_horizon(), date(2026, 12, 31))

    @mock.patch("django.utils.timezone.localdate", return_value=date(2026, 11, 15))
    def test_late_in_the_year_90_days_spills_into_next_year(self, _):
        self.assertEqual(Reminder.default_generation_horizon(), date(2026, 11, 15) + __import__("datetime").timedelta(days=90))


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class AutoGenerateOccurrencesTests(LoggedInTestCase):
    def test_creating_a_recurring_reminder_materializes_future_occurrences_up_to_end_date(self, _):
        root = Reminder.objects.create(
            account=self.account, amount=Decimal("10.00"), next_due_date=date(2026, 9, 20),
            frequency="monthly", end_date=date(2026, 12, 20),
        )
        occurrence_dates = sorted(o.next_due_date for o in root.occurrences.all())
        self.assertEqual(occurrence_dates, [date(2026, 10, 20), date(2026, 11, 20), date(2026, 12, 20)])
        for occ in root.occurrences.all():
            self.assertEqual(occ.frequency, "one_time")
            self.assertEqual(occ.series_id, root.pk)
            self.assertEqual(occ.amount, Decimal("10.00"))

    def test_no_end_date_generates_out_to_the_default_horizon(self, _):
        root = Reminder.objects.create(
            account=self.account, amount=Decimal("10.00"), next_due_date=date(2026, 9, 20), frequency="monthly",
        )
        horizon = Reminder.default_generation_horizon(TODAY)
        self.assertTrue(all(o.next_due_date <= horizon for o in root.occurrences.all()))
        self.assertTrue(root.occurrences.exists())

    def test_shortening_the_end_date_removes_occurrences_beyond_it(self, _):
        form = ReminderForm(data={
            "reminder_type": "expense", "account": self.account.pk, "frequency": "monthly",
            "next_due_date": "20/09/2026", "end_date": "20/01/2027",
        })
        self.assertTrue(form.is_valid(), form.errors)
        root = form.save()
        self.assertEqual(root.occurrences.count(), 4)  # Oct, Nov, Dec, Jan

        edit_form = ReminderForm(instance=root, data={
            "reminder_type": "expense", "account": self.account.pk, "frequency": "monthly",
            "next_due_date": "20/09/2026", "end_date": "20/10/2026",
        })
        self.assertTrue(edit_form.is_valid(), edit_form.errors)
        edit_form.save()
        root.refresh_from_db()
        self.assertEqual(list(root.occurrences.values_list("next_due_date", flat=True)), [date(2026, 10, 20)])

    def test_extending_the_end_date_adds_more_occurrences(self, _):
        root = Reminder.objects.create(
            account=self.account, amount=Decimal("10.00"), next_due_date=date(2026, 9, 20),
            frequency="monthly", end_date=date(2026, 10, 20),
        )
        self.assertEqual(root.occurrences.count(), 1)
        form = ReminderForm(instance=root, data={
            "reminder_type": "expense", "account": self.account.pk, "frequency": "monthly",
            "next_due_date": "20/09/2026", "end_date": "20/12/2026",
        })
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        root.refresh_from_db()
        self.assertEqual(root.occurrences.count(), 3)

    def test_entering_the_current_occurrence_absorbs_the_matching_preview_and_tops_up_the_tail(self, _):
        root = Reminder.objects.create(
            account=self.account, amount=Decimal("10.00"), next_due_date=date(2026, 9, 20),
            frequency="monthly", end_date=date(2026, 12, 20),
        )
        self.assertEqual(root.occurrences.count(), 3)  # Oct, Nov, Dec
        response = self.client.post(reverse("finance:transaction_add"), {
            "reminder": root.pk, "account": self.account.pk, "date": "20/09/2026",
            "transaction_number": "", "payee_name": "", "category": "", "memo": "", "amount": "-10.00",
        })
        self.assertRedirects(response, reverse("finance:reminder_list"))
        root.refresh_from_db()
        self.assertEqual(root.next_due_date, date(2026, 10, 20))
        # October's preview is now redundant with the root itself and is dropped; November
        # and December are still within the (unchanged) end date, so both stay untouched.
        # (plus a standing "Paid" record of September itself, which was just entered).
        self.assertEqual(
            list(root.occurrences.values_list("next_due_date", flat=True)),
            [date(2026, 9, 20), date(2026, 11, 20), date(2026, 12, 20)],
        )

    def test_an_already_paid_occurrence_survives_the_end_date_moving_earlier(self, _):
        root = Reminder.objects.create(
            account=self.account, amount=Decimal("10.00"), next_due_date=date(2026, 9, 20),
            frequency="monthly", end_date=date(2026, 12, 20),
        )
        october = root.occurrences.get(next_due_date=date(2026, 10, 20))
        txn = Transaction.objects.create(account=self.account, date=date(2026, 10, 20), amount=Decimal("-10.00"))
        october.last_entered_transaction = txn
        october.save(update_fields=["last_entered_transaction"])

        form = ReminderForm(instance=root, data={
            "reminder_type": "expense", "account": self.account.pk, "frequency": "monthly",
            "next_due_date": "20/09/2026", "end_date": "20/09/2026",
        })
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertTrue(Reminder.objects.filter(pk=october.pk).exists())

    def test_each_occurrence_can_be_entered_independently(self, _):
        root = Reminder.objects.create(
            account=self.account, amount=Decimal("10.00"), next_due_date=date(2026, 9, 20),
            frequency="monthly", end_date=date(2026, 12, 20),
        )
        november = root.occurrences.get(next_due_date=date(2026, 11, 20))
        response = self.client.post(reverse("finance:transaction_add"), {
            "reminder": november.pk, "account": self.account.pk, "date": "20/11/2026",
            "transaction_number": "", "payee_name": "", "category": "", "memo": "", "amount": "-10.00",
        })
        self.assertRedirects(response, reverse("finance:reminder_list"))
        # Root and October's preview are untouched - only November was acted on, and it stays
        # in place (per the "keep it, marked Paid" answer) rather than disappearing.
        root.refresh_from_db()
        self.assertEqual(root.next_due_date, date(2026, 9, 20))
        self.assertTrue(root.occurrences.filter(next_due_date=date(2026, 10, 20)).exists())
        november.refresh_from_db()
        self.assertEqual(november.status(), "paid")

    def test_deleting_the_series_head_cascades_to_its_unresolved_occurrences(self, _):
        root = Reminder.objects.create(
            account=self.account, amount=Decimal("10.00"), next_due_date=date(2026, 9, 20),
            frequency="monthly", end_date=date(2026, 12, 20),
        )
        occurrence_ids = list(root.occurrences.values_list("pk", flat=True))
        root.delete()
        self.assertFalse(Reminder.objects.filter(pk__in=occurrence_ids).exists())

    def test_an_occurrence_cannot_be_turned_into_its_own_recurring_series(self, _):
        root = Reminder.objects.create(
            account=self.account, amount=Decimal("10.00"), next_due_date=date(2026, 9, 20),
            frequency="monthly", end_date=date(2026, 12, 20),
        )
        occurrence = root.occurrences.first()
        form = ReminderForm(instance=occurrence, data={
            "reminder_type": "expense", "account": self.account.pk, "frequency": "monthly",
            "next_due_date": occurrence.next_due_date.strftime("%d/%m/%Y"), "no_end_date": "on",
        })
        self.assertFalse(form.is_valid())


class DashboardExcludesResolvedRemindersTests(LoggedInTestCase):
    @mock.patch("django.utils.timezone.localdate", return_value=TODAY)
    def test_a_paid_reminder_due_soon_does_not_show_on_the_dashboard(self, _):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 9, 16), amount=Decimal("-1.00"))
        Reminder.objects.create(
            account=self.account, amount=Decimal("1"), next_due_date=date(2026, 9, 16), frequency="one_time",
            last_entered_transaction=txn,
        )
        response = self.client.get(reverse("finance:dashboard"))
        self.assertEqual(list(response.context["due_soon"]), [])
