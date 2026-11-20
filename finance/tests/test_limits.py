"""The limits on what Stashy stores (`finance/limits.py`): a date from
1900 to 2100, a reminder interval of at most 120, a transaction number
of at most 999,999,999, and an amount a money column can hold. Each way
in meets them: the forms, the admin and the importer. What is already
stored is not checked, and stays as it is until someone saves it again.
"""

import ast
import inspect
import io
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django import forms
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.test import SimpleTestCase
from django.urls import reverse

from openpyxl import Workbook

from .. import forms as finance_forms
from .. import limits as limits_module
from .. import regional
from ..forms import (
    AccountCreateForm,
    ReconcileFinishForm,
    ReconcileStartForm,
    ReminderForm,
    TransactionForm,
    TransferForm,
)
from ..limits import (
    DATE_OUT_OF_RANGE_MESSAGE,
    MAX_AMOUNT,
    MAX_REMINDER_INTERVAL,
    MAX_TRANSACTION_NUMBER,
    is_plausible_date,
    is_plausible_rate,
    validate_plausible_date,
)
from ..models import Account, Category, ExchangeRate, Profile, Reconciliation, Reminder, Transaction
from ..money import parse_amount
from ..services import importing
from ..services.importing import _parse_transaction_number, import_file
from .base import (
    LoggedInAsSuperuserTestCase,
    LoggedInWithAccountTestCase,
    StashTestCase,
    csv_upload,
    leaked_syntax_in,
)


# Day first, the way the test stash's dates are typed.
BAD_DATES = ("31/12/1899", "01/01/2101", "01/09/0026", "31/12/9999", "01/01/0001")
EDGE_DATES = ("01/01/1900", "31/12/2100")
HUGE_AMOUNT = "9" * 30


class PlausibleDateTests(SimpleTestCase):
    def test_the_first_and_last_day_are_inside_and_the_days_around_them_are_not(self):
        for value, inside in (
            (date(1899, 12, 31), False),
            (date(1900, 1, 1), True),
            (date(2026, 9, 12), True),
            (date(2100, 12, 31), True),
            (date(2101, 1, 1), False),
            (date(1, 1, 1), False),
            (date(9999, 12, 31), False),
        ):
            with self.subTest(value=value):
                self.assertEqual(is_plausible_date(value), inside)

    def test_the_validator_says_what_the_limits_are(self):
        validate_plausible_date(date(2026, 9, 12))
        with self.assertRaises(ValidationError) as caught:
            validate_plausible_date(date(26, 9, 1))
        self.assertEqual(caught.exception.messages, ["Enter a date between 1900 and 2100."])
        self.assertEqual(DATE_OUT_OF_RANGE_MESSAGE, "Enter a date between 1900 and 2100.")


