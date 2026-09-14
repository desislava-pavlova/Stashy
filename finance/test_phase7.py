"""Tests for Phase 7 (Reminders): optional amount, Skip instead of is_active, computed
status (overdue/due soon/paid/pending) backed by a real transaction link, Enter opening a
pre-filled transaction form instead of silently creating one, end-of-period due dates,
"no end date", and the reminder_type -> category dropdown filter."""
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import ReminderForm
from .models import Account, Category, Payee, Reminder, Transaction

TODAY = date(2026, 9, 13)


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


class OptionalAmountTests(LoggedInTestCase):
    def test_model_accepts_a_blank_amount(self):
        reminder = Reminder.objects.create(account=self.account, next_due_date=date(2026, 10, 1))
        self.assertIsNone(reminder.amount)
        self.assertIsNone(reminder.signed_amount())

    def test_form_does_not_require_an_amount(self):
        form = ReminderForm(data={
            "reminder_type": "expense", "account": self.account.pk, "frequency": "monthly",
            "next_due_date": "01/10/2026", "no_end_date": "on",
        })
        self.assertTrue(form.is_valid(), form.errors)


class AutoEnterDaysBeforeRemovedTests(TestCase):
    def test_field_is_gone(self):
        self.assertFalse(hasattr(Reminder(), "auto_enter_days_before"))
        self.assertNotIn("auto_enter_days_before", ReminderForm().fields)


class IsActiveRemovedAndSkipAddedTests(LoggedInTestCase):
    def test_field_is_gone(self):
        self.assertFalse(hasattr(Reminder(), "is_active"))
        self.assertNotIn("is_active", ReminderForm().fields)

    def test_skip_advances_the_due_date_without_creating_a_transaction(self):
        reminder = Reminder.objects.create(
            account=self.account, amount=Decimal("20.00"), next_due_date=date(2026, 10, 1), frequency="monthly",
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
            account=self.account, amount=Decimal("20.00"), next_due_date=date(2026, 10, 1), frequency="monthly",
            last_entered_transaction=txn,
        )
        self.client.post(reverse("finance:reminder_skip", args=[reminder.pk]))
        reminder.refresh_from_db()
        self.assertIsNone(reminder.last_entered_transaction_id)

    def test_skip_requires_post(self):
        reminder = Reminder.objects.create(account=self.account, amount=Decimal("20.00"), next_due_date=date(2026, 10, 1))
        self.client.get(reverse("finance:reminder_skip", args=[reminder.pk]))
        reminder.refresh_from_db()
        self.assertEqual(reminder.next_due_date, date(2026, 10, 1))


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class StatusTests(LoggedInTestCase):
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
            account=self.account, amount=Decimal("1"), next_due_date=date(2026, 12, 1),
            last_entered_transaction=txn,
        )
        self.assertEqual(r.status(), "paid")

    def test_deleting_the_linked_transaction_reverts_the_status(self, _):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 9, 1), amount=Decimal("-1.00"))
        r = Reminder.objects.create(
            account=self.account, amount=Decimal("1"), next_due_date=date(2026, 12, 1),
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
            account=self.account, amount=Decimal("1"), next_due_date=date(2026, 9, 14), frequency="monthly",
            last_entered_transaction=txn,
        )
        self.assertEqual(r.status(), "due_soon")

    def test_deleting_the_reminder_does_not_touch_the_transaction(self, _):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 9, 1), amount=Decimal("-1.00"))
        r = Reminder.objects.create(
            account=self.account, amount=Decimal("1"), next_due_date=date(2026, 12, 1),
            last_entered_transaction=txn,
        )
        r.delete()
        self.assertTrue(Transaction.objects.filter(pk=txn.pk).exists())


class EnterOpensAPrefilledFormTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.payee = Payee.objects.create(name="Landlord")
        self.category = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        self.reminder = Reminder.objects.create(
            account=self.account, payee=self.payee, category=self.category, amount=Decimal("500.00"),
            reminder_type=Category.CategoryType.EXPENSE, next_due_date=date(2026, 10, 1), frequency="monthly",
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
        response = self.client.post(reverse("finance:transaction_add"), {
            "reminder": self.reminder.pk,
            "account": self.account.pk, "date": "01/10/2026", "transaction_number": "",
            "payee_name": "Landlord", "category": self.category.pk, "memo": "Rent", "amount": "-500.00",
        })
        self.assertRedirects(response, reverse("finance:reminder_list"))
        self.assertEqual(Transaction.objects.count(), 1)
        txn = Transaction.objects.get()
        self.reminder.refresh_from_db()
        self.assertEqual(self.reminder.last_entered_transaction_id, txn.pk)
        self.assertEqual(self.reminder.next_due_date, date(2026, 11, 1))

    def test_editing_an_existing_transaction_ignores_a_reminder_param(self):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("-1.00"))
        response = self.client.get(reverse("finance:transaction_edit", args=[txn.pk]), {"reminder": self.reminder.pk})
        self.assertIsNone(response.context["reminder"])


