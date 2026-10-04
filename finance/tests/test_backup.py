"""Backups: the backup files, the quarterly backup, Settings' "Back up now", and restoring a
stash from a backup."""

import sqlite3
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.test import SimpleTestCase
from django.urls import reverse

from .. import backup as finance_backup
from ..stashes import registry
from .base import LoggedInWithAccountTestCase, TemporaryStashesMixin


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


def _make_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(path).close()
    return path


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
        self.assertTrue(result.name.endswith(".sqlite3"))
        self.assertIn("_backup_manual_", result.name)
        self.assertEqual(read_db(result), "current content")

    def test_backup_now_returns_none_when_theres_no_database_yet(self):
        missing = self.root / "no-such-file.sqlite3"
        self.assertIsNone(finance_backup.backup_now(missing, self.root))

    def test_quarterly_backup_runs_when_none_exists_yet(self):
        result = finance_backup.quarterly_backup_if_due(self.db, self.root)
        self.assertIsNotNone(result)
        self.assertIn("_backup_quarterly_", result.name)

    def test_quarterly_backup_skips_if_a_recent_one_already_exists(self):
        finance_backup.quarterly_backup_if_due(self.db, self.root)
        second = finance_backup.quarterly_backup_if_due(self.db, self.root)
        self.assertIsNone(second)
        self.assertEqual(len(list((self.root / "backups").glob("*_backup_quarterly_*.sqlite3"))), 1)

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
        self.assertEqual(
            names,
            [
                "backup_manual_2026-09-14_11-57-36.sqlite3",
                "backup_manual_2026-06-01_00-00-00.sqlite3",
                "db_manual_20260101_000000.sqlite3",
            ],
        )

    def test_restore_from_copies_the_backup_over_the_database_and_keeps_a_safety_copy(self):
        finance_backup.backup_now(self.db, self.root, tag="manual")
        target = next((self.root / "backups").glob("*_backup_manual_*.sqlite3"))
        write_db(target, "chosen backup content")
        write_db(self.db, "current (about to be overwritten) content")

        finance_backup.restore_from(target, self.db, self.root)

        self.assertEqual(read_db(self.db), "chosen backup content")
        safety_copies = list((self.root / "backups").glob("*_backup_before_restore_*.sqlite3"))
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


class ManualBackupButtonTests(LoggedInWithAccountTestCase):
    # The test stash's db.sqlite3 is an empty placeholder (its data lives in the test runner's
    # in-memory database), so these patch finance.backup.backup_now itself - the file-copying
    # logic is covered directly by BackupHelperTests above.
    def test_back_up_now_creates_a_file_and_shows_a_message(self):
        fake_backup = registry.stash_backups_dir(settings.TEST_STASH) / "db_manual_20260101_000000.sqlite3"
        with mock.patch("finance.backup.backup_now", return_value=fake_backup):
            response = self.client.post(reverse("finance:create_backup"), follow=True)
        self.assertContains(response, "Backed up to")

    def test_no_database_yet_shows_an_error_instead(self):
        with mock.patch("finance.backup.backup_now", return_value=None):
            response = self.client.post(reverse("finance:create_backup"), follow=True)
        self.assertContains(response, "Nothing to back up yet")


class RestoreBackupTests(LoggedInWithAccountTestCase):
    def existing_backup(self):
        backups_dir = registry.stash_backups_dir(settings.TEST_STASH)
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


