from django.contrib import admin

from .models import Account, Category, ImportBatch, Payee, Reminder, Tag, Transaction


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("name", "account_type", "institution", "balance", "is_active")
    list_filter = ("account_type", "is_active")
    search_fields = ("name", "institution")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("full_name", "category_type", "parent", "is_active")
    list_filter = ("category_type", "is_active")
    search_fields = ("name",)


@admin.register(Payee)
class PayeeAdmin(admin.ModelAdmin):
    list_display = ("name", "default_category", "default_amount", "is_active")
    search_fields = ("name",)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "account", "display_payee", "category", "amount", "cleared", "reconciled")
    list_filter = ("account", "category", "cleared", "reconciled")
    search_fields = ("payee_name", "memo")
    date_hierarchy = "date"
    autocomplete_fields = ("payee", "category", "account", "transfer_account")


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("source_filename", "account", "imported_at", "row_count", "duplicate_count")


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ("name", "reminder_type", "amount", "frequency", "next_due_date", "is_active")
    list_filter = ("reminder_type", "frequency", "is_active")
