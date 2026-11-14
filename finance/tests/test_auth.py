"""Getting into a stash: the PIN rule, the lockout, recovery codes, a login for every page,
the admin's login, and switching stashes."""

from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from django.urls import NoReverseMatch, URLPattern, resolve, reverse
from django.utils import timezone
from django.views.generic import RedirectView

from .. import urls as finance_urls
from ..auth_forms import ChangePinForm, CurrentPinForm, ForgotPinForm, StashCreateForm
from ..models import Profile
from ..stashes import StashError, registry, security
from .base import REHASHING_PASSWORD_HASHERS, StashTestCase

# Values for the URL parameters, so every pattern in finance/urls.py can be reversed.
SAMPLE_KWARGS = {"pk": 1, "step": 1, "report": "spending"}


def _every_finance_url():
    for pattern in finance_urls.urlpatterns:
        assert isinstance(pattern, URLPattern)
        kwargs = {name: SAMPLE_KWARGS[name] for name in pattern.pattern.converters}
        yield pattern.name, reverse(f"finance:{pattern.name}", kwargs=kwargs)


class StashPathHelperTests(SimpleTestCase):
    def test_validate_pin_requires_exactly_four_digits(self):
        for bad in ("123", "12345", "abcd", "", None):
            with self.assertRaises(StashError):
                security.validate_pin(bad)
        security.validate_pin("0000")  # does not raise

    def test_recovery_code_format(self):
        code = security._generate_recovery_code()
        parts = code.split("-")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(len(p) == 4 for p in parts))
        # No ambiguous characters (0/O/1/I) that are hard to tell apart handwritten.
        self.assertFalse(set(code) & set("01OI-") - {"-"})