class BackupNamesCarryTheStashTests(SimpleTestCase):
    """Plan 03 R5: two stashes' backups never share a file name."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.stashes = Path(tmp.name)

    def _stash(self, name):
        folder = self.stashes / name
        _make_db(folder / "db.sqlite3")
        return folder

    def test_the_name_starts_with_the_stash(self):
        alex = self._stash("alex")
        result = finance_backup.backup_now(alex / "db.sqlite3", alex, tag="manual")
        self.assertRegex(result.name, r"^alex_backup_manual_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.sqlite3$")

    def test_two_stashes_backed_up_together_get_different_names(self):
        alex, sam = self._stash("alex"), self._stash("sam-2")
        first = finance_backup.backup_now(alex / "db.sqlite3", alex, tag="before_update")
        second = finance_backup.backup_now(sam / "db.sqlite3", sam, tag="before_update")
        self.assertNotEqual(first.name, second.name)
        self.assertTrue(second.name.startswith("sam-2_backup_before_update_"))

    def test_the_list_mixes_all_three_naming_schemes_newest_first(self):
        alex = self._stash("alex")
        backups = alex / "backups"
        backups.mkdir()
        for name in (
            "db_before_update_20260101_000000.sqlite3",
            "backup_manual_2026-06-01_00-00-00.sqlite3",
            "alex_backup_before_restore_2026-09-14_11-57-36.sqlite3",
        ):
            (backups / name).write_text("x")
        names = [p.name for p in finance_backup.list_backups(alex)]
        self.assertEqual(
            names,
            [
                "alex_backup_before_restore_2026-09-14_11-57-36.sqlite3",
                "backup_manual_2026-06-01_00-00-00.sqlite3",
                "db_before_update_20260101_000000.sqlite3",
            ],
        )

    def test_the_quarterly_check_sees_a_recent_quarterly_backup_under_the_new_name(self):
        alex = self._stash("alex")
        (alex / "backups").mkdir()
        recent = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        (alex / "backups" / f"alex_backup_quarterly_{recent}.sqlite3").write_text("x")
        self.assertIsNone(finance_backup.quarterly_backup_if_due(alex / "db.sqlite3", alex))

    def test_an_old_quarterly_backup_under_the_new_name_makes_a_new_one_due(self):
        alex = self._stash("alex")
        (alex / "backups").mkdir()
        old = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d_%H-%M-%S")
        (alex / "backups" / f"alex_backup_quarterly_{old}.sqlite3").write_text("x")
        self.assertIsNotNone(finance_backup.quarterly_backup_if_due(alex / "db.sqlite3", alex))

    def test_a_manual_backup_is_never_mistaken_for_a_quarterly_one(self):
        # A stash may even be called "quarterly": only the tag after "backup_" counts.
        quarterly = self._stash("quarterly")
        finance_backup.backup_now(quarterly / "db.sqlite3", quarterly, tag="manual")
        self.assertIsNotNone(finance_backup.quarterly_backup_if_due(quarterly / "db.sqlite3", quarterly))

    def test_restoring_leaves_a_safety_backup_named_after_the_stash(self):
        alex = self._stash("alex")
        source = finance_backup.backup_now(alex / "db.sqlite3", alex, tag="manual")
        finance_backup.restore_from(source, alex / "db.sqlite3", alex)
        tags = sorted(p.name.split("_backup_")[1].rsplit("_", 2)[0] for p in finance_backup.list_backups(alex))
        self.assertEqual(tags, ["before_restore", "manual"])


class RestoreBringsBackTodaysSchemaTests(TemporaryStashesMixin, SimpleTestCase):
    """1.3 (F44): a backup taken before an update is migrated as soon as it's restored."""

    def test_restoring_a_pre_update_backup_migrates_it(self):
        self.make_stashes("restorecheck")
        call_command("migrate", "finance", "0016", database="restorecheck", verbosity=0)
        old_backup = finance_backup.backup_now(
            registry.stash_db_path("restorecheck"), registry.stash_path("restorecheck"), tag="before_update"
        )
        call_command("migrate", database="restorecheck", verbosity=0)

        registry.restore_stash("restorecheck", old_backup)

        with connections["restorecheck"].cursor() as cursor:
            columns = [
                c.name
                for c in connections["restorecheck"].introspection.get_table_description(cursor, "finance_profile")
            ]
        self.assertIn("owner_name", columns)  # added by migration 0017, after the backup
