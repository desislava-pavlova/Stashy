"""Tests for Phase 14 (admin, tags, audit log) not already covered by audit/tests.py:
tags are gone everywhere, and the audit log's own timestamp format (A3)."""
from datetime import datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import regional
from .forms import TransactionForm
from .models import Account, Transaction


class TagsAreGoneTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("tester", password="not-a-real-password-123")
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
        from django.contrib import admin

        self.assertFalse(any(model.__name__ == "Tag" for model in admin.site._registry))

    def test_tag_model_no_longer_exists(self):
        from . import models

        self.assertFalse(hasattr(models, "Tag"))


class AuditTimestampFormatTests(SimpleTestCase):
    def test_iso_style_with_milliseconds_and_offset(self):
        value = datetime(2026, 9, 15, 12, 6, 48, 930000, tzinfo=dt_timezone.utc)
        with regional.activate(regional.Preferences(time_zone="Europe/Sofia")):
            self.assertEqual(regional.format_audit_timestamp(value), "2026-09-15 15:06:48.930 +03:00")

    def test_never_follows_the_date_format_preference(self):
        value = datetime(2026, 9, 15, 12, 6, 48, tzinfo=dt_timezone.utc)
        preferences = regional.Preferences(date_format="mdy", time_zone="Europe/Sofia")
        with regional.activate(preferences):
            self.assertTrue(regional.format_audit_timestamp(value).startswith("2026-09-15"))

    def test_negative_offset(self):
        value = datetime(2026, 1, 15, 12, 0, 0, tzinfo=dt_timezone.utc)
        with regional.activate(regional.Preferences(time_zone="America/New_York")):
            formatted = regional.format_audit_timestamp(value)
        self.assertTrue(formatted.endswith("-05:00"), formatted)

    def test_blank_for_missing_value(self):
        self.assertEqual(regional.format_audit_timestamp(None), "")
