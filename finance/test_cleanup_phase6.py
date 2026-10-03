"""Plan 02, Phase 6 (side effects and boundaries): saves that run their side effect only when it
matters, the audit log's own suspend switch, the router's ContextVar, the stashes package split,
one PIN rule, the audit app standing on its own, one rule for a new subcategory's type, and
imports without cycles."""

import ast
import tempfile
import threading
from datetime import date
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import signals as model_signals
from django.test import SimpleTestCase, TestCase

from audit import signals as audit_signals
from audit.context import audit_suspended
from audit.models import AuditLogEntry

from . import db_router, starter_categories
from .auth_forms import ChangePinForm, ForgotPinForm, StashCreateForm
from .models import Account, Category, Profile, Reminder
from .services.importing import _resolve_category
from .stashes import StashError, registry, security
from .test_cleanup_phase1 import TemporaryStashesMixin


class AccountSaveTests(TestCase):
    """6.1 (F12): only a new account or a new name touches its transfer category."""

    def setUp(self):
        self.account = Account.objects.create(name="Wallet")

    def test_a_new_account_gets_its_transfer_category(self):
        self.assertEqual(self.account.transfer_category.name, "[Wallet]")

    def test_an_update_fields_save_without_the_name_skips_the_transfer_category(self):
        self.account.is_active = False
        with mock.patch.object(Account, "ensure_transfer_category") as ensure:
            self.account.save(update_fields=["is_active"])
        ensure.assert_not_called()

    def test_a_full_save_with_the_same_name_skips_it(self):
        account = Account.objects.get(pk=self.account.pk)
        account.notes = "Pocket money"
        with mock.patch.object(Account, "ensure_transfer_category") as ensure:
            account.save()
        ensure.assert_not_called()

    def test_a_rename_renames_the_transfer_category(self):
        account = Account.objects.get(pk=self.account.pk)
        account.name = "Piggy bank"
        account.save()
        self.assertEqual(Category.objects.get(account=account).name, "[Piggy bank]")

    def test_a_rename_through_update_fields_renames_it_too(self):
        self.account.name = "Piggy bank"
        self.account.save(update_fields=["name"])
        self.assertEqual(Category.objects.get(account=self.account).name, "[Piggy bank]")

    def test_a_second_save_after_a_rename_skips_it(self):
        self.account.name = "Piggy bank"
        self.account.save()
        with mock.patch.object(Account, "ensure_transfer_category") as ensure:
            self.account.save()
        ensure.assert_not_called()


class ReminderSaveTests(TestCase):
    """6.2 (F12): a save regenerates occurrences only when it can move a date."""

    def setUp(self):
        account = Account.objects.create(name="Wallet")
        self.reminder = Reminder.objects.create(
            account=account, frequency=Reminder.Frequency.MONTHLY, next_due_date=date(2026, 10, 15)
        )

    def _saved(self, **kwargs):
        with mock.patch.object(Reminder, "regenerate_occurrences") as regenerate:
            self.reminder.save(**kwargs)
        return regenerate.called

    def test_an_update_fields_save_outside_the_schedule_does_not_regenerate(self):
        self.assertFalse(self._saved(update_fields=["notes"]))
        self.assertFalse(self._saved(update_fields=["last_entered_transaction"]))

    def test_saving_any_schedule_field_regenerates(self):
        for field in Reminder.SCHEDULE_FIELDS:
            with self.subTest(field=field):
                self.assertTrue(self._saved(update_fields=[field, "notes"]))

    def test_a_full_save_regenerates(self):
        self.assertTrue(self._saved())

    def test_a_one_time_reminder_never_regenerates(self):
        self.reminder.frequency = Reminder.Frequency.ONE_TIME
        self.assertFalse(self._saved())


