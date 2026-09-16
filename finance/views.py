import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connections
from django.db.models import Count, Sum, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from audit.context import system_action

from . import backup as finance_backup
from . import regional
from . import stashes
from .currency import RateFetchError, RateTable, SUFFIX_CURRENCIES, convert_balances, fetch_latest_rates, format_money
from .forms import (
    AccountCreateForm, AccountForm, CategoryForm, CheckRatesForm, DateRangeFilterForm, ImportUploadForm,
    PayeeForm, ProfileForm, ReconcileStartForm, ReminderForm, TransactionForm, TransferForm, WatchedExpensesForm,
    convertible_currencies,
)
from .models import (
    Account, Category, ImportBatch, Payee, Profile, Reconciliation, Reminder, Tag, Transaction,
    categories_alphabetical, grouped_accounts, grouped_categories,
)
from .auth_forms import ChangePinForm
from .money import CENT, ZERO, parse_amount
from .starter_categories import add_starter_categories, starter_categories_path
from .reporting import (
    DEFAULT_LOOKBACK_PERIODS, DEFAULT_RANGE_KEY, category_report_rows, category_totals_top_level,
    category_totals_vs_average, category_vs_average_report_rows, income_and_spending,
    income_and_spending_vs_average, net_worth_by_account, resolve_range, savings_rate, spending_tree,
    subtotal_overflow_message, trimmed_average_periods, uncategorized_count, watched_expenses_category_ids, watched_expenses_series,
    watched_expenses_totals,
)

ACCOUNT_TIPS = [
    "Opening balance is what the account held on the day you start tracking it in Stashy — Stashy turns it into a real transaction.",
    "Institution is optional — fill it in only when a bank is behind the account.",
    "Stopped using an account? Untick Is active instead of deleting it, so its history stays.",
]
CATEGORY_TIPS = [
    "Give a category a parent to make it a subcategory, like Food → Groceries.",
    "Income and expense categories are reported separately — the Parent list narrows to match once you pick a type.",
    "Keeping it simple is fine too: top-level categories can be used on their own.",
]
PAYEE_TIPS = [
    "You rarely need to add payees by hand — Stashy memorizes them as you enter transactions.",
    "Category, memo and amount fill in automatically from what you used last time, per category — "
    "so a payee you use for more than one kind of purchase offers each one to pick from.",
]
REMINDER_TIPS = [
    "Reminders never create transactions on their own — press Enter when a bill is actually paid.",
    "Pick the account the money leaves from, or arrives in for income.",
    "Anything due within the next 7 days shows up on your dashboard.",
]
TRANSACTION_TIPS = [
    "Type a payee you've used before and Stashy fills in the category, memo and amount.",
    "Use a minus sign for money going out, like -12.50.",
    "Moving money between your own accounts? Use the Transfer button on the account page instead.",
]
IMPORT_TIPS = [
    "CSV columns recognized: Date, Payee/Description, Memo, Amount, Account, Category.",
    'A category that doesn\'t exist yet is created for you automatically - marked "auto" in '
    'the category list - and understands "Parent:Child" or "Parent > Child".',
    "An Account column lets one file cover several accounts at once; a row naming an account "
    "that doesn't exist yet is skipped rather than guessed at.",
    'Leave Date format on "Detect automatically" unless Stashy gets it wrong for this '
    "particular file - it reads a sample of the dates first and only asks you to pick when it can't tell.",
]

# The transaction list adds "All time" to Reports' own date-range presets (Phase 5) — a
# register naturally wants an unbounded view sometimes, unlike a chart that needs a range.
TRANSACTION_RANGE_CHOICES = [("", "All time")] + DateRangeFilterForm.RANGE_CHOICES


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


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

# Kept as one constant on the model (Reminder.DUE_SOON_DAYS) so the dashboard's "due soon"
# window and each reminder's computed status agree on what "soon" means.
REMINDER_LOOKAHEAD_DAYS = Reminder.DUE_SOON_DAYS


def _greeting():
    hour = timezone.localtime().hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


@login_required
def dashboard(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    main_currency = Profile.for_user(request.user).main_currency
    accounts = list(Account.objects.filter(is_active=True).with_balances())
    net_worth, missing_rates = convert_balances(accounts, main_currency)
    # Same grouping/order as the accounts page (Physical Liquid -> Digital Liquid -> Illiquid) -
    # inactive accounts never reach this panel, since `accounts` above already excludes them.
    account_groups = grouped_accounts(accounts)

    this_month = income_and_spending(month_start, today)
    last_month = income_and_spending(last_month_start, last_month_end)
    rate = savings_rate(last_month["income"], last_month["spending"])

    due_soon = list(
        Reminder.objects.filter(next_due_date__lte=today + timedelta(days=REMINDER_LOOKAHEAD_DAYS))
        .select_related("account")
        .order_by("next_due_date")
    )
    # Already paid or skipped doesn't belong on a "what needs attention" widget, even if its
    # date still falls in the lookahead window (2026-09-14 follow-up).
    due_soon = [r for r in due_soon if r.status(today) not in ("paid", "skipped")]
    for reminder in due_soon:
        reminder.days_until = (reminder.next_due_date - today).days

    context = {
        "greeting": _greeting(),
        "today": today,
        "accounts": accounts,
        "account_groups": account_groups,
        "main_currency": main_currency,
        "missing_rates": missing_rates,
        "net_worth": net_worth,
        "month_income": this_month["income"],
        "month_spending": this_month["spending"],
        "last_month_start": last_month_start,
        "savings_rate": rate,
        "savings_bar": max(0, min(100, int(rate))) if rate is not None else 0,
        "due_soon": due_soon,
        "reminder_lookahead_days": REMINDER_LOOKAHEAD_DAYS,
        "spending_tree": spending_tree(month_start, today),
        "spending_period": {"start": month_start.isoformat(), "end": today.isoformat()},
        # The donut chart (JS) needs to know whether its symbol goes before or after the
        # number, same convention |moneyc already applies server-side.
        "currency_is_suffix": main_currency in SUFFIX_CURRENCIES,
    }
    return render(request, "finance/dashboard.html", context)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

class AccountListView(LoginRequiredMixin, ListView):
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


@login_required
def account_detail(request, pk):
    account = get_object_or_404(Account, pk=pk)
    transactions = account.transactions.select_related("category__parent", "payee")
    transactions, sort, sort_next = _sorted(request, transactions, ACCOUNT_REGISTER_SORT_FIELDS)
    return render(request, "finance/account_detail.html", {
        "account": account, "transactions": transactions, "sort": sort, "sort_next": sort_next,
    })


class AccountCreateView(LoginRequiredMixin, CreateView):
    model = Account
    form_class = AccountCreateForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:account_list")
    extra_context = {"title": "Add account", "tips": ACCOUNT_TIPS}


class AccountUpdateView(LoginRequiredMixin, UpdateView):
    model = Account
    form_class = AccountForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:account_list")
    extra_context = {"title": "Edit account", "tips": ACCOUNT_TIPS}


class AccountDeleteView(LoginRequiredMixin, DeleteView):
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


@login_required
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
        # First visit to the checklist - pre-check anything already marked "cleared" (the
        # register's own C badge), since that's already been confirmed against something once.
        # A retry after an unaccepted-difference error always passes the exact set actually
        # submitted instead, even if that's empty.
        checked_ids = {str(t.pk) for t in candidates if t.cleared}
    for t in candidates:
        t.was_checked = str(t.pk) in checked_ids
    deposits = [t for t in candidates if t.amount > 0]
    payments = [t for t in candidates if t.amount < 0]
    return {
        "account": account, "statement_date": statement_date, "statement_balance": statement_balance,
        "opening_balance": opening_balance,
        "currency_is_suffix": account.currency in SUFFIX_CURRENCIES,
        "deposits": deposits, "payments": payments,
        "deposits_total": sum((t.amount for t in deposits), ZERO),
        "payments_total": sum((t.amount for t in payments), ZERO),
    }


@login_required
def account_reconcile(request, pk):
    """The account page's Reconcile button (Phase 8): a Quicken-style two-step flow - state
    the statement's ending balance and date, then check off the transactions it covers."""
    account = get_object_or_404(Account, pk=pk)

    if request.method == "POST" and request.POST.get("stage") == "finish":
        statement_date = date.fromisoformat(request.POST["statement_date"])
        statement_balance = Decimal(request.POST["statement_balance"])
        checked_ids = set(request.POST.getlist("transaction"))
        checked = account.transactions.filter(pk__in=checked_ids, reconciled=False)
        opening_balance = account.reconciled_balance()
        cleared_total = sum((t.amount for t in checked), ZERO)
        new_balance = opening_balance + cleared_total
        difference = (statement_balance - new_balance).quantize(CENT)

        if difference != ZERO and request.POST.get("accept_difference") != "1":
            messages.error(
                request,
                f"That leaves a difference of {format_money(difference, account.currency)} — "
                "check the box to accept it and create an adjustment, or fix your selections.",
            )
            return render(request, "finance/account_reconcile_review.html", _reconcile_review_context(
                account, statement_date, statement_balance, checked_ids
            ))

        adjustment = None
        with system_action(f"Account '{account.name}' reconciled to {regional.format_date(statement_date)}"):
            for t in checked:
                t.cleared = True
                t.reconciled = True
                t.save(update_fields=["cleared", "reconciled"])
            if difference != ZERO:
                # Not real income/spending - excluded from reports the same way a transfer or
                # opening balance is (D1/D7), since it's a bookkeeping correction, not a purchase.
                adjustment = Transaction.objects.create(
                    account=account, date=statement_date, payee=Payee.system(Payee.RECONCILIATION_ADJUSTMENT),
                    category=account.ensure_transfer_category(), memo="Reconciliation adjustment",
                    amount=difference, cleared=True, reconciled=True,
                )
            Reconciliation.objects.create(
                account=account, statement_date=statement_date, statement_balance=statement_balance,
                opening_balance=opening_balance, adjustment_transaction=adjustment,
            )
        messages.success(request, f"{account.name} reconciled to {regional.format_date(statement_date)}.")
        return redirect("finance:account_detail", pk=account.pk)

    if request.method == "POST":
        start_form = ReconcileStartForm(request.POST)
        if start_form.is_valid():
            return render(request, "finance/account_reconcile_review.html", _reconcile_review_context(
                account, start_form.cleaned_data["statement_date"], start_form.cleaned_data["statement_balance"]
            ))
    else:
        start_form = ReconcileStartForm(initial={"statement_date": date.today()})

    return render(request, "finance/account_reconcile_start.html", {
        "account": account, "form": start_form, "opening_balance": account.reconciled_balance(),
    })


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

class CategoryListView(LoginRequiredMixin, ListView):
    model = Category
    template_name = "finance/category_list.html"
    context_object_name = "categories"

    def get_queryset(self):
        qs = Category.objects.select_related("parent", "account").annotate(usage_count=Count("transactions"))
        search = self.request.GET.get("q", "").strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(parent__name__icontains=search))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["groups"] = grouped_categories(ctx["categories"])
        ctx["search"] = self.request.GET.get("q", "").strip()
        return ctx


