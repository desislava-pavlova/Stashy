"""Helpers for turning a DateRangeFilterForm's cleaned data into a concrete
(start, end, interval) tuple, and for bucketing transactions by that interval.
"""
from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db.models import Q, Sum

from .models import Account, Category, Transaction, grouped_accounts, grouped_categories
from .money import ZERO, to_cent

# Default depth for "Average interval": how many periods of the current date range's own
# length to average over, immediately preceding it (see vs_average_periods() below).
DEFAULT_LOOKBACK_PERIODS = 12


def resolve_range(cleaned_data):
    today = date.today()
    range_key = cleaned_data.get("range") or "ytd"
    start = cleaned_data.get("start")
    end = cleaned_data.get("end")

    if range_key == "custom" and start and end:
        return start, end, cleaned_data.get("interval") or str(DEFAULT_LOOKBACK_PERIODS)

    if range_key == "this_month":
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


def excluding_transfers(queryset):
    """Transfer-type categories (Phase 3) never count as income or spending (D7/D11)."""
    return queryset.exclude(category__account__isnull=False)


def income_and_spending(start, end, account=None):
    qs = Transaction.objects.filter(date__gte=start, date__lte=end)
    if account:
        qs = qs.filter(account=account)
    totals = excluding_transfers(qs).aggregate(
        income=Sum("amount", filter=Q(amount__gt=0)),
        spending=Sum("amount", filter=Q(amount__lt=0)),
    )
    return {"income": to_cent(totals["income"]), "spending": abs(to_cent(totals["spending"]))}


def savings_rate(income, spending):
    """Percentage of income that wasn't spent; None when there was no income to save from."""
    if income <= 0:
        return None
    return ((income - spending) / income * 100).quantize(Decimal("0.1"))


def vs_average_periods(start, end, lookback):
    """`lookback` consecutive periods the same length as [start, end] (inclusive), chained
    backward with no gap starting immediately before `start` - what "Average interval" means
    since the 2026-09-15 follow-up: the average a report compares "current" against is always
    the same length as whatever Date range is actually selected (1 month in, 1-month periods
    out; a custom 10-day range in, 10-day periods out), and the only thing the interval picker
    controls is how many of those periods back to look. Not recalendared to month/quarter
    boundaries - a range that isn't itself a whole calendar period won't produce
    calendar-aligned history either, which is the price of the periods genuinely matching the
    range's own length rather than some fixed granularity."""
    span_days = (end - start).days + 1
    periods = []
    cursor_end = start - timedelta(days=1)
    for _ in range(lookback):
        cursor_start = cursor_end - timedelta(days=span_days - 1)
        periods.append((cursor_start, cursor_end))
        cursor_end = cursor_start - timedelta(days=1)
    return periods


def income_and_spending_vs_average(start, end, lookback, account=None):
    """This period's income/spending/savings-rate vs. the average of `lookback` periods the
    same length as [start, end], immediately preceding it - the same comparison
    category_totals_vs_average() makes per category, but for the overall totals shown on the
    Income & expense card."""
    current = income_and_spending(start, end, account=account)

    incomes, spendings = [], []
    for period_start, period_end in vs_average_periods(start, end, lookback):
        period_totals = income_and_spending(period_start, period_end, account=account)
        incomes.append(period_totals["income"])
        spendings.append(period_totals["spending"])
    average_income = to_cent(sum(incomes, ZERO) / len(incomes)) if incomes else ZERO
    average_spending = to_cent(sum(spendings, ZERO) / len(spendings)) if spendings else ZERO

    return {
        "current_rate": savings_rate(current["income"], current["spending"]),
        "average_rate": savings_rate(average_income, average_spending),
    }


def category_totals(start, end, expense_only=False, account=None):
    """Per-category totals for the period, transfers excluded. Expense-only returns positive
    magnitudes sorted biggest-first (for "Spending by category"); otherwise totals stay
    signed (income positive, expense negative) and sorted low-to-high, so the chart reads as
    one continuous scale from biggest expense to biggest income."""
    qs = excluding_transfers(Transaction.objects.filter(date__gte=start, date__lte=end, category__isnull=False))
    if expense_only:
        qs = qs.filter(amount__lt=0)
    if account:
        qs = qs.filter(account=account)
    rows = qs.values("category_id", "category__name").annotate(total=Sum("amount")).order_by("total")
    if expense_only:
        return [{"category_id": r["category_id"], "name": r["category__name"], "total": abs(to_cent(r["total"]))} for r in rows]
    return [{"category_id": r["category_id"], "name": r["category__name"], "total": to_cent(r["total"])} for r in rows]


