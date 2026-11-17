"""The dashboard: the savings rate, the spending tree and donut,
reminders due soon, account groups, the welcome for an empty stash, and
converting into the main currency."""

import re
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from ..models import Account, Category, ExchangeRate, Payee, Profile, Reminder, Transaction
from ..reporting import income_and_spending, savings_rate, spending_tree
from .base import (
    LEAKED_TEMPLATE_SYNTAX,
    TODAY_12_SEPT,
    TODAY_14_SEPT,
    LoggedInWithAccountTestCase,
    StashTestCase,
    create_reminders,
    leaked_syntax_in,
)

# Tests that patch timezone.localdate pretend today is one of these
# days, so the fixed dates they set up keep their meaning (overdue, due
# soon, this month).


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
        cls.food = Category.objects.create(name="Food", category_type=Category.CategoryType.EXPENSE)
        cls.groceries = cls.food.create_subcategory("Groceries")
        cls.bakery = cls.food.create_subcategory("Bakery")
        cls.rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)

    def add(self, amount, category, on=date(2026, 9, 5)):
        Transaction.objects.create(account=self.account, date=on, category=category, amount=Decimal(amount))

    def test_groups_by_top_level_category_with_one_level_of_breakdown(self):
        self.add("-30.00", self.groceries)
        self.add("-12.50", self.bakery)
        self.add("-7.50", self.food)
        self.add("-500.00", self.rent)
        self.add("-4.00", None)
        self.add("2000.00", None)
        self.add("-99.00", self.rent, on=date(2026, 8, 31))

        tree = spending_tree(date(2026, 9, 1), date(2026, 9, 30))

        # Uncategorized transactions are excluded from every report -
        # the two category=None rows above never show up here, on the
        # dashboard or anywhere else.
        self.assertEqual([group["name"] for group in tree], ["Rent", "Food"])
        self.assertEqual(tree[0]["total"], 500.0)
        food = tree[1]
        self.assertEqual(food["total"], 50.0)
        self.assertEqual(
            food["children"],
            [
                {"id": self.groceries.pk, "name": "Groceries", "with_subcategories": True, "total": 30.0},
                {"id": self.bakery.pk, "name": "Bakery", "with_subcategories": True, "total": 12.5},
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
        # A category's type decides income vs. spending - the amount
        # alone doesn't, so these need real Income/Expense categories to
        # count toward the rate.
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

    def test_a_month_with_only_a_refund_shows_negative_spending(self, _):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=self.account, date=date(2026, 9, 3), category=groceries, amount=Decimal("50.00")
        )
        response = self.client.get(reverse("finance:dashboard"))
        self.assertEqual(response.context["month_spending"], Decimal("-50.00"))

    def test_the_savings_rate_follows_the_stashs_number_format(self, _):
        """S33: 10.000,50 stashes see "71,2%", not "71.2%"."""
        profile = Profile.for_user(self.user)
        profile.number_format = "dot_comma"
        profile.save()
        salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        for on, amount, category in [(date(2026, 8, 1), "2000.00", salary), (date(2026, 8, 20), "-576.00", rent)]:
            Transaction.objects.create(account=self.account, date=on, amount=Decimal(amount), category=category)
        response = self.client.get(reverse("finance:dashboard"))
        self.assertContains(response, "71,2%")
        self.assertNotContains(response, "71.2%")

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


@mock.patch("django.utils.timezone.localdate", return_value=TODAY_12_SEPT)
class DashboardRefundAndSavingsRateEdgeTests(LoggedInWithAccountTestCase):
    """S33 and S50 on the dashboard: the "Saved in" tile for a negative
    rate and one over 100 (a refund makes that possible) in every number
    format, the bar that must stop at both ends, the Spent tile when
    only a refund came in, and a refund in another currency."""

    # number format -> the rate as shown for -25.0 and for 105.0
    EXPECTED = {
        "space_dot": ("-25.0%", "105.0%"),
        "comma_dot": ("-25.0%", "105.0%"),
        "dot_comma": ("-25,0%", "105,0%"),
        "space_comma": ("-25,0%", "105,0%"),
    }

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        cls.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)

    def add(self, on, amount, category, account=None):
        Transaction.objects.create(account=account or self.account, date=on, category=category, amount=Decimal(amount))

    def saved_tile(self, number_format):
        """The "Saved in August" tile as (its value's extra classes, its
        text, the bar's width)."""
        profile = Profile.for_user(self.user)
        profile.number_format = number_format
        profile.save()
        page = self.client.get(reverse("finance:dashboard")).content.decode()
        tile = re.search(
            r'Saved in August</div>\s*<div class="stat-value([^"]*)">([^<]*)</div>'
            r'\s*<div class="savings-bar" aria-hidden="true"><span style="width: (\d+)%">',
            page,
        )
        return tile.groups()

    def test_a_negative_rate_is_red_and_its_bar_is_empty_in_every_number_format(self, _):
        self.add(date(2026, 8, 1), "1000.00", self.salary)
        self.add(date(2026, 8, 20), "-1250.00", self.groceries)

        for number_format, shown in self.EXPECTED.items():
            with self.subTest(number_format=number_format):
                self.assertEqual(self.saved_tile(number_format), (" amount-negative", shown[0], "0"))

    def test_a_rate_over_100_is_not_red_and_its_bar_stops_at_full(self, _):
        self.add(date(2026, 8, 1), "1000.00", self.salary)
        self.add(date(2026, 8, 20), "50.00", self.groceries)  # a refund and nothing spent

        for number_format, shown in self.EXPECTED.items():
            with self.subTest(number_format=number_format):
                self.assertEqual(self.saved_tile(number_format), ("", shown[1], "100"))

    def test_a_month_with_only_a_refund_shows_the_spent_tile_as_a_negative_amount(self, _):
        self.add(date(2026, 9, 3), "50.00", self.groceries)

        page = self.client.get(reverse("finance:dashboard")).content.decode()

        tile = re.search(r'Spent this month</div>\s*<div class="stat-value amount-negative">([^<]*)</div>', page)
        self.assertTrue(tile.group(1).startswith("-50.00"), tile.group(1))

    def test_a_refund_in_another_currency_nets_in_the_tile_and_the_donut(self, _):
        usd = Account.objects.create(name="US card", currency="USD")
        ExchangeRate.objects.create(base_currency="USD", quote_currency="EUR", rate=Decimal("0.5"), date=TODAY_12_SEPT)
        self.add(date(2026, 9, 5), "-100.00", self.groceries, account=usd)  # 50.00 in euro
        self.add(date(2026, 9, 6), "30.00", self.groceries, account=usd)  # 15.00 in euro
        self.add(date(2026, 9, 7), "-20.00", self.groceries)

        response = self.client.get(reverse("finance:dashboard"))

        self.assertEqual(response.context["month_spending"], Decimal("55.00"))
        self.assertEqual([group["total"] for group in response.context["spending_tree"]], [55.0])


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
    """1.8 (F30): the dashboard converts into the main currency, like
    Reports."""

    def test_spending_in_another_currency_is_converted(self):
        today = timezone.localdate()
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        usd = Account.objects.create(name="US card", currency="USD")
        ExchangeRate.objects.create(base_currency="USD", quote_currency="EUR", rate=Decimal("0.5"), date=today)
        Transaction.objects.create(account=self.account, date=today, category=groceries, amount=Decimal("-10.00"))
        Transaction.objects.create(account=usd, date=today, category=groceries, amount=Decimal("-100.00"))

        response = self.client.get(reverse("finance:dashboard"))

        self.assertEqual(response.context["main_currency"], "EUR")
        self.assertEqual(response.context["month_spending"], Decimal("60.00"))
        self.assertEqual(response.context["spending_tree"][0]["total"], 60.0)


