from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from audit.context import system_action
from audit.models import AuditLogEntry
from finance.models import Account, Category, ImportBatch, Payee, Transaction

Action = AuditLogEntry.Action


class AuditTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)

    def entries(self, model=None, **filters):
        queryset = AuditLogEntry.objects.order_by("id").filter(**filters)
        return queryset.filter(table=model._meta.db_table) if model else queryset


class ModelChangeLoggingTests(AuditTestCase):
    def test_create_through_the_app_is_logged_as_the_user(self):
        response = self.client.post(reverse("finance:account_add"), {
            "name": "Cash", "account_type": "cash", "currency": "EUR", "is_active": "on",
            "opening_balance": "10.5", "opening_balance_date": "01/01/2026",
        })
        self.assertEqual(response.status_code, 302)
        entry = self.entries(Account).get()
        self.assertEqual(entry.action, Action.INSERT)
        self.assertEqual(entry.source, "user")
        self.assertEqual(entry.user, self.user)
        self.assertEqual(entry.request, "POST /accounts/add/")
        self.assertEqual(entry.row_label, "Cash")
        self.assertEqual(entry.values["name"], "Cash")
        self.assertEqual(entry.values["account_type"], "cash")
        # The opening balance becomes its own transaction, logged as automatic (D1).
        opening = self.entries(Transaction).get()
        self.assertEqual((opening.source, opening.values["amount"]), ("system", "10.5"))
        self.assertEqual(opening.operation, entry.operation)

    def test_update_records_one_row_per_changed_column_and_skips_no_op_saves(self):
        account = Account.objects.create(name="Cash")
        account.save()
        self.assertFalse(self.entries(Account, action=Action.UPDATE).exists())
        account.name = "Wallet"
        account.save()
        entry = self.entries(Account, action=Action.UPDATE).get()
        self.assertEqual(entry.column, "name")
        self.assertEqual(entry.old_value, "Cash")
        self.assertEqual(entry.new_value, "Wallet")
        self.assertEqual(entry.row_label, "Wallet")

    def test_update_with_several_changed_columns_gets_one_row_each(self):
        account = Account.objects.create(name="Checking", institution="Old Bank")
        account.name, account.institution = "Savings", "New Bank"
        account.save()
        rows = self.entries(Account, action=Action.UPDATE)
        self.assertEqual({row.column for row in rows}, {"name", "institution"})
        by_column = {row.column: (row.old_value, row.new_value) for row in rows}
        self.assertEqual(by_column["name"], ("Checking", "Savings"))
        self.assertEqual(by_column["institution"], ("Old Bank", "New Bank"))
        # Both columns changed in the same save() - same operation, same timestamp.
        self.assertEqual(len({row.operation for row in rows}), 1)
        self.assertEqual(len({row.timestamp for row in rows}), 1)

    def test_dates_decimals_and_relations_are_raw(self):
        account = Account.objects.create(name="Checking")
        category = Category.objects.create(name="Groceries")
        Transaction.objects.create(account=account, category=category, date=date(2026, 4, 3), amount=Decimal("-5"))
        values = self.entries(Transaction).get().values
        self.assertEqual(values["date"], "2026-04-03")
        self.assertEqual(values["amount"], "-5")
        self.assertEqual(values["category_id"], f"{category.pk} (Groceries)")

    def test_stored_choice_keys_are_kept_raw_not_their_label(self):
        Account.objects.create(name="Wallet", account_type="debit_card")
        self.assertEqual(self.entries(Account).get().values["account_type"], "debit_card")

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
        self.assertEqual(payee_entry.action, Action.INSERT)
        self.assertEqual(payee_entry.source, "system")
        self.assertEqual(payee_entry.operation, transaction_entry.operation)

    def test_watched_expenses_categories_changes_log_one_row_per_item(self):
        from finance.models import Profile

        profile = Profile.for_user(self.user)
        groceries = Category.objects.create(name="Groceries")
        takeout = Category.objects.create(name="Take-Outs")
        AuditLogEntry.objects.all().delete()

        profile.watched_expenses_categories.add(groceries, takeout)
        added = self.entries(Profile, action=Action.UPDATE, column="watched_expenses_categories")
        self.assertEqual(added.count(), 2)
        self.assertEqual(
            set(added.values_list("old_value", "new_value")),
            {(None, f"{groceries.pk} (Groceries)"), (None, f"{takeout.pk} (Take-Outs)")},
        )

        profile.watched_expenses_categories.remove(groceries)
        removed = self.entries(Profile, action=Action.UPDATE, column="watched_expenses_categories", new_value=None).get()
        self.assertEqual(removed.old_value, f"{groceries.pk} (Groceries)")

    def test_recovery_code_hash_is_never_logged(self):
        from finance.models import Profile

        profile = Profile.for_user(self.user)
        profile.recovery_code_hash = "pbkdf2_sha256$some$real$hash"
        profile.save()
        entry = self.entries(Profile, action=Action.UPDATE, column="recovery_code_hash").get()
        self.assertEqual(entry.old_value, None)
        self.assertEqual(entry.new_value, "(hidden)")

    def test_failed_pin_attempts_still_logged(self):
        from finance.models import Profile

        profile = Profile.for_user(self.user)
        profile.failed_pin_attempts = 1
        profile.save()
        entry = self.entries(Profile, action=Action.UPDATE, column="failed_pin_attempts").get()
        self.assertEqual((entry.old_value, entry.new_value), ("0", "1"))


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
        self.assertEqual(deletes.filter(table="finance_transaction").count(), 3)
        self.assertEqual(deletes.filter(table="finance_importbatch").count(), 1)
        # The account's own transfer category (D7) is cascade-deleted along with it.
        self.assertEqual(deletes.filter(table="finance_category").count(), 1)
        self.assertEqual(deletes.filter(table="finance_account").get().values["name"], "Checking")
        self.assertEqual(len(set(self.entries().values_list("operation", flat=True))), 1)
        # Transfer categories are never shared across accounts, so nothing elsewhere needs its link cleared.
        self.assertFalse(self.entries(action=Action.UPDATE).exists())

    def test_deleted_record_keeps_its_full_values(self):
        account = Account.objects.create(name="Checking", institution="Old Bank")
        AuditLogEntry.objects.all().delete()
        account.delete()
        entry = self.entries(action=Action.DELETE, table="finance_account").get()
        self.assertEqual(entry.column, "")
        self.assertEqual(entry.values["institution"], "Old Bank")


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
        response = self.client.post(
            reverse("finance:import_transactions"), {"account": account.pk, "file": upload}, follow=True
        )
        self.assertContains(response, "couldn&#x27;t read this OFX/QFX file")
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
        expected = f"{reverse('admin:audit_auditlogentry_changelist')}?table=finance_account&row_id={account.pk}"
        self.assertRedirects(response, expected, fetch_redirect_response=False)

    def test_users_and_groups_are_hidden_and_profile_is_view_only(self):
        response = self.client.get(reverse("admin:index"))
        self.assertNotContains(response, "Users")
        self.assertNotContains(response, "Groups")
        from finance.models import Profile

        profile = Profile.for_user(self.user)
        self.assertEqual(self.client.get(reverse("admin:finance_profile_add")).status_code, 403)
        detail = self.client.get(reverse("admin:finance_profile_change", args=[profile.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "recovery_code_hash")
        self.assertNotContains(detail, "failed_pin_attempts")
