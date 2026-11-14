"""Exports: CSV and Excel files of the transactions, the lists and every report, and their
file names."""

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.db import connections
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from openpyxl import load_workbook

from ..models import Account, Category, Payee, Profile, Transaction
from ..views.exports import REPORT_EXPORTS
from .base import LoggedInWithAccountTestCase


class ExportTransactionsTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        payee = Payee.objects.create(name="Landlord")
        Transaction.objects.create(
            account=cls.account,
            date=date(2026, 1, 1),
            payee=payee,
            category=rent,
            memo="January rent",
            amount=Decimal("-500.00"),
            reconciled=True,
        )
        Account.objects.create(name="Savings")

    def test_csv_export_contains_the_row(self):
        response = self.client.get(reverse("finance:export_transactions"), {"format": "csv"})
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(
            rows[0], ["Date", "Number", "Account", "Payee", "Category", "Memo", "Amount", "Currency", "Reconciled"]
        )
        self.assertIn(
            ["2026-01-01", rows[1][1], "Checking", "Landlord", "Rent", "January rent", "-500.00", "EUR", "Y"], rows
        )

    def test_xlsx_export_has_the_right_content_type(self):
        response = self.client.get(reverse("finance:export_transactions"), {"format": "xlsx"})
        self.assertEqual(response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertTrue(len(response.content) > 0)

    def test_export_respects_the_current_filters(self):
        savings = Account.objects.get(name="Savings")
        Transaction.objects.create(account=savings, date=date(2026, 1, 2), amount=Decimal("10.00"))
        response = self.client.get(
            reverse("finance:export_transactions"), {"format": "csv", "account": self.account.pk}
        )
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        accounts_in_export = {row[2] for row in rows[1:]}
        self.assertEqual(accounts_in_export, {"Checking"})

    def test_export_has_every_matching_row_not_just_the_page_on_screen(self):
        # 8.11: the export link carries the page you're on, and the export ignores it.
        Transaction.objects.bulk_create(
            Transaction(account=self.account, date=date(2026, 1, 1), amount=Decimal("-1.00")) for _ in range(1001)
        )
        response = self.client.get(reverse("finance:export_transactions"), {"format": "csv", "range": "", "page": 2})
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 1 + 1002)  # the header, the rent row and the 1,001 new ones


class ExportOtherListsTests(LoggedInWithAccountTestCase):
    def test_accounts_export_lists_every_account(self):
        response = self.client.get(reverse("finance:export_accounts"), {"format": "csv"})
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        names = [row[0] for row in rows[1:]]
        self.assertIn("Checking", names)

    def test_categories_export_is_a_full_export_including_transfer_categories(self):
        # 2026-09-14 follow-up: "full export of the data" - system-managed rows are included
        # now, just marked, rather than left out silently.
        Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        response = self.client.get(reverse("finance:export_categories"), {"format": "csv"})
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        by_name = {row[0]: row for row in rows[1:]}
        self.assertIn("Groceries", by_name)
        self.assertEqual(by_name["Groceries"][4], "N")
        transfer_name = self.account.ensure_transfer_category().name
        self.assertIn(transfer_name, by_name)
        self.assertEqual(by_name[transfer_name][4], "Y")

    def test_payees_export_is_a_full_export_including_system_payees(self):
        Payee.objects.create(name="Lidl")
        Payee.system(Payee.OPENING_BALANCE)
        response = self.client.get(reverse("finance:export_payees"), {"format": "csv"})
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        by_name = {row[0]: row for row in rows[1:]}
        self.assertIn("Lidl", by_name)
        self.assertEqual(by_name["Lidl"][2], "N")
        self.assertIn(Payee.OPENING_BALANCE, by_name)
        self.assertEqual(by_name[Payee.OPENING_BALANCE][2], "Y")


