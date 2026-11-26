"""Importing bank files: CSV, Excel and OFX files, skipping rows already
imported, categories and accounts named in a file, guessing a category
from the payee's history, date formats, and the messages for files that
can't be read."""

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from openpyxl import Workbook

from ..forms.imports import ImportUploadForm
from ..models import Account, Category, ImportBatch, Payee, Transaction, name_key
from ..services import importing
from ..services.importing import ImportResult, _parse_transaction_number
from ..views.imports import _import_summary
from .base import (
    LEAKED_TEMPLATE_SYNTAX,
    LoggedInWithAccountTestCase,
    StashTestCase,
    csv_upload,
    ofx_file,
    post_import,
)


EXPENSE, INCOME = Category.CategoryType.EXPENSE, Category.CategoryType.INCOME

OFX_FILE = ofx_file()


class ImportTestCase(LoggedInWithAccountTestCase):
    """Logged in with an account, plus _import(), which posts CSV text
    to the import page as bank.csv, for self.account."""

    def _import(self, csv_text, **extra):
        return post_import(self.client, self.account, csv_upload(csv_text), **extra)


class CsvImportTests(LoggedInWithAccountTestCase):
    CSV = 'Date,Payee,Memo,Amount\n03/04/2026,Shop,,"1.234,56"\n04/04/2026,Cafe,,-19.99\n'

    def _import(self):
        return post_import(self.client, self.account, csv_upload(self.CSV))

    def test_amounts_are_exact_decimals_and_dates_are_day_first(self):
        self._import()
        shop = Transaction.objects.get(payee__name="Shop")
        self.assertEqual(shop.amount, Decimal("1234.56"))
        self.assertEqual(shop.date, date(2026, 4, 3))
        self.assertEqual(Transaction.objects.get(payee__name="Cafe").amount, Decimal("-19.99"))

    def test_reimporting_the_same_file_skips_duplicates(self):
        self._import()
        self._import()
        self.assertEqual(Transaction.objects.count(), 2)


class ReconciledColumnTests(LoggedInWithAccountTestCase):
    """S41: only R (and X, reconciled, yes, ...) comes in reconciled. A
    blank, a cell of spaces, a missing column, and the "cleared" marks
    (c, cleared, *) come in unreconciled: in Quicken a cleared
    transaction is one the bank has, not one checked against a
    statement, and imported as reconciled it would never show up in the
    next Reconcile checklist."""

    NOT_RECONCILED = (
        "",
        "   ",
        "n",
        "No",
        "FALSE",
        "0",
        "u",
        "Unreconciled",
        "uncleared",
        "c",
        "C",
        "cleared",
        "*",
    )
    RECONCILED = ("R", "r", "X", "reconciled", "Reconciled", "yes", "TRUE", "1")

    def import_values(self, values, heading="Reconciled"):
        """Imports one row per value, each with its own payee, and
        returns {value: reconciled}."""
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Date", "Payee", "Amount", heading])
        for number, value in enumerate(values, start=1):
            writer.writerow(["2026-01-05", f"Payee {number}", f"-{number}.00", value])
        result = importing.import_file(csv_upload(buffer.getvalue()), self.account)
        self.assertEqual(result.created, len(values))
        return {
            value: Transaction.objects.get(payee__name=f"Payee {n}").reconciled
            for n, value in enumerate(values, 1)
        }

    def test_these_csv_values_mean_not_reconciled(self):
        for value, reconciled in self.import_values(self.NOT_RECONCILED).items():
            with self.subTest(value=value):
                self.assertFalse(reconciled)

    def test_these_csv_values_mean_reconciled(self):
        for value, reconciled in self.import_values(self.RECONCILED).items():
            with self.subTest(value=value):
                self.assertTrue(reconciled)

    def test_a_file_with_no_reconciled_column_comes_in_unreconciled(self):
        importing.import_file(
            csv_upload("Date,Payee,Amount\n2026-01-05,Lidl,-3.00\n"), self.account
        )
        self.assertFalse(Transaction.objects.get(payee__name="Lidl").reconciled)

    def test_the_value_decides_not_the_columns_heading(self):
        # A "Yes" in a column called Cleared still counts as reconciled;
        # "c" in a Status column does not.
        self.assertTrue(self.import_values(["Yes"], heading="Cleared")["Yes"])
        Transaction.objects.all().delete()
        self.assertFalse(self.import_values(["c"], heading="Status")["c"])

    def test_an_excel_file_reads_blank_spaces_booleans_and_marks_the_same_way(self):
        workbook = Workbook()
        workbook.active.append(["Date", "Payee", "Amount", "Reconciled"])
        values = [None, "   ", False, 0, "c", "*", "R", True, 1]
        for number, value in enumerate(values, start=1):
            workbook.active.append([date(2026, 1, 5), f"Payee {number}", -number, value])
        buffer = io.BytesIO()
        workbook.save(buffer)

        importing.import_file(SimpleUploadedFile("bank.xlsx", buffer.getvalue()), self.account)

        shown = [
            Transaction.objects.get(payee__name=f"Payee {n}").reconciled
            for n in range(1, len(values) + 1)
        ]
        self.assertEqual(shown, [False, False, False, False, False, False, True, True, True])

    def test_an_excel_file_with_no_reconciled_column_comes_in_unreconciled(self):
        workbook = Workbook()
        workbook.active.append(["Date", "Payee", "Amount"])
        workbook.active.append([date(2026, 1, 5), "Lidl", -3])
        buffer = io.BytesIO()
        workbook.save(buffer)

        importing.import_file(SimpleUploadedFile("bank.xlsx", buffer.getvalue()), self.account)

        self.assertFalse(Transaction.objects.get(payee__name="Lidl").reconciled)


