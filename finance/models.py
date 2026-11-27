"""Stashy's database tables, and the rules that belong with the data.

Each rule lives on the model so that every way in gets it: `clean()`
holds the checks a form or the admin shows, and `save()` keeps the
hidden columns in step (`name_key`, a recurring reminder's
occurrences). A model never says which stash it is in: `db_router.py`
sends the query to the active one. Report maths doesn't belong here;
it lives in `reporting.py`. (`grouped_accounts()`,
`grouped_categories()` and `payee_summaries()` only shape a model's
own rows for a list page.)
"""

import contextlib
from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, router
from django.db.models import Case, Count, F, Max, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from django.db.transaction import atomic
from django.urls import reverse
from django.utils import timezone

from dateutil.relativedelta import relativedelta

from audit.context import system_action

from . import regional
from .currency import CURRENCY_CHOICES, DEFAULT_CURRENCY
from .limits import (
    AMOUNT_DECIMAL_PLACES,
    AMOUNT_MAX_DIGITS,
    MAX_REMINDER_INTERVAL,
    MAX_TRANSACTION_NUMBER,
    RATE_DECIMAL_PLACES,
    RATE_MAX_DIGITS,
    validate_plausible_date,
)
from .money import ZERO, to_cent
from .text import possessive


# ----------------------------------------------------------------------
# Names
# ----------------------------------------------------------------------


def name_key(name):
    """How Stashy compares payee, category and stash owner names: names
    that differ only in upper/lower case are the same one. Python's
    casefold() knows every alphabet. SQLite's own lower() and LIKE only
    fold A-Z, so to the database "ЛИДЛ" and "лидл" would be two
    different names."""
    return (name or "").strip().casefold()


def _with_name_key(update_fields):
    """save(update_fields=[..., "name"]) must write the name's key along
    with it."""
    if update_fields is not None and "name" in update_fields and "name_key" not in update_fields:
        return [*update_fields, "name_key"]
    return update_fields


# ----------------------------------------------------------------------
# Profile
# ----------------------------------------------------------------------


