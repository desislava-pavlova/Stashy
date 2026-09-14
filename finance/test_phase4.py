"""Tests for Phase 4: categories (parent/type filtering, search, usage, mass re-categorize)."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .forms import CategoryForm
from .models import Account, Category, Transaction, grouped_categories

Type = Category.CategoryType


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)


class IsActiveRemovedTests(TestCase):
    def test_category_has_no_is_active_field(self):
        self.assertFalse(hasattr(Category(name="x"), "is_active"))

    def test_category_form_has_no_is_active_field(self):
        self.assertNotIn("is_active", CategoryForm().fields)


class DisplayNameTests(TestCase):
    def test_full_name_shows_parent_then_child_with_an_arrow(self):
        food = Category.objects.create(name="Food", category_type=Type.EXPENSE)
        groceries = Category.objects.create(name="Groceries", parent=food, category_type=Type.EXPENSE)
        self.assertEqual(groceries.full_name, "Food > Groceries")
        self.assertEqual(food.full_name, "Food")

    def test_transfer_category_is_named_in_brackets(self):
        account = Account.objects.create(name="Cash")
        self.assertEqual(account.transfer_category.name, "[Cash]")
        account.name = "Wallet"
        account.save()
        account.transfer_category.refresh_from_db()
        self.assertEqual(account.transfer_category.name, "[Wallet]")


class OneLevelOfSubcategoriesTests(TestCase):
    def test_a_subcategory_is_not_offered_as_a_parent(self):
        food = Category.objects.create(name="Food", category_type=Type.EXPENSE)
        groceries = Category.objects.create(name="Groceries", parent=food, category_type=Type.EXPENSE)
        self.assertNotIn(groceries, CategoryForm().fields["parent"].queryset)
        self.assertIn(food, CategoryForm().fields["parent"].queryset)

    def test_a_category_cannot_become_a_subcategory_of_itself(self):
        food = Category.objects.create(name="Food", category_type=Type.EXPENSE)
        form = CategoryForm({"name": "Food", "parent": food.pk, "category_type": "expense"}, instance=food)
        self.assertFalse(form.is_valid())
        self.assertIn("parent", form.errors)

    def test_a_category_with_children_cannot_be_demoted_into_a_subcategory(self):
        food = Category.objects.create(name="Food", category_type=Type.EXPENSE)
        Category.objects.create(name="Groceries", parent=food, category_type=Type.EXPENSE)
        rent = Category.objects.create(name="Rent", category_type=Type.EXPENSE)
        form = CategoryForm({"name": "Food", "parent": rent.pk, "category_type": "expense"}, instance=food)
        self.assertFalse(form.is_valid())
        self.assertIn("parent", form.errors)

    def test_a_childless_category_can_become_a_subcategory(self):
        rent = Category.objects.create(name="Rent", category_type=Type.EXPENSE)
        bills = Category.objects.create(name="Bills", category_type=Type.EXPENSE)
        form = CategoryForm({"name": "Rent", "parent": bills.pk, "category_type": "expense"}, instance=rent)
        self.assertTrue(form.is_valid(), form.errors)


class GroupedCategoriesTests(TestCase):
    def test_income_then_expense_then_transfer_each_sorted_by_parent_then_name(self):
        salary = Category.objects.create(name="Salary", category_type=Type.INCOME)
        bonus = Category.objects.create(name="Bonus", category_type=Type.INCOME)
        food = Category.objects.create(name="Food", category_type=Type.EXPENSE)
        groceries = Category.objects.create(name="Groceries", parent=food, category_type=Type.EXPENSE)
        rent = Category.objects.create(name="Rent", category_type=Type.EXPENSE)
        account = Account.objects.create(name="Checking")  # brings its own Transfer category

        groups = grouped_categories(Category.objects.select_related("parent").all())

        self.assertEqual([g["key"] for g in groups], ["income", "expense", "transfer"])
        self.assertEqual([c.name for c in groups[0]["categories"]], ["Bonus", "Salary"])
        self.assertEqual([c.name for c in groups[1]["categories"]], ["Food", "Groceries", "Rent"])
        self.assertEqual([c.name for c in groups[2]["categories"]], [account.transfer_category.name])

    def test_a_category_type_that_differs_from_data_still_groups_by_its_own_type(self):
        # Grouping reads each row's own category_type - it doesn't try to inherit a parent's.
        parent = Category.objects.create(name="Parent", category_type=Type.INCOME)
        child = Category.objects.create(name="Child", parent=parent, category_type=Type.EXPENSE)
        groups = {g["key"]: [c.name for c in g["categories"]] for g in grouped_categories([parent, child])}
        self.assertEqual(groups, {"income": ["Parent"], "expense": ["Child"]})


class CategoryParentTypeFilterTests(TestCase):
    def test_parent_options_are_tagged_with_their_own_category_type(self):
        food = Category.objects.create(name="Food", category_type=Type.EXPENSE)
        salary = Category.objects.create(name="Salary", category_type=Type.INCOME)
        form = CategoryForm()
        widget = form.fields["parent"].widget
        self.assertEqual(widget.categories_by_id[food.pk].category_type, Type.EXPENSE)
        self.assertEqual(widget.categories_by_id[salary.pk].category_type, Type.INCOME)

    def test_a_transfer_category_is_not_offered_as_a_parent(self):
        account = Account.objects.create(name="Checking")
        form = CategoryForm()
        self.assertNotIn(account.transfer_category.pk, form.fields["parent"].widget.categories_by_id)
        self.assertNotIn(account.transfer_category, form.fields["parent"].queryset)


class CategorySearchTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.food = Category.objects.create(name="Food", category_type=Type.EXPENSE)
        Category.objects.create(name="Groceries", parent=self.food, category_type=Type.EXPENSE)
        Category.objects.create(name="Rent", category_type=Type.EXPENSE)

    def test_search_matches_by_own_name_or_parent_name(self):
        response = self.client.get(reverse("finance:category_list"), {"q": "food"})
        names = {c.name for c in response.context["categories"]}
        self.assertEqual(names, {"Food", "Groceries"})

    def test_search_with_no_matches_shows_a_message(self):
        response = self.client.get(reverse("finance:category_list"), {"q": "nonexistent"})
        self.assertContains(response, "No categories match")


class CategoryUsageAndRecategorizeTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.account = Account.objects.create(name="Checking")
        self.groceries = Category.objects.create(name="Groceries", category_type=Type.EXPENSE)
        self.household = Category.objects.create(name="Household", category_type=Type.EXPENSE)
        self.txns = [
            Transaction.objects.create(account=self.account, date=date(2026, 1, d), category=self.groceries, amount=Decimal("-10.00"))
            for d in (1, 2, 3)
        ]

    def test_usage_count_shown_on_the_category_list(self):
        response = self.client.get(reverse("finance:category_list"))
        categories = {c.pk: c.usage_count for group in response.context["groups"] for c in group["categories"]}
        self.assertEqual(categories[self.groceries.pk], 3)
        self.assertEqual(categories[self.household.pk], 0)

    def test_usage_view_lists_only_that_categorys_own_transactions(self):
        sub = Category.objects.create(name="Organic", parent=self.groceries, category_type=Type.EXPENSE)
        Transaction.objects.create(account=self.account, date=date(2026, 1, 4), category=sub, amount=Decimal("-5.00"))
        response = self.client.get(reverse("finance:category_usage", args=[self.groceries.pk]))
        self.assertEqual(len(response.context["transactions"]), 3)

    def test_moving_selected_transactions_updates_only_those_selected(self):
        response = self.client.post(reverse("finance:category_usage", args=[self.groceries.pk]), {
            "transaction_id": [self.txns[0].pk, self.txns[1].pk],
            "move_to": self.household.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.txns[0].refresh_from_db()
        self.txns[1].refresh_from_db()
        self.txns[2].refresh_from_db()
        self.assertEqual(self.txns[0].category, self.household)
        self.assertEqual(self.txns[1].category, self.household)
        self.assertEqual(self.txns[2].category, self.groceries)

    def test_cannot_move_transactions_into_a_transfer_category(self):
        self.client.post(reverse("finance:category_usage", args=[self.groceries.pk]), {
            "transaction_id": [self.txns[0].pk],
            "move_to": self.account.transfer_category.pk,
        })
        self.txns[0].refresh_from_db()
        self.assertEqual(self.txns[0].category, self.groceries)

    def test_selecting_nothing_shows_an_error_and_moves_nothing(self):
        self.client.post(reverse("finance:category_usage", args=[self.groceries.pk]), {"move_to": self.household.pk})
        self.txns[0].refresh_from_db()
        self.assertEqual(self.txns[0].category, self.groceries)


class SystemManagedCategoryStillGuardedTests(LoggedInTestCase):
    def test_transfer_category_not_offered_in_its_own_usage_views_move_to_list(self):
        account = Account.objects.create(name="Checking")
        other = Account.objects.create(name="Savings")
        response = self.client.get(reverse("finance:category_usage", args=[account.transfer_category.pk]))
        self.assertNotIn(other.transfer_category, response.context["other_categories"])