class ExportTests(LoggedInWithAccountTestCase):
    """1.11 (F33, F46): exports open correctly in Excel and can't run formulas."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=cls.account,
            date=date(2026, 9, 1),
            category=rent,
            payee=Payee.objects.create(name="Магазин"),
            memo='=HYPERLINK("http://evil.example")',
            amount=Decimal("-500.00"),
        )

    def export(self, fmt):
        return self.client.get(reverse("finance:export_transactions"), {"format": fmt})

    def test_csv_starts_with_a_bom_and_escapes_formulas_but_not_amounts(self):
        content = self.export("csv").content.decode("utf-8")
        self.assertTrue(content.startswith("﻿"))
        row = list(csv.reader(io.StringIO(content.lstrip("﻿"))))[1]
        self.assertEqual(row[3], "Магазин")
        self.assertEqual(row[5], '\'=HYPERLINK("http://evil.example")')
        self.assertEqual(row[6], "-500.00")

    def test_xlsx_has_real_numbers_and_dates_and_formulas_stay_text(self):
        sheet = load_workbook(io.BytesIO(self.export("xlsx").content)).active
        date_cell, amount_cell, memo_cell = sheet["A2"], sheet["G2"], sheet["F2"]
        self.assertEqual(date_cell.value, datetime(2026, 9, 1))
        self.assertEqual(date_cell.number_format, "DD/MM/YYYY")
        self.assertEqual(amount_cell.value, -500)
        self.assertEqual(amount_cell.number_format, "0.00")
        self.assertEqual(memo_cell.data_type, "s")
        self.assertEqual(memo_cell.value, '=HYPERLINK("http://evil.example")')


class ReportExportTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=cls.account, date=date.today(), category=cls.groceries, amount=Decimal("-42.00")
        )

    def test_spending_export_has_a_plain_amount_column_when_not_subtotaled(self):
        response = self.client.get(reverse("finance:export_report", args=["spending"]), {"format": "csv"})

        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["Category", "Spending"])

    def test_spending_export_gets_period_columns_when_subtotaled(self):
        response = self.client.get(
            reverse("finance:export_report", args=["spending"]), {"format": "csv", "subtotal": "month"}
        )

        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertIn("Total", rows[0])
        self.assertGreater(len(rows[0]), 2)  # Category + at least one period + Total

    def test_watched_expenses_export_404s_when_nothing_is_chosen(self):
        response = self.client.get(reverse("finance:export_report", args=["watched-expenses"]), {"format": "csv"})
        self.assertEqual(response.status_code, 404)

    def test_net_worth_export_has_a_group_row_an_account_row_and_a_total_row(self):
        response = self.client.get(reverse("finance:export_report", args=["net-worth"]), {"format": "csv"})

        rows = list(csv.reader(io.StringIO(response.content.decode())))
        names = [row[0] for row in rows[1:]]
        self.assertIn("  Checking", names)
        self.assertIn("Physical Liquid", names)
        self.assertIn("Total", names)


class ReportExportRegistryTests(LoggedInWithAccountTestCase):
    """5.2 (F15, F4): one registry and one view for every report export."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=cls.account, date=date.today(), category=cls.groceries, amount=Decimal("-42.00")
        )

    def _csv(self, slug, **params):
        response = self.client.get(reverse("finance:export_report", args=[slug]), {"format": "csv", **params})
        return response, list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))

    def test_the_urls_are_the_same_as_before_the_merge(self):
        for slug in REPORT_EXPORTS:
            with self.subTest(slug=slug):
                self.assertEqual(reverse("finance:export_report", args=[slug]), f"/reports/export/{slug}/")

    def test_every_report_exports_as_csv_and_xlsx(self):
        profile = Profile.for_user(self.user)
        profile.watched_expenses_categories.add(self.groceries)
        for slug in REPORT_EXPORTS:
            for fmt, content_type in (("csv", "text/csv"), ("xlsx", "spreadsheetml")):
                with self.subTest(slug=slug, format=fmt):
                    response = self.client.get(reverse("finance:export_report", args=[slug]), {"format": fmt})
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(content_type, response["Content-Type"])
                    self.assertIn(f"stashy-{REPORT_EXPORTS[slug].filename}-", response["Content-Disposition"])

    def test_an_unknown_report_is_a_404(self):
        response = self.client.get(reverse("finance:export_report", args=["no-such-report"]))
        self.assertEqual(response.status_code, 404)

    def test_watched_expenses_without_categories_explains_itself(self):
        response = self.client.get(reverse("finance:export_report", args=["watched-expenses"]))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "add some in Settings", status_code=404)

    def test_each_layout_keeps_its_columns(self):
        _response, rows = self._csv("income-expense")
        self.assertEqual(rows[0], ["Category", "Amount"])
        _response, rows = self._csv("spending-vs-average")
        self.assertEqual(rows[0], ["Category", "Current", "Average", "Difference"])
        _response, rows = self._csv("net-worth")
        self.assertEqual(rows[0][0], "Account")
        self.assertEqual(rows[-1][0], "Total")

    def test_income_expense_vs_average_export_has_the_summary_rows_the_page_shows(self):
        """S3: the page adds an Income total and an Expense total row; the export used to lack them."""
        salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        Transaction.objects.create(account=self.account, date=date.today(), category=salary, amount=Decimal("100.00"))

        _response, rows = self._csv("income-expense-vs-average")
        by_name = {row[0]: row for row in rows[1:]}
        page_table = self.client.get(reverse("finance:reports")).context["income_expense_vs_average_table"]

        self.assertEqual(by_name["Income total"][1], "100.00")
        self.assertEqual(by_name["Expense total"][1], "42.00")
        page_names = [row["name"] for group in page_table["groups"] for row in group["rows"]]
        self.assertEqual([row[0].strip() for row in rows[1:] if row[0] != "Total"], page_names)

    def test_income_expense_vs_average_export_leaves_every_average_blank_with_no_earlier_periods(self):
        """ "Earliest to date" has nothing earlier to average: the Summary rows say so like the
        category rows do, instead of an average of 0.00."""
        _response, rows = self._csv("income-expense-vs-average", range="earliest")
        by_name = {row[0].strip(): row for row in rows[1:]}

        self.assertEqual(by_name["Expense total"][1], "42.00")
        for name in ("Groceries", "Income total", "Expense total"):
            with self.subTest(name=name):
                self.assertEqual(by_name[name][2:], ["", ""])  # Average, Difference

    def test_every_export_link_on_the_reports_page_works(self):
        Profile.for_user(self.user).watched_expenses_categories.add(self.groceries)
        page = self.client.get(reverse("finance:reports"), {"subtotal": "month"}).content.decode()
        links = set(re.findall(r'href="(/reports/export/[^"]+)"', page))
        slugs = {re.match(r"/reports/export/([^/]+)/", link).group(1) for link in links}
        self.assertEqual(slugs, set(REPORT_EXPORTS))
        for link in links:
            with self.subTest(link=link):
                response = self.client.get(link.replace("&amp;", "&"))
                self.assertEqual(response.status_code, 200)
                self.assertIn("subtotal=month", link)


