"""Plan 02, Phase 5 (split the views module): login by default, one view for every report
export, Settings' separate form endpoints, and the middlewares' exempt URLs."""

import csv
import io
import re
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.urls import URLPattern, resolve, reverse

from . import urls as finance_urls
from .currency import RateFetchError
from .middleware import RequireSetupMiddleware, RequireStashMiddleware
from .models import Category, Profile, Transaction
from .test_cleanup_phase1 import LoggedInTestCase
from .views.exports import REPORT_EXPORTS

# Values for the URL parameters, so every pattern in finance/urls.py can be reversed.
SAMPLE_KWARGS = {"pk": 1, "step": 1, "report": "spending"}


def _every_finance_url():
    for pattern in finance_urls.urlpatterns:
        assert isinstance(pattern, URLPattern)
        kwargs = {name: SAMPLE_KWARGS[name] for name in pattern.pattern.converters}
        yield pattern.name, reverse(f"finance:{pattern.name}", kwargs=kwargs)


class LoginByDefaultTests(TestCase):
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


class MiddlewareExemptPrefixesTests(SimpleTestCase):
    """5.6: the prefixes come from the URL names and settings, and match the old strings."""

    def test_require_stash_exempts_the_picker_static_files_and_the_admin(self):
        self.assertEqual(RequireStashMiddleware.exempt_prefixes(), ("/stashes/", "/static/", "/admin/"))

    def test_require_setup_exempts_the_wizard_static_files_the_admin_and_logout(self):
        self.assertEqual(
            RequireSetupMiddleware.exempt_prefixes(), ("/welcome/", "/static/", "/admin/", "/accounts/logout/")
        )


class ReportExportTests(LoggedInTestCase):
    """5.2 (F15, F4): one registry and one view for every report export."""

    def setUp(self):
        super().setUp()
        self.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=self.account, date=date.today(), category=self.groceries, amount=Decimal("-42.00")
        )

    def _csv(self, slug, **params):
        response = self.client.get(reverse("finance:export_report", args=[slug]), {"format": "csv", **params})
        return response, list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))

    def test_the_urls_are_the_same_as_before_the_merge(self):
        for slug in REPORT_EXPORTS:
            with self.subTest(slug=slug):
                self.assertEqual(reverse("finance:export_report", args=[slug]), f"/reports/export/{slug}/")

    def test_every_report_exports_as_csv_and_xlsx(self):
        profile = Profile.for_user(self.user)
        profile.watched_expenses_categories.add(self.groceries)
        for slug in REPORT_EXPORTS:
            for fmt, content_type in (("csv", "text/csv"), ("xlsx", "spreadsheetml")):
                with self.subTest(slug=slug, format=fmt):
                    response = self.client.get(reverse("finance:export_report", args=[slug]), {"format": fmt})
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(content_type, response["Content-Type"])
                    self.assertIn(f"stashy-{REPORT_EXPORTS[slug].filename}-", response["Content-Disposition"])

    def test_an_unknown_report_is_a_404(self):
        response = self.client.get(reverse("finance:export_report", args=["no-such-report"]))
        self.assertEqual(response.status_code, 404)

    def test_watched_expenses_without_categories_explains_itself(self):
        response = self.client.get(reverse("finance:export_report", args=["watched-expenses"]))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "add some in Settings", status_code=404)

    def test_each_layout_keeps_its_columns(self):
        _response, rows = self._csv("income-expense")
        self.assertEqual(rows[0], ["Category", "Amount"])
        _response, rows = self._csv("spending-vs-average")
        self.assertEqual(rows[0], ["Category", "Current", "Average", "Difference"])
        _response, rows = self._csv("net-worth")
        self.assertEqual(rows[0][0], "Account")
        self.assertEqual(rows[-1][0], "Total")

    def test_every_export_link_on_the_reports_page_works(self):
        Profile.for_user(self.user).watched_expenses_categories.add(self.groceries)
        page = self.client.get(reverse("finance:reports"), {"subtotal": "month"}).content.decode()
        links = set(re.findall(r'href="(/reports/export/[^"]+)"', page))
        slugs = {re.match(r"/reports/export/([^/]+)/", link).group(1) for link in links}
        self.assertEqual(slugs, set(REPORT_EXPORTS))
        for link in links:
            with self.subTest(link=link):
                response = self.client.get(link.replace("&amp;", "&"))
                self.assertEqual(response.status_code, 200)
                self.assertIn("subtotal=month", link)


class SettingsEndpointsTests(LoggedInTestCase):
    """5.3 (F16): each form on Settings posts to its own URL."""

    def setUp(self):
        super().setUp()
        self.profile = Profile.for_user(self.user)

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


class TipsMovedOutOfTheViewsTests(LoggedInTestCase):
    """5.4: the help-card tips come from finance/content.py and still show."""

    def test_the_add_account_page_still_shows_its_tips(self):
        response = self.client.get(reverse("finance:account_add"))
        self.assertContains(response, "Institution is optional")

    def test_the_import_page_still_shows_its_tips(self):
        response = self.client.get(reverse("finance:import_transactions"))
        self.assertContains(response, "Detect automatically")


class RenderCheckTests(LoggedInTestCase):
    """5.5: every page renders without leaked template syntax."""

    LEAKS = ("{#", "#}", "{%", "%}")

    def test_no_page_leaks_template_syntax(self):
        category = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(account=self.account, date=date.today(), category=category, amount=Decimal("-5"))
        Profile.for_user(self.user).watched_expenses_categories.add(category)
        pages = [
            reverse("finance:dashboard"),
            reverse("finance:account_list"),
            reverse("finance:account_detail", args=[self.account.pk]),
            reverse("finance:category_list"),
            reverse("finance:payee_list"),
            reverse("finance:transaction_list"),
            reverse("finance:transaction_add"),
            reverse("finance:reminder_list"),
            reverse("finance:reminder_add"),
            reverse("finance:import_transactions"),
            reverse("finance:reports"),
            reverse("finance:settings"),
            reverse("finance:change_pin"),
            reverse("finance:recovery_code"),
        ]
        for url in pages:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                for leak in self.LEAKS:
                    self.assertNotIn(leak, body)
