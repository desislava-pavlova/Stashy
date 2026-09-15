"""Tests for Phase 12's starter categories: a plain text file (basic_categories.txt) read at
runtime, never hardcoded, so the user can edit the list without touching code."""
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import starter_categories
from .models import Category


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)


class StarterCategoriesFileTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "basic_categories.txt"
        self.patcher = mock.patch("finance.starter_categories.starter_categories_path", return_value=self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_returns_empty_list_when_the_file_does_not_exist(self):
        self.assertEqual(starter_categories.starter_category_names(), [])

    def test_reads_non_blank_lines_in_order(self):
        self.path.write_text("Groceries\n\nRent\n  \nSalary\n", encoding="utf-8")
        self.assertEqual(starter_categories.starter_category_names(), ["Groceries", "Rent", "Salary"])


class AddStarterCategoriesTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "basic_categories.txt"
        self.patcher = mock.patch("finance.starter_categories.starter_categories_path", return_value=self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_creates_every_name_in_the_file(self):
        self.path.write_text("Groceries\nWork Income\n", encoding="utf-8")
        created = starter_categories.add_starter_categories()
        self.assertEqual(created, ["Groceries", "Work Income"])
        self.assertEqual(Category.objects.filter(parent__isnull=True).count(), 2)

    def test_a_name_containing_income_becomes_an_income_category(self):
        self.path.write_text("Work Income\nGroceries\n", encoding="utf-8")
        starter_categories.add_starter_categories()
        self.assertEqual(Category.objects.get(name="Work Income").category_type, Category.CategoryType.INCOME)
        self.assertEqual(Category.objects.get(name="Groceries").category_type, Category.CategoryType.EXPENSE)

    def test_already_existing_categories_are_skipped_case_insensitively(self):
        Category.objects.create(name="groceries", category_type=Category.CategoryType.EXPENSE)
        self.path.write_text("Groceries\nRent\n", encoding="utf-8")
        created = starter_categories.add_starter_categories()
        self.assertEqual(created, ["Rent"])
        self.assertEqual(Category.objects.filter(name__iexact="groceries").count(), 1)

    def test_running_it_twice_creates_nothing_the_second_time(self):
        self.path.write_text("Groceries\n", encoding="utf-8")
        starter_categories.add_starter_categories()
        second_run = starter_categories.add_starter_categories()
        self.assertEqual(second_run, [])


class AddStarterCategoriesViewTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "basic_categories.txt"
        # Two names resolve to the same function object at two different import sites -
        # views.py imported its own reference, so both need patching to the same temp path.
        for target in ("finance.views.starter_categories_path", "finance.starter_categories.starter_categories_path"):
            patcher = mock.patch(target, return_value=self.path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_missing_file_shows_an_error(self):
        response = self.client.post(reverse("finance:add_starter_categories"), follow=True)
        self.assertContains(response, "wasn&#x27;t found")

    def test_posting_creates_the_categories_and_shows_a_success_message(self):
        self.path.write_text("Groceries\nRent\n", encoding="utf-8")
        response = self.client.post(reverse("finance:add_starter_categories"), follow=True)
        self.assertContains(response, "Added 2 categories")
        self.assertTrue(Category.objects.filter(name="Groceries").exists())

    def test_posting_again_reports_nothing_to_add(self):
        self.path.write_text("Groceries\n", encoding="utf-8")
        self.client.post(reverse("finance:add_starter_categories"))
        response = self.client.post(reverse("finance:add_starter_categories"), follow=True)
        self.assertContains(response, "already exist")


class CategoryListButtonTests(LoggedInTestCase):
    def test_add_starter_categories_button_is_on_the_page(self):
        response = self.client.get(reverse("finance:category_list"))
        self.assertContains(response, "Add starter categories")