def category_totals_top_level(start, end, expense_only=False, account=None):
    """Like category_totals(), but a subcategory's amount is rolled into its top-level
    parent's - for charts that read better as one bar per main category (Phase 11.1) than
    with every subcategory competing for space alongside them."""
    qs = excluding_transfers(Transaction.objects.filter(date__gte=start, date__lte=end, category__isnull=False))
    if expense_only:
        qs = qs.filter(amount__lt=0)
    if account:
        qs = qs.filter(account=account)

    raw = {}
    for row in qs.values("category_id", "amount"):
        raw[row["category_id"]] = raw.get(row["category_id"], ZERO) + row["amount"]
    if not raw:
        return []

    categories = {c.pk: c for c in Category.objects.filter(pk__in=raw.keys()).select_related("parent")}
    top_totals, top_names = {}, {}
    for category_id, amount in raw.items():
        category = categories[category_id]
        top = category.parent if category.parent_id else category
        top_totals[top.pk] = top_totals.get(top.pk, ZERO) + amount
        top_names[top.pk] = top.name

    rows = [
        {"category_id": cid, "name": top_names[cid], "total": to_cent(total)}
        for cid, total in top_totals.items()
    ]
    rows.sort(key=lambda r: r["total"])
    if expense_only:
        for r in rows:
            r["total"] = abs(r["total"])
    return rows


def category_totals_vs_average(start, end, lookback, expense_only=False, account=None):
    """Per category: [start, end]'s total vs. the average of `lookback` periods the same
    length as it, immediately preceding it (periods with no activity for a category don't
    count toward its average - mirrors the overall current-vs-average metric's own behavior).
    Transfers excluded."""
    qs = excluding_transfers(Transaction.objects.filter(category__isnull=False))
    if expense_only:
        qs = qs.filter(amount__lt=0)
    if account:
        qs = qs.filter(account=account)

    current_rows = list(
        qs.filter(date__gte=start, date__lte=end).values("category_id", "category__name").annotate(total=Sum("amount"))
    )
    names, current_by_category, history_by_category = {}, {}, {}
    for row in current_rows:
        names[row["category_id"]] = row["category__name"]
        current_by_category[row["category_id"]] = to_cent(row["total"])
    for period_start, period_end in vs_average_periods(start, end, lookback):
        for row in (
            qs.filter(date__gte=period_start, date__lte=period_end)
            .values("category_id", "category__name").annotate(total=Sum("amount"))
        ):
            names.setdefault(row["category_id"], row["category__name"])
            history_by_category.setdefault(row["category_id"], []).append(to_cent(row["total"]))

    result = []
    for category_id, name in names.items():
        current = current_by_category.get(category_id, ZERO)
        history = history_by_category.get(category_id, [])
        average = to_cent(sum(history, ZERO) / len(history)) if history else ZERO
        if expense_only:
            current, average = abs(current), abs(average)
        result.append({"category_id": category_id, "name": name, "current": current, "average": average})
    result.sort(key=lambda r: r["current"])
    return result


BILLS_AND_UTILITIES_NAME = "Bills & Utilities"


def bills_and_utilities_category():
    return Category.objects.filter(parent__isnull=True, name__iexact=BILLS_AND_UTILITIES_NAME).first()


def bills_and_utilities_totals(start, end, account=None):
    """Spending by subcategory under the "Bills & Utilities" top-level category, for the
    Bills & Utilities report's chart (Phase 11) - that category is expected to already exist
    (Phase 12 may offer to create a starter set of categories including this one); returns
    None if it doesn't, so the view can show a "create it" message instead of an empty chart."""
    top = bills_and_utilities_category()
    if top is None:
        return None
    category_ids = bills_and_utilities_category_ids(top)
    qs = excluding_transfers(Transaction.objects.filter(
        date__gte=start, date__lte=end, category_id__in=category_ids, amount__lt=0
    ))
    if account:
        qs = qs.filter(account=account)
    rows = qs.values("category_id", "category__name").annotate(total=Sum("amount")).order_by("total")
    return {
        "category": top,
        "rows": [{"category_id": r["category_id"], "name": r["category__name"], "total": abs(to_cent(r["total"]))} for r in rows],
    }


def bills_and_utilities_category_ids(top):
    return [top.pk] + list(top.subcategories.values_list("pk", flat=True))


