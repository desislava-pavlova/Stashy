"""The payees pages: the list (each payee's categories, number of uses
and totals), add/edit/delete, and a payee's detail. Names are compared
through `name_key`, so "lidl" and "LIDL" are one payee.
"""

from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, UpdateView

from ..content import PAYEE_TIPS
from ..forms import PayeeForm
from ..models import Category, Payee, Transaction, categories_alphabetical
from ..money import ZERO
from ._helpers import _as_id


PAYEE_SORT_KEYS = {
    "name": lambda row: row["payee"].name.lower(),
    "category": lambda row: row["categories_label"].lower(),
    "amount": lambda row: row["total_sort_value"],
    "usage": lambda row: row["usage_count"],
}


def payee_list(request):
    """The payee list: one row per payee with its categories, number of
    uses and totals, narrowed by the search boxes and sorted by the
    clicked header."""
    search = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "")
    memo_filter = request.GET.get("memo", "").strip()

    payees = Payee.objects.all()
    if search:
        payees = payees.filter(name__icontains=search)
    if category_id == "none":
        payees = payees.filter(transactions__category__isnull=True).distinct()
    elif (category_pk := _as_id(category_id)) is not None:
        payees = payees.filter(transactions__category_id=category_pk).distinct()
    if memo_filter:
        payees = payees.filter(transactions__memo__icontains=memo_filter).distinct()
    payees = list(payees)
    payee_ids = [p.pk for p in payees]

    # One query per kind of data for the whole page, not one per payee:
    # the list can hold hundreds of payees, so walking each payee's own
    # history would be far too slow.
    categories_by_payee = {}
    category_rows = (
        Transaction.objects.filter(payee_id__in=payee_ids)
        .values("payee_id", "category_id", "category__name", "category__parent__name")
        # Transaction's default ordering would add its date, number
        # and created-at columns to the DISTINCT and repeat each
        # category once per transaction.
        .order_by()
        .distinct()
    )
    for row in category_rows:
        label = (
            "Uncategorized"
            if row["category_id"] is None
            else (
                f"{row['category__parent__name']} > {row['category__name']}"
                if row["category__parent__name"]
                else row["category__name"]
            )
        )
        categories_by_payee.setdefault(row["payee_id"], []).append(label)

    usage_by_payee = {
        row["payee_id"]: row["count"]
        for row in Transaction.objects.filter(payee_id__in=payee_ids)
        .values("payee_id")
        .annotate(count=Count("id"))
    }

    totals_by_payee = {}
    for row in (
        Transaction.objects.filter(payee_id__in=payee_ids)
        .values("payee_id", "account__currency")
        .annotate(total=Sum("amount"))
    ):
        totals_by_payee.setdefault(row["payee_id"], []).append(
            (row["account__currency"], row["total"])
        )

    rows = []
    for payee in payees:
        labels = sorted(categories_by_payee.get(payee.pk, []))
        totals = sorted(totals_by_payee.get(payee.pk, []))
        rows.append(
            {
                "payee": payee,
                "categories": labels,
                "categories_label": ", ".join(
                    labels
                ),  # sort key only - the template shows one per row
                "category_count": len(labels),
                "totals": totals,
                # Currency-blind, for sort order only: never shown as an
                # amount.
                "total_sort_value": sum((amount for _, amount in totals), ZERO),
                "usage_count": usage_by_payee.get(payee.pk, 0),
            }
        )

    sort = request.GET.get("sort", "name")
    if sort.removeprefix("-") not in PAYEE_SORT_KEYS:
        # A hand-typed or old link: sort by name, and say so, so the
        # Payee header shows its arrow.
        sort = "name"
    rows.sort(key=PAYEE_SORT_KEYS[sort.removeprefix("-")], reverse=sort.startswith("-"))
    sort_next = {name: (f"-{name}" if sort == name else name) for name in PAYEE_SORT_KEYS}

    return render(
        request,
        "finance/payee_list.html",
        {
            "rows": rows,
            "search": search,
            "selected_category": category_id,
            "memo_filter": memo_filter,
            "categories": categories_alphabetical(
                Category.objects.select_related("parent").exclude(account__isnull=False)
            ),
            "sort": sort,
            "sort_next": sort_next,
        },
    )


def payee_detail(request, pk):
    """Every category ever used with this payee, and the most recent
    memo/amount in each."""
    payee = get_object_or_404(Payee, pk=pk)
    return render(
        request, "finance/payee_detail.html", {"payee": payee, "history": payee.category_history()}
    )


class PayeeCreateView(CreateView):
    model = Payee
    form_class = PayeeForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:payee_list")
    extra_context = {"title": "Add memorized payee", "tips": PAYEE_TIPS}


class PayeeUpdateView(UpdateView):
    model = Payee
    form_class = PayeeForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:payee_list")
    extra_context = {"title": "Edit payee", "tips": PAYEE_TIPS}


class PayeeDeleteView(DeleteView):
    model = Payee
    template_name = "finance/generic_confirm_delete.html"
    success_url = reverse_lazy("finance:payee_list")
