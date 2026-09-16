import re
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .forms import CategoryForm
from .models import Account, Category, Payee, Reminder, Transaction
from .reporting import savings_rate, spending_tree

TODAY = date(2026, 9, 12)


class SavingsRateTests(SimpleTestCase):
    def test_share_of_income_not_spent(self):
        self.assertEqual(savings_rate(Decimal("2000.00"), Decimal("500.00")), Decimal("75.0"))

    def test_spending_more_than_income_is_negative(self):
        self.assertEqual(savings_rate(Decimal("1000"), Decimal("1250")), Decimal("-25.0"))

    def test_no_income_means_no_rate(self):
        self.assertIsNone(savings_rate(Decimal("0"), Decimal("80")))


class SpendingTreeTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Checking")
        self.food = Category.objects.create(name="Food")
        self.groceries = Category.objects.create(name="Groceries", parent=self.food)
        self.organic = Category.objects.create(name="Organic", parent=self.groceries)
        self.rent = Category.objects.create(name="Rent")

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
        self.assertEqual(food["children"], [
            {"id": self.groceries.pk, "name": "Groceries", "with_subcategories": True, "total": 42.5},
            {"id": self.food.pk, "name": "Food (general)", "with_subcategories": False, "total": 7.5},
        ])

    def test_a_category_that_is_its_own_parent_does_not_hang(self):
        Category.objects.filter(pk=self.rent.pk).update(parent=self.rent)
        self.add("-10.00", self.rent)
        tree = spending_tree(date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(tree[0]["total"], 10.0)


class CategoryLoopTests(TestCase):
    def setUp(self):
        self.parent = Category.objects.create(name="Parent")
        self.child = Category.objects.create(name="Child", parent=self.parent)

    def test_descendants_stops_at_a_loop(self):
        Category.objects.filter(pk=self.parent.pk).update(parent=self.child)
        self.parent.refresh_from_db()
        self.assertEqual([c.pk for c in self.parent.descendants()], [self.child.pk])

    def test_form_rejects_itself_or_its_own_subcategory_as_parent(self):
        for invalid_parent in (self.parent, self.child):
            form = CategoryForm(
                {"name": "Parent", "parent": invalid_parent.pk, "category_type": "expense"},
                instance=self.parent,
            )
            self.assertFalse(form.is_valid())
            self.assertIn("parent", form.errors)


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
class DashboardTests(LoggedInTestCase):
    def test_shows_last_months_savings_rate(self, _):
        # A category's type decides income vs. spending (R1, Phase 13) - amount alone no longer
        # does, so these need real Income/Expense categories to count toward the rate.
        salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        for on, amount, category in [
            (date(2026, 8, 1), "2000.00", salary), (date(2026, 8, 20), "-500.00", rent), (date(2026, 9, 3), "-40.00", rent),
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
            return Reminder.objects.create(account=self.account, payee=payee, amount=Decimal("10.00"),
                                            next_due_date=due)
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


class TransactionListFilterTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.food = Category.objects.create(name="Food")
        self.groceries = Category.objects.create(name="Groceries", parent=self.food)

        def add(on, category):
            return Transaction.objects.create(account=self.account, date=on, category=category, amount=Decimal("-1.00"))

        self.september_groceries = add(date(2026, 9, 5), self.groceries)
        self.september_food = add(date(2026, 9, 6), self.food)
        self.september_uncategorized = add(date(2026, 9, 7), None)
        self.august_groceries = add(date(2026, 8, 30), self.groceries)

    def shown(self, **params):
        response = self.client.get(reverse("finance:transaction_list"), params)
        self.assertEqual(response.status_code, 200)
        return {t.pk for t in response.context["transactions"]}

    def test_date_range(self):
        expected = {self.september_groceries.pk, self.september_food.pk, self.september_uncategorized.pk}
        self.assertEqual(self.shown(start="2026-09-01", end="2026-09-30"), expected)

    def test_uncategorized_only(self):
        self.assertEqual(self.shown(category="none"), {self.september_uncategorized.pk})

    def test_category_including_subcategories(self):
        expected = {self.september_groceries.pk, self.september_food.pk, self.august_groceries.pk}
        self.assertEqual(self.shown(category=self.food.pk, with_subcategories="1"), expected)

    def test_exact_category(self):
        self.assertEqual(self.shown(category=self.food.pk), {self.september_food.pk})

    def test_malformed_filters_are_ignored_instead_of_crashing(self):
        self.assertEqual(len(self.shown(start="not-a-date", category="abc", account="x")), 4)


class OfflineAndThemeTests(LoggedInTestCase):
    EXTERNAL_ASSET = re.compile(r'(?:src|href)="https?://')

    def test_pages_load_nothing_from_the_internet(self):
        urls = [
            reverse(name) for name in (
                "finance:dashboard", "finance:transaction_list", "finance:account_add",
                "finance:reports", "finance:settings", "finance:category_list", "finance:category_add",
                "finance:payee_list",
            )
        ]
        urls.append(reverse("finance:account_detail", args=[self.account.pk]))
        for url in urls:
            content = self.client.get(url).content.decode()
            self.assertIsNone(self.EXTERNAL_ASSET.search(content), url)
            self.assertIn("data-theme-toggle", content)
        self.client.logout()
        login = self.client.get(reverse("login")).content.decode()
        self.assertIsNone(self.EXTERNAL_ASSET.search(login))
        self.assertIn("mascot-full-720.webp", login)

    def test_every_referenced_static_file_exists(self):
        for path in (
            "finance/css/stashy.css",
            "finance/js/stashy.js",
            "finance/js/charts.js",
            "finance/js/dashboard.js",
            "finance/vendor/bootstrap/css/bootstrap.min.css",
            "finance/vendor/bootstrap/js/bootstrap.bundle.min.js",
            "finance/vendor/bootstrap-icons/bootstrap-icons.min.css",
            "finance/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2",
            "finance/vendor/chartjs/chart.umd.min.js",
            "finance/vendor/flatpickr/flatpickr.min.js",
            "finance/vendor/fonts/nunito/nunito-cyrillic-wght-normal.woff2",
            "finance/vendor/fonts/manrope/manrope-latin-wght-normal.woff2",
            "finance/img/stashy-icon-32.png",
            "finance/img/stashy-icon-180.png",
            "finance/img/stashy-icon-192.png",
            "finance/img/mascot-face-96.png",
            "finance/img/mascot-face-192.png",
            "finance/img/mascot-full-720.webp",
        ):
            with self.subTest(path=path):
                self.assertIsNotNone(finders.find(path))

    def test_form_pages_show_tips_beside_the_form(self):
        response = self.client.get(reverse("finance:account_add"))
        self.assertContains(response, "help-card")
        self.assertContains(response, "Untick Is active instead of deleting it")
