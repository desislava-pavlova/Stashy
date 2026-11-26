"""The payees pages: the list (each payee's categories, number of uses
and totals), add/edit/delete, and a payee's detail. Names are compared
through `name_key`, so "lidl" and "LIDL" are one payee.
"""

from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, UpdateView

from ..forms.payees import PayeeForm
from ..ids import as_id
from ..models import Category, Payee, categories_alphabetical, name_key, payee_summaries
from ..tips import PAYEE_TIPS
from ._helpers import next_sort_keys


# Names sort through `name_key`, as the database's own ordering does.
PAYEE_SORT_KEYS = {
    "name": lambda row: row["payee"].name_key,
    "category": lambda row: name_key(row["categories_label"]),
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
    elif (category_pk := as_id(category_id)) is not None:
        payees = payees.filter(transactions__category_id=category_pk).distinct()
    if memo_filter:
        payees = payees.filter(transactions__memo__icontains=memo_filter).distinct()
    rows = payee_summaries(payees)

    sort = request.GET.get("sort", "name")
    if sort.removeprefix("-") not in PAYEE_SORT_KEYS:
        # A hand-typed or old link: sort by name, and say so, so the
        # Payee header shows its arrow.
        sort = "name"
    rows.sort(key=PAYEE_SORT_KEYS[sort.removeprefix("-")], reverse=sort.startswith("-"))
    sort_next = next_sort_keys(PAYEE_SORT_KEYS, sort)

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
