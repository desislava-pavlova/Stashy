"""Payees: one payee per name whatever the case, what was used with each
category before, and the payee list and detail pages."""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connections, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from audit.models import AuditLogEntry

from ..forms.payees import PayeeForm
from ..models import Account, Category, Payee, Transaction, name_key, payee_summaries
from .base import (
    AMOUNTS_THAT_CANCEL_OUT,
    LoggedInTestCase,
    LoggedInWithAccountTestCase,
    StashTestCase,
    csv_upload,
    post_import,
)


class NameKeyTests(StashTestCase):
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


class PayeeIsSystemTests(StashTestCase):
    """S53: Payee.system() reuses a payee typed in another case, so "is
    it one of Stashy's own" is asked through name_key, not by exact
    spelling."""

    def test_the_system_payees_in_any_case(self):
        for system_name in Payee.SYSTEM_NAMES:
            for name in (system_name, system_name.upper(), system_name.lower()):
                with self.subTest(name=name):
                    # The three spellings share one name_key, so only
                    # one can exist at a time.
                    payee = Payee.objects.create(name=name)
                    self.assertTrue(payee.is_system)
                    payee.delete()

    def test_a_payee_that_only_looks_similar_is_not_a_system_payee(self):
        for name in ("Opening Balance Fee", "Transfer", "Лидл", "Opening  Balance"):
            with self.subTest(name=name):
                self.assertFalse(Payee.objects.create(name=name).is_system)

    def test_a_reused_system_payee_still_counts(self):
        typed = Payee.objects.create(name="opening balance")
        self.assertTrue(Payee.system(Payee.OPENING_BALANCE).is_system)
        self.assertEqual(Payee.system(Payee.OPENING_BALANCE), typed)


class PayeeDefaultOrderTests(StashTestCase):
    def test_payees_come_in_name_key_order_in_any_alphabet(self):
        # S4: plain `name` sorts "lidl" after "Zara" and a lower-case
        # Cyrillic name after a capital one.
        for name in ("Zara", "lidl", "бензин", "Аптека", "Вода"):
            Payee.objects.create(name=name)
        self.assertEqual(
            list(Payee.objects.values_list("name", flat=True)),
            ["lidl", "Zara", "Аптека", "бензин", "Вода"],
        )


class PayeeUniquenessTests(StashTestCase):
    """2.4 (F3): one payee per name, whatever the case."""

    def test_the_database_rejects_a_case_duplicate(self):
        Payee.objects.create(name="Lidl")
        with self.assertRaises(IntegrityError), transaction.atomic(using=settings.TEST_STASH):
            Payee.objects.create(name="LIDL")

    def test_the_database_rejects_a_cyrillic_case_duplicate(self):
        # SQLite's own lower() leaves Cyrillic alone - the reason for
        # the name_key column.
        Payee.objects.create(name="Лидл")
        with self.assertRaises(IntegrityError), transaction.atomic(using=settings.TEST_STASH):
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


