from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from ..content import CATEGORY_TIPS
from ..forms import CategoryForm
from ..models import Category, Transaction, grouped_categories


class CategoryListView(ListView):
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


class CategoryCreateView(CreateView):
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
            messages.error(
                request,
                f"'{self.object.full_name}' belongs to the {self.object.account.name} account"
                " and is managed automatically.",
            )
            return redirect("finance:category_list")
        return super().dispatch(request, *args, **kwargs)


class CategoryUpdateView(SystemManagedCategoryGuard, UpdateView):
    model = Category
    form_class = CategoryForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:category_list")
    extra_context = {"title": "Edit category", "tips": CATEGORY_TIPS, "category_type_filter": True}


class CategoryDeleteView(SystemManagedCategoryGuard, DeleteView):
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


def category_usage(request, pk):
    """A category's own transactions (not its subcategories'), with a mass re-categorize tool."""
    category = get_object_or_404(Category, pk=pk)
    transactions = category.transactions.select_related("account", "payee")

    if request.method == "POST":
        selected_ids = request.POST.getlist("transaction_id")
        target = (
            Category.objects.filter(pk=request.POST.get("move_to"))
            .exclude(Q(pk=category.pk) | Q(account__isnull=False))
            .first()
        )
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
    return render(
        request,
        "finance/category_usage.html",
        {
            "category": category,
            "transactions": transactions,
            "other_categories": other_categories,
        },
    )
