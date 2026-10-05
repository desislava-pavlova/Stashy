from datetime import date
from urllib.parse import urlencode

from dateutil.relativedelta import relativedelta
from django.contrib import messages
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import DeleteView

from audit.context import system_action

from .. import regional
from ..content import TRANSACTION_TIPS
from ..currency import format_money
from ..forms import DateRangeFilterForm, TransactionForm
from ..models import Account, Category, Profile, Reminder, Transaction, grouped_categories
from ..money import ZERO
from ..reporting import resolve_range
from ._helpers import _paged, _safe_next, _sorted

# The transaction list adds "All time" to Reports' own date-range presets - a register
# naturally wants an unbounded view sometimes, unlike a chart that needs a range.
TRANSACTION_RANGE_CHOICES = [("", "All time"), *DateRangeFilterForm.RANGE_CHOICES]

TRANSACTION_LIST_SORT_FIELDS = {
    "date": ["date", "transaction_number"],
    "num": ["transaction_number"],
    "account": ["account__name"],
    "payee": ["payee__name"],
    "category": ["category__name"],
    "memo": ["memo"],
    "amount": ["amount"],
}


def _iso_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _longer_than_a_year(start, end):
    """Whether a date range is long enough to be split into pages: longer than one year, or
    open at either end ("All time"). A whole calendar year, 01/01 to 31/12, is not."""
    return start is None or end is None or end >= start + relativedelta(years=1)


def _filtered_transactions(request):
    """Every filter the transaction list applies, shared with its CSV/XLSX export so what you
    export always matches what you're currently looking at."""
    transactions = Transaction.objects.select_related("account", "category__parent", "payee")

    account_id = request.GET.get("account", "")
    # Category is multi-select - one or more "category" values, any mix of digit category ids
    # and the literal "none" for uncategorized, matched with OR.
    category_values = request.GET.getlist("category")
    status = request.GET.get("status", "")
    search = request.GET.get("q", "").strip()
    with_subcategories = request.GET.get("with_subcategories") == "1"
    include_uncategorized = "none" in category_values
    selected_categories = list(
        Category.objects.filter(pk__in=[v for v in category_values if v.isdigit()]).select_related("parent")
    )

    range_key = request.GET.get("range", "")
    start = _iso_date(request.GET.get("start"))
    end = _iso_date(request.GET.get("end"))
    if range_key and range_key != "custom":
        start, end, _ = resolve_range({"range": range_key})
    elif "range" not in request.GET and start is None and end is None:
        # Default view: this year, until you explicitly ask for something else - "range"
        # being present at all (even "" for "All time") means you already did.
        start, end, _ = resolve_range({"range": "this_year"})
        range_key = "this_year"
    elif start or end:
        range_key = "custom"

    if account_id.isdigit():
        transactions = transactions.filter(account_id=account_id)
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
        transactions = transactions.filter(Q(payee__name__icontains=search) | Q(memo__icontains=search))

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


