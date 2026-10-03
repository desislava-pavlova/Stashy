"""Plan 02, Phase 1 (data safety and wrong numbers): one stash's transaction per request,
SQLite-API backups and restore, the reconcile finish form, POST-only stash switching, currency
conversion and refunds on the dashboard, import errors, safe exports, the admin login, the
recovery code kept out of the cookie, and the open redirect."""

import csv
import io
import sqlite3
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connections
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from openpyxl import load_workbook

from . import backup as finance_backup
from . import db_router
from .middleware import StashAtomicRequestMiddleware
from .models import Account, Category, ExchangeRate, ImportBatch, Payee, Profile, Transaction
from .reporting import income_and_spending, spending_tree
from .services.importing import _parse_transaction_number
from .stashes import registry, security
from .views._helpers import _safe_next


class TemporaryStashesMixin:
    """Real stash aliases backed by files in a temporary folder - registered at runtime the way
    the live app does it, and removed again afterwards."""

    def make_stashes(self, *names):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(registry, "stashes_dir", return_value=Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in names:
            registry.stash_path(name).mkdir(parents=True)
            sqlite3.connect(registry.stash_db_path(name)).close()
            registry.ensure_registered(name)
            self.addCleanup(self._unregister, name)
        self.addCleanup(db_router.clear_current_stash_alias)
        # Django's test runner only lets a test touch the databases it lists. These aliases
        # don't exist until now, so they're added for this one test.
        test_class = type(self)
        self.addCleanup(setattr, test_class, "databases", test_class.databases)
        test_class.databases = frozenset(test_class.databases) | set(names)

    @staticmethod
    def _unregister(name):
        connections[name].close()
        del connections[name]
        del connections.databases[name]


class StashAtomicRequestTests(TemporaryStashesMixin, SimpleTestCase):
    """1.1/1.2 (F1): only the stash the request uses gets a transaction."""

    def setUp(self):
        self.make_stashes("alpha", "beta")
        self.middleware = StashAtomicRequestMiddleware(lambda request: HttpResponse())
        self.request = RequestFactory().get("/")

    def test_registered_stashes_never_get_djangos_per_request_transaction(self):
        self.assertFalse(connections.settings["alpha"]["ATOMIC_REQUESTS"])
        self.assertFalse(connections.settings["beta"]["ATOMIC_REQUESTS"])

    def test_only_the_active_stash_is_in_a_transaction_during_the_view(self):
        seen = {}

        def view(request):
            seen["alpha_atomic"] = connections["alpha"].in_atomic_block
            seen["beta_connected"] = connections["beta"].connection is not None
            seen["beta_atomic"] = connections["beta"].in_atomic_block
            return HttpResponse("ok")

        db_router.set_current_stash_alias("alpha")
        response = self.middleware.process_view(self.request, view, (), {})

        self.assertEqual(response.content, b"ok")
        self.assertEqual(seen, {"alpha_atomic": True, "beta_connected": False, "beta_atomic": False})
        self.assertFalse(connections["alpha"].in_atomic_block)

    def test_an_error_in_the_view_rolls_back_its_changes(self):
        with connections["alpha"].cursor() as cursor:
            cursor.execute("CREATE TABLE note (content TEXT)")

        def view(request):
            with connections["alpha"].cursor() as cursor:
                cursor.execute("INSERT INTO note VALUES ('half-done')")
            raise ValueError("something broke")

        db_router.set_current_stash_alias("alpha")
        with self.assertRaises(ValueError):
            self.middleware.process_view(self.request, view, (), {})
        with connections["alpha"].cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM note")
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_a_non_atomic_view_runs_without_a_transaction(self):
        from django.db import transaction

        @transaction.non_atomic_requests
        def view(request):
            return HttpResponse(str(connections["alpha"].in_atomic_block))

        db_router.set_current_stash_alias("alpha")
        self.assertEqual(self.middleware.process_view(self.request, view, (), {}).content, b"False")

    def test_without_a_stash_django_handles_the_request_as_usual(self):
        self.assertIsNone(self.middleware.process_view(self.request, lambda request: HttpResponse(), (), {}))

    def test_it_runs_after_every_other_view_middleware(self):
        self.assertEqual(settings.MIDDLEWARE[-1], "finance.middleware.StashAtomicRequestMiddleware")


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


class OnCommitUsesTheStashTests(LoggedInTestCase):
    """1.1 (F42): the audit log's after-commit step waits on the stash's own database."""

    def test_cleared_references_wait_for_the_routed_database(self):
        payee = Payee.objects.create(name="Corner shop")
        Transaction.objects.create(account=self.account, date=date(2026, 9, 1), payee=payee, amount=Decimal("-5"))
        with (
            mock.patch("audit.signals.router.db_for_write", return_value="default") as db_for_write,
            mock.patch("audit.signals.transaction.on_commit") as on_commit,
        ):
            payee.delete()
        db_for_write.assert_called()
        self.assertEqual(on_commit.call_args.kwargs["using"], "default")


class RestoreBringsBackTodaysSchemaTests(TemporaryStashesMixin, SimpleTestCase):
    """1.3 (F44): a backup taken before an update is migrated as soon as it's restored."""

    def test_restoring_a_pre_update_backup_migrates_it(self):
        self.make_stashes("restorecheck")
        call_command("migrate", "finance", "0016", database="restorecheck", verbosity=0)
        old_backup = finance_backup.backup_now(
            registry.stash_db_path("restorecheck"), registry.stash_path("restorecheck"), tag="before_update"
        )
        call_command("migrate", database="restorecheck", verbosity=0)

        registry.restore_stash("restorecheck", old_backup)

        with connections["restorecheck"].cursor() as cursor:
            columns = [
                c.name
                for c in connections["restorecheck"].introspection.get_table_description(cursor, "finance_profile")
            ]
        self.assertIn("owner_name", columns)  # added by migration 0017, after the backup


class ReconcileFinishFormTests(LoggedInTestCase):
    """1.6 (F5): a garbled finish step gets a message, never a server error."""

    def setUp(self):
        super().setUp()
        self.txn = Transaction.objects.create(account=self.account, date=date(2026, 9, 1), amount=Decimal("-20.00"))
        self.url = reverse("finance:account_reconcile", args=[self.account.pk])

    def finish(self, **data):
        return self.client.post(self.url, {"stage": "finish", **data})

    def test_missing_or_garbled_values_restart_with_a_message(self):
        for data in (
            {},
            {"statement_date": "yesterday", "statement_balance": "-20.00"},
            {"statement_date": "2026-09-30", "statement_balance": "lots"},
            {"statement_date": "2026-09-30", "statement_balance": "-20.00", "transaction": "abc"},
        ):
            with self.subTest(data=data):
                response = self.finish(**data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "didn&#x27;t come through")
        self.txn.refresh_from_db()
        self.assertFalse(self.txn.reconciled)

    def test_a_valid_finish_still_reconciles(self):
        response = self.finish(statement_date="2026-09-30", statement_balance="-20.00", transaction=self.txn.pk)
        self.assertRedirects(response, reverse("finance:account_detail", args=[self.account.pk]))
        self.txn.refresh_from_db()
        self.assertTrue(self.txn.reconciled)


class SwitchStashIsPostOnlyTests(TestCase):
    """1.7 (F6): a link or image on another site can't log you out."""

    def test_get_is_refused(self):
        self.assertEqual(self.client.get(reverse("switch_stash")).status_code, 405)

    def test_post_returns_to_the_picker(self):
        self.assertRedirects(
            self.client.post(reverse("switch_stash")), reverse("stash_picker"), fetch_redirect_response=False
        )

    def test_the_pin_screen_offers_it_as_a_form_button(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, f'<form method="post" action="{reverse("switch_stash")}"', html=False)
        self.assertNotContains(response, f'href="{reverse("switch_stash")}"')
        for leaked in ("{#", "#}", "{%", "%}"):
            self.assertNotContains(response, leaked)


class DashboardCurrencyTests(LoggedInTestCase):
    """1.8 (F30): the dashboard converts into the main currency, like Reports."""

    def test_spending_in_another_currency_is_converted(self):
        today = date.today()
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        usd = Account.objects.create(name="US card", currency="USD")
        ExchangeRate.objects.create(base_currency="USD", quote_currency="EUR", rate=Decimal("0.5"), date=today)
        Transaction.objects.create(account=self.account, date=today, category=groceries, amount=Decimal("-10.00"))
        Transaction.objects.create(account=usd, date=today, category=groceries, amount=Decimal("-100.00"))

        response = self.client.get(reverse("finance:dashboard"))

        self.assertEqual(response.context["main_currency"], "EUR")
        self.assertEqual(response.context["month_spending"], Decimal("60.00"))
        self.assertEqual(response.context["spending_tree"][0]["total"], 60.0)


class SpendingTreeRefundTests(TestCase):
    """1.9 (F31): every category is its own net total; a bigger refund shows as negative."""

    def setUp(self):
        self.account = Account.objects.create(name="Checking")
        expense = Category.CategoryType.EXPENSE
        self.groceries = Category.objects.create(name="Groceries", category_type=expense)
        self.rent = Category.objects.create(name="Rent", category_type=expense)

    def add(self, amount, category):
        Transaction.objects.create(
            account=self.account, date=date(2026, 9, 10), category=category, amount=Decimal(amount)
        )

    def test_a_refund_bigger_than_the_spend_is_negative_and_nets_the_total(self):
        self.add("-40.00", self.groceries)
        self.add("100.00", self.groceries)
        self.add("-500.00", self.rent)

        tree = spending_tree(date(2026, 9, 1), date(2026, 9, 30))

        self.assertEqual([(g["name"], g["total"]) for g in tree], [("Rent", 500.0), ("Groceries", -60.0)])
        self.assertEqual(tree[1]["children"][0]["total"], -60.0)
        spending = income_and_spending(date(2026, 9, 1), date(2026, 9, 30))["spending"]
        self.assertEqual(Decimal(str(sum(g["total"] for g in tree))), spending)

    def test_a_subcategory_refund_nets_inside_its_parent(self):
        organic = Category.objects.create(
            name="Organic", parent=self.groceries, category_type=Category.CategoryType.EXPENSE
        )
        self.add("-30.00", self.groceries)
        self.add("10.00", organic)
        group = spending_tree(date(2026, 9, 1), date(2026, 9, 30))[0]
        self.assertEqual(group["total"], 20.0)
        self.assertEqual([c["total"] for c in group["children"]], [30.0, -10.0])


class ImportErrorTests(LoggedInTestCase):
    """1.10 (F32, F47): unreadable files get a message; skipped rows are counted."""

    def upload(self, name, content):
        return self.client.post(
            reverse("finance:import_transactions"),
            {"account": self.account.pk, "file": SimpleUploadedFile(name, content)},
            follow=True,
        )

    def test_a_windows_1251_csv_is_read_and_says_so(self):
        content = "Date,Payee,Memo,Amount\n03/09/2026,Магазин,,-5.00\n".encode("cp1251")
        response = self.upload("bank.csv", content)
        self.assertContains(response, "read as Windows-1251")
        self.assertEqual(Transaction.objects.get().payee.name, "Магазин")

    def test_a_damaged_excel_file_gets_a_message_and_writes_nothing(self):
        response = self.upload("bank.xlsx", b"not really a spreadsheet")
        self.assertContains(response, "couldn&#x27;t open this Excel file")
        self.assertFalse(ImportBatch.objects.exists())

    def test_unreadable_rows_are_counted_and_blank_lines_are_not(self):
        content = b"Date,Payee,Memo,Amount\n03/09/2026,Shop,,-5.00\nsomeday,Shop,,-5.00\n04/09/2026,Shop,,lots\n,,,\n"
        response = self.upload("bank.csv", content)
        self.assertContains(
            response, "Imported 1 transaction (0 duplicates skipped). 2 rows skipped - unreadable date or amount."
        )

    def test_a_bad_num_becomes_no_number_instead_of_a_crash(self):
        content = b"Date,Num,Payee,Amount\n03/09/2026,-7,Shop,-1.00\n04/09/2026,inf,Shop,-2.00\n"
        self.upload("bank.csv", content)
        self.assertEqual(Transaction.objects.count(), 2)

    def test_parse_transaction_number(self):
        cases = {
            "12": 12,
            "12.0": 12,
            " 7 ": 7,
            "": None,
            "-7": None,
            "inf": None,
            "nan": None,
            "12.5": None,
            "abc": None,
            "99999999999": None,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(_parse_transaction_number(raw), expected)


class ExportTests(LoggedInTestCase):
    """1.11 (F33, F46): exports open correctly in Excel and can't run formulas."""

    def setUp(self):
        super().setUp()
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 9, 1),
            category=rent,
            payee=Payee.objects.create(name="Магазин"),
            memo='=HYPERLINK("http://evil.example")',
            amount=Decimal("-500.00"),
        )

    def export(self, fmt):
        return self.client.get(reverse("finance:export_transactions"), {"format": fmt})

    def test_csv_starts_with_a_bom_and_escapes_formulas_but_not_amounts(self):
        content = self.export("csv").content.decode("utf-8")
        self.assertTrue(content.startswith("﻿"))
        row = list(csv.reader(io.StringIO(content.lstrip("﻿"))))[1]
        self.assertEqual(row[3], "Магазин")
        self.assertEqual(row[5], '\'=HYPERLINK("http://evil.example")')
        self.assertEqual(row[6], "-500.00")

    def test_xlsx_has_real_numbers_and_dates_and_formulas_stay_text(self):
        sheet = load_workbook(io.BytesIO(self.export("xlsx").content)).active
        date_cell, amount_cell, memo_cell = sheet["A2"], sheet["G2"], sheet["F2"]
        self.assertEqual(date_cell.value, datetime(2026, 9, 1))
        self.assertEqual(date_cell.number_format, "DD/MM/YYYY")
        self.assertEqual(amount_cell.value, -500)
        self.assertEqual(amount_cell.number_format, "0.00")
        self.assertEqual(memo_cell.data_type, "s")
        self.assertEqual(memo_cell.value, '=HYPERLINK("http://evil.example")')