class Profile(models.Model):
    """A stash's one settings row: who it belongs to, the main currency
    (D2), the regional formats (D10), the welcome wizard's progress, the
    PIN lockout and recovery code, and the Watched expenses setup."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="stashy_profile"
    )

    # Who this stash belongs to - a person, a business or a family -
    # shown everywhere instead of the stash's internal folder name. Set
    # once at creation; there's no Settings field to change it.
    owner_name = models.CharField(max_length=60, default="", blank=True)
    # 0-3 while the welcome wizard is in progress (which step is next),
    # 4 once finished. An existing stash counts as already finished -
    # see the migration.
    WELCOME_STEPS_DONE = 4
    welcome_step = models.PositiveSmallIntegerField(default=WELCOME_STEPS_DONE)

    main_currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default=DEFAULT_CURRENCY,
        help_text="Net worth is shown in this currency; new accounts start with it.",
    )
    time_zone = models.CharField(max_length=64, default=settings.TIME_ZONE)
    date_format = models.CharField(
        max_length=3, choices=regional.DATE_FORMAT_CHOICES, default="dmy"
    )
    number_format = models.CharField(
        max_length=12, choices=regional.NUMBER_FORMAT_CHOICES, default="space_dot"
    )
    first_day_of_week = models.PositiveSmallIntegerField(
        choices=regional.FIRST_DAY_CHOICES, default=1
    )

    # A stash's one-time recovery code, hashed the same way a password
    # is - shown to the user once, at stash creation, never stored in
    # the clear. Lockout state lives here too, since a stash's single
    # Profile row is the natural place for "this stash" data that isn't
    # a regional preference.
    recovery_code_hash = models.CharField(max_length=128, blank=True, default="")
    failed_pin_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    # "Watched expenses": any set of expense categories worth watching
    # closely, picked in Settings and reported on their own.
    watched_expenses_title = models.CharField(max_length=60, default="Watched expenses")
    watched_expenses_categories = models.ManyToManyField(
        "Category",
        blank=True,
        related_name="+",
        help_text="Which expense categories the Watched expenses report tracks.",
    )

    def __str__(self):
        return f"Preferences for {self.user}"

    @property
    def owner_possessive(self):
        """ "Alex" -> "Alex's"; "The Smiths" -> "The Smiths'"."""
        return possessive(self.owner_name)

    @property
    def setup_finished(self):
        return self.welcome_step >= self.WELCOME_STEPS_DONE

    @classmethod
    def for_user(cls, user):
        """The login's preferences, created from this computer's
        regional settings the first time."""
        profile = cls.objects.filter(user=user).first()
        if profile is not None:
            return profile
        with system_action("Preferences created from this computer's regional settings"):
            profile, _ = cls.objects.get_or_create(
                user=user, defaults=regional.profile_defaults_from_computer()
            )
        return profile

    @property
    def preferences(self):
        return regional.Preferences(
            date_format=self.date_format,
            number_format=self.number_format,
            first_day_of_week=self.first_day_of_week,
            time_zone=self.time_zone,
            main_currency=self.main_currency,
        )


# ----------------------------------------------------------------------
# Accounts
# ----------------------------------------------------------------------


class AccountType(models.TextChoices):
    CASH = "cash", "Cash"
    COUPONS = "coupons", "Coupons / Vouchers"
    DEBIT_CARD = "debit_card", "Debit Card / Checking"
    GIFT_CARD = "gift_card", "Gift Card"
    E_WALLET = "e_wallet", "E-Wallet"
    SAVINGS = "savings", "Savings"
    INVESTMENT = "investment", "Investment"
    RETIREMENT = "retirement", "Retirement"
    LOAN = "loan", "Loan"
    CREDIT_CARD = "credit_card", "Credit Card"
    OTHER = "other", "Other"


# (key, name, short explainer, account types), in the order accounts are
# listed.
ACCOUNT_GROUPS = [
    ("physical", "Physical Liquid", "Cash in hand", [AccountType.CASH, AccountType.COUPONS]),
    (
        "digital",
        "Digital Liquid",
        "Spendable, not physical",
        [AccountType.DEBIT_CARD, AccountType.GIFT_CARD, AccountType.E_WALLET],
    ),
    (
        "illiquid",
        "Illiquid",
        "Not instantly spendable",
        [
            AccountType.SAVINGS,
            AccountType.INVESTMENT,
            AccountType.RETIREMENT,
            AccountType.LOAN,
            AccountType.CREDIT_CARD,
            AccountType.OTHER,
        ],
    ),
]
# Inactive accounts always come last, whatever their type.
INACTIVE_GROUP = ("inactive", "Inactive", "No longer used")
ACCOUNT_TYPE_CHOICES = [
    (name, [(kind.value, kind.label) for kind in kinds]) for _, name, _, kinds in ACCOUNT_GROUPS
]
_GROUP_INDEX = {
    kind.value: index for index, group in enumerate(ACCOUNT_GROUPS) for kind in group[3]
}


class AccountQuerySet(models.QuerySet):
    def with_balances(self):
        """Compute every balance in the same query instead of one extra
        query per account."""
        return self.annotate(
            balance_total=Coalesce(
                Sum("transactions__amount"),
                Value(ZERO),
                # Room for the sum of many amounts, so two digits more.
                output_field=models.DecimalField(
                    max_digits=AMOUNT_MAX_DIGITS + 2, decimal_places=AMOUNT_DECIMAL_PLACES
                ),
            )
        )


class Account(models.Model):
    AccountType = AccountType

    name = models.CharField(max_length=120)
    account_type = models.CharField(
        max_length=20, choices=ACCOUNT_TYPE_CHOICES, default=AccountType.CASH
    )
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default=DEFAULT_CURRENCY)
    institution = models.CharField(
        max_length=120,
        blank=True,
        help_text="Fill when a bank or company is behind this account.",
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AccountQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        name_saved = update_fields is None or "name" in update_fields
        sync = self._state.adding or (
            name_saved and self.name != getattr(self, "_saved_name", None)
        )
        super().save(*args, **kwargs)
        if name_saved:
            self._saved_name = self.name
        # Only a new account or a new name touches the transfer category
        # (D7); any other save, like ticking "inactive", leaves it
        # alone.
        if sync:
            self.ensure_transfer_category()

    def get_absolute_url(self):
        return reverse("finance:account_detail", args=[self.pk])

    @classmethod
    def from_db(cls, db, field_names, values, **kwargs):
        account = super().from_db(db, field_names, values, **kwargs)
        # The name as saved, so save() can tell whether a rename needs
        # the transfer category renamed too.
        account._saved_name = dict(zip(field_names, values, strict=True)).get("name")
        return account

    def clean(self):
        super().clean()
        if self.pk is None:
            return
        saved_currency = (
            Account.objects.filter(pk=self.pk).values_list("currency", flat=True).first()
        )
        if saved_currency not in (None, self.currency) and self.transactions.exists():
            # Conversion only ever uses the latest known rate, not
            # per-transaction history, so a new currency would misstate
            # every transaction already in the account.
            raise ValidationError(
                {
                    "currency": (
                        "This account already has transactions, so its currency can't change."
                    )
                }
            )

    @property
    def balance(self):
        """The sum of the account's transactions - its opening balance
        is one of them (D1)."""
        if "balance_total" in self.__dict__:
            total = self.balance_total
        else:
            total = self.transactions.aggregate(total=Sum("amount"))["total"]
        # SQLite adds decimals as floating point, so round away results
        # like 9.66000000000000.
        return to_cent(total)

    def reconciled_balance(self):
        """Sum of already-reconciled transactions - the opening balance
        a new reconciliation starts from, since everything reconciled so
        far is the settled starting point."""
        total = self.transactions.filter(reconciled=True).aggregate(total=Sum("amount"))["total"]
        return to_cent(total)

    @property
    def group(self):
        """(key, name, explainer) of the account list group this account
        is shown in."""
        if not self.is_active:
            return INACTIVE_GROUP
        return ACCOUNT_GROUPS[_GROUP_INDEX.get(self.account_type, len(ACCOUNT_GROUPS) - 1)][:3]

    @property
    def group_order(self):
        return (
            len(ACCOUNT_GROUPS)
            if not self.is_active
            else _GROUP_INDEX.get(self.account_type, len(ACCOUNT_GROUPS) - 1)
        )

    def ensure_transfer_category(self):
        """Every account owns exactly one transfer category, named after
        it (D7). Returns it."""
        name = f"[{self.name}]"
        category = Category.objects.filter(account=self).first()

        if category is None:
            with system_action(f"Transfer category created for account '{self.name}'"):
                category = Category.objects.create(
                    name=name, category_type=Category.CategoryType.TRANSFER, account=self
                )
        elif category.name != name:
            category.name = name
            with system_action(f"Transfer category renamed to match account '{self.name}'"):
                category.save(update_fields=["name"])

        return category


def grouped_accounts(accounts):
    """Accounts split into Physical Liquid → Digital Liquid → Illiquid →
    Inactive, by name within each group."""
    groups = []
    for account in sorted(accounts, key=lambda a: (a.group_order, name_key(a.name))):
        key, name, explainer = account.group
        if not groups or groups[-1]["key"] != key:
            groups.append({"key": key, "name": name, "explainer": explainer, "accounts": []})
        groups[-1]["accounts"].append(account)
    return groups


# ----------------------------------------------------------------------
# Categories
# ----------------------------------------------------------------------


class CategoryQuerySet(models.QuerySet):
    def main_categories(self):
        """Top-level categories, the ones a subcategory can go under -
        never an account's transfer category."""
        return self.filter(parent__isnull=True, account__isnull=True)


