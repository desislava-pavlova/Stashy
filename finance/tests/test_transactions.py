"""Transactions: numbers, the add and edit forms, the list (filters, sorting, totals, Edit
mode), deleting, and where you land after saving."""

import html
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import connections
from django.test import RequestFactory, SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .. import models
from ..forms import TransactionForm
from ..models import Account, Category, Payee, Profile, Transaction
from ..views._helpers import _safe_next
from ..views.transactions import _longer_than_a_year
from .base import LoggedInTestCase, LoggedInWithAccountTestCase, StashTestCase

TODAY = date(2026, 9, 13)


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


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
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


@mock.patch("django.utils.timezone.localdate", return_value=TODAY)
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

    def test_sort_by_amount_ascending(self):
        self.assertEqual(self.ordered("amount"), [self.big.pk, self.small.pk])

    def test_sort_by_amount_descending(self):
        self.assertEqual(self.ordered("-amount"), [self.small.pk, self.big.pk])

    def test_sort_by_account_name(self):
        # "Checking" sorts before "Savings".
        self.assertEqual(self.ordered("account"), [self.big.pk, self.small.pk])

    def test_an_unknown_sort_column_is_ignored_not_a_crash(self):
        response = self.client.get(reverse("finance:transaction_list"), {"range": "", "sort": "nonsense"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort"], "")

    def test_account_register_sorts_within_one_account_too(self):
        older = Transaction.objects.create(account=self.account, date=date(2025, 1, 1), amount=Decimal("-1.00"))
        response = self.client.get(reverse("finance:account_detail", args=[self.account.pk]), {"sort": "date"})
        pks = [t.pk for t in response.context["transactions"]]
        self.assertEqual(pks, [older.pk, self.big.pk])


class TransactionNumberFormTests(StashTestCase):
    def test_form_includes_transaction_number(self):
        self.assertIn("transaction_number", TransactionForm().fields)

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
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertContains(response, "justify-content-between")


class TransactionListTotalTests(LoggedInWithAccountTestCase):
    def test_total_amount_is_the_sum_of_the_filtered_transactions(self):
        Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("100.00"))
        Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("-40.00"))
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
            "sort": "-amount",
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
        for leaked in ("{#", "#}", "{%", "%}"):
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
        Transaction.objects.create(account=account, date=date.today(), amount=Decimal("100.00"))
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
        next_url = reverse("finance:account_detail", args=[self.account.pk]) + "?sort=amount"
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
            account=self.account, date=date.today(), transaction_number=41, amount=Decimal("-1.00")
        )
        response = self.client.get(reverse("finance:transaction_add"))
        self.assertEqual(response.context["form"].initial.get("transaction_number"), 42)

    def test_edit_form_shows_the_transactions_own_number_not_a_fresh_preview(self):
        txn = Transaction.objects.create(
            account=self.account, date=date.today(), transaction_number=7, amount=Decimal("-1.00")
        )
        Transaction.objects.create(
            account=self.account, date=date.today(), transaction_number=8, amount=Decimal("-1.00")
        )
        response = self.client.get(reverse("finance:transaction_edit", args=[txn.pk]))
        # The highest number right now is 8, so a fresh "next available" preview would wrongly
        # show 9 here - editing must show this transaction's own number (7) untouched instead.
        self.assertEqual(response.context["form"].initial.get("transaction_number"), 7)


class TransactionListDeleteAndLabelsTests(LoggedInWithAccountTestCase):
    def test_delete_button_present_and_links_back_here(self):
        txn = Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("-1.00"))
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertContains(response, reverse("finance:transaction_delete", args=[txn.pk]))

    def test_deleting_from_the_list_returns_to_the_list(self):
        txn = Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("-1.00"))
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
        Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("-1.00"), reconciled=True)
        Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("-2.00"), reconciled=False)
        response = self.client.get(reverse("finance:transaction_list"))
        self.assertEqual(response.content.decode().count("row-reconciled"), 1)


class TagsAreGoneTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser("tester", password="not-a-real-password-123")

    def setUp(self):
        self.client.force_login(self.user)

    def test_transaction_model_has_no_tags_field(self):
        self.assertNotIn("tags", {field.name for field in Transaction._meta.get_fields()})

    def test_transaction_form_has_no_tags_field(self):
        self.assertNotIn("tags", TransactionForm().fields)

    def test_transaction_form_page_does_not_mention_tags(self):
        account = Account.objects.create(name="Checking")
        response = self.client.get(reverse("finance:transaction_add") + f"?account={account.pk}")
        self.assertNotContains(response, "Tags")

    def test_tag_admin_is_not_registered(self):

        self.assertFalse(any(model.__name__ == "Tag" for model in admin.site._registry))

    def test_tag_model_no_longer_exists(self):

        self.assertFalse(hasattr(models, "Tag"))


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


class PayeeNameColumnTests(StashTestCase):
    """2.9 (F51): the always-blank Transaction.payee_name column is gone."""

    def test_the_column_is_gone(self):
        self.assertNotIn("payee_name", [field.name for field in Transaction._meta.get_fields()])

    def test_the_transactions_search_still_finds_payees_and_memos(self):
        user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(user)
        account = Account.objects.create(name="Checking")
        Transaction.objects.create(
            account=account, date=date.today(), payee=Payee.objects.create(name="Rewe"), amount=Decimal("-4")
        )
        response = self.client.get(reverse("finance:transaction_list"), {"q": "rewe"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rewe")


class SafeNextTests(SimpleTestCase):
    """1.15 (F55): only pages on this site are allowed as a "next" destination."""

    def test_off_site_destinations_are_refused(self):
        request = RequestFactory().get("/")
        for url in ("/\\evil.com", "//evil.com", "http://evil.com/", "https://evil.com", "javascript:alert(1)"):
            with self.subTest(url=url):
                self.assertEqual(_safe_next(request, url), "")
        self.assertEqual(_safe_next(request, "/transactions/?account=3"), "/transactions/?account=3")
