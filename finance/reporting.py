"""Helpers for turning a DateRangeFilterForm's cleaned data into a concrete
(start, end, interval) tuple, and for bucketing/aggregating transactions for the Reports page.

Phase 13 (15/09/2026) rule, everywhere in this file: a category's own type decides income vs.
spending, never the sign of its amount (R1) - an expense category that nets positive (a refund
exceeding spend) just nets against its own total, no special handling. Every total also passes
through an optional `rates` (a currency.RateTable) so multi-currency accounts convert into the
main currency the same way net worth already does (D2: latest known rate, not historical).
"""

from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db.models import Sum

from .models import Account, Category, Transaction, grouped_accounts, grouped_categories
from .money import ZERO, to_cent

# Default depth for "Average interval": how many periods of the current date range's own
# length to average over, immediately preceding it (see vs_average_periods() below). R3
# (Phase 13): defaults are This month - no subtotal - Last 3 periods - All accounts.
DEFAULT_LOOKBACK_PERIODS = 3
DEFAULT_RANGE_KEY = "this_month"

EARLIEST_RANGE_KEY = "earliest"

# R5: past this many subtotal columns, a table/chart shows a short notice instead of trying
# to render them all - 36 is roomy for anything anyone would actually want to read (3 years of
# months, 9 years of quarters, 18 years of half-years) without ever being a real limit in
# practice for normal use.
SUBTOTAL_LIMIT = 36
_SUBTOTAL_NEXT_STEP = {"week": "month", "month": "quarter", "quarter": "half_year", "half_year": "year"}
_SUBTOTAL_PLURAL = {
    "week": "weeks",
    "month": "months",
    "quarter": "quarters",
    "half_year": "half-years",
    "year": "years",
}
_SUBTOTAL_SINGULAR = {"month": "month", "quarter": "quarter", "half_year": "half-year", "year": "year"}


def resolve_range(cleaned_data, account=None):
    today = date.today()
    range_key = cleaned_data.get("range") or DEFAULT_RANGE_KEY
    start = cleaned_data.get("start")
    end = cleaned_data.get("end")

    if range_key == "custom" and start and end:
        return start, end, cleaned_data.get("interval") or str(DEFAULT_LOOKBACK_PERIODS)

    if range_key == EARLIEST_RANGE_KEY:
        start = earliest_transaction_date(account=account) or today
        end = today
    elif range_key == "this_month":
        start = today.replace(day=1)
        end = today
    elif range_key == "last_month":
        first_this_month = today.replace(day=1)
        end = first_this_month - relativedelta(days=1)
        start = end.replace(day=1)
    elif range_key == "this_quarter":
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        start = today.replace(month=q_start_month, day=1)
        end = today
    elif range_key == "ytd":
        start = today.replace(month=1, day=1)
        end = today
    elif range_key == "this_year":
        start = today.replace(month=1, day=1)
        end = today.replace(month=12, day=31)
    elif range_key == "last_year":
        start = today.replace(month=1, day=1, year=today.year - 1)
        end = today.replace(month=12, day=31, year=today.year - 1)
    else:  # last_12_months, default
        start = (today - relativedelta(months=11)).replace(day=1)
        end = today

    return start, end, cleaned_data.get("interval") or str(DEFAULT_LOOKBACK_PERIODS)


def earliest_transaction_date(account=None):
    """The date of the very first transaction (within the Account filter), or None with no
    transactions at all - "Earliest to date" starts here (R3), and it's also where "periods
    before the data begins don't count" (R4) trims the vs-average lookback."""
    qs = Transaction.objects.all()
    if account:
        qs = qs.filter(account=account)
    row = qs.order_by("date").first()
    return row.date if row else None


def excluding_transfers(queryset):
    """Transfer-type categories (Phase 3) never count as income or spending (D7/D11)."""
    return queryset.exclude(category__account__isnull=False)