def bills_and_utilities_series(start, end, account=None):
    """Bills & Utilities spending per month across the range (a flow, not a running balance -
    unlike Net worth's own line, there's nothing to accumulate here), for the report's line
    chart (Phase 11.1: "should be lines like net worth") - sampled the same fixed-monthly way
    Net worth's chart always is, independent of the Subtotal filter, which only affects the
    tables. Assumes the "Bills & Utilities" category already exists - callers only call this
    once bills_and_utilities_totals() has confirmed that."""
    top = bills_and_utilities_category()
    if top is None:
        return []
    category_ids = bills_and_utilities_category_ids(top)
    points = []
    cursor = start.replace(day=1)
    while cursor <= end:
        month_end = (cursor + relativedelta(months=1)) - relativedelta(days=1)
        period_start, period_end = max(cursor, start), min(month_end, end)
        qs = excluding_transfers(Transaction.objects.filter(
            date__gte=period_start, date__lte=period_end, category_id__in=category_ids, amount__lt=0
        ))
        if account:
            qs = qs.filter(account=account)
        total = qs.aggregate(total=Sum("amount"))["total"]
        points.append({"period": cursor, "total": abs(to_cent(total))})
        cursor += relativedelta(months=1)
    return points


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


def category_report_rows(start, end, subtotal="", expense_only=False, account=None, category_ids=None):
    """Category totals for the period, grouped and ordered like the category list page
    (grouped_categories(): Income group then Expense group, a parent immediately followed by
    its own subcategories) - a parent row is rolled up to include its subcategories' amounts
    on top of its own, matching how the category list page already treats a category as
    "itself and what's under it". When `subtotal` is a period granularity, each row gets one
    value per period bucket across the range plus a row-level total; otherwise each row just
    gets its one total for the whole range (as a single-element values list).

    Returns {"periods": [labels...], "groups": [{"name", "rows": [{"name", "is_child",
    "values", "total"}]}], "totals": {"values": [...], "total": Decimal} or None if empty}.
    """
    qs = excluding_transfers(Transaction.objects.filter(date__gte=start, date__lte=end, category__isnull=False))
    if expense_only:
        qs = qs.filter(amount__lt=0)
    if account:
        qs = qs.filter(account=account)
    if category_ids is not None:
        qs = qs.filter(category_id__in=category_ids)

    periods = _period_columns(start, end, subtotal) if subtotal else []
    period_index = {p["key"]: i for i, p in enumerate(periods)}
    width = len(periods) if subtotal else 1

    totals = {}
    for row in qs.values("category_id", "amount", "date"):
        values = totals.setdefault(row["category_id"], [ZERO] * width)
        amount = abs(row["amount"]) if expense_only else row["amount"]
        if subtotal:
            key, _label = period_bucket(row["date"], subtotal)
            values[period_index[key]] += amount
        else:
            values[0] += amount

    if not totals:
        return {"periods": [p["label"] for p in periods], "groups": [], "totals": None}

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
            rows.append({
                "id": category.pk, "parent_id": category.parent_id, "name": category.name,
                "is_child": category.parent_id is not None,
                "has_children": any(cid in totals for cid in child_ids_by_parent.get(category.pk, [])),
                "values": values, "total": sum(values, ZERO),
            })
            if not category.parent_id:
                grand_totals = [g + v for g, v in zip(grand_totals, values)]
        if rows:
            result_groups.append({"key": group["key"], "name": group["name"], "rows": rows})

    return {
        "periods": [p["label"] for p in periods],
        "groups": result_groups,
        "totals": {"values": grand_totals, "total": sum(grand_totals, ZERO)},
    }