class PayeeResolutionViewTests(StashTestCase):
    """2.4: every way in - the transaction form, the import and
    autofill - finds the saved payee."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "tester", password="not-a-real-password-123"
        )
        cls.account = Account.objects.create(name="Checking")
        cls.lidl = Payee.objects.create(name="Lidl")

    def setUp(self):
        self.client.force_login(self.user)

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
        self.assertEqual(Payee.objects.count(), 1)

    def test_a_genuinely_new_name_still_creates_a_payee(self):
        self.client.post(
            reverse("finance:transaction_add"),
            {
                "account": self.account.pk,
                "date": "05/09/2026",
                "payee_name": "Brand New Shop",
                "amount": "-5.00",
            },
        )
        self.assertTrue(Payee.objects.named("Brand New Shop").exists())
        self.assertEqual(Payee.objects.count(), 2)

    def test_an_import_reuses_the_saved_payee_and_logs_the_new_one_as_imported(self):
        content = "Date,Payee,Memo,Amount\n03/09/2026,LIDL,,-5.00\n04/09/2026,Rewe,,-6.00\n"
        post_import(self.client, self.account, csv_upload(content))
        self.assertEqual(Transaction.objects.get(amount=Decimal("-5.00")).payee, self.lidl)
        self.assertEqual(Payee.objects.count(), 2)
        rewe_row = AuditLogEntry.objects.get(
            table="finance_payee", action="insert", row_label="Rewe"
        )
        self.assertEqual(rewe_row.reason, "Imported from bank.csv")

    def test_autofill_finds_the_payee_in_any_case(self):
        Transaction.objects.create(
            account=self.account, date=date(2026, 9, 1), payee=self.lidl, amount=Decimal("-3")
        )
        response = self.client.get(reverse("finance:api_payee_autofill"), {"name": "LIDL"})
        self.assertEqual(len(response.json()["combos"]), 1)


class PayeeHistoryTests(StashTestCase):
    """Payee memory is computed live from transaction history, per
    category — there's no stored default to fall out of sync."""

    @classmethod
    def setUpTestData(cls):
        cls.account = Account.objects.create(name="Checking")
        cls.groceries = Category.objects.create(name="Groceries")
        cls.household = Category.objects.create(name="Household")
        cls.payee = Payee.objects.create(name="Lidl")

    def test_one_transaction_gives_one_history_entry(self):
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 5),
            payee=self.payee,
            category=self.groceries,
            memo="weekly shop",
            amount=Decimal("-30.00"),
        )
        history = self.payee.category_history()
        self.assertEqual(len(history), 1)
        self.assertEqual((history[0].category, history[0].memo), (self.groceries, "weekly shop"))

    def test_two_categories_give_two_entries_newest_first(self):
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 5),
            payee=self.payee,
            category=self.groceries,
            memo="weekly shop",
            amount=Decimal("-30.00"),
        )
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 10),
            payee=self.payee,
            category=self.household,
            memo="cleaning supplies",
            amount=Decimal("-12.00"),
        )
        history = self.payee.category_history()
        self.assertEqual([h.category for h in history], [self.household, self.groceries])

    def test_history_reflects_the_latest_by_date_not_by_entry_order(self):
        # Entered second, but dated earlier - shouldn't override the
        # later-dated one.
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 20),
            payee=self.payee,
            category=self.groceries,
            memo="second shop",
            amount=Decimal("-40.00"),
        )
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 5),
            payee=self.payee,
            category=self.groceries,
            memo="first shop",
            amount=Decimal("-30.00"),
        )
        history = self.payee.category_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].memo, "second shop")

    def test_editing_an_unrelated_field_does_not_change_the_history(self):
        txn = Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 5),
            payee=self.payee,
            category=self.groceries,
            memo="weekly shop",
            amount=Decimal("-30.00"),
        )
        txn.reconciled = True
        txn.save()
        self.assertEqual(self.payee.category_history()[0].memo, "weekly shop")

    def test_uncategorized_transactions_form_their_own_entry(self):
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 5),
            payee=self.payee,
            amount=Decimal("-10.00"),
        )
        history = self.payee.category_history()
        self.assertEqual(len(history), 1)
        self.assertIsNone(history[0].category)

    def test_the_latest_category_is_the_first_history_entrys_in_every_case(self):
        """S65: an import asks for it on every row, so it reads one
        transaction, and must answer what the whole history's first
        entry would."""
        self.assertIsNone(self.payee.latest_category())  # no transactions
        steps = [
            (date(2026, 1, 5), self.groceries, self.groceries),
            (date(2026, 1, 10), self.household, self.household),  # newer date wins
            (date(2026, 1, 7), self.groceries, self.household),  # entered last, dated earlier
            (date(2026, 1, 12), None, None),  # the newest has no category
        ]
        for when, category, expected in steps:
            Transaction.objects.create(
                account=self.account,
                date=when,
                payee=self.payee,
                category=category,
                amount=Decimal("-10.00"),
            )
            with self.subTest(date=when):
                self.assertEqual(self.payee.latest_category(), expected)
                self.assertEqual(
                    self.payee.latest_category(), self.payee.category_history()[0].category
                )


class PayeeDetailTests(LoggedInWithAccountTestCase):
    def test_shows_every_category_with_its_last_memo_and_amount(self):
        payee = Payee.objects.create(name="Lidl")
        groceries = Category.objects.create(name="Groceries")
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 5),
            payee=payee,
            category=groceries,
            memo="weekly shop",
            amount=Decimal("-30.00"),
        )
        response = self.client.get(reverse("finance:payee_detail", args=[payee.pk]))
        self.assertContains(response, "weekly shop")
        self.assertContains(response, "Groceries")
        self.assertEqual(len(response.context["history"]), 1)

    def test_empty_history_shows_an_empty_state(self):
        payee = Payee.objects.create(name="New Shop")
        response = self.client.get(reverse("finance:payee_detail", args=[payee.pk]))
        self.assertContains(response, "No transactions yet")


