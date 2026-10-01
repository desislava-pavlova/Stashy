"""Tests for the Phase 11.1 follow-up: main-categories-only charts, collapsible parent/child
table rows, and the transaction list total's position. The Bills & Utilities line-chart tests
were retired in Phase 13 (replaced by Settings-configurable "Watched expenses", tested in
test_phase13_reports.py)."""

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
        supermarket = Category.objects.create(
            name="Supermarket", parent=groceries, category_type=Category.CategoryType.EXPENSE
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=groceries, amount=Decimal("-20.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 6), category=supermarket, amount=Decimal("-30.00")
        )

        rows = reporting.category_totals_top_level(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        self.assertEqual(rows, [{"category_id": groceries.pk, "name": "Groceries", "total": Decimal("50.00")}])

    def test_signed_version_keeps_income_and_expense_separate_top_level_rows(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=groceries, amount=Decimal("-20.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 6), category=salary, amount=Decimal("2000.00")
        )
        rows = reporting.category_totals_top_level(date(2026, 1, 1), date(2026, 1, 31))
        by_name = {r["name"]: r["total"] for r in rows}
        self.assertEqual(by_name["Groceries"], Decimal("-20.00"))
        self.assertEqual(by_name["Salary"], Decimal("2000.00"))

    def test_income_only_returns_positive_magnitudes_sorted_biggest_first(self):
        salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        gift = Category.objects.create(name="Gifts", category_type=Category.CategoryType.INCOME)
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=salary, amount=Decimal("2000.00")
        )
        Transaction.objects.create(account=self.account, date=date(2026, 1, 6), category=gift, amount=Decimal("50.00"))
        rows = reporting.category_totals_top_level(date(2026, 1, 1), date(2026, 1, 31), income_only=True)
        self.assertEqual([r["name"] for r in rows], ["Salary", "Gifts"])


class ReportsPageMarkupTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Category.objects.create(name="Supermarket", parent=groceries, category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=self.account, date=date.today(), category=groceries, amount=Decimal("-10.00")
        )
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
        # No Bootstrap display-utility class here - "d-inline-block" carries "!important" on
        # display, which silently overrides the "hidden" attribute's own display:none.
        response = self.client.get(reverse("finance:reports"))
        body = response.content.decode()
        self.assertIn('class="report-export" hidden', body)

    def test_net_replaces_total_in_the_income_expense_stat_row(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertContains(response, ">Net<")

    def test_filter_form_has_the_new_row_layout_fields(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertContains(response, "Average interval")
        self.assertContains(response, "Subtotal by")

    def test_show_hide_all_button_is_present(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertContains(response, "report-showhide-btn")


class TransactionListTotalPositionTests(LoggedInTestCase):
    def test_total_sits_in_a_flex_row_with_the_transaction_count(self):
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertContains(response, "justify-content-between")