class LockoutTests(StashTestCase):
    """These operate on the *current* (test/default) database's own Profile, exactly the way
    the login view does - no temp stash needed, since lockout state lives on Profile."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("tester", password="1234")
        cls.profile = Profile.objects.create(user=cls.user)

    def test_not_locked_out_initially(self):
        self.assertFalse(security.is_locked_out(self.profile))

    def test_locks_out_after_the_threshold(self):
        for _ in range(security.LOCKOUT_THRESHOLD):
            security.register_failed_attempt(self.profile)
        self.profile.refresh_from_db()
        self.assertTrue(security.is_locked_out(self.profile))
        self.assertEqual(self.profile.failed_pin_attempts, 0)  # reset when the lock kicks in

    def test_a_failed_attempt_while_already_locked_does_not_extend_it(self):
        self.profile.locked_until = timezone.now() + timedelta(minutes=5)
        self.profile.save()
        original = self.profile.locked_until
        security.register_failed_attempt(self.profile)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.locked_until, original)

    def test_successful_login_clears_a_partial_failure_count(self):
        security.register_failed_attempt(self.profile)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 1)
        security.clear_failed_attempts(self.profile)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 0)

    def test_check_pin_counts_a_wrong_pin_toward_the_lockout(self):
        self.assertFalse(security.check_pin(self.profile, "0000"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 1)

    def test_check_pin_accepts_the_right_pin_and_clears_earlier_tries(self):
        security.register_failed_attempt(self.profile)
        self.assertTrue(security.check_pin(self.profile, "1234"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 0)

    def test_check_pin_counts_from_the_saved_number_not_an_old_copy(self):
        """Plan 03, S75: two requests at the same moment both hold a copy read before either
        counted. Each must count from the saved number, or one wrong PIN goes uncounted."""
        old_copy = Profile.objects.get(pk=self.profile.pk)
        security.check_pin(self.profile, "0000")
        security.check_pin(old_copy, "0000")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 2)

    def test_an_old_copy_cannot_get_past_or_erase_a_lockout(self):
        old_copy = Profile.objects.get(pk=self.profile.pk)
        self.profile.locked_until = timezone.now() + timedelta(minutes=security.LOCKOUT_MINUTES)
        self.profile.save()
        self.assertFalse(security.check_pin(old_copy, "1234"))
        self.profile.refresh_from_db()
        self.assertTrue(security.is_locked_out(self.profile))

    def test_check_pin_refuses_even_the_right_pin_while_locked_out(self):
        self.profile.locked_until = timezone.now() + timedelta(minutes=5)
        self.profile.save()
        self.assertFalse(security.check_pin(self.profile, "1234"))

    def test_check_pin_accepts_the_right_pin_once_the_lockout_has_run_out(self):
        self.profile.locked_until = timezone.now() - timedelta(seconds=1)
        self.profile.save()
        self.assertTrue(security.check_pin(self.profile, "1234"))
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.locked_until)

    def test_one_try_short_of_the_threshold_does_not_lock_and_the_next_one_does(self):
        for _ in range(security.LOCKOUT_THRESHOLD - 1):
            self.assertFalse(security.check_pin(self.profile, "0000"))
        self.assertFalse(security.is_locked_out(self.profile))
        self.assertFalse(security.check_pin(self.profile, "0000"))
        self.assertTrue(security.is_locked_out(self.profile))
        self.assertEqual(self.profile.failed_pin_attempts, 0)  # the count starts again after a lockout
        self.assertFalse(security.check_pin(self.profile, "1234"))

    def test_a_wrong_pin_while_locked_out_is_not_counted_and_does_not_extend_the_lockout(self):
        locked_until = timezone.now() + timedelta(minutes=2)
        self.profile.locked_until = locked_until
        self.profile.save()
        self.assertFalse(security.check_pin(self.profile, "0000"))
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.failed_pin_attempts, self.profile.locked_until), (0, locked_until))

    def test_an_old_copy_counts_the_try_that_starts_the_lockout(self):
        old_copy = Profile.objects.get(pk=self.profile.pk)
        self.profile.failed_pin_attempts = security.LOCKOUT_THRESHOLD - 1
        self.profile.save()
        self.assertFalse(security.check_pin(old_copy, "0000"))
        self.profile.refresh_from_db()
        self.assertTrue(security.is_locked_out(self.profile))

    def test_an_old_copy_is_held_to_the_pin_as_it_is_now_not_as_it_was(self):
        old_copy = Profile.objects.select_related("user").get(pk=self.profile.pk)
        self.user.set_password("5678")
        self.user.save()
        self.assertFalse(security.check_pin(old_copy, "1234"))
        self.assertTrue(security.check_pin(old_copy, "5678"))

    def test_checking_a_pin_never_re_saves_its_hash(self):
        """Plan 03, S87: the login is tied to the stored hash. Saving it again in a newer format
        after a Django update would log you out of the very page that asked for the PIN."""
        with override_settings(PASSWORD_HASHERS=REHASHING_PASSWORD_HASHERS):
            self.assertTrue(security.check_pin(self.profile, "1234"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.password.startswith("md5$"), self.user.password)

    def test_an_old_copy_of_a_lockout_that_has_since_been_cleared_is_not_held_to_it(self):
        """The new read works both ways: it's the saved state that counts, not the caller's copy."""
        self.profile.locked_until = timezone.now() + timedelta(minutes=5)
        self.profile.save()
        old_copy = Profile.objects.get(pk=self.profile.pk)
        security.clear_failed_attempts(self.profile)
        self.assertTrue(security.check_pin(old_copy, "1234"))


class RecoveryCodeResetTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("tester", password="1234")
        cls.recovery_code = "ABCD-EFGH-JKLM"
        cls.profile = Profile.objects.create(user=cls.user, recovery_code_hash=security._hash(cls.recovery_code))

    def test_correct_code_resets_the_pin_and_issues_a_new_code(self):
        ok, new_code = security.reset_pin_with_recovery_code(self.profile, self.recovery_code, "9999")
        self.assertTrue(ok)
        self.assertNotEqual(new_code, self.recovery_code)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("9999"))

    def test_old_recovery_code_stops_working_after_a_reset(self):
        security.reset_pin_with_recovery_code(self.profile, self.recovery_code, "9999")
        self.profile.refresh_from_db()
        ok, _ = security.reset_pin_with_recovery_code(self.profile, self.recovery_code, "1111")
        self.assertFalse(ok)

    def test_a_wrong_code_counts_from_the_saved_number_not_an_old_copy(self):
        """Plan 03, S75, on the Forgot PIN page."""
        old_copy = Profile.objects.get(pk=self.profile.pk)
        security.register_failed_attempt(self.profile)
        security.reset_pin_with_recovery_code(old_copy, "WRONG-CODE-HERE", "9999")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 2)

    def test_wrong_code_counts_as_a_failed_attempt(self):
        ok, new_code = security.reset_pin_with_recovery_code(self.profile, "WRONG-CODE-HERE", "9999")
        self.assertFalse(ok)
        self.assertIsNone(new_code)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 1)

    def test_a_right_code_works_during_a_lockout_and_ends_it(self):
        """Plan 03, S91 (the user's call): the PIN screen offers the recovery code as the way out."""
        self.profile.failed_pin_attempts = 2
        self.profile.locked_until = timezone.now() + timedelta(minutes=5)
        self.profile.save()
        ok, new_code = security.reset_pin_with_recovery_code(self.profile, self.recovery_code, "9999")
        self.assertTrue(ok)
        self.assertTrue(new_code)
        self.user.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertTrue(self.user.check_password("9999"))
        self.assertEqual((self.profile.failed_pin_attempts, self.profile.locked_until), (0, None))

    def test_a_wrong_code_during_a_lockout_changes_nothing_and_does_not_extend_it(self):
        old_hash = self.profile.recovery_code_hash
        locked_until = timezone.now() + timedelta(minutes=5)
        self.profile.locked_until = locked_until
        self.profile.save()
        result = security.reset_pin_with_recovery_code(self.profile, "WRONG-CODE-HERE", "9999")
        self.assertEqual(result, (False, None))
        self.user.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertTrue(self.user.check_password("1234"))
        self.assertEqual(self.profile.recovery_code_hash, old_hash)
        self.assertEqual((self.profile.failed_pin_attempts, self.profile.locked_until), (0, locked_until))

    def test_an_old_copy_cannot_get_a_wrong_code_past_the_lockout_either_way(self):
        """The request's copy says "not locked"; the saved row says it is. A wrong code is
        still refused, and the lockout is left as it is."""
        old_copy = Profile.objects.get(pk=self.profile.pk)
        locked_until = timezone.now() + timedelta(minutes=5)
        self.profile.locked_until = locked_until
        self.profile.save()
        result = security.reset_pin_with_recovery_code(old_copy, "WRONG-CODE-HERE", "9999")
        self.assertEqual(result, (False, None))
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.failed_pin_attempts, self.profile.locked_until), (0, locked_until))

    def test_an_old_copy_cannot_use_a_code_that_has_since_been_replaced(self):
        old_copy = Profile.objects.select_related("user").get(pk=self.profile.pk)
        security.regenerate_recovery_code(self.profile)
        result = security.reset_pin_with_recovery_code(old_copy, self.recovery_code, "9999")
        self.assertEqual(result, (False, None))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("1234"))

    def test_the_right_code_forgets_earlier_wrong_tries_and_an_ended_lockout(self):
        self.profile.failed_pin_attempts = 3
        self.profile.locked_until = timezone.now() - timedelta(seconds=1)
        self.profile.save()
        ok, _ = security.reset_pin_with_recovery_code(self.profile, self.recovery_code, "9999")
        self.assertTrue(ok)
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.failed_pin_attempts, self.profile.locked_until), (0, None))

    def test_a_stash_without_a_recovery_code_never_accepts_one(self):
        """Not even a blank one: an empty saved hash must never match an empty guess."""
        self.profile.recovery_code_hash = ""
        self.profile.save()
        for guess in ("", " ", "ABCD-EFGH-JKLM"):
            with self.subTest(guess=guess):
                self.assertEqual(security.reset_pin_with_recovery_code(self.profile, guess, "9999"), (False, None))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("1234"))


