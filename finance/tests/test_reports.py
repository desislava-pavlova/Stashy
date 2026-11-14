"""The Reports page: its filters and defaults, the tables and their markup, the averages and
their tints, the notices, and Watched expenses."""

import csv
import io
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from .. import reporting
from ..forms import DateRangeFilterForm, WatchedExpensesForm, watched_expenses_picker_context
from ..models import Account, Category, Profile, Transaction
from ..regional import NBSP
from ..templatetags.stashy_format import diff_tint
from .base import LEAKED_TEMPLATE_SYNTAX, LoggedInTestCase, LoggedInWithAccountTestCase, StashTestCase

EXPENSE, INCOME = Category.CategoryType.EXPENSE, Category.CategoryType.INCOME


def _row_html(page, opening_tag):
    """The inside of the table row of the rendered `page` that starts with `opening_tag`."""
    return re.search(re.escape(opening_tag) + r"(.*?)</tr>", page, re.S).group(1)


def _amount_cells(row_html):
    """The text of a table row's amount cells, without the currency symbol."""
    cells = re.findall(r'<td class="text-end[^"]*">([^<]*)</td>', row_html)
    return [cell.strip().removesuffix("€").strip() for cell in cells]


class ReportsViewTests(LoggedInWithAccountTestCase):
    def test_reports_page_loads(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertEqual(response.status_code, 200)

    def test_defaults_to_this_month(self):
        # R3 (Phase 13): This month - no subtotal - Last 3 periods - All accounts.
        response = self.client.get(reverse("finance:reports"))
        today = date.today()
        self.assertEqual(response.context["start"], today.replace(day=1))
        self.assertEqual(response.context["end"], today)
        self.assertEqual(response.context["average_periods"], 3)

    def test_savings_rate_is_shown_for_the_period(self):
        salary = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        rent = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        today = date.today()
        Transaction.objects.create(account=self.account, date=today, category=salary, amount=Decimal("1000.00"))
        Transaction.objects.create(account=self.account, date=today, category=rent, amount=Decimal("-250.00"))
        response = self.client.get(reverse("finance:reports"), {"range": "this_month"})
        self.assertEqual(response.context["savings_rate"], Decimal("75.0"))
        self.assertContains(response, "75.0%")

    def test_no_income_means_no_savings_rate_shown(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertIsNone(response.context["savings_rate"])

    def test_account_filter_applies_to_the_by_category_reports(self):
        other_account = Account.objects.create(name="Savings")
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        today = date.today()
        Transaction.objects.create(account=self.account, date=today, category=groceries, amount=Decimal("-30.00"))
        Transaction.objects.create(account=other_account, date=today, category=groceries, amount=Decimal("-999.00"))
        response = self.client.get(reverse("finance:reports"), {"range": "this_month", "account": self.account.pk})
        self.assertEqual(
            response.context["spending_by_category"],
            [{"category_id": groceries.pk, "name": "Groceries", "total": Decimal("30.00")}],
        )


class ReportsCustomRangeErrorTests(LoggedInWithAccountTestCase):
    def test_incomplete_custom_range_shows_an_error(self):
        response = self.client.get(reverse("finance:reports"), {"range": "custom", "start": "01/01/2026"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "custom range needs both a start and an end date")

    def test_a_custom_range_is_applied(self):
        response = self.client.get(
            reverse("finance:reports"), {"range": "custom", "start": "01/02/2026", "end": "31/03/2026"}
        )
        self.assertEqual((response.context["start"], response.context["end"]), (date(2026, 2, 1), date(2026, 3, 31)))

    def test_the_date_boxes_switch_the_range_to_custom_and_earliest_to_date_stays(self):
        # 8.14: stashy.js switches Range to "Custom range" when a date is typed. 8.13 removes
        # "Earliest to date" from the Transactions page only.
        response = self.client.get(reverse("finance:reports"))
        self.assertEqual(response.content.decode().count("data-range-date"), 2)
        self.assertContains(response, '<option value="earliest">Earliest to date</option>', html=True)


class DateRangeFilterFormTests(SimpleTestCase):
    def test_custom_range_requires_both_dates(self):
        form = DateRangeFilterForm({"range": "custom", "start": "01/01/2026"})
        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)

    def test_custom_range_start_must_not_be_after_end(self):
        form = DateRangeFilterForm({"range": "custom", "start": "31/03/2026", "end": "01/01/2026"})
        self.assertFalse(form.is_valid())

    def test_valid_custom_range_parses_european_dates(self):
        form = DateRangeFilterForm({"range": "custom", "start": "01/02/2026", "end": "31/03/2026"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["start"], date(2026, 2, 1))

    def test_preset_range_needs_no_dates(self):
        self.assertTrue(DateRangeFilterForm({"range": "this_month"}).is_valid())


class ReportsPageMarkupTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Category.objects.create(name="Supermarket", parent=groceries, category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=cls.account, date=date.today(), category=groceries, amount=Decimal("-10.00")
        )
        subcat = Category.objects.get(name="Supermarket")
        Transaction.objects.create(account=cls.account, date=date.today(), category=subcat, amount=Decimal("-15.00"))

    def test_collapsible_toggle_and_parent_child_classes_are_present(self):
        response = self.client.get(reverse("finance:reports"))
        body = response.content.decode()
        self.assertIn("report-toggle-icon", body)
        self.assertIn("category-main", body)
        self.assertIn("category-sub", body)
        self.assertIn("report-value-sub", body)

    def test_subcategory_rows_are_collapsed_by_default(self):
        response = self.client.get(reverse("finance:reports"))
        body = response.content.decode()
        self.assertIn('data-cat-parent="incexp-', body)
        # Every subcategory row (data-cat-parent) is hidden until its own chevron is clicked -
        # not just present somewhere hidden for an unrelated reason.
        start = body.index('data-cat-parent="incexp-')
        row_start = body.rindex("<tr", 0, start)
        row_end = body.index(">", start)
        self.assertIn("hidden", body[row_start:row_end])

    def test_export_is_hidden_until_a_report_is_expanded(self):
        # No Bootstrap display-utility class here - "d-inline-block" carries "!important" on
        # display, which silently overrides the "hidden" attribute's own display:none.
        response = self.client.get(reverse("finance:reports"))
        body = response.content.decode()
        self.assertIn('class="report-export" hidden', body)

    def test_net_replaces_total_in_the_income_expense_stat_row(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertContains(response, ">Net<")

    def test_filter_form_has_the_new_row_layout_fields(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertContains(response, "Average interval")
        self.assertContains(response, "Subtotal by")

    def test_show_hide_all_button_is_present(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertContains(response, "report-showhide-btn")


class DateRangeFormSubtotalFieldTests(SimpleTestCase):
    def test_subtotal_field_exists_with_expected_choices(self):
        form = DateRangeFilterForm()
        self.assertIn("subtotal", form.fields)
        values = [choice[0] for choice in form.fields["subtotal"].choices]
        self.assertEqual(values, ["", "week", "month", "quarter", "half_year", "year"])

    def test_interval_field_is_now_labeled_average_interval(self):
        form = DateRangeFilterForm()
        self.assertEqual(form.fields["interval"].label, "Average interval")

    def test_interval_field_choices_are_lookback_period_counts(self):
        form = DateRangeFilterForm()
        values = [choice[0] for choice in form.fields["interval"].choices]
        self.assertEqual(values, ["3", "6", "9", "12"])


class ReportsPageExpandButtonTests(LoggedInWithAccountTestCase):
    def test_expand_button_toggles_itself_not_a_separate_back_button(self):
        response = self.client.get(reverse("finance:reports"))
        body = response.content.decode()
        self.assertIn("data-expand-text", body)
        self.assertNotIn("reportsBackBtn", body)


class ReportsCollapseButtonLabelTests(LoggedInTestCase):
    def test_expand_button_relabels_to_collapse_not_contract(self):
        # The relabel happens client-side (reports.js), not in the server-rendered HTML, which
        # always starts on "Expand" - check the script itself rather than a page fetch.

        js = (Path(settings.BASE_DIR) / "finance" / "static" / "finance" / "js" / "reports.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("'Collapse'", js)
        self.assertNotIn("Contract", js)


class UncategorizedNoticeTests(LoggedInWithAccountTestCase):
    def test_counts_uncategorized_transactions_in_range(self):
        Transaction.objects.create(account=self.account, date=date(2026, 1, 5), amount=Decimal("-10.00"))
        Transaction.objects.create(account=self.account, date=date(2026, 1, 6), amount=Decimal("-10.00"))
        self.assertEqual(reporting.uncategorized_count(date(2026, 1, 1), date(2026, 1, 31)), 2)

    def test_reports_page_shows_a_notice_when_any_exist(self):
        Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("-10.00"))
        response = self.client.get(reverse("finance:reports"), {"range": "this_month"})
        self.assertContains(response, "uncategorized transaction")

    def test_no_notice_when_none_exist(self):
        response = self.client.get(reverse("finance:reports"), {"range": "this_month"})
        self.assertNotContains(response, "uncategorized transaction")


class ReportFixesAfterFirstLookTests(LoggedInWithAccountTestCase):
    """Bugs the user found on the real page after Phase 13 shipped (15/09/2026)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.bills = Category.objects.create(name="Bills", category_type=Category.CategoryType.EXPENSE)
        cls.water = Category.objects.create(
            name="Water", parent=cls.bills, category_type=Category.CategoryType.EXPENSE
        )

    def test_show_hide_all_is_hidden_until_a_report_is_expanded(self):
        # Like Export: it acts on the table, which isn't on screen until the card is expanded.
        # The button sits in a plain wrapper div - "hidden" on the button itself would lose to
        # Bootstrap's own .btn display rule.
        response = self.client.get(reverse("finance:reports"))
        body = response.content.decode()
        self.assertIn('class="report-showhide" hidden', body)
        self.assertEqual(body.count("report-showhide-btn"), body.count('class="report-showhide" hidden'))

    def test_subtotal_reaches_the_category_charts(self):
        # Without it every chart got data-subtotal="" and never switched to columns.
        response = self.client.get(reverse("finance:reports"), {"range": "this_year", "subtotal": "month"})
        self.assertContains(response, 'id="spendingByCategoryChart" data-subtotal="month"')
        self.assertContains(response, 'id="incomeByCategoryChart" data-subtotal="month"')

    def test_watched_categories_chosen_but_nothing_spent_is_not_no_categories_chosen(self):
        profile = Profile.for_user(self.user)
        profile.watched_expenses_categories.set([self.bills.pk])
        response = self.client.get(reverse("finance:reports"), {"range": "this_month"})
        self.assertNotContains(response, "No categories chosen")
        self.assertContains(response, 'id="watchedExpensesChart"')

    def test_vs_average_chart_rolls_subcategories_into_their_main_category(self):
        start, end = date(2026, 3, 1), date(2026, 3, 31)
        Transaction.objects.create(account=self.account, date=start, category=self.bills, amount=Decimal("-10.00"))
        Transaction.objects.create(account=self.account, date=start, category=self.water, amount=Decimal("-30.00"))
        rows = reporting.category_totals_vs_average(start, end, [], expense_only=True, top_level=True)
        self.assertEqual([(r["name"], r["current"]) for r in rows], [("Bills", Decimal("40.00"))])

    def test_watched_lines_follow_the_subtotal_granularity(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 1, 5), category=self.water, amount=Decimal("-20.00")
        )
        Transaction.objects.create(
            account=self.account, date=date(2026, 4, 5), category=self.water, amount=Decimal("-25.00")
        )
        series = reporting.watched_expenses_series(
            date(2026, 1, 1), date(2026, 6, 30), [self.water.pk], granularity="quarter"
        )
        self.assertEqual([p["period"] for p in series[0]["points"]], ["Q1 2026", "Q2 2026"])
        self.assertEqual([p["total"] for p in series[0]["points"]], [Decimal("20.00"), Decimal("25.00")])

    def test_net_worth_chart_follows_the_subtotal(self):
        Transaction.objects.create(account=self.account, date=date.today(), amount=Decimal("100.00"))
        response = self.client.get(reverse("finance:reports"), {"range": "this_year", "subtotal": "quarter"})
        labels = [point["label"] for point in response.context["net_worth_chart"]]
        self.assertEqual(labels, response.context["net_worth_table"]["periods"])
        self.assertTrue(all(label.startswith("Q") for label in labels))


class RefundsOnTheReportsPageTests(LoggedInWithAccountTestCase):
    """S50: the page's tables and charts agree about a refund."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.groceries = Category.objects.create(name="Groceries", category_type=EXPENSE)

    def add(self, amount):
        Transaction.objects.create(
            account=self.account, date=date.today(), category=self.groceries, amount=Decimal(amount)
        )

    def get_page(self):
        return self.client.get(reverse("finance:reports"), {"range": "this_month", "subtotal": "month"})

    def test_a_refund_shrinks_the_category_in_the_table_and_in_every_chart(self):
        self.add("-100.00")
        self.add("30.00")

        context = self.get_page().context

        self.assertEqual(context["spending_table"]["groups"][0]["rows"][0]["total"], Decimal("70.00"))
        self.assertEqual([r["total"] for r in context["spending_chart"]], [70.0])
        self.assertEqual(context["spending_stacked_chart"][0]["categories"], [{"name": "Groceries", "value": 70.0}])
        expense_series = context["income_expense_chart"]["expense"]
        self.assertEqual(expense_series[0]["categories"], [{"name": "Groceries", "value": 70.0}])

    def test_a_category_that_nets_to_a_refund_reads_negative_in_the_table_and_in_every_chart(self):
        self.add("-40.00")
        self.add("100.00")

        context = self.get_page().context

        self.assertEqual(context["spending_table"]["groups"][0]["rows"][0]["total"], Decimal("-60.00"))
        self.assertEqual([r["total"] for r in context["spending_chart"]], [-60.0])
        self.assertEqual(context["spending_stacked_chart"][0]["categories"], [{"name": "Groceries", "value": -60.0}])
        expense_series = context["income_expense_chart"]["expense"]
        self.assertEqual(expense_series[0]["categories"], [{"name": "Groceries", "value": -60.0}])

    def test_the_spending_table_shows_the_refund_category_with_its_minus_sign(self):
        self.add("-40.00")
        self.add("100.00")

        response = self.get_page()

        # A table cell in the spending colour, not the Net or Expense stat above the tables.
        self.assertContains(response, '<td class="text-end amount-negative">-60.00')
        for leaked in LEAKED_TEMPLATE_SYNTAX:
            self.assertNotContains(response, leaked)


class RefundEdgeCasesOnTheReportsPageTests(LoggedInWithAccountTestCase):
    """S50 on the page itself: table rows, the Watched expenses card, the Account filter and the
    vs-average tables, read from the context and from the rendered HTML."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.groceries = Category.objects.create(name="Groceries", category_type=EXPENSE)
        cls.organic = Category.objects.create(name="Organic", parent=cls.groceries, category_type=EXPENSE)
        cls.salary = Category.objects.create(name="Salary", category_type=INCOME)
        cls.savings = Account.objects.create(name="Savings")
        cls.month_start = timezone.localdate().replace(day=1)

    def add(self, amount, category=None, months_back=0, account=None):
        Transaction.objects.create(
            account=account or self.account,
            date=self.month_start - relativedelta(months=months_back),
            category=category or self.groceries,
            amount=Decimal(amount),
        )

    def get_page(self, **params):
        return self.client.get(reverse("finance:reports"), {"range": "this_month", **params})

    def test_a_subcategorys_refund_reads_negative_in_its_row_and_nets_inside_the_parent_row(self):
        self.add("-30.00")
        self.add("10.00", self.organic)

        response = self.get_page()
        page = response.content.decode()

        parent = _row_html(page, f'<tr data-cat-id="spend-{self.groceries.pk}">')
        child = _row_html(page, f'<tr data-cat-parent="spend-{self.groceries.pk}" hidden>')
        self.assertEqual(_amount_cells(parent), ["20.00"])
        self.assertEqual(_amount_cells(child), ["-10.00"])
        self.assertEqual(response.context["spending_table"]["totals"]["total"], Decimal("20.00"))
        self.assertEqual([r["total"] for r in response.context["spending_chart"]], [20.0])

    def test_the_watched_expenses_card_shrinks_a_partly_refunded_category(self):
        Profile.for_user(self.user).watched_expenses_categories.set([self.groceries])
        self.add("-100.00")
        self.add("30.00")

        response = self.get_page()

        self.assertEqual([r["total"] for r in response.context["watched_totals"]], [Decimal("70.00")])
        self.assertEqual([p["total"] for p in response.context["watched_chart"][0]["points"]], [70.0])
        row = _row_html(response.content.decode(), f'<tr data-cat-id="watched-{self.groceries.pk}">')
        self.assertEqual(_amount_cells(row), ["70.00"])

    def test_the_watched_expenses_card_reads_negative_when_the_refunds_beat_the_spending(self):
        Profile.for_user(self.user).watched_expenses_categories.set([self.groceries])
        self.add("-40.00")
        self.add("100.00")

        response = self.get_page(subtotal="month")

        self.assertEqual([r["total"] for r in response.context["watched_totals"]], [Decimal("-60.00")])
        self.assertEqual(response.context["watched_table"]["groups"][0]["rows"][0]["values"], [Decimal("-60.00")])
        self.assertEqual([p["total"] for p in response.context["watched_chart"][0]["points"]], [-60.0])
        row = _row_html(response.content.decode(), f'<tr data-cat-id="watched-{self.groceries.pk}">')
        self.assertEqual(_amount_cells(row), ["-60.00", "-60.00"])  # the month, then the row's total

    def test_the_account_filter_applies_to_the_refund_totals_on_the_page(self):
        self.add("-100.00")
        self.add("30.00")
        self.add("-50.00", account=self.savings)
        self.add("80.00", account=self.savings)

        for account, expected in [(self.account, "70.00"), (self.savings, "-30.00"), (None, "40.00")]:
            with self.subTest(account=account):
                context = self.get_page(**({"account": account.pk} if account else {})).context
                self.assertEqual(context["expense_total"], Decimal(expected))
                self.assertEqual(context["spending_table"]["totals"]["total"], Decimal(expected))
                self.assertEqual([r["total"] for r in context["spending_by_category"]], [Decimal(expected)])

    def test_a_month_with_only_a_refund_has_negative_expense_and_a_net_above_the_income(self):
        self.add("1000.00", self.salary)
        self.add("50.00")

        response = self.get_page()

        self.assertEqual(response.context["expense_total"], Decimal("-50.00"))
        self.assertEqual(response.context["net_total"], Decimal("1050.00"))
        self.assertEqual(response.context["savings_rate"], Decimal("105.0"))
        self.assertContains(response, '<div class="stat-value amount-negative">-50.00')
        self.assertContains(response, "105.0%")

    def test_the_spending_vs_average_table_nets_refunds_in_the_current_and_the_average_columns(self):
        for months_back in (1, 2, 3):
            self.add("-30.00", months_back=months_back)
        self.add("-40.00")
        self.add("100.00")

        response = self.client.get(reverse("finance:reports"))

        self.assertTrue(response.context["has_average"])
        row = _row_html(response.content.decode(), f'<tr data-cat-id="spendavg-{self.groceries.pk}">')
        self.assertEqual(_amount_cells(row), ["-60.00", "30.00", "-90.00"])
        self.assertIn("diff-good", row)  # spending fell by more than the average: a good change

    def test_the_income_and_expense_vs_average_table_ends_with_the_summary_rows(self):
        for months_back in (1, 2, 3):
            self.add("400.00", self.salary, months_back=months_back)
            self.add("-200.00", months_back=months_back)
        self.add("500.00", self.salary)
        self.add("50.00")

        response = self.client.get(reverse("finance:reports"))
        page = response.content.decode()

        self.assertContains(response, 'fw-semibold pt-3">Summary</td>')
        income = _row_html(page, '<tr data-cat-id="incexpavg-summary_income">')
        expense = _row_html(page, '<tr data-cat-id="incexpavg-summary_expense">')
        self.assertEqual(_amount_cells(income), ["500.00", "400.00", "100.00"])
        self.assertEqual(_amount_cells(expense), ["-50.00", "200.00", "-250.00"])
        self.assertIn("diff-good", expense)  # a falling expense is the good direction


class SavingsRateNumberFormatTests(LoggedInWithAccountTestCase):
    """S33: the savings rate follows the stash's number format, like every other number."""

    def setUp(self):
        super().setUp()
        profile = Profile.for_user(self.user)
        profile.number_format = "dot_comma"  # 10.000,50
        profile.save()
        salary = Category.objects.create(name="Salary", category_type=INCOME)
        rent = Category.objects.create(name="Rent", category_type=EXPENSE)
        this_month = date.today()
        last_month = this_month.replace(day=1) - timedelta(days=1)
        for on, category, amount in [
            (this_month, salary, "2000.00"),
            (this_month, rent, "-576.00"),
            (last_month, salary, "1000.00"),
            (last_month, rent, "-250.00"),
        ]:
            Transaction.objects.create(account=self.account, date=on, category=category, amount=Decimal(amount))

    def test_the_savings_rate_card_uses_the_stashs_decimal_mark(self):
        response = self.client.get(reverse("finance:reports"), {"range": "this_month"})

        self.assertContains(response, "71,2%")
        self.assertNotContains(response, "71.2%")

    def test_the_income_and_expense_vs_average_stats_use_it_too(self):
        response = self.client.get(reverse("finance:reports"), {"range": "this_month"})

        stat = next(s for s in response.context["vs_average_stats"] if s["label"] == "Savings rate")

        self.assertEqual(stat["current"], "71,2%")
        self.assertEqual(stat["average"], "75,0%")
        self.assertEqual(stat["diff_text"], "▼ 3,8%")


class SavingsRateExtremesInEveryNumberFormatTests(LoggedInWithAccountTestCase):
    """S33: a negative savings rate, one over 100 (refunds make that possible, S50) and one in the
    thousands read right in every number format - the minus sign, the decimal mark and the
    thousands separator all follow the stash's style."""

    # number format -> the rate as shown for -25.0, 105.0 and 1000.0
    EXPECTED = {
        "space_dot": ("-25.0%", "105.0%", f"1{NBSP}000.0%"),
        "comma_dot": ("-25.0%", "105.0%", "1,000.0%"),
        "dot_comma": ("-25,0%", "105,0%", "1.000,0%"),
        "space_comma": ("-25,0%", "105,0%", f"1{NBSP}000,0%"),
    }

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.salary = Category.objects.create(name="Salary", category_type=INCOME)
        cls.rent = Category.objects.create(name="Rent", category_type=EXPENSE)

    def add(self, amount, category, months_back=0):
        when = timezone.localdate().replace(day=1) - relativedelta(months=months_back)
        Transaction.objects.create(account=self.account, date=when, category=category, amount=Decimal(amount))

    def use_number_format(self, number_format):
        profile = Profile.for_user(self.user)
        profile.number_format = number_format
        profile.save()

    def savings_rate_card(self):
        page = self.client.get(reverse("finance:reports"), {"range": "this_month"}).content.decode()
        return re.search(r'Savings rate</div>\s*<div class="stat-value">([^<]*)</div>', page).group(1)

    def check_every_format(self, which):
        for number_format, shown in self.EXPECTED.items():
            with self.subTest(number_format=number_format):
                self.use_number_format(number_format)
                self.assertEqual(self.savings_rate_card(), shown[which])

    def test_a_negative_rate_keeps_its_minus_sign(self):
        self.add("1000.00", self.salary)
        self.add("-1250.00", self.rent)

        self.check_every_format(0)

    def test_a_rate_over_100_when_a_refund_is_the_only_spending(self):
        self.add("1000.00", self.salary)
        self.add("50.00", self.rent)

        self.check_every_format(1)

    def test_a_rate_in_the_thousands_gets_the_thousands_separator(self):
        self.add("10.00", self.salary)
        self.add("90.00", self.rent)

        self.check_every_format(2)

    def test_the_vs_average_stats_follow_the_style_for_a_negative_rate(self):
        """This month saves -25.0%, last month 75.0%: the difference is the plain 100.0, no sign
        (the arrow says which way it went)."""
        self.add("1000.00", self.salary)
        self.add("-1250.00", self.rent)
        self.add("1000.00", self.salary, months_back=1)
        self.add("-250.00", self.rent, months_back=1)
        for number_format, mark in [("comma_dot", "."), ("dot_comma", ",")]:
            with self.subTest(number_format=number_format):
                self.use_number_format(number_format)

                response = self.client.get(reverse("finance:reports"), {"range": "this_month"})
                stat = next(s for s in response.context["vs_average_stats"] if s["label"] == "Savings rate")

                self.assertEqual(stat["current"], f"-25{mark}0%")
                self.assertEqual(stat["average"], f"75{mark}0%")
                self.assertEqual(stat["diff_text"], f"▼ 100{mark}0%")
                self.assertFalse(stat["good"])

    def test_without_income_in_the_earlier_periods_the_average_rate_is_a_dash(self):
        self.use_number_format("dot_comma")
        self.add("1000.00", self.salary)
        self.add("-250.00", self.rent)
        self.add("-100.00", self.rent, months_back=1)

        response = self.client.get(reverse("finance:reports"), {"range": "this_month"})
        stat = next(s for s in response.context["vs_average_stats"] if s["label"] == "Savings rate")

        self.assertEqual((stat["current"], stat["average"], stat["diff_text"]), ("75,0%", "—", ""))
        self.assertIsNone(stat["good"])


class AverageDisplayTests(LoggedInWithAccountTestCase):
    def test_no_average_period_line(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertNotContains(response, "vs. average of last")

    def test_nothing_earlier_shows_the_message_instead_of_the_report(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=self.account, date=date.today(), category=groceries, amount=Decimal("-10.00")
        )
        response = self.client.get(reverse("finance:reports"), {"range": "earliest"})
        self.assertFalse(response.context["has_average"])
        self.assertContains(response, "No earlier data to compare with.")
        self.assertNotContains(response, 'id="spendingVsAverageChart"')

    def test_one_earlier_period_is_not_an_average_either(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        # Data starting inside last month leaves exactly one whole earlier month to compare with.
        last_month_end = date.today().replace(day=1) - timedelta(days=1)
        Transaction.objects.create(
            account=self.account, date=last_month_end, category=groceries, amount=Decimal("-40.00")
        )
        Transaction.objects.create(
            account=self.account, date=date.today(), category=groceries, amount=Decimal("-10.00")
        )
        response = self.client.get(reverse("finance:reports"))
        self.assertEqual(response.context["average_periods"], 1)
        self.assertFalse(response.context["has_average"])
        self.assertContains(response, "No earlier data to compare with.")

    def test_two_earlier_periods_do_make_an_average(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        two_months_back = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1) - timedelta(days=1)
        Transaction.objects.create(
            account=self.account, date=two_months_back, category=groceries, amount=Decimal("-40.00")
        )
        Transaction.objects.create(
            account=self.account, date=date.today(), category=groceries, amount=Decimal("-10.00")
        )
        response = self.client.get(reverse("finance:reports"))
        self.assertTrue(response.context["has_average"])
        self.assertContains(response, 'id="spendingVsAverageChart"')

    def test_a_category_without_earlier_activity_averages_zero(self):
        # Plan 04 R6 (03/10/2026) replaced "no earlier activity = no average": once earlier
        # periods exist, a category that had nothing in them averages 0.
        start, end = date(2026, 3, 1), date(2026, 3, 31)
        seen_before = Category.objects.create(name="Rent", category_type=Category.CategoryType.EXPENSE)
        brand_new = Category.objects.create(name="Gifts", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=self.account, date=date(2026, 2, 10), category=seen_before, amount=Decimal("-60.00")
        )
        Transaction.objects.create(account=self.account, date=start, category=seen_before, amount=Decimal("-50.00"))
        Transaction.objects.create(account=self.account, date=start, category=brand_new, amount=Decimal("-15.00"))
        periods = reporting.vs_average_periods(start, end, 2, range_key="this_month")
        rows = {r["name"]: r for r in reporting.category_totals_vs_average(start, end, periods, expense_only=True)}
        self.assertEqual(rows["Rent"]["average"], Decimal("30.00"))  # 60.00 over the 2 earlier months
        self.assertEqual(rows["Gifts"]["average"], Decimal("0.00"))
        self.assertTrue(rows["Gifts"]["no_earlier_activity"])
        self.assertFalse(rows["Rent"]["no_earlier_activity"])

    def test_top_5_color_data_is_gone(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertNotContains(response, "top5-data")


class InvalidFilterTests(LoggedInWithAccountTestCase):
    def test_invalid_custom_range_shows_the_actual_defaults_not_the_rejected_values(self):
        response = self.client.get(reverse("finance:reports"), {"range": "custom"})
        self.assertContains(response, "Your filters weren")
        # The rejected "custom" choice isn't what's shown as selected - the real default is.
        self.assertNotContains(response, '<option value="custom" selected')
        self.assertContains(response, '<option value="this_month" selected')


class WatchedExpensesSettingsTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.profile = Profile.for_user(cls.user)
        cls.electricity = Category.objects.create(name="Electricity", category_type=Category.CategoryType.EXPENSE)

    def test_default_title_is_bills_and_utilities(self):
        self.assertEqual(self.profile.watched_expenses_title, "Bills & Utilities")

    def test_form_only_offers_expense_categories(self):
        income = Category.objects.create(name="Salary", category_type=Category.CategoryType.INCOME)
        form = WatchedExpensesForm(instance=self.profile)
        offered = set(form.fields["watched_expenses_categories"].queryset)
        self.assertIn(self.electricity, offered)
        self.assertNotIn(income, offered)

    def test_saving_from_settings_updates_the_report(self):
        response = self.client.post(
            reverse("finance:save_watched_expenses"),
            {
                "watched_expenses_title": "My bills",
                "watched_expenses_categories": [self.electricity.pk],
            },
        )
        self.assertRedirects(response, reverse("finance:settings"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.watched_expenses_title, "My bills")
        self.assertEqual(list(self.profile.watched_expenses_categories.all()), [self.electricity])

        Transaction.objects.create(
            account=self.account, date=date.today(), category=self.electricity, amount=Decimal("-40.00")
        )
        response = self.client.get(reverse("finance:reports"), {"range": "this_month"})
        self.assertContains(response, "My bills")

    def test_no_categories_chosen_shows_the_go_to_settings_message(self):
        response = self.client.get(reverse("finance:reports"))
        self.assertContains(response, "No categories chosen")

    def test_watched_expenses_totals_none_when_nothing_chosen(self):
        self.assertIsNone(reporting.watched_expenses_totals(date(2026, 1, 1), date(2026, 1, 31), []))


class WatchedExpensesMigrationPrefillTests(StashTestCase):
    """The Phase 13 migration pre-fills existing "Bills & Utilities" categories into every
    Profile's Watched expenses, so nothing looks different on day one - re-verified here at the
    model level since the migration itself only runs once, at upgrade time."""

    def test_a_profile_can_watch_a_pre_existing_bills_category(self):
        user = get_user_model().objects.create_user("someone", password="not-a-real-password-123")
        profile = Profile.for_user(user)
        top = Category.objects.create(name="Bills & Utilities", category_type=Category.CategoryType.EXPENSE)
        water = Category.objects.create(name="Water", parent=top, category_type=Category.CategoryType.EXPENSE)
        profile.watched_expenses_categories.set([top.pk, water.pk])
        self.assertEqual(reporting.watched_expenses_category_ids(profile), [top.pk, water.pk])


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


class WatchedExpensesPickerTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.profile = Profile.for_user(get_user_model().objects.create_user("alex"))
        cls.food = Category.objects.create(name="Food", category_type=EXPENSE)
        cls.groceries = Category.objects.create(name="Groceries", category_type=EXPENSE, parent=cls.food)

    def test_the_button_label_follows_what_is_chosen(self):
        self.assertEqual(watched_expenses_picker_context(self.profile)["watched_button_label"], "No categories chosen")
        self.profile.watched_expenses_categories.set([self.groceries])
        context = watched_expenses_picker_context(self.profile)
        self.assertEqual(context["watched_button_label"], self.groceries.full_name)
        self.assertEqual(context["watched_selected_values"], {str(self.groceries.pk)})
        self.profile.watched_expenses_categories.add(self.food)
        self.assertEqual(
            watched_expenses_picker_context(self.profile)["watched_button_label"], "2 categories selected"
        )


class NoEarlierActivityAveragesZeroTests(LoggedInWithAccountTestCase):
    """Plan 04 R6: with earlier periods to compare with, a category that had nothing in them
    averages 0 - the page shows "—" for it but fills in the difference and draws the dot."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.month_start = timezone.localdate().replace(day=1)
        cls.groceries = cls._category("Groceries")
        cls.accommodation = cls._category("Accommodation")

    @classmethod
    def _category(cls, name, parent=None):
        return Category.objects.create(name=name, category_type=Category.CategoryType.EXPENSE, parent=parent)

    def _spend(self, category, amount, months_back=0):
        when = self.month_start - relativedelta(months=months_back)
        Transaction.objects.create(account=self.account, date=when, category=category, amount=Decimal(amount))

    def _three_earlier_months(self):
        for months_back in (1, 2, 3):
            self._spend(self.groceries, "-30.00", months_back)

    def _csv(self, slug):
        response = self.client.get(reverse("finance:export_report", args=[slug]), {"format": "csv"})
        return {row[0]: row[1:] for row in csv.reader(io.StringIO(response.content.decode("utf-8-sig")))}

    def test_the_export_has_a_zero_average_and_the_real_difference(self):
        self._three_earlier_months()
        self._spend(self.accommodation, "-583.50")
        self.assertEqual(self._csv("spending-vs-average")["Accommodation"], ["583.50", "0.00", "583.50"])
        # Expenses are negative in the combined report, so the same gap is negative there.
        self.assertEqual(self._csv("income-expense-vs-average")["Accommodation"], ["-583.50", "0.00", "-583.50"])

    def test_the_page_shows_a_dash_for_the_average_but_the_difference_and_a_dot(self):
        self._three_earlier_months()
        self._spend(self.accommodation, "-583.50")
        response = self.client.get(reverse("finance:reports"))
        self.assertTrue(response.context["has_average"])
        chart = {r["name"]: r for r in response.context["spending_vs_average_chart"]}
        self.assertEqual(chart["Accommodation"]["average"], 0.0)  # a dot at 0, not a gap

        # The Spending vs average table's own row, read from the page's real HTML.
        page = response.content.decode()
        row_html = re.search(rf'<tr data-cat-id="spendavg-{self.accommodation.pk}">(.*?)</tr>', page, re.S).group(1)
        cells = [cell.strip() for cell in re.findall(r'<td class="text-end[^"]*">([^<]*)</td>', row_html)]
        self.assertIn("583.50", cells[0])  # current
        self.assertEqual(cells[1], "—")  # average
        self.assertIn("583.50", cells[2])  # difference

    def test_a_category_with_some_earlier_activity_averages_over_every_period(self):
        self._three_earlier_months()
        self._spend(self.accommodation, "-600.00", months_back=2)
        self._spend(self.accommodation, "-100.00")
        self.assertEqual(self._csv("spending-vs-average")["Accommodation"], ["100.00", "200.00", "-100.00"])

    def test_a_main_category_whose_subcategory_is_brand_new_gets_a_dash_too(self):
        # Folds in R2: the parent and its new subcategory both average 0 and show "—".
        food = self._category("Food")
        bakery = self._category("Bakery", parent=food)
        self._three_earlier_months()
        self._spend(bakery, "-12.00")
        periods = reporting.vs_average_periods(self.month_start, timezone.localdate(), 3, range_key="this_month")
        table = reporting.category_vs_average_report_rows(
            self.month_start, timezone.localdate(), periods, expense_only=True
        )
        rows = {r["name"]: r for group in table["groups"] for r in group["rows"]}
        for name in ("Food", "Bakery"):
            self.assertEqual(rows[name]["average"], Decimal("0.00"))
            self.assertEqual(rows[name]["difference"], Decimal("12.00"))
            self.assertTrue(rows[name]["no_earlier_activity"])
        self.assertFalse(rows["Groceries"]["no_earlier_activity"])

    def test_a_main_category_counts_its_subcategories_earlier_activity(self):
        food = self._category("Food")
        bakery = self._category("Bakery", parent=food)
        self._spend(bakery, "-30.00", months_back=1)
        self._spend(self.groceries, "-30.00", months_back=3)
        self._spend(bakery, "-12.00")
        periods = reporting.vs_average_periods(self.month_start, timezone.localdate(), 3, range_key="this_month")
        table = reporting.category_vs_average_report_rows(
            self.month_start, timezone.localdate(), periods, expense_only=True
        )
        rows = {r["name"]: r for group in table["groups"] for r in group["rows"]}
        self.assertFalse(rows["Food"]["no_earlier_activity"])
        self.assertEqual(rows["Food"]["average"], Decimal("10.00"))

    def test_fewer_than_two_earlier_periods_still_means_no_earlier_data(self):
        self._spend(self.groceries, "-30.00", months_back=1)
        self._spend(self.accommodation, "-583.50")
        response = self.client.get(reverse("finance:reports"))
        self.assertFalse(response.context["has_average"])
        self.assertContains(response, "No earlier data to compare with.")
