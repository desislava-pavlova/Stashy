from django.urls import path

from . import views

app_name = "finance"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    # Accounts
    path("accounts/", views.AccountListView.as_view(), name="account_list"),
    path("accounts/add/", views.AccountCreateView.as_view(), name="account_add"),
    path("accounts/<int:pk>/", views.account_detail, name="account_detail"),
    path("accounts/<int:pk>/edit/", views.AccountUpdateView.as_view(), name="account_edit"),
    path("accounts/<int:pk>/delete/", views.AccountDeleteView.as_view(), name="account_delete"),
    path("accounts/<int:pk>/transfer/", views.account_transfer, name="account_transfer"),
    path("accounts/<int:pk>/reconcile/", views.account_reconcile, name="account_reconcile"),

    # Categories
    path("categories/", views.CategoryListView.as_view(), name="category_list"),
    path("categories/add/", views.CategoryCreateView.as_view(), name="category_add"),
    path("categories/add-starter/", views.add_starter_categories_view, name="add_starter_categories"),
    path("categories/<int:pk>/edit/", views.CategoryUpdateView.as_view(), name="category_edit"),
    path("categories/<int:pk>/delete/", views.CategoryDeleteView.as_view(), name="category_delete"),
    path("categories/<int:pk>/usage/", views.category_usage, name="category_usage"),

    # Payees
    path("payees/", views.payee_list, name="payee_list"),
    path("payees/add/", views.PayeeCreateView.as_view(), name="payee_add"),
    path("payees/<int:pk>/", views.payee_detail, name="payee_detail"),
    path("payees/<int:pk>/edit/", views.PayeeUpdateView.as_view(), name="payee_edit"),
    path("payees/<int:pk>/delete/", views.PayeeDeleteView.as_view(), name="payee_delete"),

    # Transactions
    path("transactions/", views.transaction_list, name="transaction_list"),
    path("transactions/add/", views.transaction_form_view, name="transaction_add"),
    path("transactions/<int:pk>/edit/", views.transaction_form_view, name="transaction_edit"),
    path("transactions/<int:pk>/delete/", views.TransactionDeleteView.as_view(), name="transaction_delete"),

    # Reminders
    path("reminders/", views.reminder_list, name="reminder_list"),
    path("reminders/add/", views.ReminderCreateView.as_view(), name="reminder_add"),
    path("reminders/<int:pk>/edit/", views.reminder_edit, name="reminder_edit"),
    path("reminders/<int:pk>/delete/", views.ReminderDeleteView.as_view(), name="reminder_delete"),
    path("reminders/<int:pk>/skip/", views.reminder_skip, name="reminder_skip"),

    # Import
    path("import/", views.import_transactions, name="import_transactions"),

    # Export (Phase 10)
    path("transactions/export/", views.export_transactions, name="export_transactions"),
    path("accounts/export/", views.export_accounts, name="export_accounts"),
    path("categories/export/", views.export_categories, name="export_categories"),
    path("payees/export/", views.export_payees, name="export_payees"),

    # Reports
    path("reports/", views.reports_view, name="reports"),
    path("reports/export/spending/", views.export_report_spending, name="export_report_spending"),
    path("reports/export/spending-vs-average/", views.export_report_spending_vs_average, name="export_report_spending_vs_average"),
    path("reports/export/income-expense/", views.export_report_income_expense, name="export_report_income_expense"),
    path("reports/export/income-expense-vs-average/", views.export_report_income_expense_vs_average, name="export_report_income_expense_vs_average"),
    path("reports/export/bills/", views.export_report_bills, name="export_report_bills"),
    path("reports/export/net-worth/", views.export_report_net_worth, name="export_report_net_worth"),

    # Settings (currency + regional preferences)
    path("settings/", views.settings_view, name="settings"),
    path("settings/backups/restore/", views.backup_restore, name="backup_restore"),

    # JSON APIs (used by the transaction form's autofill JS)
    path("api/payee-autofill/", views.api_payee_autofill, name="api_payee_autofill"),
    path("api/payees/", views.api_payee_list, name="api_payee_list"),
]
