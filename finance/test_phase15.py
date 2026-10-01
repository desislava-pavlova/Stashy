"""Tests for Phase 15 (login and new stash setup) not already covered by
test_phase12_stash_login.py: owner names (L1), Settings' new layout and the Change PIN /
recovery code pages that moved out of it (L6-L8), starter categories from the .xlsx file (L5),
and the welcome wizard (L4).

create_stash(), the picker's owner-name listing/uniqueness check, and the folder-name slug
generator all open *other* stashes' databases via dynamically-registered aliases, which
Django's test runner blocks (see test_phase12_stash_login.py's own docstring) - those were
instead verified directly against a real temporary directory; see plans/01_upgrade_plan.md's Phase 15
notes for what was checked. RequireSetupMiddleware short-circuits under settings.TESTING (same
as every other stash-routing middleware, so a normal Client-driven test can log in without a
real stash), so its own redirect logic is tested directly via RequestFactory, the same pattern
test_phase12_stash_login.py's RequireStashMiddlewareTests already uses; the welcome_step view's
own step-order logic (unaffected by that flag) is tested through the real Client instead."""
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import stashes, starter_categories
from .middleware import RequireSetupMiddleware, RequireStashMiddleware
from .models import Account, Category, Profile


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


class SetupFinishedTests(SimpleTestCase):
    def test_a_fresh_profile_default_counts_as_finished(self):
        # Existing stashes (and every plain Profile() created outside create_stash()) start
        # with the "already set up" default - only create_stash() sets welcome_step=0.
        self.assertTrue(Profile().setup_finished)

    def test_welcome_step_below_the_total_is_not_finished(self):
        self.assertFalse(Profile(welcome_step=0).setup_finished)
        self.assertFalse(Profile(welcome_step=3).setup_finished)

    def test_welcome_step_at_the_total_is_finished(self):
        self.assertTrue(Profile(welcome_step=Profile.WELCOME_STEPS_DONE).setup_finished)


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.profile = Profile.for_user(self.user)
        self.profile.owner_name = "Alex"
        self.profile.save(update_fields=["owner_name"])


class SettingsLayoutTests(LoggedInTestCase):
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


