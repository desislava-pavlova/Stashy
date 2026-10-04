"""Reminders: schedules and their occurrences, status, entering and skipping, the reminder form
and list, editing one occurrence or the whole series, and deleting."""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.urls import reverse

from ..forms import ReminderForm
from ..models import Account, Category, Payee, Reminder, Transaction
from .base import LoggedInWithAccountTestCase, StashTestCase

# Tests that patch timezone.localdate pretend today is one of these days, so the fixed dates
# they set up keep their meaning (overdue, due soon, this month).
TODAY_13_SEPT = date(2026, 9, 13)
TODAY_14_SEPT = date(2026, 9, 14)
TODAY_19_SEPT = date(2026, 9, 19)


class OptionalAmountTests(LoggedInWithAccountTestCase):
    def test_model_accepts_a_blank_amount(self):
        reminder = Reminder.objects.create(account=self.account, next_due_date=date(2026, 10, 1))
        self.assertIsNone(reminder.amount)
        self.assertIsNone(reminder.signed_amount())

    def test_form_does_not_require_an_amount(self):
        form = ReminderForm(
            data={
                "reminder_type": "expense",
                "account": self.account.pk,
                "frequency": "monthly",
                "next_due_date": "01/10/2026",
                "no_end_date": "on",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)


class AutoEnterDaysBeforeRemovedTests(StashTestCase):
    def test_field_is_gone(self):
        self.assertFalse(hasattr(Reminder(), "auto_enter_days_before"))
        self.assertNotIn("auto_enter_days_before", ReminderForm().fields)


class IsActiveRemovedAndSkipAddedTests(LoggedInWithAccountTestCase):
    def test_field_is_gone(self):
        self.assertFalse(hasattr(Reminder(), "is_active"))
        self.assertNotIn("is_active", ReminderForm().fields)

    def test_skip_advances_the_due_date_without_creating_a_transaction(self):
        reminder = Reminder.objects.create(
            account=self.account,
            amount=Decimal("20.00"),
            next_due_date=date(2026, 10, 1),
            frequency="monthly",
        )
        before = Transaction.objects.count()
        response = self.client.post(reverse("finance:reminder_skip", args=[reminder.pk]))
        self.assertRedirects(response, reverse("finance:reminder_list"))
        self.assertEqual(Transaction.objects.count(), before)
        reminder.refresh_from_db()
        self.assertEqual(reminder.next_due_date, date(2026, 11, 1))

    def test_skip_clears_a_stale_paid_link(self):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 9, 1), amount=Decimal("-1.00"))
        reminder = Reminder.objects.create(
            account=self.account,
            amount=Decimal("20.00"),
            next_due_date=date(2026, 10, 1),
            frequency="monthly",
            last_entered_transaction=txn,
        )
        self.client.post(reverse("finance:reminder_skip", args=[reminder.pk]))
        reminder.refresh_from_db()
        self.assertIsNone(reminder.last_entered_transaction_id)

    def test_skip_requires_post(self):
        reminder = Reminder.objects.create(
            account=self.account, amount=Decimal("20.00"), next_due_date=date(2026, 10, 1)
        )
        self.client.get(reverse("finance:reminder_skip", args=[reminder.pk]))
        reminder.refresh_from_db()
        self.assertEqual(reminder.next_due_date, date(2026, 10, 1))


@mock.patch("django.utils.timezone.localdate", return_value=TODAY_13_SEPT)
class StatusTests(LoggedInWithAccountTestCase):
    def test_overdue(self, _):
        r = Reminder.objects.create(account=self.account, amount=Decimal("1"), next_due_date=date(2026, 9, 1))
        self.assertEqual(r.status(), "overdue")

    def test_due_soon(self, _):
        r = Reminder.objects.create(account=self.account, amount=Decimal("1"), next_due_date=date(2026, 9, 18))
        self.assertEqual(r.status(), "due_soon")

    def test_pending_when_far_out_and_never_entered(self, _):
        r = Reminder.objects.create(account=self.account, amount=Decimal("1"), next_due_date=date(2026, 12, 1))
        self.assertEqual(r.status(), "")

    def test_paid_when_far_out_and_linked_to_a_transaction(self, _):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 9, 1), amount=Decimal("-1.00"))
        r = Reminder.objects.create(
            account=self.account,
            amount=Decimal("1"),
            next_due_date=date(2026, 12, 1),
            last_entered_transaction=txn,
        )
        self.assertEqual(r.status(), "paid")

    def test_deleting_the_linked_transaction_reverts_the_status(self, _):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 9, 1), amount=Decimal("-1.00"))
        r = Reminder.objects.create(
            account=self.account,
            amount=Decimal("1"),
            next_due_date=date(2026, 12, 1),
            last_entered_transaction=txn,
        )
        txn.delete()
        r.refresh_from_db()
        self.assertIsNone(r.last_entered_transaction_id)
        self.assertEqual(r.status(), "")

    def test_due_soon_and_overdue_outrank_a_stale_paid_link(self, _):
        # If nobody touches the reminder again, its due date eventually becomes urgent again -
        # the old "paid" link from a prior cycle shouldn't keep masking that.
        txn = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("-1.00"))
        r = Reminder.objects.create(
            account=self.account,
            amount=Decimal("1"),
            next_due_date=date(2026, 9, 14),
            frequency="monthly",
            last_entered_transaction=txn,
        )
        self.assertEqual(r.status(), "due_soon")

    def test_deleting_the_reminder_does_not_touch_the_transaction(self, _):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 9, 1), amount=Decimal("-1.00"))
        r = Reminder.objects.create(
            account=self.account,
            amount=Decimal("1"),
            next_due_date=date(2026, 12, 1),
            last_entered_transaction=txn,
        )
        r.delete()
        self.assertTrue(Transaction.objects.filter(pk=txn.pk).exists())


