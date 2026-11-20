"""The limits on what Stashy stores: how far back and forward a date can
lie, and how big a reminder's interval, a transaction number or an
amount can be.

The models' validators, the forms and the importer all read them from
here, so each rule has one home and a person typing, a file being
imported and the admin all meet the same one. A limit applies when
something is saved or imported. What is already stored is not checked
or changed, and lists and reports as before; a row outside a limit
meets it again only when someone saves it.

This module must not import from the rest of the app, which imports it.
"""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError


# Years a stored date can lie in. A typo like 0026 or 2206 is far
# outside what a personal ledger holds, and a year near 1 or 9999
# overflows the calendar maths (a reminder's next date, a report's
# range).
EARLIEST_YEAR = 1900
LATEST_YEAR = 2100
EARLIEST_DATE = date(EARLIEST_YEAR, 1, 1)
LATEST_DATE = date(LATEST_YEAR, 12, 31)
DATE_OUT_OF_RANGE_MESSAGE = f"Enter a date between {EARLIEST_YEAR} and {LATEST_YEAR}."

# "Every N weeks/months/...": no real schedule needs more, and a huge
# one pushes the next date past the calendar's end.
MAX_REMINDER_INTERVAL = 120

# A transaction number restarts every year, so nine digits is far more
# than any year holds. The biggest value a database column holds is
# not a safe maximum: the next number is one more than the highest.
MAX_TRANSACTION_NUMBER = 999_999_999

# What a money column holds: 12 digits, 2 of them after the point.
MAX_AMOUNT = Decimal("9999999999.99")

# What the exchange rate column holds: 20 digits, 10 of them after the
# point. A rate that rounds to zero at that size is no rate.
MAX_EXCHANGE_RATE = Decimal("9999999999.9999999999")
_EXCHANGE_RATE_STEP = Decimal("0.0000000001")


def is_plausible_date(value):
    return EARLIEST_DATE <= value <= LATEST_DATE


def is_plausible_rate(rate):
    """Whether an exchange rate fits its column and is more than zero
    once rounded to the column's 10 places. A rate read from the
    network can be anything, NaN included."""
    return (
        rate.is_finite()
        and 0 < rate <= MAX_EXCHANGE_RATE
        and rate.quantize(_EXCHANGE_RATE_STEP) > 0
    )


def validate_plausible_date(value):
    """For a date field's `validators`. The migrations refer to this
    function by its name: keep the name, or leave an alias behind
    when renaming it."""
    if not is_plausible_date(value):
        raise ValidationError(DATE_OUT_OF_RANGE_MESSAGE, code="date_out_of_range")