def category_vs_average_report_rows(start, end, lookback, expense_only=False, account=None):
    """Like category_totals_vs_average(), but grouped/ordered like the category list page
    (parent rolled up to include its subcategories) instead of sorted by amount, and with
    each row's difference (current - average) included - shaped for the report's table
    rather than its chart, which keeps using the amount-sorted, flat category_totals_vs_
    average() unchanged."""
    qs = excluding_transfers(Transaction.objects.filter(category__isnull=False))
    if expense_only:
        qs = qs.filter(amount__lt=0)
    if account:
        qs = qs.filter(account=account)

    current_by_category = {
        r["category_id"]: to_cent(r["total"])
        for r in qs.filter(date__gte=start, date__lte=end).values("category_id").annotate(total=Sum("amount"))
    }
    history_by_category = {}
    for period_start, period_end in vs_average_periods(start, end, lookback):
        for r in (
            qs.filter(date__gte=period_start, date__lte=period_end)
            .values("category_id").annotate(total=Sum("amount"))
        ):
            history_by_category.setdefault(r["category_id"], []).append(to_cent(r["total"]))

    all_ids = set(current_by_category) | set(history_by_category)
    if not all_ids:
        return {"groups": [], "totals": None}

    categories = {c.pk: c for c in Category.objects.filter(pk__in=all_ids).select_related("parent")}
    for category in list(categories.values()):
        if category.parent_id and category.parent_id not in categories:
            categories[category.parent_id] = category.parent

    current = {cid: current_by_category.get(cid, ZERO) for cid in all_ids}
    history = {cid: history_by_category.get(cid, []) for cid in all_ids}
    for category in list(categories.values()):
        if category.parent_id:
            current.setdefault(category.parent_id, ZERO)
            current[category.parent_id] += current.get(category.pk, ZERO)
            history[category.parent_id] = history.get(category.parent_id, []) + history.get(category.pk, [])

    def average_of(cid):
        samples = history.get(cid, [])
        return to_cent(sum(samples, ZERO) / len(samples)) if samples else ZERO

    have_a_row = {cid for cid in categories if cid in current or cid in history}
    child_ids_by_parent = {}
    for category in categories.values():
        if category.parent_id:
            child_ids_by_parent.setdefault(category.parent_id, []).append(category.pk)

    result_groups = []
    totals = {"current": ZERO, "average": ZERO}
    for group in grouped_categories(categories.values()):
        rows = []
        for category in group["categories"]:
            if category.pk not in current and category.pk not in history:
                continue
            cur, avg = current.get(category.pk, ZERO), average_of(category.pk)
            if expense_only:
                cur, avg = abs(cur), abs(avg)
            rows.append({
                "id": category.pk, "parent_id": category.parent_id, "name": category.name,
                "is_child": category.parent_id is not None,
                "has_children": any(cid in have_a_row for cid in child_ids_by_parent.get(category.pk, [])),
                "current": cur, "average": avg, "difference": cur - avg,
            })
            if not category.parent_id:
                totals["current"] += cur
                totals["average"] += avg
        if rows:
            result_groups.append({"key": group["key"], "name": group["name"], "rows": rows})

    totals["difference"] = totals["current"] - totals["average"]
    return {"groups": result_groups, "totals": totals}


def net_worth_by_account(start, end, granularity):
    """Every account's running balance (D1: opening balance is itself a transaction, so a sum
    to a date already includes it) at the end of every period bucket in the range, grouped
    like the accounts page (grouped_accounts(): Physical Liquid, Digital Liquid, Illiquid,
    Inactive) - each group's own rollup row stands in for a category table's "parent" row
    (collapsible, to show/hide the individual accounts under it), plus an overall Total row
    summing every group - the combined net worth the old single-line chart already showed."""
    periods = _period_columns(start, end, granularity)
    width = len(periods)
    accounts = list(Account.objects.all())
    values_by_account = {
        account.pk: [
            to_cent(account.transactions.filter(date__lte=p["end"]).aggregate(total=Sum("amount"))["total"])
            for p in periods
        ]
        for account in accounts
    }

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

    return {"periods": [p["label"] for p in periods], "groups": result_groups, "totals": grand_totals}


def spending_tree(start, end):
    """Spending grouped by top-level category, each broken down one level further.

    Shaped for the dashboard's drill-down donut. Money is summed as Decimal and only
    becomes float at the very end, for JSON.
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

    groups = {}
    rows = (
        excluding_transfers(Transaction.objects.filter(date__gte=start, date__lte=end, amount__lt=0))
        .values("category_id")
        .annotate(total=Sum("amount"))
    )
    for row in rows:
        amount = abs(row["total"])
        chain = lineage(row["category_id"]) if row["category_id"] else []
        top = chain[0] if chain else None
        group = groups.setdefault(top.pk if top else None, {
            "id": top.pk if top else None,
            "name": top.name if top else "Uncategorized",
            "total": ZERO,
            "children": {},
        })
        group["total"] += amount
        if top is None:
            continue
        branch = chain[1] if len(chain) > 1 else None
        key = branch.pk if branch else top.pk
        child = group["children"].setdefault(key, {
            "id": key,
            "name": branch.name if branch else f"{top.name} (general)",
            "with_subcategories": branch is not None,
            "total": ZERO,
        })
        child["total"] += amount

    by_total = lambda item: item["total"]  # noqa: E731
    return [
        {
            "id": group["id"],
            "name": group["name"],
            "total": float(group["total"]),
            "children": [
                {**child, "total": float(child["total"])}
                for child in sorted(group["children"].values(), key=by_total, reverse=True)
            ],
        }
        for group in sorted(groups.values(), key=by_total, reverse=True)
    ]