class EnterOpensAPrefilledFormTests(LoggedInWithAccountTestCase):
    def setUp(self):
        super().setUp()
        self.payee = Payee.objects.create(name="Landlord")
        self.category = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        self.reminder = Reminder.objects.create(
            account=self.account,
            payee=self.payee,
            category=self.category,
            amount=Decimal("500.00"),
            reminder_type=Category.CategoryType.EXPENSE,
            next_due_date=date(2026, 10, 1),
            frequency="monthly",
            notes="Rent",
        )

    def test_get_prefills_from_the_reminder_without_creating_anything(self):
        response = self.client.get(reverse("finance:transaction_add"), {"reminder": self.reminder.pk})
        self.assertEqual(Transaction.objects.count(), 0)
        form = response.context["form"]
        self.assertEqual(form.initial["payee_name"], "Landlord")
        self.assertEqual(form.initial["category"], self.category.pk)
        self.assertEqual(form.initial["amount"], Decimal("-500.00"))
        self.assertEqual(form.initial["date"], date(2026, 10, 1))

    def test_a_reminder_with_no_fixed_amount_leaves_amount_blank(self):
        self.reminder.amount = None
        self.reminder.save(update_fields=["amount"])
        response = self.client.get(reverse("finance:transaction_add"), {"reminder": self.reminder.pk})
        self.assertIsNone(response.context["form"].initial.get("amount"))

    def test_posting_creates_the_transaction_links_it_and_advances_the_due_date(self):
        response = self.client.post(
            reverse("finance:transaction_add"),
            {
                "reminder": self.reminder.pk,
                "account": self.account.pk,
                "date": "01/10/2026",
                "transaction_number": "",
                "payee_name": "Landlord",
                "category": self.category.pk,
                "memo": "Rent",
                "amount": "-500.00",
            },
        )
        self.assertRedirects(response, reverse("finance:reminder_list"))
        self.assertEqual(Transaction.objects.count(), 1)
        self.reminder.refresh_from_db()
        # The recurring reminder now stands for the next, unpaid period - no stale "paid" link.
        self.assertIsNone(self.reminder.last_entered_transaction_id)
        self.assertNotEqual(self.reminder.status(), "paid")
        self.assertEqual(self.reminder.next_due_date, date(2026, 11, 1))

    def test_editing_an_existing_transaction_ignores_a_reminder_param(self):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("-1.00"))
        response = self.client.get(reverse("finance:transaction_edit", args=[txn.pk]), {"reminder": self.reminder.pk})
        self.assertIsNone(response.context["reminder"])


class EndOfPeriodDueDateTests(LoggedInWithAccountTestCase):
    def test_monthly_lands_on_the_last_day_of_next_month(self):
        r = Reminder.objects.create(
            account=self.account,
            amount=Decimal("1"),
            frequency="monthly",
            end_of_period=True,
            next_due_date=date(2026, 1, 31),
        )
        r.advance_due_date()
        self.assertEqual(r.next_due_date, date(2026, 2, 28))

    def test_quarterly_lands_on_a_quarter_end(self):
        r = Reminder.objects.create(
            account=self.account,
            amount=Decimal("1"),
            frequency="quarterly",
            end_of_period=True,
            next_due_date=date(2026, 3, 31),
        )
        r.advance_due_date()
        self.assertEqual(r.next_due_date, date(2026, 6, 30))

    def test_yearly_lands_on_31_december(self):
        r = Reminder.objects.create(
            account=self.account,
            amount=Decimal("1"),
            frequency="yearly",
            end_of_period=True,
            next_due_date=date(2025, 12, 31),
        )
        r.advance_due_date()
        self.assertEqual(r.next_due_date, date(2026, 12, 31))

    def test_weekly_lands_on_a_sunday(self):
        r = Reminder.objects.create(
            account=self.account,
            amount=Decimal("1"),
            frequency="weekly",
            end_of_period=True,
            next_due_date=date(2026, 9, 6),  # a Sunday
        )
        r.advance_due_date()
        self.assertEqual(r.next_due_date.strftime("%A"), "Sunday")

    def test_without_the_toggle_it_is_a_plain_fixed_interval(self):
        r = Reminder.objects.create(
            account=self.account,
            amount=Decimal("1"),
            frequency="monthly",
            end_of_period=False,
            next_due_date=date(2026, 1, 15),
        )
        r.advance_due_date()
        self.assertEqual(r.next_due_date, date(2026, 2, 15))


