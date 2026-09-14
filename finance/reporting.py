"""Helpers for turning a DateRangeFilterForm's cleaned data into a concrete
(start, end, interval) tuple, and for bucketing transactions by that interval.
"""
from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db.models import Q, Sum
from django.db.models.functions import TruncDay, TruncMonth, TruncQuarter, TruncWeek, TruncYear

from .models import Category, Transaction
from .money import ZERO

TRUNC_FUNCS = {
    "day": TruncDay,
    "week": TruncWeek,
    "month": TruncMonth,
    "quarter": TruncQuarter,
    "year": TruncYear,
}


def resolve_range(cleaned_data):
    today = date.today()
    range_key = cleaned_data.get("range") or "last_12_months"
    start = cleaned_data.get("start")
    end = cleaned_data.get("end")

    if range_key == "custom" and start and end:
        return start, end, cleaned_data.get("interval") or "month"

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

    return start, end, cleaned_data.get("interval") or "month"


def trunc_func(interval):
    return TRUNC_FUNCS.get(interval, TruncMonth)("date")


def excluding_transfers(queryset):
    """Transfer-type categories (Phase 3) never count as income or spending (D7/D11)."""
    return queryset.exclude(category__account__isnull=False)


def income_and_spending(start, end):
    totals = excluding_transfers(Transaction.objects.filter(date__gte=start, date__lte=end)).aggregate(
        income=Sum("amount", filter=Q(amount__gt=0)),
        spending=Sum("amount", filter=Q(amount__lt=0)),
    )
    return {"income": totals["income"] or ZERO, "spending": abs(totals["spending"] or ZERO)}


def savings_rate(income, spending):
    """Percentage of income that wasn't spent; None when there was no income to save from."""
    if income <= 0:
        return None
    return ((income - spending) / income * 100).quantize(Decimal("0.1"))


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
