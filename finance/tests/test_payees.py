"""Payees: one payee per name whatever the case, what was used with each category before,
and the payee list and detail pages."""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.urls import reverse

from audit.models import AuditLogEntry

from ..forms import PayeeForm
from ..models import Account, Category, Payee, Transaction, name_key
from .base import LoggedInTestCase, LoggedInWithAccountTestCase, StashTestCase


class NameKeyTests(StashTestCase):
    def test_case_is_folded_in_every_alphabet(self):
        self.assertEqual(name_key("  LIDL "), "lidl")
        self.assertEqual(name_key("ЛИДЛ"), name_key("лидл"))
        self.assertEqual(name_key("Straße"), name_key("STRASSE"))

    def test_save_keeps_the_key_in_step_even_with_update_fields(self):
        payee = Payee.objects.create(name="Corner Shop")
        payee.name = "ÜBER Shop"
        payee.save(update_fields=["name"])
        payee.refresh_from_db()
        self.assertEqual(payee.name_key, "über shop")


class PayeeUniquenessTests(StashTestCase):
    """2.4 (F3): one payee per name, whatever the case."""

    def test_the_database_rejects_a_case_duplicate(self):
        Payee.objects.create(name="Lidl")
        with self.assertRaises(IntegrityError), transaction.atomic(using=settings.TEST_STASH):
            Payee.objects.create(name="LIDL")

    def test_the_database_rejects_a_cyrillic_case_duplicate(self):
        # SQLite's own lower() leaves Cyrillic alone - the reason for the name_key column.
        Payee.objects.create(name="Лидл")
        with self.assertRaises(IntegrityError), transaction.atomic(using=settings.TEST_STASH):
            Payee.objects.create(name="ЛИДЛ")

    def test_resolve_keeps_the_spelling_already_saved(self):
        lidl = Payee.objects.create(name="Лидл")
        self.assertEqual(Payee.objects.resolve("лидл"), lidl)
        self.assertEqual(Payee.objects.count(), 1)

    def test_system_payees_reuse_a_payee_typed_in_another_case(self):
        typed = Payee.objects.create(name="opening balance")
        self.assertEqual(Payee.system(Payee.OPENING_BALANCE), typed)

    def test_the_form_rejects_a_case_duplicate_with_a_clear_message(self):
        Payee.objects.create(name="Lidl")
        form = PayeeForm(data={"name": "lidl", "is_active": True})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["name"], ["A payee with this name already exists."])

    def test_a_payee_can_change_the_case_of_its_own_name(self):
        payee = Payee.objects.create(name="lidl")
        form = PayeeForm(data={"name": "Lidl", "is_active": True}, instance=payee)
        self.assertTrue(form.is_valid(), form.errors)


class PayeeResolutionViewTests(StashTestCase):
    """2.4: every way in - the transaction form, the import and autofill - finds the saved payee."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        cls.account = Account.objects.create(name="Checking")
        cls.lidl = Payee.objects.create(name="Lidl")

    def setUp(self):
        self.client.force_login(self.user)

    def test_typing_lidl_on_a_transaction_saves_it_as_lidl(self):
        self.client.post(
            reverse("finance:transaction_add"),
            {
                "account": self.account.pk,
                "date": "05/09/2026",
                "payee_name": "lidl",
                "amount": "-12.00",
            },
        )
        txn = Transaction.objects.get(amount=Decimal("-12.00"))
        self.assertEqual(txn.payee, self.lidl)
        self.assertEqual(txn.payee.name, "Lidl")

    def test_an_import_reuses_the_saved_payee_and_logs_the_new_one_as_imported(self):
        content = b"Date,Payee,Memo,Amount\n03/09/2026,LIDL,,-5.00\n04/09/2026,Rewe,,-6.00\n"
        self.client.post(
            reverse("finance:import_transactions"),
            {"account": self.account.pk, "file": SimpleUploadedFile("bank.csv", content)},
        )
        self.assertEqual(Transaction.objects.get(amount=Decimal("-5.00")).payee, self.lidl)
        self.assertEqual(Payee.objects.count(), 2)
        rewe_row = AuditLogEntry.objects.get(table="finance_payee", action="insert", row_label="Rewe")
        self.assertEqual(rewe_row.reason, "Imported from bank.csv")

    def test_autofill_finds_the_payee_in_any_case(self):
        Transaction.objects.create(account=self.account, date=date(2026, 9, 1), payee=self.lidl, amount=Decimal("-3"))
        response = self.client.get(reverse("finance:api_payee_autofill"), {"name": "LIDL"})
        self.assertEqual(len(response.json()["combos"]), 1)


class PayeeHistoryTests(StashTestCase):
    """Payee memory (Phase 6) is computed live from transaction history, per category —
    there's no stored default to fall out of sync."""

    @classmethod
    def setUpTestData(cls):
        cls.account = Account.objects.create(name="Checking")
        cls.groceries = Category.objects.create(name="Groceries")
        cls.household = Category.objects.create(name="Household")
        cls.payee = Payee.objects.create(name="Lidl")

    def test_one_transaction_gives_one_history_entry(self):
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 5),
            payee=self.payee,
            category=self.groceries,
            memo="weekly shop",
            amount=Decimal("-30.00"),
        )
        history = self.payee.category_history()
        self.assertEqual(len(history), 1)
        self.assertEqual((history[0].category, history[0].memo), (self.groceries, "weekly shop"))

    def test_two_categories_give_two_entries_newest_first(self):
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 5),
            payee=self.payee,
            category=self.groceries,
            memo="weekly shop",
            amount=Decimal("-30.00"),
        )
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 10),
            payee=self.payee,
            category=self.household,
            memo="cleaning supplies",
            amount=Decimal("-12.00"),
        )
        history = self.payee.category_history()
        self.assertEqual([h.category for h in history], [self.household, self.groceries])

    def test_history_reflects_the_latest_by_date_not_by_entry_order(self):
        # Entered second, but dated earlier - shouldn't override the later-dated one.
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 20),
            payee=self.payee,
            category=self.groceries,
            memo="second shop",
            amount=Decimal("-40.00"),
        )
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 5),
            payee=self.payee,
            category=self.groceries,
            memo="first shop",
            amount=Decimal("-30.00"),
        )
        history = self.payee.category_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].memo, "second shop")

    def test_editing_an_unrelated_field_does_not_change_the_history(self):
        txn = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 5),
            payee=self.payee,
            category=self.groceries,
            memo="weekly shop",
            amount=Decimal("-30.00"),
        )
        txn.reconciled = True
        txn.save()
        self.assertEqual(self.payee.category_history()[0].memo, "weekly shop")

    def test_uncategorized_transactions_form_their_own_entry(self):
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 5),
            payee=self.payee,
            amount=Decimal("-10.00"),
        )
        history = self.payee.category_history()
        self.assertEqual(len(history), 1)
        self.assertIsNone(history[0].category)


