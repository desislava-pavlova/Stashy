"""Reminders as rules: the schedule and its occurrences (generating them, the end date, moving the
due date), status, the form's own checks, entering and skipping, and what a save regenerates."""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from ..forms import ReminderForm
from ..models import Account, Category, Payee, Reminder, Transaction
from .base import TODAY_13_SEPT, TODAY_14_SEPT, LoggedInWithAccountTestCase, StashTestCase


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


class GenerationHorizonTests(StashTestCase):
    @mock.patch("django.utils.timezone.localdate", return_value=date(2026, 3, 1))
    def test_early_in_the_year_year_end_is_further_than_90_days(self, _):
        self.assertEqual(Reminder.default_generation_horizon(), date(2026, 12, 31))

    @mock.patch("django.utils.timezone.localdate", return_value=date(2026, 11, 15))
    def test_late_in_the_year_90_days_spills_into_next_year(self, _):
        self.assertEqual(Reminder.default_generation_horizon(), date(2026, 11, 15) + timedelta(days=90))


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

    def _series_with_a_september_whose_payment_was_deleted(self):
        """S51: September was entered (so it became a Paid record and the series moved on),
        then the transaction that paid it was deleted, leaving it unpaid and in the past."""
        root = Reminder.objects.create(
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 12, 20),
        )
        payment = Transaction.objects.create(account=self.account, date=date(2026, 9, 20), amount=Decimal("-10.00"))
        root.mark_entered(payment)
        payment.delete()
        september = root.occurrences.get(next_due_date=date(2026, 9, 20))
        self.assertIsNone(september.last_entered_transaction_id)
        return root, september

    def test_an_unpaid_earlier_period_survives_skipping_the_series(self, _):
        root, september = self._series_with_a_september_whose_payment_was_deleted()
        root.refresh_from_db()
        root.skip()
        # Kept, and still overdue: it's a bill nobody paid, so it stays where you'll see it.
        kept = Reminder.objects.get(pk=september.pk)
        self.assertEqual(kept.status(date(2026, 10, 1)), "overdue")

    def test_an_unpaid_earlier_period_survives_a_full_save_of_the_series(self, _):
        root, september = self._series_with_a_september_whose_payment_was_deleted()
        root.refresh_from_db()
        root.save()
        self.assertTrue(Reminder.objects.filter(pk=september.pk).exists())

    def _move_due_date(self, root, new_due_date):
        form = ReminderForm(
            instance=root,
            data={
                "reminder_type": "expense",
                "account": self.account.pk,
                "frequency": "monthly",
                "next_due_date": new_due_date,
                "end_date": "20/12/2026",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

    def test_moving_the_due_date_later_removes_the_previews_it_skipped_over(self, _):
        # The previews between the old and the new due date are no longer upcoming, and nobody
        # owes them, so they go (the date you typed also stands for itself, so its preview goes).
        root = Reminder.objects.create(
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 12, 20),
        )
        self.assertEqual(root.occurrences.count(), 3)  # Oct, Nov, Dec
        self._move_due_date(root, "20/11/2026")
        self.assertEqual(list(root.occurrences.values_list("next_due_date", flat=True)), [date(2026, 12, 20)])

    def test_moving_the_due_date_later_keeps_an_unpaid_earlier_period(self, _):
        # S51 still holds: the unpaid September is dated before where the due date used to be.
        root, september = self._series_with_a_september_whose_payment_was_deleted()
        root.refresh_from_db()
        self._move_due_date(root, "20/12/2026")
        self.assertTrue(Reminder.objects.filter(pk=september.pk).exists())
        self.assertFalse(root.occurrences.filter(next_due_date__in=[date(2026, 11, 20)]).exists())

    def _open_occurrence(self, root, on):
        """An occurrence of `root` that nobody has paid or skipped, put on a date by hand."""
        return Reminder.objects.create(
            series=root,
            frequency=Reminder.Frequency.ONE_TIME,
            next_due_date=on,
            account=root.account,
            amount=root.amount,
        )

    def _occurrences(self, root):
        """Every occurrence of `root`, oldest first, as (date, "open" | "paid" | "skipped")."""

        def state(occurrence):
            if occurrence.last_entered_transaction_id:
                return "paid"
            return "skipped" if occurrence.skipped else "open"

        return [(o.next_due_date, state(o)) for o in root.occurrences.order_by("next_due_date")]

    def test_moving_the_due_date_earlier_keeps_an_unpaid_earlier_period_and_redates_the_previews(self, _):
        # Broken if the earlier-moving case dropped every open occurrence that is not on the new
        # schedule (the pre-S51 rule): September would go. The previews must follow the new day (5th).
        root, _september = self._series_with_a_september_whose_payment_was_deleted()
        root.refresh_from_db()
        self.assertEqual(root.next_due_date, date(2026, 10, 20))
        self._move_due_date(root, "05/10/2026")
        self.assertEqual(
            self._occurrences(root),
            [(date(2026, 9, 20), "open"), (date(2026, 11, 5), "open"), (date(2026, 12, 5), "open")],
        )

    def test_moving_the_due_date_back_before_an_unpaid_period_takes_it_in_as_a_preview(self, _):
        # The rule is "on or after the earlier of the old and new due date". September 20th is
        # after the 5th, so once the series starts on 5 September it is just a date that is not on
        # the new schedule (5 October, 5 November, ...), like the old 20 November and 20 December.
        root, _september = self._series_with_a_september_whose_payment_was_deleted()
        root.refresh_from_db()
        self._move_due_date(root, "05/09/2026")
        self.assertEqual(
            self._occurrences(root),
            [(date(2026, 10, 5), "open"), (date(2026, 11, 5), "open"), (date(2026, 12, 5), "open")],
        )

    def test_occurrences_already_paid_or_skipped_survive_the_due_date_moving_past_them(self, _):
        root = Reminder.objects.create(
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 12, 20),
        )
        october = root.occurrences.get(next_due_date=date(2026, 10, 20))
        october.last_entered_transaction = Transaction.objects.create(
            account=self.account, date=date(2026, 10, 20), amount=Decimal("-10.00")
        )
        october.save(update_fields=["last_entered_transaction"])
        root.occurrences.get(next_due_date=date(2026, 11, 20)).skip()

        self._move_due_date(root, "20/12/2026")

        # October (paid) and November (skipped) lie between the old and the new due date but are
        # records now; only the open December preview goes, because the series itself stands for it.
        self.assertEqual(self._occurrences(root), [(date(2026, 10, 20), "paid"), (date(2026, 11, 20), "skipped")])

    def test_regenerating_with_no_previous_due_date_only_drops_previews_from_the_due_date_on(self, _):
        # The default argument: nothing is known about an earlier due date, so only the current one counts.
        root, _september = self._series_with_a_september_whose_payment_was_deleted()
        root.refresh_from_db()
        self._open_occurrence(root, root.next_due_date)  # on the series' own date: the series stands for it
        root.regenerate_occurrences()
        self.assertEqual(
            self._occurrences(root),
            [(date(2026, 9, 20), "open"), (date(2026, 11, 20), "open"), (date(2026, 12, 20), "open")],
        )

    def test_entering_the_series_keeps_an_unpaid_earlier_period_and_creates_no_duplicates(self, _):
        root, _september = self._series_with_a_september_whose_payment_was_deleted()
        root.refresh_from_db()
        payment = Transaction.objects.create(account=self.account, date=date(2026, 10, 20), amount=Decimal("-10.00"))
        root.mark_entered(payment)
        root.refresh_from_db()
        self.assertEqual(root.next_due_date, date(2026, 11, 20))
        # September still unpaid, October now a Paid record, November absorbed by the series itself.
        self.assertEqual(
            self._occurrences(root),
            [(date(2026, 9, 20), "open"), (date(2026, 10, 20), "paid"), (date(2026, 12, 20), "open")],
        )

    def test_skipping_the_series_keeps_an_unpaid_earlier_period_and_creates_no_duplicates(self, _):
        root, _september = self._series_with_a_september_whose_payment_was_deleted()
        root.refresh_from_db()
        root.skip()
        root.refresh_from_db()
        self.assertEqual(root.next_due_date, date(2026, 11, 20))
        self.assertEqual(
            self._occurrences(root),
            [(date(2026, 9, 20), "open"), (date(2026, 10, 20), "skipped"), (date(2026, 12, 20), "open")],
        )

    def test_an_end_of_period_series_moves_on_and_keeps_an_unpaid_earlier_period(self, _):
        # Last-day-of-month dates differ in length (30, 31): not the same arithmetic as a plain monthly series.
        root = Reminder.objects.create(
            account=self.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 30),
            frequency="monthly",
            end_of_period=True,
            end_date=date(2026, 12, 31),
        )
        self.assertEqual(
            [due for due, _state in self._occurrences(root)],
            [date(2026, 10, 31), date(2026, 11, 30), date(2026, 12, 31)],
        )
        self._open_occurrence(root, date(2026, 8, 31))
        payment = Transaction.objects.create(account=self.account, date=date(2026, 9, 30), amount=Decimal("-10.00"))
        root.mark_entered(payment)
        root.refresh_from_db()
        self.assertEqual(root.next_due_date, date(2026, 10, 31))
        self.assertEqual(
            self._occurrences(root),
            [
                (date(2026, 8, 31), "open"),
                (date(2026, 9, 30), "paid"),
                (date(2026, 11, 30), "open"),
                (date(2026, 12, 31), "open"),
            ],
        )

    def test_moving_the_due_date_later_works_for_every_frequency(self, _):
        earlier = date(2026, 1, 5)
        cases = [
            # frequency, interval, first due, end date, new due date, what is still open afterwards
            ("weekly", 1, date(2026, 9, 21), date(2026, 10, 19), date(2026, 10, 12), [date(2026, 10, 19)]),
            ("weekly", 2, date(2026, 9, 21), date(2026, 11, 2), date(2026, 10, 19), [date(2026, 11, 2)]),
            (
                "quarterly",
                1,
                date(2026, 9, 20),
                date(2027, 9, 20),
                date(2027, 3, 20),
                [date(2027, 6, 20), date(2027, 9, 20)],
            ),
            (
                "yearly",
                1,
                date(2026, 9, 20),
                date(2030, 9, 20),
                date(2028, 9, 20),
                [date(2029, 9, 20), date(2030, 9, 20)],
            ),
        ]
        for frequency, interval, first_due, end_date, new_due, still_open in cases:
            with self.subTest(frequency=frequency, interval=interval):
                root = Reminder.objects.create(
                    account=self.account,
                    amount=Decimal("10.00"),
                    next_due_date=first_due,
                    frequency=frequency,
                    interval=interval,
                    end_date=end_date,
                )
                self.assertGreater(root.occurrences.count(), len(still_open))  # there is something to remove
                self._open_occurrence(root, earlier)
                root.next_due_date = new_due
                root.save()
                self.assertEqual(self._occurrences(root), [(earlier, "open"), *((d, "open") for d in still_open)])

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


