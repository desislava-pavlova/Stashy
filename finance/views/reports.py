"""The Reports page: it reads the filters off the address, asks one
card function per report for that report's numbers and chart data, and
hands them to the template.

The tables come from `report_tables.py` and the filters from
`report_filters()` in `filters.py`; the exports use both too, so a
downloaded file always matches what is on screen. No report maths
belongs here: a card only shapes what `reporting.py` worked out (floats
for Chart.js, an arrow and a colour for the stat row).
"""

from django.shortcuts import render

from .. import regional
from ..currency import format_money
from ..report_tables import (
    income_expense_table,
    income_expense_vs_average_table,
    income_table,
    income_vs_average_table,
    net_worth_table,
    spending_table,
    spending_vs_average_table,
    watched_expenses_table,
)
from ..reporting import (
    category_totals_vs_average,
    currencies_without_a_rate,
    savings_rate,
    subtotal_overflow_message,
    uncategorized_count,
    watched_expenses_series,
    watched_expenses_totals,
)
from ._helpers import report_inputs


def _float_or_none(value):
    """A Decimal as a float for Chart.js; an average that doesn't exist
    (None) stays None, which Chart.js reads as a gap in the marker line
    rather than crashing on it."""
    return None if value is None else float(value)


def _float_rows(rows, *decimal_keys):
    """Copies a list of row dicts with the given Decimal keys converted
    to float, for the JSON handed to Chart.js - keeps the original
    Decimal rows intact for the server-rendered table (money stays
    Decimal for every calculation; float only at the JSON boundary)."""
    return [{**row, **{key: float(row[key]) for key in decimal_keys}} for row in rows]


def _all_rows(table):
    """Every row of a category table, the groups run together."""
    return [row for group in table["groups"] for row in group["rows"]]


def _stacked_series(periods, rows, spending_is_negative=False):
    """Transpose category_report_rows()' per-row-per-period shape into
    per-period lists of {name, value} - top-level categories only - what
    the split-by-category column charts (reports 1/2 with a subtotal,
    and 5) draw from.

    Values keep their meaning: spending reads positive, and a category
    that netted to a refund in a period reads negative, so Chart.js
    stacks it below the axis instead of passing it off as spending.
    `spending_is_negative` is for report 5's expense rows, which come
    from the signed table (spending stored negative) and are flipped
    here."""
    labels = periods or ["Total"]
    sign = -1 if spending_is_negative else 1
    return [
        {
            "period": labels[i],
            "categories": [
                {"name": r["name"], "value": sign * float(r["values"][i])}
                for r in rows
                if not r["is_child"] and r["values"][i] != 0
            ],
        }
        for i in range(len(labels))
    ]


def _top_level_rows(table, group_key):
    """The rows of one group ("income" or "expense") of a category
    table, or none when that group has no rows."""
    group = next((g for g in table["groups"] if g["key"] == group_key), None)
    return group["rows"] if group else []


def _vs_average_chart_rows(rows):
    """Like _float_rows(), but "average" may legitimately be None (no
    earlier data)."""
    return [
        {
            "name": r["name"],
            "current": float(r["current"]),
            "average": _float_or_none(r["average"]),
        }
        for r in rows
    ]


def _vs_average_stat(label, current, average, higher_is_better, formatter):
    """current / average / difference for report 6's stat row, with a ▲▼
    arrow that reflects the actual direction of change and a
    "good"/"bad" color that depends on the metric ("green means good, so
    spending less is green" - a falling Expense is good, a falling
    Income isn't)."""
    if current is None or average is None:
        return {
            "label": label,
            "current": formatter(current) if current is not None else "—",
            "average": "—",
            "diff_text": "",
            "good": None,
        }

    diff = current - average
    # The arrow already says which way it moved, so the amount beside it
    # is a plain magnitude.
    if diff == 0:
        return {
            "label": label,
            "current": formatter(current),
            "average": formatter(average),
            "diff_text": formatter(diff),
            "good": None,
        }

    arrow = "▲" if diff > 0 else "▼"
    good = (diff > 0) == higher_is_better
    return {
        "label": label,
        "current": formatter(current),
        "average": formatter(average),
        "diff_text": f"{arrow} {formatter(abs(diff))}",
        "good": good,
    }


