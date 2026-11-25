"""Small pieces several view modules share: column sorting, paging, the
safe "next" redirect, plurals, and what the Watched-expenses picker
needs on two pages."""

from django.core.paginator import Paginator
from django.db.models import Case, DecimalField, F, When
from django.utils.http import url_has_allowed_host_and_scheme

from ..models import Category, grouped_categories


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


def apply_sort(request, queryset, sortable_columns):
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


def ordered_with_ties_broken(transactions):
    """`transactions` in the order it is already sorted in, ending with
    the primary key. Rows that tie on every sort column (the same payee,
    say, or every row with nothing in a Payment sort) need one fixed
    order, or the database could put the same row on two pages and leave
    another off both. The export uses it too, so it matches the page it
    was made from."""
    ordering = transactions.query.order_by or transactions.model._meta.ordering
    return transactions.order_by(*ordering, "-pk")


def paged(request, transactions, split):
    """(the rows to show, the page or None) for a list of transactions,
    already sorted.

    Only a `split` list is cut into pages, at ?page=N (a missing or
    wrong number shows the nearest real page). Either way the order ends
    with the primary key (see ordered_with_ties_broken())."""
    transactions = ordered_with_ties_broken(transactions)
    if not split:
        return transactions, None
    page = Paginator(transactions, TRANSACTIONS_PER_PAGE).get_page(request.GET.get("page"))
    return page.object_list, page


def safe_next(request, url):
    """`url` if it stays on this site, else "" - never redirect
    off-site. Django's own check also refuses tricks a plain "starts
    with /" test lets through, like `/\\evil.com`, which browsers read
    as `//evil.com`."""
    allowed = url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return url if allowed else ""


def plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


def watched_expenses_picker_context(profile):
    """What the Watched-expenses category picker needs, on Settings and
    in the welcome wizard: the expense categories in groups, the ones
    already chosen, and the button's label."""
    chosen = list(profile.watched_expenses_categories.select_related("parent"))
    if not chosen:
        button_label = "No categories chosen"
    elif len(chosen) == 1:
        button_label = chosen[0].full_name
    else:
        button_label = f"{len(chosen)} categories selected"
    return {
        "watched_category_groups": grouped_categories(
            Category.objects.filter(
                category_type=Category.CategoryType.EXPENSE, account__isnull=True
            )
        ),
        "watched_selected_values": {str(category.pk) for category in chosen},
        "watched_button_label": button_label,
    }
