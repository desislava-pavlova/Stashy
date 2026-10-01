"""Tests for: payee list category dedup, dashboard account grouping, donut center currency."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Account, Category, Payee, Transaction


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


class PayeeListDistinctCategoriesTests(LoggedInTestCase):
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


class DashboardAccountGroupingTests(LoggedInTestCase):
    def test_accounts_panel_groups_like_the_accounts_page_and_skips_inactive(self):
        # self.account ("Checking") defaults to Cash - physical, same group as "Alpha cash".
        Account.objects.create(name="Zeta savings", account_type=Account.AccountType.SAVINGS)
        Account.objects.create(name="Alpha cash", account_type=Account.AccountType.CASH)
        Account.objects.create(name="Debit account", account_type=Account.AccountType.DEBIT_CARD)
        Account.objects.create(name="Old wallet", account_type=Account.AccountType.CASH, is_active=False)
        response = self.client.get(reverse("finance:dashboard"))
        groups = response.context["account_groups"]
        self.assertEqual([g["key"] for g in groups], ["physical", "digital", "illiquid"])
        self.assertEqual([a.name for a in groups[0]["accounts"]], ["Alpha cash", "Checking"])
        all_names = [a.name for g in groups for a in g["accounts"]]
        self.assertNotIn("Old wallet", all_names)

    def test_a_divider_appears_between_groups_but_not_before_the_first(self):
        Account.objects.create(name="Savings", account_type=Account.AccountType.SAVINGS)
        response = self.client.get(reverse("finance:dashboard"))
        html = response.content.decode()
        # Checking (physical/digital, default type) and Savings (illiquid) are two groups.
        self.assertEqual(html.count("account-group-divider"), 1)


class DashboardDonutCurrencyTests(LoggedInTestCase):
    def test_spending_panel_carries_the_currency_symbol_for_the_donut_center(self):
        # 2026-09-14 follow-up: the donut needs the actual symbol ("€"), not the bare ISO
        # code, to match how every other amount on the page already displays via |moneyc.
        Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("-10.00"))
        response = self.client.get(reverse("finance:dashboard"))
        self.assertContains(response, 'data-main-currency="€"')
        self.assertContains(response, 'data-currency-suffix="1"')
