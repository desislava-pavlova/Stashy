"""CSV/XLSX downloads: the four list exports (transactions, accounts, categories, payees) and
the one view behind every report's Export button.

Every export builds rows of real values (Decimal, date, None) and hands them to
_export_response(), which formats them for the chosen file type.
"""

from collections.abc import Callable
from typing import NamedTuple

from django.http import Http404, HttpResponse
from django.utils import timezone

from ..currency import RateTable
from ..models import Account, Category, Payee
from ..reporting import (
    category_report_rows,
    category_vs_average_report_rows,
    net_worth_by_account,
    watched_expenses_category_ids,
)
from ._helpers import _export_response, _sorted
from .reports import _report_filters
from .transactions import TRANSACTION_LIST_SORT_FIELDS, _filtered_transactions


def _filename(stem):
    """stashy-<stem>-2026-10-03_14-32-05: the time too, so several exports on one day get
    their own names (the same date-and-time style as backups)."""
    return f"stashy-{stem}-{timezone.localtime():%Y-%m-%d_%H-%M-%S}"


def _requested_format(request):
    return request.GET.get("format", "csv")


# ---------------------------------------------------------------------------
# List exports
# ---------------------------------------------------------------------------


def export_transactions(request):
    """Exports whatever the transaction list is currently filtered/sorted to - same filters,
    so what you get matches what you were looking at."""
    transactions, _filters = _filtered_transactions(request)
    transactions, _sort, _sort_next = _sorted(request, transactions, TRANSACTION_LIST_SORT_FIELDS)
    headers = ["Date", "Number", "Account", "Payee", "Category", "Memo", "Amount", "Currency", "Reconciled"]
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
        [a.name, a.get_account_type_display(), a.currency, a.institution, "Y" if a.is_active else "N", a.balance]
        for a in Account.objects.with_balances()
    ]
    return _export_response(_requested_format(request), _filename("accounts"), headers, rows)


def export_categories(request):
    # A full export: system-managed transfer categories are included too, marked as such,
    # rather than silently left out of "export everything".
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
    rows = [[p.name, "Y" if p.is_active else "N", "Y" if p.name in Payee.SYSTEM_NAMES else "N"] for p in payees]
    return _export_response(_requested_format(request), _filename("payees"), headers, rows)


# ---------------------------------------------------------------------------
# Report exports: one registry, one view
# ---------------------------------------------------------------------------
#
# Each report's builder makes the same table the Reports page shows, from the same filters.
# A builder takes (filters, rates, profile) and returns the table, or None when there is
# nothing to export. Adding a report export means adding one line to REPORT_EXPORTS.


def _category_rows(**kind):
    """A builder for a "category rows" report; `kind` is expense_only=True, income_only=True,
    or nothing for income and expense together."""

    def build(filters, rates, profile):
        return category_report_rows(
            filters["start"],
            filters["end"],
            subtotal=filters["subtotal"],
            account=filters["account"],
            rates=rates,
            **kind,
        )

    return build


def _vs_average_rows(**kind):
    """A builder for a "current vs. average" report; `kind` as in _category_rows()."""

    def build(filters, rates, profile):
        return category_vs_average_report_rows(
            filters["start"], filters["end"], filters["periods"], account=filters["account"], rates=rates, **kind
        )

    return build


def _net_worth(filters, rates, profile):
    return net_worth_by_account(filters["start"], filters["end"], filters["subtotal"] or "month", rates=rates)


def _watched_expenses(filters, rates, profile):
    category_ids = watched_expenses_category_ids(profile)
    if not category_ids:
        return None
    return category_report_rows(
        filters["start"],
        filters["end"],
        subtotal=filters["subtotal"],
        expense_only=True,
        account=filters["account"],
        category_ids=category_ids,
        rates=rates,
    )


def _category_layout(table, amount_label):
    """Category rows: one column per period plus Total, or a single amount column when the
    report isn't subtotaled. Subcategories are indented by two spaces."""
    periods = table["periods"]
    headers = ["Category", *([*periods, "Total"] if periods else [amount_label])]
    rows = []
    for group in table["groups"]:
        for row in group["rows"]:
            name = ("  " if row["is_child"] else "") + row["name"]
            rows.append([name] + list(row["values"]) + ([row["total"]] if periods else []))
    if table["totals"]:
        rows.append(["Total"] + list(table["totals"]["values"]) + ([table["totals"]["total"]] if periods else []))
    return headers, rows


def _vs_average_layout(table, amount_label):
    headers = ["Category", "Current", "Average", "Difference"]
    rows = [
        [("  " if row["is_child"] else "") + row["name"], row["current"], row["average"], row["difference"]]
        for group in table["groups"]
        for row in group["rows"]
    ]
    if table["totals"]:
        totals = table["totals"]
        rows.append(["Total", totals["current"], totals["average"], totals["difference"]])
    return headers, rows


def _net_worth_layout(table, amount_label):
    """A row per account group (its subtotal), each account indented under it, then Total."""
    headers = ["Account"] + table["periods"]
    rows = []
    for group in table["groups"]:
        rows.append([group["name"], *group["totals"]])
        for row in group["rows"]:
            rows.append(["  " + row["name"], *row["values"]])
    rows.append(["Total", *table["totals"]])
    return headers, rows


class ReportExport(NamedTuple):
    build: Callable  # (filters, rates, profile) -> table, or None when there's nothing to export
    layout: Callable  # (table, amount_label) -> (headers, rows)
    filename: str  # the file is named stashy-<filename>-<YYYYMMDD>
    amount_label: str = ""  # the single amount column's header when a category report has no subtotal
    empty_message: str = ""  # shown with a 404 when build() returns None


# The keys are the URL slugs: reports/export/<slug>/.
REPORT_EXPORTS = {
    "spending": ReportExport(_category_rows(expense_only=True), _category_layout, "spending-by-category", "Spending"),
    "spending-vs-average": ReportExport(
        _vs_average_rows(expense_only=True), _vs_average_layout, "spending-vs-average"
    ),
    "income": ReportExport(_category_rows(income_only=True), _category_layout, "income-by-category", "Income"),
    "income-vs-average": ReportExport(_vs_average_rows(income_only=True), _vs_average_layout, "income-vs-average"),
    "income-expense": ReportExport(_category_rows(), _category_layout, "income-expense-by-category", "Amount"),
    "income-expense-vs-average": ReportExport(_vs_average_rows(), _vs_average_layout, "income-expense-vs-average"),
    "net-worth": ReportExport(_net_worth, _net_worth_layout, "net-worth"),
    "watched-expenses": ReportExport(
        _watched_expenses,
        _category_layout,
        "watched-expenses",
        "Spending",
        empty_message="No Watched expenses categories are chosen yet - add some in Settings.",
    ),
}


def export_report(request, report):
    """Any report on the Reports page as CSV/XLSX, with the same filters and the same
    currency conversion as the page, so the file matches what's on screen."""
    export = REPORT_EXPORTS.get(report)
    if export is None:
        raise Http404("No such report.")
    profile = request.stashy_profile
    table = export.build(_report_filters(request), RateTable(profile.main_currency), profile)
    if table is None:
        return HttpResponse(export.empty_message, status=404)
    headers, rows = export.layout(table, export.amount_label)
    return _export_response(_requested_format(request), _filename(export.filename), headers, rows)