class ChangePinPageTests(LoggedInTestCase):
    def test_get_shows_the_form_with_a_back_link(self):
        response = self.client.get(reverse("finance:change_pin"))
        self.assertContains(response, "Current PIN")
        self.assertContains(response, reverse("finance:settings"))

    def test_correct_current_pin_changes_it_and_returns_to_settings(self):
        self.user.set_password("1234")
        self.user.save()
        self.client.force_login(self.user)  # the old session's auth hash died with the password
        response = self.client.post(reverse("finance:change_pin"), {
            "current_pin": "1234", "new_pin": "5678", "new_pin_confirm": "5678",
        })
        self.assertRedirects(response, reverse("finance:settings"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("5678"))

    def test_wrong_current_pin_is_rejected(self):
        self.user.set_password("1234")
        self.user.save()
        self.client.force_login(self.user)  # the old session's auth hash died with the password
        response = self.client.post(reverse("finance:change_pin"), {
            "current_pin": "0000", "new_pin": "5678", "new_pin_confirm": "5678",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("1234"))


class RecoveryCodePageTests(LoggedInTestCase):
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
        from . import stashes

        known_code = stashes.regenerate_recovery_code(self.profile)
        self.client.post(reverse("finance:recovery_code"))
        self.profile.refresh_from_db()
        ok, _ = stashes.reset_pin_with_recovery_code(self.profile, known_code, "1234")
        self.assertFalse(ok)


class StarterCategoriesXlsxTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "starter_categories.xlsx"
        self.patcher = mock.patch("finance.starter_categories.starter_categories_path", return_value=self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _write(self, sheets):
        from openpyxl import Workbook

        workbook = Workbook()
        workbook.remove(workbook.active)
        for name, rows in sheets.items():
            sheet = workbook.create_sheet(name)
            sheet.append(["Type", "Category", "Subcategory"])
            for row in rows:
                sheet.append(row)
        workbook.save(self.path)

    def test_missing_file_has_no_available_sheets(self):
        self.assertEqual(starter_categories.available_sheets(), [])

    def test_available_sheets_matches_whats_in_the_file(self):
        self._write({"Basic": [("Expense", "Groceries", None)]})
        self.assertEqual(starter_categories.available_sheets(), ["Basic"])

    def test_basic_creates_top_level_categories_only(self):
        self._write({"Basic": [("Expense", "Groceries", None), ("Income", "Work Income", None)]})
        created = starter_categories.add_starter_categories("Basic")
        self.assertEqual(created, ["Groceries", "Work Income"])
        self.assertEqual(Category.objects.get(name="Groceries").category_type, Category.CategoryType.EXPENSE)
        self.assertEqual(Category.objects.get(name="Work Income").category_type, Category.CategoryType.INCOME)
        self.assertFalse(Category.objects.filter(parent__isnull=False).exists())

    def test_extended_creates_subcategories_under_a_shared_parent(self):
        self._write({"Extended": [
            ("Expense", "Auto", "Fuel"), ("Expense", "Auto", "Repairs"), ("Income", "Other Income", "Gifts"),
        ]})
        created = starter_categories.add_starter_categories("Extended")
        auto = Category.objects.get(name="Auto", parent__isnull=True)
        self.assertEqual(set(auto.subcategories.values_list("name", flat=True)), {"Fuel", "Repairs"})
        self.assertEqual(created, ["Auto", "Auto: Fuel", "Auto: Repairs", "Other Income", "Other Income: Gifts"])

    def test_running_twice_creates_nothing_the_second_time(self):
        self._write({"Basic": [("Expense", "Groceries", None)]})
        starter_categories.add_starter_categories("Basic")
        second_run = starter_categories.add_starter_categories("Basic")
        self.assertEqual(second_run, [])

    def test_existing_category_is_matched_case_insensitively(self):
        Category.objects.create(name="groceries", category_type=Category.CategoryType.EXPENSE)
        self._write({"Basic": [("Expense", "Groceries", None)]})
        created = starter_categories.add_starter_categories("Basic")
        self.assertEqual(created, [])
        self.assertEqual(Category.objects.filter(name__iexact="groceries").count(), 1)

    def test_unavailable_sheet_returns_none(self):
        self._write({"Basic": [("Expense", "Groceries", None)]})
        self.assertIsNone(starter_categories.add_starter_categories("Extended"))


class OldStarterCategoriesButtonIsGoneTests(LoggedInTestCase):
    def test_category_list_has_no_add_starter_categories_button(self):
        response = self.client.get(reverse("finance:category_list"))
        self.assertNotContains(response, "Add starter categories")


class RememberedStashCookieTests(SimpleTestCase):
    """L3: the "last stash used" cookie lets RequireStashMiddleware skip the picker and land
    straight on that stash's own PIN screen - a browser cookie, separate from the session
    logout() flushes, per test_phase12_stash_login.py's own RequireStashMiddlewareTests
    pattern (a plain dict stands in for the session)."""

    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(TESTING=False)
    def test_a_remembered_stash_that_still_exists_is_selected_and_the_request_redirected(self):
        with mock.patch.object(stashes, "stash_exists", return_value=True):
            middleware = RequireStashMiddleware(get_response=lambda r: "should not be reached")
            request = self.factory.get("/accounts/login/")
            request.session = {}
            request.COOKIES = {stashes.LAST_STASH_COOKIE: "alex"}
            response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/accounts/login/")
        self.assertEqual(request.session["stash"], "alex")

    @override_settings(TESTING=False)
    def test_a_remembered_stash_that_no_longer_exists_falls_back_to_the_picker(self):
        with mock.patch.object(stashes, "stash_exists", return_value=False):
            middleware = RequireStashMiddleware(get_response=lambda r: "should not be reached")
            request = self.factory.get("/accounts/login/")
            request.session = {}
            request.COOKIES = {stashes.LAST_STASH_COOKIE: "gone"}
            response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("stash_picker"))
        self.assertNotIn("stash", request.session)

    @override_settings(TESTING=False)
    def test_no_cookie_falls_back_to_the_picker_as_before(self):
        middleware = RequireStashMiddleware(get_response=lambda r: "should not be reached")
        request = self.factory.get("/accounts/login/")
        request.session = {}
        request.COOKIES = {}
        response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("stash_picker"))

    @override_settings(TESTING=False)
    def test_the_pickers_own_urls_stay_exempt_even_with_a_remembered_cookie(self):
        # A cookie doesn't force-select a stash on the picker's own pages - picking one there
        # is still an explicit action, per stash_picker()'s "already selected -> redirect to
        # login" guard, not something this middleware should short-circuit.
        with mock.patch.object(stashes, "stash_exists", return_value=True):
            middleware = RequireStashMiddleware(get_response=lambda r: "ok")
            request = self.factory.get("/stashes/")
            request.session = {}
            request.COOKIES = {stashes.LAST_STASH_COOKIE: "alex"}
            response = middleware(request)
        self.assertEqual(response, "ok")
        self.assertNotIn("stash", request.session)


class RequireSetupMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(TESTING=False)
    def test_unfinished_setup_redirects_to_its_own_next_step(self):
        middleware = RequireSetupMiddleware(get_response=lambda r: "should not be reached")
        request = self.factory.get("/transactions/")
        request.stashy_profile = Profile(welcome_step=1)
        response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("finance:welcome_step", args=[2]))

    @override_settings(TESTING=False)
    def test_finished_setup_passes_through(self):
        middleware = RequireSetupMiddleware(get_response=lambda r: "ok")
        request = self.factory.get("/transactions/")
        request.stashy_profile = Profile(welcome_step=Profile.WELCOME_STEPS_DONE)
        self.assertEqual(middleware(request), "ok")

    @override_settings(TESTING=False)
    def test_the_wizards_own_urls_are_exempt(self):
        middleware = RequireSetupMiddleware(get_response=lambda r: "ok")
        request = self.factory.get("/welcome/2/")
        request.stashy_profile = Profile(welcome_step=0)
        self.assertEqual(middleware(request), "ok")

    @override_settings(TESTING=False)
    def test_logout_stays_reachable_mid_setup(self):
        middleware = RequireSetupMiddleware(get_response=lambda r: "ok")
        request = self.factory.get("/accounts/logout/")
        request.stashy_profile = Profile(welcome_step=0)
        self.assertEqual(middleware(request), "ok")

    @override_settings(TESTING=False)
    def test_no_profile_on_the_request_passes_through(self):
        # Regional preferences middleware never sets stashy_profile for an unauthenticated
        # request - nothing here for this middleware to act on either.
        middleware = RequireSetupMiddleware(get_response=lambda r: "ok")
        request = self.factory.get("/transactions/")
        self.assertEqual(middleware(request), "ok")


class WelcomeWizardTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.profile = Profile.for_user(self.user)
        self.profile.owner_name = "Alex"
        self.profile.welcome_step = 0
        self.profile.save(update_fields=["owner_name", "welcome_step"])


class WelcomeStepOrderTests(WelcomeWizardTestCase):
    def test_step_one_is_reachable_at_welcome_step_zero(self):
        response = self.client.get(reverse("finance:welcome_step", args=[1]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Money & region")
        self.assertContains(response, "Step 1 of 4")

    def test_jumping_ahead_bounces_back_to_the_current_step(self):
        response = self.client.get(reverse("finance:welcome_step", args=[3]))
        self.assertRedirects(response, reverse("finance:welcome_step", args=[1]))

    def test_an_already_completed_step_can_still_be_revisited(self):
        self.profile.welcome_step = 2
        self.profile.save(update_fields=["welcome_step"])
        response = self.client.get(reverse("finance:welcome_step", args=[1]))
        self.assertEqual(response.status_code, 200)

    def test_finished_setup_redirects_any_step_to_the_dashboard(self):
        self.profile.welcome_step = Profile.WELCOME_STEPS_DONE
        self.profile.save(update_fields=["welcome_step"])
        response = self.client.get(reverse("finance:welcome_step", args=[1]))
        self.assertRedirects(response, reverse("finance:dashboard"))


class WelcomeStepOneTests(WelcomeWizardTestCase):
    def test_saving_preferences_advances_to_step_two(self):
        response = self.client.post(reverse("finance:welcome_step", args=[1]), {
            "main_currency": "USD", "time_zone": "Europe/Sofia", "date_format": "dmy",
            "number_format": "space_dot", "first_day_of_week": "1",
        })
        self.assertRedirects(response, reverse("finance:welcome_step", args=[2]))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.main_currency, "USD")
        self.assertEqual(self.profile.welcome_step, 1)


class WelcomeStepTwoTests(WelcomeWizardTestCase):
    def setUp(self):
        super().setUp()
        self.profile.welcome_step = 1
        self.profile.save(update_fields=["welcome_step"])
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "starter_categories.xlsx"
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Basic"
        sheet.append(["Type", "Category", "Subcategory"])
        sheet.append(["Expense", "Groceries", None])
        workbook.save(path)
        self.patcher = mock.patch("finance.starter_categories.starter_categories_path", return_value=path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_picking_basic_creates_categories_and_advances(self):
        response = self.client.post(reverse("finance:welcome_step", args=[2]), {"sheet": "Basic"})
        self.assertRedirects(response, reverse("finance:welcome_step", args=[3]))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.welcome_step, 2)
        self.assertTrue(Category.objects.filter(name="Groceries", parent__isnull=True).exists())

    def test_an_unavailable_sheet_name_is_rejected(self):
        with mock.patch.object(starter_categories, "available_sheets", return_value=["Basic"]):
            response = self.client.post(reverse("finance:welcome_step", args=[2]), {"sheet": "Extended"})
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.welcome_step, 1)

    def test_a_missing_file_offers_skip_instead_of_a_dead_end(self):
        with mock.patch.object(starter_categories, "available_sheets", return_value=[]):
            get_response = self.client.get(reverse("finance:welcome_step", args=[2]))
            self.assertContains(get_response, "wasn't found")
            post_response = self.client.post(reverse("finance:welcome_step", args=[2]), {"skip": "1"})
        self.assertRedirects(post_response, reverse("finance:welcome_step", args=[3]))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.welcome_step, 2)


