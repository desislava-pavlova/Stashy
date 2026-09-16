"""Tests for Phase 11 (Reports redesign): per-category totals (plain and vs. trailing
average) and the reports view's basic rendering. The Bills & Utilities-specific tests were
retired in Phase 13 (replaced by Settings-configurable "Watched expenses", tested in
test_phase13_reports.py); the vs.-average tests now build their own untrimmed period list via
reporting.vs_average_periods(), since Phase 13 added earliest-transaction-date trimming
(trimmed_average_periods()) that these fixed-date tests aren't exercising."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import reporting
from .models import Account, Category, Transaction


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


class CategoryTotalsTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        self.salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        self.other_account = Account.objects.create(name="Savings")

    def test_expense_only_returns_positive_magnitudes_sorted_biggest_first(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-30.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 6), category=self.groceries, amount=Decimal("-90.00"))
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        self.assertEqual(rows, [{"category_id": self.groceries.pk, "name": "Groceries", "total": Decimal("120.00")}])

    def test_signed_totals_span_income_and_expense_sorted_low_to_high(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-30.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 6), category=self.salary, amount=Decimal("2000.00"))
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual([r["name"] for r in rows], ["Groceries", "Salary"])
        self.assertEqual(rows[0]["total"], Decimal("-30.00"))
        self.assertEqual(rows[1]["total"], Decimal("2000.00"))

    def test_transfers_are_excluded(self):
        transfer_category = self.account.ensure_transfer_category()
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=transfer_category, amount=Decimal("-100.00"))
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(rows, [])

    def test_account_filter_limits_to_one_account(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-30.00"))
        Transaction.objects.create(account=self.other_account, date=date(2026, 1, 6), category=self.groceries, amount=Decimal("-15.00"))
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31), expense_only=True, account=self.account)
        self.assertEqual(rows, [{"category_id": self.groceries.pk, "name": "Groceries", "total": Decimal("30.00")}])

    def test_a_positive_amount_in_an_expense_category_just_nets_against_it_not_income(self):
        """R1 (Phase 13): the category's type alone decides income vs. spending, never the
        amount's sign - a refund (a positive amount under an expense category) nets against
        that category's own total instead of counting as income anywhere."""
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-100.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 6), category=self.groceries, amount=Decimal("30.00"))
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        self.assertEqual(rows, [{"category_id": self.groceries.pk, "name": "Groceries", "total": Decimal("70.00")}])
        totals = reporting.income_and_spending(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(totals["income"], Decimal("0.00"))

    def test_uncategorized_transactions_are_excluded(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), amount=Decimal("-30.00"))
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(rows, [])


