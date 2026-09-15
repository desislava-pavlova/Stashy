"""Tests for Phase 11 (Reports redesign): per-category totals (plain and vs. trailing
average), the Bills & Utilities report, and the reports view's rendering of all six."""
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


class CategoryTotalsVsAverageTests(LoggedInTestCase):
    """The average is always over periods the same length as the given [start, end] range
    (2026-09-15 follow-up) - a 31-day range averages over 31-day periods immediately preceding
    it, not calendar months, so these use fixed dates rather than "today" to keep period
    boundaries exact and reproducible."""

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

        rows = reporting.category_totals_vs_average(self.start, self.end, 2, expense_only=True)
        row = next(r for r in rows if r["name"] == "Groceries")
        self.assertEqual(row["current"], Decimal("50.00"))
        # Average only over the 2 prior periods that actually had spending, not divided by 12.
        self.assertEqual(row["average"], Decimal("200.00"))

    def test_average_uses_periods_the_same_length_as_the_current_range(self):
        # A 10-day range looks back in 10-day chunks, not calendar months.
        start, end = date(2026, 3, 1), date(2026, 3, 10)
        Transaction.objects.create(account=self.account, date=start, category=self.groceries, amount=Decimal("-10.00"))
        # Falls inside the immediately-preceding 10-day period (2026-02-19 .. 2026-02-28).
        Transaction.objects.create(account=self.account, date=date(2026, 2, 25), category=self.groceries, amount=Decimal("-40.00"))
        # Falls just outside that window, so it must not count toward the average.
        Transaction.objects.create(account=self.account, date=date(2026, 2, 18), category=self.groceries, amount=Decimal("-999.00"))

        rows = reporting.category_totals_vs_average(start, end, 1, expense_only=True)
        row = next(r for r in rows if r["name"] == "Groceries")
        self.assertEqual(row["average"], Decimal("40.00"))

    def test_category_with_no_history_still_appears(self):
        Transaction.objects.create(account=self.account, date=self.start, category=self.groceries, amount=Decimal("-20.00"))
        rows = reporting.category_totals_vs_average(self.start, self.end, 3, expense_only=True)
        row = next(r for r in rows if r["name"] == "Groceries")
        self.assertEqual(row["current"], Decimal("20.00"))
        self.assertEqual(row["average"], Decimal("0"))

    def test_signed_version_keeps_income_positive_and_expense_negative(self):
        salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        Transaction.objects.create(account=self.account, date=self.start, category=salary, amount=Decimal("2000.00"))
        Transaction.objects.create(account=self.account, date=self.start, category=self.groceries, amount=Decimal("-50.00"))
        rows = reporting.category_totals_vs_average(self.start, self.end, 3)
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

        rows = reporting.category_totals_vs_average(self.start, self.end, 1, expense_only=True, account=self.account)
        row = next(r for r in rows if r["name"] == "Groceries")
        self.assertEqual(row["current"], Decimal("50.00"))
        self.assertEqual(row["average"], Decimal("30.00"))


class BillsAndUtilitiesTests(LoggedInTestCase):
    def test_returns_none_when_the_category_does_not_exist(self):
        self.assertIsNone(reporting.bills_and_utilities_totals(date(2026, 1, 1), date(2026, 1, 31)))

    def test_matches_the_category_name_case_insensitively(self):
        Category.objects.create(name="bills & utilities", category_type=Category.CategoryType.EXPENSE)
        self.assertIsNotNone(reporting.bills_and_utilities_totals(date(2026, 1, 1), date(2026, 1, 31)))

    def test_includes_subcategories(self):
        top = Category.objects.create(name="Bills & Utilities", category_type=Category.CategoryType.EXPENSE)
        electricity = Category.objects.create(name="Electricity", parent=top, category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=top, amount=Decimal("-10.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 6), category=electricity, amount=Decimal("-40.00"))
        result = reporting.bills_and_utilities_totals(date(2026, 1, 1), date(2026, 1, 31))
        totals = {row["name"]: row["total"] for row in result["rows"]}
        self.assertEqual(totals, {"Bills & Utilities": Decimal("10.00"), "Electricity": Decimal("40.00")})

    def test_a_different_category_with_a_similar_name_is_not_matched(self):
        Category.objects.create(name="Utilities", category_type=Category.CategoryType.EXPENSE)
        self.assertIsNone(reporting.bills_and_utilities_totals(date(2026, 1, 1), date(2026, 1, 31)))


class ReportsViewTests(LoggedInTestCase):
    def test_reports_page_loads(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertEqual(response.status_code, 200)

    def test_defaults_to_year_to_date(self):
        response = self.client.get(reverse("finance:reports"))
        today = date.today()
        self.assertEqual(response.context["start"], today.replace(month=1, day=1))
        self.assertEqual(response.context["end"], today)

    def test_missing_bills_category_shows_a_create_prompt_not_a_chart(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertContains(response, "Missing")
        self.assertNotContains(response, 'id="billsChart"')

    def test_bills_category_present_renders_its_chart(self):
        Category.objects.create(name="Bills & Utilities", category_type=Category.CategoryType.EXPENSE)
        response = self.client.get(reverse("finance:reports"))
        self.assertContains(response, 'id="billsChart"')

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