class PayeeListFilterAndSortTests(LoggedInWithAccountTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.groceries = Category.objects.create(name="Groceries")
        cls.household = Category.objects.create(name="Household")
        cls.lidl = Payee.objects.create(name="Lidl")
        cls.ikea = Payee.objects.create(name="Ikea")
        Transaction.objects.create(
            account=cls.account,
            date=date(2026, 1, 5),
            payee=cls.lidl,
            category=cls.groceries,
            memo="weekly shop",
            amount=Decimal("-30.00"),
        )
        Transaction.objects.create(
            account=cls.account,
            date=date(2026, 1, 6),
            payee=cls.ikea,
            category=cls.household,
            memo="shelves",
            amount=Decimal("-90.00"),
        )

    def rows(self, **params):
        response = self.client.get(reverse("finance:payee_list"), params)
        return response.context["rows"]

    def test_default_sort_is_by_name(self):
        names = [r["payee"].name for r in self.rows()]
        self.assertEqual(names, ["Ikea", "Lidl"])

    def test_the_name_sort_follows_the_database_order_for_letters_lower_does_not_fold(self):
        """S109: "Straße" folds to "strasse", so it comes before
        "Strasse2", as the database's `name_key` order has it; sorting
        by `.lower()` put it after."""
        Payee.objects.create(name="Strasse2")
        Payee.objects.create(name="Straße")

        names = [r["payee"].name for r in self.rows()]

        self.assertEqual(names, ["Ikea", "Lidl", "Straße", "Strasse2"])
        self.assertEqual(names, list(Payee.objects.values_list("name", flat=True)))

    def test_the_payee_header_shows_the_arrow_without_a_sort_in_the_url(self):
        # The list is sorted by name even without ?sort=, so "Payee"
        # shows the arrow from the start.
        response = self.client.get(reverse("finance:payee_list"))
        self.assertRegex(response.content.decode(), r"Payee\s*<i [^>]*caret-up-fill")

    def test_an_unknown_sort_falls_back_to_name_and_shows_its_arrow(self):
        # S69: it sorted by name already, but kept the unknown value, so
        # no header showed an arrow and the first click on "Payee"
        # (which asks for sort=name) seemed to do nothing.
        for sort in ("nonsense", "-nonsense", "-"):
            with self.subTest(sort=sort):
                response = self.client.get(reverse("finance:payee_list"), {"sort": sort})
                self.assertEqual(response.context["sort"], "name")
                self.assertEqual(
                    [r["payee"].name for r in response.context["rows"]], ["Ikea", "Lidl"]
                )
                self.assertRegex(response.content.decode(), r"Payee\s*<i [^>]*caret-up-fill")

    def test_each_header_asks_for_the_opposite_of_the_sort_in_use(self):
        # No ?sort= at all is a sort by name, so Payee's link flips it.
        for current, name_next in (
            ("", "-name"),
            ("name", "-name"),
            ("-name", "name"),
            ("usage", "name"),
        ):
            with self.subTest(sort=current):
                params = {"sort": current} if current else {}
                response = self.client.get(reverse("finance:payee_list"), params)
                self.assertEqual(
                    response.context["sort_next"],
                    {
                        "name": name_next,
                        "category": "category",
                        "amount": "amount",
                        "usage": "-usage" if current == "usage" else "usage",
                    },
                )

    def test_a_category_filter_that_is_not_a_number_is_ignored(self):
        # S2's family: "²" passes str.isdigit() but int() refuses it,
        # which used to be a 500 page.
        for bad in ("abc", "²", "-1", "9" * 30):
            with self.subTest(category=bad):
                self.assertEqual(
                    {r["payee"].name for r in self.rows(category=bad)}, {"Ikea", "Lidl"}
                )

    def test_search_by_name(self):
        names = [r["payee"].name for r in self.rows(q="lid")]
        self.assertEqual(names, ["Lidl"])

    def test_filter_by_category(self):
        names = [r["payee"].name for r in self.rows(category=self.household.pk)]
        self.assertEqual(names, ["Ikea"])

    def test_filter_by_uncategorized(self):
        Payee.objects.create(name="No Category Yet")
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 7),
            payee=Payee.objects.get(name="No Category Yet"),
            amount=Decimal("-5.00"),
        )
        names = [r["payee"].name for r in self.rows(category="none")]
        self.assertEqual(names, ["No Category Yet"])

    def test_filter_by_memo(self):
        names = [r["payee"].name for r in self.rows(memo="shelves")]
        self.assertEqual(names, ["Ikea"])

    def test_sort_by_amount(self):
        # Ascending: Ikea's -90.00 sorts before Lidl's -30.00.
        names = [r["payee"].name for r in self.rows(sort="amount")]
        self.assertEqual(names, ["Ikea", "Lidl"])

    def test_sort_by_usage(self):
        # Both have 1 use each until a second Lidl transaction is
        # added - lets least-used payees sort first, for spotting ones
        # worth deleting.
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 2, 1),
            payee=self.lidl,
            category=self.groceries,
            memo="second visit",
            amount=Decimal("-10.00"),
        )
        names = [r["payee"].name for r in self.rows(sort="usage")]
        self.assertEqual(names, ["Ikea", "Lidl"])

    def test_each_row_shows_all_categories_total_amount_and_usage(self):
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 2, 1),
            payee=self.lidl,
            category=self.household,
            memo="one-off",
            amount=Decimal("-15.00"),
        )
        row = next(r for r in self.rows() if r["payee"] == self.lidl)
        self.assertEqual(row["categories_label"], "Groceries, Household")
        self.assertEqual(row["totals"], [("EUR", Decimal("-45.00"))])
        self.assertEqual(row["usage_count"], 2)


