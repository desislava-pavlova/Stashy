"""The tables the Reports page and the exports share
(finance/report_tables.py): the inputs they are built from, one builder
per report, and the page and the export holding the same table. The
maths under them is tested in test_reporting.py."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.db import connections
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .. import report_tables, reporting
from ..filters import report_filters
from ..models import Account, Category, Profile, Transaction
from ..report_tables import ReportInputs
from ..services.rates import RateTable
from ..views.exports import REPORT_EXPORTS
from .base import LoggedInWithAccountTestCase


EXPENSE, INCOME = Category.CategoryType.EXPENSE, Category.CategoryType.INCOME

JANUARY_TO_MARCH = {
    "range": "custom",
    "start": "01/01/2026",
    "end": "31/03/2026",
    "subtotal": "month",
}
MONTHS = ["Jan 2026", "Feb 2026", "Mar 2026"]


def names(table):
    """The row names of a table, groups run together."""
    return [row["name"] for group in table["groups"] for row in group["rows"]]


class ReportTablesTestCase(LoggedInWithAccountTestCase):
    """Some activity before the range and some in it. Food has a
    subcategory with a refund in February; Rent is paid in March."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.salary = Category.objects.create(name="Salary", category_type=INCOME)
        cls.food = Category.objects.create(name="Food", category_type=EXPENSE)
        cls.groceries = cls.food.create_subcategory("Groceries")
        cls.rent = Category.objects.create(name="Rent", category_type=EXPENSE)
        activity = [
            (date(2025, 11, 3), cls.salary, "900.00"),
            (date(2025, 12, 3), cls.salary, "950.00"),
            (date(2025, 12, 5), cls.groceries, "-40.00"),
            (date(2026, 1, 3), cls.salary, "1000.00"),
            (date(2026, 1, 6), cls.groceries, "-60.00"),
            (date(2026, 2, 6), cls.groceries, "-30.00"),
            (date(2026, 2, 7), cls.groceries, "10.00"),  # a refund
            (date(2026, 3, 1), cls.rent, "-500.00"),
        ]
        for when, category, amount in activity:
            Transaction.objects.create(
                account=cls.account, date=when, category=category, amount=Decimal(amount)
            )

    def inputs(self, query=None):
        """The inputs a page load with this address would build."""
        request = RequestFactory().get("/", JANUARY_TO_MARCH if query is None else query)
        profile = Profile.for_user(self.user)
        return ReportInputs(report_filters(request), RateTable(profile.main_currency), profile)

    def choose_watched(self, *categories):
        Profile.for_user(self.user).watched_expenses_categories.set(categories)


class ReportInputsTests(ReportTablesTestCase):
    def test_it_carries_the_filters_the_page_read(self):
        inputs = self.inputs({**JANUARY_TO_MARCH, "account": self.account.pk})

        self.assertEqual((inputs.start, inputs.end), (date(2026, 1, 1), date(2026, 3, 31)))
        self.assertEqual(inputs.subtotal, "month")
        self.assertEqual(inputs.account, self.account)
        self.assertEqual(inputs.earlier_periods, inputs.filters["periods"])
        self.assertEqual(inputs.main_currency, "EUR")

    def test_the_granularity_is_the_subtotal_or_month_when_there_is_none(self):
        self.assertEqual(self.inputs().granularity, "month")
        self.assertEqual(self.inputs({**JANUARY_TO_MARCH, "subtotal": ""}).granularity, "month")
        self.assertEqual(self.inputs({**JANUARY_TO_MARCH, "subtotal": "year"}).granularity, "year")

    def test_the_shared_sums_are_the_reporting_modules_answers(self):
        inputs = self.inputs()
        start, end, rates = inputs.start, inputs.end, inputs.rates

        self.assertEqual(
            inputs.spending_by_category,
            reporting.category_totals_top_level(start, end, expense_only=True, rates=rates),
        )
        self.assertEqual(
            inputs.income_by_category,
            reporting.category_totals_top_level(start, end, income_only=True, rates=rates),
        )
        self.assertEqual(
            inputs.range_totals, reporting.income_and_spending(start, end, rates=rates)
        )
        self.assertEqual(
            inputs.vs_average_totals,
            reporting.income_and_spending_vs_average(
                start, end, inputs.earlier_periods, rates=rates
            ),
        )

    def test_a_shared_sum_asks_the_database_once_however_often_it_is_read(self):
        self.choose_watched(self.rent)
        inputs = self.inputs()
        for name in (
            "spending_by_category",
            "income_by_category",
            "range_totals",
            "vs_average_totals",
            "watched_category_ids",
        ):
            with self.subTest(sum=name):
                with CaptureQueriesContext(connections[settings.TEST_STASH]) as first:
                    value = getattr(inputs, name)
                with CaptureQueriesContext(connections[settings.TEST_STASH]) as again:
                    same = getattr(inputs, name)

                self.assertGreater(len(first), 0)
                self.assertEqual(len(again), 0)
                self.assertIs(same, value)


