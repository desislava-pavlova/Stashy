from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import formats

from .forms import DateRangeFilterForm
from .models import Account, Category, Payee, Reminder, Transaction
from .money import parse_amount
from .templatetags.stashy_format import money

NBSP = "\u00a0"


class ParseAmountTests(SimpleTestCase):
    def test_common_bank_formats(self):
        cases = {
            "1234.56": Decimal("1234.56"),
            "19.99": Decimal("19.99"),
            "-50": Decimal("-50.00"),
            "0.1": Decimal("0.10"),
            "1 234.56": Decimal("1234.56"),
            f"1{NBSP}234,56": Decimal("1234.56"),
            "1.234,56": Decimal("1234.56"),
            "1,234.56": Decimal("1234.56"),
            "1.234.567": Decimal("1234567.00"),
            "12,50": Decimal("12.50"),
            "12,5": Decimal("12.50"),
            "1,234": Decimal("1234.00"),
            "(12.00)": Decimal("-12.00"),
            "€ -5,00": Decimal("-5.00"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_amount(raw), expected)

    def test_unreadable_values_return_none(self):
        for raw in ("", "   ", "abc", "NaN", None):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_amount(raw))


class MoneyFilterTests(SimpleTestCase):
    def test_formatting(self):
        cases = [
            (Decimal("10000.5"), f"10{NBSP}000.50"),
            (Decimal("-1234567.891"), f"-1{NBSP}234{NBSP}567.89"),
            (Decimal("999"), "999.00"),
            (0, "0.00"),
            (Decimal("-0.001"), "0.00"),
            (None, ""),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(money(value), expected)


class DateFormatTests(SimpleTestCase):
    def test_dates_render_european(self):
        self.assertEqual(formats.date_format(date(2026, 4, 3)), "03/04/2026")
        self.assertEqual(formats.get_format("FIRST_DAY_OF_WEEK"), 1)


class LoggedInTestCase(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(user)
        self.account = Account.objects.create(name="Checking")


class CsvImportTests(LoggedInTestCase):
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


class PayeeHistoryTests(TestCase):
    """Payee memory (Phase 6) is computed live from transaction history, per category —
    there's no stored default to fall out of sync."""

    def setUp(self):
        self.account = Account.objects.create(name="Checking")
        self.groceries = Category.objects.create(name="Groceries")
        self.household = Category.objects.create(name="Household")
        self.payee = Payee.objects.create(name="Lidl")

    def test_one_transaction_gives_one_history_entry(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), payee=self.payee,
            category=self.groceries, memo="weekly shop", amount=Decimal("-30.00"),
        )
        history = self.payee.category_history()
        self.assertEqual(len(history), 1)
        self.assertEqual((history[0].category, history[0].memo), (self.groceries, "weekly shop"))

    def test_two_categories_give_two_entries_newest_first(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), payee=self.payee,
            category=self.groceries, memo="weekly shop", amount=Decimal("-30.00"),
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 10), payee=self.payee,
            category=self.household, memo="cleaning supplies", amount=Decimal("-12.00"),
        )
        history = self.payee.category_history()
        self.assertEqual([h.category for h in history], [self.household, self.groceries])

    def test_history_reflects_the_latest_by_date_not_by_entry_order(self):
        # Entered second, but dated earlier - shouldn't override the later-dated one.
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 20), payee=self.payee,
            category=self.groceries, memo="second shop", amount=Decimal("-40.00"),
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), payee=self.payee,
            category=self.groceries, memo="first shop", amount=Decimal("-30.00"),
        )
        history = self.payee.category_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].memo, "second shop")

    def test_editing_an_unrelated_field_does_not_change_the_history(self):
        txn = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), payee=self.payee,
            category=self.groceries, memo="weekly shop", amount=Decimal("-30.00"),
        )
        txn.reconciled = True
        txn.save()
        self.assertEqual(self.payee.category_history()[0].memo, "weekly shop")

    def test_uncategorized_transactions_form_their_own_entry(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), payee=self.payee, amount=Decimal("-10.00"),
        )
        history = self.payee.category_history()
        self.assertEqual(len(history), 1)
        self.assertIsNone(history[0].category)