class PayeeSummariesTests(StashTestCase):
    """Plan 03, S66: the grouping and totals behind the payee list,
    asked of the database once for the whole list."""

    @classmethod
    def setUpTestData(cls):
        cls.euros = Account.objects.create(name="Euros", currency="EUR")
        cls.dollars = Account.objects.create(name="Dollars", currency="USD")
        cls.groceries = Category.objects.create(name="Groceries")
        cls.supermarket = cls.groceries.create_subcategory("Supermarket")

    def spend(self, payee, amount, account=None, category=None):
        return Transaction.objects.create(
            account=account or self.euros,
            date=date(2026, 1, 5),
            payee=payee,
            category=category,
            amount=Decimal(amount),
        )

    def test_a_payee_with_no_transactions_has_an_empty_summary(self):
        payee = Payee.objects.create(name="Never Used")

        (summary,) = payee_summaries([payee])

        self.assertEqual(summary["payee"], payee)
        self.assertEqual(summary["categories"], [])
        self.assertEqual(summary["categories_label"], "")
        self.assertEqual(summary["category_count"], 0)
        self.assertEqual(summary["totals"], [])
        self.assertEqual(summary["total_sort_value"], Decimal("0"))
        self.assertEqual(summary["usage_count"], 0)

    def test_a_subcategory_reads_parent_then_child_and_no_category_reads_uncategorized(self):
        payee = Payee.objects.create(name="Lidl")
        self.spend(payee, "-1.00", category=self.supermarket)
        self.spend(payee, "-2.00", category=self.groceries)
        self.spend(payee, "-3.00", category=None)

        (summary,) = payee_summaries([payee])

        self.assertEqual(
            summary["categories"], ["Groceries", "Groceries > Supermarket", "Uncategorized"]
        )
        self.assertEqual(summary["category_count"], 3)

    def test_totals_are_per_currency_and_usage_counts_every_transaction(self):
        payee = Payee.objects.create(name="Lidl")
        self.spend(payee, "-10.00")
        self.spend(payee, "-5.00")
        self.spend(payee, "-3.00", account=self.dollars)

        (summary,) = payee_summaries([payee])

        self.assertEqual(
            summary["totals"], [("EUR", Decimal("-15.00")), ("USD", Decimal("-3.00"))]
        )
        self.assertEqual(summary["total_sort_value"], Decimal("-18.00"))
        self.assertEqual(summary["usage_count"], 3)

    def test_each_payee_gets_only_its_own_figures_in_the_order_given(self):
        lidl = Payee.objects.create(name="Lidl")
        ikea = Payee.objects.create(name="Ikea")
        self.spend(lidl, "-10.00", category=self.groceries)
        self.spend(ikea, "-90.00")

        summaries = payee_summaries([ikea, lidl])

        self.assertEqual([s["payee"] for s in summaries], [ikea, lidl])
        self.assertEqual([s["usage_count"] for s in summaries], [1, 1])
        self.assertEqual([s["categories"] for s in summaries], [["Uncategorized"], ["Groceries"]])
        self.assertEqual(
            [s["total_sort_value"] for s in summaries], [Decimal("-90.00"), Decimal("-10.00")]
        )

    def test_the_number_of_queries_does_not_grow_with_the_payees(self):
        payees = []
        for number in range(6):
            payee = Payee.objects.create(name=f"Shop {number}")
            self.spend(payee, "-1.00", category=self.groceries)
            payees.append(payee)

        def queries_for(chosen):
            with CaptureQueriesContext(connections[settings.TEST_STASH]) as queries:
                payee_summaries(chosen)
            return len(queries)

        self.assertEqual(queries_for(payees[:1]), queries_for(payees))


