"""Tests for the Phase 11.1 follow-up: main-categories-only charts, the Bills & Utilities
line chart, collapsible parent/child table rows, and the transaction list total's position."""
import json
from datetime import date
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


class CategoryTotalsTopLevelTests(LoggedInTestCase):
    def test_subcategory_amount_rolls_up_into_its_top_level_parent(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        supermarket = Category.objects.create(name="Supermarket", parent=groceries, category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=groceries, amount=Decimal("-20.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 6), category=supermarket, amount=Decimal("-30.00"))

        rows = reporting.category_totals_top_level(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        self.assertEqual(rows, [{"category_id": groceries.pk, "name": "Groceries", "total": Decimal("50.00")}])

    def test_signed_version_keeps_income_and_expense_separate_top_level_rows(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=groceries, amount=Decimal("-20.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 6), category=salary, amount=Decimal("2000.00"))
        rows = reporting.category_totals_top_level(date(2026, 1, 1), date(2026, 1, 31))
        by_name = {r["name"]: r["total"] for r in rows}
        self.assertEqual(by_name["Groceries"], Decimal("-20.00"))
        self.assertEqual(by_name["Salary"], Decimal("2000.00"))


class BillsAndUtilitiesSeriesTests(LoggedInTestCase):
    def test_returns_empty_when_the_category_does_not_exist(self):
        self.assertEqual(reporting.bills_and_utilities_series(date(2026, 1, 1), date(2026, 1, 31)), [])

    def test_one_point_per_month_with_the_periods_total(self):
        top = Category.objects.create(name="Bills & Utilities", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=top, amount=Decimal("-50.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 2, 5), category=top, amount=Decimal("-70.00"))
        points = reporting.bills_and_utilities_series(date(2026, 1, 1), date(2026, 2, 28))
        self.assertEqual([p["period"] for p in points], [date(2026, 1, 1), date(2026, 2, 1)])
        self.assertEqual([p["total"] for p in points], [Decimal("50.00"), Decimal("70.00")])


class ReportsPageMarkupTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Category.objects.create(name="Supermarket", parent=groceries, category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(account=self.account, date=date.today(), category=groceries, amount=Decimal("-10.00"))
        subcat = Category.objects.get(name="Supermarket")
        Transaction.objects.create(account=self.account, date=date.today(), category=subcat, amount=Decimal("-15.00"))

    def test_collapsible_toggle_and_parent_child_classes_are_present(self):
        response = self.client.get(reverse("finance:reports"))
        body = response.content.decode()
        self.assertIn("report-toggle-icon", body)
        self.assertIn("category-main", body)
        self.assertIn("category-sub", body)
        self.assertIn("report-value-sub", body)

    def test_subcategory_rows_are_collapsed_by_default(self):
        response = self.client.get(reverse("finance:reports"))
        body = response.content.decode()
        self.assertIn('data-cat-parent="incexp-', body)
        # Every subcategory row (data-cat-parent) is hidden until its own chevron is clicked -
        # not just present somewhere hidden for an unrelated reason.
        start = body.index('data-cat-parent="incexp-')
        row_start = body.rindex("<tr", 0, start)
        row_end = body.index(">", start)
        self.assertIn("hidden", body[row_start:row_end])

    def test_export_is_hidden_until_a_report_is_expanded(self):
        # No Bootstrap display-utility class here (2026-09-15 fix) - "d-inline-block" carries
        # "!important" on display, which silently overrides the "hidden" attribute's own
        # display:none and left the export button visible on every collapsed card.
        response = self.client.get(reverse("finance:reports"))
        body = response.content.decode()
        self.assertIn('class="report-export" hidden', body)

    def test_income_expense_chart_data_includes_the_three_totals(self):
        response = self.client.get(reverse("finance:reports"))
        body = response.content.decode()
        start = body.index('id="income-expense-chart-data"')
        end = body.index("</script>", start)
        data = json.loads(body[body.index(">", start) + 1:end])
        names = [row["name"] for row in data]
        self.assertIn("Total income", names)
        self.assertIn("Total expense", names)
        self.assertIn("Total", names)

    def test_filter_form_has_the_new_row_layout_fields(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertContains(response, "Average interval")
        self.assertContains(response, "Subtotal tables by")


class TransactionListTotalPositionTests(LoggedInTestCase):
    def test_total_sits_in_a_flex_row_with_the_transaction_count(self):
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertContains(response, "justify-content-between")