class Category(models.Model):
    class CategoryType(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"
        TRANSFER = "transfer", "Transfer"

    name = models.CharField(max_length=120)
    # name_key(name), kept by save(): the database checks uniqueness on
    # this column.
    name_key = models.CharField(max_length=120, editable=False)
    # One level of subcategories only: a subcategory can't itself have
    # children (see clean()).
    parent = models.ForeignKey(
        "self", null=True, blank=True, related_name="subcategories", on_delete=models.CASCADE
    )
    category_type = models.CharField(
        max_length=10, choices=CategoryType.choices, default=CategoryType.EXPENSE
    )
    account = models.OneToOneField(
        Account,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.CASCADE,
        related_name="transfer_category",
        help_text=(
            "Set only on an account's own transfer category, which Stashy creates and deletes "
            "with the account."
        ),
    )
    # Set when an import created this category itself because the file
    # named one Stashy didn't already have - flagged for
    # visibility/later cleanup rather than baked into the name forever,
    # same idea as system-managed categories getting a badge, not a
    # label.
    is_auto_generated = models.BooleanField(default=False)

    objects = CategoryQuerySet.as_manager()

    class Meta:
        verbose_name_plural = "categories"
        # name_key, not name: SQLite sorts plain text by code point, so
        # "lidl" would come after "Zara", and it needs no join to the
        # parent.
        ordering = ["name_key"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name_key"], name="unique_subcategory_name_ci"
            ),
            # NULL parents never clash in SQL, so top-level names need
            # their own rule. An account's transfer category is left
            # out: it's named after its account, and two accounts may
            # share a name.
            models.UniqueConstraint(
                fields=["name_key"],
                condition=Q(parent__isnull=True, account__isnull=True),
                name="unique_top_level_category_name_ci",
            ),
            models.CheckConstraint(
                condition=(
                    Q(category_type="transfer", account__isnull=False)
                    | (~Q(category_type="transfer") & Q(account__isnull=True))
                ),
                name="category_transfer_iff_account",
            ),
        ]

    def __str__(self):
        return self.full_name

    def save(self, *args, **kwargs):
        self.name_key = name_key(self.name)
        kwargs["update_fields"] = _with_name_key(kwargs.get("update_fields"))
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        return f"{self.parent.name} > {self.name}" if self.parent_id else self.name

    def create_subcategory(self, name, **fields):
        """A new subcategory under this one. It always takes this
        category's type: an "Income" category with an "Expense" child
        would only confuse the reports."""
        return Category.objects.create(
            name=name, category_type=self.category_type, parent=self, **fields
        )

    def clean(self):
        super().clean()
        errors = {}
        parent = self.parent if self.parent_id else None

        if parent is not None:
            if parent.pk == self.pk:
                errors["parent"] = "A category can't be placed under itself."
            elif parent.parent_id:
                errors["parent"] = (
                    "A subcategory can't have its own subcategories - pick a main category."
                )
            elif parent.account_id:
                errors["parent"] = "An account's transfer category can't have subcategories."
            elif self.pk and self.subcategories.exists():
                errors["parent"] = (
                    "This category already has its own subcategories, so it can't become one "
                    "itself — move or remove them first."
                )

        is_transfer = self.category_type == self.CategoryType.TRANSFER
        if is_transfer and not self.account_id:
            errors["category_type"] = (
                "Transfer categories belong to an account and are created with it."
            )
        elif self.account_id and not is_transfer:
            errors["category_type"] = (
                "This category belongs to an account, so its type stays Transfer."
            )

        if "parent" not in errors and not self.account_id:
            same_name = Category.objects.filter(name_key=name_key(self.name)).exclude(pk=self.pk)
            if parent is not None and same_name.filter(parent=parent).exists():
                errors["name"] = f"'{parent.name}' already has a subcategory with this name."
            elif parent is None and same_name.main_categories().exists():
                errors["name"] = "A category with this name already exists."

        if errors:
            raise ValidationError(errors)

    @property
    def indented_name(self):
        """For dropdowns: a subcategory is indented under its main
        category (which is listed first, un-indented) instead of
        repeating the parent's name - the same look as the tick-list on
        the Transactions filter. Non-breaking spaces, since <option>
        text collapses normal ones."""
        return f"    {self.name}" if self.parent_id else self.name

    @property
    def is_system_managed(self):
        return self.account_id is not None

    def descendants(self):
        """All subcategories at any depth — the ones a delete would
        cascade to."""
        found, frontier, seen = [], [self], {self.pk}
        while frontier:
            children = [
                child
                for child in Category.objects.filter(parent__in=frontier).select_related("parent")
                if child.pk not in seen  # a parent loop in the data must not hang the app
            ]
            seen.update(child.pk for child in children)
            found.extend(children)
            frontier = children
        return found


# Income first, then Expense, then the system-managed Transfer
# categories last.
_CATEGORY_TYPE_ORDER = {
    Category.CategoryType.INCOME: 0,
    Category.CategoryType.EXPENSE: 1,
    Category.CategoryType.TRANSFER: 2,
}
# Where a type this table doesn't know would sort: after all of them.
_UNKNOWN_TYPE_RANK = 99


def categories_alphabetical(queryset):
    """Order a Category queryset for a dropdown: Income, then Expense,
    then Transfer (the category list page's grouping too, see
    grouped_categories() below), and within each type every subcategory
    right after its own parent, in one alphabetical run. So typing a
    letter into a <select>, or scanning it by eye, finds "Personal Care"
    and everything under it together. For dropdowns only: the category
    list page uses grouped_categories()."""
    return queryset.annotate(
        _sort_type=Case(
            *(
                When(category_type=kind, then=Value(rank))
                for kind, rank in _CATEGORY_TYPE_ORDER.items()
            ),
            default=Value(_UNKNOWN_TYPE_RANK),
        ),
        _sort_group=Coalesce("parent__name_key", "name_key"),
        _sort_child=Case(When(parent__isnull=True, then=Value("")), default=F("name_key")),
    ).order_by("_sort_type", "_sort_group", "_sort_child")


def category_name_order(category):
    """The sort key for putting categories in alphabetical order in
    Python: a subcategory right after its own parent, both compared
    through `name_key` like the database does it
    (categories_alphabetical() above). `.lower()` would differ from it
    for letters such as the German "ß", so a list and a dropdown could
    disagree."""
    if category.parent_id:
        return (category.parent.name_key, category.name_key)
    return (category.name_key, "")