def _convert(amount, currency, rates):
    """`amount` (in `currency`) converted into `rates`'s target currency, cents-rounded.

    `rates=None` means "don't convert" (single-currency setups, and every existing caller/test
    that predates Phase 13) - amounts are trusted to already be comparable. With rates given but
    no known rate for `currency`, the amount is left out (0) rather than silently mixed into a
    total in the wrong currency; reports_view() separately warns once when this happens (missing
    a rate is rare and worth a banner, not a per-row asterisk)."""
    if rates is None or currency == rates.target:
        return amount
    converted = rates.convert(amount, currency)
    return converted if converted is not None else ZERO


def income_and_spending(start, end, account=None, rates=None):
    qs = excluding_transfers(Transaction.objects.filter(date__gte=start, date__lte=end, category__isnull=False))
    if account:
        qs = qs.filter(account=account)
    income, spending = ZERO, ZERO
    for row in qs.values("amount", "category__category_type", "account__currency"):
        amount = _convert(row["amount"], row["account__currency"], rates)
        if row["category__category_type"] == Category.CategoryType.INCOME:
            income += amount
        elif row["category__category_type"] == Category.CategoryType.EXPENSE:
            spending += amount
    return {"income": to_cent(income), "spending": abs(to_cent(spending))}


def savings_rate(income, spending):
    """Percentage of income that wasn't spent; None when there was no income to save from."""
    if income <= 0:
        return None
    return ((income - spending) / income * 100).quantize(Decimal("0.1"))


_MONTH_RANGES = {"this_month", "last_month"}
_YEAR_RANGES = {"ytd", "this_year", "last_year"}


