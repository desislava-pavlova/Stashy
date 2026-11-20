"""Stashes themselves: owner names, creating a stash, and the
last-resort PIN reset."""

import sqlite3
import tempfile
from contextlib import closing
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import DEFAULT_DB_ALIAS, connections
from django.test import SimpleTestCase
from django.utils import timezone

from audit.context import audit_suspended
from audit.models import AuditLogEntry

from .. import backup as finance_backup
from .. import db_router, regional
from ..management.commands import reset_pin
from ..models import Payee, Profile
from ..stashes import StashError, folder_names, registry, security
from .base import TemporaryStashesMixin


class OwnerPossessiveTests(SimpleTestCase):
    def test_a_name_not_ending_in_s_gets_apostrophe_s(self):
        profile = Profile(owner_name="Alex")
        self.assertEqual(profile.owner_possessive, "Alex's")

    def test_a_name_ending_in_lowercase_s_gets_just_an_apostrophe(self):
        profile = Profile(owner_name="The Smiths")
        self.assertEqual(profile.owner_possessive, "The Smiths'")

    def test_a_name_ending_in_uppercase_s_gets_just_an_apostrophe(self):
        profile = Profile(owner_name="Chris S")
        self.assertEqual(profile.owner_possessive, "Chris S'")


class CreateAndResetStashTests(TemporaryStashesMixin, SimpleTestCase):
    """6.5 (F14): creating a stash and the reset_pin last resort, on a
    real temporary stash."""

    def test_create_then_reset_the_pin(self):
        self.make_stashes()
        folder, code = self.create_stash("The Smiths", "1234")
        self.assertEqual(folder, "the-smiths")
        self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")

        user = get_user_model().objects.db_manager(folder).get()
        profile = Profile.objects.using(folder).get()
        self.assertTrue(user.check_password("1234"))
        self.assertEqual(profile.owner_name, "The Smiths")
        self.assertTrue(security._check(code, profile.recovery_code_hash))
        # A brand new stash starts with an empty audit log.
        self.assertEqual(AuditLogEntry.objects.using(folder).count(), 0)
        self.assertEqual(registry.stash_choices(), [("the-smiths", "The Smiths")])

        Profile.objects.using(folder).update(failed_pin_attempts=3)
        registry.reset_pin_directly("the smiths", "5678")
        user.refresh_from_db()
        self.assertTrue(user.check_password("5678"))
        self.assertEqual(Profile.objects.using(folder).get().failed_pin_attempts, 0)

    def test_reset_rejects_a_bad_pin_and_an_unknown_stash(self):
        self.make_stashes()
        with self.assertRaisesMessage(StashError, "PIN must be exactly 4 digits."):
            registry.reset_pin_directly("anyone", "12")
        with self.assertRaisesMessage(StashError, 'No stash named "nobody".'):
            registry.reset_pin_directly("nobody", "1234")

    def test_an_owner_called_default_gets_a_real_stash_of_its_own(self):
        """S105: the folder name is also the stash's connection alias,
        and "default" is already the picker's empty placeholder
        database. The stash would have been built in the placeholder and
        its own folder left without a database, so it was never listed.
        """
        self.make_stashes()
        folder, _ = self.create_stash("Default", "1234")
        self.assertEqual(folder, "default-2")
        self.assertTrue(registry.stash_db_path("default-2").exists())
        self.assertEqual(registry.list_stashes(), ["default-2"])
        self.assertEqual(registry.stash_choices(), [("default-2", "Default")])
        self.assertEqual(Profile.objects.using("default-2").get().owner_name, "Default")

    def test_a_folder_name_that_is_reserved_gets_the_suffix_a_duplicate_gets(self):
        self.make_stashes()
        for owner_name, expected in [
            ("Default", "default-2"),
            ("DEFAULT", "default-2"),
            ("Con", "con-2"),
            ("nul", "nul-2"),
            ("AUX", "aux-2"),
            ("prn", "prn-2"),
            ("COM1", "com1-2"),
            ("lpt9", "lpt9-2"),
        ]:
            with self.subTest(owner_name=owner_name):
                self.assertEqual(registry._folder_name_from_owner(owner_name), expected)

    def test_names_that_only_look_like_reserved_ones_are_left_alone(self):
        self.make_stashes()
        for owner_name, expected in [
            ("Default Dan", "default-dan"),
            ("Console", "console"),
            ("com10", "com10"),
            ("Nully", "nully"),
        ]:
            with self.subTest(owner_name=owner_name):
                self.assertEqual(registry._folder_name_from_owner(owner_name), expected)

    def test_a_reserved_name_keeps_counting_past_a_suffix_already_taken(self):
        self.make_stashes("default-2")
        self.assertEqual(registry._folder_name_from_owner("Default"), "default-3")

    def test_the_first_free_suffix_is_used_even_when_a_later_one_is_taken(self):
        self.make_stashes("default-3", "con-2")
        self.assertEqual(registry._folder_name_from_owner("Default"), "default-2")
        self.assertEqual(registry._folder_name_from_owner("Con"), "con-3")

    def test_the_check_is_on_the_tidied_folder_name_so_spaces_and_punctuation_do_not_dodge_it(
        self,
    ):
        self.make_stashes()
        for owner_name, expected in [
            ("  Default  ", "default-2"),
            ("!Con!", "con-2"),
            # Only a name that tidies up to exactly a reserved word is
            # reserved.
            ("N.U.L", "n-u-l"),
            ("Com 1", "com-1"),
        ]:
            with self.subTest(owner_name=owner_name):
                self.assertEqual(registry._folder_name_from_owner(owner_name), expected)

    def test_the_pin_of_an_owner_called_default_can_be_reset_by_that_name(self):
        """The owner name finds the stash in "default-2": nothing looks
        for a folder called "default", and the stash's user is named
        after the folder it really lives in."""
        self.make_stashes()
        folder, _ = self.create_stash("Default", "1234")
        self.assertEqual(registry.reset_pin_directly("default", "5678"), ("default-2", "Default"))
        self.assertTrue(get_user_model().objects.db_manager(folder).get().check_password("5678"))