class ReconciledImportThroughThePageTests(ImportTestCase):
    """S41 end to end: the file goes up the import page, and what comes
    in is what the Reconcile checklist then offers."""

    def reconcile_checklist(self, statement_balance="0.00"):
        return self.client.post(
            reverse("finance:account_reconcile", args=[self.account.pk]),
            {"statement_date": "31/01/2026", "statement_balance": statement_balance},
        )

    def test_cleared_rows_wait_in_the_checklist_and_reconciled_ones_are_the_opening_balance(self):
        self._import(
            "Date,Payee,Amount,Reconciled\n"
            "2026-01-05,Lidl,-10.00,R\n"
            "2026-01-06,Ikea,-20.00,c\n"
            "2026-01-07,Salary,100.00,*\n"
            "2026-01-08,Cafe,-3.00,\n"
        )
        checklist = self.reconcile_checklist()
        self.assertEqual([t.payee.name for t in checklist.context["payments"]], ["Ikea", "Cafe"])
        self.assertEqual([t.payee.name for t in checklist.context["deposits"]], ["Salary"])
        self.assertEqual(checklist.context["opening_balance"], Decimal("-10.00"))

    def test_importing_the_same_file_again_leaves_every_reconciled_mark_as_it_is(self):
        self._import(
            "Date,Payee,Amount,Reconciled\n2026-01-05,Lidl,-10.00,\n2026-01-06,Ikea,-20.00,R\n"
        )
        # Lidl is reconciled since (an account's own reconcile does
        # that); the second file says the opposite about both rows.
        lidl = Transaction.objects.get(payee__name="Lidl")
        lidl.reconciled = True
        lidl.save(update_fields=["reconciled"])

        response = self._import(
            "Date,Payee,Amount,Reconciled\n2026-01-05,Lidl,-10.00,\n2026-01-06,Ikea,-20.00,\n"
        )

        self.assertEqual(Transaction.objects.count(), 2)
        self.assertEqual(
            dict(Transaction.objects.values_list("payee__name", "reconciled")),
            {"Lidl": True, "Ikea": True},
        )
        # The first import's message is still waiting too: the second
        # one is the last.
        messages = [str(m) for m in self.client.get(response["Location"]).context["messages"]]
        self.assertIn("Imported 0 transactions (2 duplicates skipped)", messages[-1])

    def test_a_status_column_of_bank_words_comes_in_reconciled(self):
        # Characterization, not a claim that it is right. Possible bug:
        # banks that export a Status column ("Posted", "Pending",
        # "Completed") have every row come in reconciled, because only
        # the few marks in _RECONCILED_FALSE_VALUES mean "not". Those
        # rows then never show up in the Reconcile checklist.
        self._import(
            "Date,Payee,Amount,Status\n"
            "2026-01-05,Lidl,-10.00,Posted\n"
            "2026-01-06,Ikea,-20.00,Pending\n"
        )
        self.assertEqual(set(Transaction.objects.values_list("reconciled", flat=True)), {True})
        self.assertEqual(self.reconcile_checklist().context["payments"], [])


