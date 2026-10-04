"""The welcome wizard a new stash goes through: its four steps and the order they come in."""

import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from django.urls import reverse
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
    def test_saving_preferences_advances_to_step_two(self):
        response = self.client.post(
            reverse("finance:welcome_step", args=[1]),
            {
                "main_currency": "USD",
                "time_zone": "Europe/Sofia",
                "date_format": "dmy",
                "number_format": "space_dot",
                "first_day_of_week": "1",
            },
        )
        self.assertRedirects(response, reverse("finance:welcome_step", args=[2]))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.main_currency, "USD")
        self.assertEqual(self.profile.welcome_step, 1)


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
