"""Tests for the 2026-09-14 follow-up batch: default 15-day reminder window, category
dropdowns ordered so subcategories sit next to their parent, the replacement of BIWEEKLY with
a free "every N periods" interval, memo prefilled from payee+category history on Enter, signed
reminder amounts, the reminder delete-confirmation warning, and the transaction list's new
Delete button + renamed status filter."""
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import ReminderForm, TransactionForm
from .models import Account, Category, Payee, Reminder, Transaction

TODAY = date(2026, 9, 14)


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class ReminderListDefaultWindowTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.soon = Reminder.objects.create(account=self.account, amount=Decimal("1"), next_due_date=date(2026, 9, 20))
        self.far = Reminder.objects.create(account=self.account, amount=Decimal("1"), next_due_date=date(2026, 12, 1))

    def test_a_fresh_visit_defaults_to_15_days(self, _):
        response = self.client.get(reverse("finance:reminder_list"))
        ids = {r.pk for r in response.context["reminders"]}
        self.assertEqual(ids, {self.soon.pk})
        self.assertEqual(response.context["window"], "15")

    def test_explicitly_choosing_all_shows_everything(self, _):
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        ids = {r.pk for r in response.context["reminders"]}
        self.assertEqual(ids, {self.soon.pk, self.far.pk})


class CategoryDropdownOrderingTests(LoggedInTestCase):
    def test_subcategories_sort_next_to_their_own_parent(self):
        food = Category.objects.create(name="Food", category_type=Category.CategoryType.EXPENSE)
        Category.objects.create(name="Groceries", parent=food, category_type=Category.CategoryType.EXPENSE)
        personal = Category.objects.create(name="Personal Care", category_type=Category.CategoryType.EXPENSE)
        Category.objects.create(name="Haircut", parent=personal, category_type=Category.CategoryType.EXPENSE)
        form = TransactionForm()
        names = [str(c) for c in form.fields["category"].queryset]
        # "Personal Care" and its own "Haircut" subcategory land next to each other, not split
        # into a top-level block and a separate subcategories block.
        personal_index = names.index("Personal Care")
        self.assertEqual(names[personal_index + 1], "Personal Care > Haircut")


class OneTimeIntervalTests(LoggedInTestCase):
    def test_biweekly_is_gone_replaced_by_weekly_interval_2(self):
        self.assertNotIn("biweekly", dict(Reminder.Frequency.choices))

    def test_weekly_with_interval_2_behaves_like_the_old_biweekly(self):
        r = Reminder.objects.create(
            account=self.account, amount=Decimal("1"), frequency="weekly", interval=2,
            next_due_date=date(2026, 9, 1),
        )
        self.assertEqual(r._advance(r.next_due_date), date(2026, 9, 15))

    def test_interval_defaults_to_1_and_is_not_required_on_the_form(self):
        form = ReminderForm(data={
            "reminder_type": "expense", "account": self.account.pk, "frequency": "monthly",
            "next_due_date": "01/10/2026", "no_end_date": "on",
        })
        self.assertTrue(form.is_valid(), form.errors)
        reminder = form.save()
        self.assertEqual(reminder.interval, 1)

    def test_every_3_months_still_resolves_last_day_of_the_landed_month(self):
        r = Reminder.objects.create(
            account=self.account, amount=Decimal("1"), frequency="monthly", interval=3, end_of_period=True,
            next_due_date=date(2026, 1, 31),
        )
        r.advance_due_date()
        self.assertEqual(r.next_due_date, date(2026, 4, 30))


class ReminderFormFieldOrderTests(LoggedInTestCase):
    def test_end_of_period_sits_between_next_due_date_and_end_date(self):
        fields = list(ReminderForm().fields.keys())
        self.assertLess(fields.index("next_due_date"), fields.index("end_of_period"))
        self.assertLess(fields.index("end_of_period"), fields.index("end_date"))

    def test_end_of_period_has_no_help_text_and_is_relabeled(self):
        field = ReminderForm().fields["end_of_period"]
        self.assertEqual(field.help_text, "")
        self.assertEqual(field.label, "Last day of period")