class CategoryAutoCreateTests(ImportTestCase):
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
        self.assertEqual(Category.objects.filter(name_key=name_key("groceries")).count(), 1)
        self.assertFalse(Category.objects.get(name_key=name_key("groceries")).is_auto_generated)

    def test_parent_child_with_colon_creates_a_subcategory(self):
        self._import(
            "Date,Payee,Memo,Amount,Category\n01/01/2026,ViK,,-20.75,Bills & Utilities:Water\n"
        )
        water = Category.objects.get(name="Water")
        self.assertEqual(water.parent.name, "Bills & Utilities")
        self.assertTrue(water.is_auto_generated)
        self.assertTrue(water.parent.is_auto_generated)

    def test_parent_child_with_arrow_also_works(self):
        self._import("Date,Payee,Memo,Amount,Category\n01/01/2026,ViK,,-20.75,Bills > Water\n")
        water = Category.objects.get(name="Water")
        self.assertEqual(water.parent.name, "Bills")

    def test_a_new_subcategory_inherits_its_existing_parents_type(self):
        income_parent = Category.objects.create(
            name="Work Income", category_type=Category.CategoryType.INCOME
        )
        # A negative amount would normally infer "expense", but the
        # parent already exists as income.
        self._import(
            "Date,Payee,Memo,Amount,Category\n01/01/2026,Employer,,-5.00,Work Income:Adjustment\n"
        )
        adjustment = Category.objects.get(name="Adjustment")
        self.assertEqual(adjustment.parent_id, income_parent.pk)
        self.assertEqual(adjustment.category_type, "income")

    def test_no_category_column_guesses_the_payees_most_recently_used_category(self):
        groceries = Category.objects.create(name="Groceries", category_type=EXPENSE)
        household = Category.objects.create(name="Household", category_type=EXPENSE)
        payee = Payee.objects.create(name="Lidl")
        for on, category in ((date(2026, 1, 1), groceries), (date(2026, 1, 8), household)):
            Transaction.objects.create(
                account=self.account,
                date=on,
                payee=payee,
                category=category,
                amount=Decimal("-5.00"),
            )
        self._import("Date,Payee,Memo,Amount\n10/01/2026,Lidl,,-10.00\n")
        self.assertEqual(Transaction.objects.get(date=date(2026, 1, 10)).category, household)

    def test_a_guess_sees_the_rows_the_same_file_already_made_and_skips_the_whole_history(self):
        """S65: the guess reads the payee's newest transaction only, so
        a row earlier in the file counts when it is newer than what was
        stored, and the payee's whole history is never loaded."""
        groceries = Category.objects.create(name="Groceries", category_type=EXPENSE)
        household = Category.objects.create(name="Household", category_type=EXPENSE)
        payee = Payee.objects.create(name="Lidl")
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 8),
            payee=payee,
            category=household,
            amount=Decimal("-5.00"),
        )
        file = (
            "Date,Payee,Memo,Amount,Category\n"
            "10/01/2026,Lidl,,-10.00,\n"
            "20/01/2026,Lidl,,-11.00,Groceries\n"
            "25/01/2026,Lidl,,-12.00,\n"
        )

        with mock.patch.object(Payee, "category_history", side_effect=AssertionError):
            self._import(file)

        categories = {t.date.day: t.category for t in Transaction.objects.filter(payee=payee)}
        self.assertEqual(categories[10], household)
        self.assertEqual(categories[20], groceries)
        self.assertEqual(categories[25], groceries)

    def test_auto_generated_badge_shown_in_category_list(self):
        Category.objects.create(name="Made by hand", category_type=Category.CategoryType.EXPENSE)
        self._import("Date,Payee,Memo,Amount,Category\n01/01/2026,Lidl,,-20.00,Groceries\n")
        response = self.client.get(reverse("finance:category_list"))
        self.assertContains(
            response, 'title="Created automatically during an import">auto</span>', count=1
        )


