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

    # Reports
    path("reports/", views.reports_view, name="reports"),

    # Settings (currency + regional preferences)
    path("settings/", views.settings_view, name="settings"),

    # JSON APIs (used by the transaction form's autofill JS)
    path("api/payee-autofill/", views.api_payee_autofill, name="api_payee_autofill"),
    path("api/payees/", views.api_payee_list, name="api_payee_list"),
]
