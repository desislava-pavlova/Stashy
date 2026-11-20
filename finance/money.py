"""Money as Decimal, never float: the cent constants, rounding, and
reading an amount a person typed or imported (`parse_amount`).

Report maths and exports call these instead of rounding by hand, so a
total is always exact to the cent. A float belongs only at the
chart/JSON edge.
"""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .limits import MAX_AMOUNT


CENT = Decimal("0.01")
ZERO = Decimal("0.00")

_NON_NUMERIC = re.compile(r"[^\d,.\-+]")


def to_cent(value):
    """Quantize a Decimal to cents (rounding half up). Use on every
    Sum()/average result pulled out of an aggregate query before storing
    or exporting it raw - SQLite can hand back a Decimal with far more
    decimal places than the underlying amounts actually have (e.g.
    Decimal("-132.370000000000")), which is invisible through |moneyc's
    own rounding but shows up ugly in a plain str() export or a
    difference computed from two such values. A tiny negative rounds to
    zero without its sign: `Decimal("-0.0033")` would otherwise come
    back as `Decimal("-0.00")`, and the CSV writer would print "-0.00".
    """
    return (value if value is not None else ZERO).quantize(CENT, rounding=ROUND_HALF_UP) or ZERO


def parse_amount(raw):
    """Parse a money string from any common bank format into a Decimal
    rounded to cents.

    Handles "1 234.56", "1.234,56", "1,234.56", "12,50", "(12.00)",
    "€ -5" etc. Returns None if the value can't be read as a number, or
    is too big for an amount (a money column holds 12 digits, so one
    with thirty would otherwise be an error deep inside the save).
    """
    if raw is None:
        return None
    text = str(raw).strip()
    negative = text.startswith("(") and text.endswith(")")
    text = _NON_NUMERIC.sub("", text)
    if not text:
        return None

    if "," in text and "." in text:
        # Whichever separator comes last is the decimal one.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        if text.count(",") == 1 and len(tail) in (1, 2):
            text = f"{head}.{tail}"
        else:
            text = text.replace(",", "")
    elif text.count(".") > 1:
        text = text.replace(".", "")

    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    # copy_abs() and copy_negate() never round. abs() and - do, to 28
    # digits, and raise Overflow for a number of a million digits.
    if negative:
        value = value.copy_abs().copy_negate()
    # Checked before rounding, because quantizing a very long number is
    # itself an InvalidOperation. Half a cent past the largest amount
    # would round up out of range; anything below it rounds into it.
    if value.copy_abs() >= MAX_AMOUNT + CENT / 2:
        return None
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
