"""The dashboard: the savings rate, the spending tree and donut, reminders due soon, account
groups, the welcome for an empty stash, and converting into the main currency."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.db import connections
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from ..models import Account, Category, ExchangeRate, Payee, Reminder, Transaction
from ..reporting import income_and_spending, savings_rate, spending_tree
from .base import LoggedInWithAccountTestCase, StashTestCase

# Tests that patch timezone.localdate pretend today is one of these days, so the fixed dates
# they set up keep their meaning (overdue, due soon, this month).
TODAY_12_SEPT = date(2026, 9, 12)
TODAY_14_SEPT = date(2026, 9, 14)


class SavingsRateTests(SimpleTestCase):
    def test_share_of_income_not_spent(self):
        self.assertEqual(savings_rate(Decimal("2000.00"), Decimal("500.00")), Decimal("75.0"))

    def test_spending_more_than_income_is_negative(self):
        self.assertEqual(savings_rate(Decimal("1000"), Decimal("1250")), Decimal("-25.0"))

    def test_no_income_means_no_rate(self):
        self.assertIsNone(savings_rate(Decimal("0"), Decimal("80")))


class SpendingTreeTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.account = Account.objects.create(name="Checking")
        cls.food = Category.objects.create(name="Food")
        cls.groceries = Category.objects.create(name="Groceries", parent=cls.food)
        cls.organic = Category.objects.create(name="Organic", parent=cls.groceries)
        cls.rent = Category.objects.create(name="Rent")

    def add(self, amount, category, on=date(2026, 9, 5)):
        Transaction.objects.create(account=self.account, date=on, category=category, amount=Decimal(amount))

    def test_groups_by_top_level_category_with_one_level_of_breakdown(self):
        self.add("-30.00", self.groceries)
        self.add("-12.50", self.organic)
        self.add("-7.50", self.food)
        self.add("-500.00", self.rent)
        self.add("-4.00", None)
        self.add("2000.00", None)
        self.add("-99.00", self.rent, on=date(2026, 8, 31))

        tree = spending_tree(date(2026, 9, 1), date(2026, 9, 30))

        # Uncategorized transactions are excluded from every report (R1, Phase 13) - the two
        # category=None rows above never show up here, on the dashboard or anywhere else.
        self.assertEqual([group["name"] for group in tree], ["Rent", "Food"])
        self.assertEqual(tree[0]["total"], 500.0)
        food = tree[1]
        self.assertEqual(food["total"], 50.0)
        self.assertEqual(
            food["children"],
            [
                {"id": self.groceries.pk, "name": "Groceries", "with_subcategories": True, "total": 42.5},
                {"id": self.food.pk, "name": "Food (general)", "with_subcategories": False, "total": 7.5},
            ],
        )

    def test_a_category_that_is_its_own_parent_does_not_hang(self):
        Category.objects.filter(pk=self.rent.pk).update(parent=self.rent)
        self.add("-10.00", self.rent)
        tree = spending_tree(date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(tree[0]["total"], 10.0)


@mock.patch("django.utils.timezone.localdate", return_value=TODAY_12_SEPT)
class DashboardTests(LoggedInWithAccountTestCase):
    def test_shows_last_months_savings_rate(self, _):
        # A category's type decides income vs. spending (R1, Phase 13) - amount alone no longer
        # does, so these need real Income/Expense categories to count toward the rate.
        salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        for on, amount, category in [
            (date(2026, 8, 1), "2000.00", salary),
            (date(2026, 8, 20), "-500.00", rent),
            (date(2026, 9, 3), "-40.00", rent),
        ]:
            Transaction.objects.create(account=self.account, date=on, amount=Decimal(amount), category=category)
        response = self.client.get(reverse("finance:dashboard"))
        self.assertEqual(response.context["savings_rate"], Decimal("75.0"))
        self.assertEqual(response.context["savings_bar"], 75)
        self.assertEqual(response.context["month_spending"], Decimal("40.00"))
        self.assertContains(response, "Saved in August")
        self.assertContains(response, "75.0%")

    def test_lists_reminders_due_within_a_week_including_overdue(self, _):
        def reminder(label, due):
            payee = Payee.objects.create(name=label)
            return Reminder.objects.create(
                account=self.account, payee=payee, amount=Decimal("10.00"), next_due_date=due
            )

        reminder("Overdue bill", date(2026, 9, 10))
        reminder("Due in a week", date(2026, 9, 19))
        reminder("Too far away", date(2026, 9, 20))
        response = self.client.get(reverse("finance:dashboard"))
        self.assertEqual([r.payee.name for r in response.context["due_soon"]], ["Overdue bill", "Due in a week"])
        self.assertContains(response, "badge-overdue")

    def test_brand_new_user_gets_a_welcome_instead_of_empty_widgets(self, _):
        self.account.delete()
        response = self.client.get(reverse("finance:dashboard"))
        self.assertContains(response, "start your stash")
        self.assertNotContains(response, 'id="spending-donut"')


class DashboardExcludesResolvedRemindersTests(LoggedInWithAccountTestCase):
    @mock.patch("django.utils.timezone.localdate", return_value=TODAY_14_SEPT)
    def test_a_paid_reminder_due_soon_does_not_show_on_the_dashboard(self, _):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 9, 16), amount=Decimal("-1.00"))
        Reminder.objects.create(
            account=self.account,
            amount=Decimal("1"),
            next_due_date=date(2026, 9, 16),
            frequency="one_time",
            last_entered_transaction=txn,
        )
        response = self.client.get(reverse("finance:dashboard"))
        self.assertEqual(list(response.context["due_soon"]), [])


class DashboardCurrencyTests(LoggedInWithAccountTestCase):
    """1.8 (F30): the dashboard converts into the main currency, like Reports."""

    def test_spending_in_another_currency_is_converted(self):
        today = date.today()
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        usd = Account.objects.create(name="US card", currency="USD")
        ExchangeRate.objects.create(base_currency="USD", quote_currency="EUR", rate=Decimal("0.5"), date=today)
        Transaction.objects.create(account=self.account, date=today, category=groceries, amount=Decimal("-10.00"))
        Transaction.objects.create(account=usd, date=today, category=groceries, amount=Decimal("-100.00"))

        response = self.client.get(reverse("finance:dashboard"))

        self.assertEqual(response.context["main_currency"], "EUR")
        self.assertEqual(response.context["month_spending"], Decimal("60.00"))
        self.assertEqual(response.context["spending_tree"][0]["total"], 60.0)


class SpendingTreeRefundTests(StashTestCase):
    """1.9 (F31): every category is its own net total; a bigger refund shows as negative."""

    @classmethod
    def setUpTestData(cls):
        cls.account = Account.objects.create(name="Checking")
        expense = Category.CategoryType.EXPENSE
        cls.groceries = Category.objects.create(name="Groceries", category_type=expense)
        cls.rent = Category.objects.create(name="Rent", category_type=expense)

    def add(self, amount, category):
        Transaction.objects.create(
            account=self.account, date=date(2026, 9, 10), category=category, amount=Decimal(amount)
        )

    def test_a_refund_bigger_than_the_spend_is_negative_and_nets_the_total(self):
        self.add("-40.00", self.groceries)
        self.add("100.00", self.groceries)
        self.add("-500.00", self.rent)

        tree = spending_tree(date(2026, 9, 1), date(2026, 9, 30))

        self.assertEqual([(g["name"], g["total"]) for g in tree], [("Rent", 500.0), ("Groceries", -60.0)])
        self.assertEqual(tree[1]["children"][0]["total"], -60.0)
        spending = income_and_spending(date(2026, 9, 1), date(2026, 9, 30))["spending"]
        self.assertEqual(Decimal(str(sum(g["total"] for g in tree))), spending)

    def test_a_subcategory_refund_nets_inside_its_parent(self):
        organic = Category.objects.create(
            name="Organic", parent=self.groceries, category_type=Category.CategoryType.EXPENSE
        )
        self.add("-30.00", self.groceries)
        self.add("10.00", organic)
        group = spending_tree(date(2026, 9, 1), date(2026, 9, 30))[0]
        self.assertEqual(group["total"], 20.0)
        self.assertEqual([c["total"] for c in group["children"]], [30.0, -10.0])


class DashboardAccountGroupingTests(LoggedInWithAccountTestCase):
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


class DashboardDonutCurrencyTests(LoggedInWithAccountTestCase):
    def test_spending_panel_carries_the_currency_symbol_for_the_donut_center(self):
        # 2026-09-14 follow-up: the donut needs the actual symbol ("€"), not the bare ISO
        # code, to match how every other amount on the page already displays via |moneyc.
        Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("-10.00"))
        response = self.client.get(reverse("finance:dashboard"))
        self.assertContains(response, 'data-main-currency="€"')
        self.assertContains(response, 'data-currency-suffix="1"')


@mock.patch("django.utils.timezone.localdate", return_value=date(2026, 9, 12))
class DueSoonQueryTests(LoggedInWithAccountTestCase):
    """8.15 (F56): the "due soon" list loads each reminder's payee and category with the list."""

    def queries_with(self, count):
        food = Category.objects.get_or_create(name="Food", category_type=Category.CategoryType.EXPENSE)[0]
        for i in range(count):
            Reminder.objects.create(
                account=self.account,
                payee=Payee.objects.create(name=f"Shop {count}-{i}")
                if i % 2
                else None,  # no payee: the category shows
                category=food.create_subcategory(f"Groceries {count}-{i}"),
                amount=Decimal("10.00"),
                next_due_date=date(2026, 9, 14),
                frequency="one_time",
            )
        url = reverse("finance:dashboard")
        self.client.get(url)  # the first page of a session also creates its Profile
        with CaptureQueriesContext(connections[settings.TEST_STASH]) as queries:
            response = self.client.get(url)
        self.assertEqual(len(response.context["due_soon"]), Reminder.objects.count())
        return len(queries)

    def test_more_reminders_need_no_more_queries(self, _today):
        self.assertEqual(self.queries_with(1), self.queries_with(4))
