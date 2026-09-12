"""Helpers for turning a DateRangeFilterForm's cleaned data into a concrete
(start, end, interval) tuple, and for bucketing transactions by that interval.
"""
from datetime import date

from dateutil.relativedelta import relativedelta
from django.db.models.functions import TruncDay, TruncMonth, TruncQuarter, TruncWeek, TruncYear

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