def transaction_list(request):
    transactions, filters = _filtered_transactions(request)
    transactions, sort, sort_next = _sorted(request, transactions, TRANSACTION_LIST_SORT_FIELDS)
    # Every page shows the total of everything the filters match, so it's taken before paging.
    # It mixes currencies across accounts without conversion, same known simplification as the
    # accounts page's own net worth total (D2) - accurate for a single-currency setup.
    total_amount = transactions.aggregate(total=Sum("amount"))["total"] or ZERO
    rows, page_obj = _paged(request, transactions, split=_longer_than_a_year(filters["start"], filters["end"]))

    category_groups = grouped_categories(Category.objects.select_related("parent"))

    selected_categories = filters["selected_categories"]
    include_uncategorized = filters["include_uncategorized"]
    picked_count = len(selected_categories) + (1 if include_uncategorized else 0)
    if picked_count == 0:
        category_filter_label = "All categories"
    elif picked_count == 1:
        category_filter_label = (
            "Uncategorized" if include_uncategorized and not selected_categories else selected_categories[0].full_name
        )
    else:
        category_filter_label = f"{picked_count} categories selected"

    context = {
        "transactions": rows,
        "page_obj": page_obj,
        "transaction_count": page_obj.paginator.count if page_obj else len(rows),
        "total_amount": total_amount,
        # Mixed currencies have no one true symbol either - the main currency is the same
        # stand-in the accounts page's own net worth total already uses (D2).
        "main_currency": Profile.for_user(request.user).main_currency,
        "accounts": Account.objects.all(),
        # Income -> Expense -> Transfer, each main category with its own subcategories right
        # under it - the category list page's own grouping, reused so the checkbox dropdown
        # reads exactly like that page.
        "category_groups": category_groups,
        "category_filter_label": category_filter_label,
        # Edit mode's per-row category <select> - same categories, same order, as the checkbox
        # filter above (just flattened and stripped of transfer/system ones, which - like the
        # transaction form - aren't picked by hand); built from the groups already fetched
        # above rather than a second query.
        "edit_categories": [
            category for group in category_groups for category in group["categories"] if category.account_id is None
        ],
        "selected_account": filters["account_id"],
        "selected_category_values": set(filters["category_values"]),
        "include_uncategorized": include_uncategorized,
        # The "+ subcategories" chip only means something when it's unambiguous which one
        # category it's talking about - still how the dashboard's own drill-down link (the
        # only thing that sets with_subcategories) always arrives here, one category at a time.
        "selected_category_obj": (
            selected_categories[0]
            if filters["with_subcategories"] and len(selected_categories) == 1 and not filters["include_uncategorized"]
            else None
        ),
        "with_subcategories": filters["with_subcategories"],
        "status": filters["status"],
        "range": filters["range_key"],
        "range_choices": TRANSACTION_RANGE_CHOICES,
        "start": filters["start"],
        "end": filters["end"],
        "sort": sort,
        "sort_next": sort_next,
        "search": filters["search"],
    }
    return render(request, "finance/transaction_list.html", context)


def transaction_form_view(request, pk=None):
    instance = get_object_or_404(Transaction, pk=pk) if pk else None
    next_url = _safe_next(request, request.POST.get("next") or request.GET.get("next", ""))
    reminder = None
    reminder_id = request.POST.get("reminder") or request.GET.get("reminder")
    # Only for a brand-new transaction - editing an existing one never carries a reminder link.
    if reminder_id and not instance:
        reminder = get_object_or_404(Reminder, pk=reminder_id)
    initial = {}
    if instance and instance.payee_id:
        initial["payee_name"] = instance.payee.name
    if not instance:
        if reminder:
            # A reminder's "Enter" - review/adjust before saving, rather than silently creating
            # the transaction, so a reminder with no fixed amount can still be entered.
            initial["account"] = reminder.account_id
            if reminder.payee_id:
                initial["payee_name"] = reminder.payee.name
            initial["category"] = reminder.category_id
            initial["amount"] = reminder.signed_amount()
            # Prefer the memo you actually used last time for this exact payee + category
            # combo (the payee's memorized history) over the reminder's own generic notes,
            # if one exists.
            remembered_memo = None
            if reminder.payee_id and reminder.category_id:
                match = next(
                    (t for t in reminder.payee.category_history() if t.category_id == reminder.category_id), None
                )
                remembered_memo = match.memo if match else None
            initial["memo"] = remembered_memo or reminder.notes
            initial["date"] = reminder.next_due_date
            initial["transaction_number"] = Transaction.next_number(reminder.next_due_date)
        else:
            default_account = request.GET.get("account")
            if default_account:
                initial["account"] = default_account
            # "Save and add another" hands back the date just used; anything unparseable
            # falls back to today.
            try:
                initial["date"] = date.fromisoformat(request.GET.get("date", ""))
            except ValueError:
                initial["date"] = date.today()
            # A preview of what Stashy would assign anyway - change the date and clear the
            # number to have it recomputed for that date's year instead.
            initial["transaction_number"] = Transaction.next_number(initial["date"])

    if request.method == "POST":
        form = TransactionForm(request.POST, instance=instance, initial=initial)
        if form.is_valid():
            account = form.cleaned_data["account"]
            if not account.is_active and request.POST.get("confirm_inactive_account") != "1":
                # Don't silently reactivate, or silently block, an inactive account someone
                # is trying to use - ask, and only act once they say yes.
                return render(
                    request,
                    "finance/transaction_form.html",
                    {
                        "form": form,
                        "instance": instance,
                        "tips": TRANSACTION_TIPS,
                        "next": next_url,
                        "reminder": reminder,
                        "inactive_account_confirm": account,
                    },
                )
            if not account.is_active:
                with system_action(f"Account '{account.name}' made active again by entering a transaction on it"):
                    account.is_active = True
                    account.save(update_fields=["is_active"])
            txn = form.save()
            if reminder:
                reminder.mark_entered(txn)
                if reminder.frequency == Reminder.Frequency.ONE_TIME:
                    messages.success(request, f"Entered {reminder}.")
                else:
                    moved_to = regional.format_date(reminder.next_due_date)
                    messages.success(request, f"Entered {reminder} and moved it to {moved_to}.")
                return redirect("finance:reminder_list")
            messages.success(request, "Transaction saved.")
            if not instance:
                if "save_and_add" in request.POST:
                    # Straight back to a blank form on the same account, ready for the next one.
                    query = {"account": txn.account_id, "date": txn.date.isoformat()}
                    if next_url:
                        query["next"] = next_url
                    return redirect(f"{reverse('finance:transaction_add')}?{urlencode(query)}")
                return redirect("finance:transaction_list")
            # Editing: back to wherever you opened this from (the account register or the
            # all-accounts list) if we know it; otherwise the transaction's own account.
            return redirect(next_url or reverse("finance:account_detail", kwargs={"pk": txn.account_id}))
    else:
        form = TransactionForm(instance=instance, initial=initial)

    return render(
        request,
        "finance/transaction_form.html",
        {
            "form": form,
            "instance": instance,
            "tips": TRANSACTION_TIPS,
            "next": next_url,
            "reminder": reminder,
        },
    )


