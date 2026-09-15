"""Tests for Phase 6: per-category payee memory, payee detail, list filters/sorting."""
import json
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import PayeeForm
from .models import Account, Category, Payee, Transaction


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


class PayeeFormTests(TestCase):
    def test_form_no_longer_has_default_fields(self):
        form = PayeeForm()
        for field in ("default_category", "default_memo", "default_amount"):
            self.assertNotIn(field, form.fields)
        self.assertIn("name", form.fields)


class ApiPayeeAutofillTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.payee = Payee.objects.create(name="Lidl")
        self.groceries = Category.objects.create(name="Groceries")
        self.household = Category.objects.create(name="Household")

    def fetch(self, name):
        response = self.client.get(reverse("finance:api_payee_autofill"), {"name": name})
        return json.loads(response.content)

    def test_no_history_returns_no_combos(self):
        self.assertEqual(self.fetch("Lidl"), {"combos": []})

    def test_unknown_payee_returns_no_combos(self):
        self.assertEqual(self.fetch("Nonexistent Shop"), {"combos": []})

    def test_one_category_gives_one_combo(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), payee=self.payee,
            category=self.groceries, memo="weekly shop", amount=Decimal("-30.00"),
        )
        combos = self.fetch("Lidl")["combos"]
        self.assertEqual(len(combos), 1)
        self.assertEqual(combos[0]["category_id"], self.groceries.pk)
        self.assertEqual(combos[0]["memo"], "weekly shop")
        self.assertEqual(combos[0]["amount"], "-30.00")

    def test_two_categories_give_two_combos_newest_first(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), payee=self.payee,
            category=self.groceries, memo="weekly shop", amount=Decimal("-30.00"),
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 10), payee=self.payee,
            category=self.household, memo="cleaning", amount=Decimal("-12.00"),
        )
        combos = self.fetch("Lidl")["combos"]
        self.assertEqual([c["category_id"] for c in combos], [self.household.pk, self.groceries.pk])

    def test_lookup_is_case_insensitive(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), payee=self.payee,
            category=self.groceries, amount=Decimal("-30.00"),
        )
        self.assertEqual(len(self.fetch("lidl")["combos"]), 1)


class ApiPayeeListTests(LoggedInTestCase):
    def test_system_payees_are_excluded(self):
        Payee.objects.create(name="Regular Shop")
        Payee.system(Payee.OPENING_BALANCE)
        Payee.system(Payee.TRANSFER_MONEY)
        response = self.client.get(reverse("finance:api_payee_list"))
        names = json.loads(response.content)["payees"]
        self.assertIn("Regular Shop", names)
        self.assertNotIn(Payee.OPENING_BALANCE, names)
        self.assertNotIn(Payee.TRANSFER_MONEY, names)

    def test_inactive_payees_are_excluded(self):
        Payee.objects.create(name="Closed Shop", is_active=False)
        response = self.client.get(reverse("finance:api_payee_list"))
        self.assertNotIn("Closed Shop", json.loads(response.content)["payees"])


class PayeeDetailTests(LoggedInTestCase):
    def test_shows_every_category_with_its_last_memo_and_amount(self):
        payee = Payee.objects.create(name="Lidl")
        groceries = Category.objects.create(name="Groceries")
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), payee=payee,
            category=groceries, memo="weekly shop", amount=Decimal("-30.00"),
        )
        response = self.client.get(reverse("finance:payee_detail", args=[payee.pk]))
        self.assertContains(response, "weekly shop")
        self.assertContains(response, "Groceries")
        self.assertEqual(len(response.context["history"]), 1)

    def test_empty_history_shows_an_empty_state(self):
        payee = Payee.objects.create(name="New Shop")
        response = self.client.get(reverse("finance:payee_detail", args=[payee.pk]))
        self.assertContains(response, "No transactions yet")


class PayeeListFilterAndSortTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.groceries = Category.objects.create(name="Groceries")
        self.household = Category.objects.create(name="Household")
        self.lidl = Payee.objects.create(name="Lidl")
        self.ikea = Payee.objects.create(name="Ikea")
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), payee=self.lidl,
            category=self.groceries, memo="weekly shop", amount=Decimal("-30.00"),
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 6), payee=self.ikea,
            category=self.household, memo="shelves", amount=Decimal("-90.00"),
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
        Transaction.objects.create(account=self.account, date=date(2026, 1, 7), payee=Payee.objects.get(name="No Category Yet"), amount=Decimal("-5.00"))
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
            account=self.account, date=date(2026, 2, 1), payee=self.lidl,
            category=self.groceries, memo="second visit", amount=Decimal("-10.00"),
        )
        names = [r["payee"].name for r in self.rows(sort="usage")]
        self.assertEqual(names, ["Ikea", "Lidl"])

    def test_each_row_shows_all_categories_total_amount_and_usage(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 2, 1), payee=self.lidl,
            category=self.household, memo="one-off", amount=Decimal("-15.00"),
        )
        row = next(r for r in self.rows() if r["payee"] == self.lidl)
        self.assertEqual(row["categories_label"], "Groceries, Household")
        self.assertEqual(row["totals"], [("EUR", Decimal("-45.00"))])
        self.assertEqual(row["usage_count"], 2)


class ImportCategoryGuessTests(LoggedInTestCase):
    def test_import_guesses_the_payees_most_recent_category(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        groceries = Category.objects.create(name="Groceries")
        payee = Payee.objects.create(name="Lidl")
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), payee=payee, category=groceries, amount=Decimal("-10.00"),
        )
        upload = SimpleUploadedFile("bank.csv", b"Date,Payee,Memo,Amount\n10/01/2026,Lidl,,-20.00\n")
        self.client.post(reverse("finance:import_transactions"), {"account": self.account.pk, "file": upload})
        imported = Transaction.objects.get(date=date(2026, 1, 10))
        self.assertEqual(imported.category, groceries)
