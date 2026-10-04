"""Exports: CSV and Excel files of the transactions, the lists and every report, and their
file names."""

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal

from django.urls import reverse
from openpyxl import load_workbook

from ..models import Account, Category, Payee, Profile, Transaction
from ..views.exports import REPORT_EXPORTS
from .base import LoggedInWithAccountTestCase


class ExportTransactionsTests(LoggedInWithAccountTestCase):
    def setUp(self):
        super().setUp()
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        payee = Payee.objects.create(name="Landlord")
        Transaction.objects.create(
            account=self.account,
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

    def setUp(self):
        super().setUp()
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=self.account,
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
    def setUp(self):
        super().setUp()
        self.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=self.account, date=date.today(), category=self.groceries, amount=Decimal("-42.00")
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

    def setUp(self):
        super().setUp()
        self.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=self.account, date=date.today(), category=self.groceries, amount=Decimal("-42.00")
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


class ExportNamesCarryTheTimeTests(LoggedInWithAccountTestCase):
    """Plan 03 R4: several exports on one day get their own names."""

    STAMP = r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}"

    def test_a_list_export_has_the_date_and_time(self):
        response = self.client.get(reverse("finance:export_accounts"), {"format": "csv"})
        self.assertRegex(response["Content-Disposition"], rf'filename="stashy-accounts-{self.STAMP}\.csv"')

    def test_a_report_export_has_the_date_and_time(self):
        response = self.client.get(reverse("finance:export_report", args=["net-worth"]), {"format": "xlsx"})
        self.assertRegex(response["Content-Disposition"], rf'filename="stashy-net-worth-{self.STAMP}\.xlsx"')
        self.assertTrue(re.search(self.STAMP, response["Content-Disposition"]))
