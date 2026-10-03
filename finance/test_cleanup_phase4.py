"""Plan 02, Phase 4 (service layer): the importer and reconciliation as services, the reminder
rules on the model, the shared Watched-expenses picker, the upload form's extension check and
the report query building blocks - each tested directly, without a web request."""

import io
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from audit.models import AuditLogEntry

from . import reporting
from .forms import ImportUploadForm, watched_expenses_picker_context
from .models import Account, Category, ImportBatch, Payee, Profile, Reconciliation, Reminder, Transaction
from .services import importing, reconciliation

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


class ImportFileTests(TestCase):
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


class ImportUploadFormTests(TestCase):
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


class ReconcileTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Wallet")
        self.pay = Transaction.objects.create(account=self.account, date=date(2026, 1, 5), amount=Decimal("100.00"))
        self.buy = Transaction.objects.create(account=self.account, date=date(2026, 1, 6), amount=Decimal("-30.00"))

    def test_a_matching_statement_marks_the_transactions_reconciled(self):
        done = reconciliation.reconcile(self.account, date(2026, 1, 31), Decimal("70.00"), [self.pay, self.buy])
        self.assertIsNone(done.adjustment_transaction)
        self.assertEqual(done.opening_balance, Decimal("0"))
        self.assertEqual(self.account.transactions.filter(reconciled=True).count(), 2)

    def test_an_unaccepted_difference_saves_nothing(self):
        with self.assertRaises(reconciliation.UnacceptedDifferenceError) as caught:
            reconciliation.reconcile(self.account, date(2026, 1, 31), Decimal("75.00"), [self.pay, self.buy])
        self.assertEqual(caught.exception.difference, Decimal("5.00"))
        self.assertFalse(Reconciliation.objects.exists())
        self.assertFalse(self.account.transactions.filter(reconciled=True).exists())

    def test_an_accepted_difference_gets_an_adjustment_outside_the_reports(self):
        done = reconciliation.reconcile(
            self.account, date(2026, 1, 31), Decimal("65.00"), [self.pay], accept_difference=True
        )
        adjustment = done.adjustment_transaction
        self.assertEqual((adjustment.amount, adjustment.reconciled), (Decimal("-35.00"), True))
        self.assertEqual(adjustment.category, self.account.ensure_transfer_category())
        self.assertEqual(self.account.reconciled_balance(), Decimal("65.00"))
        self.assertFalse(Transaction.objects.reportable().filter(pk=adjustment.pk).exists())


class ReminderEnterAndSkipTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Wallet")
        self.due = date.today() + timedelta(days=3)
        self.txn = Transaction.objects.create(account=self.account, date=self.due, amount=Decimal("-5"))

    def reminder(self, frequency=Reminder.Frequency.ONE_TIME):
        return Reminder.objects.create(
            account=self.account, amount=Decimal("5"), next_due_date=self.due, frequency=frequency
        )

    def test_entering_a_one_time_reminder_marks_it_paid(self):
        reminder = self.reminder()
        reminder.mark_entered(self.txn)
        reminder.refresh_from_db()
        self.assertEqual((reminder.status(), reminder.next_due_date), ("paid", self.due))

    def test_entering_a_recurring_reminder_records_the_period_and_moves_on(self):
        reminder = self.reminder(Reminder.Frequency.MONTHLY)
        reminder.mark_entered(self.txn)
        reminder.refresh_from_db()
        self.assertGreater(reminder.next_due_date, self.due)
        self.assertIsNone(reminder.last_entered_transaction)
        record = reminder.occurrences.get(next_due_date=self.due)
        self.assertEqual((record.status(), record.last_entered_transaction), ("paid", self.txn))

    def test_skipping_a_one_time_reminder_marks_it_done(self):
        reminder = self.reminder()
        reminder.skip()
        reminder.refresh_from_db()
        self.assertEqual(reminder.status(), "skipped")

    def test_skipping_a_recurring_reminder_clears_an_old_link_and_moves_on(self):
        reminder = self.reminder(Reminder.Frequency.MONTHLY)
        reminder.last_entered_transaction = self.txn
        reminder.save(update_fields=["last_entered_transaction"])
        reminder.skip()
        reminder.refresh_from_db()
        self.assertGreater(reminder.next_due_date, self.due)
        self.assertIsNone(reminder.last_entered_transaction)
        self.assertTrue(reminder.occurrences.get(next_due_date=self.due).skipped)

    def test_a_series_edit_reaches_the_head_and_open_occurrences_only(self):
        head = self.reminder(Reminder.Frequency.MONTHLY)
        first, second = head.occurrences.order_by("next_due_date")[:2]
        second.skipped = True
        second.save(update_fields=["skipped"])
        first.amount = Decimal("9")
        first.save(update_fields=["amount"])

        first.apply_to_series()

        head.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(head.amount, Decimal("9"))
        self.assertEqual(second.amount, Decimal("5"))  # skipped: a record now, left alone
        open_amounts = set(head.occurrences.filter(skipped=False).values_list("amount", flat=True))
        self.assertEqual(open_amounts, {Decimal("9")})


