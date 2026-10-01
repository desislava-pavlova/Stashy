from datetime import date
from decimal import Decimal
from unittest import mock

from django.urls import reverse

from .models import Reminder, Transaction
from .test_phase7_2 import LoggedInTestCase

TODAY = date(2026, 9, 19)


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class ThisMonthTests(LoggedInTestCase):
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


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class EnteredPeriodStaysVisibleTests(LoggedInTestCase):
    def _recurring(self):
        return Reminder.objects.create(
            account=self.account, amount=Decimal("5"), next_due_date=date(2026, 9, 25), frequency="monthly",
        )

    def test_entering_a_recurring_reminder_leaves_a_paid_row_for_that_month(self, _):
        root = self._recurring()
        self.client.post(reverse("finance:transaction_add"), {
            "reminder": root.pk, "account": self.account.pk, "date": "25/09/2026",
            "transaction_number": "", "payee_name": "", "category": "", "memo": "", "amount": "-5.00",
        })
        response = self.client.get(reverse("finance:reminder_list"))
        paid = [r for r in response.context["reminders"] if r.status(TODAY) == "paid"]
        self.assertEqual([r.next_due_date for r in paid], [date(2026, 9, 25)])
        root.refresh_from_db()
        self.assertEqual(root.next_due_date, date(2026, 10, 25))
        self.assertEqual(root.occurrences.filter(next_due_date=date(2026, 9, 25)).count(), 1)

    def test_skipping_a_recurring_reminder_leaves_a_skipped_row(self, _):
        root = self._recurring()
        self.client.post(reverse("finance:reminder_skip", args=[root.pk]))
        response = self.client.get(reverse("finance:reminder_list"))
        self.assertEqual([r.status(TODAY) for r in response.context["reminders"]], ["skipped"])

    def test_checkbox_is_shown_and_unticking_hides_paid_rows(self, _):
        root = self._recurring()
        self.client.post(reverse("finance:reminder_skip", args=[root.pk]))
        response = self.client.get(reverse("finance:reminder_list"))
        self.assertContains(response, "Show paid/skipped")
        self.assertContains(response, "checked")
        response = self.client.get(reverse("finance:reminder_list"), {"window": "month", "show_done": "0"})
        self.assertEqual(list(response.context["reminders"]), [])