class ReservedFolderNameTests(SimpleTestCase):
    """S105 and S110: the names no stash may take."""

    def test_device_names_are_reserved_in_any_case(self):
        for name in [
            "con",
            "CON",
            "Prn",
            "aux",
            "NUL",
            "com1",
            "COM9",
            "lpt1",
            "Lpt9",
            "default",
            "Default",
        ]:
            with self.subTest(name=name):
                self.assertTrue(folder_names.is_reserved_folder_name(name))

    def test_names_that_only_look_reserved_are_not(self):
        for name in [
            "default-2",
            "default-dan",
            "console",
            "com10",
            "com0",
            "lpt",
            "nully",
            "alex",
        ]:
            with self.subTest(name=name):
                self.assertFalse(folder_names.is_reserved_folder_name(name))

    def test_default_is_the_placeholder_alias_django_calls_default(self):
        # folder_names can't import Django, so it spells the name out:
        # this is what keeps the two from drifting apart.
        self.assertIn(DEFAULT_DB_ALIAS, folder_names.RESERVED_FOLDER_NAMES)


class StashFolderScanTests(TemporaryStashesMixin, SimpleTestCase):
    """S110: the folders under stashes/ that are *not* stashes, though
    they hold a db.sqlite3."""

    def test_a_hand_made_default_folder_is_not_listed_as_a_stash(self):
        self.make_stashes("alex", "default-dan")
        registry.stash_path("default").mkdir()
        sqlite3.connect(registry.stash_db_path("default")).close()

        self.assertEqual(registry.list_stashes(), ["alex", "default-dan"])
        self.assertFalse(registry.is_stash("default"))
        self.assertTrue(registry.is_stash("default-dan"))

    def test_the_folder_scan_ignores_upper_and_lower_case_too(self):
        """The name check lower-cases the folder's name, and so must the
        scan that uses it: a folder copied in as "Default" is the same
        folder as "default" on Windows. A name that only starts or ends
        like a reserved one is a stash, as before. (Device names such as
        "CON" are left out of this test on purpose: Windows can't open,
        copy or delete a folder of that name, so the test would leave
        one behind.)"""
        self.make_stashes("alex", "console", "com10")
        registry.stash_path("Default").mkdir()
        sqlite3.connect(registry.stash_db_path("Default")).close()

        self.assertEqual(registry.list_stashes(), ["alex", "com10", "console"])
        self.assertFalse(registry.is_stash("Default"))