class ForgotPinPageTests(StashTestCase):
    """The Forgot PIN page, through real requests: the one place a recovery code is used, and
    the other page (with the PIN screen) that counts toward the lockout."""

    RECOVERY_CODE = "ABCD-EFGH-JKLM"

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(settings.TEST_STASH, password="1234")
        cls.profile = Profile.objects.create(
            user=cls.user, owner_name="Alex", recovery_code_hash=security._hash(cls.RECOVERY_CODE)
        )

    def reset(self, code=RECOVERY_CODE, pin="5678", confirm=None):
        data = {"recovery_code": code, "new_pin": pin, "new_pin_confirm": pin if confirm is None else confirm}
        return self.client.post(reverse("forgot_pin"), data)

    def assert_nothing_changed(self, *, tries=0):
        self.user.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertTrue(self.user.check_password("1234"))
        self.assertTrue(security._check(self.RECOVERY_CODE, self.profile.recovery_code_hash))
        self.assertEqual(self.profile.failed_pin_attempts, tries)

    def test_the_page_asks_for_the_code_and_the_new_pin_twice(self):
        response = self.client.get(reverse("forgot_pin"))
        self.assertContains(response, "Reset your PIN")
        self.assertContains(response, "For Alex&#x27;s stash")
        for field in ("recovery_code", "new_pin", "new_pin_confirm"):
            self.assertContains(response, f'name="{field}"')
        for leaked in ("{%", "%}", "{#", "#}"):
            self.assertNotContains(response, leaked)

    def test_the_right_code_changes_the_pin_and_shows_a_new_code_once(self):
        response = self.reset()
        self.assertContains(response, "PIN changed")
        new_code = response.context["recovery_code"]
        self.assertContains(response, new_code)
        self.user.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertTrue(self.user.check_password("5678"))
        self.assertTrue(security._check(new_code, self.profile.recovery_code_hash))
        self.assertFalse(security._check(self.RECOVERY_CODE, self.profile.recovery_code_hash))
        self.assertNotContains(self.client.get(reverse("forgot_pin")), new_code)

    def test_the_new_pin_logs_in_and_the_old_one_does_not(self):
        self.reset()
        old = self.client.post(reverse("login"), {"username": settings.TEST_STASH, "password": "1234"})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(old.status_code, 200)
        new = self.client.post(reverse("login"), {"username": settings.TEST_STASH, "password": "5678"})
        self.assertRedirects(new, reverse("finance:dashboard"), fetch_redirect_response=False)

    def test_the_code_is_accepted_in_any_case_and_with_spaces_around_it(self):
        self.assertContains(self.reset(code=f"  {self.RECOVERY_CODE.lower()} "), "PIN changed")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("5678"))

    def test_a_wrong_code_says_so_changes_nothing_and_counts_as_a_try(self):
        response = self.reset(code="WXYZ-WXYZ-WXYZ")
        self.assertContains(response, "That recovery code isn&#x27;t right.")
        self.assertNotContains(response, "PIN changed")
        self.assert_nothing_changed(tries=1)

    def test_a_mistyped_confirmation_or_a_pin_of_the_wrong_shape_is_not_a_try(self):
        cases = [
            ({"pin": "5678", "confirm": "8765"}, "PINs don&#x27;t match."),
            ({"pin": "56"}, "PIN must be exactly 4 digits."),
            ({"pin": "abcd"}, "PIN must be exactly 4 digits."),
        ]
        for kwargs, message in cases:
            with self.subTest(**kwargs):
                self.assertContains(self.reset(**kwargs), message)
        self.assert_nothing_changed()

    def test_the_last_wrong_code_locks_the_stash_and_the_page_says_so_but_keeps_the_form(self):
        for _ in range(security.LOCKOUT_THRESHOLD - 1):
            self.assertContains(self.reset(code="WXYZ-WXYZ-WXYZ"), "Reset PIN</button>")
        response = self.reset(code="WXYZ-WXYZ-WXYZ")
        self.assertContains(response, "Too many wrong attempts. The PIN screen is locked for about 5 minutes")
        self.assertContains(response, "Reset PIN</button>")  # S91: the recovery code is the way out
        self.profile.refresh_from_db()
        self.assertTrue(security.is_locked_out(self.profile))

    def test_a_locked_stash_accepts_the_right_code_and_the_lockout_ends(self):
        """Plan 03, S91 (the user's call). The PIN screen says "or reset it with your recovery code"."""
        self.profile.failed_pin_attempts = 2
        self.profile.locked_until = timezone.now() + timedelta(minutes=security.LOCKOUT_MINUTES)
        self.profile.save(update_fields=["failed_pin_attempts", "locked_until"])
        locked_page = self.client.get(reverse("forgot_pin"))
        self.assertContains(locked_page, "your recovery code still works here")
        self.assertContains(locked_page, "Reset PIN</button>")
        for leaked in ("{%", "%}", "{#", "#}"):
            self.assertNotContains(locked_page, leaked)
        response = self.reset()
        self.assertContains(response, "PIN changed")
        self.user.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertTrue(self.user.check_password("5678"))
        self.assertEqual((self.profile.failed_pin_attempts, self.profile.locked_until), (0, None))
        login = self.client.post(reverse("login"), {"username": settings.TEST_STASH, "password": "5678"})
        self.assertRedirects(login, reverse("finance:dashboard"), fetch_redirect_response=False)

    def test_a_wrong_code_during_a_lockout_is_refused_and_leaves_the_lockout_alone(self):
        locked_until = timezone.now() + timedelta(minutes=security.LOCKOUT_MINUTES)
        self.profile.locked_until = locked_until
        self.profile.save(update_fields=["locked_until"])
        response = self.reset(code="WXYZ-WXYZ-WXYZ")
        self.assertContains(response, "That recovery code isn&#x27;t right.")
        self.assertNotContains(response, "PIN changed")
        self.assert_nothing_changed()
        self.assertEqual(Profile.objects.get(pk=self.profile.pk).locked_until, locked_until)

    def test_the_lockout_is_shared_with_the_pin_screen(self):
        for _ in range(security.LOCKOUT_THRESHOLD - 1):
            self.client.post(reverse("login"), {"username": settings.TEST_STASH, "password": "0000"})
        self.reset(code="WXYZ-WXYZ-WXYZ")
        self.profile.refresh_from_db()
        self.assertTrue(security.is_locked_out(self.profile))
        self.assertContains(self.client.get(reverse("forgot_pin")), "Too many wrong attempts")
        self.assertContains(
            self.client.post(reverse("login"), {"username": settings.TEST_STASH, "password": "1234"}),
            "Too many wrong PINs",
        )