class WatchedExpensesPickerTests(TestCase):
    def setUp(self):
        self.profile = Profile.for_user(get_user_model().objects.create_user("alex"))
        self.food = Category.objects.create(name="Food", category_type=EXPENSE)
        self.groceries = Category.objects.create(name="Groceries", category_type=EXPENSE, parent=self.food)

    def test_the_button_label_follows_what_is_chosen(self):
        self.assertEqual(watched_expenses_picker_context(self.profile)["watched_button_label"], "No categories chosen")
        self.profile.watched_expenses_categories.set([self.groceries])
        context = watched_expenses_picker_context(self.profile)
        self.assertEqual(context["watched_button_label"], self.groceries.full_name)
        self.assertEqual(context["watched_selected_values"], {str(self.groceries.pk)})
        self.profile.watched_expenses_categories.add(self.food)
        self.assertEqual(
            watched_expenses_picker_context(self.profile)["watched_button_label"], "2 categories selected"
        )


class TransactionQuerySetTests(TestCase):
    def setUp(self):
        self.wallet = Account.objects.create(name="Wallet")
        self.bank = Account.objects.create(name="Bank")
        groceries = Category.objects.create(name="Groceries", category_type=EXPENSE)
        salary = Category.objects.create(name="Salary", category_type=INCOME)

        def add(account, day, category, amount):
            return Transaction.objects.create(
                account=account, date=date(2026, 1, day), category=category, amount=Decimal(amount)
            )

        self.spent = add(self.wallet, 1, groceries, "-10")
        self.refund = add(self.wallet, 31, groceries, "4")  # positive, still spending (R1)
        self.earned = add(self.bank, 15, salary, "100")
        self.uncategorized = add(self.wallet, 10, None, "-1")
        self.transfer = add(self.wallet, 10, self.wallet.ensure_transfer_category(), "-50")
        self.february = add(self.wallet, 1, groceries, "-1")
        self.february.date = date(2026, 2, 1)
        self.february.save()

    def pks(self, queryset):
        return set(queryset.values_list("pk", flat=True))

    def test_in_range_includes_both_ends(self):
        january = Transaction.objects.in_range(date(2026, 1, 1), date(2026, 1, 31))
        self.assertNotIn(self.february.pk, self.pks(january))
        self.assertIn(self.refund.pk, self.pks(january))

    def test_reportable_leaves_out_transfers_and_uncategorized(self):
        reportable = self.pks(Transaction.objects.reportable())
        self.assertEqual(reportable, {self.spent.pk, self.refund.pk, self.earned.pk, self.february.pk})

    def test_of_kind_goes_by_the_category_type_not_the_sign(self):
        self.assertEqual(
            self.pks(Transaction.objects.reportable().of_kind(expense_only=True)),
            {self.spent.pk, self.refund.pk, self.february.pk},
        )
        self.assertEqual(self.pks(Transaction.objects.reportable().of_kind(income_only=True)), {self.earned.pk})

    def test_for_account_none_means_every_account(self):
        self.assertEqual(Transaction.objects.for_account(None).count(), Transaction.objects.count())
        self.assertEqual(self.pks(Transaction.objects.for_account(self.bank)), {self.earned.pk})


class RollupToParentsTests(TestCase):
    def test_a_parent_includes_its_subcategories_per_column(self):
        food = Category.objects.create(name="Food", category_type=EXPENSE)
        groceries = Category.objects.create(name="Groceries", category_type=EXPENSE, parent=food)
        cafe = Category.objects.create(name="Cafe", category_type=EXPENSE, parent=food)
        totals = {food.pk: [Decimal("1"), Decimal("0")], groceries.pk: [Decimal("2"), Decimal("3")]}

        categories = reporting._with_parents([groceries.pk, cafe.pk])
        rolled = reporting._rollup_to_parents(totals, categories, 2)

        self.assertEqual(rolled[food.pk], [Decimal("3"), Decimal("3")])
        self.assertEqual(rolled[groceries.pk], [Decimal("2"), Decimal("3")])
        self.assertEqual(totals[food.pk], [Decimal("1"), Decimal("0")])  # the input is left alone


class ImportPageTests(TestCase):
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