class DashboardMissingRateTests(LoggedInWithAccountTestCase):
    """S31: an account whose currency has no known rate is left out of
    the net worth total, and the tile says so, with the same hint the
    Accounts page shows."""

    def test_an_account_with_no_known_rate_is_flagged_under_the_net_worth_tile(self):
        Account.objects.create(name="US card", currency="USD")
        response = self.client.get(reverse("finance:dashboard"))
        self.assertContains(response, "No known rate for USD")
        self.assertContains(response, "Check rates")
        self.assertContains(response, reverse("finance:settings"))

    def test_the_accounts_page_shows_the_same_hint(self):
        Account.objects.create(name="US card", currency="USD")
        hint = "No known rate for USD"
        self.assertContains(self.client.get(reverse("finance:account_list")), hint)

    def test_no_hint_once_the_rate_is_known(self):
        Account.objects.create(name="US card", currency="USD")
        ExchangeRate.objects.create(
            base_currency="USD", quote_currency="EUR", rate=Decimal("0.5"), date=timezone.localdate()
        )
        for name in ("finance:dashboard", "finance:account_list"):
            with self.subTest(page=name):
                self.assertNotContains(self.client.get(reverse(name)), "No known rate")

    def test_every_currency_left_out_is_named(self):
        for currency in ("USD", "GBP"):
            Account.objects.create(name=f"{currency} account", currency=currency)
        # Alphabetical, whatever order the accounts come in.
        self.assertContains(self.client.get(reverse("finance:dashboard")), "No known rate for GBP, USD")

    def test_two_accounts_in_one_currency_name_it_once(self):
        # The view hands the include the accounts, not the currencies,
        # so it must not repeat one.
        for name in ("US card", "US savings"):
            Account.objects.create(name=name, currency="USD")
        for page in ("finance:dashboard", "finance:account_list"):
            with self.subTest(page=page):
                body = self.client.get(reverse(page)).content.decode()
                self.assertIn("No known rate for USD!", body)
                self.assertNotIn("USD, USD", body)

    def test_the_hint_is_a_second_line_inside_the_net_worth_tile_and_leaks_no_syntax(self):
        Account.objects.create(name="US card", currency="USD")
        body = self.client.get(reverse("finance:dashboard")).content.decode()
        accounts_page = self.client.get(reverse("finance:account_list")).content.decode()
        for syntax in LEAKED_TEMPLATE_SYNTAX:
            self.assertNotIn(syntax, body)
            self.assertNotIn(syntax, accounts_page)
        # From the Net worth label up to the next tile's label: that
        # tile's text, and only it.
        net_worth_tile = body.split('<div class="stat-label">Net worth</div>')[1].split("stat-label")[0]
        self.assertLess(net_worth_tile.index("active account"), net_worth_tile.index("No known rate"))
        self.assertEqual(body.count("No known rate"), 1)