class AuditSuspendedTests(TestCase):
    """6.3 (F17): the audit app owns its own off switch, and it affects only this thread."""

    def test_nothing_is_logged_inside_the_block(self):
        before = AuditLogEntry.objects.count()
        with audit_suspended():
            account = Account.objects.create(name="Wallet")
            account.notes = "Pocket money"
            account.save()
            account.delete()
        self.assertEqual(AuditLogEntry.objects.count(), before)

    def test_logging_comes_back_after_the_block(self):
        with audit_suspended():
            pass
        before = AuditLogEntry.objects.count()
        Account.objects.create(name="Wallet")
        self.assertGreater(AuditLogEntry.objects.count(), before)

    def test_the_signal_handlers_stay_connected(self):
        with audit_suspended():
            receivers = str(model_signals.post_save.receivers)
        self.assertIn("audit.post_save", receivers)

    def test_other_threads_keep_logging(self):
        seen = {}
        with audit_suspended():
            self.assertFalse(audit_signals._logging(Account))
            thread = threading.Thread(target=lambda: seen.update(other=audit_signals._logging(Account)))
            thread.start()
            thread.join()
        self.assertTrue(seen["other"])


class RouterContextVarTests(SimpleTestCase):
    """6.4 (F18): the current stash is private to its own thread."""

    def tearDown(self):
        db_router.clear_current_stash_alias()

    def test_another_thread_does_not_see_this_threads_stash(self):
        db_router.set_current_stash_alias("alpha")
        seen = {}
        thread = threading.Thread(target=lambda: seen.update(other=db_router.current_stash_alias()))
        thread.start()
        thread.join()
        self.assertEqual(db_router.current_stash_alias(), "alpha")
        self.assertIsNone(seen["other"])

    def test_clearing_goes_back_to_no_stash(self):
        db_router.set_current_stash_alias("alpha")
        db_router.clear_current_stash_alias()
        self.assertIsNone(db_router.current_stash_alias())


class CreateAndResetStashTests(TemporaryStashesMixin, SimpleTestCase):
    """6.5 (F14): creating a stash and the reset_pin last resort, on a real temporary stash."""

    def test_create_then_reset_the_pin(self):
        self.make_stashes()
        # create_stash() registers the alias itself, so the test allows it up front.
        type(self).databases |= {"the-smiths"}
        folder, code = registry.create_stash("The Smiths", "1234")
        self.addCleanup(self._unregister, folder)
        self.assertEqual(folder, "the-smiths")
        self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")

        user = get_user_model().objects.db_manager(folder).get()
        profile = Profile.objects.using(folder).get()
        self.assertTrue(user.check_password("1234"))
        self.assertEqual(profile.owner_name, "The Smiths")
        self.assertTrue(security._check(code, profile.recovery_code_hash))
        # A brand new stash starts with an empty audit log.
        self.assertEqual(AuditLogEntry.objects.using(folder).count(), 0)
        self.assertEqual(registry.stash_choices(), [("the-smiths", "The Smiths")])

        Profile.objects.using(folder).update(failed_pin_attempts=3)
        registry.reset_pin_directly("the smiths", "5678")
        user.refresh_from_db()
        self.assertTrue(user.check_password("5678"))
        self.assertEqual(Profile.objects.using(folder).get().failed_pin_attempts, 0)

    def test_reset_rejects_a_bad_pin_and_an_unknown_stash(self):
        self.make_stashes()
        with self.assertRaisesMessage(StashError, "PIN must be exactly 4 digits."):
            registry.reset_pin_directly("anyone", "12")
        with self.assertRaisesMessage(StashError, 'No stash named "nobody".'):
            registry.reset_pin_directly("nobody", "1234")