class TableBuildersTests(ReportTablesTestCase):
    def test_spending_has_only_expense_categories_shown_as_spending(self):
        table = report_tables.spending_table(self.inputs())

        self.assertEqual(names(table), ["Food", "Groceries", "Rent"])
        self.assertEqual(table["periods"], MONTHS)
        food = table["groups"][0]["rows"][0]
        self.assertEqual(food["values"], [Decimal("60.00"), Decimal("20.00"), Decimal("0")])

    def test_income_has_only_income_categories(self):
        table = report_tables.income_table(self.inputs())

        self.assertEqual(names(table), ["Salary"])
        self.assertEqual(
            table["groups"][0]["rows"][0]["values"],
            [Decimal("1000.00"), Decimal("0"), Decimal("0")],
        )

    def test_income_and_expense_keeps_the_sign_with_income_first(self):
        table = report_tables.income_expense_table(self.inputs())

        self.assertEqual(names(table), ["Salary", "Food", "Groceries", "Rent"])
        food = next(
            row for group in table["groups"] for row in group["rows"] if row["name"] == "Food"
        )
        self.assertEqual(food["values"], [Decimal("-60.00"), Decimal("-20.00"), Decimal("0")])

    def test_the_tables_without_a_subtotal_have_one_column(self):
        table = report_tables.spending_table(self.inputs({**JANUARY_TO_MARCH, "subtotal": ""}))

        self.assertEqual(table["periods"], [])
        self.assertEqual(table["groups"][0]["rows"][0]["values"], [Decimal("80.00")])

    def test_the_vs_average_tables_compare_with_the_earlier_periods(self):
        inputs = self.inputs()

        spending = report_tables.spending_vs_average_table(inputs)
        income = report_tables.income_vs_average_table(inputs)

        self.assertEqual(names(spending), ["Food", "Groceries", "Rent"])
        self.assertEqual(names(income), ["Salary"])
        self.assertEqual(spending["average_periods"], len(inputs.earlier_periods))
        self.assertGreater(len(inputs.earlier_periods), 0)

    def test_income_and_expense_vs_average_ends_with_the_summary_rows(self):
        table = report_tables.income_expense_vs_average_table(self.inputs())

        summary = table["groups"][-1]
        self.assertEqual(summary["key"], "summary")
        self.assertEqual(
            [row["name"] for row in summary["rows"]], ["Income total", "Expense total"]
        )

    def test_net_worth_has_a_column_for_every_month_and_the_running_total(self):
        table = report_tables.net_worth_table(self.inputs())

        self.assertEqual(table["periods"], MONTHS)
        self.assertEqual(table["totals"][-1], Decimal("2230.00"))

    def test_net_worth_follows_the_subtotal_and_defaults_to_months(self):
        by_year = report_tables.net_worth_table(
            self.inputs({**JANUARY_TO_MARCH, "subtotal": "year"})
        )
        by_default = report_tables.net_worth_table(
            self.inputs({**JANUARY_TO_MARCH, "subtotal": ""})
        )

        self.assertEqual(by_year["periods"], ["2026"])
        self.assertEqual(by_default["periods"], MONTHS)

    def test_watched_expenses_is_none_until_a_category_is_chosen(self):
        self.assertIsNone(report_tables.watched_expenses_table(self.inputs()))

    def test_watched_expenses_lists_only_the_chosen_categories_as_spending(self):
        self.choose_watched(self.rent)

        table = report_tables.watched_expenses_table(self.inputs())

        self.assertEqual(names(table), ["Rent"])
        self.assertEqual(table["groups"][0]["rows"][0]["values"], [0, 0, Decimal("500.00")])


