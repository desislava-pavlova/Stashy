"""The Settings page: its layout, its separate form endpoints (exchange rates, Watched
expenses), and the Change PIN and recovery code pages."""

from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.test import override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from audit.models import AuditLogEntry

from ..currency import RateFetchError
from ..forms import ProfileForm, WatchedExpensesForm
from ..models import Category, Profile
from ..stashes import security
from .base import (
    LEAKED_TEMPLATE_SYNTAX,
    REHASHING_PASSWORD_HASHERS,
    AlexsStashTestCase,
    LoggedInWithAccountTestCase,
)


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

    def save_preferences(self, time_zone):
        """Posts the preferences form with the currency switched to USD and `time_zone`."""
        return self.client.post(
            reverse("finance:settings"),
            {
                "main_currency": "USD",
                "time_zone": time_zone,
                "date_format": self.profile.date_format,
                "number_format": self.profile.number_format,
                "first_day_of_week": self.profile.first_day_of_week,
            },
        )

    def test_saving_preferences_still_posts_to_the_page_itself(self):
        response = self.save_preferences("Europe/Sofia")
        self.assertRedirects(response, reverse("finance:settings"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.main_currency, "USD")

    def test_a_time_zone_that_is_not_in_the_list_is_refused(self):
        """Plan 03, S54: the dropdown alone didn't stop a hand-made request."""
        before = (self.profile.time_zone, self.profile.main_currency)
        response = self.save_preferences("Mars/Olympus_Mons")
        self.assertEqual(response.status_code, 200)
        self.assertIn("time_zone", response.context["form"].errors)
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.time_zone, self.profile.main_currency), before)

    def test_a_blank_time_zone_is_refused(self):
        before = (self.profile.time_zone, self.profile.main_currency)
        response = self.save_preferences("")
        self.assertEqual(response.context["form"].errors["time_zone"], ["This field is required."])
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.time_zone, self.profile.main_currency), before)

    def test_utc_can_be_chosen(self):
        """The list comes from the time zone database's zone.tab, which has no plain "UTC":
        regional.time_zone_choices() adds it, and since S54 the list decides what's saved."""
        response = self.save_preferences("UTC")
        self.assertRedirects(response, reverse("finance:settings"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.time_zone, "UTC")

    def test_a_zone_saved_under_an_older_name_is_shown_and_kept(self):
        """Plan 03, S77: Europe/Kiev isn't in the list (it's Europe/Kyiv now), but a stash saved
        with it keeps it. Before, the dropdown showed Africa/Abidjan and a save stored that."""
        self.profile.time_zone = "Europe/Kiev"
        self.profile.save(update_fields=["time_zone"])
        page = self.client.get(reverse("finance:settings"))
        self.assertContains(page, '<option value="Europe/Kiev" selected>Europe/Kiev</option>', html=True)
        self.assertRedirects(self.save_preferences("Europe/Kiev"), reverse("finance:settings"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.time_zone, "Europe/Kiev")

    def test_every_kind_of_older_zone_name_is_shown_and_kept(self):
        for zone in ("Etc/UTC", "Asia/Calcutta", "Europe/Kiev"):
            with self.subTest(zone=zone):
                self.profile.time_zone = zone
                self.profile.save(update_fields=["time_zone"])
                page = self.client.get(reverse("finance:settings"))
                self.assertContains(page, f'<option value="{zone}" selected>{zone}</option>', html=True)
                self.assertRedirects(self.save_preferences(zone), reverse("finance:settings"))
                self.profile.refresh_from_db()
                self.assertEqual(self.profile.time_zone, zone)

    def test_a_zone_that_does_not_exist_is_not_offered_and_the_pages_still_open(self):
        """A hand-edited stash: the pages must not break, and picking a real zone fixes it."""
        self.profile.time_zone = "Mars/Olympus_Mons"
        self.profile.save(update_fields=["time_zone"])
        for name in ("finance:dashboard", "finance:settings"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'value="Mars/Olympus_Mons"')
        self.assertRedirects(self.save_preferences("Europe/Sofia"), reverse("finance:settings"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.time_zone, "Europe/Sofia")

    def test_an_older_zone_name_is_refused_when_it_isnt_the_stashs_own(self):
        before = self.profile.time_zone
        self.assertEqual(self.save_preferences("Europe/Kiev").status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.time_zone, before)

    def test_saving_settings_never_puts_back_an_old_lockout_or_recovery_code(self):
        """Plan 03, S75: the page edits request.stashy_profile, read before the request's
        write lock. Saving the whole row would put back what that copy held."""
        old_copies = [Profile.objects.get(pk=self.profile.pk) for _ in range(2)]
        self.profile.locked_until = timezone.now() + timedelta(minutes=security.LOCKOUT_MINUTES)
        self.profile.recovery_code_hash = "today's hash"
        self.profile.save()
        preferences = {
            "main_currency": "USD",
            "time_zone": self.profile.time_zone,
            "date_format": self.profile.date_format,
            "number_format": self.profile.number_format,
            "first_day_of_week": self.profile.first_day_of_week,
        }
        forms = [
            ProfileForm(data=preferences, instance=old_copies[0]),
            WatchedExpensesForm(data={"watched_expenses_title": "Bills"}, instance=old_copies[1]),
        ]
        for form in forms:
            with self.subTest(form=type(form).__name__):
                self.assertTrue(form.is_valid(), form.errors)
                form.save()
                self.profile.refresh_from_db()
                self.assertTrue(security.is_locked_out(self.profile))
                self.assertEqual(self.profile.recovery_code_hash, "today's hash")
        self.assertEqual((self.profile.main_currency, self.profile.watched_expenses_title), ("USD", "Bills"))

    def test_the_settings_page_does_not_undo_a_lockout_or_a_new_code_it_has_not_seen(self):
        """Plan 03, S75, through the real pages: the page's own copy of the Profile is read
        before the request takes the stash's write lock, so it can be older than the saved row."""
        posts = {
            "settings": lambda: self.save_preferences("Europe/Sofia"),
            "watched": lambda: self.client.post(
                reverse("finance:save_watched_expenses"), {"watched_expenses_title": "Bills"}
            ),
        }
        for name, post in posts.items():
            with self.subTest(form=name):
                Profile.objects.filter(pk=self.profile.pk).update(
                    failed_pin_attempts=0, locked_until=None, recovery_code_hash="old hash"
                )
                old_copy = Profile.objects.get(pk=self.profile.pk)
                locked_until = timezone.now() + timedelta(minutes=security.LOCKOUT_MINUTES)
                Profile.objects.filter(pk=self.profile.pk).update(
                    failed_pin_attempts=3, locked_until=locked_until, recovery_code_hash="today's hash"
                )
                with mock.patch.object(Profile, "for_user", return_value=old_copy):
                    self.assertEqual(post().status_code, 302)
                saved = Profile.objects.get(pk=self.profile.pk)
                self.assertEqual(
                    (saved.failed_pin_attempts, saved.locked_until, saved.recovery_code_hash),
                    (3, locked_until, "today's hash"),
                )

    def test_a_save_from_an_old_copy_writes_only_the_forms_own_columns_in_the_audit_log(self):
        """Saving the whole row from an old copy would also log "changes" to the lockout."""
        old_copy = Profile.objects.get(pk=self.profile.pk)
        Profile.objects.filter(pk=self.profile.pk).update(failed_pin_attempts=3, welcome_step=2)
        preferences = {
            "main_currency": "USD",
            "time_zone": old_copy.time_zone,
            "date_format": old_copy.date_format,
            "number_format": old_copy.number_format,
            "first_day_of_week": old_copy.first_day_of_week,
        }
        form = ProfileForm(data=preferences, instance=old_copy)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        changes = AuditLogEntry.objects.filter(table="finance_profile", action=AuditLogEntry.Action.UPDATE)
        self.assertEqual(set(changes.values_list("column", flat=True)), {"main_currency"})
        self.assertEqual(Profile.objects.get(pk=self.profile.pk).welcome_step, 2)

    def test_a_form_saved_without_committing_writes_nothing_until_the_caller_does(self):
        category = Category.objects.create(name="Water", category_type=Category.CategoryType.EXPENSE)
        form = WatchedExpensesForm(
            data={"watched_expenses_title": "Water", "watched_expenses_categories": [category.pk]},
            instance=Profile.objects.get(pk=self.profile.pk),
        )
        self.assertTrue(form.is_valid(), form.errors)
        profile = form.save(commit=False)
        self.assertEqual(Profile.objects.get(pk=self.profile.pk).watched_expenses_title, "Bills & Utilities")
        profile.save()
        form.save_m2m()
        self.assertEqual(list(profile.watched_expenses_categories.all()), [category])

    def test_saving_watched_expenses_replaces_and_clears_the_chosen_categories(self):
        """The partial save still saves the categories (a many-to-many), and not only adds."""
        expense = Category.CategoryType.EXPENSE
        water = Category.objects.create(name="Water", category_type=expense)
        power = Category.objects.create(name="Power", category_type=expense)
        url = reverse("finance:save_watched_expenses")
        steps = [([water.pk, power.pk], [water, power]), ([power.pk], [power]), ([], [])]
        for chosen, expected in steps:
            with self.subTest(chosen=chosen):
                response = self.client.post(
                    url, {"watched_expenses_title": "Bills", "watched_expenses_categories": chosen}
                )
                self.assertRedirects(response, reverse("finance:settings"))
                self.assertCountEqual(self.profile.watched_expenses_categories.all(), expected)

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


class PinPageTestCase(AlexsStashTestCase):
    """Alex's stash with a real 4-digit PIN, which the Change PIN and recovery code pages ask
    for again (plan 03, S7)."""

    PIN = "1234"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user.set_password(cls.PIN)
        cls.user.save()

    def assert_locked_out_and_logged_out(self, response):
        """Out to the PIN screen, which says the stash is locked. The session still names the
        stash, so the PIN screen knows which one."""
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.session["stash"], settings.TEST_STASH)
        self.assertContains(self.client.get(reverse("login")), "Too many wrong PINs")

    def assert_the_last_wrong_pin_locks_out(self, url, data):
        for _ in range(security.LOCKOUT_THRESHOLD - 1):
            self.assertEqual(self.client.post(url, data).status_code, 200)
        self.assert_locked_out_and_logged_out(self.client.post(url, data))

    def lock_the_stash(self):
        self.profile.locked_until = timezone.now() + timedelta(minutes=security.LOCKOUT_MINUTES)
        self.profile.save(update_fields=["locked_until"])


class ChangePinPageTests(PinPageTestCase):
    WRONG = {"current_pin": "0000", "new_pin": "5678", "new_pin_confirm": "5678"}

    def test_get_shows_the_form_with_a_back_link(self):
        response = self.client.get(reverse("finance:change_pin"))
        self.assertContains(response, "Current PIN")
        self.assertContains(response, reverse("finance:settings"))

    def test_correct_current_pin_changes_it_and_returns_to_settings(self):
        response = self.client.post(
            reverse("finance:change_pin"),
            {
                "current_pin": self.PIN,
                "new_pin": "5678",
                "new_pin_confirm": "5678",
            },
        )
        self.assertRedirects(response, reverse("finance:settings"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("5678"))

    def test_wrong_current_pin_is_rejected_and_counts_toward_the_lockout(self):
        response = self.client.post(reverse("finance:change_pin"), self.WRONG)
        self.assertContains(response, "That&#x27;s not your current PIN.")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.PIN))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 1)

    def test_the_last_wrong_pin_logs_out_to_the_locked_pin_screen(self):
        self.assert_the_last_wrong_pin_locks_out(reverse("finance:change_pin"), self.WRONG)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.PIN))

    def test_a_locked_stash_logs_out_even_with_the_right_pin(self):
        self.lock_the_stash()
        response = self.client.post(
            reverse("finance:change_pin"),
            {"current_pin": self.PIN, "new_pin": "5678", "new_pin_confirm": "5678"},
        )
        self.assert_locked_out_and_logged_out(response)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.PIN))

    def test_after_the_lockout_the_pin_screen_refuses_even_the_right_pin(self):
        """Otherwise logging straight back in would undo the lockout."""
        self.assert_the_last_wrong_pin_locks_out(reverse("finance:change_pin"), self.WRONG)
        response = self.client.post(reverse("login"), {"username": settings.TEST_STASH, "password": self.PIN})
        self.assertContains(response, "Too many wrong PINs")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_the_right_pin_with_a_mistyped_new_pin_changes_nothing_but_clears_earlier_tries(self):
        """The right current PIN shows it's the owner, so the earlier wrong tries are forgotten
        (as after logging in), even though the new PIN has to be typed again."""
        self.profile.failed_pin_attempts = security.LOCKOUT_THRESHOLD - 1
        self.profile.save(update_fields=["failed_pin_attempts"])
        response = self.client.post(
            reverse("finance:change_pin"),
            {"current_pin": self.PIN, "new_pin": "5678", "new_pin_confirm": "8765"},
        )
        self.assertContains(response, "PINs don&#x27;t match.")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.PIN))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 0)

    def test_a_current_pin_that_is_not_four_digits_gets_the_pin_rule_and_is_not_counted(self):
        """It can't be the right PIN, and it's never checked against it, so it isn't a guess."""
        response = self.client.post(reverse("finance:change_pin"), {**self.WRONG, "current_pin": "12a4"})
        self.assertContains(response, "PIN must be exactly 4 digits.")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 0)


