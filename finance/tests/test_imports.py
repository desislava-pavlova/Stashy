"""Importing bank files: CSV, Excel and OFX files, skipping rows already imported, categories
and accounts named in a file, guessing a category from the payee's history, date formats,
and the messages for files that can't be read."""

import io
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook

from audit.models import AuditLogEntry

from ..forms import ImportUploadForm
from ..models import Account, Category, ImportBatch, Payee, Transaction
from ..services import importing
from ..services.importing import _parse_transaction_number
from .base import LoggedInWithAccountTestCase, StashTestCase

EXPENSE, INCOME = Category.CategoryType.EXPENSE, Category.CategoryType.INCOME

OFX_FILE = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX><BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS><CURDEF>EUR<BANKACCTFROM><BANKID>1<ACCTID>2<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260101<DTEND>20260131
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260105<TRNAMT>-12.50<FITID>1<NAME>Lidl<MEMO>weekly shop</STMTTRN>
</BANKTRANLIST><LEDGERBAL><BALAMT>100<DTASOF>20260131</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""


def csv_upload(text, name="bank.csv", encoding="utf-8"):
    return SimpleUploadedFile(name, text.encode(encoding), content_type="text/csv")


class ImportTestCase(LoggedInWithAccountTestCase):
    """Logged in with an account, plus _import(), which posts CSV text to the import page
    as bank.csv, for self.account."""

    def _import(self, csv_text, **extra):
        upload = SimpleUploadedFile("bank.csv", csv_text.encode(), content_type="text/csv")
        data = {"account": self.account.pk, "file": upload, **extra}
        return self.client.post(reverse("finance:import_transactions"), data)


class CsvImportTests(LoggedInWithAccountTestCase):
    CSV = 'Date,Payee,Memo,Amount\n03/04/2026,Shop,,"1.234,56"\n04/04/2026,Cafe,,-19.99\n'

    def _import(self):
        upload = SimpleUploadedFile("bank.csv", self.CSV.encode(), content_type="text/csv")
        return self.client.post(reverse("finance:import_transactions"), {"account": self.account.pk, "file": upload})

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


class ImportCategoryGuessTests(LoggedInWithAccountTestCase):
    def test_import_guesses_the_payees_most_recent_category(self):

        groceries = Category.objects.create(name="Groceries")
        payee = Payee.objects.create(name="Lidl")
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 1),
            payee=payee,
            category=groceries,
            amount=Decimal("-10.00"),
        )
        upload = SimpleUploadedFile("bank.csv", b"Date,Payee,Memo,Amount\n10/01/2026,Lidl,,-20.00\n")
        self.client.post(reverse("finance:import_transactions"), {"account": self.account.pk, "file": upload})
        imported = Transaction.objects.get(date=date(2026, 1, 10))
        self.assertEqual(imported.category, groceries)


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

        groceries = Category.objects.create(name="Groceries")
        payee = Payee.objects.create(name="Lidl")
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), payee=payee, category=groceries, amount=Decimal("-5.00")
        )
        self._import("Date,Payee,Memo,Amount\n02/01/2026,Lidl,,-10.00\n")
        self.assertEqual(Transaction.objects.get(date=date(2026, 1, 2)).category, groceries)

    def test_auto_generated_badge_shown_in_category_list(self):
        self._import("Date,Payee,Memo,Amount,Category\n01/01/2026,Lidl,,-20.00,Groceries\n")
        response = self.client.get(reverse("finance:category_list"))
        self.assertContains(response, "auto")


class AccountColumnTests(ImportTestCase):
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
        upload = SimpleUploadedFile(
            "bank.csv",
            (
                b"Date,Account,Payee,Memo,Amount\n"
                b"01/01/2026,Nonexistent Card,Employer,,100.00\n"
                b"02/01/2026,Savings,Employer,,50.00\n"
            ),
            content_type="text/csv",
        )
        response = self.client.post(
            reverse("finance:import_transactions"),
            {"account": self.account.pk, "file": upload},
            follow=True,
        )
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
        upload = SimpleUploadedFile(
            "bank.csv", b"Date,Payee,Memo,Amount\n25/01/2026,Shop,,-10.00\n", content_type="text/csv"
        )
        response = self.client.post(
            reverse("finance:import_transactions"),
            {"account": self.account.pk, "file": upload},
            follow=True,
        )
        self.assertContains(response, "DD/MM/YYYY")


