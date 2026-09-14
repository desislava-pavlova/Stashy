"""Tests for Phase 3: accounts (taxonomy, currency), transfer categories, transaction
numbering, and regional preferences (D1, D2, D7, D9, D10)."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import regional
from .currency import RateTable, convert_balances
from .forms import CategoryForm
from .models import Account, Category, ExchangeRate, Payee, Profile, Transaction, grouped_accounts
from .reporting import income_and_spending, spending_tree


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)


class OpeningBalanceTests(LoggedInTestCase):
    def test_creating_an_account_with_an_opening_balance_makes_a_real_transaction(self):
        response = self.client.post(reverse("finance:account_add"), {
            "name": "Savings", "account_type": "savings", "currency": "EUR", "is_active": "on",
            "opening_balance": "500.00", "opening_balance_date": "01/01/2026",
        })
        self.assertEqual(response.status_code, 302)
        account = Account.objects.get(name="Savings")
        self.assertEqual(account.balance, Decimal("500.00"))

        opening = Transaction.objects.get(account=account)
        self.assertEqual(opening.amount, Decimal("500.00"))
        self.assertEqual(opening.date, date(2026, 1, 1))
        self.assertEqual(opening.payee.name, Payee.OPENING_BALANCE)
        self.assertEqual(opening.category, account.transfer_category)
        self.assertEqual(opening.memo, "Opening Balance")

    def test_an_opening_balance_is_a_regular_editable_transaction(self):
        self.client.post(reverse("finance:account_add"), {
            "name": "Cash", "account_type": "cash", "currency": "EUR", "is_active": "on",
            "opening_balance": "20", "opening_balance_date": "01/01/2026",
        })
        opening = Transaction.objects.get(account__name="Cash")
        response = self.client.post(reverse("finance:transaction_edit", args=[opening.pk]), {
            "account": opening.account_id, "date": "02/01/2026", "payee_name": "Opening Balance",
            "category": opening.category_id, "memo": "Fixed", "amount": "25.00",
        })
        self.assertEqual(response.status_code, 302)
        opening.refresh_from_db()
        self.assertEqual(opening.amount, Decimal("25.00"))

    def test_no_opening_balance_means_no_transaction(self):
        self.client.post(reverse("finance:account_add"), {
            "name": "Wallet", "account_type": "cash", "currency": "EUR", "is_active": "on",
        })
        account = Account.objects.get(name="Wallet")
        self.assertEqual(account.balance, Decimal("0.00"))
        self.assertFalse(Transaction.objects.filter(account=account).exists())


class TransferCategoryTests(TestCase):
    def test_every_account_gets_its_own_transfer_category(self):
        account = Account.objects.create(name="Checking")
        category = account.transfer_category
        self.assertEqual(category.category_type, Category.CategoryType.TRANSFER)
        self.assertTrue(category.is_system_managed)
        self.assertIn("Checking", category.name)

    def test_renaming_the_account_renames_its_transfer_category(self):
        account = Account.objects.create(name="Checking")
        account.name = "Everyday"
        account.save()
        account.transfer_category.refresh_from_db()
        self.assertIn("Everyday", account.transfer_category.name)

    def test_deleting_the_account_deletes_its_transfer_category(self):
        account = Account.objects.create(name="Checking")
        category_id = account.transfer_category.pk
        account.delete()
        self.assertFalse(Category.objects.filter(pk=category_id).exists())

    def test_category_form_rejects_a_system_managed_category(self):
        account = Account.objects.create(name="Checking")
        form = CategoryForm({"name": "Renamed", "category_type": "expense"}, instance=account.transfer_category)
        self.assertFalse(form.is_valid())

    def test_transfer_type_is_not_an_available_choice_when_creating_a_category(self):
        form = CategoryForm()
        self.assertNotIn(Category.CategoryType.TRANSFER, dict(form.fields["category_type"].choices))


class TransferButtonTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.checking = Account.objects.create(name="Checking")
        self.savings = Account.objects.create(name="Savings")

    def test_transfer_creates_two_paired_transactions(self):
        response = self.client.post(reverse("finance:account_transfer", args=[self.checking.pk]), {
            "to_account": self.savings.pk, "amount": "100.00", "date": "05/01/2026",
        })
        self.assertEqual(response.status_code, 302)

        out = Transaction.objects.get(account=self.checking)
        into = Transaction.objects.get(account=self.savings)
        self.assertEqual(out.amount, Decimal("-100.00"))
        self.assertEqual(into.amount, Decimal("100.00"))
        self.assertEqual(out.category, self.checking.transfer_category)
        self.assertEqual(into.category, self.savings.transfer_category)
        self.assertEqual(out.payee.name, Payee.TRANSFER_MONEY)
        self.assertEqual(out.payee_id, into.payee_id)
        self.assertIn("Savings", out.memo)
        self.assertIn("Checking", into.memo)

    def test_cannot_transfer_to_the_same_account(self):
        form_response = self.client.get(reverse("finance:account_transfer", args=[self.checking.pk]))
        options = form_response.context["form"].fields["to_account"].queryset
        self.assertNotIn(self.checking, options)


class TransactionNumberingTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Checking")

    def test_numbers_start_at_one_and_increase(self):
        first = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("-1.00"))
        second = Transaction.objects.create(account=self.account, date=date(2026, 1, 2), amount=Decimal("-1.00"))
        self.assertEqual((first.transaction_number, second.transaction_number), (1, 2))

    def test_numbering_resets_each_calendar_year(self):
        Transaction.objects.create(account=self.account, date=date(2026, 12, 31), amount=Decimal("-1.00"))
        next_year = Transaction.objects.create(account=self.account, date=date(2027, 1, 1), amount=Decimal("-1.00"))
        self.assertEqual(next_year.transaction_number, 1)

    def test_numbering_is_shared_across_accounts_not_independent(self):
        # Revised D9 (13/09/2026): one sequence per year, shared by every account - not one
        # sequence per account. A second account's first transaction continues the same count.
        other = Account.objects.create(name="Savings")
        Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("-1.00"))
        first_on_other = Transaction.objects.create(account=other, date=date(2026, 1, 1), amount=Decimal("-1.00"))
        self.assertEqual(first_on_other.transaction_number, 2)

    def test_a_chosen_number_is_kept_as_is(self):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("-1.00"), transaction_number=9999)
        self.assertEqual(txn.transaction_number, 9999)
        # Numbering isn't strictly unique/enforced (D9) — the next auto-assigned number just continues past it.
        following = Transaction.objects.create(account=self.account, date=date(2026, 1, 2), amount=Decimal("-1.00"))
        self.assertEqual(following.transaction_number, 10000)


class TransferExclusionTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Checking")
        self.other = Account.objects.create(name="Savings")
        self.groceries = Category.objects.create(name="Groceries")

    def test_transfers_are_excluded_from_income_and_spending(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 6), category=self.account.transfer_category, amount=Decimal("-100.00"))
        totals = income_and_spending(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(totals["spending"], Decimal("20.00"))

    def test_transfers_are_excluded_from_the_spending_donut(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), category=self.groceries, amount=Decimal("-20.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 6), category=self.account.transfer_category, amount=Decimal("-100.00"))
        tree = spending_tree(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual([g["name"] for g in tree], ["Groceries"])


class CurrencyConversionTests(TestCase):
    def test_convert_uses_the_latest_rate(self):
        ExchangeRate.objects.create(base_currency="USD", quote_currency="EUR", rate=Decimal("0.9"), date=date(2026, 1, 1))
        ExchangeRate.objects.create(base_currency="USD", quote_currency="EUR", rate=Decimal("0.8"), date=date(2026, 2, 1))
        rates = RateTable("EUR")
        self.assertEqual(rates.convert(Decimal("100.00"), "USD"), Decimal("80.00"))

    def test_a_rate_stored_the_other_way_round_still_works(self):
        ExchangeRate.objects.create(base_currency="EUR", quote_currency="USD", rate=Decimal("1.25"), date=date(2026, 1, 1))
        rates = RateTable("EUR")
        self.assertEqual(rates.convert(Decimal("125.00"), "USD"), Decimal("100.00"))

    def test_accounts_missing_a_rate_are_reported_but_others_still_total(self):
        eur_account = Account.objects.create(name="Checking", currency="EUR")
        Transaction.objects.create(account=eur_account, date=date(2026, 1, 1), amount=Decimal("50.00"))
        yen_account = Account.objects.create(name="Japan trip", currency="JPY")
        Transaction.objects.create(account=yen_account, date=date(2026, 1, 1), amount=Decimal("1000.00"))

        total, missing = convert_balances([eur_account, yen_account], "EUR")
        self.assertEqual(total, Decimal("50.00"))
        self.assertEqual(missing, [yen_account])


class AccountGroupingTests(TestCase):
    def test_groups_follow_the_liquidity_order_then_name(self):
        Account.objects.create(name="Zeta savings", account_type=Account.AccountType.SAVINGS)
        Account.objects.create(name="Alpha cash", account_type=Account.AccountType.CASH)
        Account.objects.create(name="Beta checking", account_type=Account.AccountType.DEBIT_CARD)
        Account.objects.create(name="Old wallet", account_type=Account.AccountType.CASH, is_active=False)

        groups = grouped_accounts(Account.objects.all())
        self.assertEqual([g["key"] for g in groups], ["physical", "digital", "illiquid", "inactive"])
        self.assertEqual([a.name for a in groups[0]["accounts"]], ["Alpha cash"])
        self.assertEqual([a.name for a in groups[-1]["accounts"]], ["Old wallet"])


class RegionalPreferencesTests(SimpleTestCase):
    def test_format_number_respects_the_active_style(self):
        comma_style = regional.Preferences(number_format="dot_comma")
        self.assertEqual(regional.format_number(Decimal("1234.5"), preferences=comma_style), "1.234,50")

    def test_format_date_respects_the_active_style(self):
        ymd = regional.Preferences(date_format="ymd")
        self.assertEqual(regional.format_date(date(2026, 4, 3), preferences=ymd), "2026-04-03")

    def test_date_format_detected_from_a_windows_short_date_pattern(self):
        self.assertEqual(regional.date_format_from_pattern("d.M.yyyy 'г.'"), "dmy")
        self.assertEqual(regional.date_format_from_pattern("M/d/yyyy"), "mdy")
        self.assertEqual(regional.date_format_from_pattern("yyyy-MM-dd"), "ymd")

    def test_number_format_detected_from_separators(self):
        self.assertEqual(regional.number_format_from_separators(" ", "."), "space_dot")
        self.assertEqual(regional.number_format_from_separators(",", "."), "comma_dot")
        self.assertEqual(regional.number_format_from_separators(".", ","), "dot_comma")


class ProfileTests(TestCase):
    def test_profile_is_created_once_and_then_left_alone(self):
        user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        first = Profile.for_user(user)
        first.date_format = "ymd"
        first.save()
        second = Profile.for_user(user)
        self.assertEqual(second.date_format, "ymd")
        self.assertEqual(Profile.objects.filter(user=user).count(), 1)
