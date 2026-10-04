"""Checks that hold for every page: no leaked template syntax, nothing loaded from the
internet, the error pages, the sidebar, and the help-card tips."""

import re
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.contrib.staticfiles import finders
from django.test import override_settings
from django.urls import path as url_path
from django.urls import reverse

from ..models import Category, Profile, Transaction
from .base import LoggedInWithAccountTestCase, StashTestCase


# Public, so the crash happens for an anonymous client too: this URLconf has no login page
# for LoginRequiredMiddleware to redirect to.
@login_not_required
def _broken_view(request):
    raise RuntimeError("deliberate failure for the 500 page test")


urlpatterns = [url_path("broken/", _broken_view)]

LEAKED_TEMPLATE_SYNTAX = ("{%", "%}", "{#", "#}")


class ErrorPageTests(StashTestCase):
    """Tests always run with DEBUG off, the way Stashy.bat now runs too."""

    def test_a_missing_page_shows_the_friendly_404(self):
        response = self.client.get("/no-such-page/")

        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, "404.html")
        content = response.content.decode()
        self.assertIn("Nothing stashed here", content)
        self.assertIn("stashy.css", content)
        for syntax in LEAKED_TEMPLATE_SYNTAX:
            self.assertNotIn(syntax, content)

    @override_settings(ROOT_URLCONF=__name__)
    def test_a_crash_shows_the_friendly_500_and_logs_the_traceback(self):
        self.client.raise_request_exception = False

        with self.assertLogs("django.request", level="ERROR") as logs:
            response = self.client.get("/broken/")

        self.assertEqual(response.status_code, 500)
        content = response.content.decode()
        self.assertIn("Something went wrong", content)
        self.assertIn(r"logs\stashy.log", content)
        for syntax in LEAKED_TEMPLATE_SYNTAX:
            self.assertNotIn(syntax, content)
        self.assertIn("deliberate failure for the 500 page test", "\n".join(logs.output))


class RenderCheckTests(LoggedInWithAccountTestCase):
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


class TipsMovedOutOfTheViewsTests(LoggedInWithAccountTestCase):
    """5.4: the help-card tips come from finance/content.py and still show."""

    def test_the_add_account_page_still_shows_its_tips(self):
        response = self.client.get(reverse("finance:account_add"))
        self.assertContains(response, "Institution is optional")

    def test_the_import_page_still_shows_its_tips(self):
        response = self.client.get(reverse("finance:import_transactions"))
        self.assertContains(response, "Detect automatically")


class NoLeakedTemplateSyntaxTests(LoggedInWithAccountTestCase):
    """Regression test: a multi-line {# ... #} Django comment isn't valid - {# #} only matches
    within a single line (no re.DOTALL), so a comment spanning several lines silently falls
    through as literal page text instead of being stripped. Bit `includes/report_card_header.html`
    and `includes/category_checkbox_filter.html` this way (15/09/2026) - every report card and
    the Settings "Watched expenses" picker showed a paragraph of raw comment text. Guards every
    page these two includes touch against the same mistake reappearing."""

    def test_reports_page_has_no_leaked_comment_syntax(self):
        response = self.client.get(reverse("finance:reports"))
        body = response.content.decode()
        self.assertNotIn("{#", body)
        self.assertNotIn("#}", body)

    def test_settings_page_has_no_leaked_comment_syntax(self):
        response = self.client.get(reverse("finance:settings"))
        body = response.content.decode()
        self.assertNotIn("{#", body)
        self.assertNotIn("#}", body)

    def test_transaction_list_page_has_no_leaked_comment_syntax(self):
        response = self.client.get(reverse("finance:transaction_list"))
        body = response.content.decode()
        self.assertNotIn("{#", body)
        self.assertNotIn("#}", body)


class OfflineAndThemeTests(LoggedInWithAccountTestCase):
    EXTERNAL_ASSET = re.compile(r'(?:src|href)="https?://')

    def test_pages_load_nothing_from_the_internet(self):
        urls = [
            reverse(name)
            for name in (
                "finance:dashboard",
                "finance:transaction_list",
                "finance:account_add",
                "finance:reports",
                "finance:settings",
                "finance:category_list",
                "finance:category_add",
                "finance:payee_list",
            )
        ]
        urls.append(reverse("finance:account_detail", args=[self.account.pk]))
        for url in urls:
            content = self.client.get(url).content.decode()
            self.assertIsNone(self.EXTERNAL_ASSET.search(content), url)
            self.assertIn("data-theme-toggle", content)
        self.client.logout()
        self.client.choose_stash(settings.TEST_STASH)  # logging out forgets the stash too
        login = self.client.get(reverse("login")).content.decode()
        self.assertIsNone(self.EXTERNAL_ASSET.search(login))
        self.assertIn("mascot-full-720.webp", login)

    def test_every_referenced_static_file_exists(self):
        for path in (
            "finance/css/stashy.css",
            "finance/js/stashy.js",
            "finance/js/charts.js",
            "finance/js/dashboard.js",
            "finance/vendor/bootstrap/css/bootstrap.min.css",
            "finance/vendor/bootstrap/js/bootstrap.bundle.min.js",
            "finance/vendor/bootstrap-icons/bootstrap-icons.min.css",
            "finance/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2",
            "finance/vendor/chartjs/chart.umd.min.js",
            "finance/vendor/flatpickr/flatpickr.min.js",
            "finance/vendor/fonts/nunito/nunito-cyrillic-wght-normal.woff2",
            "finance/vendor/fonts/manrope/manrope-latin-wght-normal.woff2",
            "finance/img/stashy-icon-32.png",
            "finance/img/stashy-icon-180.png",
            "finance/img/stashy-icon-192.png",
            "finance/img/mascot-face-96.png",
            "finance/img/mascot-face-192.png",
            "finance/img/mascot-full-720.webp",
        ):
            with self.subTest(path=path):
                self.assertIsNotNone(finders.find(path))

    def test_form_pages_show_tips_beside_the_form(self):
        response = self.client.get(reverse("finance:account_add"))
        self.assertContains(response, "help-card")
        self.assertContains(response, "Untick Is active instead of deleting it")


class SidebarAdminLinkTests(LoggedInWithAccountTestCase):
    def test_admin_link_is_relabeled_and_opens_a_new_tab(self):
        response = self.client.get(reverse("finance:dashboard"))
        html = response.content.decode()
        self.assertIn("Admin &amp; Audit Log", html)
        self.assertNotIn("Admin &amp; audit log", html)
        self.assertIn('target="_blank"', html)
