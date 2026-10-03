from django.urls import path

from .views import (
    accounts,
    api,
    categories,
    dashboard,
    exports,
    imports,
    payees,
    reminders,
    reports,
    transactions,
    welcome,
)
from .views import settings as settings_views

app_name = "finance"

urlpatterns = [
    path("", dashboard.dashboard, name="dashboard"),
    # Welcome wizard - runs once, right after a new stash's first login
    path("welcome/<int:step>/", welcome.welcome_step, name="welcome_step"),
    # Accounts
    path("accounts/", accounts.AccountListView.as_view(), name="account_list"),
    path("accounts/add/", accounts.AccountCreateView.as_view(), name="account_add"),
    path("accounts/<int:pk>/", accounts.account_detail, name="account_detail"),
    path("accounts/<int:pk>/edit/", accounts.AccountUpdateView.as_view(), name="account_edit"),
    path("accounts/<int:pk>/delete/", accounts.AccountDeleteView.as_view(), name="account_delete"),
    path("accounts/<int:pk>/transfer/", accounts.account_transfer, name="account_transfer"),
    path("accounts/<int:pk>/reconcile/", accounts.account_reconcile, name="account_reconcile"),
    # Categories
    path("categories/", categories.CategoryListView.as_view(), name="category_list"),
    path("categories/add/", categories.CategoryCreateView.as_view(), name="category_add"),
    path("categories/<int:pk>/edit/", categories.CategoryUpdateView.as_view(), name="category_edit"),
    path("categories/<int:pk>/delete/", categories.CategoryDeleteView.as_view(), name="category_delete"),
    path("categories/<int:pk>/usage/", categories.category_usage, name="category_usage"),
    # Payees
    path("payees/", payees.payee_list, name="payee_list"),
    path("payees/add/", payees.PayeeCreateView.as_view(), name="payee_add"),
    path("payees/<int:pk>/", payees.payee_detail, name="payee_detail"),
    path("payees/<int:pk>/edit/", payees.PayeeUpdateView.as_view(), name="payee_edit"),
    path("payees/<int:pk>/delete/", payees.PayeeDeleteView.as_view(), name="payee_delete"),
    # Transactions
    path("transactions/", transactions.transaction_list, name="transaction_list"),
    path("transactions/add/", transactions.transaction_form_view, name="transaction_add"),
    path("transactions/<int:pk>/edit/", transactions.transaction_form_view, name="transaction_edit"),
    path("transactions/<int:pk>/quick-edit/", transactions.transaction_quick_edit, name="transaction_quick_edit"),
    path("transactions/<int:pk>/delete/", transactions.TransactionDeleteView.as_view(), name="transaction_delete"),
    # Reminders
    path("reminders/", reminders.reminder_list, name="reminder_list"),
    path("reminders/add/", reminders.ReminderCreateView.as_view(), name="reminder_add"),
    path("reminders/<int:pk>/edit/", reminders.reminder_edit, name="reminder_edit"),
    path("reminders/<int:pk>/delete/", reminders.ReminderDeleteView.as_view(), name="reminder_delete"),
    path("reminders/<int:pk>/skip/", reminders.reminder_skip, name="reminder_skip"),
    # Import
    path("import/", imports.import_transactions, name="import_transactions"),
    # Export
    path("transactions/export/", exports.export_transactions, name="export_transactions"),
    path("accounts/export/", exports.export_accounts, name="export_accounts"),
    path("categories/export/", exports.export_categories, name="export_categories"),
    path("payees/export/", exports.export_payees, name="export_payees"),
    # Reports. Every report's export is one view; the slugs are the keys of exports.REPORT_EXPORTS.
    path("reports/", reports.reports_view, name="reports"),
    path("reports/export/<slug:report>/", exports.export_report, name="export_report"),
    # Settings: the page itself saves the preferences; each other form on it has its own URL
    path("settings/", settings_views.settings_view, name="settings"),
    path("settings/rates/check/", settings_views.check_rates, name="check_rates"),
    path("settings/backups/create/", settings_views.create_backup, name="create_backup"),
    path("settings/watched-expenses/", settings_views.save_watched_expenses, name="save_watched_expenses"),
    path("settings/backups/restore/", settings_views.backup_restore, name="backup_restore"),
    path("settings/change-pin/", settings_views.change_pin_view, name="change_pin"),
    path("settings/recovery-code/", settings_views.recovery_code_view, name="recovery_code"),
    # JSON APIs (used by the transaction form's autofill JS)
    path("api/payee-autofill/", api.api_payee_autofill, name="api_payee_autofill"),
    path("api/payees/", api.api_payee_list, name="api_payee_list"),
]
