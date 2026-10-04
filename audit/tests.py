"""The audit log: what each save and delete records (who, what and why), imports and cascaded
deletes, the read-only admin and its timestamps, audit_suspended(), and the after-commit step
on the stash's own database."""

import threading
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import signals as model_signals
from django.test import SimpleTestCase
from django.urls import reverse

from audit import signals as audit_signals
from audit.admin import format_timestamp
from audit.context import audit_suspended, system_action
from audit.models import AuditLogEntry
from finance import regional
from finance.models import Account, Category, ImportBatch, Payee, Profile, Transaction
from finance.tests.base import LoggedInWithAccountTestCase, StashTestCase

Action = AuditLogEntry.Action


class AuditTestCase(StashTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)

    def entries(self, model=None, **filters):
        queryset = AuditLogEntry.objects.order_by("id").filter(**filters)
        return queryset.filter(table=model._meta.db_table) if model else queryset


class ModelChangeLoggingTests(AuditTestCase):
    def test_create_through_the_app_is_logged_as_the_user(self):
        response = self.client.post(
            reverse("finance:account_add"),
            {
                "name": "Cash",
                "account_type": "cash",
                "currency": "EUR",
                "is_active": "on",
                "opening_balance": "10.5",
                "opening_balance_date": "01/01/2026",
            },
        )
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
        response = self.client.post(
            reverse("finance:transaction_add"),
            {
                "account": account.pk,
                "date": "05/01/2026",
                "payee_name": "Lidl",
                "memo": "weekly",
                "amount": "-30.00",
            },
        )
        self.assertEqual(response.status_code, 302)
        transaction_entry = self.entries(Transaction).get()
        self.assertEqual(transaction_entry.source, "user")
        payee_entry = self.entries(Payee).get()
        self.assertEqual(payee_entry.action, Action.INSERT)
        self.assertEqual(payee_entry.source, "system")
        self.assertEqual(payee_entry.operation, transaction_entry.operation)

    def test_watched_expenses_categories_changes_log_one_row_per_item(self):

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
        removed = self.entries(
            Profile, action=Action.UPDATE, column="watched_expenses_categories", new_value=None
        ).get()
        self.assertEqual(removed.old_value, f"{groceries.pk} (Groceries)")

    def test_recovery_code_hash_is_never_logged(self):

        profile = Profile.for_user(self.user)
        profile.recovery_code_hash = "pbkdf2_sha256$some$real$hash"
        profile.save()
        entry = self.entries(Profile, action=Action.UPDATE, column="recovery_code_hash").get()
        self.assertEqual(entry.old_value, None)
        self.assertEqual(entry.new_value, "(hidden)")

    def test_failed_pin_attempts_still_logged(self):

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
            Transaction.objects.create(
                account=account, date=date(2026, 1, day), amount=Decimal("-1.00"), import_batch=batch
            )
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
        self.assertEqual(
            self.client.post(reverse("admin:audit_auditlogentry_delete", args=[entry.pk])).status_code, 403
        )

    def test_history_button_opens_that_objects_audit_trail(self):
        account = Account.objects.create(name="Cash")
        response = self.client.get(reverse("admin:finance_account_history", args=[account.pk]))
        expected = f"{reverse('admin:audit_auditlogentry_changelist')}?table=finance_account&row_id={account.pk}"
        self.assertRedirects(response, expected, fetch_redirect_response=False)

    def test_users_and_groups_are_hidden_and_profile_is_view_only(self):
        response = self.client.get(reverse("admin:index"))
        self.assertNotContains(response, "Users")
        self.assertNotContains(response, "Groups")

        profile = Profile.for_user(self.user)
        self.assertEqual(self.client.get(reverse("admin:finance_profile_add")).status_code, 403)
        detail = self.client.get(reverse("admin:finance_profile_change", args=[profile.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "recovery_code_hash")
        self.assertNotContains(detail, "failed_pin_attempts")


class OnCommitUsesTheStashTests(LoggedInWithAccountTestCase):
    """1.1 (F42): the audit log's after-commit step waits on the stash's own database."""

    def test_cleared_references_wait_for_the_routed_database(self):
        payee = Payee.objects.create(name="Corner shop")
        Transaction.objects.create(account=self.account, date=date(2026, 9, 1), payee=payee, amount=Decimal("-5"))
        with (
            mock.patch("audit.signals.router.db_for_write", return_value=settings.TEST_STASH) as db_for_write,
            mock.patch("audit.signals.transaction.on_commit") as on_commit,
        ):
            payee.delete()
        db_for_write.assert_called()
        self.assertEqual(on_commit.call_args.kwargs["using"], settings.TEST_STASH)


class AuditSkipsNameKeyTests(StashTestCase):
    """name_key only repeats the name, so the audit log leaves it out."""

    def test_neither_inserts_nor_renames_log_the_key(self):
        payee = Payee.objects.create(name="Lidl")
        payee.name = "Lidl Express"
        payee.save()
        rows = AuditLogEntry.objects.filter(table="finance_payee", row_id=str(payee.pk))
        self.assertNotIn("name_key", rows.get(action="insert").values)
        self.assertEqual(list(rows.filter(action="update").values_list("column", flat=True)), ["name"])


class AuditSuspendedTests(StashTestCase):
    """6.3 (F17): the audit app owns its own off switch, and it affects only this thread."""

    def test_nothing_is_logged_inside_the_block(self):
        before = AuditLogEntry.objects.count()
        with audit_suspended():
            account = Account.objects.create(name="Wallet")
            account.notes = "Pocket money"
            account.save()
            account.delete()
        self.assertEqual(AuditLogEntry.objects.count(), before)

    def test_logging_comes_back_after_the_block(self):
        with audit_suspended():
            pass
        before = AuditLogEntry.objects.count()
        Account.objects.create(name="Wallet")
        self.assertGreater(AuditLogEntry.objects.count(), before)

    def test_the_signal_handlers_stay_connected(self):
        with audit_suspended():
            receivers = str(model_signals.post_save.receivers)
        self.assertIn("audit.post_save", receivers)

    def test_other_threads_keep_logging(self):
        seen = {}
        with audit_suspended():
            self.assertFalse(audit_signals._logging(Account))
            thread = threading.Thread(target=lambda: seen.update(other=audit_signals._logging(Account)))
            thread.start()
            thread.join()
        self.assertTrue(seen["other"])


class AuditTimestampFormatTests(SimpleTestCase):
    def test_iso_style_with_milliseconds_and_offset(self):
        value = datetime(2026, 9, 15, 12, 6, 48, 930000, tzinfo=UTC)
        with regional.activate(regional.Preferences(time_zone="Europe/Sofia")):
            self.assertEqual(format_timestamp(value), "2026-09-15 15:06:48.930 +03:00")

    def test_never_follows_the_date_format_preference(self):
        value = datetime(2026, 9, 15, 12, 6, 48, tzinfo=UTC)
        preferences = regional.Preferences(date_format="mdy", time_zone="Europe/Sofia")
        with regional.activate(preferences):
            self.assertTrue(format_timestamp(value).startswith("2026-09-15"))

    def test_negative_offset(self):
        value = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        with regional.activate(regional.Preferences(time_zone="America/New_York")):
            formatted = format_timestamp(value)
        self.assertTrue(formatted.endswith("-05:00"), formatted)

    def test_blank_for_missing_value(self):
        self.assertEqual(format_timestamp(None), "")