class EnterMemoFromHistoryTests(LoggedInTestCase):
    def test_enter_prefills_the_memo_remembered_for_this_payee_and_category(self):
        payee = Payee.objects.create(name="Landlord")
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=self.account, date=date(2026, 8, 1), payee=payee, category=rent,
            amount=Decimal("-500.00"), memo="August rent, paid early",
        )
        reminder = Reminder.objects.create(
            account=self.account, payee=payee, category=rent, amount=Decimal("500.00"),
            reminder_type=Category.CategoryType.EXPENSE, next_due_date=date(2026, 9, 1), notes="Rent due",
        )
        response = self.client.get(reverse("finance:transaction_add"), {"reminder": reminder.pk})
        self.assertEqual(response.context["form"].initial["memo"], "August rent, paid early")

    def test_falls_back_to_the_reminders_own_notes_when_no_history_matches(self):
        payee = Payee.objects.create(name="New Landlord")
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        reminder = Reminder.objects.create(
            account=self.account, payee=payee, category=rent, amount=Decimal("500.00"),
            reminder_type=Category.CategoryType.EXPENSE, next_due_date=date(2026, 9, 1), notes="Rent due",
        )
        response = self.client.get(reverse("finance:transaction_add"), {"reminder": reminder.pk})
        self.assertEqual(response.context["form"].initial["memo"], "Rent due")


class SignedReminderAmountDisplayTests(LoggedInTestCase):
    def test_expense_shows_negative_in_the_list(self):
        Reminder.objects.create(account=self.account, amount=Decimal("50.00"), next_due_date=date.today())
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        self.assertContains(response, "-50")

    def test_income_stays_positive(self):
        Reminder.objects.create(
            account=self.account, amount=Decimal("50.00"), reminder_type="income", next_due_date=date.today(),
        )
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        self.assertNotContains(response, "-50")
        self.assertContains(response, "50")


class ReminderDeleteWarningTests(LoggedInTestCase):
    def test_deleting_a_series_head_warns_how_many_occurrences_will_go(self):
        root = Reminder.objects.create(
            account=self.account, amount=Decimal("10.00"), next_due_date=date(2026, 9, 20),
            frequency="monthly", end_date=date(2026, 12, 20),
        )
        response = self.client.get(reverse("finance:reminder_delete", args=[root.pk]))
        self.assertContains(response, "3 upcoming occurrences")

    def test_a_resolved_occurrence_survives_deleting_the_series_head(self):
        root = Reminder.objects.create(
            account=self.account, amount=Decimal("10.00"), next_due_date=date(2026, 9, 20),
            frequency="monthly", end_date=date(2026, 12, 20),
        )
        october = root.occurrences.get(next_due_date=date(2026, 10, 20))
        october.skipped = True
        october.save(update_fields=["skipped"])
        root.delete()
        october.refresh_from_db()
        self.assertIsNone(october.series_id)

    def test_unresolved_occurrences_are_gone_after_deleting_the_series_head(self):
        root = Reminder.objects.create(
            account=self.account, amount=Decimal("10.00"), next_due_date=date(2026, 9, 20),
            frequency="monthly", end_date=date(2026, 12, 20),
        )
        occurrence_ids = list(root.occurrences.values_list("pk", flat=True))
        root.delete()
        self.assertFalse(Reminder.objects.filter(pk__in=occurrence_ids).exists())


class TransactionListDeleteAndLabelsTests(LoggedInTestCase):
    def test_delete_button_present_and_links_back_here(self):
        txn = Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("-1.00"))
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertContains(response, reverse("finance:transaction_delete", args=[txn.pk]))

    def test_deleting_from_the_list_returns_to_the_list(self):
        txn = Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("-1.00"))
        list_url = reverse("finance:transaction_list")
        # Real usage: the confirm-delete GET renders `next` as a hidden field, submitted with
        # the POST - not passed as a query string on the POST itself.
        response = self.client.post(reverse("finance:transaction_delete", args=[txn.pk]), {"next": list_url})
        self.assertRedirects(response, list_url)

    def test_status_filter_labels_renamed(self):
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertContains(response, "All Transactions")
        self.assertContains(response, ">Reconciled<")
        self.assertContains(response, ">Unreconciled<")
        self.assertNotContains(response, "Reconciled or not")
        self.assertNotContains(response, "Reconciled only")
        self.assertNotContains(response, "Not reconciled")
