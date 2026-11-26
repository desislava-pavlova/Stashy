"""The Transactions pages: the filtered, sorted and paged list; add,
edit and delete (a reminder's Enter lands on the same form); and the
quick save behind the list's Edit mode. The list's filters are read by
`filtered_transactions()` in `filters.py`, which the export shares.
"""

from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import DeleteView

from dateutil.relativedelta import relativedelta

from audit.context import system_action

from .. import regional
from ..currency import format_money
from ..filters import filtered_transactions
from ..forms.filters import DateRangeFilterForm
from ..forms.transactions import TransactionForm
from ..ids import as_id
from ..models import Account, Category, Reminder, Transaction, grouped_categories
from ..money import to_cent
from ..reporting import EARLIEST_RANGE_KEY
from ..tips import TRANSACTION_TIPS
from ._helpers import AMOUNT_SORT_COLUMNS, apply_sort, paged, safe_next


# Reports' date-range presets, plus "All dates": no date limits at all,
# so even a transaction dated in the future by mistake shows up. It
# takes the place of Reports' "Earliest to date", which a chart needs
# (it has to start somewhere) but a register doesn't.
TRANSACTION_RANGE_CHOICES = [
    ("", "All dates"),
    *(choice for choice in DateRangeFilterForm.RANGE_CHOICES if choice[0] != EARLIEST_RANGE_KEY),
]

TRANSACTION_LIST_SORT_FIELDS = {
    "date": ["date", "transaction_number"],
    "num": ["transaction_number"],
    "account": ["account__name"],
    "payee": ["payee__name_key"],
    "category": ["category__name_key"],
    "memo": ["memo"],
    **AMOUNT_SORT_COLUMNS,
}


def _longer_than_a_year(start, end):
    """Whether a date range is long enough to be split into pages:
    longer than one year, or open at either end ("All time"). A whole
    calendar year, 01/01 to 31/12, is not."""
    if start is None or end is None:
        return True
    try:
        return end >= start + relativedelta(years=1)
    except (ValueError, OverflowError):
        # A start in year 9999 has no "a year later": nothing can be
        # longer than a year after it.
        return False


