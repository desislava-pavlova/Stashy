import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

_NON_NUMERIC = re.compile(r"[^\d,.\-+]")


def to_cent(value):
    """Quantize a Decimal to cents (rounding half up). Use on every Sum()/average result
    pulled out of an aggregate query before storing or exporting it raw - SQLite can hand
    back a Decimal with far more decimal places than the underlying amounts actually have
    (e.g. Decimal("-132.370000000000")), which is invisible through |moneyc's own rounding
    but shows up ugly in a plain str() export or a difference computed from two such values."""
    return (value if value is not None else ZERO).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_amount(raw):
    """Parse a money string from any common bank format into a Decimal rounded to cents.

    Handles "1 234.56", "1.234,56", "1,234.56", "12,50", "(12.00)", "€ -5" etc.
    Returns None if the value can't be read as a number.
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
    if negative:
        value = -abs(value)
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
