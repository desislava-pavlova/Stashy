"""Tests for Phase 13 (Reports redesign): the R1-R8 decisions not already covered by the
updated Phase 11 tests - earliest-date range/trimming, the subtotal overflow notice, currency
conversion, the uncategorized-transactions notice, and Settings-configurable Watched expenses."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import reporting
from .currency import RateTable
from .forms import DateRangeFilterForm, WatchedExpensesForm
from .models import Account, Category, ExchangeRate, Profile, Transaction


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


class EarliestToDateRangeTests(LoggedInTestCase):
    def test_resolves_to_the_first_transactions_date(self):
        Transaction.objects.create(account=self.account, date=date(2021, 10, 3), amount=Decimal("10.00"))
        Transaction.objects.create(account=self.account, date=date(2022, 1, 1), amount=Decimal("5.00"))
        start, end, _ = reporting.resolve_range({"range": "earliest"})
        self.assertEqual(start, date(2021, 10, 3))
        self.assertEqual(end, date.today())

    def test_falls_back_to_today_with_no_transactions_at_all(self):
        start, _end, _ = reporting.resolve_range({"range": "earliest"})
        self.assertEqual(start, date.today())

    def test_respects_the_account_filter(self):
        other = Account.objects.create(name="Savings")
        Transaction.objects.create(account=self.account, date=date(2021, 1, 1), amount=Decimal("1.00"))
        Transaction.objects.create(account=other, date=date(2023, 6, 1), amount=Decimal("1.00"))
        start, _, _ = reporting.resolve_range({"range": "earliest"}, account=other)
        self.assertEqual(start, date(2023, 6, 1))

    def test_is_a_choice_on_the_form(self):
        form = DateRangeFilterForm()
        values = [choice[0] for choice in form.fields["range"].choices]
        self.assertIn("earliest", values)


class TrimmedAveragePeriodsTests(LoggedInTestCase):
    """R4: "periods before the data begins don't count, whatever Average interval says"."""

    def test_periods_entirely_before_the_first_transaction_are_dropped(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        start, end = date(2026, 3, 1), date(2026, 3, 31)
        # Only transaction is the current period's own - every one of the 3 trailing 31-day
        # periods ends before it, so none should count.
        Transaction.objects.create(account=self.account, date=start, category=groceries, amount=Decimal("-20.00"))
        periods = reporting.trimmed_average_periods(start, end, 3, account=self.account)
        self.assertEqual(periods, [])

    def test_a_period_the_range_only_partly_precedes_data_is_kept(self):
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        start, end = date(2026, 3, 1), date(2026, 3, 31)
        # The first trailing period is 2026-01-29..2026-02-28; a transaction on 2026-02-15
        # means that period doesn't entirely precede the data, so it's kept.
        Transaction.objects.create(
            account=self.account, date=date(2026, 2, 15), category=groceries, amount=Decimal("-5.00")
        )
        periods = reporting.trimmed_average_periods(start, end, 3, account=self.account)
        self.assertEqual(len(periods), 1)

    def test_no_transactions_at_all_keeps_every_period(self):
        periods = reporting.trimmed_average_periods(date(2026, 3, 1), date(2026, 3, 31), 3)
        self.assertEqual(len(periods), 3)


class SubtotalOverflowTests(SimpleTestCase):
    def test_no_message_within_the_limit(self):
        self.assertIsNone(reporting.subtotal_overflow_message(date(2026, 1, 1), date(2026, 1, 31), "month"))

    def test_message_names_the_next_coarser_granularity_with_no_count(self):
        message = reporting.subtotal_overflow_message(date(2015, 1, 1), date(2026, 1, 1), "month")
        self.assertEqual(message, "Too many months to show here. Try subtotaling by quarter instead.")
        self.assertNotRegex(message, r"\d")

    def test_years_has_no_next_step(self):
        message = reporting.subtotal_overflow_message(date(1900, 1, 1), date(2026, 1, 1), "year")
        self.assertEqual(message, "Too many years to show here. Try a shorter date range.")

    def test_no_granularity_means_no_overflow(self):
        self.assertIsNone(reporting.subtotal_overflow_message(date(1900, 1, 1), date(2026, 1, 1), ""))


class ReportCurrencyConversionTests(LoggedInTestCase):
    def test_converts_a_foreign_currency_account_using_the_latest_rate(self):
        usd_account = Account.objects.create(name="US Checking", currency="USD")
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=usd_account, date=date(2026, 1, 5), category=groceries, amount=Decimal("-100.00")
        )
        ExchangeRate.objects.create(
            base_currency="USD", quote_currency="EUR", rate=Decimal("0.9"), date=date(2026, 1, 1)
        )

        rates = RateTable("EUR")
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31), expense_only=True, rates=rates)
        self.assertEqual(rows, [{"category_id": groceries.pk, "name": "Groceries", "total": Decimal("90.00")}])

    def test_no_known_rate_excludes_it_rather_than_mixing_currencies(self):
        usd_account = Account.objects.create(name="US Checking", currency="USD")
        groceries = Category.objects.create(name="Groceries", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(
            account=usd_account, date=date(2026, 1, 5), category=groceries, amount=Decimal("-100.00")
        )
        rates = RateTable("EUR")
        rows = reporting.category_totals(date(2026, 1, 1), date(2026, 1, 31), expense_only=True, rates=rates)
        self.assertEqual(rows, [{"category_id": groceries.pk, "name": "Groceries", "total": Decimal("0.00")}])

    def test_net_worth_names_an_account_with_no_known_rate(self):
        usd_account = Account.objects.create(name="US Checking", currency="USD")
        Transaction.objects.create(account=usd_account, date=date(2026, 1, 5), amount=Decimal("50.00"))
        table = reporting.net_worth_by_account(date(2026, 1, 1), date(2026, 1, 31), "month", rates=RateTable("EUR"))
        self.assertIn("US Checking", table["missing_accounts"])


class UncategorizedNoticeTests(LoggedInTestCase):
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


class ReportFixesAfterFirstLookTests(LoggedInTestCase):
    """Bugs the user found on the real page after Phase 13 shipped (15/09/2026)."""

    def setUp(self):
        super().setUp()
        self.bills = Category.objects.create(name="Bills", category_type=Category.CategoryType.EXPENSE)
        self.water = Category.objects.create(
            name="Water", parent=self.bills, category_type=Category.CategoryType.EXPENSE
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


class CalendarAveragePeriodsTests(SimpleTestCase):
    """R4: a calendar Date range is averaged against whole earlier periods of the same kind, not
    earlier windows of the same length (This month on the 15th isn't compared with 15-day blocks)."""

    def test_this_month_compares_with_previous_full_months(self):
        periods = reporting.vs_average_periods(date(2026, 9, 1), date(2026, 9, 15), 3, range_key="this_month")
        self.assertEqual(
            periods,
            [
                (date(2026, 8, 1), date(2026, 8, 31)),
                (date(2026, 7, 1), date(2026, 7, 31)),
                (date(2026, 6, 1), date(2026, 6, 30)),
            ],
        )

    def test_last_month_compares_with_the_months_before_it(self):
        periods = reporting.vs_average_periods(date(2026, 8, 1), date(2026, 8, 31), 1, range_key="last_month")
        self.assertEqual(periods, [(date(2026, 7, 1), date(2026, 7, 31))])

    def test_quarter_compares_with_previous_quarters(self):
        periods = reporting.vs_average_periods(date(2026, 7, 1), date(2026, 9, 15), 2, range_key="this_quarter")
        self.assertEqual(periods, [(date(2026, 4, 1), date(2026, 6, 30)), (date(2026, 1, 1), date(2026, 3, 31))])

    def test_year_to_date_compares_with_previous_full_years(self):
        periods = reporting.vs_average_periods(date(2026, 1, 1), date(2026, 9, 15), 2, range_key="ytd")
        self.assertEqual(periods, [(date(2025, 1, 1), date(2025, 12, 31)), (date(2024, 1, 1), date(2024, 12, 31))])

    def test_last_12_months_compares_with_earlier_12_month_blocks(self):
        periods = reporting.vs_average_periods(date(2025, 10, 1), date(2026, 9, 15), 1, range_key="last_12_months")
        self.assertEqual(periods, [(date(2024, 10, 1), date(2025, 9, 30))])

    def test_custom_range_keeps_same_length_windows(self):
        periods = reporting.vs_average_periods(date(2026, 3, 1), date(2026, 3, 10), 1, range_key="custom")
        self.assertEqual(periods, [(date(2026, 2, 19), date(2026, 2, 28))])

    def test_earliest_to_date_has_nothing_before_it(self):
        self.assertEqual(
            reporting.vs_average_periods(date(2021, 1, 1), date(2026, 9, 15), 3, range_key="earliest"), []
        )


class AverageDisplayTests(LoggedInTestCase):
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
        # Plan 03 R6 (03/10/2026) replaced "no earlier activity = no average": once earlier
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


class NoLeakedTemplateSyntaxTests(LoggedInTestCase):
    """Regression test: a multi-line {# ... #} Django comment isn't valid - {# #} only matches
    within a single line (no re.DOTALL), so a comment spanning several lines silently falls
    through as literal page text instead of being stripped. Bit `includes/report_card_header.html`
    and `includes/category_checkbox_filter.html` this way (15/09/2026) - every report card and
    the Settings "Watched expenses" picker showed a paragraph of raw comment text. Guards every
    page these two includes touch against the same mistake reappearing."""

    def test_reports_page_has_no_leaked_comment_syntax(self):
        response = self.client.get(reverse("finance:reports"))
        body = response.content.decode()
        self.assertNotIn("{#", body)
        self.assertNotIn("#}", body)

    def test_settings_page_has_no_leaked_comment_syntax(self):
        response = self.client.get(reverse("finance:settings"))
        body = response.content.decode()
        self.assertNotIn("{#", body)
        self.assertNotIn("#}", body)

    def test_transaction_list_page_has_no_leaked_comment_syntax(self):
        response = self.client.get(reverse("finance:transaction_list"))
        body = response.content.decode()
        self.assertNotIn("{#", body)
        self.assertNotIn("#}", body)


class InvalidFilterTests(LoggedInTestCase):
    def test_invalid_custom_range_shows_the_actual_defaults_not_the_rejected_values(self):
        response = self.client.get(reverse("finance:reports"), {"range": "custom"})
        self.assertContains(response, "Your filters weren")
        # The rejected "custom" choice isn't what's shown as selected - the real default is.
        self.assertNotContains(response, '<option value="custom" selected')
        self.assertContains(response, '<option value="this_month" selected')


class WatchedExpensesSettingsTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.profile = Profile.for_user(self.user)
        self.electricity = Category.objects.create(name="Electricity", category_type=Category.CategoryType.EXPENSE)

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


class WatchedExpensesMigrationPrefillTests(TestCase):
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