class RefundExportTests(LoggedInWithAccountTestCase):
    """S50 and S3 in the files: a refund nets against its category in the CSV and the Excel file,
    a category that nets to a refund is a real negative number (never text), and the Income &
    expense vs average file holds the same rows and numbers as the page."""

    JANUARY = {"range": "custom", "start": "01/01/2026", "end": "31/01/2026"}

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        cls.organic = Category.objects.create(
            name="Organic", parent=cls.groceries, category_type=Category.CategoryType.EXPENSE
        )
        cls.salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)

    def add(self, amount, category=None, on=date(2026, 1, 10)):
        Transaction.objects.create(
            account=self.account, date=on, category=category or self.groceries, amount=Decimal(amount)
        )

    def get_export(self, slug, fmt="csv", **params):
        return self.client.get(
            reverse("finance:export_report", args=[slug]), {"format": fmt, **self.JANUARY, **params}
        )

    def csv_rows(self, slug, **params):
        response = self.get_export(slug, **params)
        return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))

    def test_a_refund_shrinks_its_category_in_the_spending_csv(self):
        self.add("-100.00")
        self.add("30.00")

        rows = self.csv_rows("spending")

        self.assertEqual(rows, [["Category", "Spending"], ["Groceries", "70.00"], ["Total", "70.00"]])

    def test_a_category_that_nets_to_a_refund_is_a_negative_number_in_the_csv_not_escaped_text(self):
        self.add("-40.00")
        self.add("100.00")

        rows = self.csv_rows("spending")

        self.assertEqual(rows[1:], [["Groceries", "-60.00"], ["Total", "-60.00"]])  # no leading apostrophe

    def test_a_category_that_nets_to_a_refund_is_a_real_negative_number_in_excel(self):
        self.add("-40.00")
        self.add("100.00")

        sheet = load_workbook(io.BytesIO(self.get_export("spending", "xlsx").content)).active

        for cell in (sheet["B2"], sheet["B3"]):  # the Groceries row and the Total row
            self.assertEqual(cell.value, -60)
            self.assertEqual(cell.data_type, "n")
            self.assertEqual(cell.number_format, "0.00")

    def test_a_subcategorys_refund_is_indented_under_its_parent_and_nets_inside_it(self):
        self.add("-30.00")
        self.add("10.00", self.organic)

        rows = self.csv_rows("spending")

        self.assertEqual(rows[1:], [["Groceries", "20.00"], ["  Organic", "-10.00"], ["Total", "20.00"]])

    def test_a_subtotaled_export_nets_each_period_on_its_own(self):
        self.add("-100.00", on=date(2026, 1, 10))
        self.add("30.00", on=date(2026, 2, 10))

        rows = self.csv_rows("spending", range="custom", start="01/01/2026", end="28/02/2026", subtotal="month")

        self.assertEqual(rows[0], ["Category", "Jan 2026", "Feb 2026", "Total"])
        self.assertEqual(rows[1], ["Groceries", "100.00", "-30.00", "70.00"])
        self.assertEqual(rows[2], ["Total", "100.00", "-30.00", "70.00"])

    def test_a_category_refunded_in_full_exports_as_plain_zero_not_minus_zero(self):
        self.add("-100.00")
        self.add("100.00")

        rows = self.csv_rows("spending")

        self.assertEqual(rows[1:], [["Groceries", "0.00"], ["Total", "0.00"]])

    def test_the_watched_expenses_file_nets_a_refund_too(self):
        Profile.for_user(self.user).watched_expenses_categories.set([self.groceries])
        self.add("-40.00")
        self.add("100.00")

        rows = self.csv_rows("watched-expenses")

        self.assertEqual(rows[1:], [["Groceries", "-60.00"], ["Total", "-60.00"]])

    def test_the_combined_file_keeps_expenses_negative_and_nets_a_refund_into_them(self):
        self.add("-100.00")
        self.add("30.00")
        self.add("1000.00", self.salary)

        rows = self.csv_rows("income-expense")

        self.assertEqual(rows[1:], [["Salary", "1000.00"], ["Groceries", "-70.00"], ["Total", "930.00"]])

    def test_the_spending_vs_average_file_nets_a_refund_in_the_current_and_the_average_columns(self):
        self.add("-40.00")
        self.add("100.00")
        self.add("-50.00", on=date(2025, 12, 10))

        rows = self.csv_rows("spending-vs-average")

        self.assertEqual(rows[0], ["Category", "Current", "Average", "Difference"])
        self.assertEqual(rows[1], ["Groceries", "-60.00", "50.00", "-110.00"])

    def add_two_months_for_the_vs_average_tables(self):
        self.add("1000.00", self.salary)
        self.add("-250.00")
        self.add("30.00")  # a refund this month
        self.add("800.00", self.salary, on=date(2025, 12, 5))
        self.add("-200.00", on=date(2025, 12, 6))

    def test_the_income_and_expense_vs_average_file_has_the_same_rows_and_numbers_as_the_page(self):
        self.add_two_months_for_the_vs_average_tables()

        table = self.client.get(reverse("finance:reports"), self.JANUARY).context["income_expense_vs_average_table"]
        rows = self.csv_rows("income-expense-vs-average")

        def cells(row):
            average, difference = row["average"], row["difference"]
            return [
                f"{row['current']:.2f}",
                "" if average is None else f"{average:.2f}",
                "" if difference is None else f"{difference:.2f}",
            ]

        page_rows = [(row["name"], cells(row)) for group in table["groups"] for row in group["rows"]]
        file_rows = [(row[0].strip(), row[1:]) for row in rows[1:] if row[0] != "Total"]
        self.assertEqual(file_rows, page_rows)
        self.assertEqual(dict(file_rows)["Income total"], ["1000.00", "800.00", "200.00"])
        self.assertEqual(dict(file_rows)["Expense total"], ["220.00", "200.00", "20.00"])  # 250 less the refund

    def test_the_income_and_expense_vs_average_excel_file_has_the_summary_rows_as_numbers(self):
        self.add_two_months_for_the_vs_average_tables()

        sheet = load_workbook(io.BytesIO(self.get_export("income-expense-vs-average", "xlsx").content)).active

        by_name = {row[0].value.strip(): row[1:] for row in sheet.iter_rows(min_row=2)}
        self.assertEqual([cell.value for cell in by_name["Income total"]], [1000, 800, 200])
        self.assertEqual([cell.value for cell in by_name["Expense total"]], [220, 200, 20])
        for cell in by_name["Expense total"]:
            self.assertEqual((cell.data_type, cell.number_format), ("n", "0.00"))

    def test_a_refund_only_periods_expense_total_is_a_negative_number_in_the_file(self):
        self.add("1000.00", self.salary)
        self.add("50.00")
        self.add("-200.00", on=date(2025, 12, 6))

        rows = {row[0]: row[1:] for row in self.csv_rows("income-expense-vs-average")}

        self.assertEqual(rows["Expense total"], ["-50.00", "200.00", "-250.00"])