class PayeeListDistinctCategoriesTests(LoggedInWithAccountTestCase):
    def test_the_same_category_used_many_times_lists_once(self):
        # This is a regression test for a real bug: Transaction's
        # default ordering was riding along into a values().distinct()
        # query, so every date a category was used counted as
        # "different" - the exact category showed up once per
        # transaction, not once overall.
        water = Category.objects.create(name="Water", category_type=Category.CategoryType.EXPENSE)
        payee = Payee.objects.create(name="City Water Co")
        for day in range(1, 9):
            Transaction.objects.create(
                account=self.account,
                date=date(2026, day, 1),
                payee=payee,
                category=water,
                amount=Decimal("-5.00"),
            )
        response = self.client.get(reverse("finance:payee_list"))
        row = next(r for r in response.context["rows"] if r["payee"] == payee)
        self.assertEqual(row["categories"], ["Water"])

    def test_two_real_categories_still_both_show(self):
        food = Category.objects.create(name="Food", category_type=Category.CategoryType.EXPENSE)
        fuel = Category.objects.create(name="Fuel", category_type=Category.CategoryType.EXPENSE)
        payee = Payee.objects.create(name="Shop")
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 1),
            payee=payee,
            category=food,
            amount=Decimal("-5.00"),
        )
        Transaction.objects.create(
            account=self.account,
            date=date(2026, 1, 2),
            payee=payee,
            category=fuel,
            amount=Decimal("-5.00"),
        )
        response = self.client.get(reverse("finance:payee_list"))
        row = next(r for r in response.context["rows"] if r["payee"] == payee)
        self.assertEqual(row["categories"], ["Food", "Fuel"])


class PayeeListCategoryOrderTests(LoggedInTestCase):
    def test_category_filter_is_ordered_like_other_category_dropdowns(self):
        groceries = Category.objects.create(
            name="Groceries", category_type=Category.CategoryType.EXPENSE
        )
        Category.objects.create(
            name="Supermarket", parent=groceries, category_type=Category.CategoryType.EXPENSE
        )
        Category.objects.create(name="Bakery", category_type=Category.CategoryType.EXPENSE)
        response = self.client.get(reverse("finance:payee_list"))
        # "Bakery" sorts before "Groceries", and "Supermarket"
        # (indented) sits right after "Groceries" rather than in a
        # separate block of subcategories.
        body = response.content.decode().replace("&nbsp;", " ")
        self.assertLess(body.index("Bakery"), body.index(">Groceries<"))
        self.assertLess(body.index(">Groceries<"), body.index(">    Supermarket<"))


