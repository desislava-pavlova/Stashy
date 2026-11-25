"""Reading the filters a page asks for in its address.

The Transactions list and its export share `filtered_transactions()`;
Reports and its exports share `report_filters()`. One reader each, so
a downloaded file always matches the page it was made from. Nothing
here builds a response or touches a template.
"""

from django.db.models import Q

from . import regional
from .forms import DateRangeFilterForm
from .ids import as_id, as_ids
from .models import Category, Transaction
from .reporting import (
    DEFAULT_LOOKBACK_PERIODS,
    DEFAULT_RANGE_KEY,
    EARLIEST_RANGE_KEY,
    resolve_range,
    trimmed_average_periods,
)


def report_filters(request):
    """Shared by the reports page and the report export view, so both
    read the exact same filters off the querystring - what you export
    always matches what's on screen.

    On an invalid submission, `form` is swapped for a fresh, unbound one
    so the widgets show the defaults actually being used rather than
    echoing back the rejected values; the original errors are kept
    separately in "form_errors" for the page's warning banner."""
    submitted = DateRangeFilterForm(request.GET or None)
    invalid = bool(request.GET) and not submitted.is_valid()
    filters = submitted.cleaned_data if submitted.is_valid() else {}
    account = filters.get("account")
    start, end, interval = resolve_range(filters, account=account)
    # "Average interval" is how many earlier periods to average over -
    # see reporting.vs_average_periods() for what an earlier period is.
    try:
        lookback = int(interval)
    except (TypeError, ValueError):
        lookback = DEFAULT_LOOKBACK_PERIODS
    range_key = filters.get("range") or DEFAULT_RANGE_KEY
    periods = trimmed_average_periods(start, end, lookback, account=account, range_key=range_key)
    return {
        "form": DateRangeFilterForm() if invalid else submitted,
        "form_errors": submitted.errors if invalid else None,
        "start": start,
        "end": end,
        "lookback": lookback,
        "periods": periods,
        "average_periods": len(periods),
        # One earlier period isn't an average, and none at all is
        # nothing to compare with - the vs-average reports say so
        # instead of drawing one.
        "has_average": len(periods) >= 2,
        "subtotal": filters.get("subtotal") or "",
        "account": account,
    }


def filtered_transactions(request):
    """Every filter the transaction list applies, shared with its
    CSV/XLSX export so what you export always matches what you're
    currently looking at."""
    transactions = Transaction.objects.select_related("account", "category__parent", "payee")

    account_id = request.GET.get("account", "")
    # Category is multi-select - one or more "category" values, any mix
    # of digit category ids and the literal "none" for uncategorized,
    # matched with OR.
    category_values = request.GET.getlist("category")
    status = request.GET.get("status", "")
    search = request.GET.get("q", "").strip()
    with_subcategories = request.GET.get("with_subcategories") == "1"
    include_uncategorized = "none" in category_values
    selected_categories = list(
        Category.objects.filter(pk__in=as_ids(category_values)).select_related("parent")
    )

    range_key = request.GET.get("range", "")
    if range_key == EARLIEST_RANGE_KEY:
        range_key = ""  # a bookmark from before "Earliest to date" became "All dates"
    start = regional.parse_date(request.GET.get("start"))
    end = regional.parse_date(request.GET.get("end"))
    if range_key == "custom":
        pass
    elif range_key:
        start, end, _ = resolve_range({"range": range_key})
    elif "range" in request.GET:
        # "All dates". The form still sends its date boxes, holding the
        # previous range's dates.
        start = end = None
    elif start is None and end is None:
        # Default view: this year, until you explicitly ask for
        # something else.
        start, end, _ = resolve_range({"range": "this_year"})
        range_key = "this_year"
    else:
        range_key = "custom"  # dates without a range: the dashboard's links

    if (account_pk := as_id(account_id)) is not None:
        transactions = transactions.filter(account_id=account_pk)
    if selected_categories or include_uncategorized:
        category_ids = {c.pk for c in selected_categories}
        if with_subcategories:
            for category in selected_categories:
                category_ids.update(sub.pk for sub in category.descendants())
        category_filter = Q(category_id__in=category_ids) if category_ids else Q(pk__in=[])
        if include_uncategorized:
            category_filter |= Q(category__isnull=True)
        transactions = transactions.filter(category_filter)
    if start:
        transactions = transactions.filter(date__gte=start)
    if end:
        transactions = transactions.filter(date__lte=end)
    if status == "reconciled":
        transactions = transactions.filter(reconciled=True)
    elif status == "unreconciled":
        transactions = transactions.filter(reconciled=False)
    if search:
        transactions = transactions.filter(
            Q(payee__name__icontains=search) | Q(memo__icontains=search)
        )

    return transactions, {
        "account_id": account_id,
        "category_values": category_values,
        "selected_categories": selected_categories,
        "include_uncategorized": include_uncategorized,
        "with_subcategories": with_subcategories,
        "status": status,
        "range_key": range_key,
        "start": start,
        "end": end,
        "search": search,
    }
