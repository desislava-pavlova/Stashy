"""Report maths in finance/reporting.py: totals per category and period, averages, date ranges,
currency conversion and net worth, plus the TransactionQuerySet filters every report starts
from."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from .. import reporting
from ..currency import RateTable
from ..forms import DateRangeFilterForm
from ..models import Account, Category, ExchangeRate, Transaction
from ..reporting import income_and_spending, spending_tree
from .base import LoggedInWithAccountTestCase, StashTestCase

EXPENSE, INCOME = Category.CategoryType.EXPENSE, Category.CategoryType.INCOME


class CategoryTotalsTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        cls.salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        cls.other_account = Account.objects.create(name="Savings")

    def test_expense_only_returns_positive_magnitudes_sorted_biggest_first(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-30.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 6), category=self.groceries, amount=Decimal("-90.00")
        )
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        self.assertEqual(rows, [{"category_id": self.groceries.pk, "name": "Groceries", "total": Decimal("120.00")}])

    def test_signed_totals_span_income_and_expense_sorted_low_to_high(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-30.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 6), category=self.salary, amount=Decimal("2000.00")
        )
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual([r["name"] for r in rows], ["Groceries", "Salary"])
        self.assertEqual(rows[0]["total"], Decimal("-30.00"))
        self.assertEqual(rows[1]["total"], Decimal("2000.00"))

    def test_transfers_are_excluded(self):
        transfer_category = self.account.ensure_transfer_category()
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=transfer_category, amount=Decimal("-100.00")
        )
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(rows, [])

    def test_account_filter_limits_to_one_account(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-30.00")
        )
        Transaction.objects.create(
            account=self.other_account, date=date(2026, 1, 6), category=self.groceries, amount=Decimal("-15.00")
        )
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31), expense_only=True, account=self.account)
        self.assertEqual(rows, [{"category_id": self.groceries.pk, "name": "Groceries", "total": Decimal("30.00")}])

    def test_a_positive_amount_in_an_expense_category_just_nets_against_it_not_income(self):
        """R1 (Phase 13): the category's type alone decides income vs. spending, never the
        amount's sign - a refund (a positive amount under an expense category) nets against
        that category's own total instead of counting as income anywhere."""
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-100.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 6), category=self.groceries, amount=Decimal("30.00")
        )
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        self.assertEqual(rows, [{"category_id": self.groceries.pk, "name": "Groceries", "total": Decimal("70.00")}])
        totals = reporting.income_and_spending(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(totals["income"], Decimal("0.00"))

    def test_uncategorized_transactions_are_excluded(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), amount=Decimal("-30.00"))
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(rows, [])


