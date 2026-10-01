"""Tests for the multi-stash login (Phase 12): PIN lockout math, the recovery-code reset
flow, PIN validation, and the require-a-stash middleware. All of these run against the normal
test database and need nothing special. (Phase 15 renamed the old validate_stash_name/NAME_RE
- typed folder names - out of existence: a stash's folder name is generated from its owner
name now, never typed by anyone.)

create_stash() and reset_pin_directly() are deliberately NOT covered here: both register a
real, dynamically-created extra database alias to build/edit a stash's schema outside of
`default`, and Django's test framework blocks connections to any alias that isn't declared in
settings.DATABASES up front - even `databases = "__all__"` only covers aliases Django already
knows about, not ones added at runtime. There's no supported way around that from inside
TestCase, so these two were instead verified directly (not under the test runner) against a
real temporary directory - see plans/01_upgrade_plan.md's Phase 12 notes (and Phase 15's, for the
owner-name-aware versions) for what was checked."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import db_router, stashes
from .middleware import RequireStashMiddleware, StashSelectionMiddleware
from .models import Profile


class StashPathHelperTests(SimpleTestCase):
    def test_validate_pin_requires_exactly_four_digits(self):
        for bad in ("123", "12345", "abcd", "", None):
            with self.assertRaises(stashes.StashError):
                stashes.validate_pin(bad)
        stashes.validate_pin("0000")  # does not raise

    def test_recovery_code_format(self):
        code = stashes._generate_recovery_code()
        parts = code.split("-")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(len(p) == 4 for p in parts))
        # No ambiguous characters (0/O/1/I) that are hard to tell apart handwritten.
        self.assertFalse(set(code) & set("01OI-") - {"-"})


class LockoutTests(TestCase):
    """These operate on the *current* (test/default) database's own Profile, exactly the way
    the login view does - no temp stash needed, since lockout state lives on Profile."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="1234")
        self.profile = Profile.objects.create(user=self.user)

    def test_not_locked_out_initially(self):
        self.assertFalse(stashes.is_locked_out(self.profile))

    def test_locks_out_after_the_threshold(self):
        for _ in range(stashes.LOCKOUT_THRESHOLD):
            stashes.register_failed_attempt(self.profile)
        self.profile.refresh_from_db()
        self.assertTrue(stashes.is_locked_out(self.profile))
        self.assertEqual(self.profile.failed_pin_attempts, 0)  # reset when the lock kicks in

    def test_a_failed_attempt_while_already_locked_does_not_extend_it(self):
        self.profile.locked_until = timezone.now() + timedelta(minutes=5)
        self.profile.save()
        original = self.profile.locked_until
        stashes.register_failed_attempt(self.profile)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.locked_until, original)

    def test_successful_login_clears_a_partial_failure_count(self):
        stashes.register_failed_attempt(self.profile)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 1)
        stashes.register_successful_login(self.profile)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 0)


class RecoveryCodeResetTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="1234")
        self.recovery_code = "ABCD-EFGH-JKLM"
        self.profile = Profile.objects.create(user=self.user, recovery_code_hash=stashes._hash(self.recovery_code))

    def test_correct_code_resets_the_pin_and_issues_a_new_code(self):
        ok, new_code = stashes.reset_pin_with_recovery_code(self.profile, self.recovery_code, "9999")
        self.assertTrue(ok)
        self.assertNotEqual(new_code, self.recovery_code)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("9999"))

    def test_old_recovery_code_stops_working_after_a_reset(self):
        stashes.reset_pin_with_recovery_code(self.profile, self.recovery_code, "9999")
        self.profile.refresh_from_db()
        ok, _ = stashes.reset_pin_with_recovery_code(self.profile, self.recovery_code, "1111")
        self.assertFalse(ok)

    def test_wrong_code_counts_as_a_failed_attempt(self):
        ok, new_code = stashes.reset_pin_with_recovery_code(self.profile, "WRONG-CODE-HERE", "9999")
        self.assertFalse(ok)
        self.assertIsNone(new_code)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 1)


class StashSelectionMiddlewareTests(SimpleTestCase):
    """The router thread-local this middleware sets is what lets a session's own stash
    resolve without a restart (see finance/db_router.py) - these check the plumbing around
    that (session absent, session naming a stash that doesn't exist on disk), not a real
    stash's own database, which hits the same dynamic-alias limitation documented above."""

    def setUp(self):
        self.factory = RequestFactory()
        self.addCleanup(db_router.clear_current_stash_alias)

    @override_settings(TESTING=False)
    def test_no_stash_in_session_leaves_the_alias_unset(self):
        seen = {}
        middleware = StashSelectionMiddleware(lambda r: seen.setdefault("alias", db_router.current_stash_alias()))
        request = self.factory.get("/")
        request.session = {}
        middleware(request)
        self.assertIsNone(seen["alias"])

    @override_settings(TESTING=False)
    def test_a_session_naming_a_stash_that_no_longer_exists_is_treated_as_none_chosen(self):
        seen = {}
        middleware = StashSelectionMiddleware(lambda r: seen.setdefault("alias", db_router.current_stash_alias()))
        request = self.factory.get("/")
        request.session = {"stash": "does-not-exist-on-disk"}
        middleware(request)
        self.assertIsNone(seen["alias"])
        self.assertNotIn("stash", request.session)

    @override_settings(TESTING=False)
    def test_the_alias_is_cleared_again_after_the_response(self):
        middleware = StashSelectionMiddleware(lambda r: "ok")
        request = self.factory.get("/")
        request.session = {}
        middleware(request)
        self.assertIsNone(db_router.current_stash_alias())


class RequireStashMiddlewareTests(SimpleTestCase):
    """Multi-stash login was rewritten 2026-09-15 so switching needs no restart: which stash
    is current now lives in request.session, not settings.CURRENT_STASH off a file - these
    tests attach a plain dict as the session, since the middleware only ever calls .get() on
    it."""

    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(TESTING=False)
    def test_redirects_to_the_picker_when_no_stash_is_current(self):
        middleware = RequireStashMiddleware(get_response=lambda r: "should not be reached")
        request = self.factory.get("/transactions/")
        request.session = {}
        response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("stash_picker"))

    @override_settings(TESTING=False)
    def test_the_pickers_own_urls_are_exempt(self):
        middleware = RequireStashMiddleware(get_response=lambda r: "ok")
        request = self.factory.get("/stashes/")
        request.session = {}
        response = middleware(request)
        self.assertEqual(response, "ok")

    @override_settings(TESTING=False)
    def test_passes_through_once_a_stash_is_current(self):
        middleware = RequireStashMiddleware(get_response=lambda r: "ok")
        request = self.factory.get("/transactions/")
        request.session = {"stash": "pavlo"}
        response = middleware(request)
        self.assertEqual(response, "ok")