class NoEndDateTests(LoggedInWithAccountTestCase):
    def test_checking_no_end_date_clears_any_end_date(self):
        form = ReminderForm(
            data={
                "reminder_type": "expense",
                "account": self.account.pk,
                "frequency": "monthly",
                "next_due_date": "01/10/2026",
                "end_date": "01/10/2027",
                "no_end_date": "on",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        reminder = form.save()
        self.assertIsNone(reminder.end_date)

    def test_leaving_it_unchecked_keeps_an_end_date(self):
        form = ReminderForm(
            data={
                "reminder_type": "expense",
                "account": self.account.pk,
                "frequency": "monthly",
                "next_due_date": "01/10/2026",
                "end_date": "01/10/2027",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        reminder = form.save()
        self.assertEqual(reminder.end_date, date(2027, 10, 1))

    def test_a_new_reminder_defaults_to_no_end_date_checked(self):
        self.assertTrue(ReminderForm().fields["no_end_date"].initial)

    def test_editing_a_reminder_with_no_end_date_keeps_the_box_checked(self):
        reminder = Reminder.objects.create(account=self.account, amount=Decimal("1"), next_due_date=date(2026, 10, 1))
        self.assertTrue(ReminderForm(instance=reminder).fields["no_end_date"].initial)

    def test_editing_a_reminder_with_an_end_date_unchecks_the_box(self):
        reminder = Reminder.objects.create(
            account=self.account,
            amount=Decimal("1"),
            next_due_date=date(2026, 10, 1),
            end_date=date(2027, 1, 1),
        )
        self.assertFalse(ReminderForm(instance=reminder).fields["no_end_date"].initial)


class CategoryDropdownFilteredByTypeTests(LoggedInWithAccountTestCase):
    def test_category_options_carry_a_data_category_type_attribute(self):
        Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        html = str(ReminderForm()["category"])
        self.assertIn('data-category-type="expense"', html)
        self.assertIn('data-category-type="income"', html)


class ReminderListFilterTests(LoggedInWithAccountTestCase):
    def setUp(self):
        super().setUp()
        self.soon = Reminder.objects.create(account=self.account, amount=Decimal("1"), next_due_date=date(2026, 9, 15))
        self.later = Reminder.objects.create(
            account=self.account, amount=Decimal("1"), next_due_date=date(2026, 12, 1)
        )
        txn = Transaction.objects.create(account=self.account, date=date(2026, 9, 1), amount=Decimal("-1.00"))
        self.paid = Reminder.objects.create(
            account=self.account,
            amount=Decimal("1"),
            next_due_date=date(2026, 12, 15),
            last_entered_transaction=txn,
        )

    @mock.patch("django.utils.timezone.localdate", return_value=TODAY_13_SEPT)
    def test_paid_reminders_are_hidden_by_default(self, _):
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        ids = {r.pk for r in response.context["reminders"]}
        self.assertIn(self.soon.pk, ids)
        self.assertIn(self.later.pk, ids)
        self.assertNotIn(self.paid.pk, ids)

    @mock.patch("django.utils.timezone.localdate", return_value=TODAY_13_SEPT)
    def test_show_paid_reveals_them(self, _):
        response = self.client.get(reverse("finance:reminder_list"), {"show_done": "1", "window": "all"})
        ids = {r.pk for r in response.context["reminders"]}
        self.assertIn(self.paid.pk, ids)

    @mock.patch("django.utils.timezone.localdate", return_value=TODAY_13_SEPT)
    def test_window_filter_narrows_to_reminders_due_within_it(self, _):
        response = self.client.get(reverse("finance:reminder_list"), {"window": "30"})
        ids = {r.pk for r in response.context["reminders"]}
        self.assertEqual(ids, {self.soon.pk})


class OneTimeFrequencyTests(LoggedInWithAccountTestCase):
    def test_one_time_is_the_default_frequency(self):
        self.assertEqual(ReminderForm().fields["frequency"].initial or Reminder.Frequency.ONE_TIME, "one_time")

    def test_entering_a_one_time_reminder_does_not_crash_and_marks_it_paid(self):
        reminder = Reminder.objects.create(
            account=self.account,
            amount=Decimal("50.00"),
            next_due_date=date(2026, 9, 20),
            frequency="one_time",
        )
        response = self.client.post(
            reverse("finance:transaction_add"),
            {
                "reminder": reminder.pk,
                "account": self.account.pk,
                "date": "20/09/2026",
                "transaction_number": "",
                "payee_name": "",
                "category": "",
                "memo": "",
                "amount": "-50.00",
            },
        )
        self.assertRedirects(response, reverse("finance:reminder_list"))
        reminder.refresh_from_db()
        self.assertEqual(reminder.next_due_date, date(2026, 9, 20))  # unchanged - nothing to advance to
        self.assertEqual(reminder.status(), "paid")

    def test_skipping_a_one_time_reminder_marks_it_skipped_instead_of_advancing(self):
        reminder = Reminder.objects.create(
            account=self.account,
            amount=Decimal("50.00"),
            next_due_date=date(2026, 9, 20),
            frequency="one_time",
        )
        self.client.post(reverse("finance:reminder_skip", args=[reminder.pk]))
        reminder.refresh_from_db()
        self.assertTrue(reminder.skipped)
        self.assertEqual(reminder.next_due_date, date(2026, 9, 20))
        self.assertEqual(reminder.status(), "skipped")


class GenerationHorizonTests(StashTestCase):
    @mock.patch("django.utils.timezone.localdate", return_value=date(2026, 3, 1))
    def test_early_in_the_year_year_end_is_further_than_90_days(self, _):
        self.assertEqual(Reminder.default_generation_horizon(), date(2026, 12, 31))

    @mock.patch("django.utils.timezone.localdate", return_value=date(2026, 11, 15))
    def test_late_in_the_year_90_days_spills_into_next_year(self, _):
        self.assertEqual(
            Reminder.default_generation_horizon(), date(2026, 11, 15) + __import__("datetime").timedelta(days=90)
        )


@mock.patch("django.utils.timezone.localdate", return_value=TODAY_14_SEPT)
class AutoGenerateOccurrencesTests(LoggedInWithAccountTestCase):
    def test_creating_a_recurring_reminder_materializes_future_occurrences_up_to_end_date(self, _):
        root = Reminder.objects.create(
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 12, 20),
        )
        occurrence_dates = sorted(o.next_due_date for o in root.occurrences.all())
        self.assertEqual(occurrence_dates, [date(2026, 10, 20), date(2026, 11, 20), date(2026, 12, 20)])
        for occ in root.occurrences.all():
            self.assertEqual(occ.frequency, "one_time")
            self.assertEqual(occ.series_id, root.pk)
            self.assertEqual(occ.amount, Decimal("10.00"))

    def test_no_end_date_generates_out_to_the_default_horizon(self, _):
        root = Reminder.objects.create(
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
        )
        horizon = Reminder.default_generation_horizon(TODAY_14_SEPT)
        self.assertTrue(all(o.next_due_date <= horizon for o in root.occurrences.all()))
        self.assertTrue(root.occurrences.exists())

    def test_shortening_the_end_date_removes_occurrences_beyond_it(self, _):
        form = ReminderForm(
            data={
                "reminder_type": "expense",
                "account": self.account.pk,
                "frequency": "monthly",
                "next_due_date": "20/09/2026",
                "end_date": "20/01/2027",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        root = form.save()
        self.assertEqual(root.occurrences.count(), 4)  # Oct, Nov, Dec, Jan

        edit_form = ReminderForm(
            instance=root,
            data={
                "reminder_type": "expense",
                "account": self.account.pk,
                "frequency": "monthly",
                "next_due_date": "20/09/2026",
                "end_date": "20/10/2026",
            },
        )
        self.assertTrue(edit_form.is_valid(), edit_form.errors)
        edit_form.save()
        root.refresh_from_db()
        self.assertEqual(list(root.occurrences.values_list("next_due_date", flat=True)), [date(2026, 10, 20)])

    def test_extending_the_end_date_adds_more_occurrences(self, _):
        root = Reminder.objects.create(
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 10, 20),
        )
        self.assertEqual(root.occurrences.count(), 1)
        form = ReminderForm(
            instance=root,
            data={
                "reminder_type": "expense",
                "account": self.account.pk,
                "frequency": "monthly",
                "next_due_date": "20/09/2026",
                "end_date": "20/12/2026",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        root.refresh_from_db()
        self.assertEqual(root.occurrences.count(), 3)

    def test_entering_the_current_occurrence_absorbs_the_matching_preview_and_tops_up_the_tail(self, _):
        root = Reminder.objects.create(
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 12, 20),
        )
        self.assertEqual(root.occurrences.count(), 3)  # Oct, Nov, Dec
        response = self.client.post(
            reverse("finance:transaction_add"),
            {
                "reminder": root.pk,
                "account": self.account.pk,
                "date": "20/09/2026",
                "transaction_number": "",
                "payee_name": "",
                "category": "",
                "memo": "",
                "amount": "-10.00",
            },
        )
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
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 12, 20),
        )
        october = root.occurrences.get(next_due_date=date(2026, 10, 20))
        txn = Transaction.objects.create(account=self.account, date=date(2026, 10, 20), amount=Decimal("-10.00"))
        october.last_entered_transaction = txn
        october.save(update_fields=["last_entered_transaction"])

        form = ReminderForm(
            instance=root,
            data={
                "reminder_type": "expense",
                "account": self.account.pk,
                "frequency": "monthly",
                "next_due_date": "20/09/2026",
                "end_date": "20/09/2026",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertTrue(Reminder.objects.filter(pk=october.pk).exists())

    def test_each_occurrence_can_be_entered_independently(self, _):
        root = Reminder.objects.create(
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 12, 20),
        )
        november = root.occurrences.get(next_due_date=date(2026, 11, 20))
        response = self.client.post(
            reverse("finance:transaction_add"),
            {
                "reminder": november.pk,
                "account": self.account.pk,
                "date": "20/11/2026",
                "transaction_number": "",
                "payee_name": "",
                "category": "",
                "memo": "",
                "amount": "-10.00",
            },
        )
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
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 12, 20),
        )
        occurrence_ids = list(root.occurrences.values_list("pk", flat=True))
        root.delete()
        self.assertFalse(Reminder.objects.filter(pk__in=occurrence_ids).exists())

    def test_an_occurrence_cannot_be_turned_into_its_own_recurring_series(self, _):
        root = Reminder.objects.create(
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 12, 20),
        )
        occurrence = root.occurrences.first()
        form = ReminderForm(
            instance=occurrence,
            data={
                "reminder_type": "expense",
                "account": self.account.pk,
                "frequency": "monthly",
                "next_due_date": occurrence.next_due_date.strftime("%d/%m/%Y"),
                "no_end_date": "on",
            },
        )
        self.assertFalse(form.is_valid())


@mock.patch("django.utils.timezone.localdate", return_value=TODAY_14_SEPT)
class ReminderListDefaultWindowTests(LoggedInWithAccountTestCase):
    def setUp(self):
        super().setUp()
        self.soon = Reminder.objects.create(account=self.account, amount=Decimal("1"), next_due_date=date(2026, 9, 20))
        self.far = Reminder.objects.create(account=self.account, amount=Decimal("1"), next_due_date=date(2026, 12, 1))

    def test_a_fresh_visit_defaults_to_this_month(self, _):
        response = self.client.get(reverse("finance:reminder_list"))
        ids = {r.pk for r in response.context["reminders"]}
        self.assertEqual(ids, {self.soon.pk})
        self.assertEqual(response.context["window"], "month")

    def test_explicitly_choosing_all_shows_everything(self, _):
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        ids = {r.pk for r in response.context["reminders"]}
        self.assertEqual(ids, {self.soon.pk, self.far.pk})


class OneTimeIntervalTests(LoggedInWithAccountTestCase):
    def test_biweekly_is_gone_replaced_by_weekly_interval_2(self):
        self.assertNotIn("biweekly", dict(Reminder.Frequency.choices))

    def test_weekly_with_interval_2_behaves_like_the_old_biweekly(self):
        r = Reminder.objects.create(
            account=self.account,
            amount=Decimal("1"),
            frequency="weekly",
            interval=2,
            next_due_date=date(2026, 9, 1),
        )
        self.assertEqual(r._advance(r.next_due_date), date(2026, 9, 15))

    def test_interval_defaults_to_1_and_is_not_required_on_the_form(self):
        form = ReminderForm(
            data={
                "reminder_type": "expense",
                "account": self.account.pk,
                "frequency": "monthly",
                "next_due_date": "01/10/2026",
                "no_end_date": "on",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        reminder = form.save()
        self.assertEqual(reminder.interval, 1)

    def test_every_3_months_still_resolves_last_day_of_the_landed_month(self):
        r = Reminder.objects.create(
            account=self.account,
            amount=Decimal("1"),
            frequency="monthly",
            interval=3,
            end_of_period=True,
            next_due_date=date(2026, 1, 31),
        )
        r.advance_due_date()
        self.assertEqual(r.next_due_date, date(2026, 4, 30))


class ReminderFormFieldOrderTests(LoggedInWithAccountTestCase):
    def test_end_of_period_sits_between_next_due_date_and_end_date(self):
        fields = list(ReminderForm().fields.keys())
        self.assertLess(fields.index("next_due_date"), fields.index("end_of_period"))
        self.assertLess(fields.index("end_of_period"), fields.index("end_date"))

    def test_end_of_period_has_no_help_text_and_is_relabeled(self):
        field = ReminderForm().fields["end_of_period"]
        self.assertEqual(field.help_text, "")
        self.assertEqual(field.label, "Last day of period")


class EnterMemoFromHistoryTests(LoggedInWithAccountTestCase):
    def test_enter_prefills_the_memo_remembered_for_this_payee_and_category(self):
        payee = Payee.objects.create(name="Landlord")
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 8, 1),
            payee=payee,
            category=rent,
            amount=Decimal("-500.00"),
            memo="August rent, paid early",
        )
        reminder = Reminder.objects.create(
            account=self.account,
            payee=payee,
            category=rent,
            amount=Decimal("500.00"),
            reminder_type=Category.CategoryType.EXPENSE,
            next_due_date=date(2026, 9, 1),
            notes="Rent due",
        )
        response = self.client.get(reverse("finance:transaction_add"), {"reminder": reminder.pk})
        self.assertEqual(response.context["form"].initial["memo"], "August rent, paid early")

    def test_falls_back_to_the_reminders_own_notes_when_no_history_matches(self):
        payee = Payee.objects.create(name="New Landlord")
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        reminder = Reminder.objects.create(
            account=self.account,
            payee=payee,
            category=rent,
            amount=Decimal("500.00"),
            reminder_type=Category.CategoryType.EXPENSE,
            next_due_date=date(2026, 9, 1),
            notes="Rent due",
        )
        response = self.client.get(reverse("finance:transaction_add"), {"reminder": reminder.pk})
        self.assertEqual(response.context["form"].initial["memo"], "Rent due")


class SignedReminderAmountDisplayTests(LoggedInWithAccountTestCase):
    def test_expense_shows_negative_in_the_list(self):
        Reminder.objects.create(account=self.account, amount=Decimal("50.00"), next_due_date=date.today())
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        self.assertContains(response, "-50")

    def test_income_stays_positive(self):
        Reminder.objects.create(
            account=self.account,
            amount=Decimal("50.00"),
            reminder_type="income",
            next_due_date=date.today(),
        )
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        self.assertNotContains(response, "-50")
        self.assertContains(response, "50")


class ReminderDeleteWarningTests(LoggedInWithAccountTestCase):
    def test_deleting_a_series_head_warns_how_many_occurrences_will_go(self):
        root = Reminder.objects.create(
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 12, 20),
        )
        response = self.client.get(reverse("finance:reminder_delete", args=[root.pk]))
        self.assertContains(response, "3 upcoming occurrences")

    def test_a_resolved_occurrence_survives_deleting_the_series_head(self):
        root = Reminder.objects.create(
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 12, 20),
        )
        october = root.occurrences.get(next_due_date=date(2026, 10, 20))
        october.skipped = True
        october.save(update_fields=["skipped"])
        root.delete()
        october.refresh_from_db()
        self.assertIsNone(october.series_id)

    def test_unresolved_occurrences_are_gone_after_deleting_the_series_head(self):
        root = Reminder.objects.create(
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 12, 20),
        )
        occurrence_ids = list(root.occurrences.values_list("pk", flat=True))
        root.delete()
        self.assertFalse(Reminder.objects.filter(pk__in=occurrence_ids).exists())