class CategoryCreateView(LoginRequiredMixin, CreateView):
    model = Category
    form_class = CategoryForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:category_list")
    extra_context = {"title": "Add category", "tips": CATEGORY_TIPS, "category_type_filter": True}


@login_required
def add_starter_categories_view(request):
    """Creates whatever's in basic_categories.txt that doesn't already exist (Phase 12) - the
    file itself, not this view, is where the actual list lives, so editing it needs no code
    change. Safe to click more than once: already-existing names are skipped."""
    if not starter_categories_path().exists():
        messages.error(request, "basic_categories.txt wasn't found in the project folder.")
        return redirect("finance:category_list")

    with system_action("Added starter categories from basic_categories.txt"):
        created = add_starter_categories()

    if created:
        messages.success(request, f"Added {len(created)} categor{'y' if len(created) == 1 else 'ies'}: {', '.join(created)}.")
    else:
        messages.info(request, "Every category in basic_categories.txt already exists - nothing to add.")
    return redirect("finance:category_list")


class SystemManagedCategoryGuard:
    """Blocks edit/delete of an account's own transfer category — it's managed automatically (D7)."""

    def dispatch(self, request, *args, **kwargs):
        self.request, self.args, self.kwargs = request, args, kwargs
        self.object = self.get_object()
        if self.object.is_system_managed:
            messages.error(request, f"'{self.object.full_name}' belongs to the {self.object.account.name} account and is managed automatically.")
            return redirect("finance:category_list")
        return super().dispatch(request, *args, **kwargs)


class CategoryUpdateView(LoginRequiredMixin, SystemManagedCategoryGuard, UpdateView):
    model = Category
    form_class = CategoryForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:category_list")
    extra_context = {"title": "Edit category", "tips": CATEGORY_TIPS, "category_type_filter": True}


class CategoryDeleteView(LoginRequiredMixin, SystemManagedCategoryGuard, DeleteView):
    model = Category
    template_name = "finance/category_confirm_delete.html"
    success_url = reverse_lazy("finance:category_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        descendants = self.object.descendants()
        ctx["descendants"] = sorted(descendants, key=lambda c: c.full_name.lower())
        ctx["affected_transaction_count"] = Transaction.objects.filter(
            category__in=[self.object, *descendants]
        ).count()
        return ctx


@login_required
def category_usage(request, pk):
    """A category's own transactions (not its subcategories'), with a mass re-categorize tool."""
    category = get_object_or_404(Category, pk=pk)
    transactions = category.transactions.select_related("account", "payee")

    if request.method == "POST":
        selected_ids = request.POST.getlist("transaction_id")
        target = Category.objects.filter(pk=request.POST.get("move_to")).exclude(
            Q(pk=category.pk) | Q(account__isnull=False)
        ).first()
        if not selected_ids:
            messages.error(request, "Select at least one transaction to move.")
        elif not target:
            messages.error(request, "Choose a category to move the selected transactions to.")
        else:
            moved = 0
            for transaction in transactions.filter(pk__in=selected_ids):
                # One save() per row (never queryset.update()) so each move is audited on its own.
                transaction.category = target
                transaction.save(update_fields=["category"])
                moved += 1
            messages.success(request, f"Moved {moved} transaction{'s' if moved != 1 else ''} to {target.full_name}.")
        return redirect("finance:category_usage", pk=category.pk)

    other_categories = sorted(
        Category.objects.exclude(Q(pk=category.pk) | Q(account__isnull=False)).select_related("parent"),
        key=lambda c: c.full_name.lower(),
    )
    return render(request, "finance/category_usage.html", {
        "category": category, "transactions": transactions, "other_categories": other_categories,
    })


# ---------------------------------------------------------------------------
# Payees (memorized payees)
# ---------------------------------------------------------------------------

PAYEE_SORT_KEYS = {
    "name": lambda row: row["payee"].name.lower(),
    "category": lambda row: row["categories_label"].lower(),
    "amount": lambda row: row["total_sort_value"],
    "usage": lambda row: row["usage_count"],
}


@login_required
def payee_list(request):
    search = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "")
    memo_filter = request.GET.get("memo", "").strip()

    payees = Payee.objects.all()
    if search:
        payees = payees.filter(name__icontains=search)
    if category_id == "none":
        payees = payees.filter(transactions__category__isnull=True).distinct()
    elif category_id.isdigit():
        payees = payees.filter(transactions__category_id=category_id).distinct()
    if memo_filter:
        payees = payees.filter(transactions__memo__icontains=memo_filter).distinct()
    payees = list(payees)
    payee_ids = [p.pk for p in payees]

    # Everything below is one query each for the whole page - every category any of these
    # payees has ever been used with, every payee's transaction count, and every payee's
    # running total per currency (a payee can span accounts in different currencies) -
    # instead of walking each payee's own history one at a time (fine for a single payee's
    # own detail page, too slow for a list that can hold well over a hundred rows).
    categories_by_payee = {}
    category_rows = (
        Transaction.objects.filter(payee_id__in=payee_ids)
        .values("payee_id", "category_id", "category__name", "category__parent__name")
        # Transaction's default ordering (by date) would otherwise ride along into the SELECT
        # a values().distinct() generates, silently defeating the distinct - every date/time
        # a category was used would count as "different", not just every category.
        .order_by()
        .distinct()
    )
    for row in category_rows:
        label = "Uncategorized" if row["category_id"] is None else (
            f"{row['category__parent__name']} > {row['category__name']}" if row["category__parent__name"]
            else row["category__name"]
        )
        categories_by_payee.setdefault(row["payee_id"], []).append(label)

    usage_by_payee = {
        row["payee_id"]: row["count"]
        for row in Transaction.objects.filter(payee_id__in=payee_ids).values("payee_id").annotate(count=Count("id"))
    }

    totals_by_payee = {}
    for row in (
        Transaction.objects.filter(payee_id__in=payee_ids)
        .values("payee_id", "account__currency")
        .annotate(total=Sum("amount"))
    ):
        totals_by_payee.setdefault(row["payee_id"], []).append((row["account__currency"], row["total"]))

    rows = []
    for payee in payees:
        labels = sorted(categories_by_payee.get(payee.pk, []))
        totals = sorted(totals_by_payee.get(payee.pk, []))
        rows.append({
            "payee": payee,
            "categories": labels,
            "categories_label": ", ".join(labels),  # sort key only - the template shows one per row
            "category_count": len(labels),
            "totals": totals,
            # A rough, currency-blind number for sort order only (display always shows each
            # currency separately) - fine for ordering rows, never shown as an actual amount.
            "total_sort_value": sum((amount for _, amount in totals), ZERO),
            "usage_count": usage_by_payee.get(payee.pk, 0),
        })

    sort = request.GET.get("sort", "name")
    column = sort.removeprefix("-")
    rows.sort(key=PAYEE_SORT_KEYS.get(column, PAYEE_SORT_KEYS["name"]), reverse=sort.startswith("-"))
    sort_next = {name: (f"-{name}" if sort == name else name) for name in PAYEE_SORT_KEYS}

    return render(request, "finance/payee_list.html", {
        "rows": rows,
        "search": search,
        "selected_category": category_id,
        "memo_filter": memo_filter,
        "categories": categories_alphabetical(Category.objects.select_related("parent").exclude(account__isnull=False)),
        "sort": sort,
        "sort_next": sort_next,
    })


