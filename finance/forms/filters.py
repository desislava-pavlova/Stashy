"""The date-range form that Transactions and Reports share. It only
checks what was chosen; filters.py, beside views/, turns the answers
into dates and querysets.
"""

from django import forms
from django.core.exceptions import ValidationError

from dateutil.relativedelta import relativedelta

from ..limits import EARLIEST_YEAR, LATEST_YEAR
from ..models import Account
from ..reporting import CUSTOM_RANGE_KEY
from ._base import BootstrapFormMixin, DateInput


class DateRangeFilterForm(BootstrapFormMixin, forms.Form):
    control_size = "sm"

    RANGE_CHOICES = [
        ("this_month", "This month"),
        ("last_month", "Last month"),
        ("this_quarter", "This quarter"),
        ("ytd", "Year to date"),
        ("last_12_months", "Last 12 months"),
        ("this_year", "This year"),
        ("last_year", "Last year"),
        ("earliest", "Earliest to date"),
        (CUSTOM_RANGE_KEY, "Custom range"),
    ]
    # How many earlier periods to average against (the Average
    # interval) - not a separate bucketing granularity; see
    # reporting.vs_average_periods() for what an earlier period is.
    INTERVAL_CHOICES = [
        ("3", "Last 3 periods"),
        ("6", "Last 6 periods"),
        ("9", "Last 9 periods"),
        ("12", "Last 12 periods"),
    ]
    # How a date range is sliced into columns ("Subtotal by"). Not the
    # same thing as Interval above, which says how many earlier periods
    # to average against.
    SUBTOTAL_CHOICES = [
        ("", "No subtotal"),
        ("week", "Week"),
        ("month", "Month"),
        ("quarter", "Quarter"),
        ("half_year", "Half-year"),
        ("year", "Year"),
    ]

    # A custom range outside the stored dates' limits would overflow
    # the report maths (year 1 or 9999), and one over a century would
    # make Net worth walk every day of it.
    MAX_RANGE_YEARS = 100

    # Reports default to This month and Last 3 periods: a fresh, unbound
    # form shows these as selected even before any filter is submitted.
    range = forms.ChoiceField(choices=RANGE_CHOICES, required=False, initial="this_month")
    # data-range-date: typing a date switches Range to "Custom range"
    # (stashy.js).
    start = forms.DateField(required=False, widget=DateInput(attrs={"data-range-date": ""}))
    end = forms.DateField(required=False, widget=DateInput(attrs={"data-range-date": ""}))
    interval = forms.ChoiceField(
        choices=INTERVAL_CHOICES, required=False, initial="3", label="Average interval"
    )
    subtotal = forms.ChoiceField(choices=SUBTOTAL_CHOICES, required=False, label="Subtotal by")
    account = forms.ModelChoiceField(queryset=Account.objects.all(), required=False)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("range") == CUSTOM_RANGE_KEY:
            start, end = cleaned.get("start"), cleaned.get("end")
            if not (start and end):
                raise ValidationError("A custom range needs both a start and an end date.")
            if start > end:
                raise ValidationError("The start date must be on or before the end date.")
            if start.year < EARLIEST_YEAR or end.year > LATEST_YEAR:
                raise ValidationError(
                    f"Dates must fall between {EARLIEST_YEAR} and {LATEST_YEAR}."
                )
            if end > start + relativedelta(years=self.MAX_RANGE_YEARS):
                raise ValidationError(
                    f"A custom range can cover at most {self.MAX_RANGE_YEARS} years."
                )
        return cleaned
