"""The filters a page reads from its address (`finance/filters.py`): the
Transactions list's date range and categories, and Reports' range. The
pages and exports are tested in their own modules; these call the
readers directly, so every branch of the date range is named.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from django.test import RequestFactory
from django.utils import timezone

from .. import regional
from ..filters import filtered_transactions, report_filters
from ..models import Account, Category, Transaction
from ..reporting import DEFAULT_LOOKBACK_PERIODS, EARLIEST_RANGE_KEY
from .base import StashTestCase


def read_transactions_address(query):
    """(the transactions, the filters) for a Transactions address with
    these parameters."""
    request = RequestFactory().get("/transactions/", query)
    return filtered_transactions(request)


class TransactionDateRangeTests(StashTestCase):
    def date_range(self, query):
        _transactions, filters = read_transactions_address(query)
        return filters["range_key"], filters["start"], filters["end"]

    def test_no_range_at_all_is_this_year(self):
        year = timezone.localdate().year
        self.assertEqual(self.date_range({}), ("this_year", date(year, 1, 1), date(year, 12, 31)))

    def test_a_known_range_is_worked_out_from_today(self):
        year = timezone.localdate().year
        self.assertEqual(
            self.date_range({"range": "last_year"}),
            ("last_year", date(year - 1, 1, 1), date(year - 1, 12, 31)),
        )

    def test_an_unknown_range_is_this_year_not_reports_fallback(self):
        year = timezone.localdate().year
        self.assertEqual(
            self.date_range({"range": "decade"}),
            ("this_year", date(year, 1, 1), date(year, 12, 31)),
        )

    def test_all_dates_has_no_limits_even_though_the_date_boxes_are_sent(self):
        query = {"range": "", "start": "2026-03-01", "end": "2026-03-31"}
        self.assertEqual(self.date_range(query), ("", None, None))

    def test_the_old_earliest_to_date_bookmark_is_all_dates(self):
        self.assertEqual(self.date_range({"range": EARLIEST_RANGE_KEY}), ("", None, None))

    def test_custom_keeps_the_dates_as_typed(self):
        query = {"range": "custom", "start": "2026-03-01", "end": "2026-03-31"}
        self.assertEqual(self.date_range(query), ("custom", date(2026, 3, 1), date(2026, 3, 31)))

    def test_custom_keeps_whichever_date_could_be_read(self):
        self.assertEqual(
            self.date_range({"range": "custom", "start": "2026-03-01"}),
            ("custom", date(2026, 3, 1), None),
        )
        self.assertEqual(
            self.date_range({"range": "custom", "start": "nonsense", "end": "2026-03-31"}),
            ("custom", None, date(2026, 3, 31)),
        )

    def test_dates_with_no_range_are_a_custom_range(self):
        """The dashboard's links arrive this way."""
        query = {"start": "2026-03-01", "end": "2026-03-31"}
        self.assertEqual(self.date_range(query), ("custom", date(2026, 3, 1), date(2026, 3, 31)))
        self.assertEqual(
            self.date_range({"end": "2026-03-31"}), ("custom", None, date(2026, 3, 31))
        )

    def test_a_known_range_ignores_dates_typed_beside_it(self):
        """The Date range box wins: its dates are worked out from today,
        whatever the date boxes still hold."""
        year = timezone.localdate().year
        this_year = ("this_year", date(year, 1, 1), date(year, 12, 31))
        query = {"range": "this_year", "start": "2020-03-01", "end": "2020-03-31"}
        self.assertEqual(self.date_range(query), this_year)

    def test_an_unknown_range_ignores_dates_typed_beside_it_too(self):
        year = timezone.localdate().year
        this_year = ("this_year", date(year, 1, 1), date(year, 12, 31))
        query = {"range": "decade", "start": "2020-03-01", "end": "2020-03-31"}
        self.assertEqual(self.date_range(query), this_year)

    def test_custom_with_no_dates_at_all_has_no_limits(self):
        self.assertEqual(self.date_range({"range": "custom"}), ("custom", None, None))

    def test_a_date_is_read_in_the_month_first_format_when_that_is_active(self):
        query = {"range": "custom", "start": "03/01/2026", "end": "03/31/2026"}
        with regional.activate(replace(regional.DEFAULTS, date_format="mdy")):
            result = self.date_range(query)
        self.assertEqual(result, ("custom", date(2026, 3, 1), date(2026, 3, 31)))

    def test_a_date_is_read_in_the_active_format(self):
        query = {"range": "custom", "start": "01/03/2026", "end": "31/03/2026"}
        self.assertEqual(self.date_range(query), ("custom", date(2026, 3, 1), date(2026, 3, 31)))


class TransactionFilterTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        account = Account.objects.create(name="Checking")
        cls.food = Category.objects.create(
            name="Groceries", category_type=Category.CategoryType.EXPENSE
        )
        cls.snacks = cls.food.create_subcategory("Snacks")
        cls.rent = Category.objects.create(
            name="Rent", category_type=Category.CategoryType.EXPENSE
        )
        for memo, day, category in (
            ("A", date(2026, 1, 10), cls.food),
            ("B", date(2026, 2, 10), cls.snacks),
            ("C", date(2026, 3, 10), None),
            ("D", date(2026, 3, 20), cls.rent),
        ):
            Transaction.objects.create(
                account=account, date=day, category=category, memo=memo, amount=Decimal("-10")
            )

    def memos(self, query):
        transactions, _filters = read_transactions_address({"range": "", **query})
        return sorted(transactions.values_list("memo", flat=True))

    def test_a_range_keeps_only_the_dates_inside_it(self):
        query = {"range": "custom", "start": "2026-03-01", "end": "2026-03-31"}
        self.assertEqual(self.memos(query), ["C", "D"])
        self.assertEqual(self.memos({"range": "custom", "start": "2026-03-15"}), ["D"])
        self.assertEqual(self.memos({"range": "custom", "end": "2026-02-28"}), ["A", "B"])

    def test_a_category_keeps_only_its_own_transactions(self):
        self.assertEqual(self.memos({"category": [str(self.food.pk)]}), ["A"])

    def test_with_subcategories_adds_the_ones_under_it(self):
        query = {"category": [str(self.food.pk)], "with_subcategories": "1"}
        self.assertEqual(self.memos(query), ["A", "B"])

    def test_uncategorized_alone_and_beside_a_category(self):
        self.assertEqual(self.memos({"category": ["none"]}), ["C"])
        self.assertEqual(self.memos({"category": ["none", str(self.rent.pk)]}), ["C", "D"])

    def test_no_category_picked_keeps_everything(self):
        self.assertEqual(self.memos({}), ["A", "B", "C", "D"])

    def test_a_category_id_nobody_has_leaves_the_list_unfiltered(self):
        self.assertEqual(self.memos({"category": ["999999"]}), ["A", "B", "C", "D"])


class ReportFiltersTests(StashTestCase):
    def read(self, query):
        return report_filters(RequestFactory().get("/reports/", query))

    def test_no_address_is_this_month_and_the_usual_lookback(self):
        today = timezone.localdate()
        filters = self.read({})
        self.assertEqual((filters["start"], filters["end"]), (today.replace(day=1), today))
        self.assertEqual(filters["lookback"], DEFAULT_LOOKBACK_PERIODS)
        self.assertIsNone(filters["form_errors"])

    def test_the_average_interval_is_the_lookback(self):
        self.assertEqual(self.read({"interval": "6"})["lookback"], 6)

    def test_a_bad_range_falls_back_to_the_defaults_and_keeps_the_errors(self):
        today = timezone.localdate()
        filters = self.read({"range": "custom", "start": "2026-03-01"})
        self.assertEqual((filters["start"], filters["end"]), (today.replace(day=1), today))
        self.assertTrue(filters["form_errors"])
        self.assertFalse(filters["form"].is_bound)