@login_required
def payee_detail(request, pk):
    """Every category ever used with this payee, and the most recent memo/amount in each (Phase 6)."""
    payee = get_object_or_404(Payee, pk=pk)
    return render(request, "finance/payee_detail.html", {"payee": payee, "history": payee.category_history()})


class PayeeCreateView(LoginRequiredMixin, CreateView):
    model = Payee
    form_class = PayeeForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:payee_list")
    extra_context = {"title": "Add memorized payee", "tips": PAYEE_TIPS}


class PayeeUpdateView(LoginRequiredMixin, UpdateView):
    model = Payee
    form_class = PayeeForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:payee_list")
    extra_context = {"title": "Edit payee", "tips": PAYEE_TIPS}


class PayeeDeleteView(LoginRequiredMixin, DeleteView):
    model = Payee
    template_name = "finance/generic_confirm_delete.html"
    success_url = reverse_lazy("finance:payee_list")


@login_required
def api_payee_autofill(request):
    """Given ?name=..., return every category/memo/amount combo on record for that payee (Phase 6) —
    one per category, most recently used first, so a repeat-visit payee with several kinds of
    purchase (e.g. a pharmacy: health one week, gifts another) offers each to pick from."""
    name = request.GET.get("name", "").strip()
    if not name:
        return JsonResponse({"combos": []})
    payee = Payee.objects.filter(name__iexact=name).first()
    if not payee:
        return JsonResponse({"combos": []})
    combos = [
        {
            "category_id": txn.category_id,
            "category_name": txn.category.full_name if txn.category_id else "Uncategorized",
            "memo": txn.memo,
            "amount": str(txn.amount),
        }
        for txn in payee.category_history()
    ]
    return JsonResponse({"combos": combos})


@login_required
def api_payee_list(request):
    # The system payees behind Opening Balance/Transfer transactions (Phase 3) aren't something
    # you'd type by hand, so they're left out of the everyday autocomplete noise.
    names = list(
        Payee.objects.filter(is_active=True).exclude(name__in=Payee.SYSTEM_NAMES).values_list("name", flat=True)
    )
    return JsonResponse({"payees": names})


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def _iso_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


TRANSACTION_LIST_SORT_FIELDS = {
    "date": ["date", "transaction_number"],
    "num": ["transaction_number"],
    "account": ["account__name"],
    "payee": ["payee__name"],
    "category": ["category__name"],
    "memo": ["memo"],
    "amount": ["amount"],
}


def _filtered_transactions(request):
    """Every filter the transaction list applies, shared with CSV/XLSX export (Phase 10) so
    what you export always matches what you're currently looking at."""
    transactions = Transaction.objects.select_related("account", "category__parent", "payee")

    account_id = request.GET.get("account", "")
    # Category is multi-select (2026-09-15 follow-up) — one or more "category" values, any mix
    # of digit category ids and the literal "none" for uncategorized, matched with OR.
    category_values = request.GET.getlist("category")
    status = request.GET.get("status", "")
    search = request.GET.get("q", "").strip()
    with_subcategories = request.GET.get("with_subcategories") == "1"
    include_uncategorized = "none" in category_values
    selected_categories = list(Category.objects.filter(
        pk__in=[v for v in category_values if v.isdigit()]
    ).select_related("parent"))

    range_key = request.GET.get("range", "")
    start = _iso_date(request.GET.get("start"))
    end = _iso_date(request.GET.get("end"))
    if range_key and range_key != "custom":
        start, end, _ = resolve_range({"range": range_key})
    elif "range" not in request.GET and start is None and end is None:
        # Default view (Phase 5): this year, until you explicitly ask for something else —
        # "range" being present at all (even "" for "All time") means you already did.
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
        transactions = transactions.filter(
            Q(payee__name__icontains=search) | Q(payee_name__icontains=search) | Q(memo__icontains=search)
        )

    return transactions, {
        "account_id": account_id, "category_values": category_values,
        "selected_categories": selected_categories, "include_uncategorized": include_uncategorized,
        "with_subcategories": with_subcategories, "status": status, "range_key": range_key,
        "start": start, "end": end, "search": search,
    }


