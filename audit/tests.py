from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from audit.context import system_action
from audit.models import AuditLogEntry
from finance.models import Account, Category, ImportBatch, Payee, Tag, Transaction

Action = AuditLogEntry.Action


class AuditTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)

    def entries(self, model=None, **filters):
        queryset = AuditLogEntry.objects.order_by("id").filter(**filters)
        return queryset.filter(model=model._meta.label_lower) if model else queryset


class ModelChangeLoggingTests(AuditTestCase):
    def test_create_through_the_app_is_logged_as_the_user(self):
        response = self.client.post(reverse("finance:account_add"), {
            "name": "Cash", "account_type": "cash", "currency": "EUR", "is_active": "on",
            "opening_balance": "10.5", "opening_balance_date": "01/01/2026",
        })
        self.assertEqual(response.status_code, 302)
        entry = self.entries(Account).get()
        self.assertEqual(entry.action, Action.CREATE)
        self.assertEqual(entry.source, "user")
        self.assertEqual(entry.user, self.user)
        self.assertEqual(entry.request, "POST /accounts/add/")
        self.assertEqual(entry.changes["name"], "Cash")
        self.assertEqual(entry.changes["account_type"], "Cash")
        # The opening balance becomes its own transaction, logged as automatic (D1).
        opening = self.entries(Transaction).get()
        self.assertEqual((opening.source, opening.changes["amount"]), ("system", "10.50"))
        self.assertEqual(opening.operation, entry.operation)

    def test_update_records_only_changed_fields_and_skips_no_op_saves(self):
        account = Account.objects.create(name="Cash")
        account.save()
        self.assertFalse(self.entries(Account, action=Action.UPDATE).exists())
        account.name = "Wallet"
        account.save()
        entry = self.entries(Account, action=Action.UPDATE).get()
        self.assertEqual(entry.changes, {"name": ["Cash", "Wallet"]})
        self.assertEqual(entry.object_repr, "Wallet")

    def test_dates_and_relations_are_readable(self):
        account = Account.objects.create(name="Checking")
        category = Category.objects.create(name="Groceries")
        Transaction.objects.create(account=account, category=category, date=date(2026, 4, 3), amount=Decimal("-5"))
        changes = self.entries(Transaction).get().changes
        self.assertEqual(changes["date"], "03/04/2026")
        self.assertEqual(changes["amount"], "-5.00")
        self.assertEqual(changes["category"], f"Groceries (#{category.pk})")

    def test_system_action_marks_changes_as_automatic_with_a_reason(self):
        with system_action("Nightly cleanup"):
            Account.objects.create(name="Cash")
        entry = self.entries(Account).get()
        self.assertEqual((entry.source, entry.reason), ("system", "Nightly cleanup"))

    def test_a_new_payee_is_created_automatically_and_grouped_with_the_users_save(self):
        # Phase 6: memory is computed live from history, not a cached field, so a transaction
        # save no longer triggers a follow-up Payee update - only its one-time auto-creation.
        account = Account.objects.create(name="Checking")
        response = self.client.post(reverse("finance:transaction_add"), {
            "account": account.pk, "date": "05/01/2026", "payee_name": "Lidl", "memo": "weekly", "amount": "-30.00",
        })
        self.assertEqual(response.status_code, 302)
        transaction_entry = self.entries(Transaction).get()
        self.assertEqual(transaction_entry.source, "user")
        payee_entry = self.entries(Payee).get()
        self.assertEqual(payee_entry.action, Action.CREATE)
        self.assertEqual(payee_entry.source, "system")
        self.assertEqual(payee_entry.operation, transaction_entry.operation)

    def test_tag_changes_are_logged(self):
        account = Account.objects.create(name="Checking")
        txn = Transaction.objects.create(account=account, date=date(2026, 1, 1), amount=Decimal("-1.00"))
        txn.tags.add(Tag.objects.create(name="holiday"))
        entry = self.entries(Transaction, action=Action.UPDATE).get()
        self.assertEqual(entry.changes, {"tags": {"added": ["holiday"]}})


class DeleteLoggingTests(AuditTestCase):
    def test_account_delete_logs_every_cascaded_row_in_one_action(self):
        account = Account.objects.create(name="Checking")
        batch = ImportBatch.objects.create(account=account, source_filename="bank.csv")
        for day in range(1, 4):
            Transaction.objects.create(account=account, date=date(2026, 1, day), amount=Decimal("-1.00"), import_batch=batch)
        AuditLogEntry.objects.all().delete()

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("finance:account_delete", args=[account.pk]))

        deletes = self.entries(action=Action.DELETE)
        self.assertEqual(deletes.filter(model="finance.transaction").count(), 3)
        self.assertEqual(deletes.filter(model="finance.importbatch").count(), 1)
        # The account's own transfer category (D7) is cascade-deleted along with it.
        self.assertEqual(deletes.filter(model="finance.category").count(), 1)
        self.assertEqual(deletes.filter(model="finance.account").get().changes["name"], "Checking")
        self.assertEqual(len(set(self.entries().values_list("operation", flat=True))), 1)
        # Transfer categories are never shared across accounts, so nothing elsewhere needs its link cleared.
        self.assertFalse(self.entries(action=Action.UPDATE).exists())


class ImportLoggingTests(AuditTestCase):
    def test_imported_rows_are_logged_as_automatic(self):
        account = Account.objects.create(name="Checking")
        upload = SimpleUploadedFile("bank.csv", b"Date,Payee,Memo,Amount\n03/04/2026,Shop,,-5.00\n")
        self.client.post(reverse("finance:import_transactions"), {"account": account.pk, "file": upload})
        entry = self.entries(Transaction).get()
        self.assertEqual((entry.source, entry.reason), ("system", "Imported from bank.csv"))

    def test_failed_import_leaves_nothing_behind(self):
        account = Account.objects.create(name="Checking")
        upload = SimpleUploadedFile("broken.ofx", b"this is not an OFX file")
        with self.assertRaises(Exception):
            self.client.post(reverse("finance:import_transactions"), {"account": account.pk, "file": upload})
        self.assertFalse(ImportBatch.objects.exists())
        self.assertFalse(self.entries(ImportBatch).exists())


class AuditAdminTests(AuditTestCase):
    def test_log_is_viewable_but_read_only(self):
        Account.objects.create(name="Cash")
        entry = self.entries(Account).get()
        self.assertContains(self.client.get(reverse("admin:audit_auditlogentry_changelist")), "Cash")
        self.assertContains(self.client.get(reverse("admin:audit_auditlogentry_change", args=[entry.pk])), "Cash")
        self.assertEqual(self.client.get(reverse("admin:audit_auditlogentry_add")).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:audit_auditlogentry_delete", args=[entry.pk])).status_code, 403)

    def test_history_button_opens_that_objects_audit_trail(self):
        account = Account.objects.create(name="Cash")
        response = self.client.get(reverse("admin:finance_account_history", args=[account.pk]))
        expected = f"{reverse('admin:audit_auditlogentry_changelist')}?model=finance.account&object_id={account.pk}"
        self.assertRedirects(response, expected, fetch_redirect_response=False)