class RecoveryCodePageTests(PinPageTestCase):
    def generate(self, pin=None, **kwargs):
        data = {"current_pin": self.PIN if pin is None else pin}
        return self.client.post(reverse("finance:recovery_code"), data, **kwargs)

    def test_get_shows_the_warning_and_a_pin_box_with_generate_and_back_but_no_code(self):
        response = self.client.get(reverse("finance:recovery_code"))
        self.assertContains(response, "This replaces your recovery code")
        self.assertContains(response, 'name="current_pin"')
        self.assertContains(response, "Generate")
        self.assertNotContains(response, "Write this down now")

    def test_generate_replaces_the_hash_and_reloads_with_the_code_shown(self):
        old_hash = self.profile.recovery_code_hash
        response = self.generate(follow=True)
        self.profile.refresh_from_db()
        self.assertNotEqual(self.profile.recovery_code_hash, old_hash)
        self.assertContains(response, "Write this down now")

    def test_after_a_django_update_checking_the_pin_still_keeps_you_logged_in_for_the_code(self):
        """Django would re-save the stored hash in a newer format when the PIN is checked. The
        login is tied to that hash, so the next page logged you out, and the new code (already
        replacing the old one) was never shown. Plan 03, S87: the PIN checks never save it."""
        with override_settings(PASSWORD_HASHERS=REHASHING_PASSWORD_HASHERS):
            response = self.generate(follow=True)
        self.assertContains(response, "Write this down now")
        self.assertNotContains(response, "This replaces your recovery code")

    def test_the_code_is_not_in_the_messages_banner(self):
        response = self.generate(follow=True)
        self.assertEqual(list(response.context["messages"]), [])

    def test_refreshing_afterwards_loses_the_code_and_no_generate_button_reappears_yet(self):
        self.generate()
        first = self.client.get(reverse("finance:recovery_code"))
        self.assertContains(first, "Write this down now")
        second = self.client.get(reverse("finance:recovery_code"))
        self.assertNotContains(second, "Write this down now")
        self.assertContains(second, "This replaces your recovery code")

    def test_old_code_stops_working_after_generating_a_new_one(self):
        known_code = security.regenerate_recovery_code(self.profile)
        self.generate()
        self.profile.refresh_from_db()
        ok, _ = security.reset_pin_with_recovery_code(self.profile, known_code, "1234")
        self.assertFalse(ok)

    def test_a_wrong_pin_makes_no_new_code_and_counts_toward_the_lockout(self):
        old_hash = self.profile.recovery_code_hash
        response = self.generate(pin="0000")
        self.assertContains(response, "That&#x27;s not your current PIN.")
        self.assertNotContains(response, "Write this down now")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.recovery_code_hash, old_hash)
        self.assertEqual(self.profile.failed_pin_attempts, 1)

    def test_no_pin_makes_no_new_code(self):
        old_hash = self.profile.recovery_code_hash
        response = self.client.post(reverse("finance:recovery_code"))
        self.assertContains(response, "This field is required.")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.recovery_code_hash, old_hash)

    def test_the_last_wrong_pin_logs_out_to_the_locked_pin_screen(self):
        old_hash = self.profile.recovery_code_hash
        self.assert_the_last_wrong_pin_locks_out(reverse("finance:recovery_code"), {"current_pin": "0000"})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.recovery_code_hash, old_hash)

    def test_a_locked_stash_logs_out_even_with_the_right_pin(self):
        old_hash = self.profile.recovery_code_hash
        self.lock_the_stash()
        self.assert_locked_out_and_logged_out(self.generate())
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.recovery_code_hash, old_hash)

    def test_the_right_pin_one_try_before_the_lockout_still_works_and_clears_the_tries(self):
        for _ in range(security.LOCKOUT_THRESHOLD - 1):
            self.generate(pin="0000")
        self.assertContains(self.generate(follow=True), "Write this down now")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 0)

    def test_the_pin_box_has_its_label_and_the_form_its_csrf_token(self):
        response = self.client.get(reverse("finance:recovery_code"))
        self.assertContains(response, '<label class="form-label" for="id_current_pin">Current PIN</label>', html=True)
        self.assertContains(response, 'id="id_current_pin"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_a_wrong_pin_shows_its_error_under_the_pin_box(self):
        page = self.generate(pin="0000").content.decode()
        pin_box = page.index('name="current_pin"')
        error = page.index("That&#x27;s not your current PIN.")
        generate_button = page.index(">Generate</button>")
        self.assertLess(pin_box, error)
        self.assertLess(error, generate_button)

    def test_no_state_of_the_page_leaks_template_syntax(self):
        """The every-page check in test_pages.py only sees the empty form; the page has two
        more states."""
        url = reverse("finance:recovery_code")
        states = {"PIN box": (self.client.get(url), "This replaces your recovery code")}
        states["wrong PIN"] = (self.generate(pin="0000"), "That&#x27;s not your current PIN.")
        self.generate()
        states["new code"] = (self.client.get(url), "Write this down now")
        for state, (response, marker) in states.items():
            with self.subTest(state=state):
                self.assertContains(response, marker)
                body = response.content.decode()
                for syntax in LEAKED_TEMPLATE_SYNTAX:
                    self.assertNotIn(syntax, body)


class SharedLockoutTests(PinPageTestCase):
    """Change PIN and the recovery code page count toward one lockout, so spreading the
    guesses over both pages gives no extra tries."""

    def test_wrong_pins_on_both_pages_add_up_to_one_lockout(self):
        pages = [reverse("finance:change_pin"), reverse("finance:recovery_code")]
        for attempt in range(security.LOCKOUT_THRESHOLD - 1):
            self.assertEqual(self.client.post(pages[attempt % 2], ChangePinPageTests.WRONG).status_code, 200)
        self.assert_locked_out_and_logged_out(self.client.post(pages[1], ChangePinPageTests.WRONG))

    def test_a_request_with_an_old_copy_of_the_profile_still_counts_toward_the_lockout(self):
        """Plan 03, S75, through the real pages: the saved row is one try short of the
        lockout, but the request's own copy (read before the stash's write lock) says no
        tries yet. The try must still count, or it goes missing and the lockout never comes."""
        pages = {
            "Change PIN": (reverse("finance:change_pin"), ChangePinPageTests.WRONG),
            "recovery code": (reverse("finance:recovery_code"), {"current_pin": "0000"}),
        }
        for name, (url, data) in pages.items():
            with self.subTest(page=name):
                self.client.force_login(self.user)
                Profile.objects.filter(pk=self.profile.pk).update(failed_pin_attempts=0, locked_until=None)
                old_copy = Profile.objects.get(pk=self.profile.pk)
                Profile.objects.filter(pk=self.profile.pk).update(failed_pin_attempts=security.LOCKOUT_THRESHOLD - 1)
                with mock.patch.object(Profile, "for_user", return_value=old_copy):
                    response = self.client.post(url, data)
                self.assert_locked_out_and_logged_out(response)


class RecoveryCodeNotInCookieTests(PinPageTestCase):
    """1.14 (F45): a new recovery code never travels in the session cookie."""

    def test_the_code_is_shown_once_and_never_stored_in_the_session(self):
        with mock.patch.object(security, "regenerate_recovery_code", return_value="ABCD-EFGH-JKLM"):
            self.client.post(reverse("finance:recovery_code"), {"current_pin": self.PIN})
        self.assertNotIn("ABCD-EFGH-JKLM", str(dict(self.client.session)))

        first = self.client.get(reverse("finance:recovery_code"))
        second = self.client.get(reverse("finance:recovery_code"))

        self.assertContains(first, "ABCD-EFGH-JKLM")
        self.assertNotContains(second, "ABCD-EFGH-JKLM")
