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
        self.assertEqual(
            [c["category_id"] for c in combos], [self.household.pk, self.groceries.pk]
        )

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

    def test_a_system_payee_typed_in_another_case_is_excluded_too(self):
        # S53: Payee.system() reuses a payee typed as "opening balance",
        # so the names are compared through name_key, not by exact
        # spelling. A payee that only starts like one stays.
        Payee.objects.create(name="opening balance")
        Payee.objects.create(name="TRANSFER MONEY")
        Payee.objects.create(name="Opening Balance Fee")
        names = json.loads(self.client.get(reverse("finance:api_payee_list")).content)["payees"]
        self.assertEqual(names, ["Opening Balance Fee"])

    def test_every_system_payee_is_left_out_in_any_case(self):
        # S53: all three names, not just the first two (Reconciliation
        # Adjustment is the easy one to forget).
        self.assertEqual(len(Payee.SYSTEM_NAMES), 3)
        for name in Payee.SYSTEM_NAMES:
            Payee.objects.create(name=name.upper())
            Payee.objects.create(name=f"{name} fee")
        names = json.loads(self.client.get(reverse("finance:api_payee_list")).content)["payees"]
        self.assertEqual(sorted(names), sorted(f"{name} fee" for name in Payee.SYSTEM_NAMES))

    def test_the_names_come_in_alphabetical_order_in_any_alphabet(self):
        # S4: sorted on name_key, so "lidl" isn't after "Zara", nor a
        # lower-case Cyrillic name after a capital one.
        for name in ("Zara", "lidl", "бензин", "Аптека", "Вода"):
            Payee.objects.create(name=name)
        names = json.loads(self.client.get(reverse("finance:api_payee_list")).content)["payees"]
        self.assertEqual(names, ["lidl", "Zara", "Аптека", "бензин", "Вода"])

    def test_inactive_payees_are_excluded(self):
        Payee.objects.create(name="Closed Shop", is_active=False)
        response = self.client.get(reverse("finance:api_payee_list"))
        self.assertNotIn("Closed Shop", json.loads(response.content)["payees"])