class ExportNamesCarryTheTimeTests(LoggedInWithAccountTestCase):
    """Plan 04 R4: several exports on one day get their own names."""

    STAMP = r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}"

    def test_a_list_export_has_the_date_and_time(self):
        response = self.client.get(reverse("finance:export_accounts"), {"format": "csv"})
        self.assertRegex(response["Content-Disposition"], rf'filename="stashy-accounts-{self.STAMP}\.csv"')

    def test_a_report_export_has_the_date_and_time(self):
        response = self.client.get(reverse("finance:export_report", args=["net-worth"]), {"format": "xlsx"})
        self.assertRegex(response["Content-Disposition"], rf'filename="stashy-net-worth-{self.STAMP}\.xlsx"')
        self.assertTrue(re.search(self.STAMP, response["Content-Disposition"]))


class AccountExportQueryTests(LoggedInWithAccountTestCase):
    """8.15 (F56): every balance in the accounts export comes from the same query."""

    def queries_with(self, count):
        for i in range(count):
            account = Account.objects.create(name=f"Account {count}-{i}")
            Transaction.objects.create(account=account, date=date(2026, 1, 5), amount=Decimal("10.00"))
        url = reverse("finance:export_accounts")
        self.client.get(url)  # the first page of a session also creates its Profile
        with CaptureQueriesContext(connections[settings.TEST_STASH]) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_more_accounts_need_no_more_queries(self):
        self.assertEqual(self.queries_with(1), self.queries_with(4))
