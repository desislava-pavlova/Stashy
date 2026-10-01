"""Tests for Phase 10 (Backup & Export): the backup helper (D8's quarterly check, and the
Settings page's "Back up now" button), and CSV/XLSX export for transactions/accounts/
categories/payees."""
import csv
import io
import sqlite3
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import backup as finance_backup
from .models import Account, Category, Payee, Transaction


def write_db(path, content):
    """A real (tiny) SQLite database holding one text value - backups go through SQLite's own
    backup API, so the files have to be real databases."""
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS note (content TEXT)")
        connection.execute("DELETE FROM note")
        connection.execute("INSERT INTO note VALUES (?)", [content])
    connection.close()


def read_db(path):
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT content FROM note").fetchone()[0]
    finally:
        connection.close()


class BackupHelperTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "db.sqlite3"
        write_db(self.db, "current content")

    def test_backup_now_copies_the_file_into_backups(self):
        result = finance_backup.backup_now(self.db, self.root, tag="manual")
        self.assertTrue(result.exists())
        self.assertTrue(result.name.startswith("backup_manual_"))
        self.assertEqual(read_db(result), "current content")

    def test_backup_now_returns_none_when_theres_no_database_yet(self):
        missing = self.root / "no-such-file.sqlite3"
        self.assertIsNone(finance_backup.backup_now(missing, self.root))

    def test_quarterly_backup_runs_when_none_exists_yet(self):
        result = finance_backup.quarterly_backup_if_due(self.db, self.root)
        self.assertIsNotNone(result)
        self.assertTrue(result.name.startswith("backup_quarterly_"))

    def test_quarterly_backup_skips_if_a_recent_one_already_exists(self):
        finance_backup.quarterly_backup_if_due(self.db, self.root)
        second = finance_backup.quarterly_backup_if_due(self.db, self.root)
        self.assertIsNone(second)
        self.assertEqual(len(list((self.root / "backups").glob("backup_quarterly_*.sqlite3"))), 1)

    def test_quarterly_backup_runs_again_once_the_last_one_is_old_enough(self):
        backups_dir = self.root / "backups"
        backups_dir.mkdir()
        old_stamp = (date.today() - timedelta(days=100)).strftime("%Y-%m-%d_00-00-00")
        (backups_dir / f"backup_quarterly_{old_stamp}.sqlite3").write_text("old backup")
        result = finance_backup.quarterly_backup_if_due(self.db, self.root)
        self.assertIsNotNone(result)

    def test_quarterly_backup_still_recognizes_the_old_pre_rename_filename_scheme(self):
        # Old backups are never renamed when the naming scheme changes - the due-check still
        # has to see them, or it would back up again right away even though one just happened.
        backups_dir = self.root / "backups"
        backups_dir.mkdir()
        recent_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        (backups_dir / f"db_quarterly_{recent_stamp}.sqlite3").write_text("old-scheme backup")
        self.assertIsNone(finance_backup.quarterly_backup_if_due(self.db, self.root))

    def test_list_backups_sorts_newest_first_across_both_naming_schemes(self):
        backups_dir = self.root / "backups"
        backups_dir.mkdir()
        (backups_dir / "db_manual_20260101_000000.sqlite3").write_text("old")
        (backups_dir / "backup_manual_2026-06-01_00-00-00.sqlite3").write_text("mid")
        (backups_dir / "backup_manual_2026-09-14_11-57-36.sqlite3").write_text("newest")
        names = [p.name for p in finance_backup.list_backups(self.root)]
        self.assertEqual(names, [
            "backup_manual_2026-09-14_11-57-36.sqlite3",
            "backup_manual_2026-06-01_00-00-00.sqlite3",
            "db_manual_20260101_000000.sqlite3",
        ])

    def test_restore_from_copies_the_backup_over_the_database_and_keeps_a_safety_copy(self):
        finance_backup.backup_now(self.db, self.root, tag="manual")
        target = next((self.root / "backups").glob("backup_manual_*.sqlite3"))
        write_db(target, "chosen backup content")
        write_db(self.db, "current (about to be overwritten) content")

        finance_backup.restore_from(target, self.db, self.root)

        self.assertEqual(read_db(self.db), "chosen backup content")
        safety_copies = list((self.root / "backups").glob("backup_before_restore_*.sqlite3"))
        self.assertEqual(len(safety_copies), 1)
        self.assertEqual(read_db(safety_copies[0]), "current (about to be overwritten) content")

    def test_a_connection_left_open_sees_the_restored_data(self):
        # The running server keeps connections open across requests. SQLite's backup API
        # rewrites the file in a way those connections notice, unlike a plain file copy.
        target = finance_backup.backup_now(self.db, self.root, tag="manual")
        write_db(self.db, "changed after the backup")
        open_connection = sqlite3.connect(self.db)
        self.addCleanup(open_connection.close)
        self.assertEqual(open_connection.execute("SELECT content FROM note").fetchone()[0], "changed after the backup")

        finance_backup.restore_from(target, self.db, self.root)

        self.assertEqual(open_connection.execute("SELECT content FROM note").fetchone()[0], "current content")

    def test_tags_with_underscores_sort_by_the_time_in_their_name(self):
        backups_dir = self.root / "backups"
        backups_dir.mkdir()
        newest = backups_dir / "backup_before_restore_2026-09-20_10-00-00.sqlite3"
        older = backups_dir / "db_before_update_20260915_090000.sqlite3"
        oldest = backups_dir / "backup_manual_2026-09-01_08-00-00.sqlite3"
        for path in (oldest, newest, older):  # file times say the opposite of the names
            write_db(path, path.name)
        self.assertEqual(finance_backup.list_backups(self.root), [newest, older, oldest])
        self.assertEqual(finance_backup._parse_stamp(older), datetime(2026, 9, 15, 9, 0, 0))


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


