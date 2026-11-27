"""CSV/XLSX downloads: the four list exports (transactions, accounts,
categories, payees) and the one view behind every report's Export
button.

Every export builds rows of real values (Decimal, date, None) and hands
them to _export_response(), which formats them for the chosen file type.
"""

import codecs
import csv
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import NamedTuple

from django.http import Http404, HttpResponse
from django.utils import timezone

from openpyxl import Workbook
from openpyxl.cell.cell import TYPE_STRING

from .. import regional
from ..filters import filtered_transactions
from ..models import Account, Category, Payee
from ..report_tables import (
    income_expense_table,
    income_expense_vs_average_table,
    income_table,
    income_vs_average_table,
    net_worth_table,
    spending_table,
    spending_vs_average_table,
    watched_expenses_table,
)
from ._helpers import (
    TRANSACTION_LIST_SORT_FIELDS,
    apply_sort,
    ordered_with_ties_broken,
    report_inputs,
)


def _filename(stem):
    """stashy-<stem>-2026-10-03_14-32-05: the time too, so several
    exports on one day get their own names (the same date-and-time style
    as backups)."""
    return f"stashy-{stem}-{timezone.localtime():%Y-%m-%d_%H-%M-%S}"


def _requested_format(request):
    return request.GET.get("format", "csv")


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
    response.write(codecs.BOM_UTF8)
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

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
    workbook.save(response)
    return response


def _export_response(fmt, filename, headers, rows):
    """Pass real values in `rows` (Decimal, date, None), never str():
    the writers format them."""
    if fmt == "xlsx":
        return _xlsx_response(filename, headers, rows)
    return _csv_response(filename, headers, rows)


# ----------------------------------------------------------------------
# List exports
# ----------------------------------------------------------------------


def export_transactions(request):
    """Exports whatever the transaction list is currently
    filtered/sorted to - same filters, so what you get matches what you
    were looking at."""
    transactions, _filters = filtered_transactions(request)
    transactions, _sort, _sort_next = apply_sort(
        request, transactions, TRANSACTION_LIST_SORT_FIELDS
    )
    transactions = ordered_with_ties_broken(transactions)

    headers = [
        "Date",
        "Number",
        "Account",
        "Payee",
        "Category",
        "Memo",
        "Amount",
        "Currency",
        "Reconciled",
    ]

    rows = [
        [
            t.date,
            t.transaction_number,
            t.account.name,
            t.display_payee,
            t.category.full_name if t.category_id else "",
            t.memo,
            t.amount,
            t.account.currency,
            "Y" if t.reconciled else "N",
        ]
        for t in transactions
    ]

    return _export_response(_requested_format(request), _filename("transactions"), headers, rows)


def export_accounts(request):
    headers = ["Name", "Type", "Currency", "Institution", "Active", "Balance"]
    rows = [
        [
            a.name,
            a.get_account_type_display(),
            a.currency,
            a.institution,
            "Y" if a.is_active else "N",
            a.balance,
        ]
        for a in Account.objects.with_balances()
    ]
    return _export_response(_requested_format(request), _filename("accounts"), headers, rows)


def export_categories(request):
    # A full export: system-managed transfer categories are included
    # too, marked as such, rather than silently left out of "export
    # everything".
    categories = Category.objects.select_related("parent", "account")
    headers = ["Name", "Parent", "Type", "Auto-generated", "System-managed"]
    rows = [
        [
            c.name,
            c.parent.name if c.parent_id else "",
            c.get_category_type_display(),
            "Y" if c.is_auto_generated else "N",
            "Y" if c.account_id else "N",
        ]
        for c in categories
    ]
    return _export_response(_requested_format(request), _filename("categories"), headers, rows)


def export_payees(request):
    # A full export: system payees are included too, marked as such.
    payees = Payee.objects.all()
    headers = ["Name", "Active", "System-managed"]
    rows = [
        [
            p.name,
            "Y" if p.is_active else "N",
            "Y" if p.is_system else "N",
        ]
        for p in payees
    ]
    return _export_response(_requested_format(request), _filename("payees"), headers, rows)