def _vs_average_stats(averages, main_currency):
    """Report 6's stat row: Income, Expense, Net and Savings rate, each
    against its average."""

    def money_fmt(value):
        return format_money(value, main_currency)

    def percent_fmt(value):
        return f"{regional.format_number(value, decimals=1)}%"

    return [
        _vs_average_stat(
            "Income", averages["current_income"], averages["average_income"], True, money_fmt
        ),
        _vs_average_stat(
            "Expense", averages["current_spending"], averages["average_spending"], False, money_fmt
        ),
        _vs_average_stat("Net", averages["current_net"], averages["average_net"], True, money_fmt),
        _vs_average_stat(
            "Savings rate", averages["current_rate"], averages["average_rate"], True, percent_fmt
        ),
    ]


def _income_expense_vs_average_chart(inputs):
    """Report 6's chart: Income and Expense split by category (the
    current period only) and Net, each with an average marker."""
    averages = inputs.vs_average_totals
    return {
        "income": _float_rows(inputs.income_by_category, "total"),
        "expense": _float_rows(inputs.spending_by_category, "total"),
        "current_income": float(averages["current_income"]),
        "current_expense": float(averages["current_spending"]),
        "current_net": float(averages["current_net"]),
        "average_income": _float_or_none(averages["average_income"]),
        "average_expense": _float_or_none(averages["average_spending"]),
        "average_net": _float_or_none(averages["average_net"]),
    }


def _net_worth_chart(table):
    """The chart plots the table's own Total row, so both follow the
    Subtotal filter."""
    return [
        {"label": label, "total": float(total)}
        for label, total in zip(table["periods"], table["totals"], strict=True)
    ]


def _watched_chart(series_list):
    return [
        {
            "category_id": series["category_id"],
            "name": series["name"],
            "points": [
                {"period": p["period"], "total": float(p["total"])} for p in series["points"]
            ],
        }
        for series in series_list
    ]


# ----------------------------------------------------------------------
# One card per report. Each takes the page's ReportInputs and returns
# the context keys that report's card in the template reads.
# ----------------------------------------------------------------------


def _spending_card(inputs):
    by_category = inputs.spending_by_category
    table = spending_table(inputs)
    return {
        "spending_by_category": by_category,
        "spending_chart": _float_rows(by_category, "total"),
        "spending_stacked_chart": _stacked_series(table["periods"], _all_rows(table)),
        "spending_table": table,
    }


def _income_card(inputs):
    by_category = inputs.income_by_category
    table = income_table(inputs)
    return {
        "income_by_category": by_category,
        "income_chart": _float_rows(by_category, "total"),
        "income_stacked_chart": _stacked_series(table["periods"], _all_rows(table)),
        "income_table": table,
    }


def _spending_vs_average_card(inputs):
    # The chart shows main categories only; the table keeps
    # subcategories.
    by_category = category_totals_vs_average(
        inputs.start,
        inputs.end,
        inputs.earlier_periods,
        expense_only=True,
        account=inputs.account,
        rates=inputs.rates,
        top_level=True,
    )
    return {
        "spending_vs_average": by_category,
        "spending_vs_average_chart": _vs_average_chart_rows(by_category),
        "spending_vs_average_table": spending_vs_average_table(inputs),
    }


def _income_vs_average_card(inputs):
    by_category = category_totals_vs_average(
        inputs.start,
        inputs.end,
        inputs.earlier_periods,
        income_only=True,
        account=inputs.account,
        rates=inputs.rates,
        top_level=True,
    )
    return {
        "income_vs_average": by_category,
        "income_vs_average_chart": _vs_average_chart_rows(by_category),
        "income_vs_average_table": income_vs_average_table(inputs),
    }


