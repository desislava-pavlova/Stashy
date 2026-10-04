"""Getting into a stash: the PIN rule, the lockout, recovery codes, a login for every page,
the admin's login, and switching stashes."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from django.urls import URLPattern, resolve, reverse
from django.utils import timezone

from .. import urls as finance_urls
from ..auth_forms import ChangePinForm, ForgotPinForm, StashCreateForm
from ..models import Profile
from ..stashes import StashError, registry, security
from .base import StashTestCase

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
        security.register_successful_login(self.profile)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 0)


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

    def test_wrong_code_counts_as_a_failed_attempt(self):
        ok, new_code = security.reset_pin_with_recovery_code(self.profile, "WRONG-CODE-HERE", "9999")
        self.assertFalse(ok)
        self.assertIsNone(new_code)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 1)


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

    def test_change_pin_form_checks_the_current_pin_and_the_confirmation(self):
        user = get_user_model()(username="alex")
        user.set_password("1111")
        form = ChangePinForm(
            data={"current_pin": "1111", "new_pin": "1234", "new_pin_confirm": "4321"},
            user=user,
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {"new_pin_confirm": ["PINs don't match."]})
