"""Small pieces several view modules share: column sorting, the safe "next" redirect, and the
CSV/XLSX writers every export goes through."""

import csv
from datetime import date
from decimal import Decimal

from django.http import HttpResponse
from django.utils.http import url_has_allowed_host_and_scheme

from .. import regional


def _sorted(request, queryset, sortable_columns):
    """Apply ?sort=field / ?sort=-field (toggling ascending/descending) to a queryset.

    Falls back to the model's own default ordering when the column isn't sortable or no
    sort was requested. Returns (queryset, current sort key, {column: next sort key}) —
    the last two are for building column headers that link to the next click's sort order.
    """
    sort = request.GET.get("sort", "")
    column = sort.removeprefix("-")
    if column in sortable_columns:
        order = [f"-{field}" if sort.startswith("-") else field for field in sortable_columns[column]]
        queryset = queryset.order_by(*order)
    else:
        sort = ""
    sort_next = {name: (f"-{name}" if sort == name else name) for name in sortable_columns}
    return queryset, sort, sort_next


def _safe_next(request, url):
    """`url` if it stays on this site, else "" - never redirect off-site. Django's own check
    also refuses tricks a plain "starts with /" test lets through, like `/\\evil.com`, which
    browsers read as `//evil.com`."""
    allowed = url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return url if allowed else ""


def _plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


# ---------------------------------------------------------------------------
# Export writers - CSV built in; XLSX via openpyxl
# ---------------------------------------------------------------------------

# Excel, LibreOffice and Google Sheets treat a cell starting with one of these as a formula, and
# imported bank text can start with anything - so text cells that do get a leading apostrophe in
# CSV, the usual "this is text" marker (OWASP's advice for CSV injection).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value):
    """One export value as CSV text: amounts with exactly two decimals, ISO dates (what
    Stashy's own import reads back), and text made safe from formula injection."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _csv_response(filename, headers, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    # A BOM first, or Excel guesses the wrong encoding and shows Cyrillic as garbage.
    response.write("﻿")
    writer = csv.writer(response)
    writer.writerow(headers)
    writer.writerows([_csv_cell(value) for value in row] for row in rows)
    return response


def _xlsx_response(filename, headers, rows):
    """Amounts go in as real numbers and dates as real dates, so Excel can add them up and sort
    them. Text is always stored as text - openpyxl would otherwise write a string starting with
    "=" as a formula."""
    from openpyxl import Workbook
    from openpyxl.cell.cell import TYPE_STRING

    date_format = regional.current().date_placeholder
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row_index, row in enumerate(rows, start=2):
        for column_index, value in enumerate(row, start=1):
            cell = sheet.cell(row=row_index, column=column_index)
            if value is None:
                continue
            cell.value = value
            if isinstance(value, Decimal):
                cell.number_format = "0.00"
            elif isinstance(value, date):
                cell.number_format = date_format
            elif isinstance(value, str):
                cell.data_type = TYPE_STRING
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
    workbook.save(response)
    return response


def _export_response(fmt, filename, headers, rows):
    """Pass real values in `rows` (Decimal, date, None), never str(): the writers format them."""
    if fmt == "xlsx":
        return _xlsx_response(filename, headers, rows)
    return _csv_response(filename, headers, rows)