class ResetPinCommandTests(TemporaryStashesMixin, SimpleTestCase):
    """manage.py reset_pin on a real temporary stash: Alex's, in the
    folder "alex". Building and migrating a stash takes a while, so it
    is built once for the class, and each test starts from a copy of it
    as it was then (the tests change PINs, delete a Profile and rename a
    user)."""

    @classmethod
    def setUpClass(cls):
        # Before super(): Django checks `databases` there, and the
        # stash's alias has to exist by then.
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        patcher = mock.patch.object(registry, "stashes_dir", return_value=Path(tmp.name))
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.folder, _ = registry.create_stash("Alex", "1234")
        cls.addClassCleanup(cls.unregister, cls.folder)
        cls.databases = frozenset({cls.folder})
        cls.pristine = Path(tmp.name) / "pristine.sqlite3"  # beside the stash's folder, not in it
        cls.copy_database(registry.stash_db_path(cls.folder), cls.pristine)
        super().setUpClass()

    @staticmethod
    def copy_database(source, target):
        """With SQLite's own backup, like finance/backup.py: a plain
        file copy could miss what is still in the journal."""
        with (
            closing(sqlite3.connect(source)) as from_database,
            closing(sqlite3.connect(target)) as to_database,
        ):
            from_database.backup(to_database)

    def setUp(self):
        connections[self.folder].close()
        self.copy_database(self.pristine, registry.stash_db_path(self.folder))
        self.user = get_user_model().objects.db_manager(self.folder).get()

    def test_the_name_works_in_any_upper_or_lower_case(self):
        """Plan 04, R9: "Alex" crashed on Windows, which finds the
        folder "alex" under that name too, while the stash's user is
        called "alex"."""
        for name, pin in [("Alex", "5678"), ("ALEX", "2468"), ("alex", "1357")]:
            with self.subTest(name=name):
                call_command("reset_pin", name, pin=pin, stdout=StringIO())
                self.user.refresh_from_db()
                self.assertTrue(self.user.check_password(pin))
        self.assertNotIn("Alex", connections.databases)

    def test_an_owner_name_unlike_its_folder_works_in_any_case_too(self):
        """A Cyrillic owner name gets the folder "stash", so only the
        owner name finds it."""
        folder, _ = self.create_stash("Юлия", "1234")
        call_command("reset_pin", "ЮЛИЯ", pin="5678", stdout=StringIO())
        self.assertEqual(folder, "stash")
        self.assertTrue(get_user_model().objects.db_manager(folder).get().check_password("5678"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("1234"))

    def test_an_unknown_name_or_a_bad_pin_ends_with_a_message_and_changes_nothing(self):
        cases = [
            ("Sam", "5678", 'No stash named "Sam".'),
            ("Alex", "56", "PIN must be exactly 4 digits."),
        ]
        for name, pin, message in cases:
            with self.subTest(name=name, pin=pin), self.assertRaisesMessage(CommandError, message):
                call_command("reset_pin", name, pin=pin, stdout=StringIO())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("1234"))

    def test_it_says_which_stash_it_changed(self):
        """Not just the name typed: a name could be one stash's folder
        and another's owner."""
        output = StringIO()
        call_command("reset_pin", "ALEX", pin="5678", stdout=output)
        self.assertIn('PIN reset for Alex\'s stash (folder "alex").', output.getvalue())

    def test_a_stash_copied_in_without_a_profile_still_gets_its_new_pin(self):
        with audit_suspended():
            Profile.objects.using(self.folder).all().delete()
        output = StringIO()
        call_command("reset_pin", "alex", pin="5678", stdout=output)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("5678"))
        self.assertIn('(folder "alex")', output.getvalue())

    def test_a_stash_without_its_user_ends_with_a_message(self):
        get_user_model().objects.using(self.folder).update(username="someone-else")
        with self.assertRaisesMessage(CommandError, "has no user of that name"):
            call_command("reset_pin", "alex", pin="5678", stdout=StringIO())

    def test_without_pin_it_asks_twice_with_a_star_for_each_digit(self):
        """Plan 03, S24: Python 3.14's getpass shows a * per key. S83:
        asked twice, like every PIN box on the website."""
        with (
            mock.patch.object(reset_pin, "GETPASS_SHOWS_STARS", True),
            mock.patch("getpass.getpass", return_value="5678") as ask,
        ):
            call_command("reset_pin", "Alex", stdout=StringIO())
        self.assertEqual(
            ask.call_args_list,
            [
                mock.call("New 4-digit PIN: ", echo_char="*"),
                mock.call("Type it again: ", echo_char="*"),
            ],
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("5678"))

    def test_before_python_3_14_it_asks_without_stars(self):
        """getpass has no echo_char before 3.14, and Stashy.bat still
        accepts 3.12 and 3.13."""
        with (
            mock.patch.object(reset_pin, "GETPASS_SHOWS_STARS", False),
            mock.patch("getpass.getpass", return_value="5678") as ask,
        ):
            call_command("reset_pin", "Alex", stdout=StringIO())
        self.assertEqual(
            ask.call_args_list, [mock.call("New 4-digit PIN: "), mock.call("Type it again: ")]
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("5678"))

    def test_an_unknown_name_is_refused_before_any_pin_is_asked_for(self):
        with (
            mock.patch("getpass.getpass") as ask,
            self.assertRaisesMessage(CommandError, 'No stash named "Nobody".'),
        ):
            call_command("reset_pin", "Nobody", stdout=StringIO())
        ask.assert_not_called()

    def test_two_different_pins_change_nothing(self):
        with (
            mock.patch("getpass.getpass", side_effect=["5678", "5687"]),
            self.assertRaisesMessage(
                CommandError, "PINs don't match. Nothing was changed."
            ) as caught,
        ):
            call_command("reset_pin", "Alex", stdout=StringIO())
        # What went wrong, never the PINs themselves: this message is
        # printed in the terminal.
        self.assertNotIn("5678", str(caught.exception))
        self.assertNotIn("5687", str(caught.exception))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("1234"))

    def test_a_pin_given_on_the_command_line_is_not_asked_for(self):
        with mock.patch("getpass.getpass") as ask:
            call_command("reset_pin", "Alex", pin="5678", stdout=StringIO())
        ask.assert_not_called()

    def test_a_pin_typed_twice_that_is_not_four_digits_changes_nothing(self):
        for typed in ("56", "abcd", "56789", ""):
            with (
                self.subTest(typed=typed),
                mock.patch("getpass.getpass", return_value=typed),
                self.assertRaisesMessage(CommandError, "PIN must be exactly 4 digits."),
            ):
                call_command("reset_pin", "Alex", stdout=StringIO())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("1234"))

    def test_it_leaves_the_recovery_code_alone(self):
        """It's for when the code is lost too, so it only changes the
        PIN and the lockout."""
        before = Profile.objects.using(self.folder).get().recovery_code_hash
        call_command("reset_pin", "Alex", pin="5678", stdout=StringIO())
        self.assertEqual(Profile.objects.using(self.folder).get().recovery_code_hash, before)

    def test_it_changes_the_named_stash_even_while_another_one_is_current(self):
        """The command runs with no session, and the stash locked out
        isn't necessarily the one that happens to be current: the audit
        entry belongs in the named stash's own log."""
        sam, _ = self.create_stash("Sam", "4321")
        Profile.objects.using(self.folder).update(failed_pin_attempts=3)

        with db_router.use_stash(sam):
            call_command("reset_pin", "Alex", pin="5678", stdout=StringIO())
            self.assertEqual(db_router.current_stash_alias(), sam)  # put back afterwards

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("5678"))
        self.assertTrue(get_user_model().objects.db_manager(sam).get().check_password("4321"))
        self.assertTrue(
            AuditLogEntry.objects.using(self.folder).filter(table="finance_profile").exists()
        )
        self.assertFalse(AuditLogEntry.objects.using(sam).exists())

    def test_clearing_the_lockout_is_in_the_stashs_audit_log(self):
        """Plan 03, S24's note: the reset used .update(), which the
        audit log never sees."""
        Profile.objects.using(self.folder).update(
            failed_pin_attempts=3, locked_until=timezone.now() + timedelta(minutes=5)
        )
        registry.reset_pin_directly("Alex", "5678")

        entries = AuditLogEntry.objects.using(self.folder).filter(table="finance_profile")
        self.assertEqual(
            sorted(entries.values_list("column", "old_value", "new_value")),
            [("failed_pin_attempts", "3", "0"), ("locked_until", mock.ANY, None)],
        )
        self.assertEqual(
            {(entry.source, entry.reason) for entry in entries},
            {("system", "PIN reset with manage.py reset_pin")},
        )


