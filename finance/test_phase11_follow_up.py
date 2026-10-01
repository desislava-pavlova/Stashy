"""Tests for the Phase 11 same-day follow-up: category-hierarchy report tables (pivoted by
an optional subtotal period, rolled up like the category list page), the vs.-average
difference column, per-account net worth, per-report exports, and the transaction list total."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import reporting
from .forms import DateRangeFilterForm
from .models import Account, Category, Transaction


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


class PeriodBucketTests(SimpleTestCase):
    def test_month_label(self):
        _key, label = reporting.period_bucket(date(2026, 3, 15), "month")
        self.assertEqual(label, "Mar 2026")

    def test_quarter_label(self):
        _key, label = reporting.period_bucket(date(2026, 8, 1), "quarter")
        self.assertEqual(label, "Q3 2026")

    def test_half_year_label(self):
        self.assertEqual(reporting.period_bucket(date(2026, 2, 1), "half_year")[1], "H1 2026")
        self.assertEqual(reporting.period_bucket(date(2026, 9, 1), "half_year")[1], "H2 2026")

    def test_year_label(self):
        self.assertEqual(reporting.period_bucket(date(2026, 1, 1), "year")[1], "2026")

    def test_same_month_different_days_share_a_bucket_key(self):
        key1, _ = reporting.period_bucket(date(2026, 3, 1), "month")
        key2, _ = reporting.period_bucket(date(2026, 3, 31), "month")
        self.assertEqual(key1, key2)


class CategoryReportRowsTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        self.supermarket = Category.objects.create(
            name="Supermarket", parent=self.groceries, category_type=Category.CategoryType.EXPENSE
        )
        self.salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)

    def test_parent_rolls_up_its_subcategory_total(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 6), category=self.supermarket, amount=Decimal("-30.00"))
        table = reporting.category_report_rows(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        rows = table["groups"][0]["rows"]
        parent_row = next(r for r in rows if r["name"] == "Groceries")
        child_row = next(r for r in rows if r["name"] == "Supermarket")
        self.assertEqual(parent_row["total"], Decimal("50.00"))
        self.assertFalse(parent_row["is_child"])
        self.assertEqual(child_row["total"], Decimal("30.00"))
        self.assertTrue(child_row["is_child"])

    def test_subcategory_appears_right_after_its_parent(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=self.supermarket, amount=Decimal("-10.00"))
        table = reporting.category_report_rows(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        names = [r["name"] for r in table["groups"][0]["rows"]]
        self.assertEqual(names, ["Groceries", "Supermarket"])

    def test_income_group_comes_before_expense_group(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 6), category=self.salary, amount=Decimal("2000.00"))
        table = reporting.category_report_rows(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual([g["name"] for g in table["groups"]], ["Income", "Expense"])

    def test_grand_total_counts_each_top_level_category_once(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 6), category=self.supermarket, amount=Decimal("-30.00"))
        table = reporting.category_report_rows(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        # Not -20 + -30 + -50 (which would double count the rolled-up parent) - just -50.
        self.assertEqual(table["totals"]["total"], Decimal("50.00"))

    def test_no_subtotal_gives_a_single_value_and_no_period_columns(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00"))
        table = reporting.category_report_rows(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        self.assertEqual(table["periods"], [])
        row = table["groups"][0]["rows"][0]
        self.assertEqual(row["values"], [Decimal("20.00")])

    def test_subtotal_by_month_splits_into_one_column_per_month_plus_a_total(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 2, 5), category=self.groceries, amount=Decimal("-30.00"))
        table = reporting.category_report_rows(date(2026, 1, 1), date(2026, 2, 28), subtotal="month", expense_only=True)
        self.assertEqual(table["periods"], ["Jan 2026", "Feb 2026"])
        row = table["groups"][0]["rows"][0]
        self.assertEqual(row["values"], [Decimal("20.00"), Decimal("30.00")])
        self.assertEqual(row["total"], Decimal("50.00"))

    def test_empty_period_returns_no_groups_and_no_totals(self):
        table = reporting.category_report_rows(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        self.assertEqual(table["groups"], [])
        self.assertIsNone(table["totals"])

    def test_category_ids_restricts_to_that_set(self):
        other = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 6), category=other, amount=Decimal("-500.00"))
        table = reporting.category_report_rows(
            date(2026, 1, 1), date(2026, 1, 31), expense_only=True, category_ids=[self.groceries.pk, self.supermarket.pk]
        )
        names = [r["name"] for group in table["groups"] for r in group["rows"]]
        self.assertEqual(names, ["Groceries"])


class CategoryVsAverageReportRowsTests(LoggedInTestCase):
    def test_difference_is_current_minus_average(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        start, end = date(2026, 3, 1), date(2026, 3, 31)
        period_start = start - timedelta(days=31)  # 2026-01-29, the same 31-day period back
        Transaction.objects.create(account=self.account, date=start, category=groceries, amount=Decimal("-50.00"))
        Transaction.objects.create(account=self.account, date=period_start, category=groceries, amount=Decimal("-30.00"))

        periods = reporting.vs_average_periods(start, end, 1)
        table = reporting.category_vs_average_report_rows(start, end, periods, expense_only=True)
        row = next(r for group in table["groups"] for r in group["rows"] if r["name"] == "Groceries")
        self.assertEqual(row["current"], Decimal("50.00"))
        self.assertEqual(row["average"], Decimal("30.00"))
        self.assertEqual(row["difference"], Decimal("20.00"))
        self.assertEqual(table["totals"]["difference"], Decimal("20.00"))

    def test_no_activity_returns_no_groups(self):
        periods = reporting.vs_average_periods(date(2026, 3, 1), date(2026, 3, 31), 3)
        table = reporting.category_vs_average_report_rows(date(2026, 3, 1), date(2026, 3, 31), periods, expense_only=True)
        self.assertEqual(table["groups"], [])
        self.assertIsNone(table["totals"])

    def test_no_earlier_activity_means_no_average_or_difference(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        start, end = date(2026, 3, 1), date(2026, 3, 31)
        Transaction.objects.create(account=self.account, date=start, category=groceries, amount=Decimal("-50.00"))
        table = reporting.category_vs_average_report_rows(start, end, [], expense_only=True)
        row = next(r for group in table["groups"] for r in group["rows"] if r["name"] == "Groceries")
        self.assertIsNone(row["average"])
        self.assertIsNone(row["difference"])


class NetWorthByAccountTests(LoggedInTestCase):
    def test_each_account_gets_its_own_running_balance_per_period(self):
        savings = Account.objects.create(name="Savings")
        Transaction.objects.create(account=self.account, date=date(2026, 1, 10), amount=Decimal("100.00"))
        Transaction.objects.create(account=savings, date=date(2026, 2, 10), amount=Decimal("50.00"))

        table = reporting.net_worth_by_account(date(2026, 1, 1), date(2026, 2, 28), "month")
        self.assertEqual(table["periods"], ["Jan 2026", "Feb 2026"])
        by_name = {row["name"]: row["values"] for group in table["groups"] for row in group["rows"]}
        self.assertEqual(by_name["Checking"], [Decimal("100.00"), Decimal("100.00")])
        self.assertEqual(by_name["Savings"], [Decimal("0.00"), Decimal("50.00")])

    def test_each_group_gets_its_own_rolled_up_total(self):
        savings = Account.objects.create(name="Savings")
        Transaction.objects.create(account=self.account, date=date(2026, 1, 10), amount=Decimal("100.00"))
        Transaction.objects.create(account=savings, date=date(2026, 1, 15), amount=Decimal("50.00"))
        table = reporting.net_worth_by_account(date(2026, 1, 1), date(2026, 1, 31), "month")
        # Both accounts default to the same account_type, so they land in one group together.
        self.assertEqual(len(table["groups"]), 1)
        self.assertEqual(table["groups"][0]["totals"], [Decimal("150.00")])

    def test_total_row_sums_every_account_per_period(self):
        savings = Account.objects.create(name="Savings")
        Transaction.objects.create(account=self.account, date=date(2026, 1, 10), amount=Decimal("100.00"))
        Transaction.objects.create(account=savings, date=date(2026, 1, 15), amount=Decimal("50.00"))
        table = reporting.net_worth_by_account(date(2026, 1, 1), date(2026, 1, 31), "month")
        self.assertEqual(table["totals"], [Decimal("150.00")])


class DateRangeFormSubtotalFieldTests(SimpleTestCase):
    def test_subtotal_field_exists_with_expected_choices(self):
        form = DateRangeFilterForm()
        self.assertIn("subtotal", form.fields)
        values = [choice[0] for choice in form.fields["subtotal"].choices]
        self.assertEqual(values, ["", "week", "month", "quarter", "half_year", "year"])

    def test_interval_field_is_now_labeled_average_interval(self):
        form = DateRangeFilterForm()
        self.assertEqual(form.fields["interval"].label, "Average interval")

    def test_interval_field_choices_are_lookback_period_counts(self):
        form = DateRangeFilterForm()
        values = [choice[0] for choice in form.fields["interval"].choices]
        self.assertEqual(values, ["3", "6", "9", "12"])


class ReportExportTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(account=self.account, date=date.today(), category=self.groceries, amount=Decimal("-42.00"))

    def test_spending_export_has_a_plain_amount_column_when_not_subtotaled(self):
        response = self.client.get(reverse("finance:export_report_spending"), {"format": "csv"})
        import csv, io
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["Category", "Spending"])

    def test_spending_export_gets_period_columns_when_subtotaled(self):
        response = self.client.get(reverse("finance:export_report_spending"), {"format": "csv", "subtotal": "month"})
        import csv, io
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertIn("Total", rows[0])
        self.assertGreater(len(rows[0]), 2)  # Category + at least one period + Total

    def test_watched_expenses_export_404s_when_nothing_is_chosen(self):
        response = self.client.get(reverse("finance:export_report_watched_expenses"), {"format": "csv"})
        self.assertEqual(response.status_code, 404)

    def test_net_worth_export_has_a_group_row_an_account_row_and_a_total_row(self):
        response = self.client.get(reverse("finance:export_report_net_worth"), {"format": "csv"})
        import csv, io
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        names = [row[0] for row in rows[1:]]
        self.assertIn("  Checking", names)
        self.assertIn("Physical Liquid", names)
        self.assertIn("Total", names)


class ReportsPageExpandButtonTests(LoggedInTestCase):
    def test_expand_button_toggles_itself_not_a_separate_back_button(self):
        response = self.client.get(reverse("finance:reports"))
        body = response.content.decode()
        self.assertIn("data-expand-text", body)
        self.assertNotIn("reportsBackBtn", body)


class TransactionListTotalTests(LoggedInTestCase):
    def test_total_amount_is_the_sum_of_the_filtered_transactions(self):
        Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("100.00"))
        Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("-40.00"))
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertEqual(response.context["total_amount"], Decimal("60.00"))
        self.assertContains(response, "Total:")