def transaction_list(request):
    transactions, filters = filtered_transactions(request)
    transactions, sort, sort_next = apply_sort(request, transactions, TRANSACTION_LIST_SORT_FIELDS)
    # Every page shows the total of everything the filters match, so
    # it's taken before paging. It adds amounts across accounts
    # without converting currencies, so it is only exact for a
    # single-currency stash.
    total_amount = to_cent(transactions.aggregate(total=Sum("amount"))["total"])
    rows, page_obj = paged(
        request, transactions, split=_longer_than_a_year(filters["start"], filters["end"])
    )

    category_groups = grouped_categories(Category.objects.select_related("parent"))

    selected_categories = filters["selected_categories"]
    include_uncategorized = filters["include_uncategorized"]
    picked_count = len(selected_categories) + (1 if include_uncategorized else 0)
    if picked_count == 0:
        category_filter_label = "All categories"
    elif picked_count == 1:
        category_filter_label = (
            "Uncategorized"
            if include_uncategorized and not selected_categories
            else selected_categories[0].full_name
        )
    else:
        category_filter_label = f"{picked_count} categories selected"

    context = {
        "transactions": rows,
        "page_obj": page_obj,
        "transaction_count": page_obj.paginator.count if page_obj else len(rows),
        "total_amount": total_amount,
        # Mixed currencies have no one true symbol either - the total
        # is shown with the main currency's symbol as a stand-in.
        "main_currency": request.stashy_profile.main_currency,
        "accounts": Account.objects.all(),
        # Income -> Expense -> Transfer, each main category with its own
        # subcategories right under it - the category list page's own
        # grouping, reused so the checkbox dropdown reads exactly like
        # that page.
        "category_groups": category_groups,
        "category_filter_label": category_filter_label,
        # Edit mode's per-row category <select> - same categories, same
        # order, as the checkbox filter above (just flattened and
        # stripped of transfer/system ones, which - like the transaction
        # form - aren't picked by hand); built from the groups already
        # fetched above rather than a second query.
        "edit_categories": [
            category
            for group in category_groups
            for category in group["categories"]
            if category.account_id is None
        ],
        "selected_account": filters["account_id"],
        "selected_category_values": set(filters["category_values"]),
        "include_uncategorized": include_uncategorized,
        # The "+ subcategories" chip only means something when it's
        # unambiguous which one category it's talking about - still how
        # the dashboard's own drill-down link (the only thing that sets
        # with_subcategories) always arrives here, one category at a
        # time.
        "selected_category_obj": (
            selected_categories[0]
            if filters["with_subcategories"]
            and len(selected_categories) == 1
            and not filters["include_uncategorized"]
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


def initial_from_reminder(reminder):
    """The form's starting values for entering `reminder` as a
    transaction. It is reviewed and adjusted before it is saved, rather
    than created silently, so a reminder with no fixed amount can still
    be entered."""
    memo = ""
    if reminder.payee_id and reminder.category_id:
        # What you wrote last time for this payee in this category beats
        # the reminder's own generic notes.
        memo = reminder.payee.remembered_memo(reminder.category_id)
    initial = {
        "account": reminder.account_id,
        "category": reminder.category_id,
        "amount": reminder.signed_amount(),
        "memo": memo or reminder.notes,
        "date": reminder.next_due_date,
        "transaction_number": Transaction.next_number(reminder.next_due_date),
    }
    if reminder.payee_id:
        initial["payee_name"] = reminder.payee.name
    return initial


def transaction_form_view(request, pk=None):
    instance = get_object_or_404(Transaction, pk=pk) if pk else None
    next_url = safe_next(request, request.POST.get("next") or request.GET.get("next", ""))
    reminder = None
    reminder_id = request.POST.get("reminder") or request.GET.get("reminder")
    # Only for a brand-new transaction - editing an existing one never
    # carries a reminder link.
    if reminder_id and not instance:
        reminder = get_object_or_404(Reminder, pk=as_id(reminder_id))
    initial = {}
    if instance and instance.payee_id:
        initial["payee_name"] = instance.payee.name
    if not instance:
        if reminder:
            initial.update(initial_from_reminder(reminder))
        else:
            default_account = request.GET.get("account")
            if default_account:
                initial["account"] = default_account
            # "Save and add another" hands back the date just used;
            # anything unparseable falls back to today.
            try:
                initial["date"] = date.fromisoformat(request.GET.get("date", ""))
            except ValueError:
                initial["date"] = timezone.localdate()
            # A preview of what Stashy would assign anyway - change the
            # date and clear the number to have it recomputed for that
            # date's year instead.
            initial["transaction_number"] = Transaction.next_number(initial["date"])

    if request.method == "POST":
        form = TransactionForm(request.POST, instance=instance, initial=initial)
        if form.is_valid():
            account = form.cleaned_data["account"]
            if not account.is_active and request.POST.get("confirm_inactive_account") != "1":
                # Don't silently reactivate, or silently block, an
                # inactive account someone is trying to use - ask, and
                # only act once they say yes.
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
                with system_action(
                    f"Account '{account.name}' made active again by entering a transaction on it"
                ):
                    account.is_active = True
                    account.save(update_fields=["is_active"])
            txn = form.save()
            if reminder:
                # Named before mark_entered(): it moves a recurring
                # reminder's due date on, and the message must say which
                # date was paid (the same order reminder_skip uses).
                label = str(reminder)
                reminder.mark_entered(txn)
                if reminder.frequency == Reminder.Frequency.ONE_TIME:
                    messages.success(request, f"Entered {label}.")
                else:
                    moved_to = regional.format_date(reminder.next_due_date)
                    messages.success(request, f"Entered {label} and moved it to {moved_to}.")
                return redirect("finance:reminder_list")
            messages.success(request, "Transaction saved.")
            if not instance:
                if "save_and_add" in request.POST:
                    # Straight back to a blank form on the same account,
                    # ready for the next one.
                    query = {"account": txn.account_id, "date": txn.date.isoformat()}
                    if next_url:
                        query["next"] = next_url
                    return redirect(f"{reverse('finance:transaction_add')}?{urlencode(query)}")
                return redirect("finance:transaction_list")
            # Editing: back to wherever you opened this from (the
            # account register or the all-accounts list) if we know it;
            # otherwise the transaction's own account.
            return redirect(
                next_url or reverse("finance:account_detail", kwargs={"pk": txn.account_id})
            )
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
    """The formatted values Edit mode swaps back in for a row's
    view-mode cells once a field's autosave succeeds - same formatting
    the plain list rendering uses, so toggling Edit mode off shows
    exactly what a full page reload would."""
    return {
        "date": regional.format_date(txn.date),
        "transaction_number": txn.transaction_number
        if txn.transaction_number is not None
        else "—",
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
    """Autosaves one field-change from the transactions list's Edit
    mode - reuses TransactionForm so validation and payee resolution
    stay identical to the full edit page, just without a page
    navigation."""
    instance = get_object_or_404(Transaction, pk=pk)
    form = TransactionForm(request.POST, instance=instance)
    if not form.is_valid():
        errors = {field: list(messages) for field, messages in form.errors.items()}
        return JsonResponse({"ok": False, "errors": errors}, status=400)
    account = form.cleaned_data["account"]
    if not account.is_active:
        # The full edit page offers a confirm-and-reactivate dialog for
        # this; Edit mode's per-row autosave has no room for a
        # confirmation step, so it points you there instead of silently
        # reactivating an account nobody asked to reactivate.
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "account": [
                        "This account is inactive - use the full edit page to reactivate it."
                    ]
                },
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
        ctx["next"] = safe_next(self.request, self.request.GET.get("next", ""))
        return ctx

    def get_success_url(self):
        # Back to wherever the delete link was opened from (e.g. the
        # all-accounts list), same as editing already does - otherwise
        # falls back to the transaction's own account.
        next_url = safe_next(self.request, self.request.POST.get("next", ""))
        return next_url or reverse("finance:account_detail", kwargs={"pk": self.object.account_id})
