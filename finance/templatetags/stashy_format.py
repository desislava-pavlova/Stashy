from decimal import InvalidOperation

from django import template

from finance import regional
from finance.currency import CURRENCY_SYMBOLS, format_money

register = template.Library()


@register.filter
def currency_symbol(code):
    """The symbol shown for a currency code ("EUR" -> "€"), not the bare code - same table
    |moneyc already uses, for the few spots (e.g. a chart, JS-rendered) that need just the
    symbol on its own rather than a full formatted amount."""
    return CURRENCY_SYMBOLS.get(code, code)


@register.filter
def money(value, decimals=2):
    """Format a number in the viewer's number style — 10 000.50 by default (D10)."""
    if value is None or value == "":
        return ""
    try:
        return regional.format_number(value, decimals=int(decimals))
    except (InvalidOperation, ValueError, TypeError):
        return value


@register.filter
def moneyc(value, currency):
    """Format an amount with its currency's symbol — $9.66 rather than a bare 9.66 (D2).

    Use this instead of |money wherever one specific currency applies (an account's own
    balance/transactions, or an amount already converted to the main currency). Leave
    plain |money for amounts with no single currency attached, e.g. a memorized payee's
    default amount, which can come from transactions in different accounts/currencies.
    """
    if value is None or value == "":
        return ""
    if not currency:
        return money(value)
    try:
        return format_money(value, currency)
    except (InvalidOperation, ValueError, TypeError):
        return value


@register.filter
def abs_value(value):
    """abs() for templates — used to show a signed amount unsigned in a Payment/Deposit column (D3),
    where the column itself already says which direction the money moved."""
    try:
        return abs(value)
    except TypeError:
        return value


@register.filter
def sdate(value):
    """Format a date/datetime in the viewer's date format (D10) — use instead of Django's |date for stored dates."""
    return regional.format_date(value)


@register.filter
def sdatetime(value):
    """Date + time, in the viewer's preferences — for the few places a time matters (import history, audit log)."""
    return regional.format_datetime(value)
