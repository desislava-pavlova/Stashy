"""Backups: the backup files, the quarterly backup, Settings' "Back up now", and restoring a
stash from a backup."""

import sqlite3
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from .. import backup as finance_backup
from .. import db_router
from ..models import Payee, Profile
from ..stashes import StashError, registry, security
from .base import (
    LEAKED_TEMPLATE_SYNTAX,
    REHASHING_PASSWORD_HASHERS,
    LoggedInWithAccountTestCase,
    StashClient,
    TemporaryStashesMixin,
)


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

    def test_two_backups_in_the_same_second_keep_both_files(self):
        """Plan 03, S86: a double-clicked Restore takes a safety copy for each click. The name
        is stamped to the second, and the second copy used to overwrite the first, which held
        the data from before the restore."""
        the_moment = datetime(2026, 11, 14, 12, 0, 0)
        with mock.patch.object(finance_backup, "datetime") as clock:
            clock.now.return_value = the_moment
            first = finance_backup.backup_now(self.db, self.root, tag="before_restore")
            write_db(self.db, "changed content")
            second = finance_backup.backup_now(self.db, self.root, tag="before_restore")
        self.assertNotEqual(first, second)
        self.assertTrue(first.name.endswith("_2026-11-14_12-00-00.sqlite3"))
        self.assertTrue(second.name.endswith("_2026-11-14_12-00-01.sqlite3"))
        self.assertEqual(read_db(first), "current content")
        self.assertEqual(read_db(second), "changed content")

    def test_a_failed_copy_leaves_no_empty_file_that_passes_for_a_backup(self):
        with (
            mock.patch.object(finance_backup, "_sqlite_copy", side_effect=sqlite3.OperationalError("disk full")),
            self.assertRaises(sqlite3.OperationalError),
        ):
            finance_backup.backup_now(self.db, self.root, tag="manual")
        self.assertEqual(finance_backup.list_backups(self.root), [])
        self.assertEqual(list((self.root / "backups").iterdir()), [])

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
    PIN = "1234"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # The confirm page asks for the PIN (plan 03, Q3).
        cls.user.set_password(cls.PIN)
        cls.user.save()
        Profile.for_user(cls.user)  # the lockout is kept on it

    def existing_backup(self):
        backups_dir = registry.stash_backups_dir(settings.TEST_STASH)
        backups_dir.mkdir(exist_ok=True)
        path = backups_dir / "backup_manual_2026-09-14_11-57-36.sqlite3"
        path.write_text("a backup file")
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_confirm_page_shows_the_chosen_backups_name_and_asks_for_the_pin(self):
        backup = self.existing_backup()
        response = self.client.get(reverse("finance:backup_restore"), {"name": backup.name})
        self.assertContains(response, backup.name)
        self.assertContains(response, 'name="current_pin"')
        self.assertContains(response, "your PIN and recovery code stay as they are now")

    def test_unknown_filename_is_not_found(self):
        response = self.client.get(reverse("finance:backup_restore"), {"name": "does-not-exist.sqlite3"})
        self.assertEqual(response.status_code, 404)

    def test_path_traversal_in_the_filename_is_rejected(self):
        response = self.client.get(reverse("finance:backup_restore"), {"name": "../../db.sqlite3"})
        self.assertEqual(response.status_code, 404)

    def test_posting_the_pin_confirms_and_performs_the_restore(self):
        backup = self.existing_backup()
        data = {"name": backup.name, "current_pin": self.PIN}
        with mock.patch("finance.stashes.registry.restore_stash") as restore_stash:
            response = self.client.post(reverse("finance:backup_restore"), data, follow=True)
        restore_stash.assert_called_once()
        self.assertEqual(restore_stash.call_args.args[1], backup)
        self.assertContains(response, "Restored from")

    def test_a_backup_the_restore_refuses_says_why_and_the_page_carries_on(self):
        """Plan 03, S88: the check of the file raises StashError, and the user reads it on Settings
        instead of getting an error page."""
        backup = self.existing_backup()
        data = {"name": backup.name, "current_pin": self.PIN}
        with mock.patch("finance.stashes.registry.restore_stash", side_effect=StashError("It is damaged.")):
            response = self.client.post(reverse("finance:backup_restore"), data, follow=True)
        self.assertContains(response, "It is damaged.")
        self.assertNotContains(response, "Restored from")
        self.assertTemplateUsed(response, "finance/settings.html")

    def test_a_wrong_pin_restores_nothing_and_counts_toward_the_lockout(self):
        backup = self.existing_backup()
        data = {"name": backup.name, "current_pin": "0000"}
        with mock.patch("finance.stashes.registry.restore_stash") as restore_stash:
            response = self.client.post(reverse("finance:backup_restore"), data)
        restore_stash.assert_not_called()
        self.assertContains(response, "That&#x27;s not your current PIN.")
        self.assertEqual(Profile.for_user(self.user).failed_pin_attempts, 1)

    def test_the_last_wrong_pin_logs_out_to_the_pin_screen(self):
        backup = self.existing_backup()
        data = {"name": backup.name, "current_pin": "0000"}
        with mock.patch("finance.stashes.registry.restore_stash") as restore_stash:
            for _ in range(security.LOCKOUT_THRESHOLD - 1):
                self.client.post(reverse("finance:backup_restore"), data)
            response = self.client.post(reverse("finance:backup_restore"), data)
        restore_stash.assert_not_called()
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def restore_url(self, backup):
        """The page's form has no action and no name field, so a browser posts to the same URL
        and the backup's name only ever travels in the query string."""
        return f"{reverse('finance:backup_restore')}?{urlencode({'name': backup.name})}"

    def test_a_browser_style_post_finds_the_backup_in_the_query_string(self):
        backup = self.existing_backup()
        with mock.patch("finance.stashes.registry.restore_stash") as restore_stash:
            response = self.client.post(self.restore_url(backup), {"current_pin": self.PIN})
        self.assertRedirects(response, reverse("finance:settings"), fetch_redirect_response=False)
        self.assertEqual(restore_stash.call_args.args[1], backup)

    def test_the_pin_box_has_a_label_a_token_and_no_leaked_template_syntax(self):
        response = self.client.get(self.restore_url(self.existing_backup()))
        self.assertContains(response, '<label class="form-label" for="id_current_pin">Current PIN</label>', html=True)
        self.assertContains(response, 'id="id_current_pin"')
        self.assertContains(response, "csrfmiddlewaretoken")
        for leaked in LEAKED_TEMPLATE_SYNTAX:
            with self.subTest(leaked=leaked):
                self.assertNotContains(response, leaked)

    def test_a_wrong_pin_shows_the_page_again_without_echoing_the_pin(self):
        backup = self.existing_backup()
        response = self.client.post(self.restore_url(backup), {"current_pin": "0000"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, backup.name)
        self.assertContains(response, 'name="current_pin"')
        self.assertContains(response, "Yes, restore this backup")
        self.assertNotContains(response, 'value="0000"')

    def test_a_missing_or_malformed_pin_restores_nothing_and_says_why(self):
        backup = self.existing_backup()
        cases = {
            "": "This field is required.",
            "12": "PIN must be exactly 4 digits.",
            "abcd": "PIN must be exactly 4 digits.",
        }
        with mock.patch("finance.stashes.registry.restore_stash") as restore_stash:
            for pin, message in cases.items():
                with self.subTest(pin=pin):
                    response = self.client.post(self.restore_url(backup), {"current_pin": pin})
                    self.assertContains(response, message)
        restore_stash.assert_not_called()

    def test_a_locked_stash_logs_out_even_with_the_right_pin_and_restores_nothing(self):
        backup = self.existing_backup()
        locked_until = timezone.now() + timedelta(minutes=security.LOCKOUT_MINUTES)
        Profile.objects.filter(user=self.user).update(locked_until=locked_until)
        with mock.patch("finance.stashes.registry.restore_stash") as restore_stash:
            response = self.client.post(self.restore_url(backup), {"current_pin": self.PIN})
        restore_stash.assert_not_called()
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(Profile.for_user(self.user).locked_until, locked_until)

    def test_the_right_pin_forgets_earlier_wrong_tries(self):
        backup = self.existing_backup()
        Profile.objects.filter(user=self.user).update(failed_pin_attempts=security.LOCKOUT_THRESHOLD - 1)
        with mock.patch("finance.stashes.registry.restore_stash"):
            self.client.post(self.restore_url(backup), {"current_pin": self.PIN})
        self.assertEqual(Profile.for_user(self.user).failed_pin_attempts, 0)

    def test_no_filename_at_all_is_not_found(self):
        for method in (self.client.get, self.client.post):
            with self.subTest(method=method.__name__):
                self.assertEqual(method(reverse("finance:backup_restore"), {"current_pin": self.PIN}).status_code, 404)

    def test_an_unknown_filename_is_not_found_before_the_pin_is_looked_at(self):
        """So this page can't be used to try PINs: nothing is counted for a backup that isn't there."""
        response = self.client.post(self.restore_url(Path("no-such-backup.sqlite3")), {"current_pin": "0000"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Profile.for_user(self.user).failed_pin_attempts, 0)

    def test_a_request_with_an_old_copy_of_the_profile_still_counts_toward_the_lockout(self):
        """Plan 03, S75 on this page, whose request holds no transaction of its own while the
        PIN is checked: the saved row is one try short of the lockout, the request's copy is not."""
        backup = self.existing_backup()
        old_copy = Profile.objects.get(user=self.user)
        Profile.objects.filter(user=self.user).update(failed_pin_attempts=security.LOCKOUT_THRESHOLD - 1)
        with (
            mock.patch.object(Profile, "for_user", return_value=old_copy),
            mock.patch("finance.stashes.registry.restore_stash"),
        ):
            response = self.client.post(self.restore_url(backup), {"current_pin": "0000"})
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertTrue(security.is_locked_out(Profile.objects.get(user=self.user)))

    def test_after_a_django_update_checking_the_pin_does_not_log_you_out_of_the_restore(self):
        """Plan 03, S87: Django would re-save the PIN's hash in a newer format when it is
        checked, and the login is tied to that hash, so the page after the restore would be the
        PIN screen. The PIN checks no longer save it (security.check_pin)."""
        backup = self.existing_backup()
        with (
            override_settings(PASSWORD_HASHERS=REHASHING_PASSWORD_HASHERS),
            mock.patch("finance.stashes.registry.restore_stash"),
        ):
            response = self.client.post(self.restore_url(backup), {"current_pin": self.PIN}, follow=True)
        self.assertContains(response, "Restored from")
        self.assertTemplateUsed(response, "finance/settings.html")


class BackupNamesCarryTheStashTests(SimpleTestCase):
    """Plan 04 R5: two stashes' backups never share a file name."""

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
        self.make_stashes()
        type(self).databases |= {"alex"}
        folder, _ = registry.create_stash("Alex", "1234")  # a stash with its user and Profile, as a backup needs
        self.addCleanup(self._unregister, folder)
        call_command("migrate", "finance", "0016", database=folder, verbosity=0)
        old_backup = finance_backup.backup_now(
            registry.stash_db_path(folder), registry.stash_path(folder), tag="before_update"
        )
        call_command("migrate", database=folder, verbosity=0)

        registry.restore_stash(folder, old_backup)

        with connections[folder].cursor() as cursor:
            columns = [
                c.name for c in connections[folder].introspection.get_table_description(cursor, "finance_profile")
            ]
        self.assertIn("owner_name", columns)  # added by migration 0017, after the backup


class RestoreThroughThePageTests(TemporaryStashesMixin, SimpleTestCase):
    """The whole path with nothing mocked: the page's form, the PIN check, the restore itself and
    the page after it. The restore rewrites a real stash file, so this uses a temporary stash (as
    RestoreKeepsTodaysLoginTests does) instead of the test stash's in-memory database."""

    client_class = StashClient

    def test_the_data_goes_back_and_you_stay_logged_in_with_todays_pin(self):
        self.make_stashes()
        type(self).databases |= {"alex"}
        folder, _ = registry.create_stash("Alex", "1234")
        self.addCleanup(self._unregister, folder)
        with db_router.use_stash(folder):
            profile = security.profile_for_current_stash()
            profile.welcome_step = Profile.WELCOME_STEPS_DONE
            profile.save(update_fields=["welcome_step"])
        backup_path = finance_backup.backup_now(
            registry.stash_db_path(folder), registry.stash_path(folder), tag="test"
        )
        with db_router.use_stash(folder):
            Payee.objects.create(name="Lidl")
            user = get_user_model().objects.get(username=folder)
            user.set_password("5678")
            user.save()
            self.client.choose_stash(folder)
            self.client.force_login(user)

        url = f"{reverse('finance:backup_restore')}?{urlencode({'name': backup_path.name})}"
        page = self.client.get(url)
        self.assertContains(page, "your PIN and recovery code stay as they are now")
        response = self.client.post(url, {"current_pin": "5678"})
        self.assertRedirects(response, reverse("finance:settings"), fetch_redirect_response=False)

        # Still logged in: the backup's old PIN was not brought back with the data.
        settings_page = self.client.get(reverse("finance:settings"))
        self.assertEqual(settings_page.status_code, 200)
        self.assertContains(settings_page, "Restored from")
        with db_router.use_stash(folder):
            self.assertFalse(Payee.objects.filter(name="Lidl").exists())
            self.assertTrue(get_user_model().objects.get(username=folder).check_password("5678"))
