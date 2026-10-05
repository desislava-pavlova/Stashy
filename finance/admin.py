from django.contrib import admin
from django.contrib.auth.models import Group, User

from audit.admin import AuditHistoryAdminMixin

from .models import Account, Category, ExchangeRate, ImportBatch, Payee, Profile, Reconciliation, Reminder, Transaction

# A1: the admin is used by regular people, not a developer - Users and Groups (a single shared
# login per stash, no group permissions) have nothing useful to show them.
admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(Profile)
class ProfileAdmin(AuditHistoryAdminMixin, admin.ModelAdmin):
    """View-only (A1): a stash's one Profile row is edited from Settings, not here. The
    recovery-code hash and PIN-lockout fields are left off entirely - they're operational
    secrets/state, not preferences."""

    list_display = ("user", "main_currency", "date_format", "number_format", "time_zone", "first_day_of_week")
    fields = (
        "user",
        "main_currency",
        "time_zone",
        "date_format",
        "number_format",
        "first_day_of_week",
        "watched_expenses_title",
        "watched_expenses_categories",
    )
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Account)
class AccountAdmin(AuditHistoryAdminMixin, admin.ModelAdmin):
    list_display = ("name", "account_type", "currency", "institution", "balance", "is_active")
    list_filter = ("account_type", "currency", "is_active")
    search_fields = ("name", "institution")

    def get_queryset(self, request):
        # Every balance in the list comes from the same query, not one extra query per account.
        return super().get_queryset(request).with_balances()

    def get_readonly_fields(self, request, obj=None):
        # Account.clean() refuses a new currency once transactions exist; show it as text instead.
        if obj is not None and obj.transactions.exists():
            return (*super().get_readonly_fields(request, obj), "currency")
        return super().get_readonly_fields(request, obj)


@admin.register(ExchangeRate)
class ExchangeRateAdmin(AuditHistoryAdminMixin, admin.ModelAdmin):
    list_display = ("date", "base_currency", "quote_currency", "rate", "source")
    list_filter = ("source", "base_currency", "quote_currency")
    date_hierarchy = "date"


@admin.register(Category)
class CategoryAdmin(AuditHistoryAdminMixin, admin.ModelAdmin):
    list_display = ("full_name", "category_type", "parent", "account", "is_auto_generated")
    list_filter = ("category_type", "is_auto_generated")
    search_fields = ("name",)


@admin.register(Payee)
class PayeeAdmin(AuditHistoryAdminMixin, admin.ModelAdmin):
    list_display = ("name", "is_active")
    search_fields = ("name",)


@admin.register(Transaction)
class TransactionAdmin(AuditHistoryAdminMixin, admin.ModelAdmin):
    list_display = ("date", "transaction_number", "account", "display_payee", "category", "amount", "reconciled")
    list_filter = ("account", "category", "reconciled")
    search_fields = ("payee__name", "memo")
    date_hierarchy = "date"
    autocomplete_fields = ("payee", "category", "account")


@admin.register(ImportBatch)
class ImportBatchAdmin(AuditHistoryAdminMixin, admin.ModelAdmin):
    list_display = ("source_filename", "account", "imported_at", "row_count", "duplicate_count")


@admin.register(Reminder)
class ReminderAdmin(AuditHistoryAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "account", "reminder_type", "signed_amount", "frequency", "next_due_date", "status")
    list_filter = ("reminder_type", "frequency")


@admin.register(Reconciliation)
class ReconciliationAdmin(AuditHistoryAdminMixin, admin.ModelAdmin):
    list_display = (
        "account",
        "statement_date",
        "statement_balance",
        "opening_balance",
        "adjustment_transaction",
        "created_at",
    )
    list_filter = ("account",)
    date_hierarchy = "statement_date"