class RestoreKeepsTodaysLoginTests(TemporaryStashesMixin, SimpleTestCase):
    """Plan 03, Q3: a restore brings back data, not the PIN, the
    recovery code or the lockout saved in the backup."""

    def test_the_data_goes_back_but_the_pin_the_recovery_code_and_the_lockout_stay(self):
        self.make_stashes()
        folder, code_in_the_backup = self.create_stash("Alex", "1234")
        backup_path = finance_backup.backup_now(
            registry.stash_db_path(folder), registry.stash_path(folder), tag="test"
        )
        with db_router.use_stash(folder):
            Payee.objects.create(name="Lidl")
            profile = security.profile_for_current_stash()
            profile.user.set_password("5678")
            profile.user.save()
            todays_code = security.regenerate_recovery_code(profile)
            security.register_failed_attempt(profile)

        registry.restore_stash(folder, backup_path)

        with db_router.use_stash(folder):
            self.assertFalse(Payee.objects.filter(name="Lidl").exists())
            profile = security.profile_for_current_stash()
            self.assertTrue(profile.user.check_password("5678"))
            self.assertTrue(security._check(todays_code, profile.recovery_code_hash))
            self.assertFalse(security._check(code_in_the_backup, profile.recovery_code_hash))
            self.assertEqual(profile.failed_pin_attempts, 1)
            reasons = set(AuditLogEntry.objects.values_list("reason", flat=True))
        self.assertIn("Kept today's PIN, recovery code and lockout after a restore", reasons)

    def make_alex(self):
        self.make_stashes()
        folder, _ = self.create_stash("Alex", "1234")
        return folder

    def back_up(self, folder, tag):
        return finance_backup.backup_now(
            registry.stash_db_path(folder), registry.stash_path(folder), tag=tag
        )

    def test_a_lockout_today_stays_and_a_lockout_in_the_backup_does_not_come_back(self):
        """A restore must neither undo a lockout nor start one."""
        folder = self.make_alex()
        not_locked = self.back_up(folder, "open")
        locked_until = timezone.now() + timedelta(minutes=security.LOCKOUT_MINUTES)
        with db_router.use_stash(folder):
            profile = security.profile_for_current_stash()
            profile.locked_until = locked_until
            profile.save(update_fields=["locked_until"])
        locked = self.back_up(folder, "locked")

        registry.restore_stash(folder, not_locked)
        with db_router.use_stash(folder):
            profile = security.profile_for_current_stash()
            self.assertEqual(profile.locked_until, locked_until)
            security.clear_failed_attempts(profile)

        registry.restore_stash(folder, locked)
        with db_router.use_stash(folder):
            self.assertIsNone(security.profile_for_current_stash().locked_until)

    def test_the_audit_log_never_shows_the_recovery_codes_hash(self):
        folder = self.make_alex()
        backup_path = self.back_up(folder, "test")
        with db_router.use_stash(folder):
            profile = security.profile_for_current_stash()
            security.regenerate_recovery_code(profile)
            todays_hash = profile.recovery_code_hash

        registry.restore_stash(folder, backup_path)

        with db_router.use_stash(folder):
            entries = AuditLogEntry.objects.filter(column="recovery_code_hash")
            self.assertEqual(
                {(entry.old_value, entry.new_value) for entry in entries},
                {("(hidden)", "(hidden)")},
            )
            for entry in AuditLogEntry.objects.all():
                self.assertNotIn(
                    todays_hash, f"{entry.old_value} {entry.new_value} {entry.values}"
                )

    def assert_refused_and_nothing_changed(self, folder, backup_path, message):
        """Plan 03, S88: a backup that would leave the stash unusable is
        refused, with a message, before the live database is touched: no
        safety copy is made, the data and today's PIN stay, and the
        backup file itself is left as it was."""
        before = backup_path.read_bytes()
        safety_copies = list(registry.stash_backups_dir(folder).glob("*before_restore*"))

        with self.assertRaises(StashError) as raised:
            registry.restore_stash(folder, backup_path)

        self.assertEqual(str(raised.exception), message)
        self.assertEqual(backup_path.read_bytes(), before)
        self.assertEqual(
            list(registry.stash_backups_dir(folder).glob("*before_restore*")), safety_copies
        )
        with db_router.use_stash(folder):
            self.assertTrue(Payee.objects.filter(name="Lidl").exists())
            self.assertTrue(get_user_model().objects.get(username=folder).check_password("1234"))

    def make_alex_with_a_payee(self):
        folder = self.make_alex()
        with db_router.use_stash(folder):
            Payee.objects.create(name="Lidl")
        return folder

    def test_a_file_that_is_not_a_database_is_refused(self):
        folder = self.make_alex_with_a_payee()
        damaged = (
            registry.stash_backups_dir(folder) / "alex_backup_damaged_2026-01-01_00-00-00.sqlite3"
        )
        damaged.write_text("this is not a database")

        self.assert_refused_and_nothing_changed(folder, damaged, registry.BACKUP_DAMAGED)

    def test_an_empty_backup_file_is_refused(self):
        """A crash while a backup was being written can leave a file
        with no tables at all."""
        folder = self.make_alex_with_a_payee()
        empty = (
            registry.stash_backups_dir(folder)
            / "alex_backup_cut_short_2026-01-01_00-00-00.sqlite3"
        )
        empty.touch()

        self.assert_refused_and_nothing_changed(folder, empty, registry.BACKUP_EMPTY)

    def test_a_backup_cut_short_part_way_is_refused(self):
        folder = self.make_alex_with_a_payee()
        whole = self.back_up(folder, "whole")
        cut = whole.with_name(whole.name.replace("whole", "cut"))
        cut.write_bytes(whole.read_bytes()[: whole.stat().st_size // 2])

        self.assert_refused_and_nothing_changed(folder, cut, registry.BACKUP_DAMAGED)

    def test_a_backup_from_before_there_was_a_profile_table_is_refused_while_the_stash_has_one(
        self,
    ):
        folder = self.make_alex_with_a_payee()
        old = self.back_up(folder, "ancient")
        with closing(sqlite3.connect(old)) as connection, connection:
            connection.execute("DROP TABLE finance_profile")

        self.assert_refused_and_nothing_changed(folder, old, registry.BACKUP_WITHOUT_SETTINGS)

    def test_a_database_without_this_stashs_user_is_refused(self):
        """It has tables, but nobody could log in to the stash after
        restoring it."""
        folder = self.make_alex_with_a_payee()
        other = self.back_up(folder, "other")
        with closing(sqlite3.connect(other)) as connection, connection:
            connection.execute("UPDATE auth_user SET username = 'someone-else'")

        self.assert_refused_and_nothing_changed(folder, other, registry.BACKUP_EMPTY)

    def backup_without_a_profile(self, folder):
        """A backup of `folder` with its Profile row gone, then today's
        Profile put back with the PIN 5678 and a new recovery code
        (returned)."""
        with audit_suspended():
            Profile.objects.using(folder).all().delete()
        without_profile = self.back_up(folder, "noprofile")
        with db_router.use_stash(folder):
            user = get_user_model().objects.get(username=folder)
            user.set_password("5678")
            user.save()
            profile = Profile.objects.create(user=user)
            todays_code = security.regenerate_recovery_code(profile)
        return without_profile, todays_code

    def test_a_backup_with_no_profile_is_refused_while_the_stash_has_one(self):
        """Today's recovery code and lockout would have no row to go
        back into, so the stash would end with none. Refused instead,
        and everything today stays."""
        folder = self.make_alex_with_a_payee()
        without_profile, todays_code = self.backup_without_a_profile(folder)

        with self.assertRaises(StashError) as raised:
            registry.restore_stash(folder, without_profile)

        self.assertEqual(str(raised.exception), registry.BACKUP_WITHOUT_SETTINGS)
        with db_router.use_stash(folder):
            self.assertTrue(Payee.objects.filter(name="Lidl").exists())
            self.assertTrue(get_user_model().objects.get(username=folder).check_password("5678"))
            profile = security.profile_for_current_stash()
            self.assertTrue(security._check(todays_code, profile.recovery_code_hash))

    def test_a_stash_that_has_no_profile_keeps_its_pin_and_gains_no_profile(self):
        folder = self.make_alex()
        with audit_suspended():
            Profile.objects.using(folder).all().delete()
        backup_path = self.back_up(folder, "noprofile")
        with db_router.use_stash(folder):
            user = get_user_model().objects.get(username=folder)
            user.set_password("5678")
            user.save()

        registry.restore_stash(folder, backup_path)

        with db_router.use_stash(folder):
            self.assertTrue(get_user_model().objects.get(username=folder).check_password("5678"))
            self.assertFalse(Profile.objects.exists())

    def change_todays_login(self, folder):
        """Today's PIN is 5678, the recovery code a new one (returned),
        and one wrong try is counted."""
        with db_router.use_stash(folder):
            profile = security.profile_for_current_stash()
            profile.user.set_password("5678")
            profile.user.save()
            todays_code = security.regenerate_recovery_code(profile)
            security.register_failed_attempt(profile)
        return todays_code

    def assert_todays_login_is_kept(self, folder, todays_code):
        with db_router.use_stash(folder):
            profile = security.profile_for_current_stash()
            self.assertTrue(profile.user.check_password("5678"))
            self.assertTrue(security._check(todays_code, profile.recovery_code_hash))
            self.assertEqual(profile.failed_pin_attempts, 1)

    def test_a_backup_from_before_an_update_still_gets_todays_login_put_back(self):
        """Plan 03, Q3 with an older database: until it is migrated, the
        Profile table doesn't have today's columns, and the PIN (the
        user's own table, unchanged) goes back first."""
        folder = self.make_alex()
        call_command("migrate", "finance", "0016", database=folder, verbosity=0)
        old_backup = self.back_up(folder, "before_update")
        call_command("migrate", database=folder, verbosity=0)
        todays_code = self.change_todays_login(folder)

        registry.restore_stash(folder, old_backup)

        self.assert_todays_login_is_kept(folder, todays_code)
        with db_router.use_stash(folder):
            self.assertEqual(
                Profile.objects.get().owner_name, folder
            )  # a new column, filled in by migrating

    def test_todays_pin_is_back_before_the_migration_starts(self):
        """The backup's PIN is live from the moment its file replaces
        the database, and a migration of an old backup takes a while and
        can fail."""
        folder = self.make_alex()
        backup_path = self.back_up(folder, "test")
        self.change_todays_login(folder)
        seen = {}

        def migrate(*args, **kwargs):
            with db_router.use_stash(folder):
                seen["pin"] = get_user_model().objects.get(username=folder).check_password("5678")

        with mock.patch.object(registry, "call_command", side_effect=migrate):
            registry.restore_stash(folder, backup_path)

        self.assertEqual(seen, {"pin": True})

    def test_a_migration_that_fails_still_leaves_todays_whole_login(self):
        folder = self.make_alex()
        backup_path = self.back_up(folder, "test")
        todays_code = self.change_todays_login(folder)

        with (
            mock.patch.object(
                registry, "call_command", side_effect=CommandError("the migration failed")
            ),
            self.assertRaisesMessage(CommandError, "the migration failed"),
        ):
            registry.restore_stash(folder, backup_path)

        self.assert_todays_login_is_kept(folder, todays_code)


class NewStashRegionalSettingsTests(TemporaryStashesMixin, SimpleTestCase):
    """Plan 03, S77: what a new stash reads from this computer is a zone
    the Settings list has."""

    def test_a_new_stash_gets_the_listed_name_of_the_computers_zone(self):
        self.make_stashes()
        with (
            mock.patch.object(regional, "_read_windows_settings", return_value=({}, "US")),
            mock.patch.object(regional.tzlocal, "get_localzone_name", return_value="Europe/Kiev"),
        ):
            folder, _ = self.create_stash("Alex", "1234")
        self.assertEqual(Profile.objects.using(folder).get().time_zone, "Europe/Kyiv")
        self.assertIn(Profile.objects.using(folder).get().time_zone, regional.time_zone_choices())
