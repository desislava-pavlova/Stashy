from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from .. import regional
from ..content import ACCOUNT_TIPS
from ..currency import SUFFIX_CURRENCIES, convert_balances, format_money
from ..forms import AccountCreateForm, AccountForm, ReconcileFinishForm, ReconcileStartForm, TransferForm
from ..models import Account, Profile, grouped_accounts
from ..money import ZERO
from ..services import reconciliation
from ._helpers import _sorted


class AccountListView(ListView):
    model = Account
    template_name = "finance/account_list.html"
    context_object_name = "accounts"

    def get_queryset(self):
        return Account.objects.with_balances()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        main_currency = Profile.for_user(self.request.user).main_currency
        net_worth, missing_rates = convert_balances(ctx["accounts"], main_currency)
        ctx["net_worth"] = net_worth
        ctx["main_currency"] = main_currency
        ctx["missing_rates"] = missing_rates
        ctx["groups"] = grouped_accounts(ctx["accounts"])
        return ctx


ACCOUNT_REGISTER_SORT_FIELDS = {
    "date": ["date", "transaction_number"],
    "num": ["transaction_number"],
    "payee": ["payee__name"],
    "category": ["category__name"],
    "memo": ["memo"],
    "amount": ["amount"],
}


def account_detail(request, pk):
    account = get_object_or_404(Account, pk=pk)
    transactions = account.transactions.select_related("category__parent", "payee")
    transactions, sort, sort_next = _sorted(request, transactions, ACCOUNT_REGISTER_SORT_FIELDS)
    return render(
        request,
        "finance/account_detail.html",
        {
            "account": account,
            "transactions": transactions,
            "sort": sort,
            "sort_next": sort_next,
        },
    )


class AccountCreateView(CreateView):
    model = Account
    form_class = AccountCreateForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:account_list")
    extra_context = {"title": "Add account", "tips": ACCOUNT_TIPS}


class AccountUpdateView(UpdateView):
    model = Account
    form_class = AccountForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:account_list")
    extra_context = {"title": "Edit account", "tips": ACCOUNT_TIPS}


class AccountDeleteView(DeleteView):
    model = Account
    template_name = "finance/account_confirm_delete.html"
    success_url = reverse_lazy("finance:account_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        account = self.object
        ctx["transaction_count"] = account.transactions.count()
        ctx["reminder_count"] = account.reminders.count()
        ctx["import_batch_count"] = account.import_batches.count()
        return ctx


def account_transfer(request, pk):
    """The account page's Transfer button (D7): moves money between two accounts as a paired transaction."""
    account = get_object_or_404(Account, pk=pk)
    if request.method == "POST":
        form = TransferForm(request.POST, from_account=account)
        if form.is_valid():
            form.save()
            amount = format_money(form.cleaned_data["amount"], account.currency)
            messages.success(request, f"Transferred {amount} to {form.cleaned_data['to_account'].name}.")
            return redirect("finance:account_detail", pk=account.pk)
    else:
        form = TransferForm(from_account=account)
    return render(request, "finance/transfer_form.html", {"form": form, "account": account})


def _reconcile_review_context(account, statement_date, statement_balance, checked_ids=None):
    opening_balance = account.reconciled_balance()
    candidates = list(
        account.transactions.filter(reconciled=False, date__lte=statement_date).order_by("date", "transaction_number")
    )
    if checked_ids is None:
        # First visit to the checklist starts with nothing checked - use "Mark all" if the
        # statement covers everything. A retry after an unaccepted-difference error always
        # passes the exact set actually submitted instead, even if that's empty.
        checked_ids = set()
    for txn in candidates:
        txn.was_checked = str(txn.pk) in checked_ids
    deposits = [t for t in candidates if t.amount > 0]
    payments = [t for t in candidates if t.amount < 0]
    return {
        "account": account,
        "statement_date": statement_date,
        "statement_balance": statement_balance,
        "opening_balance": opening_balance,
        "currency_is_suffix": account.currency in SUFFIX_CURRENCIES,
        "deposits": deposits,
        "payments": payments,
        "deposits_total": sum((t.amount for t in deposits), ZERO),
        "payments_total": sum((t.amount for t in payments), ZERO),
    }


def account_reconcile(request, pk):
    """The account page's Reconcile button: a Quicken-style two-step flow - state the
    statement's ending balance and date, then check off the transactions it covers."""
    account = get_object_or_404(Account, pk=pk)

    if request.method == "POST" and request.POST.get("stage") == "finish":
        finish_form = ReconcileFinishForm(request.POST, account=account)
        if not finish_form.is_valid():
            # Only a tampered or half-sent page gets here (the fields are hidden), or one whose
            # transactions were reconciled elsewhere meanwhile - start over from step 1.
            messages.error(request, "Something in that reconciliation didn't come through. Please start it again.")
            return render(
                request,
                "finance/account_reconcile_start.html",
                {
                    "account": account,
                    "form": ReconcileStartForm(initial={"statement_date": date.today()}),
                    "opening_balance": account.reconciled_balance(),
                },
            )
        statement_date = finish_form.cleaned_data["statement_date"]
        statement_balance = finish_form.cleaned_data["statement_balance"]
        checked = list(finish_form.cleaned_data["transaction"])
        try:
            reconciliation.reconcile(
                account,
                statement_date,
                statement_balance,
                checked,
                accept_difference=finish_form.cleaned_data["accept_difference"],
            )
        except reconciliation.UnacceptedDifferenceError as error:
            messages.error(
                request,
                f"That leaves a difference of {format_money(error.difference, account.currency)} — "
                "check the box to accept it and create an adjustment, or fix your selections.",
            )
            checked_ids = {str(t.pk) for t in checked}
            return render(
                request,
                "finance/account_reconcile_review.html",
                _reconcile_review_context(account, statement_date, statement_balance, checked_ids),
            )
        messages.success(request, f"{account.name} reconciled to {regional.format_date(statement_date)}.")
        return redirect("finance:account_detail", pk=account.pk)

    if request.method == "POST":
        start_form = ReconcileStartForm(request.POST)
        if start_form.is_valid():
            return render(
                request,
                "finance/account_reconcile_review.html",
                _reconcile_review_context(
                    account, start_form.cleaned_data["statement_date"], start_form.cleaned_data["statement_balance"]
                ),
            )
    else:
        start_form = ReconcileStartForm(initial={"statement_date": date.today()})

    return render(
        request,
        "finance/account_reconcile_start.html",
        {
            "account": account,
            "form": start_form,
            "opening_balance": account.reconciled_balance(),
        },
    )
