"""The welcome wizard a new stash goes through: its four steps and the
order they come in."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from openpyxl import load_workbook

from .. import starter_categories
from ..models import Account, Category, Profile, Transaction, name_key
from .base import StarterCategoriesFileMixin, StashTestCase


class SetupFinishedTests(SimpleTestCase):
    def test_a_fresh_profile_default_counts_as_finished(self):
        # Existing stashes (and every plain Profile() created outside
        # create_stash()) start with the "already set up" default - only
        # create_stash() sets welcome_step=0.
        self.assertTrue(Profile().setup_finished)

    def test_welcome_step_below_the_total_is_not_finished(self):
        self.assertFalse(Profile(welcome_step=0).setup_finished)
        self.assertFalse(Profile(welcome_step=3).setup_finished)

    def test_welcome_step_at_the_total_is_finished(self):
        self.assertTrue(Profile(welcome_step=Profile.WELCOME_STEPS_DONE).setup_finished)


class WelcomeWizardTestCase(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "tester", password="not-a-real-password-123"
        )
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
        """Plan 03, S54: step 1 uses Settings' ProfileForm, so the same
        list applies."""
        before = (self.profile.time_zone, self.profile.main_currency)
        response = self.save_step_one("Mars/Olympus_Mons")
        self.assertContains(
            response,
            "Select a valid choice. Mars/Olympus_Mons is not one of the available choices.",
        )
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.time_zone, self.profile.main_currency), before)
        self.assertEqual(self.profile.welcome_step, 0)

    def test_the_time_zone_read_from_this_computer_is_the_one_selected(self):
        response = self.show_step_one_with_time_zone("Europe/Lisbon")
        self.assertContains(
            response, '<option value="Europe/Lisbon" selected>Europe/Lisbon</option>', html=True
        )

    def test_a_time_zone_read_from_this_computer_but_not_in_the_list_is_still_the_one_selected(
        self,
    ):
        """Plan 03, S77: tzlocal gives some Windows zones names that
        zone.tab doesn't list ("FLE Standard Time" is "Europe/Kiev"),
        and stashes made before detection translated them hold that
        name. The dropdown showed Africa/Abidjan instead, and Next saved
        it."""
        response = self.show_step_one_with_time_zone("Europe/Kiev")
        self.assertContains(
            response, '<option value="Europe/Kiev" selected>Europe/Kiev</option>', html=True
        )


class WelcomeStepTwoTests(StarterCategoriesFileMixin, WelcomeWizardTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.profile.welcome_step = 1
        cls.profile.save(update_fields=["welcome_step"])

    def setUp(self):
        super().setUp()
        self.use_a_starter_categories_file()
        self.write_starter_categories({"Basic": [("Expense", "Groceries", None)]})

    def test_picking_basic_creates_categories_and_advances(self):
        response = self.client.post(reverse("finance:welcome_step", args=[2]), {"sheet": "Basic"})
        self.assertRedirects(response, reverse("finance:welcome_step", args=[3]))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.welcome_step, 2)
        self.assertTrue(Category.objects.filter(name="Groceries", parent__isnull=True).exists())

    def messages_after_picking_basic(self):
        response = self.client.post(reverse("finance:welcome_step", args=[2]), {"sheet": "Basic"})
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_one_new_category_is_called_a_category(self):
        self.assertEqual(self.messages_after_picking_basic(), ["Added 1 category from Basic."])

    def test_several_new_categories_are_called_categories(self):
        self.write_starter_categories(
            {"Basic": [("Expense", "Groceries", None), ("Expense", "Rent", None)]}
        )
        self.assertEqual(self.messages_after_picking_basic(), ["Added 2 categories from Basic."])

    def test_an_unavailable_sheet_name_is_rejected(self):
        with mock.patch.object(starter_categories, "available_sheets", return_value=["Basic"]):
            response = self.client.post(
                reverse("finance:welcome_step", args=[2]), {"sheet": "Extended"}
            )
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.welcome_step, 1)

    def test_a_missing_file_offers_skip_instead_of_a_dead_end(self):
        with mock.patch.object(starter_categories, "available_sheets", return_value=[]):
            get_response = self.client.get(reverse("finance:welcome_step", args=[2]))
            self.assertContains(get_response, "wasn't found")
            post_response = self.client.post(
                reverse("finance:welcome_step", args=[2]), {"skip": "1"}
            )
        self.assertRedirects(post_response, reverse("finance:welcome_step", args=[3]))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.welcome_step, 2)


class WelcomeStepTwoWithTheRealFileTests(WelcomeWizardTestCase):
    """Every test above swaps in a file of its own. These use the one
    Stashy ships (finance/starter_categories.xlsx), the way a new
    stash's wizard does, so a file that goes missing, or whose rows the
    step can't turn into categories, fails here and not for the first
    person to set up a stash. They compare with the file's own rows, so
    adding or renaming a category in it needs no change here."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.profile.welcome_step = 1
        cls.profile.save(update_fields=["welcome_step"])

    @staticmethod
    def rows_of(sheet_name):
        """(type, category, subcategory or None) for each row of the
        shipped file's sheet."""
        workbook = load_workbook(
            starter_categories.starter_categories_path(), read_only=True, data_only=True
        )
        try:
            rows = workbook[sheet_name].iter_rows(min_row=2, values_only=True)
            return [
                (
                    str(type_name).strip().lower(),
                    str(category).strip(),
                    str(sub).strip() if sub else None,
                )
                for type_name, category, sub in rows
                if type_name and category
            ]
        finally:
            workbook.close()

    def assert_the_whole_list_is_created(self, sheet_name):
        rows = self.rows_of(sheet_name)
        main_types = {name_key(category): type_name for type_name, category, _ in rows}
        subcategories = {(name_key(category), name_key(sub)) for _, category, sub in rows if sub}
        self.assertTrue(main_types)

        response = self.client.post(
            reverse("finance:welcome_step", args=[2]), {"sheet": sheet_name}
        )

        self.assertRedirects(response, reverse("finance:welcome_step", args=[3]))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.welcome_step, 2)
        created = Category.objects.select_related("parent")
        self.assertEqual(
            {c.name_key: c.category_type for c in created.filter(parent__isnull=True)}, main_types
        )
        self.assertEqual(
            {(c.parent.name_key, c.name_key) for c in created.filter(parent__isnull=False)},
            subcategories,
        )
        total = len(main_types) + len(subcategories)
        self.assertEqual(
            [str(message) for message in get_messages(response.wsgi_request)],
            [f"Added {total} categories from {sheet_name}."],
        )
        for category in created:
            with self.subTest(category=category.full_name):
                category.full_clean()

    def test_the_page_offers_both_lists(self):
        response = self.client.get(reverse("finance:welcome_step", args=[2]))
        self.assertEqual(response.context["available"], ["Basic", "Extended"])
        self.assertNotContains(response, "wasn't found")

    def test_basic_creates_every_category_in_its_sheet(self):
        self.assert_the_whole_list_is_created("Basic")

    def test_extended_creates_every_category_and_subcategory_in_its_sheet(self):
        self.assert_the_whole_list_is_created("Extended")


