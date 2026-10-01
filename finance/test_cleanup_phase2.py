"""Plan 02, Phase 2 (database constraints): payee and category names that differ only in case
are one name - in every alphabet, enforced by the database itself - plus the one-level
subcategory rule on the model, the rules the admin could break, and the dropped payee_name
column."""

from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse

from audit.models import AuditLogEntry

from .forms import CategoryForm, PayeeForm, ReminderForm
from .models import Account, Category, Payee, Reminder, Transaction, name_key

EXPENSE = Category.CategoryType.EXPENSE


class NameKeyTests(TestCase):
    def test_case_is_folded_in_every_alphabet(self):
        self.assertEqual(name_key("  LIDL "), "lidl")
        self.assertEqual(name_key("ЛИДЛ"), name_key("лидл"))
        self.assertEqual(name_key("Straße"), name_key("STRASSE"))

    def test_save_keeps_the_key_in_step_even_with_update_fields(self):
        payee = Payee.objects.create(name="Corner Shop")
        payee.name = "ÜBER Shop"
        payee.save(update_fields=["name"])
        payee.refresh_from_db()
        self.assertEqual(payee.name_key, "über shop")


class PayeeUniquenessTests(TestCase):
    """2.4 (F3): one payee per name, whatever the case."""

    def test_the_database_rejects_a_case_duplicate(self):
        Payee.objects.create(name="Lidl")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payee.objects.create(name="LIDL")

    def test_the_database_rejects_a_cyrillic_case_duplicate(self):
        # SQLite's own lower() leaves Cyrillic alone - the reason for the name_key column.
        Payee.objects.create(name="Лидл")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payee.objects.create(name="ЛИДЛ")

    def test_resolve_keeps_the_spelling_already_saved(self):
        lidl = Payee.objects.create(name="Лидл")
        self.assertEqual(Payee.objects.resolve("лидл"), lidl)
        self.assertEqual(Payee.objects.count(), 1)

    def test_system_payees_reuse_a_payee_typed_in_another_case(self):
        typed = Payee.objects.create(name="opening balance")
        self.assertEqual(Payee.system(Payee.OPENING_BALANCE), typed)

    def test_the_form_rejects_a_case_duplicate_with_a_clear_message(self):
        Payee.objects.create(name="Lidl")
        form = PayeeForm(data={"name": "lidl", "is_active": True})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["name"], ["A payee with this name already exists."])

    def test_a_payee_can_change_the_case_of_its_own_name(self):
        payee = Payee.objects.create(name="lidl")
        form = PayeeForm(data={"name": "Lidl", "is_active": True}, instance=payee)
        self.assertTrue(form.is_valid(), form.errors)


class PayeeResolutionViewTests(TestCase):
    """2.4: every way in - the transaction form, the import and autofill - finds the saved payee."""

    def setUp(self):
        user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")
        self.client.force_login(user)
        self.account = Account.objects.create(name="Checking")
        self.lidl = Payee.objects.create(name="Lidl")

    def test_typing_lidl_on_a_transaction_saves_it_as_lidl(self):
        self.client.post(
            reverse("finance:transaction_add"),
            {
                "account": self.account.pk,
                "date": "05/09/2026",
                "payee_name": "lidl",
                "amount": "-12.00",
            },
        )
        txn = Transaction.objects.get(amount=Decimal("-12.00"))
        self.assertEqual(txn.payee, self.lidl)
        self.assertEqual(txn.payee.name, "Lidl")

    def test_an_import_reuses_the_saved_payee_and_logs_the_new_one_as_imported(self):
        content = b"Date,Payee,Memo,Amount\n03/09/2026,LIDL,,-5.00\n04/09/2026,Rewe,,-6.00\n"
        self.client.post(
            reverse("finance:import_transactions"),
            {"account": self.account.pk, "file": SimpleUploadedFile("bank.csv", content)},
        )
        self.assertEqual(Transaction.objects.get(amount=Decimal("-5.00")).payee, self.lidl)
        self.assertEqual(Payee.objects.count(), 2)
        rewe_row = AuditLogEntry.objects.get(table="finance_payee", action="insert", row_label="Rewe")
        self.assertEqual(rewe_row.reason, "Imported from bank.csv")

    def test_autofill_finds_the_payee_in_any_case(self):
        Transaction.objects.create(account=self.account, date=date(2026, 9, 1), payee=self.lidl, amount=Decimal("-3"))
        response = self.client.get(reverse("finance:api_payee_autofill"), {"name": "LIDL"})
        self.assertEqual(len(response.json()["combos"]), 1)


