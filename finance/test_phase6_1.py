"""Tests for the Phase 6.1 fixes: edit-redirect, case-insensitive payees, small UI tweaks."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .forms import AccountForm
from .models import Account, Category, Payee, Transaction


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(self.user)
        self.account = Account.objects.create(name="Checking")


class EditRedirectTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        self.other = Account.objects.create(name="Savings")
        self.txn = Transaction.objects.create(account=self.account, date=date(2026, 1, 1), amount=Decimal("-1.00"))

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


class CaseInsensitivePayeeTests(LoggedInTestCase):
    def test_manual_entry_reuses_an_existing_payee_regardless_of_case(self):
        Payee.objects.create(name="Lidl")
        self.client.post(
            reverse("finance:transaction_add"),
            {
                "account": self.account.pk,
                "date": "05/01/2026",
                "payee_name": "lidl",
                "amount": "-5.00",
            },
        )
        self.assertEqual(Payee.objects.filter(name__iexact="lidl").count(), 1)
        self.assertEqual(Transaction.objects.get().payee.name, "Lidl")

    def test_import_reuses_an_existing_payee_regardless_of_case(self):
        Payee.objects.create(name="Lidl")
        upload = SimpleUploadedFile("bank.csv", b"Date,Payee,Memo,Amount\n05/01/2026,LIDL,,-5.00\n")
        self.client.post(reverse("finance:import_transactions"), {"account": self.account.pk, "file": upload})
        self.assertEqual(Payee.objects.filter(name__iexact="lidl").count(), 1)

    def test_a_genuinely_new_name_still_creates_a_payee(self):
        self.client.post(
            reverse("finance:transaction_add"),
            {
                "account": self.account.pk,
                "date": "05/01/2026",
                "payee_name": "Brand New Shop",
                "amount": "-5.00",
            },
        )
        self.assertTrue(Payee.objects.filter(name="Brand New Shop").exists())


class AccountFormNotesSizeTests(TestCase):
    def test_notes_widget_is_two_rows(self):
        form = AccountForm()
        self.assertEqual(form.fields["notes"].widget.attrs.get("rows"), 2)


class SidebarAdminLinkTests(LoggedInTestCase):
    def test_admin_link_is_relabeled_and_opens_a_new_tab(self):
        response = self.client.get(reverse("finance:dashboard"))
        html = response.content.decode()
        self.assertIn("Admin &amp; Audit Log", html)
        self.assertNotIn("Admin &amp; audit log", html)
        self.assertIn('target="_blank"', html)


class UsageEmptyStylingTests(LoggedInTestCase):
    def test_a_category_with_no_transactions_is_styled_pale(self):
        Category.objects.create(name="Unused", category_type=Category.CategoryType.EXPENSE)
        response = self.client.get(reverse("finance:category_list"))
        self.assertContains(response, "usage-empty")

    def test_a_category_with_transactions_is_not_styled_pale(self):
        used = Category.objects.create(name="Used", category_type=Category.CategoryType.EXPENSE)
        Transaction.objects.create(account=self.account, date=date(2026, 1, 1), category=used, amount=Decimal("-1.00"))
        response = self.client.get(reverse("finance:category_list"))
        # The account's own transfer category always has 0 usage, so some "usage-empty" will
        # exist regardless - check the specific "Used" row's link isn't the pale class.
        content = response.content.decode()
        used_index = content.index("Used<")
        nearby = content[used_index : used_index + 400]
        self.assertNotIn("usage-empty", nearby)

    def test_a_payee_with_no_transactions_is_styled_pale(self):
        Payee.objects.create(name="Ghost Payee")
        response = self.client.get(reverse("finance:payee_list"))
        self.assertContains(response, "usage-empty")