class AccountColumnTests(ImportTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other = Account.objects.create(name="Savings")

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
        upload = csv_upload(
            "Date,Account,Payee,Memo,Amount\n"
            "01/01/2026,Nonexistent Card,Employer,,100.00\n"
            "02/01/2026,Savings,Employer,,50.00\n"
        )
        response = post_import(self.client, self.account, upload, follow=True)
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(Transaction.objects.get().account, self.other)
        self.assertContains(response, "skipped")

    def test_no_account_column_uses_the_selected_account_for_every_row(self):
        self._import("Date,Payee,Memo,Amount\n01/01/2026,Employer,,100.00\n")
        self.assertEqual(Transaction.objects.get().account, self.account)


class DateFormatDetectionTests(ImportTestCase):
    def test_an_unambiguous_day_first_file_is_detected(self):
        # 25 can only be a day - settles the whole file as day-first.
        self._import("Date,Payee,Memo,Amount\n25/01/2026,Shop,,-10.00\n")
        self.assertEqual(Transaction.objects.get().date, date(2026, 1, 25))

    def test_an_unambiguous_month_first_file_is_detected(self):
        # 25 in the second slot can only be a day - settles the file as
        # month-first.
        self._import("Date,Payee,Memo,Amount\n01/25/2026,Shop,,-10.00\n")
        self.assertEqual(Transaction.objects.get().date, date(2026, 1, 25))

    def test_an_iso_file_is_detected(self):
        self._import("Date,Payee,Memo,Amount\n2026-01-05,Shop,,-10.00\n")
        self.assertEqual(Transaction.objects.get().date, date(2026, 1, 5))

    def test_ambiguous_dates_before_the_deciding_one_are_read_the_way_it_settles(self):
        # 03/04 and 04/05 can be read both ways. Only the last row (a 25
        # in the day place or the month place) settles the format, and
        # it settles it for the rows above it.
        day_first = (
            "Date,Payee,Memo,Amount\n"
            "03/04/2026,Shop,,-1.00\n"
            "04/05/2026,Cafe,,-2.00\n"
            "25/01/2026,Bar,,-3.00\n"
        )
        month_first = day_first.replace("25/01/2026", "01/25/2026")
        for name, file, expected in (
            ("day-first", day_first, {"Shop": date(2026, 4, 3), "Cafe": date(2026, 5, 4)}),
            ("month-first", month_first, {"Shop": date(2026, 3, 4), "Cafe": date(2026, 4, 5)}),
        ):
            with self.subTest(file=name):
                Transaction.objects.all().delete()
                ImportBatch.objects.all().delete()
                self._import(file)
                dates = {
                    t.payee.name: t.date for t in Transaction.objects.exclude(payee__name="Bar")
                }
                self.assertEqual(dates, expected)

    def test_an_ambiguous_file_falls_back_to_the_profile_default(self):
        # 03/04 alone could be either reading - nothing here settles it,
        # so it falls back to dmy, the default profile format in a test
        # environment.
        self._import("Date,Payee,Memo,Amount\n03/04/2026,Shop,,-10.00\n")
        self.assertEqual(Transaction.objects.get().date, date(2026, 4, 3))

    def test_day_first_dates_may_use_dots_or_dashes(self):
        self._import("Date,Payee,Memo,Amount\n25.01.2026,Dot,,-1.00\n26-01-2026,Dash,,-2.00\n")
        dates = {t.payee.name: t.date for t in Transaction.objects.all()}
        self.assertEqual(dates, {"Dot": date(2026, 1, 25), "Dash": date(2026, 1, 26)})

    def test_year_first_dates_may_use_slashes(self):
        self._import("Date,Payee,Memo,Amount\n2026/01/25,Shop,,-1.00\n")
        self.assertEqual(Transaction.objects.get().date, date(2026, 1, 25))

    def test_an_iso_date_in_a_day_first_file_is_still_read(self):
        # 25/01 settles the file as day-first, and the date app links
        # carry, YYYY-MM-DD, is read in every format.
        self._import("Date,Payee,Memo,Amount\n25/01/2026,A,,-1.00\n2026-02-03,B,,-2.00\n")
        dates = {t.payee.name: t.date for t in Transaction.objects.all()}
        self.assertEqual(dates, {"A": date(2026, 1, 25), "B": date(2026, 2, 3)})

    def test_spaces_around_a_date_are_ignored(self):
        self._import('Date,Payee,Memo,Amount\n"  25/01/2026 ",Shop,,-1.00\n')
        self.assertEqual(Transaction.objects.get().date, date(2026, 1, 25))

    def test_a_date_that_does_not_exist_is_unreadable_not_a_crash(self):
        result = importing.import_file(
            csv_upload("Date,Payee,Amount\n30/02/2026,Shop,-1.00\n32/01/2026,Shop,-1.00\n"),
            self.account,
        )
        self.assertEqual((result.created, result.unreadable), (0, 2))

    def test_an_explicit_override_beats_auto_detection(self):
        # Would auto-detect as day-first (25 can only be a day); forcing
        # month-first instead makes this row fail to parse as a date
        # (there's no 25th month), so nothing imports.
        self._import("Date,Payee,Memo,Amount\n25/01/2026,Shop,,-10.00\n", date_format="mdy")
        self.assertEqual(Transaction.objects.count(), 0)

    def test_success_message_reports_the_format_used(self):
        upload = csv_upload("Date,Payee,Memo,Amount\n25/01/2026,Shop,,-10.00\n")
        response = post_import(self.client, self.account, upload, follow=True)
        self.assertContains(response, "DD/MM/YYYY")


class ImportErrorTests(LoggedInWithAccountTestCase):
    """1.10 (F32, F47): unreadable files get a message; skipped rows are
    counted."""

    def upload(self, name, content):
        return post_import(
            self.client, self.account, SimpleUploadedFile(name, content), follow=True
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
        content = (
            b"Date,Payee,Memo,Amount\n"
            b"03/09/2026,Shop,,-5.00\n"
            b"someday,Shop,,-5.00\n"
            b"04/09/2026,Shop,,lots\n"
            b",,,\n"
        )
        response = self.upload("bank.csv", content)
        self.assertContains(
            response,
            "Imported 1 transaction (0 duplicates skipped). 2 rows skipped - "
            "unreadable date or amount, or a date outside 1900 to 2100.",
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


class ImportFileTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.account = Account.objects.create(name="Wallet")

    def test_imports_every_row_as_one_batch(self):
        upload = csv_upload(
            "Date,Payee,Category,Amount\n"
            "25/01/2026,Lidl,Food:Groceries,-12.50\n"
            "\n"
            "26/01/2026,Employer,Salary,1000\n"
            "not a date,Lidl,,-1\n"
        )
        result = importing.import_file(upload, self.account)

        self.assertEqual((result.created, result.duplicates, result.unreadable), (2, 0, 1))
        self.assertEqual(result.date_format, "dmy")  # 25 can only be a day
        batch = ImportBatch.objects.get()
        self.assertEqual(
            (batch.source_filename, batch.row_count, batch.duplicate_count), ("bank.csv", 2, 0)
        )
        groceries = Transaction.objects.get(payee__name="Lidl").category
        self.assertEqual(
            (groceries.parent.name, groceries.name, groceries.category_type),
            ("Food", "Groceries", EXPENSE),
        )
        self.assertEqual(
            Transaction.objects.get(payee__name="Employer").category.category_type, INCOME
        )

    def test_a_second_import_of_the_same_file_skips_every_row(self):
        text = "Date,Payee,Amount\n2026-01-05,Lidl,-3.00\n"
        importing.import_file(csv_upload(text), self.account)
        result = importing.import_file(csv_upload(text), self.account)
        self.assertEqual((result.created, result.duplicates), (0, 1))
        self.assertEqual(Transaction.objects.count(), 1)

    def test_a_row_repeated_within_one_file_is_a_duplicate(self):
        text = "Date,Payee,Amount\n2026-01-05,Lidl,-3.00\n2026-01-05,Lidl,-3.00\n"
        result = importing.import_file(csv_upload(text), self.account)
        self.assertEqual((result.created, result.duplicates), (1, 1))

    def test_a_transaction_repeated_within_one_ofx_file_is_a_duplicate(self):
        # DOTALL: the fixture's transaction runs over two lines.
        transaction = re.search(rb"<STMTTRN>.*?</STMTTRN>", OFX_FILE, re.DOTALL).group()
        repeated = OFX_FILE.replace(transaction, transaction * 2)
        result = importing.import_file(SimpleUploadedFile("bank.ofx", repeated), self.account)
        self.assertEqual((result.created, result.duplicates), (1, 1))

    def test_stored_fingerprints_are_read_once_not_once_per_row(self):
        # 8.3 (F29): one query for the whole file, however many rows it
        # has.
        rows = "".join(f"2026-01-{day:02d},Lidl,-{day}.00\n" for day in range(1, 29))
        with CaptureQueriesContext(connections[settings.TEST_STASH]) as queries:
            result = importing.import_file(csv_upload("Date,Payee,Amount\n" + rows), self.account)
        self.assertEqual(result.created, 28)
        # Other queries read the column along with the rest of a row;
        # these filter on it.
        fingerprint_lookups = [
            q["sql"]
            for q in queries.captured_queries
            if "import_fingerprint" in q["sql"].partition("WHERE")[2]
        ]
        self.assertEqual(len(fingerprint_lookups), 1)

    def test_an_existing_payee_keeps_its_spelling(self):
        lidl = Payee.objects.create(name="Lidl")
        importing.import_file(
            csv_upload("Date,Payee,Amount\n2026-01-05,LIDL,-3.00\n"), self.account
        )
        self.assertEqual(Transaction.objects.get().payee, lidl)

    def test_a_row_naming_an_unknown_account_is_skipped(self):
        result = importing.import_file(
            csv_upload("Date,Account,Amount\n2026-01-05,Nowhere,-3.00\n"), self.account
        )
        self.assertEqual((result.created, result.unknown_account), (0, 1))

    def test_a_windows_1251_file_is_read_and_says_so(self):
        upload = csv_upload("Date,Payee,Amount\n2026-01-05,Магазин,-3.00\n", encoding="cp1251")
        result = importing.import_file(upload, self.account)
        self.assertEqual(Transaction.objects.get().payee.name, "Магазин")
        self.assertIn("Windows-1251", result.encoding_note)

    def test_an_unsupported_file_writes_nothing(self):
        with self.assertRaisesMessage(importing.ImportFileError, "Unsupported file type"):
            importing.import_file(SimpleUploadedFile("notes.txt", b"hello"), self.account)
        self.assertFalse(ImportBatch.objects.exists())

    def test_an_unreadable_file_writes_nothing(self):
        for name in ("broken.xlsx", "broken.ofx"):
            with self.subTest(name=name), self.assertRaises(importing.ImportFileError):
                importing.import_file(
                    SimpleUploadedFile(name, b"not really that kind of file"), self.account
                )
        self.assertFalse(ImportBatch.objects.exists())

    def test_an_excel_file_with_real_dates(self):
        workbook = Workbook()
        workbook.active.append(["Date", "Payee", "Memo", "Amount", "Reconciled"])
        workbook.active.append([datetime(2026, 1, 5), "Lidl", "weekly shop", -12.5, "R"])
        workbook.active.append([date(2026, 1, 6), None, None, 3, None])
        buffer = io.BytesIO()
        workbook.save(buffer)

        result = importing.import_file(
            SimpleUploadedFile("bank.xlsx", buffer.getvalue()), self.account
        )

        self.assertEqual(result.created, 2)
        shop = Transaction.objects.get(payee__name="Lidl")
        self.assertEqual(
            (shop.date, shop.amount, shop.memo, shop.reconciled),
            (date(2026, 1, 5), Decimal("-12.50"), "weekly shop", True),
        )
        self.assertIsNone(Transaction.objects.get(amount=Decimal("3")).payee)

    def test_an_ofx_file_guesses_the_category_from_the_payee(self):
        groceries = Category.objects.create(name="Groceries", category_type=EXPENSE)
        lidl = Payee.objects.create(name="Lidl")
        Transaction.objects.create(
            account=self.account, date=date(2025, 12, 1), payee=lidl, category=groceries, amount=-1
        )

        result = importing.import_file(SimpleUploadedFile("bank.ofx", OFX_FILE), self.account)

        self.assertEqual((result.created, result.date_format), (1, None))
        imported = Transaction.objects.get(import_batch__isnull=False)
        self.assertEqual(
            (imported.date, imported.amount, imported.payee, imported.category),
            (date(2026, 1, 5), Decimal("-12.50"), lidl, groceries),
        )
        self.assertEqual(imported.memo, "weekly shop")


class ImportUploadFormTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.account = Account.objects.create(name="Wallet")

    def form(self, name):
        return ImportUploadForm(
            {"account": self.account.pk}, {"file": SimpleUploadedFile(name, b"x")}
        )

    def test_rejects_a_file_type_stashy_cant_import(self):
        form = self.form("statement.pdf")
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["file"], [importing.UNSUPPORTED_FILE_MESSAGE])

    def test_the_extension_check_ignores_case(self):
        self.assertTrue(self.form("STATEMENT.CSV").is_valid())


class ImportPageTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("alex")
        cls.account = Account.objects.create(name="Wallet")

    def setUp(self):
        self.client.force_login(self.user)

    def test_an_unsupported_file_is_explained_on_the_form(self):
        response = post_import(
            self.client,
            self.account,
            SimpleUploadedFile("statement.pdf", b"x"),
            date_format="auto",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, importing.UNSUPPORTED_FILE_MESSAGE)
        for leaked in LEAKED_TEMPLATE_SYNTAX:
            self.assertNotContains(response, leaked)
        self.assertFalse(ImportBatch.objects.exists())

    def test_a_good_file_lands_on_the_account_with_a_summary(self):
        upload = csv_upload("Date,Payee,Amount\n2026-01-05,Lidl,-3.00\n")
        response = post_import(self.client, self.account, upload, date_format="auto", follow=True)
        self.assertRedirects(
            response, reverse("finance:account_detail", kwargs={"pk": self.account.pk})
        )
        self.assertContains(response, "Imported 1 transaction (0 duplicates skipped).")


class ImportSummaryTests(StashTestCase):
    def test_a_row_naming_an_unknown_account_has_its_own_sentence(self):
        for count, rows in ((1, "1 row"), (2, "2 rows")):
            with self.subTest(rows=rows):
                summary = _import_summary(
                    ImportResult(created=3, duplicates=0, unknown_account=count)
                )
                self.assertEqual(
                    summary,
                    "Imported 3 transactions (0 duplicates skipped). "
                    f"{rows} skipped - named an account that doesn't exist yet.",
                )

    def test_the_count_of_transactions_is_singular_for_one_and_plural_for_none(self):
        for created, words in ((0, "0 transactions"), (1, "1 transaction"), (2, "2 transactions")):
            with self.subTest(created=created):
                self.assertEqual(
                    _import_summary(ImportResult(created=created)),
                    f"Imported {words} (0 duplicates skipped).",
                )

    def test_unreadable_rows_have_their_own_sentence_with_the_limits(self):
        for count, rows in ((1, "1 row"), (2, "2 rows")):
            with self.subTest(rows=rows):
                self.assertEqual(
                    _import_summary(ImportResult(created=3, unreadable=count)),
                    "Imported 3 transactions (0 duplicates skipped). "
                    f"{rows} skipped - unreadable date or amount, "
                    "or a date outside 1900 to 2100.",
                )

    def test_every_part_comes_in_its_own_order_and_a_part_with_nothing_to_say_is_left_out(self):
        result = ImportResult(
            created=1,
            duplicates=2,
            unknown_account=1,
            unreadable=1,
            date_format="mdy",
            encoding_note="The file was read as Windows-1251.",
        )
        self.assertEqual(
            _import_summary(result),
            "Imported 1 transaction (2 duplicates skipped). "
            "1 row skipped - named an account that doesn't exist yet. "
            "1 row skipped - unreadable date or amount, or a date outside 1900 to 2100. "
            "Read dates as MM/DD/YYYY. The file was read as Windows-1251.",
        )
        self.assertNotIn("skipped -", _import_summary(ImportResult(created=1)))
        self.assertNotIn("Read dates", _import_summary(ImportResult(created=1)))

    def test_a_date_format_the_form_does_not_list_is_shown_as_it_is(self):
        self.assertTrue(
            _import_summary(ImportResult(date_format="odd")).endswith(" Read dates as odd.")
        )