def grouped_categories(categories):
    """Categories split into Income -> Expense -> Transfer groups;
    within each group, ordered by (top-level) parent name, then by
    subcategory name under it."""

    def sort_key(category):
        type_rank = _CATEGORY_TYPE_ORDER.get(category.category_type, _UNKNOWN_TYPE_RANK)
        return (type_rank, *category_name_order(category))

    groups = []
    for category in sorted(categories, key=sort_key):
        key = category.category_type
        if not groups or groups[-1]["key"] != key:
            groups.append(
                {"key": key, "name": category.get_category_type_display(), "categories": []}
            )
        groups[-1]["categories"].append(category)
    return groups


# ----------------------------------------------------------------------
# Payees
# ----------------------------------------------------------------------


class PayeeQuerySet(models.QuerySet):
    def named(self, name):
        """The payee called `name`, ignoring upper/lower case (at most
        one, by constraint)."""
        return self.filter(name_key=name_key(name))

    def resolve(self, name, reason="Payee created automatically from a new payee name"):
        """The payee called `name` in any case - the spelling already
        saved wins - or a new one. A new payee is logged as automatic
        with `reason`; reason=None keeps the caller's own system_action
        (an import's "Imported from <file>")."""
        payee = self.named(name).first()
        if payee is None:
            with system_action(reason) if reason else contextlib.nullcontext():
                payee = self.create(name=name.strip())
        return payee


class Payee(models.Model):
    """A payee Stashy has seen on a transaction. Memorization is
    computed live from transaction history — one category/memo/amount
    per (payee, category) pair, not a cached default field — so it's
    never stale and there's nothing to keep in sync."""

    # Shared payees Stashy uses for the transactions it creates for you.
    OPENING_BALANCE = "Opening Balance"
    TRANSFER_MONEY = "Transfer Money"
    RECONCILIATION_ADJUSTMENT = "Reconciliation Adjustment"
    SYSTEM_NAMES = (OPENING_BALANCE, TRANSFER_MONEY, RECONCILIATION_ADJUSTMENT)
    # The same names the way name_key() sees them: Payee.system() reuses
    # a payee typed in another case ("opening balance"), so a plain
    # `name in SYSTEM_NAMES` would miss it. (Inside this generator,
    # name_key is the module's function, not the field of that name
    # defined below.)
    SYSTEM_KEYS = frozenset(name_key(name) for name in SYSTEM_NAMES)

    name = models.CharField(max_length=150)
    # name_key(name), kept by save(): the database checks uniqueness on
    # this column.
    name_key = models.CharField(max_length=150, editable=False)
    is_active = models.BooleanField(default=True)

    objects = PayeeQuerySet.as_manager()

    class Meta:
        ordering = ["name_key"]  # see Category.Meta.ordering
        constraints = [models.UniqueConstraint(fields=["name_key"], name="unique_payee_name_ci")]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name_key = name_key(self.name)
        kwargs["update_fields"] = _with_name_key(kwargs.get("update_fields"))
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if Payee.objects.named(self.name).exclude(pk=self.pk).exists():
            raise ValidationError({"name": "A payee with this name already exists."})

    @property
    def is_system(self):
        """One of the payees Stashy makes for its own transactions, in
        whatever case it was typed."""
        return self.name_key in self.SYSTEM_KEYS

    @classmethod
    def system(cls, name):
        return cls.objects.resolve(
            name, reason=f"Payee '{name}' created for transactions Stashy makes"
        )

    def category_history(self):
        """One entry per category ever used with this payee (most
        recently used first): the most recent Transaction in each — its
        category/memo/amount are what a new transaction against this
        payee would offer to autofill from."""
        seen, history = set(), []
        for transaction in self.transactions.select_related(
            "category__parent", "account"
        ).order_by("-date", "-transaction_number", "-created_at"):
            if transaction.category_id not in seen:
                seen.add(transaction.category_id)
                history.append(transaction)
        return history

    def latest_category(self):
        """The category of this payee's most recent transaction, or None
        when it has none or that one has no category: the first entry of
        category_history() without reading the rest of the history (an
        import asks this for every row it creates)."""
        latest = self.transactions.select_related("category").first()
        return latest.category if latest else None

    def remembered_memo(self, category_id):
        """The memo of this payee's most recent transaction in the
        category `category_id`, or "" when there is none."""
        memos = self.transactions.filter(category_id=category_id).values_list("memo", flat=True)
        return memos.first() or ""


def payee_summaries(payees):
    """One summary per payee in `payees`, for the Payees page: the
    categories it was used with, how many transactions it has, and its
    total in each currency.

    The list can hold hundreds of payees, so each kind of data is asked
    of the database once for the whole list, never once per payee.
    """
    payees = list(payees)
    history = Transaction.objects.filter(payee_id__in=[payee.pk for payee in payees])

    pairs = list(
        history.values_list("payee_id", "category_id")
        # Transaction's default ordering would add its date, number and
        # created-at columns to the DISTINCT and repeat each category
        # once per transaction.
        .order_by()
        .distinct()
    )
    categories = Category.objects.select_related("parent").in_bulk(
        {category_id for _, category_id in pairs if category_id is not None}
    )
    labels_by_payee = {}
    for payee_id, category_id in pairs:
        label = "Uncategorized" if category_id is None else categories[category_id].full_name
        labels_by_payee.setdefault(payee_id, []).append(label)

    usage_by_payee = {
        row["payee_id"]: row["count"]
        for row in history.values("payee_id").annotate(count=Count("id"))
    }

    totals_by_payee = {}
    for row in history.values("payee_id", "account__currency").annotate(total=Sum("amount")):
        # SQLite adds decimals as floats, so -0.10, -0.20 and 0.30 come
        # back as a tiny negative number, not zero.
        totals_by_payee.setdefault(row["payee_id"], []).append(
            (row["account__currency"], to_cent(row["total"]))
        )

    summaries = []
    for payee in payees:
        labels = sorted(labels_by_payee.get(payee.pk, []))
        totals = sorted(totals_by_payee.get(payee.pk, []))
        summaries.append(
            {
                "payee": payee,
                "categories": labels,
                # A sort key only: the template shows one label per row.
                "categories_label": ", ".join(labels),
                "category_count": len(labels),
                "totals": totals,
                # Currency-blind, for sort order only: never shown as an
                # amount.
                "total_sort_value": sum((amount for _, amount in totals), ZERO),
                "usage_count": usage_by_payee.get(payee.pk, 0),
            }
        )
    return summaries


