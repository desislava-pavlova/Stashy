"""Money and regional formats: reading typed amounts, the money filter, date formats, and
each stash's own preferences."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from django.utils import formats

from .. import regional
from ..models import Profile
from ..money import parse_amount
from ..templatetags.stashy_format import money
from .base import StashTestCase

NBSP = "\u00a0"


class ParseAmountTests(SimpleTestCase):
    def test_common_bank_formats(self):
        cases = {
            "1234.56": Decimal("1234.56"),
            "19.99": Decimal("19.99"),
            "-50": Decimal("-50.00"),
            "0.1": Decimal("0.10"),
            "1 234.56": Decimal("1234.56"),
            f"1{NBSP}234,56": Decimal("1234.56"),
            "1.234,56": Decimal("1234.56"),
            "1,234.56": Decimal("1234.56"),
            "1.234.567": Decimal("1234567.00"),
            "12,50": Decimal("12.50"),
            "12,5": Decimal("12.50"),
            "1,234": Decimal("1234.00"),
            "(12.00)": Decimal("-12.00"),
            "€ -5,00": Decimal("-5.00"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_amount(raw), expected)

    def test_unreadable_values_return_none(self):
        for raw in ("", "   ", "abc", "NaN", None):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_amount(raw))


class MoneyFilterTests(SimpleTestCase):
    def test_formatting(self):
        cases = [
            (Decimal("10000.5"), f"10{NBSP}000.50"),
            (Decimal("-1234567.891"), f"-1{NBSP}234{NBSP}567.89"),
            (Decimal("999"), "999.00"),
            (0, "0.00"),
            (Decimal("-0.001"), "0.00"),
            (None, ""),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(money(value), expected)


class DateFormatTests(SimpleTestCase):
    def test_dates_render_european(self):
        self.assertEqual(formats.date_format(date(2026, 4, 3)), "03/04/2026")
        self.assertEqual(formats.get_format("FIRST_DAY_OF_WEEK"), 1)


class RegionalPreferencesTests(SimpleTestCase):
    def test_format_number_respects_the_active_style(self):
        comma_style = regional.Preferences(number_format="dot_comma")
        self.assertEqual(regional.format_number(Decimal("1234.5"), preferences=comma_style), "1.234,50")

    def test_format_date_respects_the_active_style(self):
        ymd = regional.Preferences(date_format="ymd")
        self.assertEqual(regional.format_date(date(2026, 4, 3), preferences=ymd), "2026-04-03")

    def test_date_format_detected_from_a_windows_short_date_pattern(self):
        self.assertEqual(regional.date_format_from_pattern("d.M.yyyy 'г.'"), "dmy")
        self.assertEqual(regional.date_format_from_pattern("M/d/yyyy"), "mdy")
        self.assertEqual(regional.date_format_from_pattern("yyyy-MM-dd"), "ymd")

    def test_number_format_detected_from_separators(self):
        self.assertEqual(regional.number_format_from_separators(" ", "."), "space_dot")
        self.assertEqual(regional.number_format_from_separators(",", "."), "comma_dot")
        self.assertEqual(regional.number_format_from_separators(".", ","), "dot_comma")


class ProfileTests(StashTestCase):
    def test_profile_is_created_once_and_then_left_alone(self):
        user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        first = Profile.for_user(user)
        first.date_format = "ymd"
        first.save()
        second = Profile.for_user(user)
        self.assertEqual(second.date_format, "ymd")
        self.assertEqual(Profile.objects.filter(user=user).count(), 1)
