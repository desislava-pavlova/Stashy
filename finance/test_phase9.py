"""Tests for Phase 9 (Import): resolving categories by name (auto-creating unknown ones),
per-row Account column resolution against existing accounts only, and per-file date format
detection/override."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import Account, Category, Transaction


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")

    def _import(self, csv_text, **extra):
        upload = SimpleUploadedFile("bank.csv", csv_text.encode(), content_type="text/csv")
        data = {"account": self.account.pk, "file": upload, **extra}
        return self.client.post(reverse("finance:import_transactions"), data)


class CategoryAutoCreateTests(LoggedInTestCase):
    def test_an_unknown_top_level_category_is_created_and_flagged(self):
        self._import("Date,Payee,Memo,Amount,Category\n01/01/2026,Lidl,,-20.00,Groceries\n")
        category = Category.objects.get(name="Groceries")
        self.assertTrue(category.is_auto_generated)
        self.assertEqual(category.category_type, "expense")
        self.assertEqual(Transaction.objects.get().category, category)

    def test_income_amount_creates_an_income_category(self):
        self._import("Date,Payee,Memo,Amount,Category\n01/01/2026,Employer,,1500.00,Salary\n")
        category = Category.objects.get(name="Salary")
        self.assertEqual(category.category_type, "income")

    def test_matching_is_case_insensitive_and_does_not_duplicate(self):
        Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        self._import("Date,Payee,Memo,Amount,Category\n01/01/2026,Lidl,,-20.00,groceries\n")
        self.assertEqual(Category.objects.filter(name__iexact="groceries").count(), 1)
        self.assertFalse(Category.objects.get(name__iexact="groceries").is_auto_generated)

    def test_parent_child_with_colon_creates_a_subcategory(self):
        self._import("Date,Payee,Memo,Amount,Category\n01/01/2026,ViK,,-20.75,Bills & Utilities:Water\n")
        water = Category.objects.get(name="Water")
        self.assertEqual(water.parent.name, "Bills & Utilities")
        self.assertTrue(water.is_auto_generated)
        self.assertTrue(water.parent.is_auto_generated)

    def test_parent_child_with_arrow_also_works(self):
        self._import("Date,Payee,Memo,Amount,Category\n01/01/2026,ViK,,-20.75,Bills > Water\n")
        water = Category.objects.get(name="Water")
        self.assertEqual(water.parent.name, "Bills")

    def test_a_new_subcategory_inherits_its_existing_parents_type(self):
        income_parent = Category.objects.create(name="Work Income", category_type=Category.CategoryType.INCOME)
        # A negative amount would normally infer "expense", but the parent already exists as income.
        self._import("Date,Payee,Memo,Amount,Category\n01/01/2026,Employer,,-5.00,Work Income:Adjustment\n")
        adjustment = Category.objects.get(name="Adjustment")
        self.assertEqual(adjustment.parent_id, income_parent.pk)
        self.assertEqual(adjustment.category_type, "income")

    def test_no_category_column_falls_back_to_payee_history(self):
        from .models import Payee

        groceries = Category.objects.create(name="Groceries")
        payee = Payee.objects.create(name="Lidl")
        Transaction.objects.create(account=self.account, date=date(2026, 1, 1), payee=payee, category=groceries, amount=Decimal("-5.00"))
        self._import("Date,Payee,Memo,Amount\n02/01/2026,Lidl,,-10.00\n")
        self.assertEqual(Transaction.objects.get(date=date(2026, 1, 2)).category, groceries)

    def test_auto_generated_badge_shown_in_category_list(self):
        self._import("Date,Payee,Memo,Amount,Category\n01/01/2026,Lidl,,-20.00,Groceries\n")
        response = self.client.get(reverse("finance:category_list"))
        self.assertContains(response, "auto")


class AccountColumnTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.other = Account.objects.create(name="Savings")

    def test_a_row_naming_an_existing_account_goes_there_instead_of_the_selected_one(self):
        self._import("Date,Account,Payee,Memo,Amount\n01/01/2026,Savings,Employer,,100.00\n")
        txn = Transaction.objects.get()
        self.assertEqual(txn.account, self.other)

    def test_matching_is_case_insensitive(self):
        self._import("Date,Account,Payee,Memo,Amount\n01/01/2026,savings,Employer,,100.00\n")
        self.assertEqual(Transaction.objects.get().account, self.other)

    def test_a_blank_account_cell_falls_back_to_the_selected_account(self):
        self._import("Date,Account,Payee,Memo,Amount\n01/01/2026,,Employer,,100.00\n")
        self.assertEqual(Transaction.objects.get().account, self.account)

    def test_an_unknown_account_name_skips_just_that_row(self):
        upload = SimpleUploadedFile("bank.csv", (
            "Date,Account,Payee,Memo,Amount\n"
            "01/01/2026,Nonexistent Card,Employer,,100.00\n"
            "02/01/2026,Savings,Employer,,50.00\n"
        ).encode(), content_type="text/csv")
        response = self.client.post(
            reverse("finance:import_transactions"), {"account": self.account.pk, "file": upload}, follow=True,
        )
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(Transaction.objects.get().account, self.other)
        self.assertContains(response, "skipped")

    def test_no_account_column_uses_the_selected_account_for_every_row(self):
        self._import("Date,Payee,Memo,Amount\n01/01/2026,Employer,,100.00\n")
        self.assertEqual(Transaction.objects.get().account, self.account)


class DateFormatDetectionTests(LoggedInTestCase):
    def test_an_unambiguous_day_first_file_is_detected(self):
        # 25 can only be a day - settles the whole file as day-first.
        self._import("Date,Payee,Memo,Amount\n25/01/2026,Shop,,-10.00\n")
        self.assertEqual(Transaction.objects.get().date, date(2026, 1, 25))

    def test_an_unambiguous_month_first_file_is_detected(self):
        # 25 in the second slot can only be a day - settles the file as month-first.
        self._import("Date,Payee,Memo,Amount\n01/25/2026,Shop,,-10.00\n")
        self.assertEqual(Transaction.objects.get().date, date(2026, 1, 25))

    def test_an_iso_file_is_detected(self):
        self._import("Date,Payee,Memo,Amount\n2026-01-05,Shop,,-10.00\n")
        self.assertEqual(Transaction.objects.get().date, date(2026, 1, 5))

    def test_an_ambiguous_file_falls_back_to_the_profile_default(self):
        # 03/04 alone could be either reading - nothing here settles it, so it falls back to
        # dmy, the default profile format in a test environment.
        self._import("Date,Payee,Memo,Amount\n03/04/2026,Shop,,-10.00\n")
        self.assertEqual(Transaction.objects.get().date, date(2026, 4, 3))

    def test_an_explicit_override_beats_auto_detection(self):
        # Would auto-detect as day-first (25 can only be a day); forcing month-first instead
        # makes this row fail to parse as a date (there's no 25th month), so nothing imports.
        self._import("Date,Payee,Memo,Amount\n25/01/2026,Shop,,-10.00\n", date_format="mdy")
        self.assertEqual(Transaction.objects.count(), 0)

    def test_success_message_reports_the_format_used(self):
        upload = SimpleUploadedFile("bank.csv", b"Date,Payee,Memo,Amount\n25/01/2026,Shop,,-10.00\n", content_type="text/csv")
        response = self.client.post(
            reverse("finance:import_transactions"), {"account": self.account.pk, "file": upload}, follow=True,
        )
        self.assertContains(response, "DD/MM/YYYY")