# ----------------------------------------------------------------------
# Transactions
# ----------------------------------------------------------------------


class TransactionQuerySet(models.QuerySet):
    """The building blocks every report query is made of
    (finance/reporting.py)."""

    def in_range(self, start, end):
        """Dated from `start` to `end`, both days included."""
        return self.filter(date__gte=start, date__lte=end)

    def reportable(self):
        """What counts as income or spending: it has a category, and
        that category isn't an account's Transfer category (D7). Opening
        balances use the Transfer category too (D1)."""
        return self.filter(category__isnull=False).exclude(category__account__isnull=False)

    def of_kind(self, expense_only=False, income_only=False):
        """Only expense or only income categories, or both when neither
        flag is set. The category's type decides, never the amount's
        sign."""
        if expense_only:
            return self.filter(category__category_type=Category.CategoryType.EXPENSE)
        if income_only:
            return self.filter(category__category_type=Category.CategoryType.INCOME)
        return self

    def for_account(self, account):
        """Only `account`'s transactions, or every account's when
        `account` is None."""
        return self.filter(account=account) if account else self


class Transaction(models.Model):
    """Sign convention: positive amount = money in (income/deposit),
    negative amount = money out (expense/withdrawal)."""

    account = models.ForeignKey(Account, related_name="transactions", on_delete=models.CASCADE)
    date = models.DateField(validators=[validate_plausible_date])
    transaction_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(MAX_TRANSACTION_NUMBER)],
        help_text=(
            "Shared across all accounts, starting again at 1 every calendar year. "
            "You can change it."
        ),
    )
    payee = models.ForeignKey(
        Payee, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions"
    )
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions"
    )
    memo = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(
        max_digits=AMOUNT_MAX_DIGITS, decimal_places=AMOUNT_DECIMAL_PLACES
    )
    reconciled = models.BooleanField(default=False)
    import_batch = models.ForeignKey(
        "ImportBatch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transactions",
    )
    import_fingerprint = models.CharField(
        max_length=64, blank=True, db_index=True, help_text="Hash used to detect duplicate imports"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TransactionQuerySet.as_manager()

    class Meta:
        # transaction_number is an integer column, so it sorts 1, 2,
        # 10 - never "1, 10, 2" like text would (D9).
        ordering = ["-date", "-transaction_number", "-created_at"]
        indexes = [
            models.Index(fields=["date"]),
            models.Index(fields=["account", "date"]),
        ]

    def __str__(self):
        return f"{regional.format_date(self.date)} | {self.display_payee} | {self.amount}"

    def save(self, *args, **kwargs):
        if self._state.adding and self.transaction_number is None:
            self.transaction_number = self.next_number(self.date)
        super().save(*args, **kwargs)

    @property
    def display_payee(self):
        return self.payee.name if self.payee_id else ""

    @staticmethod
    def next_number(on):
        """The next transaction number for `on`'s calendar year - shared
        across every account, not scoped to one (D9). Duplicates are
        allowed on purpose: numbers are editable and imports reuse them.
        Two requests can't both read the same MAX() either, since every
        write request holds the stash's write lock (BEGIN IMMEDIATE)
        from its start."""
        # A number above the limit (stored before the limit existed) is
        # left out: one more than a number the database can barely hold
        # would be one it can't, and every new transaction of the year
        # would fail. And the answer never passes the limit itself, or
        # the form would refuse a number nobody typed; duplicates are
        # allowed, so the limit is given again.
        countable = Transaction.objects.filter(
            date__year=on.year, transaction_number__lte=MAX_TRANSACTION_NUMBER
        )
        highest = countable.aggregate(highest=Max("transaction_number"))["highest"]
        return min((highest or 0) + 1, MAX_TRANSACTION_NUMBER)


# ----------------------------------------------------------------------
# Imports
# ----------------------------------------------------------------------


class ImportBatch(models.Model):
    """Tracks a single CSV/OFX/QFX import run so it can be reviewed or
    undone."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="import_batches")
    source_filename = models.CharField(max_length=255)
    imported_at = models.DateTimeField(auto_now_add=True)
    row_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-imported_at"]
        verbose_name_plural = "import batches"

    def __str__(self):
        return f"{self.source_filename} ({regional.format_datetime(self.imported_at)})"


# ----------------------------------------------------------------------
# Reminders
# ----------------------------------------------------------------------


AMOUNT_NOT_NEGATIVE = (
    "Enter the amount as a positive number - the type already says whether it's income "
    "or an expense."
)


def _reminder_transaction():
    """One unit of work on the stash Reminder rows live in. Inside a
    request's own transaction it is a savepoint; from the shell or a
    script it is what makes several writes succeed or fail together."""
    return atomic(using=router.db_for_write(Reminder))


class ReminderQuerySet(models.QuerySet):
    def not_done(self):
        """Without the reminders `Reminder.status()` calls "paid" or
        "skipped", decided by the database: a page that hides them
        needn't load every one there has ever been. The two rules are
        the same one, so a test holds them together."""
        return self.exclude(
            Q(skipped=True)
            | Q(frequency=Reminder.Frequency.ONE_TIME, last_entered_transaction__isnull=False)
        )

    def resolved(self):
        """The occurrences someone has paid or skipped: a real record
        now, which a series edit, a regeneration and a delete all leave
        alone. A payment link counts whatever the row's frequency (see
        not_done(), which follows `status()` and so doesn't)."""
        return self.filter(Q(last_entered_transaction__isnull=False) | Q(skipped=True))

    def unresolved(self):
        """The occurrences nobody has paid or skipped: the previews."""
        return self.filter(last_entered_transaction__isnull=True, skipped=False)


class Reminder(models.Model):
    """A recurring bill or income reminder."""

    class Frequency(models.TextChoices):
        ONE_TIME = "one_time", "One-time"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        YEARLY = "yearly", "Yearly"

    # Frequency value -> the unit word shown next to the "Every N ..."
    # interval field.
    FREQUENCY_UNIT_WORDS = {
        "weekly": "week(s)",
        "monthly": "month(s)",
        "quarterly": "quarter(s)",
        "yearly": "year(s)",
    }

    TYPE_CHOICES = [
        (Category.CategoryType.INCOME.value, Category.CategoryType.INCOME.label),
        (Category.CategoryType.EXPENSE.value, Category.CategoryType.EXPENSE.label),
    ]

    # What a "whole series" edit copies onto the series head and its
    # still-open occurrences: the same fields regenerate_occurrences()
    # copies onto a new occurrence. The schedule (frequency, interval,
    # dates) stays per row, since an occurrence's own date is what makes
    # it that occurrence.
    SERIES_FIELDS = ["account", "payee", "category", "amount", "reminder_type", "notes"]
    # What decides a recurring reminder's future dates: saving any of
    # them regenerates its occurrences (see save()).
    SCHEDULE_FIELDS = frozenset(
        {"frequency", "interval", "next_due_date", "end_date", "end_of_period"}
    )

    # "Due soon" is treated as urgent enough to need action; anything
    # further out than this many days (and not already overdue) is
    # either "paid" (see status()) or plain pending.
    DUE_SOON_DAYS = 7

    reminder_type = models.CharField(
        max_length=10, choices=TYPE_CHOICES, default=Category.CategoryType.EXPENSE
    )
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="reminders")
    payee = models.ForeignKey(
        Payee, null=True, blank=True, on_delete=models.SET_NULL, related_name="reminders"
    )
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    amount = models.DecimalField(
        max_digits=AMOUNT_MAX_DIGITS,
        decimal_places=AMOUNT_DECIMAL_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(0, message=AMOUNT_NOT_NEGATIVE)],
        help_text="Enter as a positive number, or leave blank for a variable-amount bill",
    )
    frequency = models.CharField(
        max_length=10, choices=Frequency.choices, default=Frequency.ONE_TIME
    )
    # How many periods between occurrences - "weekly" + interval=2 is
    # "every 2 weeks". Meaningless for ONE_TIME; always 1 there.
    interval = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(MAX_REMINDER_INTERVAL)]
    )
    next_due_date = models.DateField(validators=[validate_plausible_date])
    end_of_period = models.BooleanField(default=False, verbose_name="Last day of period")
    end_date = models.DateField(null=True, blank=True, validators=[validate_plausible_date])
    # Points at the transaction created the last time this reminder was
    # entered (Enter, not Skip) - the basis for the computed "paid"
    # status (D6). Deleting that transaction clears this automatically
    # (SET_NULL) and the status reverts; deleting the reminder never
    # touches the transaction, since the FK points the other way.
    last_entered_transaction = models.ForeignKey(
        "Transaction", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # A one-time reminder has nothing to advance to once acted on, so
    # Skip marks it done instead of moving a due date that doesn't exist
    # for it.
    skipped = models.BooleanField(default=False)
    # Set only on an auto-generated future occurrence of a recurring
    # reminder (below), pointing back at the recurring "series head"
    # that generated it. Null on every reminder you create by hand,
    # recurring or not.
    series = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="occurrences"
    )
    notes = models.CharField(max_length=255, blank=True)

    objects = ReminderQuerySet.as_manager()

    class Meta:
        ordering = ["next_due_date"]
        constraints = [
            # The reminder's type says income or expense, so the amount
            # itself is never negative.
            models.CheckConstraint(
                condition=Q(amount__isnull=True) | Q(amount__gte=0),
                name="reminder_amount_not_negative",
            ),
            models.CheckConstraint(
                condition=Q(interval__gte=1), name="reminder_interval_at_least_one"
            ),
        ]

    def __str__(self):
        # A reminder has no name of its own: it is identified like a
        # pending transaction, by who it's for and how much, the same
        # way Transaction.__str__ identifies a real one.
        who = self._who_for()
        amount = self.signed_amount()
        due_date = regional.format_date(self.next_due_date)
        return f"{due_date} | {who} | {amount if amount is not None else 'varies'}"

    def save(self, *args, **kwargs):
        # A recurring reminder keeps its future occurrences in sync in
        # save() itself, not just in the form, so the admin, scripts and
        # the shell get the same occurrences. A full save (every form
        # and admin edit) always regenerates: that's also what moves the
        # generation horizon forward as time passes. A save of chosen
        # fields regenerates only if one of them is part of the
        # schedule - marking a period paid doesn't move any date.
        update_fields = kwargs.get("update_fields")
        schedule_saved = update_fields is None or not self.SCHEDULE_FIELDS.isdisjoint(
            update_fields
        )
        regenerate = self.frequency != self.Frequency.ONE_TIME and schedule_saved

        # Read before the save overwrites it: regenerate_occurrences()
        # must know if the due date moved.
        with _reminder_transaction():
            previous_due_date = self._stored_due_date() if regenerate else None
            super().save(*args, **kwargs)
            if regenerate:
                self.regenerate_occurrences(previous_due_date=previous_due_date)

    def _stored_due_date(self):
        """The due date saved in the database right now, or None for a
        reminder not saved yet."""
        if self.pk is None:
            return None
        return Reminder.objects.filter(pk=self.pk).values_list("next_due_date", flat=True).first()

    def delete(self, *args, **kwargs):
        # Deleting a whole series takes its still-open occurrences with
        # it (series is on_delete=CASCADE) - but an occurrence you've
        # already paid or skipped is a real historical record now, not
        # just a preview, so it's detached (series -> null) and kept
        # standing on its own instead of disappearing with the series.
        with _reminder_transaction():
            if self.frequency != self.Frequency.ONE_TIME:
                reason = f"Occurrence detached from deleted recurring reminder ({self})"
                with system_action(reason):
                    for occurrence in self.occurrences.resolved():
                        occurrence.series = None
                        occurrence.save(update_fields=["series"])
            return super().delete(*args, **kwargs)

    def signed_amount(self):
        if self.amount is None:
            return None
        return self.amount if self.reminder_type == Category.CategoryType.INCOME else -self.amount

    def _who_for(self):
        """Who this reminder is for, in words: its payee's name, else
        its category's full name, else "Reminder"."""
        if self.payee_id:
            return self.payee.name
        if self.category_id:
            return self.category.full_name
        return "Reminder"

    def status(self, today=None):
        """One of "overdue", "due_soon", "paid", "skipped" or "" (=
        pending).

        A one-time reminder (a real one-off, or an auto-generated future
        occurrence of a recurring one) has no next period to fall back
        into once it's been acted on, so Paid/Skipped there is
        permanent - unlike a recurring reminder, which just moves on to
        its next period and so only ever shows overdue/due soon/pending.
        """
        if self.skipped:
            return "skipped"
        if self.frequency == self.Frequency.ONE_TIME:
            return "paid" if self.last_entered_transaction_id else self._date_status(today)
        # A recurring reminder always stands for its next,
        # not-yet-entered period (entering or skipping advances it), so
        # it is never "paid" itself - even if an older row still carries
        # a stale link from before that was cleared on advance.
        return self._date_status(today)

    def _date_status(self, today=None):
        today = today or timezone.localdate()
        days = (self.next_due_date - today).days
        if days < 0:
            return "overdue"
        if days <= self.DUE_SOON_DAYS:
            return "due_soon"
        return ""

    @staticmethod
    def default_generation_horizon(today=None):
        """How far ahead to auto-generate occurrences when a reminder
        has no end date: 90 days out, or through 31 December of this
        year - whichever is further. Late in the year the 90-day floor
        wins and spills into next year; earlier in the year, year-end is
        further out, matching what the "Yearly" filter button on the
        reminder list shows."""
        today = today or timezone.localdate()
        return max(today + timedelta(days=90), date(today.year, 12, 31))

    def _period_end(self, on):
        """Last day of `on`'s period for this reminder's frequency, for
        end-of-period mode (D5).

        The period itself is always the natural calendar
        month/quarter/year `on` falls in, regardless of `interval` -
        "every 3 months, last day of period" still means the last day of
        whichever month it lands on, not a 3-month span. Weekly is the
        one case where the interval genuinely changes what "the period"
        spans."""
        if self.frequency == self.Frequency.YEARLY:
            return date(on.year, 12, 31)
        if self.frequency == self.Frequency.QUARTERLY:
            quarter_end_month = ((on.month - 1) // 3) * 3 + 3
            next_month = date(on.year, quarter_end_month, 1) + relativedelta(months=1)
            return next_month - timedelta(days=1)
        if self.frequency == self.Frequency.MONTHLY:
            next_month = date(on.year, on.month, 1) + relativedelta(months=1)
            return next_month - timedelta(days=1)
        # WEEKLY: last day of the `interval`-week span containing `on`,
        # anchored so weeks start Monday (D10's convention) - interval=1
        # is just "last day of this week".
        week_start = on - timedelta(days=on.weekday())
        return week_start + timedelta(days=7 * self.interval - 1)

    _FREQUENCY_UNITS = {
        Frequency.WEEKLY: "weeks",
        Frequency.MONTHLY: "months",
        Frequency.QUARTERLY: "months",  # relativedelta has no native "quarters" unit
        Frequency.YEARLY: "years",
    }
    _QUARTERLY_MONTH_MULTIPLIER = 3

    def _advance(self, from_date):
        """The next due date after `from_date` for this reminder's own
        frequency/interval/ end-of-period settings - pure (no save), so
        `regenerate_occurrences` can walk it forward repeatedly."""
        unit = self._FREQUENCY_UNITS[self.frequency]
        count = self.interval * (
            self._QUARTERLY_MONTH_MULTIPLIER if self.frequency == self.Frequency.QUARTERLY else 1
        )
        advanced = from_date + relativedelta(**{unit: count})
        return self._period_end(advanced) if self.end_of_period else advanced

    def _series_values(self):
        """This reminder's SERIES_FIELDS as {name: value}: what a new
        occurrence, or the record of a finished period, copies."""
        return {field: getattr(self, field) for field in self.SERIES_FIELDS}

    def record_period(self, transaction=None, skipped=False):
        """Leave a standing Paid/Skipped record of the period this
        recurring reminder is about to move past. Entering or skipping
        advances the reminder itself to the next period, so without this
        the period you just dealt with would vanish from the list."""
        with system_action(f"Resolved period recorded for recurring reminder ({self})"):
            return Reminder.objects.create(
                series=self,
                frequency=self.Frequency.ONE_TIME,
                next_due_date=self.next_due_date,
                last_entered_transaction=transaction,
                skipped=skipped,
                **self._series_values(),
            )

    def advance_due_date(self):
        self.next_due_date = self._advance(self.next_due_date)
        who = self._who_for()
        with system_action(f"Next due date moved forward one period for reminder ({who})"):
            self.save(update_fields=["next_due_date"])  # save() -> regenerate_occurrences()

    def mark_entered(self, transaction):
        """Enter (D6): `transaction` pays this reminder's current
        period. A one-time reminder links to it and is "Paid" for good;
        a recurring one records the period and moves on."""
        if self.frequency == self.Frequency.ONE_TIME:
            self.last_entered_transaction = transaction
            self.save(update_fields=["last_entered_transaction"])
        else:
            self._move_past_period(transaction=transaction)

    def skip(self):
        """Skip (D6): this period won't be paid. A one-time reminder is
        marked done, since it has no next period; a recurring one
        records the period as skipped and moves on."""
        if self.frequency == self.Frequency.ONE_TIME:
            self.skipped = True
            self.save(update_fields=["skipped"])
        else:
            self._move_past_period(skipped=True)

    def apply_to_series(self):
        """A "whole series" edit: copy this reminder's SERIES_FIELDS
        onto its series head (when it's an occurrence) and onto every
        occurrence not yet paid or skipped. The head and the occurrences
        are one automatic change, so the audit log labels them alike."""
        root = self.series if self.series_id else self
        values = self._series_values()
        # A head needs no copy of its own values.
        head = [root] if root.pk != self.pk else []
        open_occurrences = root.occurrences.unresolved().exclude(pk=self.pk)

        with (
            _reminder_transaction(),
            system_action(f"Series-wide edit applied from reminder ({self})"),
        ):
            for reminder in [*head, *open_occurrences]:
                for field, value in values.items():
                    setattr(reminder, field, value)
                reminder.save(update_fields=self.SERIES_FIELDS)

    def _move_past_period(self, transaction=None, skipped=False):
        self.record_period(transaction=transaction, skipped=skipped)
        # The reminder now stands for the next period, which nobody has
        # paid yet - an old link left from an earlier Enter would make
        # it show as "Paid".
        if self.last_entered_transaction_id:
            self.last_entered_transaction = None
            self.save(update_fields=["last_entered_transaction"])
        self.advance_due_date()

    def _wanted_dates(self):
        """The due dates after this reminder's own that its series
        should have a row for, oldest first: up to the end date, or the
        default horizon when there is none. It reads no database."""
        horizon = self.end_date or self.default_generation_horizon()
        dates = []
        cursor = self.next_due_date
        for _ in range(500):  # safety cap - far more occurrences than any real horizon needs
            cursor = self._advance(cursor)
            if cursor > horizon:
                break
            dates.append(cursor)
        return dates

    def regenerate_occurrences(self, previous_due_date=None):
        """Keep this recurring reminder's auto-generated future
        occurrences in sync with its own due date, frequency and end
        date - one real `Reminder` row per future occurrence, so each
        can be entered/skipped/edited on its own, same as any other
        reminder. Only ever called on a series head (never on a one-time
        reminder or an occurrence itself, both of which have nothing to
        generate).

        Only a preview can be dropped. Already-resolved occurrences
        (paid or skipped) are never touched, even if a shortened end
        date would otherwise drop them, and a preview that already
        exists for a wanted date is left alone too, so hand-editing one
        instance (e.g. a higher amount one month) survives the next
        regeneration.

        A preview is an open occurrence dated on or after this
        reminder's due date, or after where it used to be
        (`previous_due_date`, when this save moved it later): the dates
        between are ones you skipped over by hand. An open occurrence
        dated before both is an earlier period nobody paid (its payment
        was deleted): a real overdue bill, so it stays.
        """
        if self.frequency == self.Frequency.ONE_TIME:
            return

        first_preview_date = min(previous_due_date or self.next_due_date, self.next_due_date)
        wanted_dates = self._wanted_dates()
        wanted = set(wanted_dates)
        series_values = self._series_values()

        # One unit, read and writes together, so a crash half way leaves
        # the occurrences as they were and no other writer slips in
        # between the reading and the writing.
        with (
            _reminder_transaction(),
            system_action(f"Auto-generated occurrences updated for recurring reminder ({self})"),
        ):
            # Only rows that can matter are read: the open previews
            # (those that may go) and the dates from the first wanted
            # one on (those that need no new row). The old Paid and
            # Skipped rows, which only pile up, are never loaded. Two
            # occurrences on one date are two rows here, so a Paid one
            # can't hide an open one.
            previews = self.occurrences.unresolved().filter(next_due_date__gte=first_preview_date)
            existing_dates = (
                set(
                    self.occurrences.filter(next_due_date__gte=wanted_dates[0]).values_list(
                        "next_due_date", flat=True
                    )
                )
                if wanted_dates
                else set()
            )

            for occurrence in previews:
                if occurrence.next_due_date not in wanted:
                    occurrence.delete()

            for occ_date in wanted_dates:
                if occ_date not in existing_dates:
                    Reminder.objects.create(
                        series=self,
                        frequency=self.Frequency.ONE_TIME,
                        next_due_date=occ_date,
                        **series_values,
                    )


# ----------------------------------------------------------------------
# Exchange rates
# ----------------------------------------------------------------------


class ExchangeRate(models.Model):
    """1 unit of base_currency was worth `rate` units of quote_currency
    on `date` (D2)."""

    class Source(models.TextChoices):
        MANUAL = "manual", "Typed in"
        FRANKFURTER = "Frankfurter", "Frankfurter (ECB)"

    base_currency = models.CharField("from", max_length=3, choices=CURRENCY_CHOICES)
    quote_currency = models.CharField("to", max_length=3, choices=CURRENCY_CHOICES)
    rate = models.DecimalField(
        max_digits=RATE_MAX_DIGITS,
        decimal_places=RATE_DECIMAL_PLACES,
        help_text="How much 1 unit of the first currency is worth in the second.",
    )
    date = models.DateField(validators=[validate_plausible_date])
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.MANUAL)

    class Meta:
        ordering = ["-date", "base_currency", "quote_currency"]
        constraints = [
            models.UniqueConstraint(
                fields=["base_currency", "quote_currency", "date", "source"],
                name="unique_rate_per_day_and_source",
            ),
            models.CheckConstraint(condition=Q(rate__gt=0), name="exchange_rate_positive"),
        ]

    def __str__(self):
        rate = f"{self.rate.normalize():f}"
        rate_date = regional.format_date(self.date)
        return f"1 {self.base_currency} = {rate} {self.quote_currency} ({rate_date})"


# ----------------------------------------------------------------------
# Reconciliations
# ----------------------------------------------------------------------


class Reconciliation(models.Model):
    """A record of one reconciliation session - kept purely as an audit
    trail ("why is this balance off"). The actual settled/unsettled
    state of each transaction lives on Transaction.reconciled."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="reconciliations")
    statement_date = models.DateField(validators=[validate_plausible_date])
    statement_balance = models.DecimalField(
        max_digits=AMOUNT_MAX_DIGITS, decimal_places=AMOUNT_DECIMAL_PLACES
    )
    opening_balance = models.DecimalField(
        max_digits=AMOUNT_MAX_DIGITS, decimal_places=AMOUNT_DECIMAL_PLACES
    )
    # Set only when the statement balance and the reconciled
    # transactions didn't already match exactly and you chose to accept
    # the difference (SET_NULL if that adjustment transaction is later
    # deleted by hand - the session record itself still stands).
    adjustment_transaction = models.ForeignKey(
        Transaction, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-statement_date", "-created_at"]

    def __str__(self):
        return f"{self.account.name} reconciled to {regional.format_date(self.statement_date)}"
