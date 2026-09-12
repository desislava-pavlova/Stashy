import csv
import hashlib
import io
import json
from datetime import date

from dateutil.relativedelta import relativedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from .forms import (
    AccountForm, CategoryForm, DateRangeFilterForm, ImportUploadForm,
    PayeeForm, ReminderForm, TransactionForm,
)
from .models import Account, Category, ImportBatch, Payee, Reminder, Tag, Transaction
from .reporting import resolve_range, trunc_func


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):
    accounts = Account.objects.filter(is_active=True)
    net_worth = sum((a.balance for a in accounts), start=0)

    today = date.today()
    month_start = today.replace(day=1)
    month_spending = (
        Transaction.objects.filter(date__gte=month_start, date__lte=today, amount__lt=0)
        .aggregate(total=Sum("amount"))["total"] or 0
    )
    month_income = (
        Transaction.objects.filter(date__gte=month_start, date__lte=today, amount__gt=0)
        .aggregate(total=Sum("amount"))["total"] or 0
    )

    recent_transactions = Transaction.objects.select_related("account", "category", "payee")[:10]
    upcoming_reminders = Reminder.objects.filter(is_active=True).order_by("next_due_date")[:6]

    # Spending-by-category donut for the current month
    category_totals = (
        Transaction.objects.filter(date__gte=month_start, date__lte=today, amount__lt=0, category__isnull=False)
        .values("category__name")
        .annotate(total=Sum("amount"))
        .order_by("total")[:8]
    )

    context = {
        "accounts": accounts,
        "net_worth": net_worth,
        "month_spending": abs(month_spending),
        "month_income": month_income,
        "recent_transactions": recent_transactions,
        "upcoming_reminders": upcoming_reminders,
        "category_totals": list(category_totals),
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
        return Account.objects.all()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["net_worth"] = sum((a.balance for a in ctx["accounts"]), start=0)
        return ctx


@login_required
def account_detail(request, pk):
    account = get_object_or_404(Account, pk=pk)
    transactions = account.transactions.select_related("category", "payee")[:200]
    return render(request, "finance/account_detail.html", {"account": account, "transactions": transactions})


class AccountCreateView(LoginRequiredMixin, CreateView):
    model = Account
    form_class = AccountForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:account_list")
    extra_context = {"title": "Add account"}


class AccountUpdateView(LoginRequiredMixin, UpdateView):
    model = Account
    form_class = AccountForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:account_list")
    extra_context = {"title": "Edit account"}


class AccountDeleteView(LoginRequiredMixin, DeleteView):
    model = Account
    template_name = "finance/generic_confirm_delete.html"
    success_url = reverse_lazy("finance:account_list")


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

class CategoryListView(LoginRequiredMixin, ListView):
    model = Category
    template_name = "finance/category_list.html"
    context_object_name = "categories"
    queryset = Category.objects.select_related("parent")


class CategoryCreateView(LoginRequiredMixin, CreateView):
    model = Category
    form_class = CategoryForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:category_list")
    extra_context = {"title": "Add category"}


class CategoryUpdateView(LoginRequiredMixin, UpdateView):
    model = Category
    form_class = CategoryForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:category_list")
    extra_context = {"title": "Edit category"}


class CategoryDeleteView(LoginRequiredMixin, DeleteView):
    model = Category
    template_name = "finance/generic_confirm_delete.html"
    success_url = reverse_lazy("finance:category_list")


# ---------------------------------------------------------------------------
# Payees (memorized payees)
# ---------------------------------------------------------------------------

class PayeeListView(LoginRequiredMixin, ListView):
    model = Payee
    template_name = "finance/payee_list.html"
    context_object_name = "payees"


class PayeeCreateView(LoginRequiredMixin, CreateView):
    model = Payee
    form_class = PayeeForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:payee_list")
    extra_context = {"title": "Add memorized payee"}


class PayeeUpdateView(LoginRequiredMixin, UpdateView):
    model = Payee
    form_class = PayeeForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:payee_list")
    extra_context = {"title": "Edit payee"}


class PayeeDeleteView(LoginRequiredMixin, DeleteView):
    model = Payee
    template_name = "finance/generic_confirm_delete.html"
    success_url = reverse_lazy("finance:payee_list")


@login_required
def api_payee_autofill(request):
    """Given ?name=..., return the memorized defaults (category, memo, amount) for autofill."""
    name = request.GET.get("name", "").strip()
    if not name:
        return JsonResponse({})
    payee = Payee.objects.filter(name__iexact=name).first()
    if not payee:
        return JsonResponse({})
    return JsonResponse({
        "category_id": payee.default_category_id,
        "category_name": str(payee.default_category) if payee.default_category_id else "",
        "memo": payee.default_memo,
        "amount": str(payee.default_amount) if payee.default_amount is not None else "",
    })


@login_required
def api_payee_list(request):
    names = list(Payee.objects.filter(is_active=True).values_list("name", flat=True))
    return JsonResponse({"payees": names})


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

@login_required
def transaction_list(request):
    transactions = Transaction.objects.select_related("account", "category", "payee")

    account_id = request.GET.get("account")
    category_id = request.GET.get("category")
    search = request.GET.get("q")

    if account_id:
        transactions = transactions.filter(account_id=account_id)
    if category_id:
        transactions = transactions.filter(category_id=category_id)
    if search:
        transactions = transactions.filter(
            Q(payee__name__icontains=search) | Q(payee_name__icontains=search) | Q(memo__icontains=search)
        )

    context = {
        "transactions": transactions[:500],
        "accounts": Account.objects.all(),
        "categories": Category.objects.all(),
        "selected_account": account_id,
        "selected_category": category_id,
        "search": search or "",
    }
    return render(request, "finance/transaction_list.html", context)


@login_required
def transaction_form_view(request, pk=None):
    instance = get_object_or_404(Transaction, pk=pk) if pk else None
    initial = {}
    if instance and instance.payee_id:
        initial["payee_name"] = instance.payee.name
    if not instance:
        default_account = request.GET.get("account")
        if default_account:
            initial["account"] = default_account
        initial["date"] = date.today()

    if request.method == "POST":
        form = TransactionForm(request.POST, instance=instance, initial=initial)
        if form.is_valid():
            txn = form.save()
            messages.success(request, "Transaction saved.")
            return redirect("finance:account_detail", pk=txn.account_id)
    else:
        form = TransactionForm(instance=instance, initial=initial)

    return render(request, "finance/transaction_form.html", {"form": form, "instance": instance})


class TransactionDeleteView(LoginRequiredMixin, DeleteView):
    model = Transaction
    template_name = "finance/generic_confirm_delete.html"

    def get_success_url(self):
        return reverse_lazy("finance:account_detail", args=[self.object.account_id])


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

@login_required
def reminder_list(request):
    reminders = Reminder.objects.select_related("account", "category", "payee")
    return render(request, "finance/reminder_list.html", {"reminders": reminders})


class ReminderCreateView(LoginRequiredMixin, CreateView):
    model = Reminder
    form_class = ReminderForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:reminder_list")
    extra_context = {"title": "Add reminder"}


class ReminderUpdateView(LoginRequiredMixin, UpdateView):
    model = Reminder
    form_class = ReminderForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:reminder_list")
    extra_context = {"title": "Edit reminder"}


class ReminderDeleteView(LoginRequiredMixin, DeleteView):
    model = Reminder
    template_name = "finance/generic_confirm_delete.html"
    success_url = reverse_lazy("finance:reminder_list")


@login_required
def reminder_enter(request, pk):
    """Create the actual transaction for a due reminder, then roll it forward."""
    reminder = get_object_or_404(Reminder, pk=pk)
    Transaction.objects.create(
        account=reminder.account,
        date=reminder.next_due_date,
        payee=reminder.payee,
        category=reminder.category,
        memo=reminder.notes or reminder.name,
        amount=reminder.signed_amount(),
    )
    reminder.advance_due_date()
    messages.success(request, f"Entered '{reminder.name}' and moved it to {reminder.next_due_date}.")
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

            batch = ImportBatch.objects.create(account=account, source_filename=upload.name)
            created, duplicates = 0, 0

            if filename.endswith(".csv"):
                created, duplicates = _import_csv(upload, account, batch)
            elif filename.endswith(".ofx") or filename.endswith(".qfx"):
                created, duplicates = _import_ofx(upload, account, batch)
            else:
                messages.error(request, "Unsupported file type. Please upload a .csv, .ofx, or .qfx file.")
                batch.delete()
                return redirect("finance:import_transactions")

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
    raw = f"{account_id}|{txn_date}|{amount}|{payee}".encode()
    return hashlib.sha256(raw).hexdigest()


def _import_csv(upload, account, batch):
    text = io.TextIOWrapper(upload.file, encoding="utf-8-sig")
    reader = csv.DictReader(text)
    created, duplicates = 0, 0

    # Normalize header names so common bank export variants all work.
    fieldmap = {name.strip().lower(): name for name in (reader.fieldnames or [])}

    def get(row, *keys):
        for k in keys:
            if k in fieldmap:
                return row.get(fieldmap[k], "")
        return ""

    for row in reader:
        raw_date = get(row, "date", "transaction date").strip()
        raw_amount = get(row, "amount").strip().replace(",", "")
        payee_name = get(row, "payee", "description", "name").strip()
        memo = get(row, "memo", "notes").strip()

        if not raw_date or not raw_amount:
            continue

        txn_date = _parse_date(raw_date)
        if not txn_date:
            continue

        try:
            amount = float(raw_amount)
        except ValueError:
            continue

        fp = _fingerprint(account.id, txn_date, amount, payee_name)
        if Transaction.objects.filter(import_fingerprint=fp).exists():
            duplicates += 1
            continue

        payee = None
        if payee_name:
            payee, _ = Payee.objects.get_or_create(name=payee_name)

        Transaction.objects.create(
            account=account,
            date=txn_date,
            payee=payee,
            memo=memo,
            amount=amount,
            import_batch=batch,
            import_fingerprint=fp,
            category=payee.default_category if payee else None,
        )
        created += 1

    return created, duplicates


def _parse_date(raw):
    from datetime import datetime

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
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
            amount = float(txn.amount)
            payee_name = (txn.payee or "").strip()
            fp = _fingerprint(account.id, txn_date, amount, payee_name)
            if Transaction.objects.filter(import_fingerprint=fp).exists():
                duplicates += 1
                continue
            payee = None
            if payee_name:
                payee, _ = Payee.objects.get_or_create(name=payee_name)
            Transaction.objects.create(
                account=account,
                date=txn_date,
                payee=payee,
                memo=getattr(txn, "memo", "") or "",
                amount=amount,
                import_batch=batch,
                import_fingerprint=fp,
                category=payee.default_category if payee else None,
            )
            created += 1
    return created, duplicates


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@login_required
def reports_view(request):
    form = DateRangeFilterForm(request.GET or None)
    form.is_valid()
    start, end, interval = resolve_range(form.cleaned_data if form.is_valid() else {})

    qs = Transaction.objects.filter(date__gte=start, date__lte=end)
    if form.is_valid() and form.cleaned_data.get("account"):
        qs = qs.filter(account=form.cleaned_data["account"])
    if form.is_valid() and form.cleaned_data.get("category"):
        qs = qs.filter(category=form.cleaned_data["category"])

    # --- Spending by category ---
    spending_by_category = list(
        qs.filter(amount__lt=0, category__isnull=False)
        .values("category__name")
        .annotate(total=Sum("amount"))
        .order_by("total")
    )
    for row in spending_by_category:
        row["total"] = abs(row["total"])

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
            "income": float(row["income"] or 0),
            "expense": abs(float(row["expense"] or 0)),
        }
        for row in timeseries
    ]

    # --- Current period spending vs historical average (same interval type) ---
    current_period_start = _current_interval_start(interval)
    current_spending = abs(
        Transaction.objects.filter(date__gte=current_period_start, amount__lt=0)
        .aggregate(total=Sum("amount"))["total"] or 0
    )
    history_start = current_period_start - relativedelta(months=12)
    history_qs = (
        Transaction.objects.filter(date__gte=history_start, date__lt=current_period_start, amount__lt=0)
        .annotate(period=trunc_func(interval))
        .values("period")
        .annotate(total=Sum("amount"))
    )
    history_totals = [abs(float(r["total"] or 0)) for r in history_qs]
    average_spending = sum(history_totals) / len(history_totals) if history_totals else 0

    # --- Net worth over time (running balance across all accounts, sampled monthly) ---
    net_worth_series = _net_worth_series(start, end)

    context = {
        "form": form,
        "start": start,
        "end": end,
        "interval": interval,
        "spending_by_category": json.dumps(spending_by_category, default=float),
        "timeseries": json.dumps(timeseries),
        "current_spending": current_spending,
        "average_spending": round(average_spending, 2),
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
    """Sample total net worth (starting balances + all transactions to date)
    at each month-end between start and end."""
    starting_total = Account.objects.aggregate(total=Sum("starting_balance"))["total"] or 0

    points = []
    cursor = start.replace(day=1)
    while cursor <= end:
        month_end = (cursor + relativedelta(months=1)) - relativedelta(days=1)
        sample_date = min(month_end, end)
        txns_to_date = Transaction.objects.filter(date__lte=sample_date).aggregate(total=Sum("amount"))["total"] or 0
        points.append({"period": sample_date.isoformat(), "net_worth": float(starting_total + txns_to_date)})
        cursor += relativedelta(months=1)
    return points