class AdminLoginTests(TestCase):
    """1.12 (F41): the admin's own login page can't be used to guess a PIN."""

    def test_admin_login_sends_you_to_the_pin_screen_with_next(self):
        response = self.client.get("/admin/login/", {"next": "/admin/"})
        self.assertRedirects(response, f"{reverse('login')}?next=%2Fadmin%2F", fetch_redirect_response=False)

    def test_posting_a_pin_there_logs_nobody_in(self):
        get_user_model().objects.create_superuser("test", password="1234")
        response = self.client.post("/admin/login/", {"username": "test", "password": "1234"})
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_the_admin_itself_still_redirects_to_a_login(self):
        response = self.client.get("/admin/")
        self.assertRedirects(response, "/admin/login/?next=/admin/", fetch_redirect_response=False)


class RecoveryCodeNotInCookieTests(LoggedInTestCase):
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


class SafeNextTests(SimpleTestCase):
    """1.15 (F55): only pages on this site are allowed as a "next" destination."""

    def test_off_site_destinations_are_refused(self):
        request = RequestFactory().get("/")
        for url in ("/\\evil.com", "//evil.com", "http://evil.com/", "https://evil.com", "javascript:alert(1)"):
            with self.subTest(url=url):
                self.assertEqual(_safe_next(request, url), "")
        self.assertEqual(_safe_next(request, "/transactions/?account=3"), "/transactions/?account=3")
