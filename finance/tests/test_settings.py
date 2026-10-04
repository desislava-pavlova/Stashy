"""The Settings page: its layout, its separate form endpoints (exchange rates, Watched
expenses), and the Change PIN and recovery code pages."""

from unittest import mock

from django.urls import resolve, reverse

from ..currency import RateFetchError
from ..models import Category, Profile
from ..stashes import security
from .base import AlexsStashTestCase, LoggedInWithAccountTestCase


class SettingsEndpointsTests(LoggedInWithAccountTestCase):
    """5.3 (F16): each form on Settings posts to its own URL."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.profile = Profile.for_user(cls.user)

    def test_each_form_on_the_page_posts_to_its_own_url(self):
        page = self.client.get(reverse("finance:settings")).content.decode()
        for name in ("settings", "check_rates", "save_watched_expenses", "create_backup"):
            with self.subTest(name=name):
                self.assertIn(f'<form method="post" action="{reverse(f"finance:{name}")}"', page)
        for old_button in ('name="check_rates"', 'name="backup_now"', 'name="save_watched_expenses"'):
            self.assertNotIn(old_button, page)

    def test_the_new_endpoints_take_only_post(self):
        for name in ("check_rates", "create_backup", "save_watched_expenses"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(f"finance:{name}")).status_code, 405)

    def test_checking_rates_and_backing_up_run_outside_the_request_transaction(self):
        for name in ("check_rates", "create_backup", "backup_restore"):
            with self.subTest(name=name):
                self.assertTrue(getattr(resolve(reverse(f"finance:{name}")).func, "_non_atomic_requests", None))
        self.assertFalse(getattr(resolve(reverse("finance:settings")).func, "_non_atomic_requests", None))

    def test_check_rates_reports_what_it_stored_and_returns_to_settings(self):
        with mock.patch("finance.views.settings.fetch_latest_rates", return_value=(["USD"], ["XYZ"])):
            response = self.client.post(reverse("finance:check_rates"), follow=True)
        self.assertRedirects(response, reverse("finance:settings"))
        self.assertContains(response, "Updated 1 exchange rate.")
        self.assertContains(response, "No rate available for: XYZ")

    def test_check_rates_offline_shows_the_error(self):
        with mock.patch("finance.views.settings.fetch_latest_rates", side_effect=RateFetchError("No internet")):
            response = self.client.post(reverse("finance:check_rates"), follow=True)
        self.assertContains(response, "No internet")

    def test_saving_preferences_still_posts_to_the_page_itself(self):
        response = self.client.post(
            reverse("finance:settings"),
            {
                "main_currency": "USD",
                "time_zone": "Europe/Sofia",
                "date_format": self.profile.date_format,
                "number_format": self.profile.number_format,
                "first_day_of_week": self.profile.first_day_of_week,
            },
        )
        self.assertRedirects(response, reverse("finance:settings"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.main_currency, "USD")

    def test_invalid_watched_expenses_show_their_errors_on_the_settings_page(self):
        salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        response = self.client.post(
            reverse("finance:save_watched_expenses"),
            {"watched_expenses_title": "Bills", "watched_expenses_categories": [salary.pk]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "finance/settings.html")
        self.assertContains(response, "Watched expenses weren&#x27;t saved")
        self.assertContains(response, "Select a valid choice")
        self.assertFalse(self.profile.watched_expenses_categories.exists())


class SettingsLayoutTests(AlexsStashTestCase):
    def test_shows_the_owner_possessive_under_the_heading(self):
        response = self.client.get(reverse("finance:settings"))
        self.assertContains(response, "You're in Alex&#x27;s stash.")

    def test_no_login_card_or_switch_stash_link(self):
        response = self.client.get(reverse("finance:settings"))
        self.assertNotContains(response, "Switch stash")
        self.assertNotContains(response, "Choose a different stash")

    def test_no_bullet_list_under_the_preferences_form(self):
        response = self.client.get(reverse("finance:settings"))
        self.assertNotContains(response, "guessed from this computer")

    def test_pin_and_recovery_code_card_links_to_their_own_pages(self):
        response = self.client.get(reverse("finance:settings"))
        self.assertContains(response, "PIN &amp; recovery code")
        self.assertContains(response, reverse("finance:change_pin"))
        self.assertContains(response, reverse("finance:recovery_code"))
        self.assertNotContains(response, 'name="change_pin"')
        self.assertNotContains(response, 'name="regenerate_recovery_code"')


class ChangePinPageTests(AlexsStashTestCase):
    def test_get_shows_the_form_with_a_back_link(self):
        response = self.client.get(reverse("finance:change_pin"))
        self.assertContains(response, "Current PIN")
        self.assertContains(response, reverse("finance:settings"))

    def test_correct_current_pin_changes_it_and_returns_to_settings(self):
        self.user.set_password("1234")
        self.user.save()
        self.client.force_login(self.user)  # the old session's auth hash died with the password
        response = self.client.post(
            reverse("finance:change_pin"),
            {
                "current_pin": "1234",
                "new_pin": "5678",
                "new_pin_confirm": "5678",
            },
        )
        self.assertRedirects(response, reverse("finance:settings"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("5678"))

    def test_wrong_current_pin_is_rejected(self):
        self.user.set_password("1234")
        self.user.save()
        self.client.force_login(self.user)  # the old session's auth hash died with the password
        response = self.client.post(
            reverse("finance:change_pin"),
            {
                "current_pin": "0000",
                "new_pin": "5678",
                "new_pin_confirm": "5678",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("1234"))


class RecoveryCodePageTests(AlexsStashTestCase):
    def test_get_shows_the_warning_with_generate_and_back_but_no_code(self):
        response = self.client.get(reverse("finance:recovery_code"))
        self.assertContains(response, "This replaces your recovery code")
        self.assertContains(response, "Generate")
        self.assertNotContains(response, "Write this down now")

    def test_generate_replaces_the_hash_and_reloads_with_the_code_shown(self):
        old_hash = self.profile.recovery_code_hash
        response = self.client.post(reverse("finance:recovery_code"), follow=True)
        self.profile.refresh_from_db()
        self.assertNotEqual(self.profile.recovery_code_hash, old_hash)
        self.assertContains(response, "Write this down now")
        self.assertNotContains(response, "This replaces your recovery code")

    def test_the_code_is_not_in_the_messages_banner(self):
        response = self.client.post(reverse("finance:recovery_code"), follow=True)
        self.assertEqual(list(response.context["messages"]), [])

    def test_refreshing_afterwards_loses_the_code_and_no_generate_button_reappears_yet(self):
        self.client.post(reverse("finance:recovery_code"))
        first = self.client.get(reverse("finance:recovery_code"))
        self.assertContains(first, "Write this down now")
        second = self.client.get(reverse("finance:recovery_code"))
        self.assertNotContains(second, "Write this down now")
        self.assertContains(second, "This replaces your recovery code")

    def test_old_code_stops_working_after_generating_a_new_one(self):
        known_code = security.regenerate_recovery_code(self.profile)
        self.client.post(reverse("finance:recovery_code"))
        self.profile.refresh_from_db()
        ok, _ = security.reset_pin_with_recovery_code(self.profile, known_code, "1234")
        self.assertFalse(ok)


class RecoveryCodeNotInCookieTests(LoggedInWithAccountTestCase):
    """1.14 (F45): a new recovery code never travels in the session cookie."""

    def test_the_code_is_shown_once_and_never_stored_in_the_session(self):
        Profile.for_user(self.user)
        with mock.patch.object(security, "regenerate_recovery_code", return_value="ABCD-EFGH-JKLM"):
            self.client.post(reverse("finance:recovery_code"))
        self.assertNotIn("ABCD-EFGH-JKLM", str(dict(self.client.session)))

        first = self.client.get(reverse("finance:recovery_code"))
        second = self.client.get(reverse("finance:recovery_code"))

        self.assertContains(first, "ABCD-EFGH-JKLM")
        self.assertNotContains(second, "ABCD-EFGH-JKLM")
