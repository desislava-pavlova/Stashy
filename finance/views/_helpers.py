"""Small pieces several view modules share: column sorting, paging, the
safe "next" redirect, and the CSV/XLSX writers every export goes
through."""

import csv
import re
from datetime import date
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Case, DecimalField, F, When
from django.http import HttpResponse
from django.utils.http import url_has_allowed_host_and_scheme
from openpyxl import Workbook
from openpyxl.cell.cell import TYPE_STRING

from .. import regional

# What the Payment and Deposit headers sort by: the amount on that side,
# and NULL on the other. Every column sorts ascending on the first click
# and descending on the second, so these are written so that ascending
# puts the biggest first: a payment is stored negative already, and a
# deposit is negated. The first click then gives "biggest first", the
# second "smallest first", and the rows with nothing in that column
# (NULL) stay at the bottom both times (_order_by()).
PAYMENT_SORT = Case(When(amount__lt=0, then=F("amount")), output_field=DecimalField())
DEPOSIT_SORT = Case(When(amount__gt=0, then=-F("amount")), output_field=DecimalField())
# The same two columns on the Transactions page and on an account's
# register.
AMOUNT_SORT_COLUMNS = {"payment": [PAYMENT_SORT], "deposit": [DEPOSIT_SORT]}


def _order_by(sort_fields, descending):
    """The `order_by()` terms for one column. A plain field name takes a
    "-" to go descending; an expression (a column that is blank on some
    rows) keeps its blank rows last either way."""
    terms = []
    for field in sort_fields:
        if isinstance(field, str):
            terms.append(f"-{field}" if descending else field)
        else:
            terms.append(field.desc(nulls_last=True) if descending else field.asc(nulls_last=True))
    return terms


def _sorted(request, queryset, sortable_columns):
    """Apply ?sort=field / ?sort=-field (toggling ascending/descending)
    to a queryset.

    Falls back to the model's own default ordering when the column isn't
    sortable or no sort was requested. Returns (queryset, current sort
    key, {column: next sort key}) — the last two are for building column
    headers that link to the next click's sort order.
    """
    sort = request.GET.get("sort", "")
    column = sort.removeprefix("-")
    if column in sortable_columns:
        queryset = queryset.order_by(*_order_by(sortable_columns[column], sort.startswith("-")))
    else:
        sort = ""
    sort_next = {name: (f"-{name}" if sort == name else name) for name in sortable_columns}
    return queryset, sort, sort_next


# Rows per page on a long list of transactions. A year of real use stays
# well under this, so only a range of several years ever gets a second
# page.
TRANSACTIONS_PER_PAGE = 1000


def _ordered_with_ties_broken(transactions):
    """`transactions` in the order it is already sorted in, ending with
    the primary key. Rows that tie on every sort column (the same payee,
    say, or every row with nothing in a Payment sort) need one fixed
    order, or the database could put the same row on two pages and leave
    another off both. The export uses it too, so it matches the page it
    was made from."""
    ordering = transactions.query.order_by or transactions.model._meta.ordering
    return transactions.order_by(*ordering, "-pk")


def _paged(request, transactions, split):
    """(the rows to show, the page or None) for a list of transactions,
    already sorted.

    Only a `split` list is cut into pages, at ?page=N (a missing or
    wrong number shows the nearest real page). Either way the order ends
    with the primary key (see _ordered_with_ties_broken())."""
    transactions = _ordered_with_ties_broken(transactions)
    if not split:
        return transactions, None
    page = Paginator(transactions, TRANSACTIONS_PER_PAGE).get_page(request.GET.get("page"))
    return page.object_list, page


def _safe_next(request, url):
    """`url` if it stays on this site, else "" - never redirect
    off-site. Django's own check also refuses tricks a plain "starts
    with /" test lets through, like `/\\evil.com`, which browsers read
    as `//evil.com`."""
    allowed = url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return url if allowed else ""


# The biggest number SQLite's INTEGER holds. A lookup by a bigger one is
# an OverflowError.
_LARGEST_ID = 2**63 - 1


