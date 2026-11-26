"""Small pieces the view modules share (`views/_helpers.py`)."""

from django.test import RequestFactory, SimpleTestCase

from ..models import Transaction
from ..views._helpers import apply_sort, next_sort_keys, plural


class PluralTests(SimpleTestCase):
    def test_a_count_and_its_word(self):
        self.assertEqual(
            [plural(count, "row") for count in (0, 1, 2)], ["0 rows", "1 row", "2 rows"]
        )

    def test_a_word_that_doesnt_just_add_an_s(self):
        self.assertEqual(plural(1, "category", "categories"), "1 category")
        self.assertEqual(plural(3, "category", "categories"), "3 categories")

    def test_none_is_a_plural_too_with_or_without_a_given_plural(self):
        self.assertEqual(plural(0, "row"), "0 rows")
        self.assertEqual(plural(0, "category", "categories"), "0 categories")

    def test_a_big_count_is_written_plainly(self):
        # No thousands separator: a message is not an amount.
        self.assertEqual(plural(1000, "row"), "1000 rows")


class NextSortKeysTests(SimpleTestCase):
    COLUMNS = {"date": ["date"], "payee": ["payee__name"]}

    def test_with_no_sort_every_column_starts_ascending(self):
        self.assertEqual(next_sort_keys(self.COLUMNS, ""), {"date": "date", "payee": "payee"})

    def test_the_sorted_column_flips_to_descending(self):
        self.assertEqual(next_sort_keys(self.COLUMNS, "date"), {"date": "-date", "payee": "payee"})

    def test_a_descending_column_goes_back_to_ascending(self):
        self.assertEqual(next_sort_keys(self.COLUMNS, "-date"), {"date": "date", "payee": "payee"})

    def test_a_sort_that_is_not_a_column_leaves_every_column_ascending(self):
        self.assertEqual(
            next_sort_keys(self.COLUMNS, "nonsense"), {"date": "date", "payee": "payee"}
        )

    def test_no_columns_means_no_keys(self):
        self.assertEqual(next_sort_keys({}, "date"), {})


class ApplySortTests(SimpleTestCase):
    """`apply_sort()` reads ?sort= and hands back the sort it used and
    the next one each header asks for. It only builds the queryset's
    ORDER BY, so no database is needed."""

    COLUMNS = {"date": ["date", "transaction_number"], "memo": ["memo"]}

    def sorted_by(self, sort=None):
        request = RequestFactory().get("/", {} if sort is None else {"sort": sort})
        queryset, current, next_keys = apply_sort(request, Transaction.objects.all(), self.COLUMNS)
        return queryset.query.order_by, current, next_keys

    def test_a_column_sorts_ascending_and_its_header_asks_for_descending(self):
        order, current, next_keys = self.sorted_by("date")
        self.assertEqual(order, ("date", "transaction_number"))
        self.assertEqual(current, "date")
        self.assertEqual(next_keys, {"date": "-date", "memo": "memo"})

    def test_a_dash_sorts_every_field_of_the_column_descending(self):
        order, current, next_keys = self.sorted_by("-date")
        self.assertEqual(order, ("-date", "-transaction_number"))
        self.assertEqual(current, "-date")
        self.assertEqual(next_keys, {"date": "date", "memo": "memo"})

    def test_no_sort_keeps_the_models_own_order_and_every_header_starts_ascending(self):
        order, current, next_keys = self.sorted_by()
        self.assertEqual((order, current), ((), ""))
        self.assertEqual(next_keys, {"date": "date", "memo": "memo"})

    def test_a_sort_that_is_not_a_column_is_dropped_not_half_used(self):
        # "--date" loses only one dash, and "-" loses its only one.
        for sort in ("nonsense", "-nonsense", "--date", "-", "Date"):
            with self.subTest(sort=sort):
                order, current, next_keys = self.sorted_by(sort)
                self.assertEqual((order, current), ((), ""))
                self.assertEqual(next_keys, {"date": "date", "memo": "memo"})