class CategoryTotalsVsAverageTests(LoggedInWithAccountTestCase):
    """The average is always over periods the same length as the given [start, end] range -
    a 31-day range averages over 31-day periods immediately preceding it, not calendar months,
    so these use fixed dates rather than "today" to keep period boundaries exact and
    reproducible. `periods` is built with the untrimmed reporting.vs_average_periods() (Phase 13
    added earliest-date trimming on top of this - see trimmed_average_periods() and
    TrimmedAveragePeriodsTests below)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        cls.start, cls.end = date(2026, 3, 1), date(2026, 3, 31)  # 31 days

    def test_current_period_total_and_trailing_average(self):
        Transaction.objects.create(
            account=self.account, date=self.start, category=self.groceries, amount=Decimal("-50.00")
        )
        period_1_end = self.start - timedelta(days=1)  # 2026-02-28
        period_1_start = period_1_end - timedelta(days=30)  # 2026-01-29
        period_2_end = period_1_start - timedelta(days=1)  # 2026-01-28
        Transaction.objects.create(
            account=self.account, date=period_1_start, category=self.groceries, amount=Decimal("-100.00")
        )
        Transaction.objects.create(
            account=self.account, date=period_2_end, category=self.groceries, amount=Decimal("-300.00")
        )

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
        Transaction.objects.create(
            account=self.account, date=date(2026, 2, 25), category=self.groceries, amount=Decimal("-40.00")
        )
        # Falls just outside that window, so it must not count toward the average.
        Transaction.objects.create(
            account=self.account, date=date(2026, 2, 18), category=self.groceries, amount=Decimal("-999.00")
        )

        periods = reporting.vs_average_periods(start, end, 1)
        rows = reporting.category_totals_vs_average(start, end, periods, expense_only=True)
        row = next(r for r in rows if r["name"] == "Groceries")
        self.assertEqual(row["average"], Decimal("40.00"))

    def test_period_with_no_activity_for_a_category_counts_as_zero(self):
        """R4 (Phase 13): a period with no activity for a category counts as zero toward its
        average, not skipped - 2 periods back, one with 0 and one with -20.00, averages to -10."""
        Transaction.objects.create(
            account=self.account, date=self.start, category=self.groceries, amount=Decimal("-30.00")
        )
        period_1_start = self.start - timedelta(days=31)
        Transaction.objects.create(
            account=self.account, date=period_1_start, category=self.groceries, amount=Decimal("-20.00")
        )
        # The 2nd trailing period gets no transaction at all - still divides by 2, not 1.
        periods = reporting.vs_average_periods(self.start, self.end, 2)
        rows = reporting.category_totals_vs_average(self.start, self.end, periods, expense_only=True)
        row = next(r for r in rows if r["name"] == "Groceries")
        self.assertEqual(row["average"], Decimal("10.00"))

    def test_with_no_earlier_periods_there_is_no_average(self):
        # Nothing to compare with at all - no average, and the chart draws no dot. (A category
        # that merely had no activity in existing earlier periods averages 0: plan 03 R6.)
        Transaction.objects.create(
            account=self.account, date=self.start, category=self.groceries, amount=Decimal("-20.00")
        )
        rows = reporting.category_totals_vs_average(self.start, self.end, [], expense_only=True)
        row = next(r for r in rows if r["name"] == "Groceries")
        self.assertEqual(row["current"], Decimal("20.00"))
        self.assertIsNone(row["average"])

    def test_signed_version_keeps_income_positive_and_expense_negative(self):
        salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        Transaction.objects.create(account=self.account, date=self.start, category=salary, amount=Decimal("2000.00"))
        Transaction.objects.create(
            account=self.account, date=self.start, category=self.groceries, amount=Decimal("-50.00")
        )
        periods = reporting.vs_average_periods(self.start, self.end, 3)
        rows = reporting.category_totals_vs_average(self.start, self.end, periods)
        by_name = {r["name"]: r["current"] for r in rows}
        self.assertEqual(by_name["Salary"], Decimal("2000.00"))
        self.assertEqual(by_name["Groceries"], Decimal("-50.00"))

    def test_account_filter_applies_to_both_the_current_and_average_sides(self):
        other_account = Account.objects.create(name="Savings")
        Transaction.objects.create(
            account=self.account, date=self.start, category=self.groceries, amount=Decimal("-50.00")
        )
        Transaction.objects.create(
            account=other_account, date=self.start, category=self.groceries, amount=Decimal("-999.00")
        )
        period_1_start = self.start - timedelta(days=31)
        Transaction.objects.create(
            account=self.account, date=period_1_start, category=self.groceries, amount=Decimal("-30.00")
        )
        Transaction.objects.create(
            account=other_account, date=period_1_start, category=self.groceries, amount=Decimal("-999.00")
        )

        periods = reporting.vs_average_periods(self.start, self.end, 1)
        rows = reporting.category_totals_vs_average(
            self.start, self.end, periods, expense_only=True, account=self.account
        )
        row = next(r for r in rows if r["name"] == "Groceries")
        self.assertEqual(row["current"], Decimal("50.00"))
        self.assertEqual(row["average"], Decimal("30.00"))


class CategoryTotalsTopLevelTests(LoggedInWithAccountTestCase):
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


class CategoryReportRowsTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        cls.supermarket = Category.objects.create(
            name="Supermarket", parent=cls.groceries, category_type=Category.CategoryType.EXPENSE
        )
        cls.salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)

    def test_parent_rolls_up_its_subcategory_total(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 6), category=self.supermarket, amount=Decimal("-30.00")
        )
        table = reporting.category_report_rows(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        rows = table["groups"][0]["rows"]
        parent_row = next(r for r in rows if r["name"] == "Groceries")
        child_row = next(r for r in rows if r["name"] == "Supermarket")
        self.assertEqual(parent_row["total"], Decimal("50.00"))
        self.assertFalse(parent_row["is_child"])
        self.assertEqual(child_row["total"], Decimal("30.00"))
        self.assertTrue(child_row["is_child"])

    def test_subcategory_appears_right_after_its_parent(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.supermarket, amount=Decimal("-10.00")
        )
        table = reporting.category_report_rows(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        names = [r["name"] for r in table["groups"][0]["rows"]]
        self.assertEqual(names, ["Groceries", "Supermarket"])

    def test_income_group_comes_before_expense_group(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 6), category=self.salary, amount=Decimal("2000.00")
        )
        table = reporting.category_report_rows(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual([g["name"] for g in table["groups"]], ["Income", "Expense"])

    def test_grand_total_counts_each_top_level_category_once(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 6), category=self.supermarket, amount=Decimal("-30.00")
        )
        table = reporting.category_report_rows(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        # Not -20 + -30 + -50 (which would double count the rolled-up parent) - just -50.
        self.assertEqual(table["totals"]["total"], Decimal("50.00"))

    def test_no_subtotal_gives_a_single_value_and_no_period_columns(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00")
        )
        table = reporting.category_report_rows(date(2026, 1, 1), date(2026, 1, 31), expense_only=True)
        self.assertEqual(table["periods"], [])
        row = table["groups"][0]["rows"][0]
        self.assertEqual(row["values"], [Decimal("20.00")])

    def test_subtotal_by_month_splits_into_one_column_per_month_plus_a_total(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 2, 5), category=self.groceries, amount=Decimal("-30.00")
        )
        table = reporting.category_report_rows(
            date(2026, 1, 1), date(2026, 2, 28), subtotal="month", expense_only=True
        )
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
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 6), category=other, amount=Decimal("-500.00")
        )
        table = reporting.category_report_rows(
            date(2026, 1, 1),
            date(2026, 1, 31),
            expense_only=True,
            category_ids=[self.groceries.pk, self.supermarket.pk],
        )
        names = [r["name"] for group in table["groups"] for r in group["rows"]]
        self.assertEqual(names, ["Groceries"])


class CategoryVsAverageReportRowsTests(LoggedInWithAccountTestCase):
    def test_difference_is_current_minus_average(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        start, end = date(2026, 3, 1), date(2026, 3, 31)
        period_start = start - timedelta(days=31)  # 2026-01-29, the same 31-day period back
        Transaction.objects.create(account=self.account, date=start, category=groceries, amount=Decimal("-50.00"))
        Transaction.objects.create(
            account=self.account, date=period_start, category=groceries, amount=Decimal("-30.00")
        )

        periods = reporting.vs_average_periods(start, end, 1)
        table = reporting.category_vs_average_report_rows(start, end, periods, expense_only=True)
        row = next(r for group in table["groups"] for r in group["rows"] if r["name"] == "Groceries")
        self.assertEqual(row["current"], Decimal("50.00"))
        self.assertEqual(row["average"], Decimal("30.00"))
        self.assertEqual(row["difference"], Decimal("20.00"))
        self.assertEqual(table["totals"]["difference"], Decimal("20.00"))

    def test_no_activity_returns_no_groups(self):
        periods = reporting.vs_average_periods(date(2026, 3, 1), date(2026, 3, 31), 3)
        table = reporting.category_vs_average_report_rows(
            date(2026, 3, 1), date(2026, 3, 31), periods, expense_only=True
        )
        self.assertEqual(table["groups"], [])
        self.assertIsNone(table["totals"])

    def test_no_earlier_periods_means_no_average_or_difference(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        start, end = date(2026, 3, 1), date(2026, 3, 31)
        Transaction.objects.create(account=self.account, date=start, category=groceries, amount=Decimal("-50.00"))
        table = reporting.category_vs_average_report_rows(start, end, [], expense_only=True)
        row = next(r for group in table["groups"] for r in group["rows"] if r["name"] == "Groceries")
        self.assertIsNone(row["average"])
        self.assertIsNone(row["difference"])


class NetWorthByAccountTests(LoggedInWithAccountTestCase):
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


class EarliestToDateRangeTests(LoggedInWithAccountTestCase):
    def test_resolves_to_the_first_transactions_date(self):
        Transaction.objects.create(account=self.account, date=date(2021, 10, 3), amount=Decimal("10.00"))
        Transaction.objects.create(account=self.account, date=date(2022, 1, 1), amount=Decimal("5.00"))
        start, end, _ = reporting.resolve_range({"range": "earliest"})
        self.assertEqual(start, date(2021, 10, 3))
        self.assertEqual(end, date.today())

    def test_falls_back_to_today_with_no_transactions_at_all(self):
        start, _end, _ = reporting.resolve_range({"range": "earliest"})
        self.assertEqual(start, date.today())

    def test_respects_the_account_filter(self):
        other = Account.objects.create(name="Savings")
        Transaction.objects.create(account=self.account, date=date(2021, 1, 1), amount=Decimal("1.00"))
        Transaction.objects.create(account=other, date=date(2023, 6, 1), amount=Decimal("1.00"))
        start, _, _ = reporting.resolve_range({"range": "earliest"}, account=other)
        self.assertEqual(start, date(2023, 6, 1))

    def test_is_a_choice_on_the_form(self):
        form = DateRangeFilterForm()
        values = [choice[0] for choice in form.fields["range"].choices]
        self.assertIn("earliest", values)


class TrimmedAveragePeriodsTests(LoggedInWithAccountTestCase):
    """Phase 13 R4: "periods before the data begins don't count, whatever Average interval says"."""

    def test_periods_entirely_before_the_first_transaction_are_dropped(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        start, end = date(2026, 3, 1), date(2026, 3, 31)
        # Only transaction is the current period's own - every one of the 3 trailing 31-day
        # periods ends before it, so none should count.
        Transaction.objects.create(account=self.account, date=start, category=groceries, amount=Decimal("-20.00"))
        periods = reporting.trimmed_average_periods(start, end, 3, account=self.account)
        self.assertEqual(periods, [])

    def test_a_period_the_range_only_partly_precedes_data_is_kept(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        start, end = date(2026, 3, 1), date(2026, 3, 31)
        # The first trailing period is 2026-01-29..2026-02-28; a transaction on 2026-02-15
        # means that period doesn't entirely precede the data, so it's kept.
        Transaction.objects.create(
            account=self.account, date=date(2026, 2, 15), category=groceries, amount=Decimal("-5.00")
        )
        periods = reporting.trimmed_average_periods(start, end, 3, account=self.account)
        self.assertEqual(len(periods), 1)

    def test_no_transactions_at_all_keeps_every_period(self):
        periods = reporting.trimmed_average_periods(date(2026, 3, 1), date(2026, 3, 31), 3)
        self.assertEqual(len(periods), 3)


class SubtotalOverflowTests(SimpleTestCase):
    def test_no_message_within_the_limit(self):
        self.assertIsNone(reporting.subtotal_overflow_message(date(2026, 1, 1), date(2026, 1, 31), "month"))

    def test_message_names_the_next_coarser_granularity_with_no_count(self):
        message = reporting.subtotal_overflow_message(date(2015, 1, 1), date(2026, 1, 1), "month")
        self.assertEqual(message, "Too many months to show here. Try subtotaling by quarter instead.")
        self.assertNotRegex(message, r"\d")

    def test_years_has_no_next_step(self):
        message = reporting.subtotal_overflow_message(date(1900, 1, 1), date(2026, 1, 1), "year")
        self.assertEqual(message, "Too many years to show here. Try a shorter date range.")

    def test_no_granularity_means_no_overflow(self):
        self.assertIsNone(reporting.subtotal_overflow_message(date(1900, 1, 1), date(2026, 1, 1), ""))


class ReportCurrencyConversionTests(LoggedInWithAccountTestCase):
    def test_converts_a_foreign_currency_account_using_the_latest_rate(self):
        usd_account = Account.objects.create(name="US Checking", currency="USD")
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=usd_account, date=date(2026, 1, 5), category=groceries, amount=Decimal("-100.00")
        )
        ExchangeRate.objects.create(
            base_currency="USD", quote_currency="EUR", rate=Decimal("0.9"), date=date(2026, 1, 1)
        )

        rates = RateTable("EUR")
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31), expense_only=True, rates=rates)
        self.assertEqual(rows, [{"category_id": groceries.pk, "name": "Groceries", "total": Decimal("90.00")}])

    def test_no_known_rate_excludes_it_rather_than_mixing_currencies(self):
        usd_account = Account.objects.create(name="US Checking", currency="USD")
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=usd_account, date=date(2026, 1, 5), category=groceries, amount=Decimal("-100.00")
        )
        rates = RateTable("EUR")
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31), expense_only=True, rates=rates)
        self.assertEqual(rows, [{"category_id": groceries.pk, "name": "Groceries", "total": Decimal("0.00")}])

    def test_net_worth_names_an_account_with_no_known_rate(self):
        usd_account = Account.objects.create(name="US Checking", currency="USD")
        Transaction.objects.create(account=usd_account, date=date(2026, 1, 5), amount=Decimal("50.00"))
        table = reporting.net_worth_by_account(date(2026, 1, 1), date(2026, 1, 31), "month", rates=RateTable("EUR"))
        self.assertIn("US Checking", table["missing_accounts"])


class CalendarAveragePeriodsTests(SimpleTestCase):
    """Phase 13 R4: a calendar Date range is averaged against whole earlier periods of the same
    kind, not earlier windows of the same length (This month on the 15th isn't compared with 15-day blocks)."""

    def test_this_month_compares_with_previous_full_months(self):
        periods = reporting.vs_average_periods(date(2026, 9, 1), date(2026, 9, 15), 3, range_key="this_month")
        self.assertEqual(
            periods,
            [
                (date(2026, 8, 1), date(2026, 8, 31)),
                (date(2026, 7, 1), date(2026, 7, 31)),
                (date(2026, 6, 1), date(2026, 6, 30)),
            ],
        )

    def test_last_month_compares_with_the_months_before_it(self):
        periods = reporting.vs_average_periods(date(2026, 8, 1), date(2026, 8, 31), 1, range_key="last_month")
        self.assertEqual(periods, [(date(2026, 7, 1), date(2026, 7, 31))])

    def test_quarter_compares_with_previous_quarters(self):
        periods = reporting.vs_average_periods(date(2026, 7, 1), date(2026, 9, 15), 2, range_key="this_quarter")
        self.assertEqual(periods, [(date(2026, 4, 1), date(2026, 6, 30)), (date(2026, 1, 1), date(2026, 3, 31))])

    def test_year_to_date_compares_with_previous_full_years(self):
        periods = reporting.vs_average_periods(date(2026, 1, 1), date(2026, 9, 15), 2, range_key="ytd")
        self.assertEqual(periods, [(date(2025, 1, 1), date(2025, 12, 31)), (date(2024, 1, 1), date(2024, 12, 31))])

    def test_last_12_months_compares_with_earlier_12_month_blocks(self):
        periods = reporting.vs_average_periods(date(2025, 10, 1), date(2026, 9, 15), 1, range_key="last_12_months")
        self.assertEqual(periods, [(date(2024, 10, 1), date(2025, 9, 30))])

    def test_custom_range_keeps_same_length_windows(self):
        periods = reporting.vs_average_periods(date(2026, 3, 1), date(2026, 3, 10), 1, range_key="custom")
        self.assertEqual(periods, [(date(2026, 2, 19), date(2026, 2, 28))])

    def test_earliest_to_date_has_nothing_before_it(self):
        self.assertEqual(
            reporting.vs_average_periods(date(2021, 1, 1), date(2026, 9, 15), 3, range_key="earliest"), []
        )


class TransferExclusionTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.account = Account.objects.create(name="Checking")
        cls.other = Account.objects.create(name="Savings")
        cls.groceries = Category.objects.create(name="Groceries")

    def test_transfers_are_excluded_from_income_and_spending(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00")
        )
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 6),
            category=self.account.transfer_category,
            amount=Decimal("-100.00"),
        )
        totals = income_and_spending(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(totals["spending"], Decimal("20.00"))

    def test_transfers_are_excluded_from_the_spending_donut(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00")
        )
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 6),
            category=self.account.transfer_category,
            amount=Decimal("-100.00"),
        )
        tree = spending_tree(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual([g["name"] for g in tree], ["Groceries"])


class TransactionQuerySetTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.wallet = Account.objects.create(name="Wallet")
        cls.bank = Account.objects.create(name="Bank")
        groceries = Category.objects.create(name="Groceries", category_type=EXPENSE)
        salary = Category.objects.create(name="Salary", category_type=INCOME)

        def add(account, day, category, amount):
            return Transaction.objects.create(
                account=account, date=date(2026, 1, day), category=category, amount=Decimal(amount)
            )

        cls.spent = add(cls.wallet, 1, groceries, "-10")
        cls.refund = add(cls.wallet, 31, groceries, "4")  # positive, still spending (R1)
        cls.earned = add(cls.bank, 15, salary, "100")
        cls.uncategorized = add(cls.wallet, 10, None, "-1")
        cls.transfer = add(cls.wallet, 10, cls.wallet.ensure_transfer_category(), "-50")
        cls.february = add(cls.wallet, 1, groceries, "-1")
        cls.february.date = date(2026, 2, 1)
        cls.february.save()

    def pks(self, queryset):
        return set(queryset.values_list("pk", flat=True))

    def test_in_range_includes_both_ends(self):
        january = Transaction.objects.in_range(date(2026, 1, 1), date(2026, 1, 31))
        self.assertNotIn(self.february.pk, self.pks(january))
        self.assertIn(self.refund.pk, self.pks(january))

    def test_reportable_leaves_out_transfers_and_uncategorized(self):
        reportable = self.pks(Transaction.objects.reportable())
        self.assertEqual(reportable, {self.spent.pk, self.refund.pk, self.earned.pk, self.february.pk})

    def test_of_kind_goes_by_the_category_type_not_the_sign(self):
        self.assertEqual(
            self.pks(Transaction.objects.reportable().of_kind(expense_only=True)),
            {self.spent.pk, self.refund.pk, self.february.pk},
        )
        self.assertEqual(self.pks(Transaction.objects.reportable().of_kind(income_only=True)), {self.earned.pk})

    def test_for_account_none_means_every_account(self):
        self.assertEqual(Transaction.objects.for_account(None).count(), Transaction.objects.count())
        self.assertEqual(self.pks(Transaction.objects.for_account(self.bank)), {self.earned.pk})


class RollupToParentsTests(StashTestCase):
    def test_a_parent_includes_its_subcategories_per_column(self):
        food = Category.objects.create(name="Food", category_type=EXPENSE)
        groceries = Category.objects.create(name="Groceries", category_type=EXPENSE, parent=food)
        cafe = Category.objects.create(name="Cafe", category_type=EXPENSE, parent=food)
        totals = {food.pk: [Decimal("1"), Decimal("0")], groceries.pk: [Decimal("2"), Decimal("3")]}

        categories = reporting._with_parents([groceries.pk, cafe.pk])
        rolled = reporting._rollup_to_parents(totals, categories, 2)

        self.assertEqual(rolled[food.pk], [Decimal("3"), Decimal("3")])
        self.assertEqual(rolled[groceries.pk], [Decimal("2"), Decimal("3")])
        self.assertEqual(totals[food.pk], [Decimal("1"), Decimal("0")])  # the input is left alone
