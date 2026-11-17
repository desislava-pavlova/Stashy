"""Transactions: numbers, the add and edit forms, the list (filters, sorting, totals, Edit
mode), deleting, and where you land after saving."""

import csv
import html
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.db import connections
from django.test import RequestFactory, SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .. import models
from ..forms import TransactionForm
from ..models import Account, Category, Payee, Profile, Transaction
from ..views._helpers import _as_id, _as_ids, _safe_next
from ..views.transactions import _longer_than_a_year
from .base import LEAKED_TEMPLATE_SYNTAX, TODAY_13_SEPT, LoggedInTestCase, LoggedInWithAccountTestCase, StashTestCase


class TransactionNumberingTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.account = Account.objects.create(name="Checking")

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
        txn = Transaction.objects.create(
            account=self.account, date=date(2026, 1, 1), amount=Decimal("-1.00"), transaction_number=9999
        )
        self.assertEqual(txn.transaction_number, 9999)
        # Numbering isn't strictly unique/enforced (D9) — the next auto-assigned number just continues past it.
        following = Transaction.objects.create(account=self.account, date=date(2026, 1, 2), amount=Decimal("-1.00"))
        self.assertEqual(following.transaction_number, 10000)


@mock.patch("django.utils.timezone.localdate", return_value=TODAY_13_SEPT)
class DefaultViewTests(LoggedInWithAccountTestCase):
    """The transaction list defaults to this year, until you explicitly ask otherwise."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Transaction.objects.create(account=cls.account, date=date(2026, 3, 1), amount=Decimal("-1.00"))
        Transaction.objects.create(account=cls.account, date=date(2025, 12, 31), amount=Decimal("-1.00"))

    def shown(self, mock_today, **params):
        response = self.client.get(reverse("finance:transaction_list"), params)
        return {t.pk for t in response.context["transactions"]}, response

    def test_no_filters_at_all_defaults_to_this_year(self, mock_today):
        shown, response = self.shown(mock_today)
        self.assertEqual(len(shown), 1)
        self.assertEqual(response.context["range"], "this_year")

    def test_explicit_all_dates_shows_everything(self, mock_today):
        shown, response = self.shown(mock_today, range="")
        self.assertEqual(len(shown), 2)
        self.assertEqual(response.context["range"], "")

    def test_a_named_preset_is_respected(self, mock_today):
        shown, _response = self.shown(mock_today, range="last_year")
        self.assertEqual(len(shown), 1)

    def test_explicit_custom_dates_are_marked_as_custom(self, mock_today):
        shown, response = self.shown(mock_today, start="2025-01-01", end="2025-12-31")
        self.assertEqual(response.context["range"], "custom")
        self.assertEqual(len(shown), 1)


@mock.patch("django.utils.timezone.localdate", return_value=TODAY_13_SEPT)
class TransactionRangeTests(LoggedInWithAccountTestCase):
    """8.13: "All dates" takes the place of "Earliest to date". 8.14: dates typed into the
    Start/End boxes are read in the stash's own date format."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.last_year = Transaction.objects.create(account=cls.account, date=date(2025, 12, 31), amount=-1)
        cls.march = Transaction.objects.create(account=cls.account, date=date(2026, 3, 1), amount=-1)
        cls.future = Transaction.objects.create(account=cls.account, date=date(2027, 1, 5), amount=-1)

    def shown(self, **params):
        response = self.client.get(reverse("finance:transaction_list"), params)
        return {t.pk for t in response.context["transactions"]}, response

    def test_the_range_list_offers_all_dates_and_no_earliest_to_date(self, _today):
        _shown, response = self.shown()
        values = [value for value, _label in response.context["range_choices"]]
        self.assertIn(("", "All dates"), response.context["range_choices"])
        self.assertNotIn("earliest", values)
        self.assertIn("custom", values)
        self.assertNotContains(response, "Earliest to date")

    def test_all_dates_shows_everything_even_a_date_in_the_future(self, _today):
        shown, _response = self.shown(range="")
        self.assertEqual(shown, {self.last_year.pk, self.march.pk, self.future.pk})

    def test_all_dates_ignores_the_dates_the_form_still_sends(self, _today):
        # Picking "All dates" submits the form with the previous range's dates still in the boxes.
        shown, response = self.shown(range="", start="01/01/2026", end="31/12/2026")
        self.assertEqual(len(shown), 3)
        self.assertIsNone(response.context["start"])
        self.assertIsNone(response.context["end"])

    def test_an_old_earliest_to_date_bookmark_lands_on_all_dates(self, _today):
        shown, response = self.shown(range="earliest", start="01/01/2026", end="31/12/2026")
        self.assertEqual(len(shown), 3)
        self.assertEqual(response.context["range"], "")

    def test_a_custom_range_reads_dates_in_the_stashs_own_format(self, _today):
        shown, response = self.shown(range="custom", start="01/02/2026", end="31/03/2026")
        self.assertEqual(shown, {self.march.pk})
        self.assertEqual((response.context["start"], response.context["end"]), (date(2026, 2, 1), date(2026, 3, 31)))

    def test_a_custom_range_follows_the_stashs_date_format(self, _today):
        profile = Profile.for_user(self.user)
        profile.date_format = "mdy"
        profile.save()
        shown, _response = self.shown(range="custom", start="02/01/2026", end="03/31/2026")
        self.assertEqual(shown, {self.march.pk})

    def test_a_preset_still_decides_when_the_range_says_so(self, _today):
        # The page's script switches the Range dropdown to "Custom range" as soon as a date is typed.
        shown, _response = self.shown(range="last_year", start="01/02/2026", end="31/03/2026")
        self.assertEqual(shown, {self.last_year.pk})

    def test_the_date_boxes_switch_the_range_to_custom(self, _today):
        _shown, response = self.shown()
        self.assertContains(
            response, 'name="start" class="form-control form-control-sm" data-datepicker data-range-date'
        )
        self.assertContains(
            response, 'name="end" class="form-control form-control-sm" data-datepicker data-range-date'
        )


class ReconciledFilterTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.reconciled = Transaction.objects.create(
            account=cls.account,
            date=date(2026, 1, 1),
            amount=Decimal("-1.00"),
            reconciled=True,
        )
        cls.unreconciled = Transaction.objects.create(
            account=cls.account,
            date=date(2026, 1, 2),
            amount=Decimal("-1.00"),
            reconciled=False,
        )

    def shown(self, **params):
        params.setdefault("range", "")  # don't let the this-year default interfere
        response = self.client.get(reverse("finance:transaction_list"), params)
        return {t.pk for t in response.context["transactions"]}

    def test_reconciled_only(self):
        self.assertEqual(self.shown(status="reconciled"), {self.reconciled.pk})

    def test_unreconciled_only(self):
        self.assertEqual(self.shown(status="unreconciled"), {self.unreconciled.pk})

    def test_no_status_filter_shows_both(self):
        self.assertEqual(self.shown(), {self.reconciled.pk, self.unreconciled.pk})


class SortingTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other = Account.objects.create(name="Savings")
        cls.big = Transaction.objects.create(account=cls.account, date=date(2026, 1, 1), amount=Decimal("-50.00"))
        cls.small = Transaction.objects.create(account=cls.other, date=date(2026, 1, 2), amount=Decimal("-5.00"))

    def ordered(self, sort):
        response = self.client.get(reverse("finance:transaction_list"), {"range": "", "sort": sort})
        return [t.pk for t in response.context["transactions"]]

    def test_sort_by_account_name(self):
        # "Checking" sorts before "Savings".
        self.assertEqual(self.ordered("account"), [self.big.pk, self.small.pk])

    def test_an_unknown_sort_column_is_ignored_not_a_crash(self):
        response = self.client.get(reverse("finance:transaction_list"), {"range": "", "sort": "nonsense"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort"], "")

    def test_only_the_clicked_amount_header_shows_its_arrow(self):
        # S39: Payment and Deposit sort on their own, so each shows an arrow only when it's the one sorted.
        for column, other in (("payment", "Deposit"), ("deposit", "Payment")):
            with self.subTest(column=column):
                html = self.client.get(
                    reverse("finance:transaction_list"), {"range": "", "sort": column}
                ).content.decode()
                self.assertRegex(html, rf"{column.title()}\s*<i [^>]*caret-up-fill")
                self.assertNotRegex(html, rf"{other}\s*<i ")
                self.assertEqual(html.count("sort-arrow"), 1)
                html = self.client.get(
                    reverse("finance:transaction_list"), {"range": "", "sort": f"-{column}"}
                ).content.decode()
                self.assertRegex(html, rf"{column.title()}\s*<i [^>]*caret-down-fill")
                self.assertEqual(html.count("sort-arrow"), 1)

    def test_the_old_amount_sort_is_ignored_not_a_crash(self):
        # S39: "amount" was the one sort both headers shared; a saved link to it just shows the default order.
        response = self.client.get(reverse("finance:transaction_list"), {"range": "", "sort": "amount"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort"], "")

    def test_account_register_sorts_within_one_account_too(self):
        older = Transaction.objects.create(account=self.account, date=date(2025, 1, 1), amount=Decimal("-1.00"))
        response = self.client.get(reverse("finance:account_detail", args=[self.account.pk]), {"sort": "date"})
        pks = [t.pk for t in response.context["transactions"]]
        self.assertEqual(pks, [older.pk, self.big.pk])


def _pages_with_amount_headers(account):
    """The two pages that have Payment and Deposit headers, as {name: (url, query that shows every row)}."""
    return {
        "transactions": (reverse("finance:transaction_list"), {"range": ""}),
        "register": (reverse("finance:account_detail", args=[account.pk]), {}),
    }


class PaymentAndDepositSortingTests(LoggedInWithAccountTestCase):
    """S39: the Payment and Deposit headers each sort their own side, on the Transactions page and
    on an account's register. The first click puts the biggest first, the second the smallest, and
    the rows with nothing in that column stay at the bottom either way."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        def make(amount):
            return Transaction.objects.create(account=cls.account, date=date(2026, 1, 1), amount=Decimal(amount)).pk

        cls.big_payment, cls.small_payment = make("-50.00"), make("-5.00")
        cls.big_deposit, cls.small_deposit = make("200.00"), make("30.00")
        cls.nothing = make("0.00")  # blank in both columns

    def pages(self):
        return _pages_with_amount_headers(self.account)

    def ordered(self, sort):
        """The pks in each page's order, for ?sort=<sort>."""
        result = {}
        for name, (url, params) in self.pages().items():
            response = self.client.get(url, {**params, "sort": sort})
            result[name] = [t.pk for t in response.context["transactions"]]
        return result

    def assert_order(self, sort, first, rest):
        """`first` must lead in that order; `rest` (the rows with nothing in the column) follow, any order."""
        for name, pks in self.ordered(sort).items():
            with self.subTest(page=name, sort=sort):
                self.assertEqual(pks[: len(first)], first)
                self.assertEqual(set(pks[len(first) :]), set(rest))

    def test_the_first_click_on_payment_puts_the_biggest_payment_first(self):
        others = [self.big_deposit, self.small_deposit, self.nothing]
        self.assert_order("payment", [self.big_payment, self.small_payment], others)

    def test_the_second_click_on_payment_puts_the_smallest_payment_first(self):
        others = [self.big_deposit, self.small_deposit, self.nothing]
        self.assert_order("-payment", [self.small_payment, self.big_payment], others)

    def test_the_first_click_on_deposit_puts_the_biggest_deposit_first(self):
        others = [self.big_payment, self.small_payment, self.nothing]
        self.assert_order("deposit", [self.big_deposit, self.small_deposit], others)

    def test_the_second_click_on_deposit_puts_the_smallest_deposit_first(self):
        others = [self.big_payment, self.small_payment, self.nothing]
        self.assert_order("-deposit", [self.small_deposit, self.big_deposit], others)

    def test_each_header_links_to_its_own_next_click(self):
        for name, (url, params) in self.pages().items():
            with self.subTest(page=name):
                html = self.client.get(url, params).content.decode()
                self.assertRegex(html, r'sort=payment"[^>]*>Payment')
                self.assertRegex(html, r'sort=deposit"[^>]*>Deposit')
                html = self.client.get(url, {**params, "sort": "payment"}).content.decode()
                self.assertRegex(html, r'sort=-payment"[^>]*>Payment')
                self.assertRegex(html, r'sort=deposit"[^>]*>Deposit')

    def test_the_export_is_in_the_same_order_as_the_page_ties_included(self):
        # Rows with nothing in the sorted column tie, so the page breaks the tie by newest first (see
        # _paged()). The export must break it the same way, or it stops matching what you were looking at.
        for sort in ("payment", "-payment", "deposit", "-deposit"):
            with self.subTest(sort=sort):
                page = self.client.get(reverse("finance:transaction_list"), {"range": "", "sort": sort})
                response = self.client.get(
                    reverse("finance:export_transactions"), {"format": "csv", "range": "", "sort": sort}
                )
                rows = list(csv.DictReader(response.content.decode().splitlines()))
                self.assertEqual(
                    [Decimal(row["Amount"]) for row in rows], [t.amount for t in page.context["transactions"]]
                )


class AmountSortEdgeCaseTests(LoggedInWithAccountTestCase):
    """S39: amounts a cent apart, a third click on a header, and sort keys that must be ignored."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        def make(amount):
            return Transaction.objects.create(account=cls.account, date=date(2026, 1, 1), amount=Decimal(amount)).pk

        # Made in an order that is neither smallest-first nor biggest-first, so a tie between them
        # (which a whole-unit rounding would cause) cannot come out right by accident in both directions.
        cls.pay_2c, cls.pay_3c, cls.pay_1c = make("-0.02"), make("-0.03"), make("-0.01")
        cls.dep_2c, cls.dep_3c, cls.dep_1c = make("0.02"), make("0.03"), make("0.01")

    def assert_order_on_both_pages(self, sort, expected):
        for name, (url, params) in _pages_with_amount_headers(self.account).items():
            with self.subTest(page=name, sort=sort):
                response = self.client.get(url, {**params, "sort": sort})
                self.assertEqual([t.pk for t in response.context["transactions"]], expected)

    def test_amounts_a_cent_apart_sort_in_the_right_order(self):
        # Rows with nothing in the sorted column come last, newest first (the tie-break), both ways.
        # Broken if the amounts were rounded to whole units: every row on that side would tie.
        newest_deposits_first = [self.dep_1c, self.dep_3c, self.dep_2c]
        newest_payments_first = [self.pay_1c, self.pay_3c, self.pay_2c]
        self.assert_order_on_both_pages("payment", [self.pay_3c, self.pay_2c, self.pay_1c, *newest_deposits_first])
        self.assert_order_on_both_pages("-payment", [self.pay_1c, self.pay_2c, self.pay_3c, *newest_deposits_first])
        self.assert_order_on_both_pages("deposit", [self.dep_3c, self.dep_2c, self.dep_1c, *newest_payments_first])
        self.assert_order_on_both_pages("-deposit", [self.dep_1c, self.dep_2c, self.dep_3c, *newest_payments_first])

    def test_a_third_click_goes_back_to_the_first_order(self):
        for name, (url, params) in _pages_with_amount_headers(self.account).items():
            for column in ("payment", "deposit"):
                with self.subTest(page=name, column=column):
                    html = self.client.get(url, {**params, "sort": f"-{column}"}).content.decode()
                    self.assertRegex(html, rf'sort={column}"[^>]*>{column.title()}')

    def test_sort_keys_that_are_not_a_column_are_ignored_on_both_pages(self):
        # The register is as safe as the Transactions page: "amount" was a real sort until S39, and
        # a doubled minus, other capitals or a bare minus are not columns either.
        for name, (url, params) in _pages_with_amount_headers(self.account).items():
            for sort in ("amount", "-amount", "--payment", "Payment", "-", " payment"):
                with self.subTest(page=name, sort=sort):
                    response = self.client.get(url, {**params, "sort": sort})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.context["sort"], "")


class AmountSortWithFiltersTests(LoggedInWithAccountTestCase):
    """S39: the Payment and Deposit sorts work together with the Transactions page's filters, and the
    count and the total still describe exactly the rows the filters match."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        savings = Account.objects.create(name="Savings")

        def make(amount, memo="Rent", account=cls.account, reconciled=False):
            return Transaction.objects.create(
                account=account, date=date(2026, 1, 1), amount=Decimal(amount), memo=memo, reconciled=reconciled
            ).pk

        cls.pay_30, cls.pay_10, cls.dep_5 = make("-30.00"), make("-10.00"), make("5.00")
        make("-20.00", reconciled=True)  # filtered out by the status
        make("-99.00", memo="Other")  # filtered out by the search
        make("-500.00", account=savings)  # filtered out by the account

    def get(self, sort):
        return self.client.get(
            reverse("finance:transaction_list"),
            {"range": "", "account": self.account.pk, "q": "rent", "status": "unreconciled", "sort": sort},
        )

    def test_the_filters_and_the_sort_apply_together(self):
        expected = {
            "payment": [self.pay_30, self.pay_10, self.dep_5],
            "-payment": [self.pay_10, self.pay_30, self.dep_5],
            # The one deposit leads both ways; the payments have no deposit and follow, newest first.
            "deposit": [self.dep_5, self.pay_10, self.pay_30],
            "-deposit": [self.dep_5, self.pay_10, self.pay_30],
        }
        for sort, pks in expected.items():
            with self.subTest(sort=sort):
                response = self.get(sort)
                self.assertEqual(response.status_code, 200)
                self.assertEqual([t.pk for t in response.context["transactions"]], pks)

    def test_the_count_and_total_do_not_depend_on_the_sort(self):
        for sort in ("payment", "-deposit"):
            with self.subTest(sort=sort):
                response = self.get(sort)
                self.assertEqual(response.context["transaction_count"], 3)
                self.assertEqual(response.context["total_amount"], Decimal("-35.00"))


@mock.patch("finance.views._helpers.TRANSACTIONS_PER_PAGE", 4)
class AmountSortAcrossPagesTests(LoggedInWithAccountTestCase):
    """S39: with a sort on Payment or Deposit, many rows tie (everything in the other column, and
    equal amounts), yet across pages every row shows exactly once and the order is the one a single
    long page would have. The page size is 4 here, so 13 rows make 4 pages."""

    PAGES = 4

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Payments and deposits, with equal amounts and a few zeros, mixed up so that no amount's
        # rows happen to sit next to each other in the table.
        amounts = ["-5", "5", "0", "-5", "-2", "5", "-5", "0", "2", "-2", "-0.01", "0.02", "-7"]
        for amount in amounts:
            Transaction.objects.create(account=cls.account, date=date(2026, 1, 1), amount=Decimal(amount))

    def expected_pks(self, sort):
        """What the order should be, worked out in plain Python: the column's rows by size (the first
        click biggest first, the second smallest first), then every other row, ties newest first."""
        column, descending = sort.removeprefix("-"), sort.startswith("-")
        rows = list(Transaction.objects.all())
        in_column = [t for t in rows if (t.amount < 0 if column == "payment" else t.amount > 0)]
        others = [t for t in rows if t not in in_column]
        size = (lambda t: abs(t.amount)) if descending else (lambda t: -abs(t.amount))
        return [t.pk for t in sorted(in_column, key=lambda t: (size(t), -t.pk))] + [
            t.pk for t in sorted(others, key=lambda t: -t.pk)
        ]

    def pks_across_pages(self, url, params):
        first = self.client.get(url, params)
        self.assertEqual(first.context["page_obj"].paginator.num_pages, self.PAGES)  # the page size patch is in effect
        pks = []
        for number in range(1, self.PAGES + 1):
            response = self.client.get(url, {**params, "page": number})
            pks += [t.pk for t in response.context["transactions"]]
        return pks

    def test_every_row_shows_once_and_in_order_across_the_pages(self):
        for name, (url, params) in _pages_with_amount_headers(self.account).items():
            for sort in ("payment", "-payment", "deposit", "-deposit"):
                with self.subTest(page=name, sort=sort):
                    self.assertEqual(self.pks_across_pages(url, {**params, "sort": sort}), self.expected_pks(sort))

    def test_the_export_is_every_page_put_together(self):
        amounts = dict(Transaction.objects.values_list("pk", "amount"))
        for sort in ("payment", "-payment", "deposit", "-deposit"):
            with self.subTest(sort=sort):
                response = self.client.get(
                    reverse("finance:export_transactions"), {"format": "csv", "range": "", "sort": sort}
                )
                rows = list(csv.DictReader(response.content.decode().splitlines()))
                self.assertEqual(
                    [Decimal(row["Amount"]) for row in rows], [amounts[pk] for pk in self.expected_pks(sort)]
                )


class DefaultOrderTiesTests(LoggedInWithAccountTestCase):
    """With no sort chosen, the Transactions page, the register and the export all use the model's
    own order (newest date first), and rows that still tie come out newest first."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for day, memo in ((2, "first"), (1, "second"), (2, "third")):
            Transaction.objects.create(account=cls.account, date=date(2026, 3, day), amount=Decimal("-1"), memo=memo)
        # Number and creation time are the other things the default order looks at: make them tie too.
        Transaction.objects.update(transaction_number=1, created_at=datetime(2026, 3, 1, tzinfo=UTC))

    # The two 2 March rows tie on everything, so the later one ("third") leads; 1 March is last.
    EXPECTED_MEMOS = ["third", "first", "second"]

    def test_the_pages_follow_the_default_order(self):
        # Broken if it dropped the model's order: "-pk" alone would put "second" before "first".
        for name, (url, params) in _pages_with_amount_headers(self.account).items():
            with self.subTest(page=name):
                response = self.client.get(url, params)
                self.assertEqual([t.memo for t in response.context["transactions"]], self.EXPECTED_MEMOS)

    def test_the_export_follows_the_same_order(self):
        response = self.client.get(reverse("finance:export_transactions"), {"format": "csv", "range": ""})
        rows = list(csv.DictReader(response.content.decode().splitlines()))
        self.assertEqual([row["Memo"] for row in rows], self.EXPECTED_MEMOS)


class NameColumnSortingTests(LoggedInWithAccountTestCase):
    """S4: the Payee and Category columns sort on name_key, on the Transactions page and on an
    account's register. Plain `name` put "lidl" after "Zara" and a lower-case Cyrillic name after
    every capital one."""

    NAMES = ("Zara", "lidl", "бензин", "Аптека", "Вода")
    IN_ORDER = ["lidl", "Zara", "Аптека", "бензин", "Вода"]

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for name in cls.NAMES:
            Transaction.objects.create(
                account=cls.account,
                date=date(2026, 1, 1),
                payee=Payee.objects.create(name=name),
                category=Category.objects.create(name=name, category_type=Category.CategoryType.EXPENSE),
                amount=Decimal("-1.00"),
            )

    def names_shown(self, column, sort):
        shown = {}
        for page, (url, params) in {
            "transactions": (reverse("finance:transaction_list"), {"range": ""}),
            "register": (reverse("finance:account_detail", args=[self.account.pk]), {}),
        }.items():
            rows = self.client.get(url, {**params, "sort": sort}).context["transactions"]
            shown[page] = [getattr(t, column).name for t in rows]
        return shown

    def test_the_payee_column_sorts_in_any_alphabet(self):
        for page, names in self.names_shown("payee", "payee").items():
            with self.subTest(page=page):
                self.assertEqual(names, self.IN_ORDER)
        for page, names in self.names_shown("payee", "-payee").items():
            with self.subTest(page=page, direction="descending"):
                self.assertEqual(names, self.IN_ORDER[::-1])

    def test_the_category_column_sorts_in_any_alphabet(self):
        for page, names in self.names_shown("category", "category").items():
            with self.subTest(page=page):
                self.assertEqual(names, self.IN_ORDER)
        for page, names in self.names_shown("category", "-category").items():
            with self.subTest(page=page, direction="descending"):
                self.assertEqual(names, self.IN_ORDER[::-1])


class AsIdTests(SimpleTestCase):
    """S2's helper: an id from a hand-editable place is plain ASCII digits that fit SQLite, else None."""

    def test_ids_that_fit(self):
        for text, expected in (
            ("5", 5),
            ("0", 0),
            ("007", 7),
            ("0" * 5000 + "1", 1),
            ("9223372036854775807", 2**63 - 1),
        ):
            with self.subTest(text=text[:30]):
                self.assertEqual(_as_id(text), expected)

    def test_everything_else_is_none_and_never_an_error(self):
        for value in (
            "",
            " ",
            "abc",
            "1.5",
            "-1",
            "+1",
            "1 2",
            "²",
            "٣",
            "9223372036854775808",
            "9" * 30,
            "9" * 4301,
            None,
            5,
            ["5"],
        ):
            with self.subTest(value=str(value)[:30]):
                self.assertIsNone(_as_id(value))

    def test_a_list_keeps_only_the_ids(self):
        self.assertEqual(_as_ids(["1", "x", "²", "3", "9" * 4301, ""]), [1, 3])


class HandEditedIdTests(LoggedInWithAccountTestCase):
    """S2: whatever a hand-edited URL sends in place of an id must not become a 500 page. "²" is
    the sneaky one: `str.isdigit()` is true for it, but `int()` refuses it."""

    # "9" * 4301 is longer than Python will turn into an int at all.
    BAD_IDS = ("abc", "²", "٣", "1.5", "-1", "0x10", "9" * 30, "9" * 4301)

    def test_a_reminder_that_is_not_a_number_is_a_404_like_one_that_does_not_exist(self):
        self.assertEqual(self.client.get(reverse("finance:transaction_add"), {"reminder": "999999"}).status_code, 404)
        for bad in self.BAD_IDS:
            with self.subTest(reminder=bad):
                self.assertEqual(
                    self.client.get(reverse("finance:transaction_add"), {"reminder": bad}).status_code, 404
                )

    def test_a_posted_reminder_that_is_not_a_number_is_a_404_too(self):
        response = self.client.post(reverse("finance:transaction_add"), {"reminder": "abc"})
        self.assertEqual(response.status_code, 404)

    def test_an_account_or_category_filter_that_is_not_a_number_is_ignored(self):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("-1.00"))
        for bad in self.BAD_IDS:
            for param in ("account", "category"):
                with self.subTest(filter=param, value=bad[:12]):
                    page = self.client.get(reverse("finance:transaction_list"), {"range": "", param: bad})
                    self.assertEqual([t.pk for t in page.context["transactions"]], [txn.pk])
                    export = self.client.get(
                        reverse("finance:export_transactions"), {"range": "", param: bad, "format": "csv"}
                    )
                    self.assertEqual(export.status_code, 200)

    def test_the_new_transaction_form_takes_a_hand_edited_account_without_an_error(self):
        # `?account=` here goes straight into the form's starting values, not through _as_id().
        for bad in self.BAD_IDS:
            with self.subTest(account=bad[:12]):
                response = self.client.get(reverse("finance:transaction_add"), {"account": bad})
                self.assertEqual(response.status_code, 200)
        good = self.client.get(reverse("finance:transaction_add"), {"account": self.account.pk})
        self.assertEqual(good.context["form"]["account"].value(), str(self.account.pk))


class MixedIdFilterTests(LoggedInWithAccountTestCase):
    """S2: a hand-edited URL that holds some good ids and some bad ones keeps the good ones: one
    bad id is dropped by itself, it doesn't switch the whole filter off (or on)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other_account = Account.objects.create(name="Savings")
        expense = Category.CategoryType.EXPENSE
        cls.food = Category.objects.create(name="Food", category_type=expense)
        cls.groceries = Category.objects.create(name="Groceries", parent=cls.food, category_type=expense)
        cls.rent = Category.objects.create(name="Rent", category_type=expense)

        def add(account, category):
            return Transaction.objects.create(
                account=account, date=date(2026, 1, 1), category=category, amount=Decimal("-1.00")
            )

        cls.in_food = add(cls.account, cls.food)
        cls.in_groceries = add(cls.account, cls.groceries)
        cls.in_rent = add(cls.account, cls.rent)
        cls.uncategorized = add(cls.account, None)
        cls.in_savings = add(cls.other_account, cls.rent)

    def page(self, **params):
        return self.client.get(reverse("finance:transaction_list"), {"range": "", **params})

    def shown(self, **params):
        return {t.pk for t in self.page(**params).context["transactions"]}

    def test_a_good_category_keeps_working_next_to_bad_ones(self):
        params = {"category": [self.rent.pk, "abc", "²", "9" * 30, "9" * 4301, "-1"]}
        self.assertEqual(self.shown(**params), {self.in_rent.pk, self.in_savings.pk})
        export = self.client.get(reverse("finance:export_transactions"), {"range": "", "format": "csv", **params})
        self.assertEqual(export.content.decode().count("\n"), 3)  # the header and the two rent rows

    def test_the_filter_label_counts_only_the_good_ids(self):
        page = self.page(category=[self.rent.pk, "abc", "²"])
        self.assertEqual(page.context["category_filter_label"], "Rent")
        page = self.page(category=[self.rent.pk, self.food.pk, "abc"])
        self.assertEqual(page.context["category_filter_label"], "2 categories selected")

    def test_a_good_category_with_its_subcategories_next_to_a_bad_id(self):
        page = self.page(category=[self.food.pk, "abc"], with_subcategories="1")
        self.assertEqual({t.pk for t in page.context["transactions"]}, {self.in_food.pk, self.in_groceries.pk})
        self.assertEqual(page.context["selected_category_obj"], self.food)

    def test_uncategorized_next_to_a_bad_id_is_still_only_the_uncategorized(self):
        self.assertEqual(self.shown(category=["none", "abc", "²"]), {self.uncategorized.pk})
        self.assertEqual(
            self.shown(category=["none", self.rent.pk, "abc"]),
            {self.uncategorized.pk, self.in_rent.pk, self.in_savings.pk},
        )

    def test_a_good_account_and_a_bad_category_still_filter_by_the_account(self):
        self.assertEqual(self.shown(account=self.other_account.pk, category="abc"), {self.in_savings.pk})
        self.assertEqual(self.shown(account="abc", category=self.rent.pk), {self.in_rent.pk, self.in_savings.pk})

    def test_a_deleted_account_or_category_in_an_old_link(self):
        # Characterization, not a claim that it is right. Possible bug: the two filters disagree.
        # An account id that is a number but names nothing shows no rows, while a category id that
        # names nothing is dropped like a bad one and shows every row (the label says "All categories").
        self.assertEqual(self.shown(account=999999), set())
        page = self.page(category=999999)
        self.assertEqual(len(page.context["transactions"]), 5)
        self.assertEqual(page.context["category_filter_label"], "All categories")


class HandEditedDateTests(LoggedInWithAccountTestCase):
    """S93's family: a date typed into the Transactions page's URL must not become a 500 page."""

    def test_no_range_can_be_longer_than_a_year_after_year_9999(self):
        # start + one year overflows the calendar there.
        self.assertFalse(_longer_than_a_year(date(9999, 6, 1), date(9999, 12, 31)))
        self.assertTrue(_longer_than_a_year(date(1, 1, 1), date(9999, 12, 31)))

    def test_extreme_dates_give_the_page_and_the_export_not_an_error(self):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("-1.00"))
        for params in (
            {"start": "9999-06-01", "end": "9999-12-31"},
            {"start": "0001-01-01", "end": "9999-12-31"},
            {"range": "custom", "start": "0001-01-01", "end": "0001-06-01"},
        ):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(reverse("finance:transaction_list"), params).status_code, 200)
                export = self.client.get(reverse("finance:export_transactions"), {**params, "format": "csv"})
                self.assertEqual(export.status_code, 200)
        wide = self.client.get(reverse("finance:transaction_list"), {"start": "0001-01-01", "end": "9999-12-31"})
        self.assertEqual([t.pk for t in wide.context["transactions"]], [txn.pk])

    def test_the_new_transaction_form_takes_any_hand_edited_date(self):
        # `?date=` is "Save and add another"'s way of handing back the date just used. A date that
        # isn't one starts the form on today; one at the very edge of the calendar is kept.
        today = timezone.localdate()
        for text, expected in (
            ("9999-12-31", date(9999, 12, 31)),
            ("0001-01-01", date(1, 1, 1)),
            ("2026-02-30", today),
            ("abc", today),
            ("", today),
            ("1" * 5000, today),
        ):
            with self.subTest(date=text[:12]):
                response = self.client.get(reverse("finance:transaction_add"), {"date": text})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["form"]["date"].value(), expected)


class TransactionNumberFormTests(StashTestCase):
    def test_form_includes_transaction_number(self):
        self.assertIn("transaction_number", TransactionForm().fields)

    def test_the_number_box_has_no_help_text_with_an_internal_code(self):
        # S5: the form's own text ended "...starting over each year, D9)" and no page showed it. What
        # is left is the model's plain sentence.
        help_text = TransactionForm().fields["transaction_number"].help_text
        self.assertNotIn("D9", help_text)
        self.assertEqual(help_text, models.Transaction._meta.get_field("transaction_number").help_text)

    def test_clearing_the_number_on_edit_reassigns_the_next_one(self):
        account = Account.objects.create(name="Checking")
        Transaction.objects.create(account=account, date=date(2026, 1, 1), amount=Decimal("-1.00"))
        txn = Transaction.objects.create(account=account, date=date(2026, 1, 2), amount=Decimal("-1.00"))
        self.assertEqual(txn.transaction_number, 2)

        form = TransactionForm(
            {
                "account": account.pk,
                "date": "02/01/2026",
                "payee_name": "",
                "category": "",
                "memo": "",
                "amount": "-1.00",
                "transaction_number": "",
            },
            instance=txn,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        # "Next" is computed fresh from what's in the database, including this record's own
        # not-yet-saved old number (2) - so clearing it lands one past that, at 3, not back at 2.
        self.assertEqual(saved.transaction_number, 3)

    def test_a_chosen_number_is_kept(self):
        account = Account.objects.create(name="Checking")
        txn = Transaction.objects.create(account=account, date=date(2026, 1, 1), amount=Decimal("-1.00"))
        form = TransactionForm(
            {
                "account": account.pk,
                "date": "01/01/2026",
                "payee_name": "",
                "category": "",
                "memo": "",
                "amount": "-1.00",
                "transaction_number": "500",
            },
            instance=txn,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().transaction_number, 500)


class TransactionListFilterTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.food = Category.objects.create(name="Food")
        cls.groceries = Category.objects.create(name="Groceries", parent=cls.food)

        def add(on, category):
            return Transaction.objects.create(account=cls.account, date=on, category=category, amount=Decimal("-1.00"))

        cls.september_groceries = add(date(2026, 9, 5), cls.groceries)
        cls.september_food = add(date(2026, 9, 6), cls.food)
        cls.september_uncategorized = add(date(2026, 9, 7), None)
        cls.august_groceries = add(date(2026, 8, 30), cls.groceries)

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


class TransactionListTests(LoggedInWithAccountTestCase):
    def test_all_transactions_shown_with_a_fixed_number_of_queries(self):
        parent = Category.objects.create(name="Parent")
        categories = [Category.objects.create(name=f"Sub {i}", parent=parent) for i in range(5)]
        payee = Payee.objects.create(name="Shop")
        Transaction.objects.bulk_create(
            Transaction(
                account=self.account,
                date=date(2026, 1, 1),
                payee=payee,
                category=categories[i % 5],
                amount=Decimal("-1.00"),
            )
            for i in range(600)
        )
        with CaptureQueriesContext(connections[settings.TEST_STASH]) as queries:
            response = self.client.get(reverse("finance:transaction_list"))
        self.assertEqual(len(response.context["transactions"]), 600)
        self.assertLess(len(queries.captured_queries), 15)


class TransactionListTotalPositionTests(LoggedInWithAccountTestCase):
    def test_total_sits_in_a_flex_row_with_the_transaction_count(self):
        Transaction.objects.create(account=self.account, date=timezone.localdate(), amount=Decimal("-5.00"))
        content = self.client.get(reverse("finance:transaction_list")).content.decode()
        row = content.split("d-flex justify-content-between align-items-baseline", 1)[1].split(
            '<div class="card">', 1
        )[0]
        self.assertIn("1 transaction", row)
        self.assertIn("Total:", row)


class TransactionListTotalTests(LoggedInWithAccountTestCase):
    def test_total_amount_is_the_sum_of_the_filtered_transactions(self):
        Transaction.objects.create(account=self.account, date=timezone.localdate(), amount=Decimal("100.00"))
        Transaction.objects.create(account=self.account, date=timezone.localdate(), amount=Decimal("-40.00"))
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertEqual(response.context["total_amount"], Decimal("60.00"))
        self.assertContains(response, "Total:")


class LongerThanAYearTests(SimpleTestCase):
    """8.11: which date ranges are split into pages."""

    def test_a_whole_calendar_year_is_not_longer_than_a_year(self):
        self.assertFalse(_longer_than_a_year(date(2026, 1, 1), date(2026, 12, 31)))
        self.assertFalse(_longer_than_a_year(date(2028, 1, 1), date(2028, 12, 31)))  # a leap year

    def test_one_day_more_is(self):
        self.assertTrue(_longer_than_a_year(date(2026, 1, 1), date(2027, 1, 1)))

    def test_a_range_open_at_either_end_is(self):
        self.assertTrue(_longer_than_a_year(None, None))
        self.assertTrue(_longer_than_a_year(date(2026, 1, 1), None))
        self.assertTrue(_longer_than_a_year(None, date(2026, 1, 1)))


class TransactionPagingTests(LoggedInWithAccountTestCase):
    """8.11: a range longer than a year shows 1,000 rows per page; the count and the total
    always cover every page."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        lidl = Payee.objects.create(name="Lidl")
        # 1,001 rows inside 2026, all with the same payee, so a sort by payee ties on every row.
        amounts = [-Decimal(i % 7 + 1) for i in range(1001)]
        Transaction.objects.bulk_create(
            Transaction(
                account=cls.account,
                date=date(2026, 1, 1) + timedelta(days=i % 365),
                payee=lidl,
                category=cls.groceries,
                amount=amount,
            )
            for i, amount in enumerate(amounts)
        )
        cls.total = sum(amounts)

    def get(self, **params):
        return self.client.get(reverse("finance:transaction_list"), params)

    def test_a_range_over_a_year_has_two_pages_with_the_same_count_and_total(self):
        first, second = self.get(range=""), self.get(range="", page=2)
        self.assertEqual(len(first.context["transactions"]), 1000)
        self.assertEqual(len(second.context["transactions"]), 1)
        for response in (first, second):
            self.assertEqual(response.context["total_amount"], self.total)
            self.assertContains(response, "1001 transactions")
        self.assertContains(first, "showing 1–1000")
        self.assertContains(second, "showing 1001–1001")

    def test_a_one_year_range_never_paginates(self):
        response = self.get(start="2026-01-01", end="2026-12-31")
        self.assertIsNone(response.context["page_obj"])
        self.assertEqual(len(response.context["transactions"]), 1001)
        self.assertNotContains(response, 'class="pagination')

    def test_one_day_more_than_a_year_does(self):
        self.assertEqual(len(self.get(start="2026-01-01", end="2027-01-01").context["transactions"]), 1000)

    def test_page_links_keep_every_filter_and_sort(self):
        filters = {
            "range": "",
            "category": str(self.groceries.pk),
            "q": "lidl",
            "status": "unreconciled",
            "sort": "-payment",
        }
        response = self.get(**filters)
        links = re.findall(r'href="([^"]*)" aria-label="Page 2"', response.content.decode())
        self.assertEqual(len(links), 2)  # the page links above and below the table
        query = parse_qs(urlsplit(html.unescape(links[0])).query, keep_blank_values=True)
        self.assertEqual(query, {**{key: [value] for key, value in filters.items()}, "page": ["2"]})

    def test_a_new_sort_starts_again_at_page_one(self):
        sort_links = re.findall(r'href="([^"]*)" class="sort-link"', self.get(range="", page=2).content.decode())
        self.assertEqual(len(sort_links), 8)
        for link in sort_links:
            self.assertNotIn("page=", link)

    def test_rows_that_tie_on_the_sort_column_appear_exactly_once(self):
        first, second = self.get(range="", sort="payee"), self.get(range="", sort="payee", page=2)
        shown = [t.pk for t in first.context["transactions"]] + [t.pk for t in second.context["transactions"]]
        self.assertEqual(sorted(shown), sorted(Transaction.objects.values_list("pk", flat=True)))

    def test_a_page_number_that_doesnt_exist_shows_the_nearest_page(self):
        self.assertEqual(len(self.get(range="", page=99).context["transactions"]), 1)
        self.assertEqual(len(self.get(range="", page="abc").context["transactions"]), 1000)

    def test_the_second_page_renders_cleanly(self):
        content = self.get(range="", page=2).content.decode()
        for leaked in LEAKED_TEMPLATE_SYNTAX:
            self.assertNotIn(leaked, content)
        self.assertIn('aria-current="page"><span class="page-link">2</span>', content)


class InactiveAccountTransactionConfirmTests(LoggedInTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.account = Account.objects.create(name="Old Wallet", is_active=False)

    def test_posting_without_confirmation_asks_first_and_does_not_save(self):
        response = self.client.post(
            reverse("finance:transaction_add"),
            {
                "account": self.account.pk,
                "date": "01/03/2026",
                "payee_name": "Shop",
                "category": "",
                "memo": "",
                "amount": "-10.00",
            },
        )
        self.assertContains(response, "is inactive")
        self.assertFalse(Transaction.objects.exists())
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_active)

    def test_confirming_saves_the_transaction_and_reactivates_the_account(self):
        response = self.client.post(
            reverse("finance:transaction_add"),
            {
                "account": self.account.pk,
                "date": "01/03/2026",
                "payee_name": "Shop",
                "category": "",
                "memo": "",
                "amount": "-10.00",
                "confirm_inactive_account": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Transaction.objects.filter(account=self.account).exists())
        self.account.refresh_from_db()
        self.assertTrue(self.account.is_active)

    def test_an_already_active_account_never_shows_the_prompt(self):
        active = Account.objects.create(name="Checking")
        response = self.client.post(
            reverse("finance:transaction_add"),
            {
                "account": active.pk,
                "date": "01/03/2026",
                "payee_name": "Shop",
                "category": "",
                "memo": "",
                "amount": "-10.00",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Transaction.objects.filter(account=active).exists())


class TransactionListTotalCurrencySymbolTests(LoggedInTestCase):
    def test_total_shows_a_currency_symbol(self):
        account = Account.objects.create(name="Checking", currency="USD")
        Transaction.objects.create(account=account, date=timezone.localdate(), amount=Decimal("100.00"))
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertContains(response, "$100.00")


class EditRedirectTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.other = Account.objects.create(name="Savings")
        cls.txn = Transaction.objects.create(account=cls.account, date=date(2026, 1, 1), amount=Decimal("-1.00"))

    def edit(self, **extra):
        data = {
            "account": self.account.pk,
            "date": "01/01/2026",
            "payee_name": "",
            "category": "",
            "memo": "",
            "amount": "-1.00",
        }
        data.update(extra)
        return self.client.post(reverse("finance:transaction_edit", args=[self.txn.pk]), data)

    def test_no_next_falls_back_to_the_transactions_own_account(self):
        response = self.edit()
        self.assertRedirects(response, reverse("finance:account_detail", args=[self.account.pk]))

    def test_next_from_the_account_register_returns_there(self):
        next_url = reverse("finance:account_detail", args=[self.account.pk]) + "?sort=payment"
        response = self.edit(next=next_url)
        self.assertRedirects(response, next_url)

    def test_next_from_the_all_accounts_list_returns_there(self):
        next_url = reverse("finance:transaction_list") + "?range=this_month"
        response = self.edit(next=next_url)
        self.assertRedirects(response, next_url)

    def test_an_external_next_is_ignored(self):
        response = self.edit(**{"next": "https://evil.example/"})
        self.assertRedirects(response, reverse("finance:account_detail", args=[self.account.pk]))

    def test_the_edit_link_on_the_account_register_carries_next_back_to_it(self):
        response = self.client.get(reverse("finance:account_detail", args=[self.account.pk]))
        edit_url = reverse("finance:transaction_edit", args=[self.txn.pk])
        account_url = reverse("finance:account_detail", args=[self.account.pk])
        self.assertContains(response, f"{edit_url}?next={account_url}")

    def test_the_edit_link_on_the_transaction_list_carries_next_back_to_it(self):
        response = self.client.get(reverse("finance:transaction_list"), {"range": ""})
        edit_url = reverse("finance:transaction_edit", args=[self.txn.pk])
        list_url = reverse("finance:transaction_list")
        self.assertContains(response, f"{edit_url}?next={list_url}%3Frange%3D")


class NewTransactionNumberPreviewTests(LoggedInWithAccountTestCase):
    def test_add_form_prefills_the_next_available_number(self):
        Transaction.objects.create(
            account=self.account, date=timezone.localdate(), transaction_number=41, amount=Decimal("-1.00")
        )
        response = self.client.get(reverse("finance:transaction_add"))
        self.assertEqual(response.context["form"].initial.get("transaction_number"), 42)

    def test_edit_form_shows_the_transactions_own_number_not_a_fresh_preview(self):
        txn = Transaction.objects.create(
            account=self.account, date=timezone.localdate(), transaction_number=7, amount=Decimal("-1.00")
        )
        Transaction.objects.create(
            account=self.account, date=timezone.localdate(), transaction_number=8, amount=Decimal("-1.00")
        )
        response = self.client.get(reverse("finance:transaction_edit", args=[txn.pk]))
        # The highest number right now is 8, so a fresh "next available" preview would wrongly
        # show 9 here - editing must show this transaction's own number (7) untouched instead.
        self.assertEqual(response.context["form"].initial.get("transaction_number"), 7)


class TransactionListDeleteAndLabelsTests(LoggedInWithAccountTestCase):
    def test_delete_button_present_and_links_back_here(self):
        txn = Transaction.objects.create(account=self.account, date=timezone.localdate(), amount=Decimal("-1.00"))
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertContains(response, reverse("finance:transaction_delete", args=[txn.pk]))

    def test_deleting_from_the_list_returns_to_the_list(self):
        txn = Transaction.objects.create(account=self.account, date=timezone.localdate(), amount=Decimal("-1.00"))
        list_url = reverse("finance:transaction_list")
        # Real usage: the confirm-delete GET renders `next` as a hidden field, submitted with
        # the POST - not passed as a query string on the POST itself.
        response = self.client.post(reverse("finance:transaction_delete", args=[txn.pk]), {"next": list_url})
        self.assertRedirects(response, list_url)

    def test_status_filter_labels_renamed(self):
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertContains(response, "All Transactions")
        self.assertContains(response, ">Reconciled<")
        self.assertContains(response, ">Unreconciled<")
        self.assertNotContains(response, "Reconciled or not")
        self.assertNotContains(response, "Reconciled only")
        self.assertNotContains(response, "Not reconciled")


class ReconciledRowStylingTests(LoggedInWithAccountTestCase):
    def test_reconciled_rows_get_the_faded_class(self):
        Transaction.objects.create(
            account=self.account, date=timezone.localdate(), amount=Decimal("-1.00"), reconciled=True
        )
        Transaction.objects.create(
            account=self.account, date=timezone.localdate(), amount=Decimal("-2.00"), reconciled=False
        )
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertEqual(response.content.decode().count("row-reconciled"), 1)


class SaveAndAddAnotherTests(LoggedInTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.account = Account.objects.create(name="Checking")

    def _data(self, **extra):
        return {
            "account": self.account.pk,
            "date": "01/03/2026",
            "payee_name": "Shop",
            "category": "",
            "memo": "",
            "amount": "-10.00",
            **extra,
        }

    def test_new_form_shows_both_buttons(self):
        response = self.client.get(reverse("finance:transaction_add"))
        self.assertContains(response, "Save and add another")

    def test_save_goes_to_all_transactions(self):
        response = self.client.post(reverse("finance:transaction_add"), self._data())
        self.assertRedirects(response, reverse("finance:transaction_list"))
        self.assertEqual(Transaction.objects.count(), 1)

    def test_save_and_add_another_returns_to_blank_form_on_same_account(self):
        response = self.client.post(reverse("finance:transaction_add"), self._data(save_and_add="1"))
        self.assertRedirects(
            response,
            reverse("finance:transaction_add") + f"?account={self.account.pk}&date=2026-03-01",
        )
        self.assertEqual(Transaction.objects.count(), 1)

    def test_edit_form_has_no_add_another_button(self):
        txn = Transaction.objects.create(account=self.account, date=date(2026, 3, 1), amount=-5)
        response = self.client.get(reverse("finance:transaction_edit", args=[txn.pk]))
        self.assertNotContains(response, "Save and add another")

    def test_blank_form_prefills_the_date_from_the_previous_save(self):
        response = self.client.get(reverse("finance:transaction_add"), {"date": "2026-03-01"})
        self.assertEqual(response.context["form"].initial["date"], date(2026, 3, 1))

    def test_bad_date_in_url_falls_back_to_today(self):
        response = self.client.get(reverse("finance:transaction_add"), {"date": "garbage"})
        self.assertEqual(response.context["form"].initial["date"], timezone.localdate())

    @mock.patch("django.utils.timezone.now", return_value=datetime(2026, 10, 5, 12, 0, tzinfo=UTC))
    def test_today_is_the_stashs_own_date_not_the_computers(self, _now):
        # 8.1 (F8): noon in London is already the next day in Auckland (UTC+13 in October).
        profile = Profile.for_user(self.user)
        profile.time_zone = "Pacific/Auckland"
        profile.save()
        response = self.client.get(reverse("finance:transaction_add"))
        self.assertEqual(response.context["form"].initial["date"], date(2026, 10, 6))


class TransactionListEditModeRenderTests(LoggedInTestCase):
    def test_list_page_renders_edit_mode_controls_for_a_transaction(self):
        account = Account.objects.create(name="Checking")
        category = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=account,
            date=date(2026, 9, 20),
            category=category,
            memo="Lidl",
            amount=Decimal("-12.50"),
        )
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('id="edit-mode-toggle"', content)
        self.assertIn('data-field="category"', content)
        self.assertIn('data-field="reconciled"', content)
        self.assertIn(reverse("finance:transaction_quick_edit", args=[0]), content)

    def test_a_transfer_categorys_own_transaction_still_offers_it_in_the_dropdown(self):
        # Transfer categories are excluded from the shared options list (like the full edit
        # form) but a transaction already filed under one must still show it, selected. Edit
        # mode adds that option from the row's data-category-label (8.10).
        source = Account.objects.create(name="Checking")
        destination = Account.objects.create(name="Savings")
        transfer_category = destination.transfer_category
        Transaction.objects.create(
            account=source,
            date=date(2026, 9, 20),
            category=transfer_category,
            amount=Decimal("-100"),
        )
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertContains(response, f'data-category="{transfer_category.pk}" data-category-label="[Savings]"')
        self.assertNotContains(response, f'<option value="{transfer_category.pk}">[Savings]</option>')

    def test_every_filter_sits_in_the_one_fieldset_edit_mode_locks(self):
        # 8.10b: the script disables the fieldset (and the links) while Edit mode is on.
        response = self.client.get(reverse("finance:transaction_list"))
        content = response.content.decode()
        start = content.index('id="transaction-filters"')
        form = content[start : content.index("</form>", start)]
        fieldset = form[form.index('<fieldset id="transaction-filter-fields">') : form.index("</fieldset>")]
        for name in ("range", "start", "end", "status", "account", "category", "q"):
            with self.subTest(field=name):
                self.assertIn(f'name="{name}"', fieldset)
        self.assertIn(">Filter</button>", fieldset)
        self.assertIn('id="edit-mode-filter-hint" hidden', form)
        self.assertIn("Finish editing to change filters.", form)


class TransactionQuickEditTests(LoggedInTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.account = Account.objects.create(name="Checking")
        cls.category = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        cls.txn = Transaction.objects.create(
            account=cls.account,
            date=date(2026, 9, 20),
            category=cls.category,
            memo="Lidl",
            amount=Decimal("-12.50"),
        )

    def _post(self, **overrides):
        data = {
            "account": self.account.pk,
            "date": "20/09/2026",
            "transaction_number": self.txn.transaction_number,
            "payee_name": "Lidl",
            "category": self.category.pk,
            "memo": "Lidl",
            "amount": "-12.50",
        }
        data.update(overrides)
        return self.client.post(reverse("finance:transaction_quick_edit", args=[self.txn.pk]), data)

    def test_editing_the_memo_saves_and_returns_the_updated_row(self):
        response = self._post(memo="Lidl - weekly shop")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["row"]["memo"], "Lidl - weekly shop")
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.memo, "Lidl - weekly shop")

    def test_switching_to_a_deposit_flips_the_sign(self):
        response = self._post(amount="40.00")
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.amount, Decimal("40.00"))
        self.assertEqual(payload["row"]["payment"], "")
        self.assertNotEqual(payload["row"]["deposit"], "")

    def test_changing_the_payee_resolves_or_creates_a_payee_like_the_full_form(self):
        response = self._post(payee_name="Rewe")
        self.assertTrue(response.json()["ok"])
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.payee.name, "Rewe")
        self.assertTrue(Payee.objects.filter(name="Rewe").exists())

    def test_invalid_date_is_rejected_without_saving(self):
        response = self._post(date="not-a-date")
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertIn("date", payload["errors"])
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.date, date(2026, 9, 20))

    def test_moving_to_an_inactive_account_is_blocked_with_a_clear_error(self):
        inactive = Account.objects.create(name="Old account", is_active=False)
        response = self._post(account=inactive.pk)
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertIn("account", payload["errors"])
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.account_id, self.account.pk)

    def test_toggling_reconciled_on(self):
        response = self._post(reconciled="on")
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["row"]["reconciled"])
        self.txn.refresh_from_db()
        self.assertTrue(self.txn.reconciled)

    def test_get_is_not_allowed(self):
        response = self.client.get(reverse("finance:transaction_quick_edit", args=[self.txn.pk]))
        self.assertEqual(response.status_code, 405)

    def test_every_edit_mode_control_carries_the_class_the_script_listens_for(self):
        # The script saves a row when a control with this class changes. The Payment and Deposit
        # boxes have no data-field (the amount is worked out from the pair), so the class is the
        # only thing that makes a change to just one of them save.
        content = self.client.get(reverse("finance:transaction_list")).content.decode()
        start = content.index('<template id="quick-edit-controls">')
        controls = re.findall(r"<(?:input|select)\b[^>]*>", content[start : content.index("</template>", start)])
        self.assertEqual(len(controls), 9)
        for control in controls:
            with self.subTest(control=control):
                self.assertIn("quick-edit-cell", control)

    def test_the_script_saves_on_any_edit_mode_control_not_only_data_field_ones(self):
        script = Path(settings.BASE_DIR) / "finance" / "static" / "finance" / "js" / "transaction-quick-edit.js"
        handler = script.read_text(encoding="utf-8").split("table.addEventListener('change'")[1]
        self.assertIn("closest('.quick-edit-cell')", handler)

    def test_a_minus_typed_into_the_payment_box_is_refused_not_saved(self):
        # The script puts its own minus in front of the Payment box, so a minus typed there arrives doubled.
        response = self._post(amount="--5")
        self.assertEqual(response.status_code, 400)
        self.assertIn("amount", response.json()["errors"])
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.amount, Decimal("-12.50"))

    def test_an_empty_amount_is_refused_not_saved_as_zero(self):
        # S55: the script sends an empty amount when both boxes are empty (it used to send "0"),
        # so the form's own "required" check catches it. This guards the server's half of that
        # contract; the script's half (the queue and the empty amount) has no automated test and
        # was checked by hand with a stand-in page.
        response = self._post(amount="")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["errors"]["amount"], ["This field is required."])
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.amount, Decimal("-12.50"))


