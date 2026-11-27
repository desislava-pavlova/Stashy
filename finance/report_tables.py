"""One builder per report's table, shared by the Reports page and the
report exports, so a downloaded file always holds the rows on screen.

A builder takes a `ReportInputs` and returns the table, or None when
there is nothing to show (Watched expenses with no categories chosen).
The page adds each report's chart data on top, in `views/reports.py`;
the exports lay the same table out in `views/exports.py`. The maths is
in `reporting.py`: nothing here adds an amount up on its own, and
nothing here touches a request or a template.
"""

from functools import cached_property

from .reporting import (
    category_report_rows,
    category_totals_top_level,
    category_vs_average_report_rows,
    income_and_spending,
    income_and_spending_vs_average,
    income_expense_vs_average_report_rows,
    net_worth_by_account,
    watched_expenses_category_ids,
)


class ReportInputs:
    """What every report is built from: the filters `report_filters()`
    read, the currency table and the stash's profile.

    The few sums that two reports share are properties: each works
    itself out the first time it is asked for and is remembered, so one
    page load asks the database once, however many reports use it.
    """

    def __init__(self, filters, rates, profile):
        self.filters = filters
        self.rates = rates
        self.profile = profile
        self.main_currency = profile.main_currency
        self.start = filters["start"]
        self.end = filters["end"]
        self.subtotal = filters["subtotal"]
        self.account = filters["account"]
        # The (start, end) windows before the range that the vs-average
        # reports compare with, not the columns of a subtotaled table.
        self.earlier_periods = filters["periods"]

    @property
    def granularity(self):
        """The columns of a report that always has some (Net worth, the
        Watched expenses chart): the Subtotal filter, or month when none
        is chosen."""
        return self.subtotal or "month"

    @cached_property
    def spending_by_category(self):
        """Spending per main category: Spending by category's chart, and
        the Income & expense vs average chart's expense side."""
        return category_totals_top_level(
            self.start, self.end, expense_only=True, account=self.account, rates=self.rates
        )

    @cached_property
    def income_by_category(self):
        """Income per main category: Income by category's chart, and the
        Income & expense vs average chart's income side."""
        return category_totals_top_level(
            self.start, self.end, income_only=True, account=self.account, rates=self.rates
        )

    @cached_property
    def range_totals(self):
        """The range's income and spending, for Income & expense."""
        return income_and_spending(self.start, self.end, account=self.account, rates=self.rates)

    @cached_property
    def vs_average_totals(self):
        """Income, spending, net and savings rate against their
        averages: the stat row and the summary rows of Income & expense
        vs average."""
        return income_and_spending_vs_average(
            self.start, self.end, self.earlier_periods, account=self.account, rates=self.rates
        )

    @cached_property
    def watched_category_ids(self):
        return watched_expenses_category_ids(self.profile)


def _category_table(inputs, category_ids=None, **kind):
    """Category rows for the range; `kind` is expense_only=True,
    income_only=True, or nothing for income and expense together.
    `category_ids` keeps only those categories (Watched expenses)."""
    return category_report_rows(
        inputs.start,
        inputs.end,
        subtotal=inputs.subtotal,
        account=inputs.account,
        category_ids=category_ids,
        rates=inputs.rates,
        **kind,
    )


def _vs_average_table(inputs, **kind):
    """Current against average per category; `kind` is expense_only=True
    or income_only=True (income and expense together is
    `income_expense_vs_average_table()`)."""
    return category_vs_average_report_rows(
        inputs.start,
        inputs.end,
        inputs.earlier_periods,
        account=inputs.account,
        rates=inputs.rates,
        **kind,
    )


def spending_table(inputs):
    return _category_table(inputs, expense_only=True)


def spending_vs_average_table(inputs):
    return _vs_average_table(inputs, expense_only=True)


def income_table(inputs):
    return _category_table(inputs, income_only=True)


def income_vs_average_table(inputs):
    return _vs_average_table(inputs, income_only=True)


def income_expense_table(inputs):
    return _category_table(inputs)


def income_expense_vs_average_table(inputs):
    """The category rows plus the Summary rows (Income total, Expense
    total)."""
    return income_expense_vs_average_report_rows(
        inputs.start,
        inputs.end,
        inputs.earlier_periods,
        account=inputs.account,
        rates=inputs.rates,
        vs_average_totals=inputs.vs_average_totals,
    )


def net_worth_table(inputs):
    return net_worth_by_account(inputs.start, inputs.end, inputs.granularity, rates=inputs.rates)


def watched_expenses_table(inputs):
    """None when no category is chosen in Settings."""
    category_ids = inputs.watched_category_ids
    if not category_ids:
        return None

    return _category_table(inputs, category_ids=category_ids, expense_only=True)
