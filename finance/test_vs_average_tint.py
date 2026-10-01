from decimal import Decimal

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .templatetags.stashy_format import diff_tint


class DiffTintFilterTests(SimpleTestCase):
    def test_spending_more_is_bad_less_is_good(self):
        self.assertEqual(diff_tint(Decimal("5"), True), "diff-bad")
        self.assertEqual(diff_tint(Decimal("-5"), True), "diff-good")

    def test_income_more_is_good_less_is_bad(self):
        self.assertEqual(diff_tint(Decimal("5")), "diff-good")
        self.assertEqual(diff_tint(Decimal("-5")), "diff-bad")

    def test_zero_and_missing_get_no_tint(self):
        self.assertEqual(diff_tint(Decimal("0"), True), "")
        self.assertEqual(diff_tint(None), "")


class TableRenderTests(SimpleTestCase):
    def _html(self, rows, totals, **extra):
        table = {"groups": [{"key": "x", "name": "G", "rows": rows}], "totals": totals}
        return render_to_string(
            "finance/includes/report_vs_average_table.html",
            {
                "table": table,
                "main_currency": "EUR",
                "prefix": "p",
                "signed": False,
                "lower_is_good": False,
                **extra,
            },
        )

    def _row(self, name, diff, **extra):
        return {
            "id": 1,
            "parent_id": None,
            "name": name,
            "is_child": False,
            "has_children": False,
            "current": Decimal("10"),
            "average": Decimal("5"),
            "difference": diff,
            **extra,
        }

    def test_spending_table_pink_when_up_green_when_down(self):
        html = self._html(
            [self._row("Up", Decimal("5")), self._row("Down", Decimal("-5"))],
            {"current": Decimal("1"), "average": Decimal("1"), "difference": Decimal("0")},
            lower_is_good=True,
        )
        self.assertEqual(html.count("diff-bad"), 1)
        self.assertEqual(html.count("diff-good"), 1)
        self.assertLess(html.index("diff-bad"), html.index("diff-good"))

    def test_income_table_green_when_up(self):
        html = self._html([self._row("Up", Decimal("5"))], None)
        self.assertIn("diff-good", html)
        self.assertNotIn("diff-bad", html)

    def test_a_row_can_flip_the_table_default(self):
        html = self._html([self._row("Expense total", Decimal("5"), lower_is_good=True)], None, signed=True)
        self.assertIn("diff-bad", html)