class LimitsModuleTests(SimpleTestCase):
    def test_it_imports_nothing_from_the_app(self):
        """The models, the forms and the importer all import it, so an
        import of any of them from there would be a cycle."""
        tree = ast.parse(Path(limits_module.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add("." * node.level + (node.module or ""))
        self.assertEqual(imported, {"datetime", "decimal", "django.core.exceptions"})

    def test_an_exchange_rate_has_to_fit_its_column_and_not_round_to_zero(self):
        for text in ("1.16", "0.0000000001", "0.00000000006", "9999999999.9999999999"):
            with self.subTest(rate=text):
                self.assertTrue(is_plausible_rate(Decimal(text)))
        for text in ("0", "-1", "NaN", "Infinity", "10000000000", "1e11", "1e30", "1e-30", "0.00000000005"):
            with self.subTest(rate=text):
                self.assertFalse(is_plausible_rate(Decimal(text)))


class EveryDateHasTheLimitTests(SimpleTestCase):
    """A date column or box added later must not skip the rule without
    someone saying so."""

    def test_every_date_column_of_a_stored_model_has_it(self):
        found = 0
        for model in apps.get_app_config("finance").get_models():
            for field in model._meta.get_fields():
                # DateTimeField is a DateField too; those are stamped by
                # the app, not typed.
                if type(field) is models.DateField:
                    found += 1
                    with self.subTest(column=f"{model.__name__}.{field.name}"):
                        self.assertIn(validate_plausible_date, field.validators)
        self.assertEqual(found, 5)

    def test_every_plain_form_with_a_date_box_has_it(self):
        """A ModelForm gets its columns' validators. The plain forms
        have to say so, and so does a ModelForm's extra date box that
        isn't a column (the opening balance's date). The Reports
        filter, which has a rule of its own (a range of at most a
        century), says it in `clean()`."""
        for name, form_class in inspect.getmembers(finance_forms, inspect.isclass):
            if not issubclass(form_class, forms.BaseForm):
                continue
            if form_class.__module__ != finance_forms.__name__ or name == "DateRangeFilterForm":
                continue
            model = getattr(getattr(form_class, "_meta", None), "model", None)
            columns = {column.name for column in model._meta.get_fields()} if model else set()
            for field_name, field in form_class.base_fields.items():
                if isinstance(field, forms.DateField) and field_name not in columns:
                    with self.subTest(form=name, field=field_name):
                        self.assertIn(validate_plausible_date, field.validators)


class AmountLimitTests(SimpleTestCase):
    def test_the_biggest_amount_is_what_a_money_column_holds(self):
        for model, name in (
            (Transaction, "amount"),
            (Reminder, "amount"),
            (Reconciliation, "statement_balance"),
            (Reconciliation, "opening_balance"),
        ):
            column = model._meta.get_field(name)
            with self.subTest(column=f"{model.__name__}.{name}"):
                self.assertEqual(column.decimal_places, 2)
                self.assertEqual(MAX_AMOUNT, Decimal(10 ** (column.max_digits - 2)) - Decimal("0.01"))

    def test_an_amount_too_big_to_store_isnt_read(self):
        for raw in (
            HUGE_AMOUNT,
            "1" + "0" * 400,
            "99999999999",
            "-99999999999",
            "(99999999999)",
            "9999999999.995",
            "-9999999999.995",
        ):
            with self.subTest(raw=raw[:20]):
                self.assertIsNone(parse_amount(raw))

    def test_the_biggest_amounts_and_the_cent_below_are_read(self):
        for raw, expected in (
            ("9999999999.99", Decimal("9999999999.99")),
            ("-9999999999.99", Decimal("-9999999999.99")),
            ("9999999999.994", Decimal("9999999999.99")),
            ("-9999999999.994", Decimal("-9999999999.99")),
            ("(9999999999.99)", Decimal("-9999999999.99")),
            ("12,50", Decimal("12.50")),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(parse_amount(raw), expected)

    def test_a_long_amount_just_under_half_a_cent_past_the_limit_still_rounds_down(self):
        """abs() rounds to 28 digits before comparing, which took this
        up to exactly half a cent past the limit and refused it."""
        self.assertEqual(parse_amount("9999999999.9949999999999999999999"), Decimal("9999999999.99"))

    def test_a_number_of_a_million_digits_is_refused_at_once(self):
        """abs() of it was a decimal.Overflow: a 500 page for one cell
        of a hand-made Excel file."""
        self.assertIsNone(parse_amount("9" * 1_100_000))
        self.assertIsNone(parse_amount("(" + "9" * 1_100_000 + ")"))


class TransactionFormLimitTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.account = Account.objects.create(name="Checking")

    def form(self, **overrides):
        data = {"account": self.account.pk, "date": "05/01/2026", "amount": "10.00", **overrides}
        return TransactionForm(data=data)

    def test_a_date_outside_the_limits_is_refused_with_the_reason(self):
        for text in BAD_DATES:
            with self.subTest(date=text):
                form = self.form(date=text)
                self.assertFalse(form.is_valid())
                self.assertEqual(form.errors["date"], [DATE_OUT_OF_RANGE_MESSAGE])

    def test_the_first_and_last_day_are_accepted(self):
        for text in EDGE_DATES:
            with self.subTest(date=text):
                self.assertTrue(self.form(date=text).is_valid())

    def test_the_limits_hold_in_the_month_first_and_year_first_formats_too(self):
        """The stash's date format only decides how a date is read; the
        limits are on the date that comes out."""
        for date_format, inside, outside in (
            ("mdy", ("01/01/1900", "12/31/2100"), ("12/31/1899", "01/01/2101", "09/01/0026")),
            ("ymd", ("1900-01-01", "2100-12-31"), ("1899-12-31", "2101-01-01", "0026-09-01")),
        ):
            with regional.activate(regional.Preferences(date_format=date_format)):
                for text in inside:
                    with self.subTest(date_format=date_format, date=text):
                        self.assertTrue(self.form(date=text).is_valid())
                for text in outside:
                    with self.subTest(date_format=date_format, date=text):
                        self.assertEqual(self.form(date=text).errors["date"], [DATE_OUT_OF_RANGE_MESSAGE])

    def test_a_number_over_the_limit_is_refused_and_the_limit_itself_is_not(self):
        for number in ("1000000000", "2147483648", "9223372036854775807"):
            with self.subTest(number=number):
                form = self.form(transaction_number=number)
                self.assertFalse(form.is_valid())
                self.assertIn("transaction_number", form.errors)
        for number in ("1", str(MAX_TRANSACTION_NUMBER)):
            with self.subTest(number=number):
                self.assertTrue(self.form(transaction_number=number).is_valid())


class ReminderFormLimitTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.account = Account.objects.create(name="Checking")

    def form(self, **overrides):
        data = {
            "reminder_type": Category.CategoryType.EXPENSE,
            "account": self.account.pk,
            "amount": "5",
            "frequency": Reminder.Frequency.MONTHLY,
            "interval": "1",
            "next_due_date": "01/10/2026",
            "no_end_date": True,
            **overrides,
        }
        return ReminderForm(data=data)

    def test_the_due_date_and_the_end_date_have_the_limits(self):
        for text in BAD_DATES:
            with self.subTest(due=text):
                form = self.form(next_due_date=text)
                self.assertEqual(form.errors["next_due_date"], [DATE_OUT_OF_RANGE_MESSAGE])
            with self.subTest(end=text):
                form = self.form(no_end_date=False, end_date=text)
                self.assertEqual(form.errors["end_date"], [DATE_OUT_OF_RANGE_MESSAGE])
        for text in EDGE_DATES:
            with self.subTest(edge=text):
                self.assertTrue(self.form(next_due_date=text).is_valid())

    def test_an_interval_over_the_limit_is_refused_and_the_limit_itself_is_not(self):
        for interval in (str(MAX_REMINDER_INTERVAL + 1), "1000", "32767"):
            with self.subTest(interval=interval):
                form = self.form(interval=interval)
                self.assertFalse(form.is_valid())
                self.assertIn("interval", form.errors)
        self.assertTrue(self.form(interval=str(MAX_REMINDER_INTERVAL)).is_valid())

    def test_a_reminder_that_used_to_overflow_the_calendar_is_a_form_error_now(self):
        """Due on the last day of year 9999, or yearly every 32767
        years, ended in a ValueError (a 500 page) while the next date
        was worked out."""
        for overrides in ({"next_due_date": "31/12/9999"}, {"frequency": "yearly", "interval": "32767"}):
            with self.subTest(overrides=overrides):
                self.assertFalse(self.form(**overrides).is_valid())

    def test_a_one_time_reminder_ignores_the_two_fields_the_page_hides_for_it(self):
        """Typed before switching to One-time, an interval of 200 or an
        end date in 2101 would be an error in a row nobody can see."""
        form = self.form(
            frequency=Reminder.Frequency.ONE_TIME, interval="200", no_end_date=False, end_date="01/01/2101"
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual((form.cleaned_data["interval"], form.cleaned_data["end_date"]), (1, None))


class OtherFormLimitTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.checking = Account.objects.create(name="Checking")
        cls.savings = Account.objects.create(name="Savings")

    def test_the_opening_balance_date_has_them(self):
        for text, valid in (("01/01/1899", False), ("01/01/1900", True), ("01/01/2101", False)):
            with self.subTest(date=text):
                form = AccountCreateForm(
                    data={
                        "name": "Wallet",
                        "account_type": "cash",
                        "currency": "EUR",
                        "opening_balance": "0",
                        "opening_balance_date": text,
                        "is_active": True,
                    }
                )
                self.assertEqual(form.is_valid(), valid, form.errors)

    def test_a_transfers_date_has_them(self):
        for text, valid in (("01/09/0026", False), ("31/12/2100", True)):
            with self.subTest(date=text):
                form = TransferForm(
                    data={"to_account": self.savings.pk, "amount": "10", "date": text}, from_account=self.checking
                )
                self.assertEqual(form.is_valid(), valid, form.errors)

    def test_the_statement_date_has_them_on_both_steps_of_reconciling(self):
        for text, valid in (("31/12/9999", False), ("01/01/2101", False), ("30/09/2026", True)):
            with self.subTest(start=text):
                form = ReconcileStartForm(data={"statement_date": text, "statement_balance": "1.00"})
                self.assertEqual(form.is_valid(), valid, form.errors)
        for text, valid in (("9999-12-31", False), ("2101-01-01", False), ("2026-09-30", True)):
            with self.subTest(finish=text):
                form = ReconcileFinishForm(
                    account=self.checking, data={"statement_date": text, "statement_balance": "1.00"}
                )
                self.assertEqual(form.is_valid(), valid, form.errors)


class TransactionPageLimitTests(LoggedInWithAccountTestCase):
    def test_a_typo_in_the_year_is_refused_and_nothing_is_saved(self):
        """01/09/0026, for 01/09/2026, was saved as the year 26 and
        threw the reports' ranges off without a word."""
        response = self.client.post(
            reverse("finance:transaction_add"),
            {"account": self.account.pk, "date": "01/09/0026", "amount": "10.00"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, DATE_OUT_OF_RANGE_MESSAGE)
        self.assertFalse(Transaction.objects.exists())


class EveryPageSaysWhyTests(LoggedInWithAccountTestCase):
    """Each page draws its own form errors, by hand on several of them,
    so a form refusing a date proves nothing about the person being told
    why."""

    DATE_BEYOND_THE_LIMIT = "01/01/2101"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.savings = Account.objects.create(name="Savings")

    def pages(self):
        """(label, url name, args, what is posted) for every page with
        a date box a person can fill in."""
        reminder = {
            "reminder_type": Category.CategoryType.EXPENSE,
            "account": self.account.pk,
            "amount": "5",
            "frequency": Reminder.Frequency.MONTHLY,
            "interval": "1",
            "next_due_date": self.DATE_BEYOND_THE_LIMIT,
            "no_end_date": "on",
        }
        old_reminder = Reminder.objects.create(
            account=self.account, frequency=Reminder.Frequency.ONE_TIME, next_due_date=date(2026, 10, 1)
        )
        return [
            (
                "add account",
                "finance:account_add",
                [],
                {
                    "name": "Wallet",
                    "account_type": "cash",
                    "currency": "EUR",
                    "opening_balance": "5",
                    "opening_balance_date": self.DATE_BEYOND_THE_LIMIT,
                    "is_active": "on",
                },
            ),
            (
                "transfer",
                "finance:account_transfer",
                [self.account.pk],
                {"to_account": self.savings.pk, "amount": "5", "date": self.DATE_BEYOND_THE_LIMIT},
            ),
            (
                "reconcile, step 1",
                "finance:account_reconcile",
                [self.account.pk],
                {"statement_date": self.DATE_BEYOND_THE_LIMIT, "statement_balance": "5"},
            ),
            ("add reminder", "finance:reminder_add", [], reminder),
            ("edit reminder", "finance:reminder_edit", [old_reminder.pk], reminder),
        ]

    def stored_rows(self):
        return [model.objects.count() for model in (Account, Transaction, Reminder, Reconciliation)]

    def test_a_date_outside_the_limits_is_explained_where_it_was_typed_and_nothing_is_saved(self):
        for label, url_name, args, data in self.pages():
            with self.subTest(page=label):
                before = self.stored_rows()
                response = self.client.post(reverse(url_name, args=args), data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, DATE_OUT_OF_RANGE_MESSAGE)
                self.assertEqual(leaked_syntax_in(response.content.decode()), [])
                self.assertEqual(self.stored_rows(), before)

    def test_a_reconciliation_finished_with_a_date_outside_the_limits_saves_nothing(self):
        """The step-2 date comes back in a hidden box, which only a
        hand-made request can change."""
        response = self.client.post(
            reverse("finance:account_reconcile", args=[self.account.pk]),
            {
                "stage": "finish",
                "statement_date": "2101-01-01",
                "statement_balance": "5.00",
                "accept_difference": "on",
            },
        )
        self.assertContains(response, "Something in that reconciliation didn&#x27;t come through")
        self.assertFalse(Reconciliation.objects.exists())
        self.assertFalse(Transaction.objects.exists())


class WelcomeAccountStepLimitTests(StashTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        profile = Profile.for_user(cls.user)
        profile.welcome_step = 3
        profile.save(update_fields=["welcome_step"])

    def setUp(self):
        self.client.force_login(self.user)

    def test_the_first_accounts_opening_date_has_the_limits(self):
        data = {"name": "Wallet", "account_type": "cash", "currency": "EUR", "opening_balance": "5", "is_active": "on"}
        response = self.client.post(
            reverse("finance:welcome_step", args=[4]), {**data, "opening_balance_date": "01/01/2101"}
        )
        self.assertContains(response, DATE_OUT_OF_RANGE_MESSAGE)
        self.assertFalse(Account.objects.exists())
        response = self.client.post(
            reverse("finance:welcome_step", args=[4]), {**data, "opening_balance_date": "31/12/2100"}
        )
        self.assertRedirects(response, reverse("finance:dashboard"))
        self.assertEqual(Transaction.objects.get().date, date(2100, 12, 31))


class StoredRowsOutsideTheLimitsTests(LoggedInWithAccountTestCase):
    """What was saved before a limit existed (or by the admin's raw
    tools) is never checked, so it still opens everywhere, and the page
    that refuses to save it says what to change. Nobody is stuck with a
    row they can't open or can't fix."""

    def old_transaction(self):
        # Made the way a row from before the limits was: no validation.
        return Transaction.objects.create(
            account=self.account, date=date(2101, 1, 1), amount=Decimal("5.00"), transaction_number=2**40
        )

    def posted(self, **overrides):
        return {
            "account": self.account.pk,
            "date": "01/01/2101",
            "transaction_number": str(2**40),
            "amount": "7.00",
            **overrides,
        }

    def test_the_edit_page_opens_and_refuses_a_change_that_leaves_it_outside(self):
        old = self.old_transaction()
        url = reverse("finance:transaction_edit", args=[old.pk])
        page = self.client.get(url)
        self.assertContains(page, 'value="01/01/2101"')
        self.assertNotContains(page, DATE_OUT_OF_RANGE_MESSAGE)

        response = self.client.post(url, self.posted(memo="changed"))

        self.assertContains(response, DATE_OUT_OF_RANGE_MESSAGE)
        self.assertContains(response, f"less than or equal to {MAX_TRANSACTION_NUMBER}")
        old.refresh_from_db()
        self.assertEqual((old.memo, old.date, old.transaction_number), ("", date(2101, 1, 1), 2**40))

    def test_changing_its_date_and_number_saves_it(self):
        old = self.old_transaction()
        url = reverse("finance:transaction_edit", args=[old.pk])
        response = self.client.post(url, self.posted(date="05/01/2026", transaction_number="5"))
        self.assertEqual(response.status_code, 302)
        old.refresh_from_db()
        self.assertEqual((old.date, old.transaction_number), (date(2026, 1, 5), 5))

    def test_edit_mode_names_the_cells_to_change_and_saves_once_they_are(self):
        old = self.old_transaction()
        url = reverse("finance:transaction_quick_edit", args=[old.pk])

        response = self.client.post(url, self.posted(memo="changed"))

        self.assertEqual(response.status_code, 400)
        errors = response.json()["errors"]
        self.assertEqual(set(errors), {"date", "transaction_number"})
        self.assertEqual(errors["date"], [DATE_OUT_OF_RANGE_MESSAGE])
        response = self.client.post(url, self.posted(date="05/01/2026", transaction_number="5"))
        self.assertEqual(response.json()["ok"], True)

    def test_a_reminder_moved_past_the_last_date_opens_and_needs_a_new_date_to_save(self):
        """Enter and Skip move a recurring reminder's date on without
        asking the limit, so one due in the last month of 2100 ends up
        in 2101."""
        reminder = Reminder.objects.create(
            account=self.account,
            amount=Decimal("5"),
            frequency=Reminder.Frequency.MONTHLY,
            next_due_date=date(2101, 1, 31),
        )
        url = reverse("finance:reminder_edit", args=[reminder.pk])
        data = {
            "reminder_type": Category.CategoryType.EXPENSE,
            "account": self.account.pk,
            "amount": "6",
            "frequency": Reminder.Frequency.MONTHLY,
            "interval": "1",
            "next_due_date": "31/01/2101",
            "no_end_date": "on",
        }
        self.assertContains(self.client.get(url), 'value="31/01/2101"')

        refused = self.client.post(url, data)

        self.assertContains(refused, DATE_OUT_OF_RANGE_MESSAGE)
        reminder.refresh_from_db()
        self.assertEqual((reminder.amount, reminder.next_due_date), (Decimal("5.00"), date(2101, 1, 31)))
        self.assertEqual(self.client.post(url, {**data, "next_due_date": "31/01/2027"}).status_code, 302)
        reminder.refresh_from_db()
        self.assertEqual((reminder.amount, reminder.next_due_date), (Decimal("6.00"), date(2027, 1, 31)))


class NextNumberLimitTests(LoggedInWithAccountTestCase):
    def test_a_number_stored_before_the_limit_cant_stop_new_transactions(self):
        """A number of 9223372036854775807 made the next one 2**63,
        which the database can't store: every new transaction of that
        year was an error. The huge one is left out when counting on."""
        on = date(2026, 9, 1)
        Transaction.objects.create(account=self.account, date=on, amount=Decimal("1"), transaction_number=2**63 - 1)
        self.assertEqual(Transaction.next_number(on), 1)
        added = Transaction.objects.create(account=self.account, date=on, amount=Decimal("1"))
        self.assertEqual(added.transaction_number, 1)

    def test_the_biggest_number_that_counts_still_counts(self):
        on = date(2026, 9, 1)
        for number in (5, 2**40, MAX_TRANSACTION_NUMBER - 1):
            Transaction.objects.create(account=self.account, date=on, amount=Decimal("1"), transaction_number=number)
        self.assertEqual(Transaction.next_number(on), MAX_TRANSACTION_NUMBER)

    def test_the_next_number_never_goes_past_the_limit(self):
        """It was limit + 1, which the database holds but the form's
        own limit refuses: the Add page was prefilled with it, and
        Save said "less than or equal to 999999999" about a number
        nobody typed. Duplicates are allowed, so the limit is given
        again."""
        on = date(2026, 9, 1)
        Transaction.objects.create(
            account=self.account, date=on, amount=Decimal("1"), transaction_number=MAX_TRANSACTION_NUMBER
        )
        self.assertLessEqual(Transaction.next_number(on), MAX_TRANSACTION_NUMBER)
        page = self.client.get(reverse("finance:transaction_add"), {"date": on.isoformat()})
        response = self.client.post(
            reverse("finance:transaction_add"),
            {
                "account": self.account.pk,
                "date": "01/09/2026",
                "amount": "5.00",
                "transaction_number": page.context["form"].initial["transaction_number"],
            },
        )
        self.assertEqual(response.status_code, 302)


class ImportLimitTests(LoggedInWithAccountTestCase):
    """A row the stored limits would refuse is "unreadable", like a row
    with a bad date, and nothing else about the file changes."""

    def import_csv(self, rows):
        text = "Date,Num,Payee,Amount\n" + "\n".join(rows) + "\n"
        return import_file(csv_upload(text), self.account, date_format="dmy")

    def test_a_row_dated_outside_the_limits_is_unreadable(self):
        result = self.import_csv(
            [
                "05/01/2026,,Good,10.00",
                "31/12/1899,,Old,10.00",
                "01/01/2101,,Future,10.00",
                "01/09/0026,,Typo,10.00",
                "01/01/1900,,First,10.00",
                "31/12/2100,,Last,10.00",
            ]
        )
        self.assertEqual((result.created, result.unreadable), (3, 3))
        self.assertEqual(set(Transaction.objects.values_list("payee__name", flat=True)), {"Good", "First", "Last"})

    def test_an_amount_too_big_to_store_is_unreadable_not_an_error_page(self):
        """Thirty digits ended the whole import in an error page."""
        result = self.import_csv(
            [
                f"05/01/2026,,Huge,{HUGE_AMOUNT}",
                "06/01/2026,,Long," + "1" + "0" * 400,
                "07/01/2026,,Big,99999999999.00",
                "08/01/2026,,Edge,9999999999.99",
            ]
        )
        self.assertEqual((result.created, result.unreadable), (1, 3))
        self.assertEqual(Transaction.objects.get().amount, Decimal("9999999999.99"))

    def test_a_number_over_the_limit_comes_in_without_it(self):
        """The row is fine, only its Num can't be stored: it is numbered
        like a row with no Num."""
        result = self.import_csv(
            ["05/01/2026,1000000000,Shop,10.00", f"06/01/2026,{MAX_TRANSACTION_NUMBER},Cafe,5.00"]
        )
        self.assertEqual(result.created, 2)
        self.assertEqual(Transaction.objects.get(payee__name="Cafe").transaction_number, MAX_TRANSACTION_NUMBER)
        self.assertEqual(Transaction.objects.get(payee__name="Shop").transaction_number, 1)

    def test_a_huge_number_in_the_num_column_is_dropped_without_being_built(self):
        """int() of a Decimal is slow in its exponent (1e1000000 took
        about 20 seconds, 1e999999999 never ends), and the Num was
        turned into an int before the limit was looked at: one odd
        cell held the import, and the stash's write lock with it. So
        int() is swapped here for a version that refuses a huge
        number at once, and the test needn't wait for the slow one."""

        def refusing_a_huge_number(value):
            self.assertLess(value.adjusted(), 100, "int() was asked to build a number of over 100 digits")
            return int(value)

        with mock.patch.object(importing, "int", refusing_a_huge_number, create=True):
            self.assertIsNone(_parse_transaction_number("1e1000000"))

    def test_the_limits_hold_in_month_first_and_year_first_files_too(self):
        for date_format, inside, outside in (
            ("mdy", "12/31/2100", ("12/31/1899", "01/01/2101", "09/01/0026")),
            ("ymd", "2100-12-31", ("1899-12-31", "2101-01-01", "0026-09-01")),
        ):
            with self.subTest(date_format=date_format):
                Transaction.objects.all().delete()
                rows = [f"{inside},,Last,1.00", *(f"{text},,Outside {text},1.00" for text in outside)]
                text = "Date,Num,Payee,Amount\n" + "\n".join(rows) + "\n"
                result = import_file(csv_upload(text), self.account, date_format=date_format)
                self.assertEqual((result.created, result.unreadable), (1, len(outside)))
                self.assertEqual(Transaction.objects.get().date, date(2100, 12, 31))

    def test_an_excel_file_is_held_to_the_same_limits(self):
        workbook = Workbook()
        workbook.active.append(["Date", "Payee", "Amount"])
        # Excel can't hold a date before 1900, so the old one is a text
        # cell, which is how a file made by another program has it.
        for on, payee in ((date(2026, 1, 5), "Good"), ("1850-01-01", "Old"), (date(2101, 1, 1), "Future")):
            workbook.active.append([on, payee, 10])
        workbook.active.append([date(2026, 1, 6), "Huge", int(HUGE_AMOUNT)])
        buffer = io.BytesIO()
        workbook.save(buffer)

        result = import_file(SimpleUploadedFile("bank.xlsx", buffer.getvalue()), self.account)

        self.assertEqual((result.created, result.unreadable), (1, 3))

    def test_an_ofx_file_is_held_to_the_same_limits(self):
        def ofx(posted, amount):
            return (
                "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\nENCODING:USASCII\n"
                "CHARSET:1252\nCOMPRESSION:NONE\nOLDFILEUID:NONE\nNEWFILEUID:NONE\n\n"
                "<OFX><BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>"
                "<STMTRS><CURDEF>EUR<BANKACCTFROM><BANKID>1<ACCTID>2<ACCTTYPE>CHECKING</BANKACCTFROM>"
                "<BANKTRANLIST><DTSTART>20260101<DTEND>20260131"
                f"<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>{posted}<TRNAMT>{amount}<FITID>1<NAME>Lidl</STMTTRN>"
                "</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>\n"
            ).encode()

        for posted, amount, created, unreadable in (
            ("20260105", "-12.50", 1, 0),
            ("18991231", "-12.50", 0, 1),
            ("21010101", "-12.50", 0, 1),
            ("20260105", HUGE_AMOUNT, 0, 1),
        ):
            with self.subTest(posted=posted, amount=amount[:10]):
                Transaction.objects.all().delete()
                result = import_file(SimpleUploadedFile("bank.ofx", ofx(posted, amount)), self.account)
                self.assertEqual((result.created, result.unreadable), (created, unreadable))


class AdminLimitTests(LoggedInAsSuperuserTestCase):
    """The admin's forms are built from the columns, so the limits reach
    it through the model."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.account = Account.objects.create(name="Checking")

    def post_transaction(self, **overrides):
        data = {"account": self.account.pk, "date": "2026-09-01", "amount": "1", **overrides}
        return self.client.post(reverse("admin:finance_transaction_add"), data)

    def test_a_transaction_with_a_date_outside_the_limits_is_refused(self):
        for text in ("0026-09-01", "1899-12-31", "2101-01-01", "9999-12-31"):
            with self.subTest(date=text):
                response = self.post_transaction(date=text)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, DATE_OUT_OF_RANGE_MESSAGE)
        self.assertFalse(Transaction.objects.exists())

    def test_the_number_that_broke_every_later_transaction_is_refused(self):
        response = self.post_transaction(transaction_number=str(2**63 - 1))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"less than or equal to {MAX_TRANSACTION_NUMBER}")
        self.assertFalse(Transaction.objects.exists())

    def test_an_ordinary_transaction_is_still_saved(self):
        self.assertEqual(self.post_transaction(transaction_number="7").status_code, 302)
        self.assertEqual(Transaction.objects.get().transaction_number, 7)

    def test_an_exchange_rate_dated_outside_the_limits_is_refused(self):
        """A rate dated in 9999 would be the latest one for ever."""
        url = reverse("admin:finance_exchangerate_add")
        data = {"base_currency": "EUR", "quote_currency": "USD", "rate": "1.1", "source": "manual"}
        response = self.client.post(url, {**data, "date": "9999-12-31"})
        self.assertContains(response, DATE_OUT_OF_RANGE_MESSAGE)
        self.assertFalse(ExchangeRate.objects.exists())
        self.assertEqual(self.client.post(url, {**data, "date": "2026-09-01"}).status_code, 302)