# ----------------------------------------------------------------------
# Report exports: one registry, one view
# ----------------------------------------------------------------------
#
# Each report's builder (report_tables.py) makes the same table the
# Reports page shows, from the same filters. A builder takes a
# ReportInputs and returns the table, or None when there is nothing to
# export. Adding a report export means adding one entry to
# REPORT_EXPORTS.


def _category_layout(table, amount_label, total_label):
    """Category rows: one column per period plus the total row, or a
    single amount column when the report isn't subtotaled.
    Subcategories are indented by two spaces."""
    periods = table["periods"]
    headers = ["Category", *([*periods, "Total"] if periods else [amount_label])]

    rows = []
    for group in table["groups"]:
        for row in group["rows"]:
            name = ("  " if row["is_child"] else "") + row["name"]
            rows.append([name] + list(row["values"]) + ([row["total"]] if periods else []))

    if table["totals"]:
        rows.append(
            [total_label]
            + list(table["totals"]["values"])
            + ([table["totals"]["total"]] if periods else [])
        )

    return headers, rows


def _vs_average_layout(table, amount_label, total_label):
    headers = ["Category", "Current", "Average", "Difference"]
    rows = [
        [
            ("  " if row["is_child"] else "") + row["name"],
            row["current"],
            row["average"],
            row["difference"],
        ]
        for group in table["groups"]
        for row in group["rows"]
    ]
    if table["totals"]:
        totals = table["totals"]
        rows.append([total_label, totals["current"], totals["average"], totals["difference"]])
    return headers, rows


def _net_worth_layout(table, amount_label, total_label):
    """A row per account group (its subtotal), each account indented
    under it, then the total row."""
    headers = ["Account"] + table["periods"]
    rows = []
    for group in table["groups"]:
        rows.append([group["name"], *group["totals"]])
        for row in group["rows"]:
            rows.append(["  " + row["name"], *row["values"]])
    rows.append([total_label, *table["totals"]])
    return headers, rows


class ReportExport(NamedTuple):
    build: Callable  # (ReportInputs) -> table, or None when there's nothing to export
    layout: Callable  # (table, amount_label, total_label) -> (headers, rows)
    filename: str  # the file is named stashy-<filename>-<date and time> (see _filename())
    # The single amount column's header when a category report has no
    # subtotal.
    amount_label: str = ""
    # The closing row's name, the one the page's table gives it.
    total_label: str = "Total"
    empty_message: str = ""  # shown with a 404 when build() returns None


# The keys are the URL slugs: reports/export/<slug>/.
REPORT_EXPORTS = {
    "spending": ReportExport(
        build=spending_table,
        layout=_category_layout,
        filename="spending-by-category",
        amount_label="Spending",
    ),
    "spending-vs-average": ReportExport(
        build=spending_vs_average_table,
        layout=_vs_average_layout,
        filename="spending-vs-average",
    ),
    "income": ReportExport(
        build=income_table,
        layout=_category_layout,
        filename="income-by-category",
        amount_label="Income",
    ),
    "income-vs-average": ReportExport(
        build=income_vs_average_table,
        layout=_vs_average_layout,
        filename="income-vs-average",
    ),
    "income-expense": ReportExport(
        build=income_expense_table,
        layout=_category_layout,
        filename="income-expense-by-category",
        amount_label="Amount",
    ),
    "income-expense-vs-average": ReportExport(
        build=income_expense_vs_average_table,
        layout=_vs_average_layout,
        filename="income-expense-vs-average",
        total_label="Net",
    ),
    "net-worth": ReportExport(
        build=net_worth_table,
        layout=_net_worth_layout,
        filename="net-worth",
    ),
    "watched-expenses": ReportExport(
        build=watched_expenses_table,
        layout=_category_layout,
        filename="watched-expenses",
        amount_label="Spending",
        empty_message="No Watched expenses categories are chosen yet - add some in Settings.",
    ),
}


def export_report(request, report):
    """Any report on the Reports page as CSV/XLSX, with the same filters
    and the same currency conversion as the page, so the file matches
    what's on screen."""
    export = REPORT_EXPORTS.get(report)
    if export is None:
        raise Http404("No such report.")
    table = export.build(report_inputs(request))
    if table is None:
        return HttpResponse(export.empty_message, status=404)
    headers, rows = export.layout(table, export.amount_label, export.total_label)
    return _export_response(_requested_format(request), _filename(export.filename), headers, rows)