@login_required
def transaction_list(request):
    transactions, filters = _filtered_transactions(request)
    transactions, sort, sort_next = _sorted(request, transactions, TRANSACTION_LIST_SORT_FIELDS)
    # Mixes currencies across accounts without conversion, same known simplification as the
    # accounts page's own net worth total (Phase 3/D2) - accurate for a single-currency setup.
    total_amount = transactions.aggregate(total=Sum("amount"))["total"] or ZERO

    selected_categories = filters["selected_categories"]
    include_uncategorized = filters["include_uncategorized"]
    picked_count = len(selected_categories) + (1 if include_uncategorized else 0)
    if picked_count == 0:
        category_filter_label = "All categories"
    elif picked_count == 1:
        category_filter_label = "Uncategorized" if include_uncategorized and not selected_categories else selected_categories[0].full_name
    else:
        category_filter_label = f"{picked_count} categories selected"

    context = {
        "transactions": transactions,
        "total_amount": total_amount,
        # Mixed currencies have no one true symbol either - the main currency is the same
        # stand-in the accounts page's own net worth total already uses (Phase 3/D2).
        "main_currency": Profile.for_user(request.user).main_currency,
        "accounts": Account.objects.all(),
        # Income -> Expense -> Transfer, each main category with its own subcategories right
        # under it - grouped_categories() is the category list page's own grouping, reused
        # here (2026-09-15 follow-up) so the checkbox dropdown reads exactly like that page.
        "category_groups": grouped_categories(Category.objects.select_related("parent")),
        "category_filter_label": category_filter_label,
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


# ---------------------------------------------------------------------------
# Export (Phase 10) - CSV built in; XLSX via openpyxl
# ---------------------------------------------------------------------------

def _csv_response(filename, headers, rows):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    writer = csv.writer(response)
    writer.writerow(headers)
    writer.writerows(rows)
    return response


def _xlsx_response(filename, headers, rows):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
    workbook.save(response)
    return response


def _export_response(fmt, filename, headers, rows):
    if fmt == "xlsx":
        return _xlsx_response(filename, headers, rows)
    return _csv_response(filename, headers, rows)


@login_required
def export_transactions(request):
    """Exports whatever the transaction list is currently filtered/sorted to (Phase 10) -
    same filters, so what you get matches what you were looking at."""
    transactions, _filters = _filtered_transactions(request)
    transactions, _sort, _sort_next = _sorted(request, transactions, TRANSACTION_LIST_SORT_FIELDS)
    headers = ["Date", "Number", "Account", "Payee", "Category", "Memo", "Amount", "Currency", "Cleared", "Reconciled"]
    rows = [
        [
            t.date.isoformat(), t.transaction_number or "", t.account.name, t.display_payee,
            t.category.full_name if t.category_id else "", t.memo, str(t.amount), t.account.currency,
            "Y" if t.cleared else "N", "Y" if t.reconciled else "N",
        ]
        for t in transactions
    ]
    filename = f"stashy-transactions-{date.today():%Y%m%d}"
    return _export_response(request.GET.get("format", "csv"), filename, headers, rows)


@login_required
def export_accounts(request):
    headers = ["Name", "Type", "Currency", "Institution", "Active", "Balance"]
    rows = [
        [a.name, a.get_account_type_display(), a.currency, a.institution, "Y" if a.is_active else "N", str(a.balance)]
        for a in Account.objects.all()
    ]
    filename = f"stashy-accounts-{date.today():%Y%m%d}"
    return _export_response(request.GET.get("format", "csv"), filename, headers, rows)


@login_required
def export_categories(request):
    # Full export (2026-09-14 follow-up): includes system-managed transfer categories too,
    # marked as such, rather than silently leaving them out of "export everything".
    categories = Category.objects.select_related("parent", "account")
    headers = ["Name", "Parent", "Type", "Auto-generated", "System-managed"]
    rows = [
        [
            c.name, c.parent.name if c.parent_id else "", c.get_category_type_display(),
            "Y" if c.is_auto_generated else "N", "Y" if c.account_id else "N",
        ]
        for c in categories
    ]
    filename = f"stashy-categories-{date.today():%Y%m%d}"
    return _export_response(request.GET.get("format", "csv"), filename, headers, rows)


@login_required
def export_payees(request):
    # Full export (2026-09-14 follow-up): includes system payees too, marked as such.
    payees = Payee.objects.all()
    headers = ["Name", "Active", "System-managed"]
    rows = [[p.name, "Y" if p.is_active else "N", "Y" if p.name in Payee.SYSTEM_NAMES else "N"] for p in payees]
    filename = f"stashy-payees-{date.today():%Y%m%d}"
    return _export_response(request.GET.get("format", "csv"), filename, headers, rows)


def _safe_next(url):
    """Only ever redirect back to a page of this same app - never off-site."""
    return url if url.startswith("/") and not url.startswith("//") else ""


@login_required
def transaction_form_view(request, pk=None):
    instance = get_object_or_404(Transaction, pk=pk) if pk else None
    next_url = _safe_next(request.POST.get("next") or request.GET.get("next", ""))
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
            # "Enter" (Phase 7) - review/adjust before saving, rather than silently creating
            # the transaction, so a reminder with no fixed amount can still be entered.
            initial["account"] = reminder.account_id
            if reminder.payee_id:
                initial["payee_name"] = reminder.payee.name
            initial["category"] = reminder.category_id
            initial["amount"] = reminder.signed_amount()
            # Prefer the memo you actually used last time for this exact payee + category
            # combo (Phase 6's memorization) over the reminder's own generic notes, if one
            # exists (2026-09-14 follow-up).
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
                # is trying to use - ask, and only act once they say yes (2026-09-15 follow-up).
                return render(request, "finance/transaction_form.html", {
                    "form": form, "instance": instance, "tips": TRANSACTION_TIPS, "next": next_url,
                    "reminder": reminder, "inactive_account_confirm": account,
                })
            if not account.is_active:
                with system_action(f"Account '{account.name}' made active again by entering a transaction on it"):
                    account.is_active = True
                    account.save(update_fields=["is_active"])
            txn = form.save()
            if reminder:
                reminder.last_entered_transaction = txn
                reminder.save(update_fields=["last_entered_transaction"])
                if reminder.frequency == Reminder.Frequency.ONE_TIME:
                    # Nothing left to advance to - it's now permanently "Paid" (status()).
                    messages.success(request, f"Entered {reminder}.")
                else:
                    reminder.advance_due_date()
                    messages.success(
                        request, f"Entered {reminder} and moved it to {reminder.next_due_date:%d/%m/%Y}."
                    )
                return redirect("finance:reminder_list")
            messages.success(request, "Transaction saved.")
            # Back to wherever you opened this from (the account register or the all-accounts
            # list) if we know it; otherwise the transaction's own account, as before.
            return redirect(next_url or reverse("finance:account_detail", kwargs={"pk": txn.account_id}))
    else:
        form = TransactionForm(instance=instance, initial=initial)

    return render(request, "finance/transaction_form.html", {
        "form": form, "instance": instance, "tips": TRANSACTION_TIPS, "next": next_url, "reminder": reminder,
    })


class TransactionDeleteView(LoginRequiredMixin, DeleteView):
    model = Transaction
    template_name = "finance/generic_confirm_delete.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["next"] = _safe_next(self.request.GET.get("next", ""))
        return ctx

    def get_success_url(self):
        # Back to wherever the delete link was opened from (e.g. the all-accounts list),
        # same as editing already does - otherwise falls back to the transaction's own account.
        next_url = _safe_next(self.request.POST.get("next", ""))
        return next_url or reverse("finance:account_detail", kwargs={"pk": self.object.account_id})


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

def _reminder_window_cutoff(window, today):
    """Same "yearly = through 31 December" meaning as Reminder.default_generation_horizon.
    None means no cutoff at all (the explicit "All" choice)."""
    if window in ("15", "30", "90"):
        return today + timedelta(days=int(window))
    if window == "yearly":
        return date(today.year, 12, 31)
    return None


@login_required
def reminder_list(request):
    today = timezone.localdate()
    # A fresh visit (no "window" param at all) defaults to 15 days out; once anything's been
    # picked - including "All" (window=all) - that stays sticky rather than snapping back,
    # same "can Django tell unset apart from explicitly chosen" pattern the transaction list
    # already uses for its own default range (2026-09-14 follow-up).
    window = request.GET.get("window")
    if window is None:
        window = "15"
    show_done = request.GET.get("show_done") == "1"
    reminders = Reminder.objects.select_related("account", "category", "payee")
    cutoff = _reminder_window_cutoff(window, today)
    if cutoff is not None:
        reminders = reminders.filter(next_due_date__lte=cutoff)
    reminders = list(reminders)
    if not show_done:
        reminders = [r for r in reminders if r.status(today) not in ("paid", "skipped")]
    return render(request, "finance/reminder_list.html", {
        "reminders": reminders, "window": window, "show_done": show_done, "today": today,
    })


REMINDER_FORM_EXTRA_CONTEXT = {
    "tips": REMINDER_TIPS, "reminder_type_filter": True,
    "unit_words_json": json.dumps(Reminder.FREQUENCY_UNIT_WORDS),
}


class ReminderCreateView(LoginRequiredMixin, CreateView):
    model = Reminder
    form_class = ReminderForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:reminder_list")
    extra_context = {"title": "Add reminder", **REMINDER_FORM_EXTRA_CONTEXT}


# Fields regenerate_occurrences() already copies root -> new occurrence on generation; a
# "whole series" edit (below) syncs the same fields onto every already-generated, still-open
# occurrence too - schedule fields (frequency/interval/dates) stay per-row on purpose, since
# they only really apply to the root and an occurrence's own date is what makes it that occurrence.
SHARED_SERIES_FIELDS = ["account", "payee", "category", "amount", "reminder_type", "notes"]


def _propagate_series_edit(saved, root):
    if root.pk != saved.pk:
        for field in SHARED_SERIES_FIELDS:
            setattr(root, field, getattr(saved, field))
        root.save(update_fields=SHARED_SERIES_FIELDS)
    targets = root.occurrences.filter(last_entered_transaction__isnull=True, skipped=False).exclude(pk=saved.pk)
    with system_action(f"Series-wide edit applied from reminder ({saved})"):
        for occurrence in targets:
            for field in SHARED_SERIES_FIELDS:
                setattr(occurrence, field, getattr(saved, field))
            occurrence.save(update_fields=SHARED_SERIES_FIELDS)


@login_required
def reminder_edit(request, pk):
    """Edit a reminder - if it's connected to a series (it's a series head with occurrences,
    or it's an occurrence itself), ask whether the edit applies to just this one or the whole
    series before showing the form (2026-09-14 follow-up)."""
    reminder = get_object_or_404(Reminder, pk=pk)
    root = reminder.series if reminder.series_id else reminder
    has_connection = bool(reminder.series_id) or root.occurrences.exists()
    scope = request.POST.get("scope") or request.GET.get("scope")
    if has_connection and scope not in ("this", "series"):
        return render(request, "finance/reminder_edit_scope.html", {"reminder": reminder})

    if request.method == "POST":
        form = ReminderForm(request.POST, instance=reminder)
        if form.is_valid():
            saved = form.save()
            if has_connection and scope == "series":
                _propagate_series_edit(saved, root)
            messages.success(request, "Reminder updated.")
            return redirect("finance:reminder_list")
    else:
        form = ReminderForm(instance=reminder)

    return render(request, "finance/generic_form.html", {
        "form": form, "title": "Edit reminder", "scope": scope if has_connection else None,
        **REMINDER_FORM_EXTRA_CONTEXT,
    })


class ReminderDeleteView(LoginRequiredMixin, DeleteView):
    model = Reminder
    template_name = "finance/reminder_confirm_delete.html"
    success_url = reverse_lazy("finance:reminder_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Only the ones that would actually be deleted - a paid/skipped occurrence is detached
        # and kept, not removed (Reminder.delete()).
        ctx["occurrence_count"] = self.object.occurrences.filter(
            last_entered_transaction__isnull=True, skipped=False
        ).count()
        return ctx


@login_required
def reminder_skip(request, pk):
    """Move a reminder past its current occurrence without creating a transaction (D6)."""
    if request.method != "POST":
        return redirect("finance:reminder_list")
    reminder = get_object_or_404(Reminder, pk=pk)
    label = str(reminder)
    if reminder.frequency == Reminder.Frequency.ONE_TIME:
        # No next period to advance to - mark it done instead (2026-09-14 follow-up).
        reminder.skipped = True
        reminder.save(update_fields=["skipped"])
        messages.success(request, f"Skipped {label}.")
    else:
        # A skip is explicitly "not paying this one" - clear any stale link from a previous
        # Enter so the next occurrence doesn't show as "paid" before it's actually due.
        reminder.last_entered_transaction = None
        reminder.save(update_fields=["last_entered_transaction"])
        reminder.advance_due_date()
        messages.success(request, f"Skipped {label}; moved it to {reminder.next_due_date:%d/%m/%Y}.")
    return redirect("finance:reminder_list")


# ---------------------------------------------------------------------------
# Import (CSV / OFX / QFX)
# ---------------------------------------------------------------------------

@login_required
def import_transactions(request):
    if request.method == "POST":
        form = ImportUploadForm(request.POST, request.FILES)
        if form.is_valid():
            account = form.cleaned_data["account"]
            upload = form.cleaned_data["file"]
            filename = upload.name.lower()

            if not filename.endswith((".csv", ".ofx", ".qfx")):
                messages.error(request, "Unsupported file type. Please upload a .csv, .ofx, or .qfx file.")
                return redirect("finance:import_transactions")

            with system_action(f"Imported from {upload.name}"):
                batch = ImportBatch.objects.create(account=account, source_filename=upload.name)
                if filename.endswith(".csv"):
                    date_format = form.cleaned_data.get("date_format") or "auto"
                    created, duplicates, unknown_account, resolved_format = _import_csv(upload, account, batch, date_format)
                else:
                    created, duplicates, unknown_account, resolved_format = _import_ofx(upload, account, batch)
                batch.row_count = created
                batch.duplicate_count = duplicates
                batch.save(update_fields=["row_count", "duplicate_count"])

            summary = f"Imported {created} transactions ({duplicates} duplicates skipped)."
            if unknown_account:
                summary += (
                    f" {unknown_account} row{'s' if unknown_account != 1 else ''} skipped - "
                    "named an account that doesn't exist yet."
                )
            if resolved_format:
                label = dict(regional.DATE_FORMAT_CHOICES).get(resolved_format, resolved_format)
                summary += f" Read dates as {label}."
            messages.success(request, summary)
            return redirect("finance:account_detail", pk=account.pk)
    else:
        form = ImportUploadForm()

    recent_batches = ImportBatch.objects.select_related("account")[:15]
    return render(request, "finance/import_form.html", {"form": form, "recent_batches": recent_batches, "tips": IMPORT_TIPS})


def _fingerprint(account_id, txn_date, amount, payee):
    raw = f"{account_id}|{txn_date.isoformat()}|{amount:.2f}|{payee}".encode()
    return hashlib.sha256(raw).hexdigest()


def _get_or_create_payee(name):
    """Case-insensitive match first, so an import never creates "Lidl" and "lidl" as separate
    payees (matching the fix already applied to manual entry)."""
    payee = Payee.objects.filter(name__iexact=name).first()
    if payee is None:
        payee, _ = Payee.objects.get_or_create(name=name)
    return payee


def _guessed_category(payee):
    """The category to pre-fill for an imported row with no Category column value: this
    payee's most recently used category, if any."""
    if payee is None:
        return None
    history = payee.category_history()
    return history[0].category if history else None


_CATEGORY_SEPARATOR_RE = re.compile(r"\s*[:>]\s*")


def _resolve_category(raw, amount):
    """Resolve the category named in an import row, auto-creating it if Stashy doesn't already
    have one by that name (Phase 9) - the main real-world use of this importer is a large
    one-time historical migration, and requiring every category to exist beforehand would be
    exactly the kind of friction that makes migrations painful. Matching is case-insensitive,
    so "Groceries" from a file never creates a duplicate of an existing "groceries". Understands
    "Parent:Child" or "Parent > Child" (Quicken's and this app's own separators) for one level
    of subcategory - a third level, if a file somehow has one, folds into the child's own name
    rather than erroring, since this app doesn't support deeper nesting anyway.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    parts = _CATEGORY_SEPARATOR_RE.split(raw, maxsplit=1)
    parent_name = parts[0].strip()
    child_name = parts[1].strip() if len(parts) > 1 else ""
    if not parent_name:
        return None

    category_type = Category.CategoryType.INCOME if amount > 0 else Category.CategoryType.EXPENSE
    parent = Category.objects.filter(name__iexact=parent_name, parent__isnull=True, account__isnull=True).first()
    if parent is None:
        parent = Category.objects.create(name=parent_name, category_type=category_type, is_auto_generated=True)

    if not child_name:
        return parent

    child = Category.objects.filter(name__iexact=child_name, parent=parent).first()
    if child is None:
        # A new subcategory always takes its parent's type - a mismatched pair (an "income"
        # category with an "expense" child) would only confuse reports for no real benefit.
        child = Category.objects.create(
            name=child_name, category_type=parent.category_type, parent=parent, is_auto_generated=True
        )
    return child


# A day value over 12 in either position settles day-first vs month-first; a 4-digit first
# component is ISO. Ambiguous rows (both parts <= 12) are skipped - the caller falls back to
# the profile's own format if nothing in the whole file was ever unambiguous.
_DATE_SNIFF_RE = re.compile(r"^(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})$")


def _detect_date_format(raw_dates):
    for raw in raw_dates:
        match = _DATE_SNIFF_RE.match((raw or "").strip())
        if not match:
            continue
        a, b, _c = match.groups()
        if len(a) == 4:
            return "ymd"
        if int(a) > 12:
            return "dmy"
        if int(b) > 12:
            return "mdy"
    return None


def _import_csv(upload, account, batch, date_format="auto"):
    text = io.TextIOWrapper(upload.file, encoding="utf-8-sig")
    reader = csv.DictReader(text)
    rows = list(reader)

    # Normalize header names so common bank export variants all work.
    fieldmap = {name.strip().lower(): name for name in (reader.fieldnames or [])}

    def get(row, *keys):
        for k in keys:
            if k in fieldmap:
                return (row.get(fieldmap[k]) or "").strip()
        return ""

    if date_format == "auto" or not date_format:
        detected = _detect_date_format(get(row, "date", "transaction date") for row in rows)
        date_format = detected or regional.current().date_format
    date_formats = regional.Preferences(date_format=date_format).date_input_formats

    # A file with its own Account column (a Quicken-style combined export, e.g.) can put each
    # row in a different, already-existing account instead of the one picked above (D9 backlog
    # "Accounts must already exist" - a wrong/typo'd name is the worst kind of import mistake to
    # auto-correct silently, so an unmatched name skips just that row rather than guessing).
    accounts_by_name = {a.name.lower(): a for a in Account.objects.all()}

    created, duplicates, unknown_account = 0, 0, 0
    for row in rows:
        raw_date = get(row, "date", "transaction date")
        raw_amount = get(row, "amount")
        payee_name = get(row, "payee", "description", "name")
        memo = get(row, "memo", "notes")
        raw_account = get(row, "account")
        raw_category = get(row, "category")

        if not raw_date or not raw_amount:
            continue

        txn_date = _parse_date(raw_date, date_formats)
        if not txn_date:
            continue

        amount = parse_amount(raw_amount)
        if amount is None:
            continue

        row_account = account
        if raw_account:
            row_account = accounts_by_name.get(raw_account.lower())
            if row_account is None:
                unknown_account += 1
                continue

        fp = _fingerprint(row_account.id, txn_date, amount, payee_name)
        if Transaction.objects.filter(import_fingerprint=fp).exists():
            duplicates += 1
            continue

        payee = _get_or_create_payee(payee_name) if payee_name else None
        category = _resolve_category(raw_category, amount) if raw_category else _guessed_category(payee)

        Transaction.objects.create(
            account=row_account,
            date=txn_date,
            payee=payee,
            memo=memo,
            amount=amount,
            import_batch=batch,
            import_fingerprint=fp,
            category=category,
        )
        created += 1

    return created, duplicates, unknown_account, date_format


def _parse_date(raw, formats):
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _import_ofx(upload, account, batch):
    try:
        from ofxparse import OfxParser
    except ImportError:
        raise RuntimeError("The 'ofxparse' package is required for OFX/QFX import. pip install ofxparse")

    ofx = OfxParser.parse(upload)
    created, duplicates = 0, 0
    for ofx_account in ofx.accounts:
        for txn in ofx_account.statement.transactions:
            txn_date = txn.date.date()
            amount = parse_amount(txn.amount)
            if amount is None:
                continue
            payee_name = (txn.payee or "").strip()
            fp = _fingerprint(account.id, txn_date, amount, payee_name)
            if Transaction.objects.filter(import_fingerprint=fp).exists():
                duplicates += 1
                continue
            payee = None
            if payee_name:
                payee = _get_or_create_payee(payee_name)
            Transaction.objects.create(
                account=account,
                date=txn_date,
                payee=payee,
                memo=getattr(txn, "memo", "") or "",
                amount=amount,
                import_batch=batch,
                import_fingerprint=fp,
                category=_guessed_category(payee),
            )
            created += 1
    # OFX has no per-row account text field and no ambiguous textual dates to detect.
    return created, duplicates, 0, None


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def _report_filters(request):
    """Shared by the reports page and every per-report export view, so both read the exact
    same filters off the querystring - what you export always matches what's on screen.

    On an invalid submission (R5, Phase 13), `form` is swapped for a fresh, unbound one so the
    widgets show the defaults actually being used rather than echoing back the rejected values;
    the original errors are kept separately in "form_errors" for the page's warning banner."""
    submitted = DateRangeFilterForm(request.GET or None)
    invalid = bool(request.GET) and not submitted.is_valid()
    filters = submitted.cleaned_data if submitted.is_valid() else {}
    account = filters.get("account")
    start, end, interval = resolve_range(filters, account=account)
    # "Average interval" is how many periods the same length as [start, end] to average over,
    # immediately preceding it - see reporting.vs_average_periods().
    try:
        lookback = int(interval)
    except (TypeError, ValueError):
        lookback = DEFAULT_LOOKBACK_PERIODS
    range_key = filters.get("range") or DEFAULT_RANGE_KEY
    periods = trimmed_average_periods(start, end, lookback, account=account, range_key=range_key)
    return {
        "form": DateRangeFilterForm() if invalid else submitted,
        "form_errors": submitted.errors if invalid else None,
        "start": start, "end": end, "lookback": lookback,
        "periods": periods, "average_periods": len(periods),
        # One earlier period isn't an average, and none at all is nothing to compare with -
        # the vs-average reports say so instead of drawing one (user, 16/09/2026).
        "has_average": len(periods) >= 2,
        "subtotal": filters.get("subtotal") or "",
        "account": account,
    }


def _stacked_series(periods, rows):
    """Transpose category_report_rows()' per-row-per-period shape into per-period lists of
    {name, value} - top-level categories only (R2), magnitudes only (a stack's height is never
    negative) - what the split-by-category column charts (reports 1/2 with a subtotal, and 5)
    draw from."""
    labels = periods or ["Total"]
    return [
        {
            "period": labels[i],
            "categories": [
                {"name": r["name"], "value": abs(float(r["values"][i]))}
                for r in rows if not r["is_child"] and abs(r["values"][i]) > 0
            ],
        }
        for i in range(len(labels))
    ]


def _top_level_rows(table, group_key):
    group = next((g for g in table["groups"] if g["key"] == group_key), None)
    return group["rows"] if group else []


def _vs_average_chart_rows(rows):
    """Like _float_rows(), but "average" may legitimately be None (R4: no earlier data) - Chart.js
    reads a JSON null as a gap in the marker line rather than crashing on it."""
    return [
        {"name": r["name"], "current": float(r["current"]), "average": None if r["average"] is None else float(r["average"])}
        for r in rows
    ]


def _vs_average_stat(label, current, average, higher_is_better, formatter):
    """current / average / difference for report 6's stat row, with a ▲▼ arrow that reflects
    the actual direction of change and a "good"/"bad" color that depends on the metric (R6:
    "green means good, so spending less is green" - a falling Expense is good, a falling
    Income isn't)."""
    if current is None or average is None:
        return {"label": label, "current": formatter(current) if current is not None else "—", "average": "—", "diff_text": "", "good": None}
    diff = current - average
    if diff == 0:
        return {"label": label, "current": formatter(current), "average": formatter(average), "diff_text": formatter(diff), "good": None}
    arrow = "▲" if diff > 0 else "▼"
    good = (diff > 0) == higher_is_better
    return {
        "label": label, "current": formatter(current), "average": formatter(average),
        "diff_text": f"{arrow} {formatter(abs(diff))}", "good": good,
    }


@login_required
def reports_view(request):
    f = _report_filters(request)
    start, end, periods, subtotal, account = f["start"], f["end"], f["periods"], f["subtotal"], f["account"]

    profile = Profile.for_user(request.user)
    main_currency = profile.main_currency
    rates = RateTable(main_currency)
    # A once-per-page-load warning (R7) - not per report, since the same missing rate would
    # otherwise repeat itself on every card that happens to touch that currency.
    missing_currencies = sorted({
        a.currency for a in Account.objects.exclude(currency=main_currency) if rates.known(a.currency) is None
    })

    uncategorized = uncategorized_count(start, end, account=account)

    # --- 1. Spending by category (chart: main categories only, R2) ---
    spending_by_category = category_totals_top_level(start, end, expense_only=True, account=account, rates=rates)
    spending_table = category_report_rows(start, end, subtotal=subtotal, expense_only=True, account=account, rates=rates)

    # --- 2. Income by category (new, mirrors Spending) ---
    income_by_category = category_totals_top_level(start, end, income_only=True, account=account, rates=rates)
    income_table = category_report_rows(start, end, subtotal=subtotal, income_only=True, account=account, rates=rates)

    # --- 3. Spending vs. average / 4. Income vs. average (new) - charts show main categories
    # only (R2), tables keep subcategories ---
    spending_vs_average = category_totals_vs_average(start, end, periods, expense_only=True, account=account, rates=rates, top_level=True)
    spending_vs_average_table = category_vs_average_report_rows(start, end, periods, expense_only=True, account=account, rates=rates)
    income_vs_average = category_totals_vs_average(start, end, periods, income_only=True, account=account, rates=rates, top_level=True)
    income_vs_average_table = category_vs_average_report_rows(start, end, periods, income_only=True, account=account, rates=rates)

    # --- 5. Income & expense by category - combined signed table (unchanged shape); the chart
    # is a stacked column per Subtotal period, split by category (R6) ---
    income_expense_table = category_report_rows(start, end, subtotal=subtotal, account=account, rates=rates)
    totals = income_and_spending(start, end, account=account, rates=rates)
    savings_rate_value = savings_rate(totals["income"], totals["spending"])
    net_total = totals["income"] - totals["spending"]
    income_expense_income_series = _stacked_series(income_expense_table["periods"], _top_level_rows(income_expense_table, "income"))
    income_expense_expense_series = _stacked_series(income_expense_table["periods"], _top_level_rows(income_expense_table, "expense"))

    # --- 6. Income & expense vs. average: Income/Expense (split by category, current period
    # only) + Net, each with an average marker ---
    vs_average_totals = income_and_spending_vs_average(start, end, periods, account=account, rates=rates)
    money_fmt = lambda v: format_money(v, main_currency)  # noqa: E731
    percent_fmt = lambda v: f"{v}%"  # noqa: E731
    vs_average_stats = [
        _vs_average_stat("Income", vs_average_totals["current_income"], vs_average_totals["average_income"], True, money_fmt),
        _vs_average_stat("Expense", vs_average_totals["current_spending"], vs_average_totals["average_spending"], False, money_fmt),
        _vs_average_stat("Net", vs_average_totals["current_net"], vs_average_totals["average_net"], True, money_fmt),
        _vs_average_stat("Savings rate", vs_average_totals["current_rate"], vs_average_totals["average_rate"], True, percent_fmt),
    ]
    # The table keeps the same per-category breakdown as report 5's, plus Income total/Expense
    # total/Net summary rows (already computed above) so the whole picture is in one table.
    income_expense_vs_average_table = category_vs_average_report_rows(start, end, periods, account=account, rates=rates)
    income_expense_vs_average_table["groups"] = income_expense_vs_average_table["groups"] + [{
        "key": "summary", "name": "Summary", "rows": [
            {
                "id": "summary_income", "parent_id": None, "name": "Income total", "is_child": False, "has_children": False,
                "current": vs_average_totals["current_income"], "average": vs_average_totals["average_income"],
                "difference": None if vs_average_totals["average_income"] is None else vs_average_totals["current_income"] - vs_average_totals["average_income"],
            },
            {
                "id": "summary_expense", "parent_id": None, "name": "Expense total", "is_child": False, "has_children": False,
                "current": vs_average_totals["current_spending"], "average": vs_average_totals["average_spending"],
                "difference": None if vs_average_totals["average_spending"] is None else vs_average_totals["current_spending"] - vs_average_totals["average_spending"],
            },
        ],
    }]

    # --- 7. Net worth over time - the chart plots the table's own Total row, so both follow
    # the Subtotal filter (month when none is chosen) ---
    net_worth_table = net_worth_by_account(start, end, subtotal or "month", rates=rates)

    # --- 8. Watched expenses (Settings-configurable, R8) ---
    watched_category_ids = watched_expenses_category_ids(profile)
    watched_totals = watched_expenses_totals(start, end, watched_category_ids, account=account, rates=rates)
    watched_table = None
    watched_series = []
    watched_chart_overflow = None
    if watched_category_ids:
        watched_table = category_report_rows(
            start, end, subtotal=subtotal, expense_only=True, account=account, rates=rates, category_ids=watched_category_ids,
        )
        watched_chart_overflow = subtotal_overflow_message(start, end, subtotal or "month")
        if not watched_chart_overflow:
            watched_series = watched_expenses_series(
                start, end, watched_category_ids, granularity=subtotal or "month", account=account, rates=rates,
            )

    context = {
        "form": f["form"],
        "form_errors": f["form_errors"],
        "start": start,
        "end": end,
        "subtotal": subtotal,
        "main_currency": main_currency,
        "missing_currencies": missing_currencies,
        "uncategorized_count": uncategorized,
        "average_periods": f["average_periods"],
        "has_average": f["has_average"],

        "spending_by_category": spending_by_category,
        "spending_chart": _float_rows(spending_by_category, "total"),
        "spending_stacked_chart": _stacked_series(spending_table["periods"], [r for g in spending_table["groups"] for r in g["rows"]]),
        "spending_table": spending_table,

        "income_by_category": income_by_category,
        "income_chart": _float_rows(income_by_category, "total"),
        "income_stacked_chart": _stacked_series(income_table["periods"], [r for g in income_table["groups"] for r in g["rows"]]),
        "income_table": income_table,

        "spending_vs_average": spending_vs_average,
        "spending_vs_average_chart": _vs_average_chart_rows(spending_vs_average),
        "spending_vs_average_table": spending_vs_average_table,

        "income_vs_average": income_vs_average,
        "income_vs_average_chart": _vs_average_chart_rows(income_vs_average),
        "income_vs_average_table": income_vs_average_table,

        "income_expense_table": income_expense_table,
        "income_expense_chart": {
            "periods": income_expense_table["periods"] or ["Total"],
            "income": income_expense_income_series,
            "expense": income_expense_expense_series,
        },
        "income_total": totals["income"],
        "expense_total": totals["spending"],
        "net_total": net_total,
        "savings_rate": savings_rate_value,

        "vs_average_totals": vs_average_totals,
        "vs_average_stats": vs_average_stats,
        "income_expense_vs_average_table": income_expense_vs_average_table,
        "income_expense_vs_average_chart": {
            "income": _float_rows(income_by_category, "total"),
            "expense": _float_rows(spending_by_category, "total"),
            "current_income": float(vs_average_totals["current_income"]),
            "current_expense": float(vs_average_totals["current_spending"]),
            "current_net": float(vs_average_totals["current_net"]),
            "average_income": None if vs_average_totals["average_income"] is None else float(vs_average_totals["average_income"]),
            "average_expense": None if vs_average_totals["average_spending"] is None else float(vs_average_totals["average_spending"]),
            "average_net": None if vs_average_totals["average_net"] is None else float(vs_average_totals["average_net"]),
        },

        "net_worth_chart": [
            {"label": label, "total": float(total)}
            for label, total in zip(net_worth_table["periods"], net_worth_table["totals"])
        ],
        "net_worth_table": net_worth_table,

        "watched_expenses_title": profile.watched_expenses_title,
        # Whether categories are *chosen* - not whether any were spent in this range, which is a
        # different question (an empty range still shows the chart and table, just at zero).
        "watched_has_categories": bool(watched_category_ids),
        "watched_totals": watched_totals,
        "watched_table": watched_table,
        "watched_chart_overflow": watched_chart_overflow,
        "watched_chart": [
            {
                "category_id": series["category_id"], "name": series["name"],
                "points": [{"period": p["period"], "total": float(p["total"])} for p in series["points"]],
            }
            for series in watched_series
        ],
    }
    return render(request, "finance/reports.html", context)


def _export_category_table(request, table, filename, amount_label):
    """Shared by every "category rows" report export (Spending, Income, Income & expense,
    Watched expenses) - same table shape category_report_rows() already builds for the page."""
    periods = table["periods"]
    headers = ["Category"] + (periods + ["Total"] if periods else [amount_label])
    rows = []
    for group in table["groups"]:
        for row in group["rows"]:
            name = ("  " if row["is_child"] else "") + row["name"]
            rows.append([name] + [str(v) for v in row["values"]] + ([str(row["total"])] if periods else []))
    if table["totals"]:
        rows.append(
            ["Total"] + [str(v) for v in table["totals"]["values"]] + ([str(table["totals"]["total"])] if periods else [])
        )
    return _export_response(request.GET.get("format", "csv"), filename, headers, rows)


def _export_vs_average_table(request, table, filename):
    headers = ["Category", "Current", "Average", "Difference"]
    rows = [
        [
            ("  " if row["is_child"] else "") + row["name"], str(row["current"]),
            "" if row["average"] is None else str(row["average"]),
            "" if row["difference"] is None else str(row["difference"]),
        ]
        for group in table["groups"] for row in group["rows"]
    ]
    if table["totals"]:
        t = table["totals"]
        rows.append(["Total", str(t["current"]), "" if t["average"] is None else str(t["average"]), "" if t["difference"] is None else str(t["difference"])])
    return _export_response(request.GET.get("format", "csv"), filename, headers, rows)


@login_required
def _export_rates(request):
    """The same RateTable every export view converts through, so a CSV export always matches
    the converted figures on screen (R7)."""
    return RateTable(Profile.for_user(request.user).main_currency)


@login_required
def export_report_spending(request):
    f = _report_filters(request)
    table = category_report_rows(f["start"], f["end"], subtotal=f["subtotal"], expense_only=True, account=f["account"], rates=_export_rates(request))
    return _export_category_table(request, table, f"stashy-spending-by-category-{date.today():%Y%m%d}", "Spending")


@login_required
def export_report_spending_vs_average(request):
    f = _report_filters(request)
    table = category_vs_average_report_rows(f["start"], f["end"], f["periods"], expense_only=True, account=f["account"], rates=_export_rates(request))
    return _export_vs_average_table(request, table, f"stashy-spending-vs-average-{date.today():%Y%m%d}")


@login_required
def export_report_income(request):
    f = _report_filters(request)
    table = category_report_rows(f["start"], f["end"], subtotal=f["subtotal"], income_only=True, account=f["account"], rates=_export_rates(request))
    return _export_category_table(request, table, f"stashy-income-by-category-{date.today():%Y%m%d}", "Income")


@login_required
def export_report_income_vs_average(request):
    f = _report_filters(request)
    table = category_vs_average_report_rows(f["start"], f["end"], f["periods"], income_only=True, account=f["account"], rates=_export_rates(request))
    return _export_vs_average_table(request, table, f"stashy-income-vs-average-{date.today():%Y%m%d}")


@login_required
def export_report_income_expense(request):
    f = _report_filters(request)
    table = category_report_rows(f["start"], f["end"], subtotal=f["subtotal"], account=f["account"], rates=_export_rates(request))
    return _export_category_table(request, table, f"stashy-income-expense-by-category-{date.today():%Y%m%d}", "Amount")


@login_required
def export_report_income_expense_vs_average(request):
    f = _report_filters(request)
    table = category_vs_average_report_rows(f["start"], f["end"], f["periods"], account=f["account"], rates=_export_rates(request))
    return _export_vs_average_table(request, table, f"stashy-income-expense-vs-average-{date.today():%Y%m%d}")


@login_required
def export_report_watched_expenses(request):
    f = _report_filters(request)
    profile = Profile.for_user(request.user)
    category_ids = watched_expenses_category_ids(profile)
    if not category_ids:
        return HttpResponse("No Watched expenses categories are chosen yet - add some in Settings.", status=404)
    table = category_report_rows(
        f["start"], f["end"], subtotal=f["subtotal"], expense_only=True, account=f["account"], category_ids=category_ids,
        rates=RateTable(profile.main_currency),
    )
    return _export_category_table(request, table, f"stashy-watched-expenses-{date.today():%Y%m%d}", "Spending")


@login_required
def export_report_net_worth(request):
    f = _report_filters(request)
    table = net_worth_by_account(f["start"], f["end"], f["subtotal"] or "month", rates=_export_rates(request))
    headers = ["Account"] + table["periods"]
    rows = []
    for group in table["groups"]:
        rows.append([group["name"]] + [str(v) for v in group["totals"]])
        for row in group["rows"]:
            rows.append(["  " + row["name"]] + [str(v) for v in row["values"]])
    rows.append(["Total"] + [str(v) for v in table["totals"]])
    return _export_response(
        request.GET.get("format", "csv"), f"stashy-net-worth-{date.today():%Y%m%d}", headers, rows
    )


def _float_rows(rows, *decimal_keys):
    """Copies a list of row dicts with the given Decimal keys converted to float, for the
    JSON handed to Chart.js - keeps the original Decimal rows intact for the server-rendered
    table (money stays Decimal for every calculation; float only at the JSON boundary)."""
    return [{**row, **{key: float(row[key]) for key in decimal_keys}} for row in rows]


# ---------------------------------------------------------------------------
# Settings (D2 main currency, D10 regional preferences)
# ---------------------------------------------------------------------------

def _stash_backup_root(request):
    """Each stash keeps its own backups/ folder next to its own db.sqlite3 (Phase 12) -
    reachable only once a stash is selected for this session (finance.middleware.
    RequireStashMiddleware blocks every finance URL, this one included, until then). Always
    BASE_DIR under `manage.py test`: TESTING also bypasses that middleware gate (see
    finance/middleware.py), and whatever a real session happens to have selected on the
    machine running the suite must never change what a test expects - this project's fixtures
    all assume BASE_DIR."""
    stash_name = request.session.get("stash") if not settings.TESTING else None
    if stash_name:
        return settings.STASHES_DIR / stash_name
    return Path(settings.BASE_DIR)


@login_required
def settings_view(request):
    profile = Profile.for_user(request.user)
    currencies = convertible_currencies(Account.objects.all()) or [profile.main_currency]

    is_pin_change = request.method == "POST" and "change_pin" in request.POST
    if is_pin_change:
        pin_form = ChangePinForm(request.POST, user=request.user)
        if pin_form.is_valid():
            request.user.set_password(pin_form.cleaned_data["new_pin"])
            request.user.save(update_fields=["password"])
            update_session_auth_hash(request, request.user)
            messages.success(request, "PIN changed.")
            return redirect("finance:settings")
        messages.error(request, "PIN wasn't changed - see the errors below.")
    else:
        pin_form = ChangePinForm(user=request.user)

    if request.method == "POST" and "check_rates" in request.POST:
        try:
            stored, unavailable = fetch_latest_rates(profile.main_currency, currencies)
        except RateFetchError as error:
            messages.error(request, str(error))
        else:
            if stored:
                messages.success(request, f"Updated {len(stored)} exchange rate{'s' if len(stored) != 1 else ''}.")
            if unavailable:
                messages.warning(request, f"No rate available for: {', '.join(unavailable)}. Enter it by hand in the admin if you need one.")
        return redirect("finance:settings")

    backup_root = _stash_backup_root(request)
    if request.method == "POST" and "backup_now" in request.POST:
        backup = finance_backup.backup_now(_current_stash_db_path(request), backup_root)
        if backup:
            messages.success(request, f"Backed up to {backup.relative_to(backup_root)}.")
        else:
            messages.error(request, "Nothing to back up yet - there's no database file.")
        return redirect("finance:settings")

    is_watched_expenses_save = request.method == "POST" and "save_watched_expenses" in request.POST
    if is_watched_expenses_save:
        watched_form = WatchedExpensesForm(request.POST, instance=profile)
        if watched_form.is_valid():
            watched_form.save()
            messages.success(request, "Watched expenses saved.")
            return redirect("finance:settings")
        messages.error(request, "Watched expenses weren't saved - see the errors below.")
    else:
        watched_form = WatchedExpensesForm(instance=profile)

    if request.method == "POST" and not is_pin_change and not is_watched_expenses_save:
        form = ProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Preferences saved.")
            return redirect("finance:settings")
    else:
        form = ProfileForm(instance=profile)

    watched_selected_values = {str(pk) for pk in profile.watched_expenses_categories.values_list("pk", flat=True)}
    watched_count = len(watched_selected_values)
    if watched_count == 0:
        watched_button_label = "No categories chosen"
    elif watched_count == 1:
        watched_button_label = next(iter(profile.watched_expenses_categories.all())).full_name
    else:
        watched_button_label = f"{watched_count} categories selected"

    rates = RateTable(profile.main_currency)
    known_rates = [
        (code, rates.known(code)) for code in currencies if code != profile.main_currency
    ]
    recent_backups = finance_backup.list_backups(backup_root, limit=5)
    return render(request, "finance/settings.html", {
        "form": form,
        "watched_form": watched_form,
        "watched_category_groups": grouped_categories(
            Category.objects.filter(category_type=Category.CategoryType.EXPENSE, account__isnull=True)
        ),
        "watched_selected_values": watched_selected_values,
        "watched_button_label": watched_button_label,
        "pin_form": pin_form,
        "stash_name": request.session.get("stash", ""),
        "check_rates_form": CheckRatesForm(),
        "profile": profile,
        "known_rates": known_rates,
        "recent_backups": [
            {"name": p.name, "size_kb": p.stat().st_size // 1024} for p in recent_backups
        ],
    })


def _current_stash_db_path(request):
    """The actual db.sqlite3 file behind this session's selected stash - not
    settings.DATABASES["default"]["NAME"], which since the Phase 12 live-switching rewrite is
    always the inert picker placeholder, never a real stash's own file."""
    stash_name = request.session.get("stash") if not settings.TESTING else None
    if stash_name:
        return stashes.stash_db_path(stash_name)
    return settings.DATABASES["default"]["NAME"]


def _backup_path_or_404(request, name):
    """Only ever resolves to a file that's actually inside the current stash's own backups/
    - `name` comes straight off the querystring, so this guards against it smuggling in a
    path (e.g. "../../other-stash/db.sqlite3")."""
    backup_root = _stash_backup_root(request)
    candidate = backup_root / "backups" / Path(name).name
    if candidate not in finance_backup.list_backups(backup_root):
        raise Http404("No such backup.")
    return candidate


@login_required
def backup_restore(request):
    """Restoring overwrites the live database, so this is a confirm-then-act page (like a
    delete) rather than a one-click button on Settings - a wrong pick here is far more costly
    than a wrong pick almost anywhere else in the app."""
    name = request.POST.get("name") or request.GET.get("name", "")
    backup_path = _backup_path_or_404(request, name)

    if request.method == "POST":
        db_path = _current_stash_db_path(request)
        finance_backup.restore_from(backup_path, db_path, _stash_backup_root(request))
        # The live server may already hold an open connection to this stash's file (this
        # request's own thread included) - close this thread's so its very next query
        # reconnects to the file as just restored, rather than a stale cached connection.
        stash_name = request.session.get("stash")
        if stash_name and not settings.TESTING:
            connections[stash_name].close()
        messages.success(
            request,
            f"Restored from {backup_path.name}. A safety backup of what was there before is in backups/ too.",
        )
        return redirect("finance:settings")

    return render(request, "finance/backup_restore_confirm.html", {"backup": backup_path.name})