class EndOfPeriodDueDateTests(LoggedInTestCase):
    def test_monthly_lands_on_the_last_day_of_next_month(self):
        r = Reminder.objects.create(
            account=self.account, amount=Decimal("1"), frequency="monthly", end_of_period=True,
            next_due_date=date(2026, 1, 31),
        )
        r.advance_due_date()
        self.assertEqual(r.next_due_date, date(2026, 2, 28))

    def test_quarterly_lands_on_a_quarter_end(self):
        r = Reminder.objects.create(
            account=self.account, amount=Decimal("1"), frequency="quarterly", end_of_period=True,
            next_due_date=date(2026, 3, 31),
        )
        r.advance_due_date()
        self.assertEqual(r.next_due_date, date(2026, 6, 30))

    def test_yearly_lands_on_31_december(self):
        r = Reminder.objects.create(
            account=self.account, amount=Decimal("1"), frequency="yearly", end_of_period=True,
            next_due_date=date(2025, 12, 31),
        )
        r.advance_due_date()
        self.assertEqual(r.next_due_date, date(2026, 12, 31))

    def test_weekly_lands_on_a_sunday(self):
        r = Reminder.objects.create(
            account=self.account, amount=Decimal("1"), frequency="weekly", end_of_period=True,
            next_due_date=date(2026, 9, 6),  # a Sunday
        )
        r.advance_due_date()
        self.assertEqual(r.next_due_date.strftime("%A"), "Sunday")

    def test_without_the_toggle_it_is_a_plain_fixed_interval(self):
        r = Reminder.objects.create(
            account=self.account, amount=Decimal("1"), frequency="monthly", end_of_period=False,
            next_due_date=date(2026, 1, 15),
        )
        r.advance_due_date()
        self.assertEqual(r.next_due_date, date(2026, 2, 15))


class NoEndDateTests(LoggedInTestCase):
    def test_checking_no_end_date_clears_any_end_date(self):
        form = ReminderForm(data={
            "reminder_type": "expense", "account": self.account.pk, "frequency": "monthly",
            "next_due_date": "01/10/2026", "end_date": "01/10/2027", "no_end_date": "on",
        })
        self.assertTrue(form.is_valid(), form.errors)
        reminder = form.save()
        self.assertIsNone(reminder.end_date)

    def test_leaving_it_unchecked_keeps_an_end_date(self):
        form = ReminderForm(data={
            "reminder_type": "expense", "account": self.account.pk, "frequency": "monthly",
            "next_due_date": "01/10/2026", "end_date": "01/10/2027",
        })
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
            account=self.account, amount=Decimal("1"), next_due_date=date(2026, 10, 1), end_date=date(2027, 1, 1),
        )
        self.assertFalse(ReminderForm(instance=reminder).fields["no_end_date"].initial)


class CategoryDropdownFilteredByTypeTests(LoggedInTestCase):
    def test_category_options_carry_a_data_category_type_attribute(self):
        Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        html = str(ReminderForm()["category"])
        self.assertIn('data-category-type="expense"', html)
        self.assertIn('data-category-type="income"', html)


class ReminderListFilterTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.soon = Reminder.objects.create(account=self.account, amount=Decimal("1"), next_due_date=date(2026, 9, 15))
        self.later = Reminder.objects.create(account=self.account, amount=Decimal("1"), next_due_date=date(2026, 12, 1))
        txn = Transaction.objects.create(account=self.account, date=date(2026, 9, 1), amount=Decimal("-1.00"))
        self.paid = Reminder.objects.create(
            account=self.account, amount=Decimal("1"), next_due_date=date(2026, 12, 15),
            last_entered_transaction=txn,
        )

    @mock.patch("django.utils.timezone.localdate", return_value=TODAY)
    def test_paid_reminders_are_hidden_by_default(self, _):
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        ids = {r.pk for r in response.context["reminders"]}
        self.assertIn(self.soon.pk, ids)
        self.assertIn(self.later.pk, ids)
        self.assertNotIn(self.paid.pk, ids)

    @mock.patch("django.utils.timezone.localdate", return_value=TODAY)
    def test_show_paid_reveals_them(self, _):
        response = self.client.get(reverse("finance:reminder_list"), {"show_done": "1", "window": "all"})
        ids = {r.pk for r in response.context["reminders"]}
        self.assertIn(self.paid.pk, ids)

    @mock.patch("django.utils.timezone.localdate", return_value=TODAY)
    def test_window_filter_narrows_to_reminders_due_within_it(self, _):
        response = self.client.get(reverse("finance:reminder_list"), {"window": "30"})
        ids = {r.pk for r in response.context["reminders"]}
        self.assertEqual(ids, {self.soon.pk})
