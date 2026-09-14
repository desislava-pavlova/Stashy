import csv
import hashlib
import io
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Sum, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from audit.context import system_action

from . import regional
from .currency import RateFetchError, RateTable, SUFFIX_CURRENCIES, convert_balances, fetch_latest_rates, format_money
from .forms import (
    AccountCreateForm, AccountForm, CategoryForm, CheckRatesForm, DateRangeFilterForm, ImportUploadForm,
    PayeeForm, ProfileForm, ReconcileStartForm, ReminderForm, TransactionForm, TransferForm,
    convertible_currencies,
)
from .models import (
    Account, Category, ImportBatch, Payee, Profile, Reconciliation, Reminder, Tag, Transaction,
    grouped_accounts, grouped_categories,
)
from .money import CENT, ZERO, parse_amount
from .reporting import excluding_transfers, income_and_spending, resolve_range, savings_rate, spending_tree, trunc_func

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
        "categories": Category.objects.select_related("parent").exclude(account__isnull=False),
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


@login_required
def transaction_list(request):
    transactions = Transaction.objects.select_related("account", "category__parent", "payee")

    account_id = request.GET.get("account", "")
    category_id = request.GET.get("category", "")
    status = request.GET.get("status", "")
    search = request.GET.get("q", "").strip()
    with_subcategories = request.GET.get("with_subcategories") == "1"
    selected_category = None

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
    if category_id == "none":
        transactions = transactions.filter(category__isnull=True)
    elif category_id.isdigit():
        selected_category = Category.objects.filter(pk=category_id).first()
        category_ids = [int(category_id)]
        if with_subcategories and selected_category:
            category_ids += [sub.pk for sub in selected_category.descendants()]
        transactions = transactions.filter(category_id__in=category_ids)
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

    transactions, sort, sort_next = _sorted(request, transactions, TRANSACTION_LIST_SORT_FIELDS)

    context = {
        "transactions": transactions,
        "accounts": Account.objects.all(),
        "categories": Category.objects.select_related("parent"),
        "selected_account": account_id,
        "selected_category": category_id,
        "selected_category_obj": selected_category,
        "with_subcategories": with_subcategories,
        "status": status,
        "range": range_key,
        "range_choices": TRANSACTION_RANGE_CHOICES,
        "start": start,
        "end": end,
        "sort": sort,
        "sort_next": sort_next,
        "search": search,
    }
    return render(request, "finance/transaction_list.html", context)


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
                importer = _import_csv if filename.endswith(".csv") else _import_ofx
                created, duplicates = importer(upload, account, batch)
                batch.row_count = created
                batch.duplicate_count = duplicates
                batch.save(update_fields=["row_count", "duplicate_count"])
            messages.success(request, f"Imported {created} transactions ({duplicates} duplicates skipped).")
            return redirect("finance:account_detail", pk=account.pk)
    else:
        form = ImportUploadForm()

    recent_batches = ImportBatch.objects.select_related("account")[:15]
    return render(request, "finance/import_form.html", {"form": form, "recent_batches": recent_batches})


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
    """The category to pre-fill for an imported row: this payee's most recently used one, if any."""
    if payee is None:
        return None
    history = payee.category_history()
    return history[0].category if history else None


def _import_csv(upload, account, batch):
    text = io.TextIOWrapper(upload.file, encoding="utf-8-sig")
    reader = csv.DictReader(text)
    created, duplicates = 0, 0

    # Normalize header names so common bank export variants all work.
    fieldmap = {name.strip().lower(): name for name in (reader.fieldnames or [])}

    def get(row, *keys):
        for k in keys:
            if k in fieldmap:
                return (row.get(fieldmap[k]) or "").strip()
        return ""

    for row in reader:
        raw_date = get(row, "date", "transaction date")
        raw_amount = get(row, "amount")
        payee_name = get(row, "payee", "description", "name")
        memo = get(row, "memo", "notes")

        if not raw_date or not raw_amount:
            continue

        txn_date = _parse_date(raw_date)
        if not txn_date:
            continue

        amount = parse_amount(raw_amount)
        if amount is None:
            continue

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
            memo=memo,
            amount=amount,
            import_batch=batch,
            import_fingerprint=fp,
            category=_guessed_category(payee),
        )
        created += 1

    return created, duplicates


# European formats only: mixing in American MM/DD would silently swap day and
# month for any date where both are <= 12 (e.g. 03/04 read as 4 March).
CSV_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%y", "%d.%m.%y")