def vs_average_periods(start, end, lookback, range_key=None):
    """The `lookback` earlier periods a Date range is averaged against, most recent first (R4):
    whole earlier periods of the same kind for a calendar range - This/Last month against the
    previous full months, This quarter against earlier quarters, year to date/this year/last
    year against earlier full years, Last 12 months against earlier 12-month blocks. A custom
    range (or no `range_key`) uses earlier windows the same length as [start, end]; Earliest to
    date has nothing before it."""
    if range_key == EARLIEST_RANGE_KEY:
        return []
    if range_key in _MONTH_RANGES:
        step, anchor = relativedelta(months=1), start.replace(day=1)
    elif range_key == "this_quarter":
        step, anchor = relativedelta(months=3), start.replace(month=((start.month - 1) // 3) * 3 + 1, day=1)
    elif range_key in _YEAR_RANGES:
        step, anchor = relativedelta(years=1), start.replace(month=1, day=1)
    elif range_key == "last_12_months":
        step, anchor = relativedelta(months=12), start
    else:
        span_days = (end - start).days + 1
        periods = []
        cursor_end = start - timedelta(days=1)
        for _ in range(lookback):
            cursor_start = cursor_end - timedelta(days=span_days - 1)
            periods.append((cursor_start, cursor_end))
            cursor_end = cursor_start - timedelta(days=1)
        return periods
    return [(anchor - step * i, anchor - step * (i - 1) - timedelta(days=1)) for i in range(1, lookback + 1)]


def trimmed_average_periods(start, end, lookback, account=None, range_key=None):
    """`vs_average_periods()`, minus any period that ends before the account's first
    transaction (R4: "periods before the data begins don't count, whatever Average interval
    says") - computed once per page load and threaded into every vs-average function below, so
    they all agree on the same real period count."""
    earliest = earliest_transaction_date(account=account)
    periods = vs_average_periods(start, end, lookback, range_key=range_key)
    if not earliest:
        return periods
    return [p for p in periods if p[1] >= earliest]


def income_and_spending_vs_average(start, end, periods, account=None, rates=None):
    """This period's income/spending/savings-rate vs. the average of the given trailing
    `periods` (see trimmed_average_periods()) - the same comparison category_totals_vs_average()
    makes per category, but for the overall totals the Income & expense vs average card shows."""
    current = income_and_spending(start, end, account=account, rates=rates)
    current_net = current["income"] - current["spending"]
    result = {
        "current_income": current["income"],
        "current_spending": current["spending"],
        "current_net": current_net,
        "current_rate": savings_rate(current["income"], current["spending"]),
        "average_periods": len(periods),
    }
    if not periods:
        result.update(average_income=ZERO, average_spending=ZERO, average_net=ZERO, average_rate=None)
        return result

    incomes, spendings = [], []
    for period_start, period_end in periods:
        period_totals = income_and_spending(period_start, period_end, account=account, rates=rates)
        incomes.append(period_totals["income"])
        spendings.append(period_totals["spending"])
    average_income = to_cent(sum(incomes, ZERO) / len(periods))
    average_spending = to_cent(sum(spendings, ZERO) / len(periods))
    result.update(
        average_income=average_income,
        average_spending=average_spending,
        average_net=average_income - average_spending,
        average_rate=savings_rate(average_income, average_spending),
    )
    return result


def _category_type_filter(qs, expense_only, income_only):
    if expense_only:
        return qs.filter(category__category_type=Category.CategoryType.EXPENSE)
    if income_only:
        return qs.filter(category__category_type=Category.CategoryType.INCOME)
    return qs


def category_totals(start, end, expense_only=False, income_only=False, account=None, rates=None):
    """Per-category totals for the period, transfers excluded (R1: category type decides, not
    amount sign). expense_only/income_only return positive magnitudes sorted biggest-first (for
    the "by category" charts); otherwise totals stay signed (income positive, expense negative),
    sorted low-to-high, so the combined chart reads as one continuous scale."""
    qs = excluding_transfers(Transaction.objects.filter(date__gte=start, date__lte=end, category__isnull=False))
    qs = _category_type_filter(qs, expense_only, income_only)
    if account:
        qs = qs.filter(account=account)

    totals, names = {}, {}
    for row in qs.values("category_id", "category__name", "amount", "account__currency"):
        amount = _convert(row["amount"], row["account__currency"], rates)
        totals[row["category_id"]] = totals.get(row["category_id"], ZERO) + amount
        names[row["category_id"]] = row["category__name"]

    rows = [{"category_id": cid, "name": names[cid], "total": to_cent(total)} for cid, total in totals.items()]
    if expense_only or income_only:
        for r in rows:
            r["total"] = abs(r["total"])
        rows.sort(key=lambda r: r["total"], reverse=True)
    else:
        rows.sort(key=lambda r: r["total"])
    return rows


def category_totals_top_level(start, end, expense_only=False, income_only=False, account=None, rates=None):
    """Like category_totals(), but a subcategory's amount is rolled into its top-level parent's
    - for charts that read better as one bar per main category (R2: charts never show
    subcategories) than with every subcategory competing for space alongside them."""
    qs = excluding_transfers(Transaction.objects.filter(date__gte=start, date__lte=end, category__isnull=False))
    qs = _category_type_filter(qs, expense_only, income_only)
    if account:
        qs = qs.filter(account=account)

    raw = {}
    for row in qs.values("category_id", "amount", "account__currency"):
        amount = _convert(row["amount"], row["account__currency"], rates)
        raw[row["category_id"]] = raw.get(row["category_id"], ZERO) + amount
    if not raw:
        return []

    categories = {c.pk: c for c in Category.objects.filter(pk__in=raw.keys()).select_related("parent")}
    top_totals, top_names = {}, {}
    for category_id, amount in raw.items():
        category = categories[category_id]
        top = category.parent if category.parent_id else category
        top_totals[top.pk] = top_totals.get(top.pk, ZERO) + amount
        top_names[top.pk] = top.name

    rows = [{"category_id": cid, "name": top_names[cid], "total": to_cent(total)} for cid, total in top_totals.items()]
    if expense_only or income_only:
        for r in rows:
            r["total"] = abs(r["total"])
        rows.sort(key=lambda r: r["total"], reverse=True)
    else:
        rows.sort(key=lambda r: r["total"])
    return rows


def category_totals_vs_average(
    start, end, periods, expense_only=False, income_only=False, account=None, rates=None, top_level=False
):
    """Per category: [start, end]'s total vs. the average of the given trailing `periods` (see
    trimmed_average_periods()). Periods with no activity for a category count as zero (R4) - the
    average always divides by len(periods), not by how many of them happened to have a row for
    that category - but a category with no activity in *any* earlier period has no average at
    all (None), rather than a misleading 0. `top_level=True` rolls subcategories into their main
    category, for charts (R2)."""
    qs = excluding_transfers(Transaction.objects.filter(category__isnull=False))
    qs = _category_type_filter(qs, expense_only, income_only)
    if account:
        qs = qs.filter(account=account)

    top_of = {}
    if top_level:
        for category in Category.objects.select_related("parent"):
            top = category.parent if category.parent_id else category
            top_of[category.pk] = (top.pk, top.name)

    def totals_for(s, e):
        totals, names = {}, {}
        for row in qs.filter(date__gte=s, date__lte=e).values(
            "category_id", "category__name", "amount", "account__currency"
        ):
            amount = _convert(row["amount"], row["account__currency"], rates)
            cid, name = top_of.get(row["category_id"], (row["category_id"], row["category__name"]))
            totals[cid] = totals.get(cid, ZERO) + amount
            names[cid] = name
        return totals, names

    current_by_category, names = totals_for(start, end)
    period_totals_list = []
    for period_start, period_end in periods:
        period_totals, period_names = totals_for(period_start, period_end)
        for cid, name in period_names.items():
            names.setdefault(cid, name)
        period_totals_list.append(period_totals)

    all_ids = set(current_by_category)
    for pt in period_totals_list:
        all_ids |= set(pt)

    result = []
    for cid in all_ids:
        current = to_cent(current_by_category.get(cid, ZERO))
        seen_before = any(cid in pt for pt in period_totals_list)
        average = (
            to_cent(sum((pt.get(cid, ZERO) for pt in period_totals_list), ZERO) / len(periods))
            if seen_before
            else None
        )
        if expense_only or income_only:
            current = abs(current)
            if average is not None:
                average = abs(average)
        result.append({"category_id": cid, "name": names[cid], "current": current, "average": average})

    if expense_only or income_only:
        result.sort(key=lambda r: r["current"], reverse=True)
    else:
        result.sort(key=lambda r: r["current"])
    return result


def subtotal_overflow_message(start, end, granularity):
    """None when a subtotal-driven table/chart fits within SUBTOTAL_LIMIT columns; otherwise a
    short notice pointing at the next coarser granularity, in the app's own alert style, with no
    exact count in the text (R5)."""
    if not granularity:
        return None
    if len(_period_columns(start, end, granularity)) <= SUBTOTAL_LIMIT:
        return None
    next_step = _SUBTOTAL_NEXT_STEP.get(granularity)
    plural = _SUBTOTAL_PLURAL[granularity]
    if next_step:
        return f"Too many {plural} to show here. Try subtotaling by {_SUBTOTAL_SINGULAR[next_step]} instead."
    return "Too many years to show here. Try a shorter date range."


def period_bucket(d, granularity):
    """The (sort_key, label) of the period `d` falls into, at the given granularity. Computed
    in plain Python rather than a DB TruncXxx - "half-year" has no Django equivalent, and
    doing every granularity the same way keeps this and _period_columns() below in sync."""
    if granularity == "week":
        start = d - timedelta(days=d.weekday())
        return start, start.strftime("%d %b")
    if granularity == "quarter":
        q = (d.month - 1) // 3 + 1
        start = d.replace(month=(q - 1) * 3 + 1, day=1)
        return start, f"Q{q} {d.year}"
    if granularity == "half_year":
        half = 1 if d.month <= 6 else 2
        start = d.replace(month=1 if half == 1 else 7, day=1)
        return start, f"H{half} {d.year}"
    if granularity == "year":
        start = d.replace(month=1, day=1)
        return start, str(d.year)
    start = d.replace(day=1)  # month default
    return start, start.strftime("%b %Y")


def _period_columns(start, end, granularity):
    """Every period bucket touched by [start, end], in calendar order, each with the latest
    date within the range that falls in it (a full period's real end, or `end` itself for a
    bucket the range cuts off early) - the "as of" date Net worth needs per column."""
    buckets = {}
    d = start
    while d <= end:
        key, label = period_bucket(d, granularity)
        entry = buckets.setdefault(key, [label, d])
        entry[1] = d
        d += timedelta(days=1)
    ordered = sorted(buckets.items(), key=lambda item: item[0])
    return [{"key": key, "label": label, "end": last_date} for key, (label, last_date) in ordered]


def uncategorized_count(start, end, account=None):
    """How many transactions in the period have no category - excluded from every report (R1),
    since Category stays optional (some imports can't supply one)."""
    qs = Transaction.objects.filter(date__gte=start, date__lte=end, category__isnull=True)
    if account:
        qs = qs.filter(account=account)
    return qs.count()


def category_report_rows(
    start, end, subtotal="", expense_only=False, income_only=False, account=None, category_ids=None, rates=None
):
    """Category totals for the period, grouped and ordered like the category list page
    (grouped_categories(): Income group then Expense group, a parent immediately followed by
    its own subcategories) - a parent row is rolled up to include its subcategories' amounts
    on top of its own, matching how the category list page already treats a category as
    "itself and what's under it". When `subtotal` is a period granularity, each row gets one
    value per period bucket across the range plus a row-level total; otherwise each row just
    gets its one total for the whole range (as a single-element values list).

    Returns {"periods": [labels...], "groups": [{"name", "rows": [{"name", "is_child",
    "values", "total"}]}], "totals": {"values": [...], "total": Decimal} or None if empty,
    "overflow": a message instead of everything else when the subtotal has too many columns (R5)}.
    """
    overflow = subtotal_overflow_message(start, end, subtotal) if subtotal else None
    if overflow:
        return {"periods": [], "groups": [], "totals": None, "overflow": overflow}

    qs = excluding_transfers(Transaction.objects.filter(date__gte=start, date__lte=end, category__isnull=False))
    qs = _category_type_filter(qs, expense_only, income_only)
    if account:
        qs = qs.filter(account=account)
    if category_ids is not None:
        qs = qs.filter(category_id__in=category_ids)

    periods = _period_columns(start, end, subtotal) if subtotal else []
    period_index = {p["key"]: i for i, p in enumerate(periods)}
    width = len(periods) if subtotal else 1

    totals = {}
    for row in qs.values("category_id", "amount", "date", "account__currency"):
        values = totals.setdefault(row["category_id"], [ZERO] * width)
        amount = _convert(row["amount"], row["account__currency"], rates)
        if expense_only or income_only:
            amount = abs(amount)
        if subtotal:
            key, _label = period_bucket(row["date"], subtotal)
            values[period_index[key]] += amount
        else:
            values[0] += amount

    if not totals:
        return {"periods": [p["label"] for p in periods], "groups": [], "totals": None, "overflow": None}

    categories = {c.pk: c for c in Category.objects.filter(pk__in=list(totals.keys())).select_related("parent")}
    for category in list(categories.values()):
        if category.parent_id and category.parent_id not in categories:
            categories[category.parent_id] = category.parent

    for category in list(categories.values()):
        if category.parent_id:
            parent_values = totals.setdefault(category.parent_id, [ZERO] * width)
            for i, v in enumerate(totals.get(category.pk, [ZERO] * width)):
                parent_values[i] += v

    child_ids_by_parent = {}
    for category in categories.values():
        if category.parent_id:
            child_ids_by_parent.setdefault(category.parent_id, []).append(category.pk)

    result_groups = []
    grand_totals = [ZERO] * width
    for group in grouped_categories(categories.values()):
        rows = []
        for category in group["categories"]:
            values = totals.get(category.pk)
            if values is None:
                continue
            rows.append(
                {
                    "id": category.pk,
                    "parent_id": category.parent_id,
                    "name": category.name,
                    "is_child": category.parent_id is not None,
                    "has_children": any(cid in totals for cid in child_ids_by_parent.get(category.pk, [])),
                    "values": values,
                    "total": sum(values, ZERO),
                }
            )
            if not category.parent_id:
                grand_totals = [g + v for g, v in zip(grand_totals, values)]
        if rows:
            result_groups.append({"key": group["key"], "name": group["name"], "rows": rows})

    return {
        "periods": [p["label"] for p in periods],
        "groups": result_groups,
        "totals": {"values": grand_totals, "total": sum(grand_totals, ZERO)},
        "overflow": None,
    }


def category_vs_average_report_rows(
    start, end, periods, expense_only=False, income_only=False, account=None, rates=None
):
    """Like category_totals_vs_average(), but grouped/ordered like the category list page
    (parent rolled up to include its subcategories) instead of sorted by amount, and with
    each row's difference (current - average) included - shaped for the report's table
    rather than its chart, which keeps using the amount-sorted, flat category_totals_vs_
    average() unchanged. A category with no activity in any earlier period has no average (None)."""
    qs = excluding_transfers(Transaction.objects.filter(category__isnull=False))
    qs = _category_type_filter(qs, expense_only, income_only)
    if account:
        qs = qs.filter(account=account)

    def totals_for(s, e):
        return (
            {
                r["category_id"]: to_cent(r["total"])
                for r in qs.filter(date__gte=s, date__lte=e).values("category_id").annotate(total=Sum("amount"))
            }
            if rates is None
            else _totals_for_converted(qs, s, e, rates)
        )

    current_by_category = totals_for(start, end)
    period_totals_list = [totals_for(s, e) for s, e in periods]

    all_ids = set(current_by_category)
    for pt in period_totals_list:
        all_ids |= set(pt)
    if not all_ids:
        return {"groups": [], "totals": None, "average_periods": len(periods)}

    categories = {c.pk: c for c in Category.objects.filter(pk__in=all_ids).select_related("parent")}
    for category in list(categories.values()):
        if category.parent_id and category.parent_id not in categories:
            categories[category.parent_id] = category.parent

    def rollup(per_category_totals):
        totals = dict(per_category_totals)
        for category in categories.values():
            if category.parent_id:
                totals[category.parent_id] = totals.get(category.parent_id, ZERO) + per_category_totals.get(
                    category.pk, ZERO
                )
        return totals

    current = rollup(current_by_category)
    period_totals_list = [rollup(pt) for pt in period_totals_list]
    have_a_row = set(current) | {cid for pt in period_totals_list for cid in pt}

    def average_of(cid):
        if not any(cid in pt for pt in period_totals_list):
            return None
        return to_cent(sum((pt.get(cid, ZERO) for pt in period_totals_list), ZERO) / len(periods))

    child_ids_by_parent = {}
    for category in categories.values():
        if category.parent_id:
            child_ids_by_parent.setdefault(category.parent_id, []).append(category.pk)

    result_groups = []
    totals = {"current": ZERO, "average": ZERO}
    for group in grouped_categories(categories.values()):
        rows = []
        for category in group["categories"]:
            if category.pk not in have_a_row:
                continue
            cur, avg = current.get(category.pk, ZERO), average_of(category.pk)
            if expense_only or income_only:
                cur = abs(cur)
                if avg is not None:
                    avg = abs(avg)
            rows.append(
                {
                    "id": category.pk,
                    "parent_id": category.parent_id,
                    "name": category.name,
                    "is_child": category.parent_id is not None,
                    "has_children": any(cid in have_a_row for cid in child_ids_by_parent.get(category.pk, [])),
                    "current": cur,
                    "average": avg,
                    "difference": None if avg is None else cur - avg,
                }
            )
            if not category.parent_id:
                totals["current"] += cur
                if avg is not None and totals["average"] is not None:
                    totals["average"] += avg
        if rows:
            result_groups.append({"key": group["key"], "name": group["name"], "rows": rows})

    totals["difference"] = None if totals["average"] is None else totals["current"] - totals["average"]
    return {"groups": result_groups, "totals": totals, "average_periods": len(periods)}


def _totals_for_converted(qs, start, end, rates):
    totals = {}
    for row in qs.filter(date__gte=start, date__lte=end).values("category_id", "amount", "account__currency"):
        amount = _convert(row["amount"], row["account__currency"], rates)
        totals[row["category_id"]] = totals.get(row["category_id"], ZERO) + amount
    return {cid: to_cent(total) for cid, total in totals.items()}


def net_worth_by_account(start, end, granularity, rates=None):
    """Every account's running balance (D1: opening balance is itself a transaction, so a sum
    to a date already includes it) at the end of every period bucket in the range, grouped
    like the accounts page (grouped_accounts(): Physical Liquid, Digital Liquid, Illiquid,
    Inactive) - each group's own rollup row stands in for a category table's "parent" row
    (collapsible, to show/hide the individual accounts under it), plus an overall Total row
    summing every group. Every account converts to `rates`'s target currency using the latest
    known rate (D2's simplification, the same the accounts page and dashboard already use) -
    an account with no known rate contributes zero and is named in "missing_accounts"."""
    overflow = subtotal_overflow_message(start, end, granularity)
    if overflow:
        return {"periods": [], "groups": [], "totals": [], "overflow": overflow, "missing_accounts": []}

    periods = _period_columns(start, end, granularity)
    width = len(periods)
    accounts = list(Account.objects.all())
    missing_accounts = []
    values_by_account = {}
    for account in accounts:
        native = [
            to_cent(account.transactions.filter(date__lte=p["end"]).aggregate(total=Sum("amount"))["total"])
            for p in periods
        ]
        if rates is None or account.currency == rates.target:
            values_by_account[account.pk] = native
        else:
            converted = [rates.convert(v, account.currency) for v in native]
            if any(v is None for v in converted):
                missing_accounts.append(account.name)
                values_by_account[account.pk] = [ZERO] * width
            else:
                values_by_account[account.pk] = converted

    result_groups = []
    grand_totals = [ZERO] * width
    for group in grouped_accounts(accounts):
        rows = [
            {"id": account.pk, "name": account.name, "values": values_by_account[account.pk]}
            for account in sorted(group["accounts"], key=lambda a: a.name.lower())
        ]
        group_totals = [ZERO] * width
        for row in rows:
            group_totals = [g + v for g, v in zip(group_totals, row["values"])]
        result_groups.append({"key": group["key"], "name": group["name"], "rows": rows, "totals": group_totals})
        grand_totals = [g + v for g, v in zip(grand_totals, group_totals)]

    return {
        "periods": [p["label"] for p in periods],
        "groups": result_groups,
        "totals": grand_totals,
        "overflow": None,
        "missing_accounts": missing_accounts,
    }


def watched_expenses_category_ids(profile):
    return list(profile.watched_expenses_categories.values_list("pk", flat=True))


def watched_expenses_totals(start, end, category_ids, account=None, rates=None):
    """Spending per chosen category (R8's "Watched expenses", a Settings-configurable
    replacement for the old hardcoded Bills & Utilities report) - None when nothing is chosen,
    so the view can show the "go to Settings" message instead of an empty chart."""
    if not category_ids:
        return None
    qs = excluding_transfers(
        Transaction.objects.filter(
            date__gte=start,
            date__lte=end,
            category_id__in=category_ids,
            category__category_type=Category.CategoryType.EXPENSE,
        )
    )
    if account:
        qs = qs.filter(account=account)
    totals, names = {}, {}
    for row in qs.values("category_id", "category__name", "amount", "account__currency"):
        amount = _convert(row["amount"], row["account__currency"], rates)
        totals[row["category_id"]] = totals.get(row["category_id"], ZERO) + amount
        names[row["category_id"]] = row["category__name"]
    rows = [{"category_id": cid, "name": names[cid], "total": abs(to_cent(total))} for cid, total in totals.items()]
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows


def watched_expenses_series(start, end, category_ids, granularity="month", account=None, rates=None):
    """One line per chosen category (R8), one point per `granularity` period - the view passes
    the Subtotal filter (month when none is chosen), the same way Net worth's chart follows it.
    A period with no activity for a category is a real zero point, not a gap."""
    if not category_ids:
        return []
    names = {c.pk: c.name for c in Category.objects.filter(pk__in=category_ids)}
    periods = _period_columns(start, end, granularity)
    index = {p["key"]: i for i, p in enumerate(periods)}
    totals = {cid: [ZERO] * len(periods) for cid in category_ids}
    qs = excluding_transfers(
        Transaction.objects.filter(
            date__gte=start,
            date__lte=end,
            category_id__in=category_ids,
            category__category_type=Category.CategoryType.EXPENSE,
        )
    )
    if account:
        qs = qs.filter(account=account)
    for row in qs.values("category_id", "amount", "date", "account__currency"):
        key, _label = period_bucket(row["date"], granularity)
        totals[row["category_id"]][index[key]] += _convert(row["amount"], row["account__currency"], rates)
    return [
        {
            "category_id": cid,
            "name": names.get(cid, ""),
            "points": [{"period": p["label"], "total": abs(to_cent(totals[cid][i]))} for i, p in enumerate(periods)],
        }
        for cid in category_ids
    ]


def spending_tree(start, end, rates=None):
    """Spending grouped by top-level category, each broken down one level further.

    Shaped for the dashboard's drill-down donut. Every category is its own net total (R1: the
    category decides, never the sign) - spent 40 and refunded 100 is -60, not +60. The donut
    draws only positive totals but lists the negative ones too, so the totals of every group
    still add up to the Spending tile. Amounts convert into the main currency through `rates`
    like every report, and are summed as Decimal; they only become float at the very end, for
    JSON.
    """
    categories = {category.pk: category for category in Category.objects.all()}

    def lineage(category_id):
        """[top-level, child, grandchild, ...] — stops if a parent loop exists in the data."""
        chain, seen = [], set()
        current = categories.get(category_id)
        while current is not None and current.pk not in seen:
            seen.add(current.pk)
            chain.insert(0, current)
            current = categories.get(current.parent_id)
        return chain

    spent = {}
    rows = excluding_transfers(
        Transaction.objects.filter(
            date__gte=start,
            date__lte=end,
            category__category_type=Category.CategoryType.EXPENSE,
        )
    ).values("category_id", "amount", "account__currency")
    for row in rows:
        # Spending is stored negative, so a refund (positive) reduces what was spent.
        amount = -_convert(row["amount"], row["account__currency"], rates)
        spent[row["category_id"]] = spent.get(row["category_id"], ZERO) + amount

    groups = {}
    for category_id, amount in spent.items():
        chain = lineage(category_id)
        top = chain[0]
        group = groups.setdefault(top.pk, {"id": top.pk, "name": top.name, "total": ZERO, "children": {}})
        group["total"] += amount
        branch = chain[1] if len(chain) > 1 else None
        key = branch.pk if branch else top.pk
        child = group["children"].setdefault(
            key,
            {
                "id": key,
                "name": branch.name if branch else f"{top.name} (general)",
                "with_subcategories": branch is not None,
                "total": ZERO,
            },
        )
        child["total"] += amount

    by_total = lambda item: item["total"]  # noqa: E731
    return [
        {
            "id": group["id"],
            "name": group["name"],
            "total": float(to_cent(group["total"])),
            "children": [
                {**child, "total": float(to_cent(child["total"]))}
                for child in sorted(group["children"].values(), key=by_total, reverse=True)
            ],
        }
        for group in sorted(groups.values(), key=by_total, reverse=True)
    ]
