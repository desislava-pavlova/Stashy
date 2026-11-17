"""Checks that hold for every page: no leaked template syntax, nothing loaded from the
internet, the error pages, the sidebar, and the help-card tips."""

import re
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_not_required
from django.contrib.staticfiles import finders
from django.test import Client, override_settings
from django.urls import path as url_path
from django.urls import reverse
from django.utils import timezone

from ..models import Category, Payee, Profile, Reminder, Transaction
from .base import (
    LEAKED_TEMPLATE_SYNTAX,
    LoggedInWithAccountTestCase,
    StashTestCase,
    every_finance_url,
    leaked_syntax_in,
)


# Public, so the crash happens for an anonymous client too: this URLconf has no login page
# for LoginRequiredMiddleware to redirect to.
@login_not_required
def _broken_view(request):
    raise RuntimeError("deliberate failure for the 500 page test")


urlpatterns = [url_path("broken/", _broken_view)]


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
    """Every page renders without leaked template syntax."""

    # The URL names in finance/urls.py that are not an HTML page you open with a plain GET.
    # Every other name must render. A new URL name has to be in one of the two groups, so a
    # new page can't skip this check.
    NOT_A_PAGE = frozenset(
        {
            # JSON.
            "api_payee_autofill",
            "api_payee_list",
            # A file.
            "export_accounts",
            "export_categories",
            "export_payees",
            "export_report",
            "export_transactions",
            # The restore page needs a real backup file; test_backup.py visits it.
            "backup_restore",
            # They only take POST and answer with a redirect or JSON.
            "check_rates",
            "create_backup",
            "reminder_skip",
            "save_watched_expenses",
            "transaction_quick_edit",
            # A finished stash is sent away from the wizard; the next test visits its steps.
            "welcome_step",
        }
    )

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.category = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        cls.payee = Payee.objects.create(name="Corner Shop")
        cls.transaction = Transaction.objects.create(
            account=cls.account,
            date=timezone.localdate(),
            category=cls.category,
            payee=cls.payee,
            amount=Decimal("-5"),
        )
        cls.reminder = Reminder.objects.create(account=cls.account, next_due_date=timezone.localdate())
        cls.recurring = Reminder.objects.create(
            account=cls.account, next_due_date=timezone.localdate(), frequency="monthly", end_date=date(2099, 12, 31)
        )
        Profile.for_user(cls.user).watched_expenses_categories.add(cls.category)

    def pk_of(self, url_name):
        """The id of the row this URL name is about (`account_edit` -> the account's)."""
        rows = {
            "account": self.account,
            "category": self.category,
            "payee": self.payee,
            "transaction": self.transaction,
            "reminder": self.reminder,
        }
        return rows[url_name.split("_")[0]].pk

    def test_no_page_leaks_template_syntax(self):
        not_pages = set()
        for name, url in every_finance_url(self.pk_of):
            with self.subTest(name=name):
                response = self.client.get(url)
                if response.status_code != 200 or "text/html" not in response["Content-Type"]:
                    not_pages.add(name)
                    continue
                self.assertEqual(leaked_syntax_in(response.content.decode()), [])
        self.assertEqual(sorted(not_pages), sorted(self.NOT_A_PAGE))

    def test_the_pages_you_only_reach_by_a_choice_or_a_post_dont_leak_either(self):
        edit_url = reverse("finance:reminder_edit", args=[self.recurring.pk])
        reconcile_url = reverse("finance:account_reconcile", args=[self.account.pk])
        pages = {
            "the reminder's scope question": self.client.get(edit_url),
            "editing only this occurrence": self.client.get(edit_url, {"scope": "this"}),
            "editing the whole series": self.client.get(edit_url, {"scope": "series"}),
            "reconcile's checklist": self.client.post(
                reconcile_url, {"statement_date": "31/01/2026", "statement_balance": "-5.00"}
            ),
        }
        for name, response in pages.items():
            with self.subTest(page=name):
                self.assertEqual(response.status_code, 200)
                self.assertEqual(leaked_syntax_in(response.content.decode()), [])

    def test_no_welcome_step_leaks_template_syntax(self):
        profile = Profile.for_user(self.user)
        for step in range(1, Profile.WELCOME_STEPS_DONE + 1):
            with self.subTest(step=step):
                profile.welcome_step = step - 1  # the wizard shows the step after the last finished one
                profile.save(update_fields=["welcome_step"])
                response = self.client.get(reverse("finance:welcome_step", args=[step]))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(leaked_syntax_in(response.content.decode()), [])


class PublicPagesRenderCheckTests(StashTestCase):
    """The pages you reach before logging in render without leaked template syntax too."""

    # The stash picker runs before any stash is chosen, on `default`, the empty placeholder.
    databases = {"default", settings.TEST_STASH}

    @classmethod
    def setUpTestData(cls):
        # The stash's own user and Profile, which the PIN pages look for.
        Profile.for_user(get_user_model().objects.create_user(settings.TEST_STASH, password="1234"))

    def test_no_public_page_leaks_template_syntax(self):
        # A fresh browser hasn't chosen a stash yet; self.client has (see base.py).
        pages = {
            "stash_picker": Client().get(reverse("stash_picker")),
            "stash_create": Client().get(reverse("stash_create")),
            "login": self.client.get(reverse("login")),
            "forgot_pin": self.client.get(reverse("forgot_pin")),
        }
        for name, response in pages.items():
            with self.subTest(page=name):
                self.assertEqual(response.status_code, 200)
                self.assertEqual(leaked_syntax_in(response.content.decode()), [])


class HelpCardTipsTests(LoggedInWithAccountTestCase):
    """The help-card tips (finance/content.py) show beside the forms."""

    def test_the_add_account_page_shows_its_tips(self):
        response = self.client.get(reverse("finance:account_add"))
        self.assertContains(response, "help-card")
        self.assertContains(response, "Institution is optional")
        self.assertContains(response, "Untick Is active instead of deleting it")

    def test_the_import_page_shows_its_tips(self):
        response = self.client.get(reverse("finance:import_transactions"))
        self.assertContains(response, "Detect automatically")


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


class SidebarAdminLinkTests(LoggedInWithAccountTestCase):
    def test_admin_link_is_relabeled_and_opens_a_new_tab(self):
        response = self.client.get(reverse("finance:dashboard"))
        html = response.content.decode()
        self.assertIn("Admin &amp; Audit Log", html)
        self.assertIn('target="_blank"', html)