class PinRuleTests(SimpleTestCase):
    """6.7 (F35): one PIN rule and one "PINs don't match" check for every form."""

    BAD_PINS = ["123", "12345", "12a4", "١٢٣٤"]

    def test_validate_pin_uses_the_same_rule(self):
        security.validate_pin("0000")
        # The forms strip spaces and line breaks first; validate_pin() itself takes none.
        for bad in [*self.BAD_PINS, "1234\n", "", None]:
            with self.subTest(pin=bad), self.assertRaisesMessage(StashError, "PIN must be exactly 4 digits."):
                security.validate_pin(bad)

    def _form_errors(self, form_class, data):
        form = form_class(data=data)
        with mock.patch.object(registry, "owner_name_taken", return_value=False):
            form.is_valid()
        return form.errors

    def test_every_pin_box_gives_the_one_message(self):
        cases = [
            (StashCreateForm, {"owner_name": "Alex"}, "pin"),
            (ForgotPinForm, {"recovery_code": "ABCD-EFGH-JKLM"}, "new_pin"),
        ]
        for form_class, data, field in cases:
            for bad in self.BAD_PINS:
                with self.subTest(form=form_class.__name__, pin=bad):
                    errors = self._form_errors(form_class, {**data, field: bad, f"{field}_confirm": bad})
                    self.assertEqual(errors[field], ["PIN must be exactly 4 digits."])

    def test_a_mismatched_confirmation_is_reported_on_the_second_box(self):
        cases = [
            (StashCreateForm, {"owner_name": "Alex", "pin": "1234", "pin_confirm": "4321"}, "pin_confirm"),
            (ForgotPinForm, {"recovery_code": "X", "new_pin": "1234", "new_pin_confirm": "4321"}, "new_pin_confirm"),
        ]
        for form_class, data, field in cases:
            with self.subTest(form=form_class.__name__):
                self.assertEqual(self._form_errors(form_class, data)[field], ["PINs don't match."])

    def test_change_pin_form_checks_the_current_pin_and_the_confirmation(self):
        user = get_user_model()(username="alex")
        user.set_password("1111")
        form = ChangePinForm(
            data={"current_pin": "1111", "new_pin": "1234", "new_pin_confirm": "4321"},
            user=user,
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {"new_pin_confirm": ["PINs don't match."]})


class AuditStandsAloneTests(SimpleTestCase):
    """6.8 (F36): the audit app imports nothing from the finance app."""

    def test_no_audit_module_imports_finance(self):
        audit_dir = Path(settings.BASE_DIR) / "audit"
        for path in audit_dir.rglob("*.py"):
            if path.name == "tests.py" or "migrations" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                elif isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                else:
                    continue
                for name in names:
                    with self.subTest(file=path.name, module=name):
                        self.assertFalse(name == "finance" or name.startswith("finance."))


class NewSubcategoryTypeTests(TestCase):
    """6.9 (F38): a new subcategory takes its main category's type, wherever it's created."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "starter_categories.xlsx"
        patcher = mock.patch.object(starter_categories, "starter_categories_path", return_value=self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, rows):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Extended"
        sheet.append(["Type", "Category", "Subcategory"])
        for row in rows:
            sheet.append(row)
        workbook.save(self.path)

    def test_starter_subcategory_follows_its_main_category_not_its_row(self):
        self._write([("Expense", "Gifts", None), ("Income", "Gifts", "Birthday money")])
        starter_categories.add_starter_categories("Extended")
        child = Category.objects.get(name="Birthday money")
        self.assertEqual(child.category_type, Category.CategoryType.EXPENSE)

    def test_starter_never_puts_a_subcategory_under_a_transfer_category(self):
        account = Account.objects.create(name="Wallet")
        self._write([("Expense", "[Wallet]", "Snacks")])
        starter_categories.add_starter_categories("Extended")
        self.assertFalse(account.transfer_category.subcategories.exists())
        main = Category.objects.main_categories().get(name="[Wallet]")
        self.assertEqual(main.subcategories.get().name, "Snacks")

    def test_importer_uses_the_same_rule(self):
        Category.objects.create(name="Gifts", category_type=Category.CategoryType.EXPENSE)
        child = _resolve_category("Gifts:Birthday money", amount=50)
        self.assertEqual(child.category_type, Category.CategoryType.EXPENSE)
        self.assertTrue(child.is_auto_generated)


class NoHiddenImportCyclesTests(SimpleTestCase):
    """6.10 (F50): every import that stays inside a function says why."""

    def test_every_function_level_import_has_a_reason(self):
        root = Path(settings.BASE_DIR)
        for folder in ("finance", "audit"):
            for path in (root / folder).rglob("*.py"):
                if path.name.startswith("test") or "migrations" in path.parts:
                    continue
                lines = path.read_text(encoding="utf-8").splitlines()
                tree = ast.parse("\n".join(lines))
                for function in ast.walk(tree):
                    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    for node in function.body:
                        if not isinstance(node, (ast.Import, ast.ImportFrom)):
                            continue
                        comments_above = []
                        index = node.lineno - 2
                        while index >= 0 and lines[index].strip().startswith("#"):
                            comments_above.append(lines[index].strip())
                            index -= 1
                        with self.subTest(file=str(path.relative_to(root)), line=node.lineno):
                            self.assertTrue(any(c.startswith("# Imported here") for c in comments_above))