class ReminderEditScopeTests(LoggedInWithAccountTestCase):
    def setUp(self):
        super().setUp()
        self.root = Reminder.objects.create(
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 12, 20),
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
        response = self.client.post(
            reverse("finance:reminder_edit", args=[self.root.pk]) + "?scope=this",
            {
                "scope": "this",
                "reminder_type": "expense",
                "account": self.account.pk,
                "payee": payee.pk,
                "frequency": "monthly",
                "interval": "1",
                "next_due_date": "20/09/2026",
                "end_date": "20/12/2026",
                "amount": "10.00",
            },
        )
        self.assertRedirects(response, reverse("finance:reminder_list"))
        self.november.refresh_from_db()
        self.assertIsNone(self.november.payee_id)

    def test_whole_series_propagates_shared_fields_to_open_occurrences(self):
        payee = Payee.objects.create(name="New Payee")
        response = self.client.post(
            reverse("finance:reminder_edit", args=[self.root.pk]) + "?scope=series",
            {
                "scope": "series",
                "reminder_type": "expense",
                "account": self.account.pk,
                "payee": payee.pk,
                "frequency": "monthly",
                "interval": "1",
                "next_due_date": "20/09/2026",
                "end_date": "20/12/2026",
                "amount": "12.00",
            },
        )
        self.assertRedirects(response, reverse("finance:reminder_list"))
        self.november.refresh_from_db()
        self.assertEqual(self.november.payee_id, payee.pk)
        self.assertEqual(self.november.amount, Decimal("12.00"))

    def test_whole_series_from_an_occurrence_updates_the_root_and_siblings(self):
        payee = Payee.objects.create(name="New Payee")
        october = self.root.occurrences.get(next_due_date=date(2026, 10, 20))
        response = self.client.post(
            reverse("finance:reminder_edit", args=[self.november.pk]) + "?scope=series",
            {
                "scope": "series",
                "reminder_type": "expense",
                "account": self.account.pk,
                "payee": payee.pk,
                "frequency": "one_time",
                "interval": "1",
                "next_due_date": "20/11/2026",
                "amount": "20.00",
            },
        )
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
        self.client.post(
            reverse("finance:reminder_edit", args=[self.root.pk]) + "?scope=series",
            {
                "scope": "series",
                "reminder_type": "expense",
                "account": self.account.pk,
                "payee": payee.pk,
                "frequency": "monthly",
                "interval": "1",
                "next_due_date": "20/09/2026",
                "end_date": "20/12/2026",
                "amount": "12.00",
            },
        )
        october.refresh_from_db()
        self.assertIsNone(october.payee_id)