class ManualBackupButtonTests(LoggedInTestCase):
    # The view's actual database path is ":memory:" under the test runner, so these patch
    # finance.backup.backup_now itself rather than relying on a real file existing on disk -
    # the file-copying logic itself is already covered directly by BackupHelperTests above.
    def test_back_up_now_creates_a_file_and_shows_a_message(self):
        fake_backup = Path(settings.BASE_DIR) / "backups" / "db_manual_20260101_000000.sqlite3"
        with mock.patch("finance.backup.backup_now", return_value=fake_backup):
            response = self.client.post(reverse("finance:settings"), {"backup_now": "1"}, follow=True)
        self.assertContains(response, "Backed up to")

    def test_no_database_yet_shows_an_error_instead(self):
        with mock.patch("finance.backup.backup_now", return_value=None):
            response = self.client.post(reverse("finance:settings"), {"backup_now": "1"}, follow=True)
        self.assertContains(response, "Nothing to back up yet")


class ExportTransactionsTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        payee = Payee.objects.create(name="Landlord")
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), payee=payee, category=rent,
            memo="January rent", amount=Decimal("-500.00"), reconciled=True,
        )
        Account.objects.create(name="Savings")

    def test_csv_export_contains_the_row(self):
        response = self.client.get(reverse("finance:export_transactions"), {"format": "csv"})
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0], ["Date", "Number", "Account", "Payee", "Category", "Memo", "Amount", "Currency", "Reconciled"])
        self.assertIn(["2026-01-01", rows[1][1], "Checking", "Landlord", "Rent", "January rent", "-500.00", "EUR", "Y"], rows)

    def test_xlsx_export_has_the_right_content_type(self):
        response = self.client.get(reverse("finance:export_transactions"), {"format": "xlsx"})
        self.assertEqual(
            response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        self.assertTrue(len(response.content) > 0)

    def test_export_respects_the_current_filters(self):
        savings = Account.objects.get(name="Savings")
        Transaction.objects.create(account=savings, date=date(2026, 1, 2), amount=Decimal("10.00"))
        response = self.client.get(reverse("finance:export_transactions"), {"format": "csv", "account": self.account.pk})
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        accounts_in_export = {row[2] for row in rows[1:]}
        self.assertEqual(accounts_in_export, {"Checking"})


class ExportOtherListsTests(LoggedInTestCase):
    def test_accounts_export_lists_every_account(self):
        response = self.client.get(reverse("finance:export_accounts"), {"format": "csv"})
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        names = [row[0] for row in rows[1:]]
        self.assertIn("Checking", names)

    def test_categories_export_is_a_full_export_including_transfer_categories(self):
        # 2026-09-14 follow-up: "full export of the data" - system-managed rows are included
        # now, just marked, rather than left out silently.
        Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        response = self.client.get(reverse("finance:export_categories"), {"format": "csv"})
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        by_name = {row[0]: row for row in rows[1:]}
        self.assertIn("Groceries", by_name)
        self.assertEqual(by_name["Groceries"][4], "N")
        transfer_name = self.account.ensure_transfer_category().name
        self.assertIn(transfer_name, by_name)
        self.assertEqual(by_name[transfer_name][4], "Y")

    def test_payees_export_is_a_full_export_including_system_payees(self):
        Payee.objects.create(name="Lidl")
        Payee.system(Payee.OPENING_BALANCE)
        response = self.client.get(reverse("finance:export_payees"), {"format": "csv"})
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        by_name = {row[0]: row for row in rows[1:]}
        self.assertIn("Lidl", by_name)
        self.assertEqual(by_name["Lidl"][2], "N")
        self.assertIn(Payee.OPENING_BALANCE, by_name)
        self.assertEqual(by_name[Payee.OPENING_BALANCE][2], "Y")


class RestoreBackupTests(LoggedInTestCase):
    def existing_backup(self):
        backups_dir = Path(settings.BASE_DIR) / "backups"
        backups_dir.mkdir(exist_ok=True)
        path = backups_dir / "backup_manual_2026-09-14_11-57-36.sqlite3"
        path.write_text("a backup file")
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_confirm_page_shows_the_chosen_backups_name(self):
        backup = self.existing_backup()
        response = self.client.get(reverse("finance:backup_restore"), {"name": backup.name})
        self.assertContains(response, backup.name)

    def test_unknown_filename_is_not_found(self):
        response = self.client.get(reverse("finance:backup_restore"), {"name": "does-not-exist.sqlite3"})
        self.assertEqual(response.status_code, 404)

    def test_path_traversal_in_the_filename_is_rejected(self):
        response = self.client.get(reverse("finance:backup_restore"), {"name": "../../db.sqlite3"})
        self.assertEqual(response.status_code, 404)

    def test_posting_confirms_and_performs_the_restore(self):
        backup = self.existing_backup()
        with mock.patch("finance.backup.restore_from") as restore_from:
            response = self.client.post(reverse("finance:backup_restore"), {"name": backup.name}, follow=True)
        restore_from.assert_called_once()
        self.assertEqual(restore_from.call_args.args[0], backup)
        self.assertContains(response, "Restored from")