def _as_id(value):
    """`value` as an integer database id if it is plain ASCII digits and
    fits, else None. Whatever a hand-edited URL or form sends must not
    reach a lookup unchecked: a value that isn't a number is a 500 page
    and a traceback in the log. `isdigit()` alone isn't enough, since it
    is also true for characters like "²" that `int()` refuses, and a
    number too big for the database overflows. A lookup by None finds
    nothing, so a bad id answers like an id that doesn't exist."""
    text = value if isinstance(value, str) else ""
    if not (text.isascii() and text.isdigit()):
        return None
    # Too long to be an id at all. Python refuses to turn a string of
    # more than 4,300 digits into an int (a ValueError), so the length
    # is checked first; leading zeros don't count.
    digits = text.lstrip("0") or "0"
    if len(digits) > len(str(_LARGEST_ID)):
        return None
    number = int(digits)
    return number if number <= _LARGEST_ID else None


def _as_ids(values):
    """The ids in `values` (see _as_id()); anything else is left out."""
    return [found for found in map(_as_id, values) if found is not None]


def _plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


# ----------------------------------------------------------------------
# Export writers - CSV built in; XLSX via openpyxl
# ----------------------------------------------------------------------

# Excel, LibreOffice and Google Sheets treat a cell starting with one of
# these as a formula, and imported bank text can start with anything -
# so text cells that do get a leading apostrophe in CSV, the usual "this
# is text" marker (OWASP's advice for CSV injection). The apostrophe
# goes after any leading spaces: the report layouts indent a
# subcategory's name with two, and an indent must not hide a formula
# from this check.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value):
    """One export value as CSV text: amounts with exactly two decimals,
    ISO dates (what Stashy's own import reads back), and text made safe
    from formula injection."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.lstrip(" ")
        if text.startswith(_FORMULA_PREFIXES):
            return value[: len(value) - len(text)] + "'" + text
    return value


def _csv_response(filename, headers, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    # A BOM first, or Excel guesses the wrong encoding and shows
    # Cyrillic as garbage.
    response.write("﻿")
    writer = csv.writer(response)
    writer.writerow(headers)
    writer.writerows([_csv_cell(value) for value in row] for row in rows)
    return response


# What a worksheet can't hold: the control characters XML forbids (every
# one but tab, line feed and carriage return), and U+FFFE and U+FFFF.
# Text pasted from a PDF or a bank's page can carry them. openpyxl
# refuses the first kind when the cell is set, and its own list
# (`ILLEGAL_CHARACTERS_RE`) misses the last two, which the XML writer
# then refuses when the file is saved. Either way one odd memo turned
# the whole export into an error page.
_FORBIDDEN_IN_WORKSHEETS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _xlsx_response(filename, headers, rows):
    """Amounts go in as real numbers and dates as real dates, so Excel
    can add them up and sort them. Text is always stored as text -
    openpyxl would otherwise write a string starting with "=" as a
    formula - and loses the characters a worksheet can't hold (see
    _FORBIDDEN_IN_WORKSHEETS; tabs and line breaks stay). The headers
    are fixed labels, never text a user typed, so they are not cleaned.
    """
    date_format = regional.current().date_placeholder
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row_index, row in enumerate(rows, start=2):
        for column_index, value in enumerate(row, start=1):
            cell = sheet.cell(row=row_index, column=column_index)
            if value is None:
                continue
            if isinstance(value, str):
                cell.value = _FORBIDDEN_IN_WORKSHEETS.sub("", value)
                cell.data_type = TYPE_STRING
                continue
            cell.value = value
            if isinstance(value, Decimal):
                cell.number_format = "0.00"
            elif isinstance(value, date):
                cell.number_format = date_format
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
    workbook.save(response)
    return response


def _export_response(fmt, filename, headers, rows):
    """Pass real values in `rows` (Decimal, date, None), never str():
    the writers format them."""
    if fmt == "xlsx":
        return _xlsx_response(filename, headers, rows)
    return _csv_response(filename, headers, rows)