class SpendingTreeRefundTests(StashTestCase):
    """1.9 (F31): every category is its own net total; a bigger refund
    shows as negative."""

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
        # self.account ("Checking") defaults to Cash - physical, same
        # group as "Alpha cash".
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
        # Checking (physical/digital, default type) and Savings
        # (illiquid) are two groups.
        self.assertEqual(html.count("account-group-divider"), 1)


class DashboardDonutCurrencyTests(LoggedInWithAccountTestCase):
    def test_spending_panel_carries_the_currency_symbol_for_the_donut_center(self):
        # The donut needs the actual symbol ("€"), not the bare ISO
        # code, to match how every other amount on the page already
        # displays via |moneyc.
        Transaction.objects.create(account=self.account, date=timezone.localdate(), amount=Decimal("-10.00"))
        response = self.client.get(reverse("finance:dashboard"))
        self.assertContains(response, 'data-main-currency="€"')
        self.assertContains(response, 'data-currency-suffix="1"')


@mock.patch("django.utils.timezone.localdate", return_value=TODAY_12_SEPT)
class DueSoonQueryTests(LoggedInWithAccountTestCase):
    """8.15 (F56): the "due soon" list loads each reminder's payee and
    category with the list."""

    def queries_with(self, count):
        create_reminders(self.account, count)
        response, query_count = self.get_and_count_queries(reverse("finance:dashboard"))
        self.assertEqual(len(response.context["due_soon"]), Reminder.objects.count())
        return query_count

    def test_more_reminders_need_no_more_queries(self, _today):
        self.assertEqual(self.queries_with(1), self.queries_with(4))


@mock.patch("django.utils.timezone.localdate", return_value=TODAY_12_SEPT)
class DueSoonCardTests(LoggedInWithAccountTestCase):
    """The Due-soon card's words: the subtitle names the real look-ahead
    (Reminder.DUE_SOON_DAYS) and the empty message names no number, so
    the two can't disagree."""

    def due_soon_card(self):
        """The card's HTML. The whole page must leak no template syntax
        (the empty state is a branch RenderCheckTests never reaches: its
        reminders are all due today)."""
        page = self.client.get(reverse("finance:dashboard")).content.decode()
        self.assertEqual(leaked_syntax_in(page), [])
        return re.search(r'<section [^>]*aria-labelledby="due-title">.*?</section>', page, re.DOTALL).group(0)

    def test_an_empty_card_says_nothing_is_due_soon(self, _):
        card = self.due_soon_card()

        self.assertIn("<h3>Nothing due soon</h3>", card)
        self.assertIn("<p>Bills and income coming up in the next few days will show up here.</p>", card)
        self.assertNotIn("this week", card)

    def test_the_empty_message_names_no_number_and_the_subtitle_follows_the_look_ahead(self, _):
        for days in (7, 3):
            with self.subTest(days=days), mock.patch.object(Reminder, "DUE_SOON_DAYS", days):
                card = self.due_soon_card()

                self.assertIn(f"Next {days} days", card)
                words = re.findall(r"<(?:h3|p)>([^<]*)</(?:h3|p)>", card)
                self.assertEqual(len(words), 2)
                self.assertNotRegex(" ".join(words), r"\d")

    def test_a_card_with_items_lists_them_and_has_no_empty_message(self, _):
        payee = Payee.objects.create(name="Water bill")
        Reminder.objects.create(
            account=self.account, payee=payee, amount=Decimal("10.00"), next_due_date=date(2026, 9, 15)
        )

        card = self.due_soon_card()

        self.assertIn("Water bill", card)
        self.assertIn("in 3 days", card)
        self.assertNotIn("Nothing due soon", card)