class ReminderAmountBlankTests(LoggedInWithAccountTestCase):
    def test_no_amount_shows_blank_not_varies(self):
        Reminder.objects.create(account=self.account, next_due_date=date(2026, 9, 20))
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        self.assertNotContains(response, "Varies")


class ReminderDeleteTitleTests(LoggedInWithAccountTestCase):
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


class ReminderNameRemovedTests(StashTestCase):
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


class ReminderListShowsPayeeTests(LoggedInWithAccountTestCase):
    def test_list_shows_payee_instead_of_a_name_column(self):
        payee = Payee.objects.create(name="Landlord")
        Reminder.objects.create(
            account=self.account, payee=payee, amount=Decimal("500.00"), next_due_date=date(2026, 10, 1)
        )
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        self.assertContains(response, "Landlord")
        self.assertContains(response, "<th>Payee</th>", html=True)
        self.assertNotContains(response, "<th>Name</th>", html=True)


@mock.patch("django.utils.timezone.localdate", return_value=TODAY_19_SEPT)
class ThisMonthTests(LoggedInWithAccountTestCase):
    def _reminder(self, due, **kw):
        return Reminder.objects.create(account=self.account, amount=Decimal("5"), next_due_date=due, **kw)

    def test_default_is_this_month_with_paid_and_unpaid_and_nothing_outside(self, _):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 9, 3), amount=Decimal("-5"))
        paid = self._reminder(date(2026, 9, 3), last_entered_transaction=txn)
        unpaid = self._reminder(date(2026, 9, 28))
        before = self._reminder(date(2026, 8, 30))
        after = self._reminder(date(2026, 10, 1))
        response = self.client.get(reverse("finance:reminder_list"))
        self.assertEqual(response.context["window"], "month")
        shown = response.context["reminders"]
        self.assertIn(paid, shown)
        self.assertIn(unpaid, shown)
        self.assertNotIn(before, shown)
        self.assertNotIn(after, shown)
        self.assertContains(response, "This month")

    def test_recurring_reminder_with_stale_link_is_never_paid(self, _):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 9, 1), amount=Decimal("-5"))
        r = self._reminder(date(2026, 12, 1), frequency="monthly", last_entered_transaction=txn)
        self.assertEqual(r.status(), "")


