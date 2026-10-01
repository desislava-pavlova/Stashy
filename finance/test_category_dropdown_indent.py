from django.urls import reverse

from .models import Category
from .test_phase7_2 import LoggedInTestCase


class IndentedCategoryDropdownTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        food = Category.objects.create(name="Food", category_type=Category.CategoryType.EXPENSE)
        Category.objects.create(name="Groceries", parent=food, category_type=Category.CategoryType.EXPENSE)

    def _assert_indented(self, html):
        self.assertIn(">Food</option>", html)
        self.assertIn(">    Groceries</option>", html.replace("&nbsp;", " "))
        self.assertNotIn("Food &gt; Groceries", html)

    def test_transaction_form(self):
        html = self.client.get(reverse("finance:transaction_add")).content.decode()
        self._assert_indented(html)
        self.assertLess(html.index(">Food</option>"), html.index("Groceries</option>"))

    def test_reminder_form(self):
        self._assert_indented(self.client.get(reverse("finance:reminder_add")).content.decode())
