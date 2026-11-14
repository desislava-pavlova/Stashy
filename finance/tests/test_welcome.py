"""The welcome wizard a new stash goes through: its four steps and the order they come in."""

import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from .. import starter_categories
from ..models import Account, Category, Profile
from .base import StashTestCase


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


class WelcomeWizardTestCase(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        cls.profile = Profile.for_user(cls.user)
        cls.profile.owner_name = "Alex"
        cls.profile.welcome_step = 0
        cls.profile.save(update_fields=["owner_name", "welcome_step"])

    def setUp(self):
        self.client.force_login(self.user)


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
    def save_step_one(self, time_zone):
        return self.client.post(
            reverse("finance:welcome_step", args=[1]),
            {
                "main_currency": "USD",
                "time_zone": time_zone,
                "date_format": "dmy",
                "number_format": "space_dot",
                "first_day_of_week": "1",
            },
        )

    def show_step_one_with_time_zone(self, time_zone):
        self.profile.time_zone = time_zone
        self.profile.save(update_fields=["time_zone"])
        return self.client.get(reverse("finance:welcome_step", args=[1]))

    def test_saving_preferences_advances_to_step_two(self):
        response = self.save_step_one("Europe/Sofia")
        self.assertRedirects(response, reverse("finance:welcome_step", args=[2]))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.main_currency, "USD")
        self.assertEqual(self.profile.welcome_step, 1)

    def test_a_time_zone_that_is_not_in_the_list_is_refused_here_too(self):
        """Plan 03, S54: step 1 uses Settings' ProfileForm, so the same list applies."""
        before = (self.profile.time_zone, self.profile.main_currency)
        response = self.save_step_one("Mars/Olympus_Mons")
        self.assertContains(response, "Select a valid choice. Mars/Olympus_Mons is not one of the available choices.")
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.time_zone, self.profile.main_currency), before)
        self.assertEqual(self.profile.welcome_step, 0)

    def test_the_time_zone_read_from_this_computer_is_the_one_selected(self):
        response = self.show_step_one_with_time_zone("Europe/Lisbon")
        self.assertContains(response, '<option value="Europe/Lisbon" selected>Europe/Lisbon</option>', html=True)

    def test_a_time_zone_read_from_this_computer_but_not_in_the_list_is_still_the_one_selected(self):
        """Plan 03, S77: tzlocal gives some Windows zones names that zone.tab doesn't list
        ("FLE Standard Time" is "Europe/Kiev"), and stashes made before detection translated
        them hold that name. The dropdown showed Africa/Abidjan instead, and Next saved it."""
        response = self.show_step_one_with_time_zone("Europe/Kiev")
        self.assertContains(response, '<option value="Europe/Kiev" selected>Europe/Kiev</option>', html=True)


class WelcomeStepTwoTests(WelcomeWizardTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.profile.welcome_step = 1
        cls.profile.save(update_fields=["welcome_step"])

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "starter_categories.xlsx"

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
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.profile.welcome_step = 2
        cls.profile.save(update_fields=["welcome_step"])
        cls.category = Category.objects.create(name="Bills", category_type=Category.CategoryType.EXPENSE)

    def test_skip_advances_without_saving_anything(self):
        response = self.client.post(reverse("finance:welcome_step", args=[3]), {"skip": "1"})
        self.assertRedirects(response, reverse("finance:welcome_step", args=[4]))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.welcome_step, 3)
        self.assertFalse(self.profile.watched_expenses_categories.exists())

    def test_saving_watched_categories_advances(self):
        response = self.client.post(
            reverse("finance:welcome_step", args=[3]),
            {
                "watched_expenses_title": "Bills & Utilities",
                "watched_expenses_categories": [str(self.category.pk)],
            },
        )
        self.assertRedirects(response, reverse("finance:welcome_step", args=[4]))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.welcome_step, 3)
        self.assertIn(self.category, self.profile.watched_expenses_categories.all())

    def test_coming_back_and_saving_again_replaces_or_clears_the_chosen_categories(self):
        """Step 3 saves only the Profile's own columns now (plan 03, S75), and its categories
        (a many-to-many) still have to be saved with them."""
        power = Category.objects.create(name="Power", category_type=Category.CategoryType.EXPENSE)
        self.profile.welcome_step = 3
        self.profile.save(update_fields=["welcome_step"])
        self.profile.watched_expenses_categories.set([self.category])
        steps = [([power.pk], [power]), ([self.category.pk, power.pk], [self.category, power]), ([], [])]
        for chosen, expected in steps:
            with self.subTest(chosen=chosen):
                self.client.post(
                    reverse("finance:welcome_step", args=[3]),
                    {"watched_expenses_title": "Bills", "watched_expenses_categories": chosen},
                )
                self.assertCountEqual(self.profile.watched_expenses_categories.all(), expected)

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


class WelcomeStepsKeepTheLoginStateTests(WelcomeWizardTestCase):
    """Plan 03, S75: a step saved from the request's older copy of the Profile must not put
    back an old lockout or recovery code, and still moves the wizard on."""

    def test_steps_one_and_three_keep_the_lockout_and_the_recovery_code(self):
        posts = {
            1: {
                "main_currency": "USD",
                "time_zone": self.profile.time_zone,
                "date_format": "dmy",
                "number_format": "space_dot",
                "first_day_of_week": "1",
            },
            3: {"watched_expenses_title": "Bills"},
        }
        for step, data in posts.items():
            with self.subTest(step=step):
                Profile.objects.filter(pk=self.profile.pk).update(
                    welcome_step=step - 1, failed_pin_attempts=0, locked_until=None, recovery_code_hash="old hash"
                )
                old_copy = Profile.objects.get(pk=self.profile.pk)
                locked_until = timezone.now() + timedelta(minutes=5)
                Profile.objects.filter(pk=self.profile.pk).update(
                    failed_pin_attempts=3, locked_until=locked_until, recovery_code_hash="today's hash"
                )
                with mock.patch.object(Profile, "for_user", return_value=old_copy):
                    response = self.client.post(reverse("finance:welcome_step", args=[step]), data)
                self.assertRedirects(
                    response, reverse("finance:welcome_step", args=[step + 1]), fetch_redirect_response=False
                )
                saved = Profile.objects.get(pk=self.profile.pk)
                self.assertEqual(
                    (saved.welcome_step, saved.failed_pin_attempts, saved.locked_until, saved.recovery_code_hash),
                    (step, 3, locked_until, "today's hash"),
                )


class WelcomeStepFourTests(WelcomeWizardTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.profile.welcome_step = 3
        cls.profile.save(update_fields=["welcome_step"])

    def test_skip_finishes_setup_without_creating_an_account(self):
        response = self.client.post(reverse("finance:welcome_step", args=[4]), {"skip": "1"})
        self.assertRedirects(response, reverse("finance:dashboard"))
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.setup_finished)
        self.assertFalse(Account.objects.exists())

    def test_creating_an_account_finishes_setup(self):
        response = self.client.post(
            reverse("finance:welcome_step", args=[4]),
            {
                "name": "Checking",
                "account_type": "debit_card",
                "currency": "EUR",
                "is_active": "on",
                "opening_balance": "50",
                "opening_balance_date": "01/01/2026",
            },
        )
        self.assertRedirects(response, reverse("finance:dashboard"))
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.setup_finished)
        self.assertTrue(Account.objects.filter(name="Checking").exists())