class WelcomeStepThreeTests(WelcomeWizardTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.profile.welcome_step = 2
        cls.profile.save(update_fields=["welcome_step"])
        cls.category = Category.objects.create(
            name="Bills", category_type=Category.CategoryType.EXPENSE
        )

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

    def test_a_category_id_too_big_for_the_database_stays_on_the_step_with_an_error(self):
        """S106: the wizard's step uses the same form as Settings, so it
        needs the same care."""
        response = self.client.post(
            reverse("finance:welcome_step", args=[3]),
            {
                "watched_expenses_title": "Bills",
                "watched_expenses_categories": [self.category.pk, "9" * 30],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "finance/welcome_watched_expenses.html")
        self.assertContains(response, "Select a valid choice")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.welcome_step, 2)
        self.assertFalse(self.profile.watched_expenses_categories.exists())

    def test_coming_back_and_saving_again_replaces_or_clears_the_chosen_categories(self):
        """Step 3 saves only the Profile's own columns, and its
        categories (a many-to-many) still have to be saved with them."""
        power = Category.objects.create(name="Power", category_type=Category.CategoryType.EXPENSE)
        self.profile.welcome_step = 3
        self.profile.save(update_fields=["welcome_step"])
        self.profile.watched_expenses_categories.set([self.category])
        steps = [
            ([power.pk], [power]),
            ([self.category.pk, power.pk], [self.category, power]),
            ([], []),
        ]
        for chosen, expected in steps:
            with self.subTest(chosen=chosen):
                self.client.post(
                    reverse("finance:welcome_step", args=[3]),
                    {"watched_expenses_title": "Bills", "watched_expenses_categories": chosen},
                )
                self.assertCountEqual(self.profile.watched_expenses_categories.all(), expected)

    def test_title_starts_with_watched_expenses_and_the_dropdown_has_bootstrap_js(self):
        # S46: the step used to empty the box to hide the old "Bills &
        # Utilities" default; a neutral default needs no hiding, so the
        # box shows it and you can keep it or type over it.
        response = self.client.get(reverse("finance:welcome_step", args=[3]))
        title = response.context["form"]["watched_expenses_title"]
        self.assertEqual(title.value(), "Watched expenses")
        self.assertNotContains(response, 'value="Bills &amp; Utilities"')
        self.assertContains(response, "bootstrap.bundle.min.js")

    def test_coming_back_keeps_the_saved_title(self):
        self.profile.welcome_step = 3
        self.profile.watched_expenses_title = "My bills"
        self.profile.save(update_fields=["welcome_step", "watched_expenses_title"])
        response = self.client.get(reverse("finance:welcome_step", args=[3]))
        self.assertContains(response, 'value="My bills"')


class WelcomeStepsKeepTheLoginStateTests(WelcomeWizardTestCase):
    """Plan 03, S75: a step saved from the request's older copy of the
    Profile must not put back an old lockout or recovery code, and still
    moves the wizard on."""

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
                    welcome_step=step - 1,
                    failed_pin_attempts=0,
                    locked_until=None,
                    recovery_code_hash="old hash",
                )
                old_copy = Profile.objects.get(pk=self.profile.pk)
                locked_until = timezone.now() + timedelta(minutes=5)
                Profile.objects.filter(pk=self.profile.pk).update(
                    failed_pin_attempts=3,
                    locked_until=locked_until,
                    recovery_code_hash="today's hash",
                )
                with mock.patch.object(Profile, "for_user", return_value=old_copy):
                    response = self.client.post(reverse("finance:welcome_step", args=[step]), data)
                self.assertRedirects(
                    response,
                    reverse("finance:welcome_step", args=[step + 1]),
                    fetch_redirect_response=False,
                )
                saved = Profile.objects.get(pk=self.profile.pk)
                self.assertEqual(
                    (
                        saved.welcome_step,
                        saved.failed_pin_attempts,
                        saved.locked_until,
                        saved.recovery_code_hash,
                    ),
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
        opening = Transaction.objects.get(account__name="Checking")
        self.assertEqual(opening.amount, Decimal("50.00"))