class DateRangeFilterFormTests(SimpleTestCase):
    def test_custom_range_requires_both_dates(self):
        form = DateRangeFilterForm({"range": "custom", "start": "01/01/2026"})
        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)

    def test_custom_range_start_must_not_be_after_end(self):
        form = DateRangeFilterForm({"range": "custom", "start": "31/03/2026", "end": "01/01/2026"})
        self.assertFalse(form.is_valid())

    def test_valid_custom_range_parses_european_dates(self):
        form = DateRangeFilterForm({"range": "custom", "start": "01/02/2026", "end": "31/03/2026"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["start"], date(2026, 2, 1))

    def test_preset_range_needs_no_dates(self):
        self.assertTrue(DateRangeFilterForm({"range": "this_month"}).is_valid())


class ReportsViewTests(LoggedInTestCase):
    def test_incomplete_custom_range_shows_an_error(self):
        response = self.client.get(reverse("finance:reports"), {"range": "custom", "start": "01/01/2026"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "custom range needs both a start and an end date")


class AccountDeleteTests(LoggedInTestCase):
    def test_confirmation_counts_everything_that_would_be_deleted(self):
        for day in range(1, 4):
            Transaction.objects.create(account=self.account, date=date(2026, 1, day), amount=Decimal("-1.00"))
        Reminder.objects.create(account=self.account, amount=Decimal("500.00"), next_due_date=date(2026, 2, 1))
        response = self.client.get(reverse("finance:account_delete", args=[self.account.pk]))
        self.assertContains(response, "3 transactions")
        self.assertContains(response, "1 reminder")

    def test_empty_account_shows_no_data_loss_warning(self):
        response = self.client.get(reverse("finance:account_delete", args=[self.account.pk]))
        self.assertNotContains(response, "will also delete")


class CategoryDeleteTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.food = Category.objects.create(name="Food")
        self.groceries = Category.objects.create(name="Groceries", parent=self.food)
        self.organic = Category.objects.create(name="Organic", parent=self.groceries)
        self.txn = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.organic, amount=Decimal("-5.00"),
        )

    def test_confirmation_lists_every_subcategory_that_would_be_deleted(self):
        response = self.client.get(reverse("finance:category_delete", args=[self.food.pk]))
        self.assertContains(response, "also delete 2 subcategories")
        self.assertContains(response, "Food &gt; Groceries")
        self.assertContains(response, "Groceries &gt; Organic")
        self.assertContains(response, "1 transaction in")

    def test_leaf_category_shows_no_subcategory_warning(self):
        response = self.client.get(reverse("finance:category_delete", args=[self.organic.pk]))
        self.assertNotContains(response, "subcategor")

    def test_deleting_keeps_transactions_uncategorized(self):
        self.client.post(reverse("finance:category_delete", args=[self.food.pk]))
        # The account's own transfer category (Phase 3) is unrelated and stays.
        self.assertFalse(Category.objects.filter(account__isnull=True).exists())
        self.txn.refresh_from_db()
        self.assertIsNone(self.txn.category)


class TransactionListTests(LoggedInTestCase):
    def test_all_transactions_shown_with_a_fixed_number_of_queries(self):
        parent = Category.objects.create(name="Parent")
        categories = [Category.objects.create(name=f"Sub {i}", parent=parent) for i in range(5)]
        payee = Payee.objects.create(name="Shop")
        Transaction.objects.bulk_create(
            Transaction(account=self.account, date=date(2026, 1, 1), payee=payee,
                        category=categories[i % 5], amount=Decimal("-1.00"))
            for i in range(600)
        )
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("finance:transaction_list"))
        self.assertEqual(len(response.context["transactions"]), 600)
        self.assertLess(len(queries.captured_queries), 15)
