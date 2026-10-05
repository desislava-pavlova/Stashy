from django.shortcuts import render

from ..currency import RateTable, format_money
from ..forms import DateRangeFilterForm
from ..models import Account
from ..reporting import (
    DEFAULT_LOOKBACK_PERIODS,
    DEFAULT_RANGE_KEY,
    category_report_rows,
    category_totals_top_level,
    category_totals_vs_average,
    category_vs_average_report_rows,
    income_and_spending,
    income_and_spending_vs_average,
    net_worth_by_account,
    resolve_range,
    savings_rate,
    subtotal_overflow_message,
    trimmed_average_periods,
    uncategorized_count,
    watched_expenses_category_ids,
    watched_expenses_series,
    watched_expenses_totals,
)


def _report_filters(request):
    """Shared by the reports page and the report export view, so both read the exact same
    filters off the querystring - what you export always matches what's on screen.

    On an invalid submission, `form` is swapped for a fresh, unbound one so the widgets show
    the defaults actually being used rather than echoing back the rejected values; the original
    errors are kept separately in "form_errors" for the page's warning banner."""
    submitted = DateRangeFilterForm(request.GET or None)
    invalid = bool(request.GET) and not submitted.is_valid()
    filters = submitted.cleaned_data if submitted.is_valid() else {}
    account = filters.get("account")
    start, end, interval = resolve_range(filters, account=account)
    # "Average interval" is how many periods the same length as [start, end] to average over,
    # immediately preceding it - see reporting.vs_average_periods().
    try:
        lookback = int(interval)
    except (TypeError, ValueError):
        lookback = DEFAULT_LOOKBACK_PERIODS
    range_key = filters.get("range") or DEFAULT_RANGE_KEY
    periods = trimmed_average_periods(start, end, lookback, account=account, range_key=range_key)
    return {
        "form": DateRangeFilterForm() if invalid else submitted,
        "form_errors": submitted.errors if invalid else None,
        "start": start,
        "end": end,
        "lookback": lookback,
        "periods": periods,
        "average_periods": len(periods),
        # One earlier period isn't an average, and none at all is nothing to compare with -
        # the vs-average reports say so instead of drawing one.
        "has_average": len(periods) >= 2,
        "subtotal": filters.get("subtotal") or "",
        "account": account,
    }


def _float_rows(rows, *decimal_keys):
    """Copies a list of row dicts with the given Decimal keys converted to float, for the
    JSON handed to Chart.js - keeps the original Decimal rows intact for the server-rendered
    table (money stays Decimal for every calculation; float only at the JSON boundary)."""
    return [{**row, **{key: float(row[key]) for key in decimal_keys}} for row in rows]


def _stacked_series(periods, rows):
    """Transpose category_report_rows()' per-row-per-period shape into per-period lists of
    {name, value} - top-level categories only, magnitudes only (a stack's height is never
    negative) - what the split-by-category column charts (reports 1/2 with a subtotal, and 5)
    draw from."""
    labels = periods or ["Total"]
    return [
        {
            "period": labels[i],
            "categories": [
                {"name": r["name"], "value": abs(float(r["values"][i]))}
                for r in rows
                if not r["is_child"] and abs(r["values"][i]) > 0
            ],
        }
        for i in range(len(labels))
    ]


def _top_level_rows(table, group_key):
    group = next((g for g in table["groups"] if g["key"] == group_key), None)
    return group["rows"] if group else []


def _vs_average_chart_rows(rows):
    """Like _float_rows(), but "average" may legitimately be None (no earlier data) - Chart.js
    reads a JSON null as a gap in the marker line rather than crashing on it."""
    return [
        {
            "name": r["name"],
            "current": float(r["current"]),
            "average": None if r["average"] is None else float(r["average"]),
        }
        for r in rows
    ]