class ForgotPinWithoutAProfileTests(StashTestCase):
    def test_a_stash_with_no_profile_goes_back_to_the_pin_screen(self):
        self.assertRedirects(self.client.get(reverse("forgot_pin")), reverse("login"), fetch_redirect_response=False)
        self.assertRedirects(self.client.post(reverse("forgot_pin")), reverse("login"), fetch_redirect_response=False)


class PinScreenLockoutTests(StashTestCase):
    """The PIN screen's own lockout, through real requests and the real middleware: the rule
    Change PIN and a new recovery code copy (plan 03, S7)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(settings.TEST_STASH, password="1234")
        cls.profile = Profile.objects.create(user=cls.user)

    def log_in(self, pin):
        return self.client.post(reverse("login"), {"username": settings.TEST_STASH, "password": pin})

    def test_each_wrong_pin_counts_and_the_last_one_locks_the_stash(self):
        self.log_in("0000")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 1)
        for _ in range(security.LOCKOUT_THRESHOLD - 1):
            self.log_in("0000")
        self.profile.refresh_from_db()
        self.assertTrue(security.is_locked_out(self.profile))

    def test_the_right_pin_logs_in_and_clears_earlier_wrong_tries(self):
        self.log_in("0000")
        response = self.log_in("1234")
        self.assertRedirects(response, reverse("finance:dashboard"), fetch_redirect_response=False)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 0)

    def test_a_locked_stash_refuses_even_the_right_pin(self):
        self.profile.locked_until = timezone.now() + timedelta(minutes=security.LOCKOUT_MINUTES)
        self.profile.save(update_fields=["locked_until"])
        response = self.log_in("1234")
        self.assertContains(response, "Too many wrong PINs")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_wrong_recovery_code_on_forgot_pin_counts_too(self):
        data = {"recovery_code": "ABCD-EFGH-JKLM", "new_pin": "5678", "new_pin_confirm": "5678"}
        self.client.post(reverse("forgot_pin"), data)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 1)

    def test_logging_in_is_what_upgrades_an_old_pin_hash_and_you_stay_logged_in(self):
        """Plan 03, S87: of everything that checks the PIN, only the PIN screen re-saves the
        hash in a newer format, and it ties the new login to the new hash."""
        with override_settings(PASSWORD_HASHERS=REHASHING_PASSWORD_HASHERS):
            self.log_in("1234")
            self.user.refresh_from_db()
            self.assertTrue(self.user.password.startswith("md5_newer$"), self.user.password)
            self.assertEqual(self.client.get(reverse("finance:dashboard")).status_code, 200)

    def test_a_login_lasts_until_the_browser_closes(self):
        """Plan 03, Q4 (the user's call, 14/11/2026): the login cookie carries no expiry date,
        so the browser drops it when it closes. Django's default kept it for two weeks."""
        self.log_in("1234")
        cookie = self.client.cookies[settings.SESSION_COOKIE_NAME]
        self.assertEqual(cookie["max-age"], "")
        self.assertEqual(cookie["expires"], "")


class ClosingTheBrowserTests(StashTestCase):
    """Plan 03, Q4: closing the browser logs you out, but Stashy still remembers which stash you
    were in, so opening it again lands on that stash's PIN screen."""

    databases = {"default", settings.TEST_STASH}

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(settings.TEST_STASH, password="1234")
        Profile.objects.create(user=cls.user, owner_name="Alex")

    def close_the_browser(self):
        """A browser forgets every cookie that has no expiry date; the test client never does."""
        for name, cookie in list(self.client.cookies.items()):
            if not cookie["expires"] and not cookie["max-age"]:
                del self.client.cookies[name]

    def test_after_closing_the_browser_the_stashs_pin_screen_comes_back(self):
        self.client.post(reverse("login"), {"username": settings.TEST_STASH, "password": "1234"})
        self.assertEqual(self.client.get(reverse("finance:dashboard")).status_code, 200)

        self.close_the_browser()

        response = self.client.get(reverse("finance:dashboard"), follow=True)
        self.assertTemplateUsed(response, "registration/login.html")
        self.assertEqual(response.context["stash_name"], settings.TEST_STASH)
        self.assertContains(response, "Welcome back, Alex")

    def test_the_login_goes_but_the_stash_name_stays(self):
        self.client.post(reverse("login"), {"username": settings.TEST_STASH, "password": "1234"})
        self.assertIn(settings.SESSION_COOKIE_NAME, self.client.cookies)
        self.close_the_browser()
        self.assertNotIn(settings.SESSION_COOKIE_NAME, self.client.cookies)
        self.assertEqual(self.client.cookies[registry.LAST_STASH_COOKIE].value, settings.TEST_STASH)


class AdminPasswordChangeTests(StashTestCase):
    """Plan 03, S76: the admin's own change-password page knew nothing about the lockout and
    took any password. Every stash's user is a superuser, so it was open to any login."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(settings.TEST_STASH, email="", password="1234")
        Profile.objects.create(user=cls.user)

    def setUp(self):
        self.client.force_login(self.user)

    def test_it_sends_you_to_change_pin(self):
        for name in ("admin:password_change", "admin:password_change_done"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertRedirects(response, reverse("finance:change_pin"), fetch_redirect_response=False)

    def test_posting_a_new_password_there_changes_nothing(self):
        data = {"old_password": "1234", "new_password1": "a", "new_password2": "a"}
        response = self.client.post(reverse("admin:password_change"), data)
        self.assertRedirects(response, reverse("finance:change_pin"), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("1234"))

    def test_the_admins_own_change_password_page_is_no_longer_what_the_address_serves(self):
        self.assertIs(resolve("/admin/password_change/").func.view_class, RedirectView)
        self.assertIs(resolve("/admin/password_change/done/").func.view_class, RedirectView)

    def test_someone_not_logged_in_is_sent_to_the_pin_screen_first(self):
        for name in ("admin:password_change", "admin:password_change_done"):
            with self.subTest(page=name):
                response = self.client_class().get(reverse(name))
                self.assertRedirects(
                    response, f"{reverse('login')}?next={reverse(name)}", fetch_redirect_response=False
                )

    def test_the_admins_change_password_link_ends_on_change_pin(self):
        admin_home = self.client.get(reverse("admin:index"))
        self.assertContains(admin_home, f'href="{reverse("admin:password_change")}"')
        response = self.client.get(reverse("admin:password_change"), follow=True)
        self.assertTemplateUsed(response, "finance/change_pin.html")

    def test_the_admin_has_no_users_page_to_set_a_password_from(self):
        """The same hole by another door: the admin's Users page would also take any password."""
        with self.assertRaises(NoReverseMatch):
            reverse("admin:auth_user_password_change", args=[self.user.pk])
        for url in ("/admin/auth/user/", f"/admin/auth/user/{self.user.pk}/password/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)


class SwitchStashIsPostOnlyTests(StashTestCase):
    """1.7 (F6): a link or image on another site can't log you out."""

    def test_get_is_refused(self):
        self.assertEqual(self.client.get(reverse("switch_stash")).status_code, 405)

    def test_post_returns_to_the_picker(self):
        self.assertRedirects(
            self.client.post(reverse("switch_stash")), reverse("stash_picker"), fetch_redirect_response=False
        )

    def test_the_pin_screen_offers_it_as_a_form_button(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, f'<form method="post" action="{reverse("switch_stash")}"', html=False)
        self.assertNotContains(response, f'href="{reverse("switch_stash")}"')
        for leaked in ("{#", "#}", "{%", "%}"):
            self.assertNotContains(response, leaked)


class AdminLoginTests(StashTestCase):
    """1.12 (F41): the admin's own login page can't be used to guess a PIN."""

    def test_admin_login_sends_you_to_the_pin_screen_with_next(self):
        response = self.client.get("/admin/login/", {"next": "/admin/"})
        self.assertRedirects(response, f"{reverse('login')}?next=%2Fadmin%2F", fetch_redirect_response=False)

    def test_posting_a_pin_there_logs_nobody_in(self):
        get_user_model().objects.create_superuser("test", password="1234")
        response = self.client.post("/admin/login/", {"username": "test", "password": "1234"})
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_the_admin_itself_still_redirects_to_a_login(self):
        response = self.client.get("/admin/")
        self.assertRedirects(response, "/admin/login/?next=/admin/", fetch_redirect_response=False)


class LoginByDefaultTests(StashTestCase):
    """5.6 (F49): every page needs a login unless its view says otherwise."""

    def test_every_finance_url_sends_an_anonymous_visitor_to_the_login_page(self):
        login_url = reverse("login")
        for name, url in _every_finance_url():
            for method in (self.client.get, self.client.post):
                with self.subTest(name=name, method=method.__name__):
                    response = method(url)
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response.url.startswith(f"{login_url}?next="), response.url)

    def test_the_pages_used_before_logging_in_are_public(self):
        for name in ("login", "logout", "forgot_pin", "stash_picker", "stash_create", "switch_stash", "admin_login"):
            with self.subTest(name=name):
                view = resolve(reverse(name)).func
                self.assertFalse(getattr(view, "login_required", True))

    def test_the_login_page_opens_without_a_login(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)

    def test_logging_out_without_a_session_goes_straight_to_the_login_page(self):
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)

    def test_the_admin_still_ends_on_the_pin_login(self):
        # The admin checks its own login and sends you to /admin/login/, which admin_login
        # forwards to the PIN screen.
        response = self.client.get("/admin/", follow=True)
        final_url = response.redirect_chain[-1][0]
        self.assertTrue(final_url.startswith(reverse("login")), final_url)
        self.assertTemplateUsed(response, "registration/login.html")