@mock.patch("django.utils.timezone.localdate", return_value=TODAY_19_SEPT)
class EnteredPeriodStaysVisibleTests(LoggedInWithAccountTestCase):
    def _recurring(self):
        return Reminder.objects.create(
            account=self.account,
            amount=Decimal("5"),
            next_due_date=date(2026, 9, 25),
            frequency="monthly",
        )

    def test_entering_a_recurring_reminder_leaves_a_paid_row_for_that_month(self, _):
        root = self._recurring()
        self.client.post(
            reverse("finance:transaction_add"),
            {
                "reminder": root.pk,
                "account": self.account.pk,
                "date": "25/09/2026",
                "transaction_number": "",
                "payee_name": "",
                "category": "",
                "memo": "",
                "amount": "-5.00",
            },
        )
        response = self.client.get(reverse("finance:reminder_list"))
        paid = [r for r in response.context["reminders"] if r.status(TODAY_19_SEPT) == "paid"]
        self.assertEqual([r.next_due_date for r in paid], [date(2026, 9, 25)])
        root.refresh_from_db()
        self.assertEqual(root.next_due_date, date(2026, 10, 25))
        self.assertEqual(root.occurrences.filter(next_due_date=date(2026, 9, 25)).count(), 1)

    def test_skipping_a_recurring_reminder_leaves_a_skipped_row(self, _):
        root = self._recurring()
        self.client.post(reverse("finance:reminder_skip", args=[root.pk]))
        response = self.client.get(reverse("finance:reminder_list"))
        self.assertEqual([r.status(TODAY_19_SEPT) for r in response.context["reminders"]], ["skipped"])

    def test_checkbox_is_shown_and_unticking_hides_paid_rows(self, _):
        root = self._recurring()
        self.client.post(reverse("finance:reminder_skip", args=[root.pk]))
        response = self.client.get(reverse("finance:reminder_list"))
        self.assertContains(response, "Show paid/skipped")
        self.assertContains(response, "checked")
        response = self.client.get(reverse("finance:reminder_list"), {"window": "month", "show_done": "0"})
        self.assertEqual(list(response.context["reminders"]), [])