class CategoryUniquenessTests(TestCase):
    """2.3 (F3): top-level names are unique too, and both rules ignore case."""

    def test_the_database_rejects_a_second_top_level_category_in_another_case(self):
        Category.objects.create(name="Food", category_type=EXPENSE)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Category.objects.create(name="FOOD", category_type=EXPENSE)

    def test_the_database_rejects_a_case_duplicate_subcategory(self):
        food = Category.objects.create(name="Food", category_type=EXPENSE)
        Category.objects.create(name="Snacks", parent=food, category_type=EXPENSE)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Category.objects.create(name="snacks", parent=food, category_type=EXPENSE)

    def test_the_same_subcategory_name_under_two_parents_is_fine(self):
        food = Category.objects.create(name="Food", category_type=EXPENSE)
        home = Category.objects.create(name="Home", category_type=EXPENSE)
        Category.objects.create(name="Other", parent=food, category_type=EXPENSE)
        Category.objects.create(name="Other", parent=home, category_type=EXPENSE)

    def test_two_accounts_with_one_name_still_get_their_transfer_categories(self):
        Account.objects.create(name="Cash")
        Account.objects.create(name="Cash")
        self.assertEqual(Category.objects.filter(name="[Cash]").count(), 2)

    def test_the_form_rejects_a_duplicate_with_a_clear_message(self):
        Category.objects.create(name="Food", category_type=EXPENSE)
        form = CategoryForm(data={"name": "food", "category_type": EXPENSE})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["name"], ["A category with this name already exists."])

    def test_the_form_rejects_a_duplicate_subcategory(self):
        food = Category.objects.create(name="Food", category_type=EXPENSE)
        Category.objects.create(name="Snacks", parent=food, category_type=EXPENSE)
        form = CategoryForm(data={"name": "SNACKS", "parent": food.pk, "category_type": EXPENSE})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["name"], ["'Food' already has a subcategory with this name."])


class OneLevelRuleTests(TestCase):
    """2.6 (F13): the one-level subcategory rule lives on the model, so the admin keeps it too."""

    def setUp(self):
        self.food = Category.objects.create(name="Food", category_type=EXPENSE)
        self.snacks = Category.objects.create(name="Snacks", parent=self.food, category_type=EXPENSE)

    def assert_parent_error(self, category, message):
        with self.assertRaises(ValidationError) as caught:
            category.full_clean()
        self.assertEqual(caught.exception.message_dict["parent"], [message])

    def test_a_subcategory_cant_have_subcategories(self):
        self.assert_parent_error(
            Category(name="Crisps", parent=self.snacks, category_type=EXPENSE),
            "A subcategory can't have its own subcategories - pick a main category.",
        )

    def test_a_category_with_subcategories_cant_become_one(self):
        home = Category.objects.create(name="Home", category_type=EXPENSE)
        self.food.parent = home
        self.assert_parent_error(
            self.food,
            "This category already has its own subcategories, so it can't become one itself "
            "— move or remove them first.",
        )

    def test_a_category_cant_sit_under_itself(self):
        self.food.subcategories.all().delete()
        self.food.parent = self.food
        self.assert_parent_error(self.food, "A category can't be placed under itself.")

    def test_a_transfer_category_cant_be_a_parent(self):
        transfer = Account.objects.create(name="Cash").transfer_category
        self.assert_parent_error(
            Category(name="Coins", parent=transfer, category_type=EXPENSE),
            "An account's transfer category can't have subcategories.",
        )

    def test_the_category_form_shows_the_models_message(self):
        home = Category.objects.create(name="Home", category_type=EXPENSE)
        form = CategoryForm(data={"name": "Food", "parent": home.pk, "category_type": EXPENSE}, instance=self.food)
        self.assertFalse(form.is_valid())
        self.assertIn("already has its own subcategories", form.errors["parent"][0])


class AdminRuleTests(TestCase):
    """2.8 (F54): rules the forms enforce now hold for the admin and the database too."""

    def test_a_transfer_category_needs_an_account(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Category.objects.create(name="Moving money", category_type=Category.CategoryType.TRANSFER)
        with self.assertRaises(ValidationError) as caught:
            Category(name="Moving money", category_type=Category.CategoryType.TRANSFER).full_clean()
        self.assertIn("category_type", caught.exception.message_dict)

    def test_an_accounts_transfer_category_stays_a_transfer(self):
        category = Account.objects.create(name="Cash").transfer_category
        category.category_type = EXPENSE
        with self.assertRaises(IntegrityError), transaction.atomic():
            category.save()

    def test_a_reminder_amount_cant_be_negative(self):
        account = Account.objects.create(name="Checking")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Reminder.objects.create(account=account, amount=Decimal("-5"), next_due_date=date(2026, 10, 1))
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
        with self.assertRaises(IntegrityError), transaction.atomic():
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


class PayeeNameColumnTests(TestCase):
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


class AuditSkipsNameKeyTests(TestCase):
    """name_key only repeats the name, so the audit log leaves it out."""

    def test_neither_inserts_nor_renames_log_the_key(self):
        payee = Payee.objects.create(name="Lidl")
        payee.name = "Lidl Express"
        payee.save()
        rows = AuditLogEntry.objects.filter(table="finance_payee", row_id=str(payee.pk))
        self.assertNotIn("name_key", rows.get(action="insert").values)
        self.assertEqual(list(rows.filter(action="update").values_list("column", flat=True)), ["name"])