def _quick_edit_row(txn):
    """The formatted values Edit mode swaps back in for a row's view-mode cells once a
    field's autosave succeeds - same formatting the plain list rendering uses, so toggling
    Edit mode off shows exactly what a full page reload would."""
    return {
        "date": regional.format_date(txn.date),
        "transaction_number": txn.transaction_number if txn.transaction_number is not None else "—",
        "account_name": txn.account.name,
        "payee": txn.display_payee,
        "category_name": txn.category.full_name if txn.category_id else "—",
        "memo": txn.memo,
        "payment": format_money(-txn.amount, txn.account.currency) if txn.amount < 0 else "",
        "deposit": format_money(txn.amount, txn.account.currency) if txn.amount > 0 else "",
        "reconciled": txn.reconciled,
    }


@require_POST
def transaction_quick_edit(request, pk):
    """Autosaves one field-change from the transactions list's Edit mode - reuses
    TransactionForm so validation and payee resolution stay identical to the full edit page,
    just without a page navigation."""
    instance = get_object_or_404(Transaction, pk=pk)
    form = TransactionForm(request.POST, instance=instance)
    if not form.is_valid():
        errors = {field: list(messages) for field, messages in form.errors.items()}
        return JsonResponse({"ok": False, "errors": errors}, status=400)
    account = form.cleaned_data["account"]
    if not account.is_active:
        # The full edit page offers a confirm-and-reactivate dialog for this; Edit mode's
        # per-row autosave has no room for a confirmation step, so it points you there instead
        # of silently reactivating an account nobody asked to reactivate.
        return JsonResponse(
            {
                "ok": False,
                "errors": {"account": ["This account is inactive - use the full edit page to reactivate it."]},
            },
            status=400,
        )
    txn = form.save()
    return JsonResponse({"ok": True, "row": _quick_edit_row(txn)})


class TransactionDeleteView(DeleteView):
    model = Transaction
    template_name = "finance/generic_confirm_delete.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["next"] = _safe_next(self.request, self.request.GET.get("next", ""))
        return ctx

    def get_success_url(self):
        # Back to wherever the delete link was opened from (e.g. the all-accounts list),
        # same as editing already does - otherwise falls back to the transaction's own account.
        next_url = _safe_next(self.request, self.request.POST.get("next", ""))
        return next_url or reverse("finance:account_detail", kwargs={"pk": self.object.account_id})