class PayeeFormTests(StashTestCase):
    def test_form_no_longer_has_default_fields(self):
        form = PayeeForm()
        for field in ("default_category", "default_memo", "default_amount"):
            self.assertNotIn(field, form.fields)
        self.assertIn("name", form.fields)


class PayeeDetailTests(LoggedInWithAccountTestCase):
    def test_shows_every_category_with_its_last_memo_and_amount(self):
        payee = Payee.objects.create(name="Lidl")
        groceries = Category.objects.create(name="Groceries")
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 5),
            payee=payee,
            category=groceries,
            memo="weekly shop",
            amount=Decimal("-30.00"),
        )
        response = self.client.get(reverse("finance:payee_detail", args=[payee.pk]))
        self.assertContains(response, "weekly shop")
        self.assertContains(response, "Groceries")
        self.assertEqual(len(response.context["history"]), 1)

    def test_empty_history_shows_an_empty_state(self):
        payee = Payee.objects.create(name="New Shop")
        response = self.client.get(reverse("finance:payee_detail", args=[payee.pk]))
        self.assertContains(response, "No transactions yet")


class PayeeListFilterAndSortTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.groceries = Category.objects.create(name="Groceries")
        cls.household = Category.objects.create(name="Household")
        cls.lidl = Payee.objects.create(name="Lidl")
        cls.ikea = Payee.objects.create(name="Ikea")
        Transaction.objects.create(
            account=cls.account,
            date=date(2026, 1, 5),
            payee=cls.lidl,
            category=cls.groceries,
            memo="weekly shop",
            amount=Decimal("-30.00"),
        )
        Transaction.objects.create(
            account=cls.account,
            date=date(2026, 1, 6),
            payee=cls.ikea,
            category=cls.household,
            memo="shelves",
            amount=Decimal("-90.00"),
        )

    def rows(self, **params):
        response = self.client.get(reverse("finance:payee_list"), params)
        return response.context["rows"]

    def test_default_sort_is_by_name(self):
        names = [r["payee"].name for r in self.rows()]
        self.assertEqual(names, ["Ikea", "Lidl"])

    def test_search_by_name(self):
        names = [r["payee"].name for r in self.rows(q="lid")]
        self.assertEqual(names, ["Lidl"])

    def test_filter_by_category(self):
        names = [r["payee"].name for r in self.rows(category=self.household.pk)]
        self.assertEqual(names, ["Ikea"])

    def test_filter_by_uncategorized(self):
        Payee.objects.create(name="No Category Yet")
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 7),
            payee=Payee.objects.get(name="No Category Yet"),
            amount=Decimal("-5.00"),
        )
        names = [r["payee"].name for r in self.rows(category="none")]
        self.assertEqual(names, ["No Category Yet"])

    def test_filter_by_memo(self):
        names = [r["payee"].name for r in self.rows(memo="shelves")]
        self.assertEqual(names, ["Ikea"])

    def test_sort_by_amount(self):
        # Ascending: Ikea's -90.00 sorts before Lidl's -30.00.
        names = [r["payee"].name for r in self.rows(sort="amount")]
        self.assertEqual(names, ["Ikea", "Lidl"])

    def test_sort_by_usage(self):
        # Both have 1 use each until a second Lidl transaction is added - lets least-used
        # payees sort first, for spotting ones worth deleting (2026-09-14 follow-up).
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 2, 1),
            payee=self.lidl,
            category=self.groceries,
            memo="second visit",
            amount=Decimal("-10.00"),
        )
        names = [r["payee"].name for r in self.rows(sort="usage")]
        self.assertEqual(names, ["Ikea", "Lidl"])

    def test_each_row_shows_all_categories_total_amount_and_usage(self):
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 2, 1),
            payee=self.lidl,
            category=self.household,
            memo="one-off",
            amount=Decimal("-15.00"),
        )
        row = next(r for r in self.rows() if r["payee"] == self.lidl)
        self.assertEqual(row["categories_label"], "Groceries, Household")
        self.assertEqual(row["totals"], [("EUR", Decimal("-45.00"))])
        self.assertEqual(row["usage_count"], 2)