class PayeeTotalRoundingTests(StashTestCase):
    def test_amounts_that_cancel_out_total_exactly_zero(self):
        account = Account.objects.create(name="Checking")
        payee = Payee.objects.create(name="Lidl")
        for amount in AMOUNTS_THAT_CANCEL_OUT:
            Transaction.objects.create(
                account=account, date=date(2026, 1, 5), amount=Decimal(amount), payee=payee
            )
        [summary] = payee_summaries([payee])
        self.assertEqual(
            [(currency, str(total)) for currency, total in summary["totals"]],
            [(account.currency, "0.00")],
        )

    def test_a_total_that_is_not_zero_has_exactly_two_decimals(self):
        # Added as floats these come to 0.6000000000000001.
        account = Account.objects.create(name="Checking")
        payee = Payee.objects.create(name="Lidl")
        for amount in ("0.10", "0.20", "0.30"):
            Transaction.objects.create(
                account=account, date=date(2026, 1, 5), amount=Decimal(amount), payee=payee
            )
        [summary] = payee_summaries([payee])
        self.assertEqual(
            [(currency, str(total)) for currency, total in summary["totals"]],
            [(account.currency, "0.60")],
        )

    def test_each_currency_is_rounded_on_its_own(self):
        euro = Account.objects.create(name="Checking")
        dollar = Account.objects.create(name="US card", currency="USD")
        payee = Payee.objects.create(name="Lidl")
        for account, amount in (
            (euro, "-0.10"),
            (euro, "-0.20"),
            (euro, "0.30"),
            (dollar, "-1.10"),
            (dollar, "-2.20"),
        ):
            Transaction.objects.create(
                account=account, date=date(2026, 1, 5), amount=Decimal(amount), payee=payee
            )
        [summary] = payee_summaries([payee])
        self.assertEqual(
            [(currency, str(total)) for currency, total in summary["totals"]],
            [("EUR", "0.00"), ("USD", "-3.30")],
        )

    def test_a_payee_with_no_transactions_has_no_totals(self):
        [summary] = payee_summaries([Payee.objects.create(name="Unused")])
        self.assertEqual(summary["totals"], [])


class RememberedMemoTests(StashTestCase):
    """The memo a payee had the last time it was filed under a
    category: what entering a reminder starts the memo with."""

    @classmethod
    def setUpTestData(cls):
        account = Account.objects.create(name="Checking")
        cls.payee = Payee.objects.create(name="Lidl")
        cls.food = Category.objects.create(name="Food", category_type="expense")
        cls.fuel = Category.objects.create(name="Fuel", category_type="expense")
        cls.other = Category.objects.create(name="Other", category_type="expense")
        for on, category, memo in (
            (date(2026, 1, 5), cls.food, "old shop"),
            (date(2026, 2, 5), cls.food, "new shop"),
            (date(2026, 3, 1), cls.fuel, "diesel"),
        ):
            Transaction.objects.create(
                account=account,
                date=on,
                amount=Decimal("-5.00"),
                payee=cls.payee,
                category=category,
                memo=memo,
            )

    def test_the_latest_memo_in_that_category_wins(self):
        self.assertEqual(self.payee.remembered_memo(self.food.pk), "new shop")

    def test_each_category_has_its_own(self):
        self.assertEqual(self.payee.remembered_memo(self.fuel.pk), "diesel")

    def test_a_category_never_used_with_the_payee_has_none(self):
        self.assertEqual(self.payee.remembered_memo(self.other.pk), "")

    def test_a_payee_with_no_transactions_remembers_nothing(self):
        self.assertEqual(Payee.objects.create(name="New").remembered_memo(self.food.pk), "")

    def test_a_memo_belongs_to_the_payee_it_was_written_for(self):
        other_payee = Payee.objects.create(name="Aldi")
        self.assertEqual(other_payee.remembered_memo(self.food.pk), "")

    def test_no_category_means_the_payees_uncategorized_transactions(self):
        # None is not "any category": it is the group of transactions
        # with none (what category_history() groups under None too).
        self.assertEqual(self.payee.remembered_memo(None), "")
        Transaction.objects.create(
            account=Account.objects.get(),
            date=date(2026, 4, 1),
            amount=Decimal("-5.00"),
            payee=self.payee,
            memo="no category",
        )
        self.assertEqual(self.payee.remembered_memo(None), "no category")
        self.assertEqual(self.payee.remembered_memo(self.food.pk), "new shop")

    def test_the_latest_date_wins_not_the_one_entered_last(self):
        Transaction.objects.create(
            account=Account.objects.get(),
            date=date(2025, 6, 1),
            amount=Decimal("-5.00"),
            payee=self.payee,
            category=self.fuel,
            memo="entered late, dated early",
        )
        self.assertEqual(self.payee.remembered_memo(self.fuel.pk), "diesel")

    def test_a_blank_latest_memo_is_what_is_remembered(self):
        # Not the last memo that had words in it.
        Transaction.objects.create(
            account=Account.objects.get(),
            date=date(2026, 5, 1),
            amount=Decimal("-5.00"),
            payee=self.payee,
            category=self.food,
            memo="",
        )
        self.assertEqual(self.payee.remembered_memo(self.food.pk), "")
