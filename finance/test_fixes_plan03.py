"""Plan 03 items done early: R4 (export file names carry the time), R5 (backup file names
carry the stash's name) and R6 (a category with no earlier activity averages 0)."""

import csv
import io
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from dateutil.relativedelta import relativedelta
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from . import backup as finance_backup
from . import reporting
from .models import Category, Transaction
from .test_cleanup_phase1 import LoggedInTestCase


def _make_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(path).close()
    return path


class BackupNamesCarryTheStashTests(SimpleTestCase):
    """R5: two stashes' backups never share a file name."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.stashes = Path(tmp.name)

    def _stash(self, name):
        folder = self.stashes / name
        _make_db(folder / "db.sqlite3")
        return folder

    def test_the_name_starts_with_the_stash(self):
        alex = self._stash("alex")
        result = finance_backup.backup_now(alex / "db.sqlite3", alex, tag="manual")
        self.assertRegex(result.name, r"^alex_backup_manual_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.sqlite3$")

    def test_two_stashes_backed_up_together_get_different_names(self):
        alex, sam = self._stash("alex"), self._stash("sam-2")
        first = finance_backup.backup_now(alex / "db.sqlite3", alex, tag="before_update")
        second = finance_backup.backup_now(sam / "db.sqlite3", sam, tag="before_update")
        self.assertNotEqual(first.name, second.name)
        self.assertTrue(second.name.startswith("sam-2_backup_before_update_"))

    def test_the_list_mixes_all_three_naming_schemes_newest_first(self):
        alex = self._stash("alex")
        backups = alex / "backups"
        backups.mkdir()
        for name in (
            "db_before_update_20260101_000000.sqlite3",
            "backup_manual_2026-06-01_00-00-00.sqlite3",
            "alex_backup_before_restore_2026-09-14_11-57-36.sqlite3",
        ):
            (backups / name).write_text("x")
        names = [p.name for p in finance_backup.list_backups(alex)]
        self.assertEqual(
            names,
            [
                "alex_backup_before_restore_2026-09-14_11-57-36.sqlite3",
                "backup_manual_2026-06-01_00-00-00.sqlite3",
                "db_before_update_20260101_000000.sqlite3",
            ],
        )

    def test_the_quarterly_check_sees_a_recent_quarterly_backup_under_the_new_name(self):
        alex = self._stash("alex")
        (alex / "backups").mkdir()
        recent = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        (alex / "backups" / f"alex_backup_quarterly_{recent}.sqlite3").write_text("x")
        self.assertIsNone(finance_backup.quarterly_backup_if_due(alex / "db.sqlite3", alex))

    def test_an_old_quarterly_backup_under_the_new_name_makes_a_new_one_due(self):
        alex = self._stash("alex")
        (alex / "backups").mkdir()
        old = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d_%H-%M-%S")
        (alex / "backups" / f"alex_backup_quarterly_{old}.sqlite3").write_text("x")
        self.assertIsNotNone(finance_backup.quarterly_backup_if_due(alex / "db.sqlite3", alex))

    def test_a_manual_backup_is_never_mistaken_for_a_quarterly_one(self):
        # A stash may even be called "quarterly": only the tag after "backup_" counts.
        quarterly = self._stash("quarterly")
        finance_backup.backup_now(quarterly / "db.sqlite3", quarterly, tag="manual")
        self.assertIsNotNone(finance_backup.quarterly_backup_if_due(quarterly / "db.sqlite3", quarterly))

    def test_restoring_leaves_a_safety_backup_named_after_the_stash(self):
        alex = self._stash("alex")
        source = finance_backup.backup_now(alex / "db.sqlite3", alex, tag="manual")
        finance_backup.restore_from(source, alex / "db.sqlite3", alex)
        tags = sorted(p.name.split("_backup_")[1].rsplit("_", 2)[0] for p in finance_backup.list_backups(alex))
        self.assertEqual(tags, ["before_restore", "manual"])


class ExportNamesCarryTheTimeTests(LoggedInTestCase):
    """R4: several exports on one day get their own names."""

    STAMP = r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}"

    def test_a_list_export_has_the_date_and_time(self):
        response = self.client.get(reverse("finance:export_accounts"), {"format": "csv"})
        self.assertRegex(response["Content-Disposition"], rf'filename="stashy-accounts-{self.STAMP}\.csv"')

    def test_a_report_export_has_the_date_and_time(self):
        response = self.client.get(reverse("finance:export_report", args=["net-worth"]), {"format": "xlsx"})
        self.assertRegex(response["Content-Disposition"], rf'filename="stashy-net-worth-{self.STAMP}\.xlsx"')
        self.assertTrue(re.search(self.STAMP, response["Content-Disposition"]))


class NoEarlierActivityAveragesZeroTests(LoggedInTestCase):
    """R6: with earlier periods to compare with, a category that had nothing in them averages
    0 - the page shows "—" for it but fills in the difference and draws the dot."""

    def setUp(self):
        super().setUp()
        self.month_start = timezone.localdate().replace(day=1)
        self.groceries = self._category("Groceries")
        self.accommodation = self._category("Accommodation")

    def _category(self, name, parent=None):
        return Category.objects.create(name=name, category_type=Category.CategoryType.EXPENSE, parent=parent)

    def _spend(self, category, amount, months_back=0):
        when = self.month_start - relativedelta(months=months_back)
        Transaction.objects.create(account=self.account, date=when, category=category, amount=Decimal(amount))

    def _three_earlier_months(self):
        for months_back in (1, 2, 3):
            self._spend(self.groceries, "-30.00", months_back)

    def _csv(self, slug):
        response = self.client.get(reverse("finance:export_report", args=[slug]), {"format": "csv"})
        return {row[0]: row[1:] for row in csv.reader(io.StringIO(response.content.decode("utf-8-sig")))}

    def test_the_export_has_a_zero_average_and_the_real_difference(self):
        self._three_earlier_months()
        self._spend(self.accommodation, "-583.50")
        self.assertEqual(self._csv("spending-vs-average")["Accommodation"], ["583.50", "0.00", "583.50"])
        # Expenses are negative in the combined report, so the same gap is negative there.
        self.assertEqual(self._csv("income-expense-vs-average")["Accommodation"], ["-583.50", "0.00", "-583.50"])

    def test_the_page_shows_a_dash_for_the_average_but_the_difference_and_a_dot(self):
        self._three_earlier_months()
        self._spend(self.accommodation, "-583.50")
        response = self.client.get(reverse("finance:reports"))
        self.assertTrue(response.context["has_average"])
        chart = {r["name"]: r for r in response.context["spending_vs_average_chart"]}
        self.assertEqual(chart["Accommodation"]["average"], 0.0)  # a dot at 0, not a gap

        # The Spending vs average table's own row, read from the page's real HTML.
        page = response.content.decode()
        row_html = re.search(rf'<tr data-cat-id="spendavg-{self.accommodation.pk}">(.*?)</tr>', page, re.S).group(1)
        cells = [cell.strip() for cell in re.findall(r'<td class="text-end[^"]*">([^<]*)</td>', row_html)]
        self.assertIn("583.50", cells[0])  # current
        self.assertEqual(cells[1], "—")  # average
        self.assertIn("583.50", cells[2])  # difference

    def test_a_category_with_some_earlier_activity_averages_over_every_period(self):
        self._three_earlier_months()
        self._spend(self.accommodation, "-600.00", months_back=2)
        self._spend(self.accommodation, "-100.00")
        self.assertEqual(self._csv("spending-vs-average")["Accommodation"], ["100.00", "200.00", "-100.00"])

    def test_a_main_category_whose_subcategory_is_brand_new_gets_a_dash_too(self):
        # Folds in R2: the parent and its new subcategory both average 0 and show "—".
        food = self._category("Food")
        bakery = self._category("Bakery", parent=food)
        self._three_earlier_months()
        self._spend(bakery, "-12.00")
        periods = reporting.vs_average_periods(self.month_start, timezone.localdate(), 3, range_key="this_month")
        table = reporting.category_vs_average_report_rows(
            self.month_start, timezone.localdate(), periods, expense_only=True
        )
        rows = {r["name"]: r for group in table["groups"] for r in group["rows"]}
        for name in ("Food", "Bakery"):
            self.assertEqual(rows[name]["average"], Decimal("0.00"))
            self.assertEqual(rows[name]["difference"], Decimal("12.00"))
            self.assertTrue(rows[name]["no_earlier_activity"])
        self.assertFalse(rows["Groceries"]["no_earlier_activity"])

    def test_a_main_category_counts_its_subcategories_earlier_activity(self):
        food = self._category("Food")
        bakery = self._category("Bakery", parent=food)
        self._spend(bakery, "-30.00", months_back=1)
        self._spend(self.groceries, "-30.00", months_back=3)
        self._spend(bakery, "-12.00")
        periods = reporting.vs_average_periods(self.month_start, timezone.localdate(), 3, range_key="this_month")
        table = reporting.category_vs_average_report_rows(
            self.month_start, timezone.localdate(), periods, expense_only=True
        )
        rows = {r["name"]: r for group in table["groups"] for r in group["rows"]}
        self.assertFalse(rows["Food"]["no_earlier_activity"])
        self.assertEqual(rows["Food"]["average"], Decimal("10.00"))

    def test_fewer_than_two_earlier_periods_still_means_no_earlier_data(self):
        self._spend(self.groceries, "-30.00", months_back=1)
        self._spend(self.accommodation, "-583.50")
        response = self.client.get(reverse("finance:reports"))
        self.assertFalse(response.context["has_average"])
        self.assertContains(response, "No earlier data to compare with.")