def _parse_date(raw):
    for fmt in CSV_DATE_FORMATS:
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
    return created, duplicates


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@login_required
def reports_view(request):
    form = DateRangeFilterForm(request.GET or None)
    filters = form.cleaned_data if form.is_valid() else {}
    start, end, interval = resolve_range(filters)

    qs = excluding_transfers(Transaction.objects.filter(date__gte=start, date__lte=end))
    if filters.get("account"):
        qs = qs.filter(account=filters["account"])
    if filters.get("category"):
        qs = qs.filter(category=filters["category"])

    # --- Spending by category ---
    spending_by_category = list(
        qs.filter(amount__lt=0, category__isnull=False)
        .values("category__name")
        .annotate(total=Sum("amount"))
        .order_by("total")
    )
    # Money stays Decimal for every calculation; float only at the JSON boundary for Chart.js.
    for row in spending_by_category:
        row["total"] = float(abs(row["total"]))

    # --- Income vs expense by category (over time, bucketed by interval) ---
    trunc = trunc_func(interval)
    timeseries = (
        qs.annotate(period=trunc)
        .values("period")
        .annotate(
            income=Sum("amount", filter=Q(amount__gt=0)),
            expense=Sum("amount", filter=Q(amount__lt=0)),
        )
        .order_by("period")
    )
    timeseries = [
        {
            "period": row["period"].isoformat() if row["period"] else "",
            "income": float(row["income"] or ZERO),
            "expense": float(abs(row["expense"] or ZERO)),
        }
        for row in timeseries
    ]

    # --- Current period spending vs historical average (same interval type) ---
    current_period_start = _current_interval_start(interval)
    current_spending = abs(
        excluding_transfers(Transaction.objects.filter(date__gte=current_period_start, amount__lt=0))
        .aggregate(total=Sum("amount"))["total"] or ZERO
    )
    history_start = current_period_start - relativedelta(months=12)
    history_qs = (
        excluding_transfers(Transaction.objects.filter(date__gte=history_start, date__lt=current_period_start, amount__lt=0))
        .annotate(period=trunc_func(interval))
        .values("period")
        .annotate(total=Sum("amount"))
    )
    history_totals = [abs(r["total"] or ZERO) for r in history_qs]
    average_spending = (
        (sum(history_totals, ZERO) / len(history_totals)).quantize(CENT) if history_totals else ZERO
    )

    # --- Net worth over time (running balance across all accounts, sampled monthly) ---
    net_worth_series = _net_worth_series(start, end)

    context = {
        "form": form,
        "start": start,
        "end": end,
        "interval": interval,
        # Reports sum every account's transactions without currency conversion (see the
        # Phase 3 backlog note) - this label is accurate for a single-currency setup and
        # a known simplification otherwise.
        "main_currency": Profile.for_user(request.user).main_currency,
        "spending_by_category": json.dumps(spending_by_category),
        "timeseries": json.dumps(timeseries),
        "current_spending": current_spending,
        "average_spending": average_spending,
        "net_worth_series": json.dumps(net_worth_series),
    }
    return render(request, "finance/reports.html", context)


def _current_interval_start(interval):
    today = date.today()
    if interval == "day":
        return today
    if interval == "week":
        return today - relativedelta(days=today.weekday())
    if interval == "quarter":
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=q_start_month, day=1)
    if interval == "year":
        return today.replace(month=1, day=1)
    return today.replace(day=1)  # month default


def _net_worth_series(start, end):
    """Sample total net worth (all transactions to date, opening balances included per D1)
    at each month-end between start and end.

    Note: this sums every account's transactions in its own currency without conversion — a
    known simplification for Phase 3 (see D2), since historical per-day exchange rates are a
    bigger feature. It's exact for a single-currency setup and only used for this one chart.
    """
    points = []
    cursor = start.replace(day=1)
    while cursor <= end:
        month_end = (cursor + relativedelta(months=1)) - relativedelta(days=1)
        sample_date = min(month_end, end)
        txns_to_date = Transaction.objects.filter(date__lte=sample_date).aggregate(total=Sum("amount"))["total"] or ZERO
        points.append({"period": sample_date.isoformat(), "net_worth": float(txns_to_date)})
        cursor += relativedelta(months=1)
    return points


# ---------------------------------------------------------------------------
# Settings (D2 main currency, D10 regional preferences)
# ---------------------------------------------------------------------------

@login_required
def settings_view(request):
    profile = Profile.for_user(request.user)
    currencies = convertible_currencies(Account.objects.all()) or [profile.main_currency]

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

    if request.method == "POST":
        form = ProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Preferences saved.")
            return redirect("finance:settings")
    else:
        form = ProfileForm(instance=profile)

    rates = RateTable(profile.main_currency)
    known_rates = [
        (code, rates.known(code)) for code in currencies if code != profile.main_currency
    ]
    return render(request, "finance/settings.html", {
        "form": form,
        "check_rates_form": CheckRatesForm(),
        "profile": profile,
        "known_rates": known_rates,
    })