class ImportErrorTests(LoggedInWithAccountTestCase):
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


class ImportFileTests(StashTestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Wallet")

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
        self.assertEqual((batch.source_filename, batch.row_count, batch.duplicate_count), ("bank.csv", 2, 0))
        groceries = Transaction.objects.get(payee__name="Lidl").category
        self.assertEqual(
            (groceries.parent.name, groceries.name, groceries.category_type), ("Food", "Groceries", EXPENSE)
        )
        self.assertEqual(Transaction.objects.get(payee__name="Employer").category.category_type, INCOME)

    def test_a_second_import_of_the_same_file_skips_every_row(self):
        text = "Date,Payee,Amount\n2026-01-05,Lidl,-3.00\n"
        importing.import_file(csv_upload(text), self.account)
        result = importing.import_file(csv_upload(text), self.account)
        self.assertEqual((result.created, result.duplicates), (0, 1))
        self.assertEqual(Transaction.objects.count(), 1)

    def test_an_existing_payee_keeps_its_spelling(self):
        lidl = Payee.objects.create(name="Lidl")
        importing.import_file(csv_upload("Date,Payee,Amount\n2026-01-05,LIDL,-3.00\n"), self.account)
        self.assertEqual(Transaction.objects.get().payee, lidl)

    def test_a_row_naming_an_unknown_account_is_skipped(self):
        result = importing.import_file(csv_upload("Date,Account,Amount\n2026-01-05,Nowhere,-3.00\n"), self.account)
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
                importing.import_file(SimpleUploadedFile(name, b"not really that kind of file"), self.account)
        self.assertFalse(ImportBatch.objects.exists())

    def test_imported_rows_are_logged_as_automatic(self):
        with self.captureOnCommitCallbacks(execute=True):
            importing.import_file(csv_upload("Date,Payee,Amount\n2026-01-05,Lidl,-3.00\n"), self.account)
        entry = AuditLogEntry.objects.get(table=Transaction._meta.db_table)
        self.assertEqual((entry.source, entry.reason), (AuditLogEntry.Source.AUTOMATIC, "Imported from bank.csv"))

    def test_an_excel_file_with_real_dates(self):
        workbook = Workbook()
        workbook.active.append(["Date", "Payee", "Memo", "Amount", "Reconciled"])
        workbook.active.append([datetime(2026, 1, 5), "Lidl", "weekly shop", -12.5, "R"])
        workbook.active.append([date(2026, 1, 6), None, None, 3, None])
        buffer = io.BytesIO()
        workbook.save(buffer)

        result = importing.import_file(SimpleUploadedFile("bank.xlsx", buffer.getvalue()), self.account)

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
    def setUp(self):
        self.account = Account.objects.create(name="Wallet")

    def form(self, name):
        return ImportUploadForm({"account": self.account.pk}, {"file": SimpleUploadedFile(name, b"x")})

    def test_rejects_a_file_type_stashy_cant_import(self):
        form = self.form("statement.pdf")
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["file"], [importing.UNSUPPORTED_FILE_MESSAGE])

    def test_the_extension_check_ignores_case(self):
        self.assertTrue(self.form("STATEMENT.CSV").is_valid())


class ImportPageTests(StashTestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("alex"))
        self.account = Account.objects.create(name="Wallet")

    def test_an_unsupported_file_is_explained_on_the_form(self):
        response = self.client.post(
            reverse("finance:import_transactions"),
            {"account": self.account.pk, "file": SimpleUploadedFile("statement.pdf", b"x"), "date_format": "auto"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, importing.UNSUPPORTED_FILE_MESSAGE)
        for leaked in ("{#", "#}", "{%", "%}"):
            self.assertNotContains(response, leaked)
        self.assertFalse(ImportBatch.objects.exists())

    def test_a_good_file_lands_on_the_account_with_a_summary(self):
        upload = SimpleUploadedFile("bank.csv", b"Date,Payee,Amount\n2026-01-05,Lidl,-3.00\n")
        response = self.client.post(
            reverse("finance:import_transactions"),
            {"account": self.account.pk, "file": upload, "date_format": "auto"},
            follow=True,
        )
        self.assertRedirects(response, reverse("finance:account_detail", kwargs={"pk": self.account.pk}))
        self.assertContains(response, "Imported 1 transaction (0 duplicates skipped).")
