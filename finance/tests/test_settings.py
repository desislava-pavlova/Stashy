"""The Settings page: its layout, its separate form endpoints (exchange
rates, Watched expenses), and the Change PIN and recovery code pages."""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from audit.models import AuditLogEntry

from ..forms._base import SafeModelMultipleChoiceField
from ..forms.settings import ProfileForm, WatchedExpensesForm
from ..models import Account, Category, ExchangeRate, Profile
from ..services.rates import RateFetchError
from ..stashes import security
from ..views.settings import _recovery_code_cache_key
from .base import (
    LEAKED_TEMPLATE_SYNTAX,
    REHASHING_PASSWORD_HASHERS,
    AlexsStashTestCase,
    LoggedInWithAccountTestCase,
    StashTestCase,
)


class ExchangeRateTableTests(LoggedInWithAccountTestCase):
    """S32: the Exchange rates table writes each rate's date in the
    stash's date format and names each source by its label, not the code
    stored in the database."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.profile = Profile.for_user(cls.user)
        for code, source in (
            ("USD", ExchangeRate.Source.MANUAL),
            ("GBP", ExchangeRate.Source.FRANKFURTER),
        ):
            Account.objects.create(name=f"{code} account", currency=code)
            ExchangeRate.objects.create(
                base_currency=code,
                quote_currency="EUR",
                rate=Decimal("0.5"),
                date=date(2026, 4, 3),
                source=source,
            )

    def page(self, date_format):
        self.profile.date_format = date_format
        self.profile.save(update_fields=["date_format"])
        return self.client.get(reverse("finance:settings"))

    def test_the_date_follows_the_stashs_date_format(self):
        self.assertContains(self.page("dmy"), "<td>03/04/2026</td>")
        month_first = self.page("mdy")
        self.assertContains(month_first, "<td>04/03/2026</td>")
        self.assertNotContains(month_first, "03/04/2026")
        self.assertContains(self.page("ymd"), "<td>2026-04-03</td>")

    def test_each_source_shows_its_label(self):
        response = self.page("dmy")
        self.assertContains(response, ">Typed in<")
        self.assertContains(response, ">Frankfurter (ECB)<")
        self.assertNotContains(response, ">manual<")
        self.assertNotContains(response, ">Frankfurter<")


class ExchangeRateTableRowsTests(LoggedInWithAccountTestCase):
    """The Exchange rates table row by row: which currencies it lists,
    and what it shows for each."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Profile.for_user(cls.user)

    def add_rate(self, source, rate, day=date(2026, 4, 3), base="USD", quote="EUR"):
        return ExchangeRate.objects.create(
            base_currency=base, quote_currency=quote, rate=Decimal(rate), date=day, source=source
        )

    def page(self):
        return self.client.get(reverse("finance:settings"))

    def test_a_currency_with_no_known_rate_says_so_instead_of_showing_a_blank_row(self):
        Account.objects.create(name="US card", currency="USD")
        response = self.page()
        self.assertEqual(response.context["known_rates"], [("USD", None)])
        self.assertContains(response, "No rate known yet")
        self.assertNotContains(response, "Every account already uses")

    def test_with_every_account_in_the_main_currency_there_is_nothing_to_convert(self):
        response = self.page()  # the one account is in the main currency
        self.assertEqual(response.context["known_rates"], [])
        self.assertContains(
            response, f"Every account already uses {response.context['profile'].main_currency}"
        )
        self.assertNotContains(response, "No rate known yet")

    def test_two_accounts_in_one_currency_make_one_row(self):
        for name in ("US card", "US savings"):
            Account.objects.create(name=name, currency="USD")
        self.assertEqual([code for code, _ in self.page().context["known_rates"]], ["USD"])

    def test_a_typed_rate_and_a_fetched_one_for_the_same_day_show_the_typed_one_only(self):
        Account.objects.create(name="US card", currency="USD")
        self.add_rate(ExchangeRate.Source.FRANKFURTER, "0.9")
        self.add_rate(ExchangeRate.Source.MANUAL, "0.8")
        response = self.page()
        self.assertEqual(len(response.context["known_rates"]), 1)
        self.assertContains(response, '<td class="text-end">0.8000</td>', html=True)
        self.assertContains(response, ">Typed in<")
        self.assertNotContains(response, "0.9000")
        self.assertNotContains(response, "Frankfurter (ECB)")

    def test_a_rate_stored_the_other_way_round_is_shown_as_the_rate_into_the_main_currency(self):
        Account.objects.create(name="US card", currency="USD")
        self.add_rate(ExchangeRate.Source.MANUAL, "1.25", base="EUR", quote="USD")
        self.assertContains(self.page(), '<td class="text-end">0.8000</td>', html=True)


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

    def test_the_new_endpoints_take_only_post(self):
        for name in ("check_rates", "create_backup", "save_watched_expenses"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(f"finance:{name}")).status_code, 405)

    def test_checking_rates_and_backing_up_run_outside_the_request_transaction(self):
        for name in ("check_rates", "create_backup", "backup_restore"):
            with self.subTest(name=name):
                self.assertTrue(
                    getattr(resolve(reverse(f"finance:{name}")).func, "_non_atomic_requests", None)
                )
        self.assertFalse(
            getattr(resolve(reverse("finance:settings")).func, "_non_atomic_requests", None)
        )

    def test_check_rates_reports_what_it_stored_and_returns_to_settings(self):
        with mock.patch(
            "finance.views.settings.fetch_latest_rates", return_value=(["USD"], ["XYZ"])
        ):
            response = self.client.post(reverse("finance:check_rates"), follow=True)
        self.assertRedirects(response, reverse("finance:settings"))
        self.assertContains(response, "Updated 1 exchange rate.")
        self.assertContains(response, "No rate available for: XYZ")

    def test_check_rates_counts_several_rates_in_the_plural(self):
        with mock.patch(
            "finance.views.settings.fetch_latest_rates", return_value=(["USD", "GBP"], [])
        ):
            response = self.client.post(reverse("finance:check_rates"), follow=True)
        self.assertContains(response, "Updated 2 exchange rates.")
        self.assertNotContains(response, "No rate available")

    def test_check_rates_with_nothing_stored_says_nothing_was_updated(self):
        with mock.patch("finance.views.settings.fetch_latest_rates", return_value=([], ["XYZ"])):
            response = self.client.post(reverse("finance:check_rates"), follow=True)
        self.assertNotContains(response, "Updated")
        self.assertContains(response, "No rate available for: XYZ")

    def test_check_rates_offline_shows_the_error(self):
        with mock.patch(
            "finance.views.settings.fetch_latest_rates", side_effect=RateFetchError("No internet")
        ):
            response = self.client.post(reverse("finance:check_rates"), follow=True)
        self.assertContains(response, "No internet")

    def save_preferences(self, time_zone):
        """Posts the preferences form with the currency switched to USD
        and `time_zone`."""
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
        """Plan 03, S54: the dropdown alone didn't stop a hand-made
        request."""
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
        """The list comes from the time zone database's zone.tab, which
        has no plain "UTC": regional.time_zone_choices() adds it, and
        since S54 the list decides what's saved."""
        response = self.save_preferences("UTC")
        self.assertRedirects(response, reverse("finance:settings"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.time_zone, "UTC")

    def test_a_zone_saved_under_an_older_name_is_shown_and_kept(self):
        """Plan 03, S77: Europe/Kiev isn't in the list (it's Europe/Kyiv
        now), but a stash saved with it keeps it. Before, the dropdown
        showed Africa/Abidjan and a save stored that."""
        self.profile.time_zone = "Europe/Kiev"
        self.profile.save(update_fields=["time_zone"])
        page = self.client.get(reverse("finance:settings"))
        self.assertContains(
            page, '<option value="Europe/Kiev" selected>Europe/Kiev</option>', html=True
        )
        self.assertRedirects(self.save_preferences("Europe/Kiev"), reverse("finance:settings"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.time_zone, "Europe/Kiev")

    def test_every_kind_of_older_zone_name_is_shown_and_kept(self):
        for zone in ("Etc/UTC", "Asia/Calcutta", "Europe/Kiev"):
            with self.subTest(zone=zone):
                self.profile.time_zone = zone
                self.profile.save(update_fields=["time_zone"])
                page = self.client.get(reverse("finance:settings"))
                self.assertContains(
                    page, f'<option value="{zone}" selected>{zone}</option>', html=True
                )
                self.assertRedirects(self.save_preferences(zone), reverse("finance:settings"))
                self.profile.refresh_from_db()
                self.assertEqual(self.profile.time_zone, zone)

    def test_a_zone_that_does_not_exist_is_not_offered_and_the_pages_still_open(self):
        """A hand-edited stash: the pages must not break, and picking a
        real zone fixes it."""
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
        """Plan 03, S75: the page edits request.stashy_profile, read
        before the request's write lock. Saving the whole row would put
        back what that copy held."""
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
        self.assertEqual(
            (self.profile.main_currency, self.profile.watched_expenses_title), ("USD", "Bills")
        )

    def test_the_settings_page_does_not_undo_a_lockout_or_a_new_code_it_has_not_seen(self):
        """Plan 03, S75, through the real pages: the page's own copy of
        the Profile is read before the request takes the stash's write
        lock, so it can be older than the saved row."""
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
                    failed_pin_attempts=3,
                    locked_until=locked_until,
                    recovery_code_hash="today's hash",
                )
                with mock.patch.object(Profile, "for_user", return_value=old_copy):
                    self.assertEqual(post().status_code, 302)
                saved = Profile.objects.get(pk=self.profile.pk)
                self.assertEqual(
                    (saved.failed_pin_attempts, saved.locked_until, saved.recovery_code_hash),
                    (3, locked_until, "today's hash"),
                )

    def test_a_save_from_an_old_copy_writes_only_the_forms_own_columns_in_the_audit_log(self):
        """Saving the whole row from an old copy would also log
        "changes" to the lockout."""
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
        changes = AuditLogEntry.objects.filter(
            table="finance_profile", action=AuditLogEntry.Action.UPDATE
        )
        self.assertEqual(set(changes.values_list("column", flat=True)), {"main_currency"})
        self.assertEqual(Profile.objects.get(pk=self.profile.pk).welcome_step, 2)

    def test_a_form_saved_without_committing_writes_nothing_until_the_caller_does(self):
        category = Category.objects.create(
            name="Water", category_type=Category.CategoryType.EXPENSE
        )
        form = WatchedExpensesForm(
            data={"watched_expenses_title": "Water", "watched_expenses_categories": [category.pk]},
            instance=Profile.objects.get(pk=self.profile.pk),
        )
        self.assertTrue(form.is_valid(), form.errors)
        profile = form.save(commit=False)
        self.assertEqual(
            Profile.objects.get(pk=self.profile.pk).watched_expenses_title, "Watched expenses"
        )
        profile.save()
        form.save_m2m()
        self.assertEqual(list(profile.watched_expenses_categories.all()), [category])

    def test_saving_watched_expenses_replaces_and_clears_the_chosen_categories(self):
        """The partial save still saves the categories (a many-to-many),
        and not only adds."""
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

    def test_a_category_id_too_big_for_the_database_shows_the_same_error_not_a_server_error(self):
        """S106: SQLite refuses a number over 2**63 - 1 (an
        OverflowError, so a 500 page). A real category sent beside it is
        not saved either: the form is refused as a whole. (2**63 - 1
        itself is the boundary: it fits, so it never overflowed, and is
        just an unknown id.)"""
        water = Category.objects.create(name="Water", category_type=Category.CategoryType.EXPENSE)
        for too_big in ("9" * 30, str(2**63), str(2**63 - 1)):
            with self.subTest(too_big=too_big):
                response = self.client.post(
                    reverse("finance:save_watched_expenses"),
                    {
                        "watched_expenses_title": "Bills",
                        "watched_expenses_categories": [water.pk, too_big],
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "finance/settings.html")
                self.assertContains(response, "Select a valid choice")
                self.assertFalse(self.profile.watched_expenses_categories.exists())


class SafeModelMultipleChoiceFieldTests(StashTestCase):
    """S106 at the field itself (the reconcile page and Watched expenses
    both use it): an id too big for the database is refused like any
    other id that isn't a choice. Everything else the field does is
    still Django's own."""

    TOO_BIG = "9" * 30

    @classmethod
    def setUpTestData(cls):
        cls.water = Category.objects.create(
            name="Water", category_type=Category.CategoryType.EXPENSE
        )

    @staticmethod
    def field(required=False):
        return SafeModelMultipleChoiceField(queryset=Category.objects.all(), required=required)

    def refusal(self, ids, **kwargs):
        with self.assertRaises(ValidationError) as caught:
            self.field(**kwargs).clean(ids)
        return caught.exception

    def test_the_message_names_the_id_that_is_too_big(self):
        error = self.refusal([self.TOO_BIG])
        self.assertEqual(error.code, "invalid_choice")
        self.assertEqual(
            error.messages,
            [f"Select a valid choice. {self.TOO_BIG} is not one of the available choices."],
        )

    def test_the_real_id_beside_it_is_not_blamed_whichever_comes_first(self):
        blamed = [f"Select a valid choice. {self.TOO_BIG} is not one of the available choices."]
        for ids in ([str(self.water.pk), self.TOO_BIG], [self.TOO_BIG, str(self.water.pk)]):
            with self.subTest(ids=ids):
                self.assertEqual(self.refusal(ids).messages, blamed)

    def test_the_edges_of_what_the_database_can_hold_all_give_the_same_refusal(self):
        for pk in (str(2**63), str(-(2**63)), str(-(2**63) - 1), "-5", "0"):
            with self.subTest(pk=pk):
                error = self.refusal([pk])
                self.assertEqual(error.code, "invalid_choice")
                self.assertIn(f"{pk} is not one of the available choices", error.messages[0])

    def test_a_value_that_is_no_number_at_all_still_gets_the_usual_message(self):
        """Django's own message and code, untouched (a string of 5,000
        digits is one Python refuses to turn into a number)."""
        for ids in (["abc"], ["9" * 5000]):
            with self.subTest(ids=[value[:12] for value in ids]):
                self.assertEqual(self.refusal(ids).code, "invalid_pk_value")

    def test_a_too_big_id_beside_a_value_that_is_no_number_is_refused_either_way_round(self):
        """Which of the two is named depends on the order, and doesn't
        matter: it is refused."""
        for ids in (["abc", self.TOO_BIG], [self.TOO_BIG, "abc"]):
            with self.subTest(ids=ids):
                self.assertIn(self.refusal(ids).code, {"invalid_choice", "invalid_pk_value"})

    def test_it_asks_the_database_nothing_to_refuse_one(self):
        with self.assertNumQueries(0, using=settings.TEST_STASH):
            self.refusal([self.TOO_BIG, str(self.water.pk)])

    def test_valid_ids_give_their_rows_as_before(self):
        cleaned = self.field().clean([self.water.pk, str(self.water.pk)])
        self.assertEqual(list(cleaned), [self.water])

    def test_an_empty_list_gives_nothing_when_the_field_is_optional(self):
        self.assertEqual(list(self.field().clean([])), [])
        self.assertEqual(self.refusal([], required=True).code, "required")


class SettingsLayoutTests(AlexsStashTestCase):
    def test_shows_the_owner_possessive_under_the_heading(self):
        response = self.client.get(reverse("finance:settings"))
        self.assertContains(response, "You're in Alex&#x27;s stash.")

    def test_pin_and_recovery_code_card_links_to_their_own_pages(self):
        response = self.client.get(reverse("finance:settings"))
        self.assertContains(response, "PIN &amp; recovery code")
        self.assertContains(response, reverse("finance:change_pin"))
        self.assertContains(response, reverse("finance:recovery_code"))


class PinPageTestCase(AlexsStashTestCase):
    """Alex's stash with a real 4-digit PIN, which the Change PIN and
    recovery code pages ask for again (plan 03, S7)."""

    PIN = "1234"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user.set_password(cls.PIN)
        cls.user.save()

    def assert_locked_out_and_logged_out(self, response):
        """Out to the PIN screen, which says the stash is locked. The
        session still names the stash, so the PIN screen knows which
        one."""
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
        response = self.client.post(
            reverse("login"), {"username": settings.TEST_STASH, "password": self.PIN}
        )
        self.assertContains(response, "Too many wrong PINs")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_the_right_pin_with_a_mistyped_new_pin_changes_nothing_but_clears_earlier_tries(self):
        """The right current PIN shows it's the owner, so the earlier
        wrong tries are forgotten (as after logging in), even though the
        new PIN has to be typed again."""
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
        """It can't be the right PIN, and it's never checked against it,
        so it isn't a guess."""
        response = self.client.post(
            reverse("finance:change_pin"), {**self.WRONG, "current_pin": "12a4"}
        )
        self.assertContains(response, "PIN must be exactly 4 digits.")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.failed_pin_attempts, 0)


class RecoveryCodePageTests(PinPageTestCase):
    def generate(self, pin=None, **kwargs):
        data = {"current_pin": self.PIN if pin is None else pin}
        return self.client.post(reverse("finance:recovery_code"), data, **kwargs)

    def test_the_page_is_never_cached(self):
        # S80: so the browser's Back button can't show a code again.
        self.assertIn(
            "no-store", self.client.get(reverse("finance:recovery_code"))["Cache-Control"]
        )
        self.generate()
        shown = self.client.get(reverse("finance:recovery_code"))
        self.assertIn("no-store", shown["Cache-Control"])

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
        """Django would re-save the stored hash in a newer format when
        the PIN is checked. The login is tied to that hash, so the next
        page logged you out, and the new code (already replacing the old
        one) was never shown. Plan 03, S87: the PIN checks never save
        it."""
        with override_settings(PASSWORD_HASHERS=REHASHING_PASSWORD_HASHERS):
            response = self.generate(follow=True)
        self.assertContains(response, "Write this down now")
        self.assertNotContains(response, "This replaces your recovery code")

    def test_the_code_is_not_in_the_messages_banner(self):
        response = self.generate(follow=True)
        self.assertEqual(list(response.context["messages"]), [])

    def test_refreshing_afterwards_loses_the_code_and_shows_the_generate_warning_again(self):
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
        self.assert_the_last_wrong_pin_locks_out(
            reverse("finance:recovery_code"), {"current_pin": "0000"}
        )
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
        self.assertContains(
            response,
            '<label class="form-label" for="id_current_pin">Current PIN</label>',
            html=True,
        )
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
        """The every-page check in test_pages.py only sees the empty
        form; the page has two more states."""
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
    """Change PIN and the recovery code page count toward one lockout,
    so spreading the guesses over both pages gives no extra tries."""

    def test_wrong_pins_on_both_pages_add_up_to_one_lockout(self):
        pages = [reverse("finance:change_pin"), reverse("finance:recovery_code")]
        for attempt in range(security.LOCKOUT_THRESHOLD - 1):
            self.assertEqual(
                self.client.post(pages[attempt % 2], ChangePinPageTests.WRONG).status_code, 200
            )
        self.assert_locked_out_and_logged_out(self.client.post(pages[1], ChangePinPageTests.WRONG))

    def test_a_request_with_an_old_copy_of_the_profile_still_counts_toward_the_lockout(self):
        """Plan 03, S75, through the real pages: the saved row is one
        try short of the lockout, but the request's own copy (read
        before the stash's write lock) says no tries yet. The try must
        still count, or it goes missing and the lockout never comes."""
        pages = {
            "Change PIN": (reverse("finance:change_pin"), ChangePinPageTests.WRONG),
            "recovery code": (reverse("finance:recovery_code"), {"current_pin": "0000"}),
        }
        for name, (url, data) in pages.items():
            with self.subTest(page=name):
                self.client.force_login(self.user)
                Profile.objects.filter(pk=self.profile.pk).update(
                    failed_pin_attempts=0, locked_until=None
                )
                old_copy = Profile.objects.get(pk=self.profile.pk)
                Profile.objects.filter(pk=self.profile.pk).update(
                    failed_pin_attempts=security.LOCKOUT_THRESHOLD - 1
                )
                with mock.patch.object(Profile, "for_user", return_value=old_copy):
                    response = self.client.post(url, data)
                self.assert_locked_out_and_logged_out(response)


class RecoveryCodeNotInCookieTests(PinPageTestCase):
    """1.14 (F45): a new recovery code never travels in the session
    cookie."""

    def test_the_code_is_shown_once_and_never_stored_in_the_session(self):
        with mock.patch.object(
            security, "regenerate_recovery_code", return_value="ABCD-EFGH-JKLM"
        ):
            self.client.post(reverse("finance:recovery_code"), {"current_pin": self.PIN})
        self.assertNotIn("ABCD-EFGH-JKLM", str(dict(self.client.session)))

        first = self.client.get(reverse("finance:recovery_code"))
        second = self.client.get(reverse("finance:recovery_code"))

        self.assertContains(first, "ABCD-EFGH-JKLM")
        self.assertNotContains(second, "ABCD-EFGH-JKLM")


class RecoveryCodeIsTakenOnceTests(PinPageTestCase):
    """3.6 (S42): reading the new code is one function that pops the
    session's token and deletes the cache entry. Sessions are signed
    cookies, so popping the token only changes this browser's copy of
    the cookie: it is the cache delete that makes the code show-once."""

    CODE = "ABCD-EFGH-JKLM"

    def generate_the_known_code(self):
        with mock.patch.object(security, "regenerate_recovery_code", return_value=self.CODE):
            self.client.post(reverse("finance:recovery_code"), {"current_pin": self.PIN})
        return self.client.session["new_recovery_code_token"]

    def put_token_in_session(self, token):
        session = self.client.session
        session["new_recovery_code_token"] = token
        session.save()
        self.client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key

    def test_the_cache_entry_is_deleted_as_soon_as_the_code_is_shown(self):
        key = _recovery_code_cache_key(self.generate_the_known_code())
        self.assertEqual(cache.get(key), self.CODE)

        self.client.get(reverse("finance:recovery_code"))

        self.assertIsNone(cache.get(key))
        self.assertNotIn("new_recovery_code_token", self.client.session)

    def test_an_older_copy_of_the_session_cannot_show_the_code_again(self):
        token = self.generate_the_known_code()
        self.client.get(reverse("finance:recovery_code"))
        self.put_token_in_session(token)

        replay = self.client.get(reverse("finance:recovery_code"))

        self.assertNotContains(replay, self.CODE)
        self.assertContains(replay, "This replaces your recovery code")

    def test_a_token_with_nothing_behind_it_shows_no_code_and_is_dropped(self):
        self.put_token_in_session("a-token-that-expired")

        response = self.client.get(reverse("finance:recovery_code"))

        self.assertNotContains(response, "Write this down now")
        self.assertContains(response, "This replaces your recovery code")
        self.assertNotIn("new_recovery_code_token", self.client.session)