def _income_expense_card(inputs):
    # One combined signed table; the chart is a stacked column per
    # Subtotal period, split by category.
    table = income_expense_table(inputs)
    totals = inputs.range_totals
    income_series = _stacked_series(table["periods"], _top_level_rows(table, "income"))
    expense_series = _stacked_series(
        table["periods"], _top_level_rows(table, "expense"), spending_is_negative=True
    )
    return {
        "income_expense_table": table,
        "income_expense_chart": {
            "periods": table["periods"] or ["Total"],
            "income": income_series,
            "expense": expense_series,
        },
        "income_total": totals["income"],
        "expense_total": totals["spending"],
        "net_total": totals["income"] - totals["spending"],
        "savings_rate": savings_rate(totals["income"], totals["spending"]),
    }


def _income_expense_vs_average_card(inputs):
    averages = inputs.vs_average_totals
    return {
        "vs_average_totals": averages,
        "vs_average_stats": _vs_average_stats(averages, inputs.main_currency),
        "income_expense_vs_average_table": income_expense_vs_average_table(inputs),
        "income_expense_vs_average_chart": _income_expense_vs_average_chart(inputs),
    }


def _net_worth_card(inputs):
    table = net_worth_table(inputs)
    return {"net_worth_table": table, "net_worth_chart": _net_worth_chart(table)}


def _watched_chart_series(inputs):
    """(the notice shown instead of the chart, the chart's lines): a
    range with too many columns gets the notice and no lines, and
    nothing chosen gets neither."""
    category_ids = inputs.watched_category_ids
    if not category_ids:
        return None, []

    overflow = subtotal_overflow_message(inputs.start, inputs.end, inputs.granularity)
    if overflow:
        return overflow, []

    series = watched_expenses_series(
        inputs.start,
        inputs.end,
        category_ids,
        granularity=inputs.granularity,
        account=inputs.account,
        rates=inputs.rates,
    )
    return None, series


def _watched_expenses_card(inputs):
    category_ids = inputs.watched_category_ids
    chart_overflow, series = _watched_chart_series(inputs)
    totals = watched_expenses_totals(
        inputs.start, inputs.end, category_ids, account=inputs.account, rates=inputs.rates
    )
    return {
        "watched_expenses_title": inputs.profile.watched_expenses_title,
        # Whether categories are *chosen* - not whether any were spent
        # in this range, which is a different question (an empty range
        # still shows the chart and table, just at zero).
        "watched_has_categories": bool(category_ids),
        "watched_totals": totals,
        "watched_table": watched_expenses_table(inputs),
        "watched_chart_overflow": chart_overflow,
        "watched_chart": _watched_chart(series),
    }


REPORT_CARDS = [
    _spending_card,
    _income_card,
    _spending_vs_average_card,
    _income_vs_average_card,
    _income_expense_card,
    _income_expense_vs_average_card,
    _net_worth_card,
    _watched_expenses_card,
]


def _page_keys(inputs):
    """The context keys that belong to the page, not to one report: the
    filter form and its range, and the two notices that appear once
    above the cards (a missing rate, transactions with no category)."""
    filters = inputs.filters
    missing_currencies = currencies_without_a_rate(inputs.rates)
    uncategorized = uncategorized_count(inputs.start, inputs.end, account=inputs.account)
    return {
        "form": filters["form"],
        "form_errors": filters["form_errors"],
        "start": inputs.start,
        "end": inputs.end,
        "subtotal": inputs.subtotal,
        "main_currency": inputs.main_currency,
        "missing_currencies": missing_currencies,
        "uncategorized_count": uncategorized,
        "average_periods": filters["average_periods"],
        "has_average": filters["has_average"],
    }


def reports_view(request):
    inputs = report_inputs(request)

    context = _page_keys(inputs)
    for card in REPORT_CARDS:
        context.update(card(inputs))

    return render(request, "finance/reports.html", context)