class CaseInsensitivePayeeTests(LoggedInWithAccountTestCase):
    def test_manual_entry_reuses_an_existing_payee_regardless_of_case(self):
        Payee.objects.create(name="Lidl")
        self.client.post(
            reverse("finance:transaction_add"),
            {
                "account": self.account.pk,
                "date": "05/01/2026",
                "payee_name": "lidl",
                "amount": "-5.00",
            },
        )
        self.assertEqual(Payee.objects.filter(name__iexact="lidl").count(), 1)
        self.assertEqual(Transaction.objects.get().payee.name, "Lidl")

    def test_import_reuses_an_existing_payee_regardless_of_case(self):
        Payee.objects.create(name="Lidl")
        upload = SimpleUploadedFile("bank.csv", b"Date,Payee,Memo,Amount\n05/01/2026,LIDL,,-5.00\n")
        self.client.post(reverse("finance:import_transactions"), {"account": self.account.pk, "file": upload})
        self.assertEqual(Payee.objects.filter(name__iexact="lidl").count(), 1)

    def test_a_genuinely_new_name_still_creates_a_payee(self):
        self.client.post(
            reverse("finance:transaction_add"),
            {
                "account": self.account.pk,
                "date": "05/01/2026",
                "payee_name": "Brand New Shop",
                "amount": "-5.00",
            },
        )
        self.assertTrue(Payee.objects.filter(name="Brand New Shop").exists())


class PayeeListDistinctCategoriesTests(LoggedInWithAccountTestCase):
    def test_the_same_category_used_many_times_lists_once(self):
        # This is a regression test for a real bug: Transaction's default ordering was riding
        # along into a values().distinct() query, so every date a category was used counted as
        # "different" - the exact category showed up once per transaction, not once overall.
        water = Category.objects.create(name="Water", category_type=Category.CategoryType.EXPENSE)
        payee = Payee.objects.create(name="City Water Co")
        for day in range(1, 9):
            Transaction.objects.create(
                account=self.account,
                date=date(2026, day, 1),
                payee=payee,
                category=water,
                amount=Decimal("-5.00"),
            )
        response = self.client.get(reverse("finance:payee_list"))
        row = next(r for r in response.context["rows"] if r["payee"] == payee)
        self.assertEqual(row["categories"], ["Water"])

    def test_two_real_categories_still_both_show(self):
        food = Category.objects.create(name="Food", category_type=Category.CategoryType.EXPENSE)
        fuel = Category.objects.create(name="Fuel", category_type=Category.CategoryType.EXPENSE)
        payee = Payee.objects.create(name="Shop")
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), payee=payee, category=food, amount=Decimal("-5.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 2), payee=payee, category=fuel, amount=Decimal("-5.00")
        )
        response = self.client.get(reverse("finance:payee_list"))
        row = next(r for r in response.context["rows"] if r["payee"] == payee)
        self.assertEqual(row["categories"], ["Food", "Fuel"])


class PayeeListCategoryOrderTests(LoggedInTestCase):
    def test_category_filter_is_ordered_like_other_category_dropdowns(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Category.objects.create(name="Supermarket", parent=groceries, category_type=Category.CategoryType.EXPENSE)
        Category.objects.create(name="Bakery", category_type=Category.CategoryType.EXPENSE)
        response = self.client.get(reverse("finance:payee_list"))
        # "Bakery" sorts before "Groceries", and "Supermarket" (indented) sits right after
        # "Groceries" rather than in a separate block of subcategories.
        body = response.content.decode().replace("&nbsp;", " ")
        self.assertLess(body.index("Bakery"), body.index(">Groceries<"))
        self.assertLess(body.index(">Groceries<"), body.index(">    Supermarket<"))
