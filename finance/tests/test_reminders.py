"""The Reminders pages: the list and its filters, entering from a reminder, editing one occurrence
or the whole series, deleting, and the queries the list makes. The rules are in
test_reminder_rules.py."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from ..forms import ReminderForm
from ..models import Category, Payee, Reminder, Transaction
from .base import TODAY_13_SEPT, TODAY_19_SEPT, LoggedInWithAccountTestCase, create_reminders


class ReminderSkipTests(LoggedInWithAccountTestCase):
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
        response = self.client.get(reverse("finance:reminder_skip", args=[reminder.pk]))
        self.assertRedirects(response, reverse("finance:reminder_list"))  # a GET only goes back to the list
        reminder.refresh_from_db()
        self.assertEqual(reminder.next_due_date, date(2026, 10, 1))


class EnterOpensAPrefilledFormTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.payee = Payee.objects.create(name="Landlord")
        cls.category = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        cls.reminder = Reminder.objects.create(
            account=cls.account,
            payee=cls.payee,
            category=cls.category,
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

    def _enter(self, reminder, on="01/10/2026"):
        return self.client.post(
            reverse("finance:transaction_add"),
            {
                "reminder": reminder.pk,
                "account": self.account.pk,
                "date": on,
                "transaction_number": "",
                "payee_name": "Landlord",
                "category": self.category.pk,
                "memo": "Rent",
                "amount": "-500.00",
            },
            follow=True,
        )

    def test_the_message_names_the_date_it_was_due_and_the_date_it_moved_to(self):
        # S52: the reminder is named before it moves on, so the two dates differ.
        response = self._enter(self.reminder)
        self.assertEqual(
            [str(m) for m in response.context["messages"]],
            ["Entered 01/10/2026 | Landlord | -500.00 and moved it to 01/11/2026."],
        )

    def test_the_message_for_a_one_time_reminder_has_no_move(self):
        one_time = Reminder.objects.create(
            account=self.account,
            payee=self.payee,
            category=self.category,
            amount=Decimal("500.00"),
            reminder_type=Category.CategoryType.EXPENSE,
            next_due_date=date(2026, 10, 1),
            frequency="one_time",
        )
        response = self._enter(one_time)
        self.assertEqual([str(m) for m in response.context["messages"]], ["Entered 01/10/2026 | Landlord | -500.00."])

    def test_entering_one_occurrence_names_it_and_leaves_the_series_where_it_is(self):
        # An occurrence is a one-time reminder of its own: no "moved it to", and the series head stays put.
        head = Reminder.objects.create(
            account=self.account,
            payee=self.payee,
            category=self.category,
            amount=Decimal("500.00"),
            reminder_type=Category.CategoryType.EXPENSE,
            next_due_date=date(2026, 10, 1),
            frequency="monthly",
            end_date=date(2026, 12, 1),
        )
        november = head.occurrences.get(next_due_date=date(2026, 11, 1))
        response = self._enter(november, on="01/11/2026")
        self.assertEqual([str(m) for m in response.context["messages"]], ["Entered 01/11/2026 | Landlord | -500.00."])
        head.refresh_from_db()
        november.refresh_from_db()
        self.assertEqual(head.next_due_date, date(2026, 10, 1))
        self.assertEqual(november.status(), "paid")

    def test_editing_an_existing_transaction_ignores_a_reminder_param(self):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("-1.00"))
        response = self.client.get(reverse("finance:transaction_edit", args=[txn.pk]), {"reminder": self.reminder.pk})
        self.assertIsNone(response.context["reminder"])


class CategoryDropdownFilteredByTypeTests(LoggedInWithAccountTestCase):
    def test_category_options_carry_a_data_category_type_attribute(self):
        Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        html = str(ReminderForm()["category"])
        self.assertIn('data-category-type="expense"', html)
        self.assertIn('data-category-type="income"', html)


class ReminderListFilterTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.soon = Reminder.objects.create(account=cls.account, amount=Decimal("1"), next_due_date=date(2026, 9, 15))
        cls.later = Reminder.objects.create(account=cls.account, amount=Decimal("1"), next_due_date=date(2026, 12, 1))
        txn = Transaction.objects.create(account=cls.account, date=date(2026, 9, 1), amount=Decimal("-1.00"))
        cls.paid = Reminder.objects.create(
            account=cls.account,
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
        self.assertEqual(ReminderForm()["frequency"].value(), Reminder.Frequency.ONE_TIME)

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


class ReminderFormFieldOrderTests(LoggedInWithAccountTestCase):
    def test_end_of_period_sits_between_next_due_date_and_end_date(self):
        fields = list(ReminderForm().fields.keys())
        self.assertLess(fields.index("next_due_date"), fields.index("end_of_period"))
        self.assertLess(fields.index("end_of_period"), fields.index("end_date"))

    def test_end_of_period_has_no_help_text_and_is_relabeled(self):
        field = ReminderForm().fields["end_of_period"]
        self.assertEqual(field.help_text, "")
        self.assertEqual(field.label, "Last day of period")

    def test_the_unit_words_arrive_as_a_json_script_block(self):
        # 8.7 (F39): data for a script goes through json_script, which escapes it, never |safe.
        response = self.client.get(reverse("finance:reminder_add"))
        self.assertContains(
            response,
            '<script id="frequency-unit-words" type="application/json">'
            '{"weekly": "week(s)", "monthly": "month(s)", "quarterly": "quarter(s)", "yearly": "year(s)"}</script>',
            html=False,
        )


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
        Reminder.objects.create(account=self.account, amount=Decimal("50.00"), next_due_date=timezone.localdate())
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        self.assertContains(response, 'amount-negative">-50.00')

    def test_income_stays_positive(self):
        Reminder.objects.create(
            account=self.account,
            amount=Decimal("50.00"),
            reminder_type="income",
            next_due_date=timezone.localdate(),
        )
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        self.assertContains(response, 'amount-positive">50.00')
        self.assertNotContains(response, "amount-negative")


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
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.root = Reminder.objects.create(
            account=cls.account,
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 20),
            frequency="monthly",
            end_date=date(2026, 12, 20),
        )
        cls.november = cls.root.occurrences.get(next_due_date=date(2026, 11, 20))

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


class ReminderListShowsPayeeTests(LoggedInWithAccountTestCase):
    def test_the_list_shows_the_payee(self):
        payee = Payee.objects.create(name="Landlord")
        Reminder.objects.create(
            account=self.account, payee=payee, amount=Decimal("500.00"), next_due_date=date(2026, 10, 1)
        )
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        self.assertContains(response, "Landlord")
        self.assertContains(response, "<th>Payee</th>", html=True)


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

    def test_choosing_all_shows_the_reminders_outside_this_month_too(self, _):
        inside = self._reminder(date(2026, 9, 28))
        outside = self._reminder(date(2026, 12, 1))
        response = self.client.get(reverse("finance:reminder_list"), {"window": "all"})
        self.assertEqual({r.pk for r in response.context["reminders"]}, {inside.pk, outside.pk})

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
        self.assertContains(response, 'name="show_done" value="1" checked')
        response = self.client.get(reverse("finance:reminder_list"), {"window": "month", "show_done": "0"})
        self.assertEqual(list(response.context["reminders"]), [])


class ReminderSkipMessageTests(LoggedInWithAccountTestCase):
    """The Skip message names the date that was skipped, taken before a recurring reminder moves on."""

    def _skip(self, **fields):
        reminder = Reminder.objects.create(
            account=self.account, amount=Decimal("10.00"), next_due_date=date(2026, 10, 1), **fields
        )
        response = self.client.post(reverse("finance:reminder_skip", args=[reminder.pk]), follow=True)
        return [str(m) for m in response.context["messages"]]

    def test_a_recurring_reminder_names_the_date_skipped_and_the_date_it_moved_to(self):
        messages = self._skip(frequency="monthly", end_date=date(2026, 12, 1))
        self.assertEqual(messages, ["Skipped 01/10/2026 | Reminder | -10.00; moved it to 01/11/2026."])

    def test_a_one_time_reminder_has_no_move(self):
        self.assertEqual(self._skip(frequency="one_time"), ["Skipped 01/10/2026 | Reminder | -10.00."])


class ReminderListQueryTests(LoggedInWithAccountTestCase):
    """8.15 (F56): the reminder list loads each reminder's payee and category with the list."""

    def queries_with(self, count):
        create_reminders(self.account, count)
        response, query_count = self.get_and_count_queries(reverse("finance:reminder_list") + "?window=all")
        self.assertEqual(len(response.context["reminders"]), Reminder.objects.count())
        return query_count

    def test_more_reminders_need_no_more_queries(self):
        self.assertEqual(self.queries_with(1), self.queries_with(4))