class PinRuleTests(SimpleTestCase):
    """6.7 (F35): one PIN rule and one "PINs don't match" check for every form."""

    BAD_PINS = ["123", "12345", "12a4", "١٢٣٤"]

    def test_validate_pin_uses_the_same_rule(self):
        security.validate_pin("0000")
        # The forms strip spaces and line breaks first; validate_pin() itself takes none.
        for bad in [*self.BAD_PINS, "1234\n", "", None]:
            with self.subTest(pin=bad), self.assertRaisesMessage(StashError, "PIN must be exactly 4 digits."):
                security.validate_pin(bad)

    def _form_errors(self, form_class, data):
        form = form_class(data=data)
        with mock.patch.object(registry, "owner_name_taken", return_value=False):
            form.is_valid()
        return form.errors

    def test_every_pin_box_gives_the_one_message(self):
        cases = [
            (StashCreateForm, {"owner_name": "Alex"}, "pin"),
            (ForgotPinForm, {"recovery_code": "ABCD-EFGH-JKLM"}, "new_pin"),
        ]
        for form_class, data, field in cases:
            for bad in self.BAD_PINS:
                with self.subTest(form=form_class.__name__, pin=bad):
                    errors = self._form_errors(form_class, {**data, field: bad, f"{field}_confirm": bad})
                    self.assertEqual(errors[field], ["PIN must be exactly 4 digits."])

    def test_a_mismatched_confirmation_is_reported_on_the_second_box(self):
        cases = [
            (StashCreateForm, {"owner_name": "Alex", "pin": "1234", "pin_confirm": "4321"}, "pin_confirm"),
            (ForgotPinForm, {"recovery_code": "X", "new_pin": "1234", "new_pin_confirm": "4321"}, "new_pin_confirm"),
        ]
        for form_class, data, field in cases:
            with self.subTest(form=form_class.__name__):
                self.assertEqual(self._form_errors(form_class, data)[field], ["PINs don't match."])


class CurrentPinFormRuleTests(StashTestCase):
    """The same rules for the forms that ask for the PIN again. They check it against the
    stash's saved Profile (security.check_pin re-reads its lockout), so they need a database."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(settings.TEST_STASH, password="1111")
        cls.profile = Profile.objects.create(user=cls.user)

    def test_the_change_pin_and_recovery_code_boxes_give_the_one_message_too(self):
        for bad in PinRuleTests.BAD_PINS:
            with self.subTest(pin=bad):
                current = CurrentPinForm(data={"current_pin": bad}, profile=self.profile)
                self.assertEqual(current.errors["current_pin"], ["PIN must be exactly 4 digits."])
                new = ChangePinForm(
                    data={"current_pin": "1111", "new_pin": bad, "new_pin_confirm": bad},
                    profile=self.profile,
                )
                self.assertEqual(new.errors["new_pin"], ["PIN must be exactly 4 digits."])

    def test_change_pin_form_checks_the_current_pin_and_the_confirmation(self):
        form = ChangePinForm(
            data={"current_pin": "1111", "new_pin": "1234", "new_pin_confirm": "4321"},
            profile=self.profile,
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {"new_pin_confirm": ["PINs don't match."]})