class ReminderIntervalTests(LoggedInWithAccountTestCase):
    def test_weekly_with_interval_2_is_every_two_weeks(self):
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


class ReminderLabelTests(StashTestCase):
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


class ReminderEnterAndSkipTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.account = Account.objects.create(name="Wallet")
        cls.due = timezone.localdate() + timedelta(days=3)
        cls.txn = Transaction.objects.create(account=cls.account, date=cls.due, amount=Decimal("-5"))

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

    @classmethod
    def setUpTestData(cls):
        account = Account.objects.create(name="Wallet")
        cls.reminder = Reminder.objects.create(
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

    def test_the_stored_due_date_of_a_reminder_not_saved_yet_is_none(self):
        unsaved = Reminder(account=self.reminder.account, next_due_date=date(2027, 1, 1))
        self.assertIsNone(unsaved._stored_due_date())

    def test_the_stored_due_date_is_what_the_database_holds_not_an_unsaved_change(self):
        reminder = Reminder.objects.get(pk=self.reminder.pk)
        reminder.next_due_date = date(2027, 1, 1)
        self.assertEqual(reminder._stored_due_date(), date(2026, 10, 15))

    def test_save_hands_regenerate_the_due_date_from_before_the_save(self):
        # Read after the save, it would be the new date and a due date moved later could not be told apart.
        self.reminder.next_due_date = date(2027, 1, 1)
        with mock.patch.object(Reminder, "regenerate_occurrences") as regenerate:
            self.reminder.save()
        regenerate.assert_called_once_with(previous_due_date=date(2026, 10, 15))

    def test_a_new_recurring_reminder_has_no_previous_due_date(self):
        new = Reminder(account=self.reminder.account, frequency="monthly", next_due_date=date(2027, 1, 1))
        with mock.patch.object(Reminder, "regenerate_occurrences") as regenerate:
            new.save()
        regenerate.assert_called_once_with(previous_due_date=None)