class AccountFilterThroughTheBuildersTests(ReportTablesTestCase):
    """The Account filter narrows every category table and every shared
    sum, because the page and the export both build them here. Savings
    holds its own Salary and a category only it uses (Travel)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.savings = Account.objects.create(name="Savings")
        cls.travel = Category.objects.create(name="Travel", category_type=EXPENSE)
        activity = [
            (date(2025, 12, 9), cls.travel, "-100.00"),
            (date(2026, 1, 9), cls.salary, "2000.00"),
            (date(2026, 2, 9), cls.travel, "-300.00"),
        ]
        for when, category, amount in activity:
            Transaction.objects.create(
                account=cls.savings, date=when, category=category, amount=Decimal(amount)
            )

    def in_account(self, account):
        return self.inputs({**JANUARY_TO_MARCH, "account": account.pk})

    def test_the_category_tables_hold_only_that_accounts_amounts(self):
        savings, checking = self.in_account(self.savings), self.in_account(self.account)

        self.assertEqual(names(report_tables.spending_table(savings)), ["Travel"])
        self.assertEqual(names(report_tables.income_table(savings)), ["Salary"])
        self.assertEqual(names(report_tables.income_expense_table(savings)), ["Salary", "Travel"])
        self.assertEqual(
            names(report_tables.spending_table(checking)), ["Food", "Groceries", "Rent"]
        )
        salary = report_tables.income_table(savings)["groups"][0]["rows"][0]
        self.assertEqual(salary["values"], [Decimal("2000.00"), Decimal("0"), Decimal("0")])

    def test_the_vs_average_tables_compare_only_that_accounts_amounts(self):
        savings = self.in_account(self.savings)

        spending = report_tables.spending_vs_average_table(savings)
        income = report_tables.income_vs_average_table(savings)
        combined = report_tables.income_expense_vs_average_table(savings)

        travel = spending["groups"][0]["rows"][0]
        self.assertEqual(
            (travel["name"], travel["current"], travel["average"]),
            ("Travel", Decimal("300.00"), Decimal("100.00")),
        )
        self.assertEqual(names(income), ["Salary"])
        self.assertEqual(names(combined)[:-2], ["Salary", "Travel"])  # then the Summary rows
        summary = {row["name"]: row["current"] for row in combined["groups"][-1]["rows"]}
        self.assertEqual(
            summary, {"Income total": Decimal("2000.00"), "Expense total": Decimal("300.00")}
        )

    def test_watched_expenses_lists_only_the_chosen_categories_that_account_spent_on(self):
        self.choose_watched(self.travel, self.rent)

        self.assertEqual(
            names(report_tables.watched_expenses_table(self.in_account(self.savings))), ["Travel"]
        )
        self.assertEqual(
            names(report_tables.watched_expenses_table(self.in_account(self.account))), ["Rent"]
        )

    def test_the_shared_sums_follow_the_account_too(self):
        savings = self.in_account(self.savings)

        self.assertEqual(
            [(row["name"], row["total"]) for row in savings.spending_by_category],
            [("Travel", Decimal("300.00"))],
        )
        self.assertEqual(
            [(row["name"], row["total"]) for row in savings.income_by_category],
            [("Salary", Decimal("2000.00"))],
        )
        self.assertEqual(
            savings.range_totals, {"income": Decimal("2000.00"), "spending": Decimal("300.00")}
        )
        self.assertEqual(savings.vs_average_totals["current_income"], Decimal("2000.00"))
        self.assertEqual(savings.vs_average_totals["current_spending"], Decimal("300.00"))

    def test_net_worth_lists_every_account_whatever_the_filter_says(self):
        """Today's behaviour, not a ruling: a balance is not a flow, and
        net_worth_by_account() takes no account."""
        table = report_tables.net_worth_table(self.in_account(self.savings))

        self.assertEqual(
            [row["name"] for group in table["groups"] for row in group["rows"]],
            ["Checking", "Savings"],
        )


class SubtotalOverflowThroughTheBuildersTests(ReportTablesTestCase):
    """Past SUBTOTAL_LIMIT (36) columns a table is the notice and
    nothing else, and the builders pass the Subtotal filter on to say
    so."""

    WEEKS_OF_2026 = {**JANUARY_TO_MARCH, "end": "31/12/2026", "subtotal": "week"}  # 53 columns
    NOTICE = "Too many weeks to show here. Try subtotaling by month instead."

    def test_every_subtotaled_category_table_is_only_the_notice(self):
        self.choose_watched(self.rent)
        inputs = self.inputs(self.WEEKS_OF_2026)

        for build in (
            report_tables.spending_table,
            report_tables.income_table,
            report_tables.income_expense_table,
            report_tables.watched_expenses_table,
        ):
            with self.subTest(report=build.__name__):
                table = build(inputs)
                self.assertEqual(table["overflow"], self.NOTICE)
                self.assertEqual(table["groups"], [])
                self.assertIsNone(table["totals"])

    def test_net_worth_is_only_the_notice_too(self):
        table = report_tables.net_worth_table(self.inputs(self.WEEKS_OF_2026))

        self.assertEqual(table["overflow"], self.NOTICE)
        self.assertEqual((table["groups"], table["totals"]), ([], []))

    def test_net_worth_counts_months_when_no_subtotal_is_chosen(self):
        """Nobody asked for columns, and four years is 48 months."""
        four_years = {"range": "custom", "start": "01/01/2023", "end": "31/12/2026"}

        table = report_tables.net_worth_table(self.inputs(four_years))

        self.assertEqual(
            table["overflow"], "Too many months to show here. Try subtotaling by quarter instead."
        )

    def test_a_range_that_fits_has_no_notice(self):
        for build in (report_tables.spending_table, report_tables.net_worth_table):
            with self.subTest(report=build.__name__):
                self.assertIsNone(build(self.inputs())["overflow"])


class WatchedExpensesWithNothingSpentTests(ReportTablesTestCase):
    def test_a_chosen_category_with_no_spending_in_the_range_is_an_empty_table_not_none(self):
        """Rent is paid in March, so January has none: the report still
        exists (categories are chosen), it just has no rows."""
        self.choose_watched(self.rent)

        table = report_tables.watched_expenses_table(
            self.inputs({**JANUARY_TO_MARCH, "end": "31/01/2026"})
        )

        self.assertIsNotNone(table)
        self.assertEqual(table["groups"], [])
        self.assertIsNone(table["totals"])
        self.assertEqual(table["periods"], ["Jan 2026"])


class PageAndExportShareTheirTablesTests(ReportTablesTestCase):
    """S14: each report's table is built once, by one function, and the
    page and its export both call it."""

    TABLE_KEYS = {
        "spending": "spending_table",
        "spending-vs-average": "spending_vs_average_table",
        "income": "income_table",
        "income-vs-average": "income_vs_average_table",
        "income-expense": "income_expense_table",
        "income-expense-vs-average": "income_expense_vs_average_table",
        "net-worth": "net_worth_table",
        "watched-expenses": "watched_table",
    }

    def test_every_export_has_a_table_on_the_page(self):
        self.assertEqual(set(self.TABLE_KEYS), set(REPORT_EXPORTS))

    def test_each_export_builds_the_table_the_page_shows(self):
        self.choose_watched(self.groceries, self.rent)
        page = self.client.get(reverse("finance:reports"), JANUARY_TO_MARCH)
        inputs = self.inputs()

        for slug, key in self.TABLE_KEYS.items():
            with self.subTest(report=slug):
                self.assertEqual(REPORT_EXPORTS[slug].build(inputs), page.context[key])

    def test_neither_has_a_watched_table_when_nothing_is_chosen(self):
        page = self.client.get(reverse("finance:reports"), JANUARY_TO_MARCH)

        self.assertIsNone(page.context["watched_table"])
        self.assertIsNone(REPORT_EXPORTS["watched-expenses"].build(self.inputs()))

    def test_a_page_load_works_out_each_shared_sum_once(self):
        """Spending by category and Income by category each feed two
        cards, and the vs-average totals feed a third, the table: a card
        that asked for its own copy would still show the right numbers,
        only after asking the database again."""
        by_category = mock.patch.object(
            report_tables, "category_totals_top_level", wraps=reporting.category_totals_top_level
        )
        vs_average = reporting.income_and_spending_vs_average
        # reporting.income_and_spending_vs_average() is also what the
        # table would call itself when handed no totals.
        vs_average_here = mock.patch.object(
            report_tables, "income_and_spending_vs_average", wraps=vs_average
        )
        vs_average_there = mock.patch.object(
            reporting, "income_and_spending_vs_average", wraps=vs_average
        )

        with by_category as top_level, vs_average_here as here, vs_average_there as there:
            self.client.get(reverse("finance:reports"), JANUARY_TO_MARCH)

        self.assertEqual(top_level.call_count, 2)  # the expense side and the income side
        self.assertEqual(here.call_count + there.call_count, 1)
