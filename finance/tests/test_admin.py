"""Rules the app's forms enforce also hold in the database (constraints,
model validation) and in the Django admin: transfer categories, reminder
amounts and intervals, and an account's currency once it has
transactions. Also what the admin answers to a request none of its pages
sends: a number in a list's address that the database or the calendar
can't hold, a lookup no page sends (a regular expression, a range), an
autocomplete value or a bulk-action id that can't be a key, and what
goes into the log about them; and the number of queries a list makes."""

import re
import warnings
from datetime import date
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.contrib import admin
from django.contrib.admin import helpers
from django.contrib.admin.options import ActionLocation
from django.core.exceptions import ValidationError
from django.core.paginator import UnorderedObjectListWarning
from django.db import IntegrityError, connections, models, transaction
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from stashy.admin_mixins import HandMadeRequestMixin, key_fits, names_for_log

from ..admin import TransactionAdmin
from ..forms import ReminderForm
from ..models import Account, Category, Payee, Reminder, Transaction
from .base import LoggedInAsSuperuserTestCase, StashTestCase, create_accounts_with_a_transaction


# More digits than any database id has: an OverflowError wherever it is
# looked up.
HUGE_NUMBER = "9" * 30

EXPENSE = Category.CategoryType.EXPENSE


class AdminRuleTests(StashTestCase):
    """2.8 (F54): rules the forms enforce now hold for the admin and the
    database too."""

    def test_a_transfer_category_needs_an_account(self):
        with self.assertRaises(IntegrityError), transaction.atomic(using=settings.TEST_STASH):
            Category.objects.create(
                name="Moving money", category_type=Category.CategoryType.TRANSFER
            )
        with self.assertRaises(ValidationError) as caught:
            Category(
                name="Moving money", category_type=Category.CategoryType.TRANSFER
            ).full_clean()
        self.assertIn("category_type", caught.exception.message_dict)

    def test_an_accounts_transfer_category_stays_a_transfer(self):
        category = Account.objects.create(name="Cash").transfer_category
        category.category_type = EXPENSE
        with self.assertRaises(IntegrityError), transaction.atomic(using=settings.TEST_STASH):
            category.save()

    def test_a_reminder_amount_cant_be_negative(self):
        account = Account.objects.create(name="Checking")
        with self.assertRaises(IntegrityError), transaction.atomic(using=settings.TEST_STASH):
            Reminder.objects.create(
                account=account, amount=Decimal("-5"), next_due_date=date(2026, 10, 1)
            )
        form = ReminderForm(
            data={
                "reminder_type": EXPENSE,
                "account": account.pk,
                "amount": "-5",
                "frequency": "one_time",
                "next_due_date": "01/10/2026",
                "no_end_date": True,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("amount", form.errors)

    def test_a_reminder_interval_is_at_least_one(self):
        account = Account.objects.create(name="Checking")
        with self.assertRaises(IntegrityError), transaction.atomic(using=settings.TEST_STASH):
            Reminder.objects.create(account=account, interval=0, next_due_date=date(2026, 10, 1))

    def test_currency_cant_change_once_transactions_exist(self):
        account = Account.objects.create(name="Checking", currency="EUR")
        Transaction.objects.create(account=account, date=date(2026, 9, 1), amount=Decimal("10"))
        account.currency = "USD"
        with self.assertRaises(ValidationError) as caught:
            account.full_clean()
        self.assertIn("currency", caught.exception.message_dict)

    def test_currency_can_change_while_the_account_is_empty(self):
        account = Account.objects.create(name="Checking", currency="EUR")
        account.currency = "USD"
        account.full_clean()

    def test_the_admin_shows_currency_read_only_once_transactions_exist(self):
        account = Account.objects.create(name="Checking")
        model_admin = admin.site._registry[Account]
        request = RequestFactory().get("/")
        self.assertNotIn("currency", model_admin.get_readonly_fields(request, account))
        Transaction.objects.create(account=account, date=date(2026, 9, 1), amount=Decimal("10"))
        self.assertIn("currency", model_admin.get_readonly_fields(request, account))


class AdminHugeNumberTests(LoggedInAsSuperuserTestCase):
    """S112: a value the database or the calendar can't hold, typed into
    an admin list's address, used to be a 500 page. Django's own answer
    to a filter it can't use is a redirect to the plain list."""

    def assert_redirected_to_the_plain_list(self, list_name, query):
        url = reverse(list_name)
        response = self.client.get(url, query)
        self.assertRedirects(response, f"{url}?e=1")

    def test_a_huge_number_in_a_filter_goes_back_to_the_plain_list(self):
        for query in (
            {"id__in": HUGE_NUMBER},
            {"date__year": HUGE_NUMBER},
            {"id__in": f"1,{HUGE_NUMBER}"},
        ):
            with self.subTest(query=query):
                self.assert_redirected_to_the_plain_list(
                    "admin:finance_transaction_changelist", query
                )

    def test_the_audit_log_list_is_covered_too(self):
        for query in ({"id__in": HUGE_NUMBER}, {"timestamp__year": HUGE_NUMBER}):
            with self.subTest(query=query):
                self.assert_redirected_to_the_plain_list(
                    "admin:audit_auditlogentry_changelist", query
                )

    def test_an_ordinary_filter_still_works(self):
        account = Account.objects.create(name="Checking")
        Transaction.objects.create(account=account, date=date(2026, 9, 1), amount=Decimal("10"))
        response = self.client.get(
            reverse("admin:finance_transaction_changelist"), {"date__year": "2026"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["cl"].result_list), 1)

    def test_every_admin_has_the_guard_ahead_of_djangos_own_list_code(self):
        # Behind ModelAdmin in the bases, the mixin would be an instance
        # check that passes and methods that never run: ModelAdmin's
        # own (the list, the lookups, the dropdowns, the bulk actions)
        # would answer first.
        for model, model_admin in admin.site._registry.items():
            with self.subTest(model=model._meta.label):
                bases = type(model_admin).__mro__
                self.assertIn(HandMadeRequestMixin, bases)
                self.assertLess(bases.index(HandMadeRequestMixin), bases.index(admin.ModelAdmin))

    def test_a_refusal_is_logged_with_the_parameter_names_and_not_what_was_typed(self):
        with self.assertLogs("stashy.admin_mixins", "WARNING") as logged:
            self.client.get(
                reverse("admin:finance_transaction_changelist"), {"id__in": HUGE_NUMBER}
            )
        message = logged.output[0]
        self.assertIn("id__in", message)
        self.assertNotIn(HUGE_NUMBER, message)

    def test_a_parameter_name_with_a_line_break_in_it_doesnt_split_the_log_line(self):
        """The audit log's JSON column is the one place where a name
        after `values__` can be anything, so it is the one that could
        carry a line break into the log."""
        query = {"id__in": HUGE_NUMBER, f"values__a{chr(10)}FAKE": "1"}
        with self.assertLogs("stashy.admin_mixins", "WARNING") as logged:
            self.client.get(reverse("admin:audit_auditlogentry_changelist"), query)
        self.assertEqual(len(logged.output), 1)
        self.assertNotIn(chr(10), logged.output[0])
        self.assertIn("FAKE", logged.output[0])

    def test_a_year_or_an_iso_year_the_calendar_cant_hold_goes_back_to_the_plain_list(self):
        """Not a number SQLite refuses, but the same family: Django
        finds the other end of a year by adding one to it, and the
        iso-year lookups check their value themselves. Both raise a
        ValueError, which the mixin turns into the same redirect."""
        for list_name, field in (
            ("admin:finance_transaction_changelist", "date"),
            ("admin:audit_auditlogentry_changelist", "timestamp"),
        ):
            for query in (
                {f"{field}__year": "9999"},
                {f"{field}__iso_year": "0"},
                {f"{field}__iso_year": "10000"},
                {f"{field}__iso_year__gt": HUGE_NUMBER},
            ):
                with self.subTest(list=list_name, query=query):
                    self.assert_redirected_to_the_plain_list(list_name, query)

    def test_a_date_past_the_end_of_the_calendar_goes_back_to_the_plain_list(self):
        """Not a number SQLite refuses, but the same family. To find the
        other end of a date range, Django adds a day or a month to the
        date typed, and the answer can fall past the year 9999: that is
        an OverflowError too, and the mixin's second job. Year 0 and
        year 10000 are the two edges of what a date can hold, and Django
        refuses them itself."""
        for list_name, field in (
            ("admin:finance_transaction_changelist", "date"),
            ("admin:audit_auditlogentry_changelist", "timestamp"),
        ):
            for query in (
                {f"{field}__year": "0"},
                {f"{field}__year": "10000"},
                {f"{field}__year": "9999", f"{field}__month": "12"},
                {f"{field}__year": "9999", f"{field}__month": "12", f"{field}__day": "31"},
                {f"{field}__year": "2026", f"{field}__month": "2", f"{field}__day": "31"},
            ):
                with self.subTest(list=list_name, query=query):
                    self.assert_redirected_to_the_plain_list(list_name, query)

    def test_the_first_and_the_last_but_one_year_of_the_calendar_still_list(self):
        for year in ("1", "9998"):
            with self.subTest(year=year):
                response = self.client.get(
                    reverse("admin:finance_transaction_changelist"), {"date__year": year}
                )
                self.assertEqual(response.status_code, 200)

    def test_an_address_the_error_flag_is_already_on_gets_a_page_not_another_redirect(self):
        """Django sends a refused address to `?e=1`. If that address is
        refused too, something else is wrong, and Django shows its
        "Database error" page instead of redirecting forever: the mixin
        must not get in the way of that."""
        response = self.client.get(
            reverse("admin:finance_transaction_changelist"), {"e": "1", "id__in": HUGE_NUMBER}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/invalid_setup.html")


class AdminRefusedLookupTests(LoggedInAsSuperuserTestCase):
    """S115: `?memo__regex=(` was a 500 page. SQLite runs the pattern
    through a Python function, so a pattern that isn't valid only fails
    once a row is looked at: every list here has a row, and one without
    would pass on the broken code. A `range` lookup, which no page of
    the admin sends either, was a 500 page for another reason: it needs
    two values and an address gives it one string."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        account = Account.objects.create(name="Checking")
        # A row for the payee list, which would otherwise be empty.
        Payee.objects.create(name="Lidl")
        Transaction.objects.create(
            account=account, date=date(2026, 9, 1), amount=Decimal("10"), memo="lunch"
        )

    def test_a_regex_lookup_in_the_address_is_refused(self):
        url = reverse("admin:finance_transaction_changelist")
        # The last query is a valid pattern: refused too, so no pattern
        # (a slow one included) reaches the database.
        for query in (
            {"memo__regex": "("},
            {"memo__iregex": "["},
            {"memo__regex": "a{2,1}"},
            {"memo__regex": "lun"},
        ):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(url, query).status_code, 400)

    def test_a_range_lookup_in_the_address_is_refused(self):
        """One letter was an IndexError and any other count a
        ProgrammingError, on any field and through a relation."""
        url = reverse("admin:finance_transaction_changelist")
        for query in (
            {"memo__range": "a"},
            {"memo__range": "abc"},
            {"id__range": "1"},
            {"account__id__range": "1"},
        ):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(url, query).status_code, 400)

    def test_every_list_refuses_them_not_just_the_transactions(self):
        for list_name, query in (
            ("admin:finance_account_changelist", {"name__regex": "("}),
            ("admin:finance_category_changelist", {"name__regex": "("}),
            ("admin:finance_payee_changelist", {"name__regex": "("}),
            ("admin:audit_auditlogentry_changelist", {"table__regex": "("}),
            ("admin:audit_auditlogentry_changelist", {"id__range": "1"}),
        ):
            with self.subTest(list=list_name, query=query):
                self.assertEqual(self.client.get(reverse(list_name), query).status_code, 400)

    def test_the_other_lookups_still_work(self):
        url = reverse("admin:finance_transaction_changelist")
        # The last but one is what the admin's own date filters send: a
        # range is made of two lookups, never of `range`.
        for query in (
            {"memo__contains": "lun"},
            {"memo__icontains": "LUN"},
            {"memo__exact": "lunch"},
            {"date__gte": "2026-09-01", "date__lt": "2026-10-01"},
            {"q": "lun"},
        ):
            with self.subTest(query=query):
                response = self.client.get(url, query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.context["cl"].result_list), 1)


class KeyFitsTests(StashTestCase):
    """The check behind the dropdown and the bulk action, on its own:
    where its edge is, and what it says about a key that isn't a
    number. (The pages' own tests reach it only through a request.)"""

    def fits(self, key_field, value):
        return key_fits(key_field, value, settings.TEST_STASH)

    def test_a_number_the_database_can_hold_fits(self):
        key = Transaction._meta.pk
        for value in ("12", 12, "007", str(2**63 - 1), str(-(2**63))):
            with self.subTest(value=value):
                self.assertTrue(self.fits(key, value))

    def test_a_number_the_database_cant_hold_doesnt(self):
        key = Transaction._meta.pk
        for value in (str(2**63), str(-(2**63) - 1), HUGE_NUMBER):
            with self.subTest(value=value):
                self.assertFalse(self.fits(key, value))

    def test_something_that_isnt_a_number_doesnt(self):
        key = Transaction._meta.pk
        for value in ("", None, "zz", "1.5", "1e3", "0x1"):
            with self.subTest(value=value):
                self.assertFalse(self.fits(key, value))

    def test_a_key_that_isnt_a_whole_number_is_only_checked_for_its_kind(self):
        """No model here has one (every key is a BigAutoField), so a
        text field stands in: whatever `to_python()` accepts fits, and
        there is no range to check."""
        self.assertTrue(self.fits(Account._meta.get_field("name"), "anything"))


class NamesForLogTests(SimpleTestCase):
    """What the admin writes into the log about a hand-made address: its
    parameter names, which the visitor typed, so they can be
    anything."""

    def test_a_line_break_or_a_control_character_cant_forge_a_log_line(self):
        name = f"values__a{chr(10)}FAKE 2026-11-19 ERROR forged{chr(27)}[2J"
        line = names_for_log(["id__in", name])
        self.assertNotIn(chr(10), line)
        self.assertNotIn(chr(27), line)
        self.assertIn("id__in", line)

    def test_the_names_come_out_sorted_and_a_long_run_is_cut_short(self):
        self.assertEqual(names_for_log(["b", "a"]), "'a', 'b'")
        line = names_for_log(["x" * 500])
        self.assertLessEqual(len(line), 203)
        self.assertTrue(line.endswith("..."))


class AdminAutocompleteValueTests(LoggedInAsSuperuserTestCase):
    """S113: the Add page's autocomplete dropdowns look their selected
    value up while the page is drawn, after the view has returned. A
    value that can't be a key was a 500 page, from the Add page's
    address and from a form posted again with its errors."""

    FIELDS = TransactionAdmin.autocomplete_fields

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.account = Account.objects.create(name="Checking")

    def selected_in(self, response, field):
        """The values of the dropdown's selected options."""
        dropdown = re.search(
            rf'<select name="{field}".*?</select>', response.content.decode(), re.S
        )
        self.assertIsNotNone(dropdown, f"no dropdown for {field}")
        return re.findall(r'<option value="([^"]*)" selected', dropdown.group(0))

    def test_a_value_that_cant_be_a_key_in_the_address_selects_nothing(self):
        url = reverse("admin:finance_transaction_add")
        for field in self.FIELDS:
            for value in (HUGE_NUMBER, "zz", "1.5"):
                with self.subTest(field=field, value=value):
                    response = self.client.get(url, {field: value})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(self.selected_in(response, field), [])

    def test_a_real_id_in_the_address_is_still_selected(self):
        response = self.client.get(
            reverse("admin:finance_transaction_add"), {"account": self.account.pk}
        )
        self.assertEqual(self.selected_in(response, "account"), [str(self.account.pk)])

    def test_a_form_posted_with_such_a_value_is_drawn_again_with_its_error(self):
        url = reverse("admin:finance_transaction_add")
        for value in (HUGE_NUMBER, "zz"):
            with self.subTest(value=value):
                response = self.client.post(
                    url, {"account": value, "date": "2026-09-01", "amount": "1"}
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("account", response.context["adminform"].form.errors)
                self.assertEqual(self.selected_in(response, "account"), [])
        self.assertFalse(Transaction.objects.exists())

    def test_a_form_posted_with_a_real_account_still_saves(self):
        data = {"account": self.account.pk, "date": "2026-09-01", "amount": "1", "reconciled": ""}
        response = self.client.post(reverse("admin:finance_transaction_add"), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Transaction.objects.count(), 1)

    def test_a_real_account_stays_selected_when_the_form_is_drawn_again(self):
        """The widget drops the values that can't be keys; it must not
        drop the real one next to a mistake in another box."""
        data = {"account": self.account.pk, "date": "not a date", "amount": "1"}
        response = self.client.post(reverse("admin:finance_transaction_add"), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("date", response.context["adminform"].form.errors)
        self.assertEqual(self.selected_in(response, "account"), [str(self.account.pk)])

    def test_a_saved_rows_own_values_are_selected_on_its_change_page(self):
        payee = Payee.objects.create(name="Lidl")
        category = Category.objects.create(name="Food", category_type=EXPENSE)
        saved = Transaction.objects.create(
            account=self.account,
            date=date(2026, 9, 1),
            amount=Decimal("1"),
            payee=payee,
            category=category,
        )
        response = self.client.get(reverse("admin:finance_transaction_change", args=[saved.pk]))
        for field, chosen in (("account", self.account), ("payee", payee), ("category", category)):
            with self.subTest(field=field):
                self.assertEqual(self.selected_in(response, field), [str(chosen.pk)])

    def test_the_edges_of_what_a_key_can_hold(self):
        """A key is a 64-bit signed number in SQLite. Nothing has such
        an id here, so every one of these selects nothing; the two just
        past the edges are the ones SQLite would refuse."""
        url = reverse("admin:finance_transaction_add")
        for value in (str(2**63 - 1), str(2**63), str(-(2**63)), str(-(2**63) - 1)):
            for field in self.FIELDS:
                with self.subTest(field=field, value=value):
                    response = self.client.get(url, {field: value})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(self.selected_in(response, field), [])

    def test_the_limits_come_from_the_stashs_own_database(self):
        """Every stash is its own database connection, and `default` is
        only the picker's empty placeholder. All of them are SQLite
        here, with the same limits, so only limits made up for the
        placeholder can show which one the check asked: they must not
        keep the account that exists out of the dropdown."""
        url = reverse("admin:finance_transaction_add")
        with mock.patch.object(
            connections["default"].ops, "integer_field_range", return_value=(10**6, 10**7)
        ):
            response = self.client.get(url, {"account": self.account.pk})
        self.assertEqual(self.selected_in(response, "account"), [str(self.account.pk)])

    def test_a_value_that_only_looks_like_a_number_selects_nothing(self):
        """`str.isdigit()` says yes to a superscript two, a circled one
        and the like, and `int()` then refuses them, so a check built on
        it would pass them on to a 500 page. The same dropdown opened as
        a popup (`_popup`, `_to_field`) is a second way in."""
        url = reverse("admin:finance_transaction_add")
        for value in ("\u00b2", "\u2460", "0x1", "1e3", "1_0_", "\x00"):
            for extra in ({}, {"_popup": "1", "_to_field": "id"}):
                with self.subTest(value=value, extra=extra):
                    response = self.client.get(url, {"account": value, "payee": value, **extra})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(self.selected_in(response, "account"), [])
                    self.assertEqual(self.selected_in(response, "payee"), [])

    def test_every_autocomplete_field_of_every_admin_is_a_foreign_key(self):
        """The widget that skips bad values is made for foreign keys.
        An autocomplete field of another kind would be a 500 page again
        without anyone noticing: this fails first, so the guard is
        extended."""
        for model, model_admin in admin.site._registry.items():
            for name in model_admin.autocomplete_fields:
                with self.subTest(model=model._meta.label, field=name):
                    self.assertIsInstance(model._meta.get_field(name), models.ForeignKey)


class AdminBulkActionTests(LoggedInAsSuperuserTestCase):
    """S114: the boxes on a list only send real ids, but a hand-made
    post can send anything. A huge id was an OverflowError and a word a
    ValueError: both a 500 page."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.account = Account.objects.create(name="Checking")
        cls.row = Transaction.objects.create(
            account=cls.account, date=date(2026, 9, 1), amount=Decimal("10")
        )

    def post_action(self, selected, **extra):
        data = {"action": "delete_selected", "_selected_action": selected, **extra}
        return self.client.post(reverse("admin:finance_transaction_changelist"), data)

    def test_an_id_that_cant_be_a_key_changes_nothing_and_says_so(self):
        real = str(self.row.pk)
        list_url = reverse("admin:finance_transaction_changelist")
        # With `post: yes` the action would delete: it must not, not
        # even the real row selected along with the bad id.
        for selected in (
            [HUGE_NUMBER],
            ["abc"],
            [real, HUGE_NUMBER],
            [real, "abc"],
            [""],
            ["1.5"],
        ):
            with self.subTest(selected=selected):
                response = self.post_action(selected, post="yes")
                # Not fetched here: the page shows the message once, and
                # the check below must be the one to see it.
                self.assertRedirects(response, list_url, fetch_redirect_response=False)
                self.assertTrue(Transaction.objects.filter(pk=self.row.pk).exists())
                self.assertContains(self.client.get(list_url), "could not be found")

    def test_it_is_refused_with_select_across_too(self):
        response = self.post_action([HUGE_NUMBER], select_across="1", post="yes")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Transaction.objects.exists())

    def test_the_refusal_is_logged_with_a_count_and_not_what_was_sent(self):
        with self.assertLogs("stashy.admin_mixins", "WARNING") as logged:
            self.post_action([HUGE_NUMBER, "abc"])
        self.assertIn("2 selected", logged.output[0])
        self.assertNotIn(HUGE_NUMBER, logged.output[0])

    def test_a_real_selection_still_reaches_the_action(self):
        response = self.post_action([str(self.row.pk)])
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/delete_selected_confirmation.html")
        self.assertTrue(Transaction.objects.exists())

    def test_the_largest_and_smallest_key_are_acceptable_and_one_past_is_not(self):
        """The edge is where a `<` that should be `<=` hides. An id
        that no row has is not an error: Django finds nothing and goes
        back to the list without a word. One SQLite can't hold is
        refused with the warning."""
        list_url = reverse("admin:finance_transaction_changelist")
        for selected, refused in (
            (2**63 - 1, False),
            (-(2**63), False),
            (2**63, True),
            (-(2**63) - 1, True),
        ):
            with self.subTest(selected=selected):
                response = self.post_action([str(selected)], post="yes")
                self.assertRedirects(response, list_url, fetch_redirect_response=False)
                self.assertTrue(Transaction.objects.filter(pk=self.row.pk).exists())
                page = self.client.get(list_url)
                if refused:
                    self.assertContains(page, "could not be found")
                else:
                    self.assertNotContains(page, "could not be found")

    def test_a_value_that_only_looks_like_a_number_is_refused_not_a_500_page(self):
        """See the same words in AdminAutocompleteValueTests."""
        for selected in ("\u00b2", "\u2460", "0x1", "1e3", "1_0_", "\x00"):
            with self.subTest(selected=selected):
                response = self.post_action([selected], post="yes")
                self.assertEqual(response.status_code, 302)
                self.assertTrue(Transaction.objects.exists())

    def test_the_list_forms_own_post_is_refused_the_same_way(self):
        """The Go button posts `index` and no `post`: a second branch
        of the list view, reaching the same method."""
        list_url = reverse("admin:finance_transaction_changelist")
        response = self.post_action([HUGE_NUMBER], index="0")
        self.assertRedirects(response, list_url, fetch_redirect_response=False)
        self.assertTrue(Transaction.objects.exists())
        self.assertContains(self.client.get(list_url), "could not be found")

    def test_the_limits_come_from_the_stashs_own_database(self):
        """The dropdown's twin of this test is in the class above:
        limits made up for the picker's placeholder must not refuse
        the row that exists."""
        with mock.patch.object(
            connections["default"].ops, "integer_field_range", return_value=(10**6, 10**7)
        ):
            response = self.post_action([str(self.row.pk)], post="yes")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Transaction.objects.exists())

    def test_the_same_real_id_twice_is_still_a_real_selection(self):
        real = str(self.row.pk)
        response = self.post_action([real, real], post="yes")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Transaction.objects.exists())

    def test_the_action_location_reaches_djangos_own_code(self):
        """Django 6.1 can show an action on a row's change page too.
        None of Stashy's admins has one, but its answer would come
        through this method: if `action_location` were dropped on the
        way, such an action would be read as one on the list."""
        model_admin = admin.site._registry[Transaction]
        request = RequestFactory().post("/", {helpers.ACTION_CHECKBOX_NAME: [str(self.row.pk)]})
        with mock.patch.object(
            admin.ModelAdmin, "response_action", return_value="answered"
        ) as djangos:
            answer = model_admin.response_action(
                request, Transaction.objects.all(), action_location=ActionLocation.CHANGE_FORM
            )
        self.assertEqual(answer, "answered")
        self.assertIs(djangos.call_args.kwargs["action_location"], ActionLocation.CHANGE_FORM)

    def test_a_list_with_no_actions_just_shows_itself_again(self):
        """The audit log offers no action (nothing can be deleted
        there), so Django's own form check answers a bulk post before
        the mixin is reached: this is not a test of the mixin, only of
        the answer such a list gives."""
        response = self.client.post(
            reverse("admin:audit_auditlogentry_changelist"),
            {"action": "delete_selected", "_selected_action": [HUGE_NUMBER]},
        )
        self.assertEqual(response.status_code, 200)


class AdminOddParameterTests(LoggedInAsSuperuserTestCase):
    """Parameters of a list's address that must never give a 500 page.
    Apart from the date filter at the edges of the calendar (the
    mixin's own job), Django's admin already answers all of these
    itself: they are here so that a Django update, or an admin that
    overrides one of them, can't quietly change that. Every list has a
    row, since a bad value can wait until a row is looked at."""

    ODD = ("zz", "", "-1", "0", "9" * 30, "1.5", "1" * 5000, "\u00b2", "\x00", "a,b")
    LISTS = ("admin:finance_transaction_changelist", "admin:audit_auditlogentry_changelist")

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        account = Account.objects.create(name="Checking")
        Transaction.objects.create(
            account=account, date=date(2026, 9, 1), amount=Decimal("10"), memo="lunch"
        )

    def test_page_order_search_and_popup_parameters_are_never_a_500_page(self):
        for list_name in self.LISTS:
            for name in (
                "p",
                "o",
                "all",
                "q",
                "_popup",
                "_to_field",
                "_changelist_filters",
                "ot",
                "index",
            ):
                for value in self.ODD:
                    with self.subTest(list=list_name, name=name, value=value[:12]):
                        self.assertLess(
                            self.client.get(reverse(list_name), {name: value}).status_code, 500
                        )

    def test_a_filter_the_list_offers_given_a_bad_value_is_never_a_500_page(self):
        for name in (
            "account__id__exact",
            "category__id__exact",
            "reconciled__exact",
            "account__isnull",
        ):
            for value in self.ODD:
                with self.subTest(name=name, value=value[:12]):
                    response = self.client.get(reverse(self.LISTS[0]), {name: value})
                    self.assertLess(response.status_code, 500)

    def test_a_date_filter_at_the_edges_of_the_calendar_is_never_a_500_page(self):
        # Django warns that a date typed into an address has no time
        # zone. That is expected here, and would only clutter the run.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            for name in ("timestamp__gte", "timestamp__lt"):
                for value in (
                    "0001-01-01",
                    "9999-12-31",
                    "0000-01-01",
                    "10000-01-01",
                    "2026-02-30",
                ):
                    with self.subTest(name=name, value=value):
                        response = self.client.get(reverse(self.LISTS[1]), {name: value})
                        self.assertLess(response.status_code, 500)


class AdminLongInputTests(LoggedInAsSuperuserTestCase):
    """S117: a filter value or a search no page sends, which SQLite
    can't run, was a 500 page and a traceback in the log. Every case
    has a row to look at, since SQLite's own errors come only once
    there is one. The row's memo is longer than any limit, so a search
    that is refused can be told from one that simply found nothing."""

    ANOTHER_LIST = "admin:audit_auditlogentry_changelist"
    LIST = "admin:finance_transaction_changelist"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        account = Account.objects.create(name="Checking")
        payee = Payee.objects.create(name="Lidl")
        cls.row = Transaction.objects.create(
            account=account,
            date=date(2026, 9, 1),
            amount=Decimal("10"),
            memo="m" * 250,
            payee=payee,
        )

    def found(self, list_name, query):
        response = self.client.get(reverse(list_name), query)
        self.assertEqual(response.status_code, 200)
        return len(response.context["cl"].result_list)

    def test_a_search_too_long_for_sqlite_finds_nothing_instead_of_a_500_page(self):
        for list_name in (self.LIST, self.ANOTHER_LIST):
            for search in ("a" * 60_000, " ".join(["word"] * 3000)):
                with self.subTest(list=list_name, search=search[:10]):
                    self.assertEqual(self.found(list_name, {"q": search}), 0)

    def test_the_autocomplete_search_has_the_same_limits(self):
        query = {"app_label": "finance", "model_name": "transaction", "field_name": "payee"}
        for term in ("a" * 60_000, " ".join(["word"] * 3000)):
            with self.subTest(term=term[:10]):
                response = self.client.get(reverse("admin:autocomplete"), {**query, "term": term})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["results"], [])

    def test_a_search_at_the_limits_still_finds_its_row_and_one_past_them_doesnt(self):
        for search, found in (
            ("m" * 200, 1),
            ("m" * 201, 0),
            (" ".join(["m"] * 50), 1),
            (" ".join(["m"] * 51), 0),
        ):
            with self.subTest(search=f"{len(search)} characters"):
                self.assertEqual(self.found(self.LIST, {"q": search}), found)

    def test_a_filter_value_of_over_a_thousand_characters_is_refused(self):
        url = reverse(self.LIST)
        self.assertEqual(self.client.get(url, {"id__in": ",".join(["1"] * 600)}).status_code, 400)
        self.assertEqual(self.client.get(url, {"id__in": ",".join(["1"] * 400)}).status_code, 200)

    def test_a_filter_value_of_a_thousand_characters_is_allowed_and_one_more_is_refused(self):
        url = reverse(self.LIST)
        for length, status in ((1000, 200), (1001, 400)):
            with self.subTest(length=length):
                # 499 single ids, then one that makes up the length.
                value = ",".join(["1"] * 499 + ["1" * (length - 998)])
                self.assertEqual(len(value), length)
                self.assertEqual(self.client.get(url, {"id__in": value}).status_code, status)

    def test_an_empty_to_field_on_a_change_or_delete_page_is_read_as_not_given(self):
        """Django asks the model for a field called "" otherwise."""
        for view in ("change", "delete"):
            url = reverse(f"admin:finance_transaction_{view}", args=[self.row.pk])
            for to_field in ("", "id"):
                with self.subTest(view=view, to_field=to_field):
                    self.assertEqual(
                        self.client.get(url, {"_to_field": to_field}).status_code, 200
                    )


class AdminAccountDropdownOrderTests(LoggedInAsSuperuserTestCase):
    def test_the_account_dropdown_pages_in_name_order_without_a_warning(self):
        """The balances in the admin's account list group its rows,
        which drops the model's own ordering. The Transaction page's
        account dropdown pages that list, so unordered it could repeat
        an account on the second page and skip another."""
        names = [f"Account {number:02d}" for number in range(30)]
        for name in reversed(names):
            Account.objects.create(name=name)
        query = {
            "term": "",
            "app_label": "finance",
            "model_name": "transaction",
            "field_name": "account",
        }
        with warnings.catch_warnings():
            warnings.simplefilter("error", UnorderedObjectListWarning)
            first = self.client.get(reverse("admin:autocomplete"), query).json()
            second = self.client.get(reverse("admin:autocomplete"), {**query, "page": 2}).json()
        shown = [result["text"] for result in first["results"] + second["results"]]
        self.assertEqual(shown, names)
        self.assertTrue(first["pagination"]["more"])
        self.assertFalse(second["pagination"]["more"])


class AdminAccountListTests(LoggedInAsSuperuserTestCase):
    """8.15 (F56): the admin's account list computes every balance in
    the same query."""

    def queries_with(self, count):
        create_accounts_with_a_transaction(count)
        response, query_count = self.get_and_count_queries(
            reverse("admin:finance_account_changelist")
        )
        self.assertEqual(
            {a.balance for a in response.context["cl"].result_list}, {Decimal("10.00")}
        )
        return query_count

    def test_more_accounts_need_no_more_queries(self):
        self.assertEqual(self.queries_with(1), self.queries_with(4))

    def test_deleting_from_the_list_still_works(self):
        account = Account.objects.create(name="Old wallet")
        response = self.client.post(
            reverse("admin:finance_account_changelist"),
            {"action": "delete_selected", "_selected_action": [account.pk], "post": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Account.objects.filter(pk=account.pk).exists())
