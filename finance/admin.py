from django.contrib import admin

from audit.admin import AuditHistoryAdminMixin

from .models import Account, Category, ExchangeRate, ImportBatch, Payee, Profile, Reconciliation, Reminder, Tag, Transaction


@admin.register(Profile)
class ProfileAdmin(AuditHistoryAdminMixin, admin.ModelAdmin):
    list_display = ("user", "main_currency", "date_format", "number_format", "time_zone", "first_day_of_week")


@admin.register(Account)
class AccountAdmin(AuditHistoryAdminMixin, admin.ModelAdmin):
    list_display = ("name", "account_type", "currency", "institution", "balance", "is_active")
    list_filter = ("account_type", "currency", "is_active")
    search_fields = ("name", "institution")


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


@admin.register(Tag)
class TagAdmin(AuditHistoryAdminMixin, admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(Transaction)
class TransactionAdmin(AuditHistoryAdminMixin, admin.ModelAdmin):
    list_display = ("date", "transaction_number", "account", "display_payee", "category", "amount", "cleared", "reconciled")
    list_filter = ("account", "category", "cleared", "reconciled")
    search_fields = ("payee_name", "memo")
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
    list_display = ("account", "statement_date", "statement_balance", "opening_balance", "adjustment_transaction", "created_at")
    list_filter = ("account",)
    date_hierarchy = "statement_date"