class CategoryTotalsVsAverageTests(LoggedInTestCase):
    """The average is always over periods the same length as the given [start, end] range -
    a 31-day range averages over 31-day periods immediately preceding it, not calendar months,
    so these use fixed dates rather than "today" to keep period boundaries exact and
    reproducible. `periods` is built with the untrimmed reporting.vs_average_periods() (Phase 13
    added earliest-date trimming on top of this - see trimmed_average_periods() and
    test_phase13_reports.py)."""

    def setUp(self):
        super().setUp()
        self.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        self.start, self.end = date(2026, 3, 1), date(2026, 3, 31)  # 31 days

    def test_current_period_total_and_trailing_average(self):
        Transaction.objects.create(account=self.account, date=self.start, category=self.groceries, amount=Decimal("-50.00"))
        period_1_end = self.start - timedelta(days=1)  # 2026-02-28
        period_1_start = period_1_end - timedelta(days=30)  # 2026-01-29
        period_2_end = period_1_start - timedelta(days=1)  # 2026-01-28
        Transaction.objects.create(account=self.account, date=period_1_start, category=self.groceries, amount=Decimal("-100.00"))
        Transaction.objects.create(account=self.account, date=period_2_end, category=self.groceries, amount=Decimal("-300.00"))

        periods = reporting.vs_average_periods(self.start, self.end, 2)
        rows = reporting.category_totals_vs_average(self.start, self.end, periods, expense_only=True)
        row = next(r for r in rows if r["name"] == "Groceries")
        self.assertEqual(row["current"], Decimal("50.00"))
        self.assertEqual(row["average"], Decimal("200.00"))

    def test_average_uses_periods_the_same_length_as_the_current_range(self):
        # A 10-day range looks back in 10-day chunks, not calendar months.
        start, end = date(2026, 3, 1), date(2026, 3, 10)
        Transaction.objects.create(account=self.account, date=start, category=self.groceries, amount=Decimal("-10.00"))
        # Falls inside the immediately-preceding 10-day period (2026-02-19 .. 2026-02-28).
        Transaction.objects.create(account=self.account, date=date(2026, 2, 25), category=self.groceries, amount=Decimal("-40.00"))
        # Falls just outside that window, so it must not count toward the average.
        Transaction.objects.create(account=self.account, date=date(2026, 2, 18), category=self.groceries, amount=Decimal("-999.00"))

        periods = reporting.vs_average_periods(start, end, 1)
        rows = reporting.category_totals_vs_average(start, end, periods, expense_only=True)
        row = next(r for r in rows if r["name"] == "Groceries")
        self.assertEqual(row["average"], Decimal("40.00"))

    def test_period_with_no_activity_for_a_category_counts_as_zero(self):
        """R4 (Phase 13): a period with no activity for a category counts as zero toward its
        average, not skipped - 2 periods back, one with 0 and one with -20.00, averages to -10."""
        Transaction.objects.create(account=self.account, date=self.start, category=self.groceries, amount=Decimal("-30.00"))
        period_1_start = self.start - timedelta(days=31)
        Transaction.objects.create(account=self.account, date=period_1_start, category=self.groceries, amount=Decimal("-20.00"))
        # The 2nd trailing period gets no transaction at all - still divides by 2, not 1.
        periods = reporting.vs_average_periods(self.start, self.end, 2)
        rows = reporting.category_totals_vs_average(self.start, self.end, periods, expense_only=True)
        row = next(r for r in rows if r["name"] == "Groceries")
        self.assertEqual(row["average"], Decimal("10.00"))

    def test_a_category_with_no_earlier_activity_has_no_average(self):
        # 16/09/2026: an average of 0 read as "spent nothing", when the truth is "nothing to
        # compare with" - so there's no average at all, and the chart draws no dot.
        Transaction.objects.create(account=self.account, date=self.start, category=self.groceries, amount=Decimal("-20.00"))
        rows = reporting.category_totals_vs_average(self.start, self.end, [], expense_only=True)
        row = next(r for r in rows if r["name"] == "Groceries")
        self.assertEqual(row["current"], Decimal("20.00"))
        self.assertIsNone(row["average"])

    def test_signed_version_keeps_income_positive_and_expense_negative(self):
        salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        Transaction.objects.create(account=self.account, date=self.start, category=salary, amount=Decimal("2000.00"))
        Transaction.objects.create(account=self.account, date=self.start, category=self.groceries, amount=Decimal("-50.00"))
        periods = reporting.vs_average_periods(self.start, self.end, 3)
        rows = reporting.category_totals_vs_average(self.start, self.end, periods)
        by_name = {r["name"]: r["current"] for r in rows}
        self.assertEqual(by_name["Salary"], Decimal("2000.00"))
        self.assertEqual(by_name["Groceries"], Decimal("-50.00"))

    def test_account_filter_applies_to_both_the_current_and_average_sides(self):
        other_account = Account.objects.create(name="Savings")
        Transaction.objects.create(account=self.account, date=self.start, category=self.groceries, amount=Decimal("-50.00"))
        Transaction.objects.create(account=other_account, date=self.start, category=self.groceries, amount=Decimal("-999.00"))
        period_1_start = self.start - timedelta(days=31)
        Transaction.objects.create(account=self.account, date=period_1_start, category=self.groceries, amount=Decimal("-30.00"))
        Transaction.objects.create(account=other_account, date=period_1_start, category=self.groceries, amount=Decimal("-999.00"))

        periods = reporting.vs_average_periods(self.start, self.end, 1)
        rows = reporting.category_totals_vs_average(self.start, self.end, periods, expense_only=True, account=self.account)
        row = next(r for r in rows if r["name"] == "Groceries")
        self.assertEqual(row["current"], Decimal("50.00"))
        self.assertEqual(row["average"], Decimal("30.00"))


class ReportsViewTests(LoggedInTestCase):
    def test_reports_page_loads(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertEqual(response.status_code, 200)

    def test_defaults_to_this_month(self):
        # R3 (Phase 13): This month - no subtotal - Last 3 periods - All accounts.
        response = self.client.get(reverse("finance:reports"))
        today = date.today()
        self.assertEqual(response.context["start"], today.replace(day=1))
        self.assertEqual(response.context["end"], today)
        self.assertEqual(response.context["average_periods"], 3)

    def test_savings_rate_is_shown_for_the_period(self):
        salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        today = date.today()
        Transaction.objects.create(account=self.account, date=today, category=salary, amount=Decimal("1000.00"))
        Transaction.objects.create(account=self.account, date=today, category=rent, amount=Decimal("-250.00"))
        response = self.client.get(reverse("finance:reports"), {"range": "this_month"})
        self.assertEqual(response.context["savings_rate"], Decimal("75.0"))
        self.assertContains(response, "75.0%")

    def test_no_income_means_no_savings_rate_shown(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertIsNone(response.context["savings_rate"])

    def test_account_filter_applies_to_the_by_category_reports(self):
        other_account = Account.objects.create(name="Savings")
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        today = date.today()
        Transaction.objects.create(account=self.account, date=today, category=groceries, amount=Decimal("-30.00"))
        Transaction.objects.create(account=other_account, date=today, category=groceries, amount=Decimal("-999.00"))
        response = self.client.get(reverse("finance:reports"), {"range": "this_month", "account": self.account.pk})
        self.assertEqual(response.context["spending_by_category"], [{"category_id": groceries.pk, "name": "Groceries", "total": Decimal("30.00")}])
