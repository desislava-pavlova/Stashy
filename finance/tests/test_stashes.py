"""Stashes themselves: owner names, creating a stash, and the last-resort PIN reset."""

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase

from audit.models import AuditLogEntry

from ..models import Profile
from ..stashes import StashError, registry, security
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
    """6.5 (F14): creating a stash and the reset_pin last resort, on a real temporary stash."""

    def test_create_then_reset_the_pin(self):
        self.make_stashes()
        # create_stash() registers the alias itself, so the test allows it up front.
        type(self).databases |= {"the-smiths"}
        folder, code = registry.create_stash("The Smiths", "1234")
        self.addCleanup(self._unregister, folder)
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