class TransactionSearchTests(LoggedInWithAccountTestCase):
    def test_the_search_finds_a_payee_and_a_memo(self):
        today = timezone.localdate()
        Transaction.objects.create(
            account=self.account, date=today, payee=Payee.objects.create(name="Rewe"), amount=Decimal("-4")
        )
        Transaction.objects.create(account=self.account, date=today, memo="Birthday cake", amount=Decimal("-5"))
        Transaction.objects.create(account=self.account, date=today, memo="Unrelated", amount=Decimal("-6"))

        by_payee = self.client.get(reverse("finance:transaction_list"), {"q": "rewe"})
        by_memo = self.client.get(reverse("finance:transaction_list"), {"q": "BIRTHDAY"})

        self.assertEqual([t.amount for t in by_payee.context["transactions"]], [Decimal("-4")])
        self.assertEqual([t.amount for t in by_memo.context["transactions"]], [Decimal("-5")])


class SafeNextTests(SimpleTestCase):
    """1.15 (F55): only pages on this site are allowed as a "next" destination."""

    def test_off_site_destinations_are_refused(self):
        request = RequestFactory().get("/")
        for url in ("/\\evil.com", "//evil.com", "http://evil.com/", "https://evil.com", "javascript:alert(1)"):
            with self.subTest(url=url):
                self.assertEqual(_safe_next(request, url), "")
        self.assertEqual(_safe_next(request, "/transactions/?account=3"), "/transactions/?account=3")
