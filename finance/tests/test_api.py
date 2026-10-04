"""The JSON endpoints behind the payee autofill and the payee list."""

import json
from datetime import date
from decimal import Decimal

from django.urls import reverse

from ..models import Category, Payee, Transaction
from .base import LoggedInWithAccountTestCase


class ApiPayeeAutofillTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.payee = Payee.objects.create(name="Lidl")
        cls.groceries = Category.objects.create(name="Groceries")
        cls.household = Category.objects.create(name="Household")

    def fetch(self, name):
        response = self.client.get(reverse("finance:api_payee_autofill"), {"name": name})
        return json.loads(response.content)

    def test_no_history_returns_no_combos(self):
        self.assertEqual(self.fetch("Lidl"), {"combos": []})

    def test_unknown_payee_returns_no_combos(self):
        self.assertEqual(self.fetch("Nonexistent Shop"), {"combos": []})

    def test_one_category_gives_one_combo(self):
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 5),
            payee=self.payee,
            category=self.groceries,
            memo="weekly shop",
            amount=Decimal("-30.00"),
        )
        combos = self.fetch("Lidl")["combos"]
        self.assertEqual(len(combos), 1)
        self.assertEqual(combos[0]["category_id"], self.groceries.pk)
        self.assertEqual(combos[0]["memo"], "weekly shop")
        self.assertEqual(combos[0]["amount"], "-30.00")

    def test_two_categories_give_two_combos_newest_first(self):
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
            memo="cleaning",
            amount=Decimal("-12.00"),
        )
        combos = self.fetch("Lidl")["combos"]
        self.assertEqual([c["category_id"] for c in combos], [self.household.pk, self.groceries.pk])

    def test_lookup_is_case_insensitive(self):
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 5),
            payee=self.payee,
            category=self.groceries,
            amount=Decimal("-30.00"),
        )
        self.assertEqual(len(self.fetch("lidl")["combos"]), 1)


class ApiPayeeListTests(LoggedInWithAccountTestCase):
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