class WelcomeStepThreeTests(WelcomeWizardTestCase):
    def setUp(self):
        super().setUp()
        self.profile.welcome_step = 2
        self.profile.save(update_fields=["welcome_step"])
        self.category = Category.objects.create(name="Bills", category_type=Category.CategoryType.EXPENSE)

    def test_skip_advances_without_saving_anything(self):
        response = self.client.post(reverse("finance:welcome_step", args=[3]), {"skip": "1"})
        self.assertRedirects(response, reverse("finance:welcome_step", args=[4]))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.welcome_step, 3)
        self.assertFalse(self.profile.watched_expenses_categories.exists())

    def test_saving_watched_categories_advances(self):
        response = self.client.post(reverse("finance:welcome_step", args=[3]), {
            "watched_expenses_title": "Bills & Utilities",
            "watched_expenses_categories": [str(self.category.pk)],
        })
        self.assertRedirects(response, reverse("finance:welcome_step", args=[4]))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.welcome_step, 3)
        self.assertIn(self.category, self.profile.watched_expenses_categories.all())

    def test_title_starts_empty_and_the_dropdown_has_bootstrap_js(self):
        response = self.client.get(reverse("finance:welcome_step", args=[3]))
        title = response.context["form"]["watched_expenses_title"]
        self.assertEqual(title.value(), "")
        self.assertNotContains(response, 'value="Bills &amp; Utilities"')
        self.assertContains(response, "bootstrap.bundle.min.js")

    def test_coming_back_keeps_the_saved_title(self):
        self.profile.welcome_step = 3
        self.profile.watched_expenses_title = "My bills"
        self.profile.save(update_fields=["welcome_step", "watched_expenses_title"])
        response = self.client.get(reverse("finance:welcome_step", args=[3]))
        self.assertContains(response, 'value="My bills"')


class WelcomeStepFourTests(WelcomeWizardTestCase):
    def setUp(self):
        super().setUp()
        self.profile.welcome_step = 3
        self.profile.save(update_fields=["welcome_step"])

    def test_skip_finishes_setup_without_creating_an_account(self):
        response = self.client.post(reverse("finance:welcome_step", args=[4]), {"skip": "1"})
        self.assertRedirects(response, reverse("finance:dashboard"))
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.setup_finished)
        self.assertFalse(Account.objects.exists())

    def test_creating_an_account_finishes_setup(self):
        response = self.client.post(reverse("finance:welcome_step", args=[4]), {
            "name": "Checking", "account_type": "debit_card", "currency": "EUR", "is_active": "on",
            "opening_balance": "50", "opening_balance_date": "01/01/2026",
        })
        self.assertRedirects(response, reverse("finance:dashboard"))
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.setup_finished)
        self.assertTrue(Account.objects.filter(name="Checking").exists())