class ReminderEnterAndSkipTests(StashTestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Wallet")
        self.due = date.today() + timedelta(days=3)
        self.txn = Transaction.objects.create(account=self.account, date=self.due, amount=Decimal("-5"))

    def reminder(self, frequency=Reminder.Frequency.ONE_TIME):
        return Reminder.objects.create(
            account=self.account, amount=Decimal("5"), next_due_date=self.due, frequency=frequency
        )

    def test_entering_a_one_time_reminder_marks_it_paid(self):
        reminder = self.reminder()
        reminder.mark_entered(self.txn)
        reminder.refresh_from_db()
        self.assertEqual((reminder.status(), reminder.next_due_date), ("paid", self.due))

    def test_entering_a_recurring_reminder_records_the_period_and_moves_on(self):
        reminder = self.reminder(Reminder.Frequency.MONTHLY)
        reminder.mark_entered(self.txn)
        reminder.refresh_from_db()
        self.assertGreater(reminder.next_due_date, self.due)
        self.assertIsNone(reminder.last_entered_transaction)
        record = reminder.occurrences.get(next_due_date=self.due)
        self.assertEqual((record.status(), record.last_entered_transaction), ("paid", self.txn))

    def test_skipping_a_one_time_reminder_marks_it_done(self):
        reminder = self.reminder()
        reminder.skip()
        reminder.refresh_from_db()
        self.assertEqual(reminder.status(), "skipped")

    def test_skipping_a_recurring_reminder_clears_an_old_link_and_moves_on(self):
        reminder = self.reminder(Reminder.Frequency.MONTHLY)
        reminder.last_entered_transaction = self.txn
        reminder.save(update_fields=["last_entered_transaction"])
        reminder.skip()
        reminder.refresh_from_db()
        self.assertGreater(reminder.next_due_date, self.due)
        self.assertIsNone(reminder.last_entered_transaction)
        self.assertTrue(reminder.occurrences.get(next_due_date=self.due).skipped)

    def test_a_series_edit_reaches_the_head_and_open_occurrences_only(self):
        head = self.reminder(Reminder.Frequency.MONTHLY)
        first, second = head.occurrences.order_by("next_due_date")[:2]
        second.skipped = True
        second.save(update_fields=["skipped"])
        first.amount = Decimal("9")
        first.save(update_fields=["amount"])

        first.apply_to_series()

        head.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(head.amount, Decimal("9"))
        self.assertEqual(second.amount, Decimal("5"))  # skipped: a record now, left alone
        open_amounts = set(head.occurrences.filter(skipped=False).values_list("amount", flat=True))
        self.assertEqual(open_amounts, {Decimal("9")})


class ReminderSaveTests(StashTestCase):
    """6.2 (F12): a save regenerates occurrences only when it can move a date."""

    def setUp(self):
        account = Account.objects.create(name="Wallet")
        self.reminder = Reminder.objects.create(
            account=account, frequency=Reminder.Frequency.MONTHLY, next_due_date=date(2026, 10, 15)
        )

    def _saved(self, **kwargs):
        with mock.patch.object(Reminder, "regenerate_occurrences") as regenerate:
            self.reminder.save(**kwargs)
        return regenerate.called

    def test_an_update_fields_save_outside_the_schedule_does_not_regenerate(self):
        self.assertFalse(self._saved(update_fields=["notes"]))
        self.assertFalse(self._saved(update_fields=["last_entered_transaction"]))

    def test_saving_any_schedule_field_regenerates(self):
        for field in Reminder.SCHEDULE_FIELDS:
            with self.subTest(field=field):
                self.assertTrue(self._saved(update_fields=[field, "notes"]))

    def test_a_full_save_regenerates(self):
        self.assertTrue(self._saved())

    def test_a_one_time_reminder_never_regenerates(self):
        self.reminder.frequency = Reminder.Frequency.ONE_TIME
        self.assertFalse(self._saved())