def _vs_average_stat(label, current, average, higher_is_better, formatter):
    """current / average / difference for report 6's stat row, with a ▲▼ arrow that reflects
    the actual direction of change and a "good"/"bad" color that depends on the metric
    ("green means good, so spending less is green" - a falling Expense is good, a falling
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


def reports_view(request):
    filters = _report_filters(request)
    start, end, periods, subtotal, account = (
        filters["start"],
        filters["end"],
        filters["periods"],
        filters["subtotal"],
        filters["account"],
    )

    profile = request.stashy_profile
    main_currency = profile.main_currency
    rates = RateTable(main_currency)
    # A once-per-page-load warning - not per report, since the same missing rate would
    # otherwise repeat itself on every card that happens to touch that currency.
    missing_currencies = sorted(
        {a.currency for a in Account.objects.exclude(currency=main_currency) if rates.known(a.currency) is None}
    )

    uncategorized = uncategorized_count(start, end, account=account)

    # --- 1. Spending by category (chart: main categories only) ---
    spending_by_category = category_totals_top_level(start, end, expense_only=True, account=account, rates=rates)
    spending_table = category_report_rows(
        start, end, subtotal=subtotal, expense_only=True, account=account, rates=rates
    )

    # --- 2. Income by category (mirrors Spending) ---
    income_by_category = category_totals_top_level(start, end, income_only=True, account=account, rates=rates)
    income_table = category_report_rows(start, end, subtotal=subtotal, income_only=True, account=account, rates=rates)

    # --- 3. Spending vs. average / 4. Income vs. average - charts show main categories
    # only, tables keep subcategories ---
    spending_vs_average = category_totals_vs_average(
        start, end, periods, expense_only=True, account=account, rates=rates, top_level=True
    )
    spending_vs_average_table = category_vs_average_report_rows(
        start, end, periods, expense_only=True, account=account, rates=rates
    )
    income_vs_average = category_totals_vs_average(
        start, end, periods, income_only=True, account=account, rates=rates, top_level=True
    )
    income_vs_average_table = category_vs_average_report_rows(
        start, end, periods, income_only=True, account=account, rates=rates
    )

    # --- 5. Income & expense by category - combined signed table; the chart is a stacked
    # column per Subtotal period, split by category ---
    income_expense_table = category_report_rows(start, end, subtotal=subtotal, account=account, rates=rates)
    totals = income_and_spending(start, end, account=account, rates=rates)
    savings_rate_value = savings_rate(totals["income"], totals["spending"])
    net_total = totals["income"] - totals["spending"]
    income_expense_income_series = _stacked_series(
        income_expense_table["periods"], _top_level_rows(income_expense_table, "income")
    )
    income_expense_expense_series = _stacked_series(
        income_expense_table["periods"], _top_level_rows(income_expense_table, "expense")
    )

    # --- 6. Income & expense vs. average: Income/Expense (split by category, current period
    # only) + Net, each with an average marker ---
    vs_average_totals = income_and_spending_vs_average(start, end, periods, account=account, rates=rates)

    def money_fmt(value):
        return format_money(value, main_currency)

    def percent_fmt(value):
        return f"{value}%"

    vs_average_stats = [
        _vs_average_stat(
            "Income", vs_average_totals["current_income"], vs_average_totals["average_income"], True, money_fmt
        ),
        _vs_average_stat(
            "Expense", vs_average_totals["current_spending"], vs_average_totals["average_spending"], False, money_fmt
        ),
        _vs_average_stat("Net", vs_average_totals["current_net"], vs_average_totals["average_net"], True, money_fmt),
        _vs_average_stat(
            "Savings rate", vs_average_totals["current_rate"], vs_average_totals["average_rate"], True, percent_fmt
        ),
    ]
    # The table keeps the same per-category breakdown as report 5's, plus Income total/Expense
    # total/Net summary rows (already computed above) so the whole picture is in one table.
    income_expense_vs_average_table = category_vs_average_report_rows(
        start, end, periods, account=account, rates=rates
    )
    income_expense_vs_average_table["groups"] = income_expense_vs_average_table["groups"] + [
        {
            "key": "summary",
            "name": "Summary",
            "rows": [
                {
                    "id": "summary_income",
                    "parent_id": None,
                    "name": "Income total",
                    "is_child": False,
                    "has_children": False,
                    "current": vs_average_totals["current_income"],
                    "average": vs_average_totals["average_income"],
                    "difference": None
                    if vs_average_totals["average_income"] is None
                    else vs_average_totals["current_income"] - vs_average_totals["average_income"],
                },
                {
                    "id": "summary_expense",
                    "parent_id": None,
                    "name": "Expense total",
                    "is_child": False,
                    "has_children": False,
                    "lower_is_good": True,
                    "current": vs_average_totals["current_spending"],
                    "average": vs_average_totals["average_spending"],
                    "difference": None
                    if vs_average_totals["average_spending"] is None
                    else vs_average_totals["current_spending"] - vs_average_totals["average_spending"],
                },
            ],
        }
    ]

    # --- 7. Net worth over time - the chart plots the table's own Total row, so both follow
    # the Subtotal filter (month when none is chosen) ---
    net_worth_table = net_worth_by_account(start, end, subtotal or "month", rates=rates)

    # --- 8. Watched expenses (chosen in Settings) ---
    watched_category_ids = watched_expenses_category_ids(profile)
    watched_totals = watched_expenses_totals(start, end, watched_category_ids, account=account, rates=rates)
    watched_table = None
    watched_series = []
    watched_chart_overflow = None
    if watched_category_ids:
        watched_table = category_report_rows(
            start,
            end,
            subtotal=subtotal,
            expense_only=True,
            account=account,
            rates=rates,
            category_ids=watched_category_ids,
        )
        watched_chart_overflow = subtotal_overflow_message(start, end, subtotal or "month")
        if not watched_chart_overflow:
            watched_series = watched_expenses_series(
                start,
                end,
                watched_category_ids,
                granularity=subtotal or "month",
                account=account,
                rates=rates,
            )

    context = {
        "form": filters["form"],
        "form_errors": filters["form_errors"],
        "start": start,
        "end": end,
        "subtotal": subtotal,
        "main_currency": main_currency,
        "missing_currencies": missing_currencies,
        "uncategorized_count": uncategorized,
        "average_periods": filters["average_periods"],
        "has_average": filters["has_average"],
        "spending_by_category": spending_by_category,
        "spending_chart": _float_rows(spending_by_category, "total"),
        "spending_stacked_chart": _stacked_series(
            spending_table["periods"], [r for g in spending_table["groups"] for r in g["rows"]]
        ),
        "spending_table": spending_table,
        "income_by_category": income_by_category,
        "income_chart": _float_rows(income_by_category, "total"),
        "income_stacked_chart": _stacked_series(
            income_table["periods"], [r for g in income_table["groups"] for r in g["rows"]]
        ),
        "income_table": income_table,
        "spending_vs_average": spending_vs_average,
        "spending_vs_average_chart": _vs_average_chart_rows(spending_vs_average),
        "spending_vs_average_table": spending_vs_average_table,
        "income_vs_average": income_vs_average,
        "income_vs_average_chart": _vs_average_chart_rows(income_vs_average),
        "income_vs_average_table": income_vs_average_table,
        "income_expense_table": income_expense_table,
        "income_expense_chart": {
            "periods": income_expense_table["periods"] or ["Total"],
            "income": income_expense_income_series,
            "expense": income_expense_expense_series,
        },
        "income_total": totals["income"],
        "expense_total": totals["spending"],
        "net_total": net_total,
        "savings_rate": savings_rate_value,
        "vs_average_totals": vs_average_totals,
        "vs_average_stats": vs_average_stats,
        "income_expense_vs_average_table": income_expense_vs_average_table,
        "income_expense_vs_average_chart": {
            "income": _float_rows(income_by_category, "total"),
            "expense": _float_rows(spending_by_category, "total"),
            "current_income": float(vs_average_totals["current_income"]),
            "current_expense": float(vs_average_totals["current_spending"]),
            "current_net": float(vs_average_totals["current_net"]),
            "average_income": None
            if vs_average_totals["average_income"] is None
            else float(vs_average_totals["average_income"]),
            "average_expense": None
            if vs_average_totals["average_spending"] is None
            else float(vs_average_totals["average_spending"]),
            "average_net": None
            if vs_average_totals["average_net"] is None
            else float(vs_average_totals["average_net"]),
        },
        "net_worth_chart": [
            {"label": label, "total": float(total)}
            for label, total in zip(net_worth_table["periods"], net_worth_table["totals"], strict=True)
        ],
        "net_worth_table": net_worth_table,
        "watched_expenses_title": profile.watched_expenses_title,
        # Whether categories are *chosen* - not whether any were spent in this range, which is a
        # different question (an empty range still shows the chart and table, just at zero).
        "watched_has_categories": bool(watched_category_ids),
        "watched_totals": watched_totals,
        "watched_table": watched_table,
        "watched_chart_overflow": watched_chart_overflow,
        "watched_chart": [
            {
                "category_id": series["category_id"],
                "name": series["name"],
                "points": [{"period": p["period"], "total": float(p["total"])} for p in series["points"]],
            }
            for series in watched_series
        ],
    }
    return render(request, "finance/reports.html", context)
