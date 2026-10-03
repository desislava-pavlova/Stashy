import contextlib
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Case, Max, Q, Sum, Value, When
from django.db.models.functions import Coalesce, Lower
from django.urls import reverse
from django.utils import timezone

from audit.context import system_action

from . import regional
from .currency import CURRENCY_CHOICES, DEFAULT_CURRENCY
from .money import CENT, ZERO


def name_key(name):
    """How Stashy compares payee and category names: names that differ only in upper/lower
    case are the same one. Python's casefold() knows every alphabet. SQLite's own lower() and
    LIKE only fold A-Z, so to the database "ЛИДЛ" and "лидл" would be two different names."""
    return (name or "").strip().casefold()


def _with_name_key(update_fields):
    """save(update_fields=[..., "name"]) must write the name's key along with it."""
    if update_fields is not None and "name" in update_fields and "name_key" not in update_fields:
        return [*update_fields, "name_key"]
    return update_fields


class Profile(models.Model):
    """One login's preferences: main currency (D2) and regional formats (D10)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="stashy_profile")

    # Phase 15 (L1): who this stash belongs to - a person, a business or a family - shown
    # everywhere instead of the stash's internal folder name. Set once at creation; there's no
    # Settings field to change it (the user's own call, 16/09/2026).
    owner_name = models.CharField(max_length=60, default="", blank=True)
    # Phase 15 (L4): 0-3 while the welcome wizard is in progress (which step is next), 4 once
    # finished. An existing stash counts as already finished - see the migration.
    WELCOME_STEPS_DONE = 4
    welcome_step = models.PositiveSmallIntegerField(default=WELCOME_STEPS_DONE)

    main_currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default=DEFAULT_CURRENCY,
        help_text="Net worth is shown in this currency; new accounts start with it.",
    )
    time_zone = models.CharField(max_length=64, default=settings.TIME_ZONE)
    date_format = models.CharField(max_length=3, choices=regional.DATE_FORMAT_CHOICES, default="dmy")
    number_format = models.CharField(max_length=12, choices=regional.NUMBER_FORMAT_CHOICES, default="space_dot")
    first_day_of_week = models.PositiveSmallIntegerField(choices=regional.FIRST_DAY_CHOICES, default=1)

    # Multi-stash login (Phase 12): a stash's one-time recovery code, hashed the same way a
    # password is - shown to the user once, at stash creation, never stored in the clear.
    # Lockout state lives here too, since a stash's single Profile row is the natural place
    # for "this stash" data that isn't a regional preference.
    recovery_code_hash = models.CharField(max_length=128, blank=True, default="")
    failed_pin_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    # "Watched expenses" (R8, Phase 13) - a Settings-configurable replacement for the old
    # hardcoded "Bills & Utilities" report: any set of expense categories worth watching closely.
    watched_expenses_title = models.CharField(max_length=60, default="Bills & Utilities")
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
        """ "Alex" -> "Alex's"; "The Smiths" -> "The Smiths'" (L1)."""
        from . import stashes

        return stashes.possessive(self.owner_name)

    @property
    def setup_finished(self):
        return self.welcome_step >= self.WELCOME_STEPS_DONE

    @classmethod
    def for_user(cls, user):
        """The login's preferences, created from this computer's regional settings the first time."""
        profile = cls.objects.filter(user=user).first()
        if profile is not None:
            return profile
        detected = regional.detect_from_computer()
        with system_action("Preferences created from this computer's regional settings"):
            profile, _ = cls.objects.get_or_create(
                user=user,
                defaults={
                    "time_zone": detected.time_zone,
                    "date_format": detected.date_format,
                    "number_format": detected.number_format,
                    "first_day_of_week": detected.first_day_of_week,
                },
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


# (key, name, short explainer, account types), in the order accounts are listed.
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
ACCOUNT_TYPE_CHOICES = [(name, [(kind.value, kind.label) for kind in kinds]) for _, name, _, kinds in ACCOUNT_GROUPS]
_GROUP_INDEX = {kind.value: index for index, group in enumerate(ACCOUNT_GROUPS) for kind in group[3]}


class AccountQuerySet(models.QuerySet):
    def with_balances(self):
        """Compute every balance in the same query instead of one extra query per account."""
        return self.annotate(
            balance_total=Coalesce(
                Sum("transactions__amount"),
                Value(ZERO),
                output_field=models.DecimalField(max_digits=14, decimal_places=2),
            )
        )


class Account(models.Model):
    AccountType = AccountType

    name = models.CharField(max_length=120)
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES, default=AccountType.CASH)
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
        super().save(*args, **kwargs)
        self.ensure_transfer_category()

    def get_absolute_url(self):
        return reverse("finance:account_detail", args=[self.pk])

    def clean(self):
        super().clean()
        if self.pk is None:
            return
        saved_currency = Account.objects.filter(pk=self.pk).values_list("currency", flat=True).first()
        if saved_currency not in (None, self.currency) and self.transactions.exists():
            # Conversion only ever uses the latest known rate, not per-transaction history, so a
            # new currency would misstate every transaction already in the account.
            raise ValidationError({"currency": "This account already has transactions, so its currency can't change."})

    @property
    def balance(self):
        """The sum of the account's transactions - its opening balance is one of them (D1)."""
        if "balance_total" in self.__dict__:
            total = self.balance_total
        else:
            total = self.transactions.aggregate(total=Sum("amount"))["total"]
        # SQLite adds decimals as floating point, so round away results like 9.66000000000000.
        return (total or ZERO).quantize(CENT)

    def reconciled_balance(self):
        """Sum of already-reconciled transactions - the opening balance a new reconciliation
        (Phase 8) starts from, since everything reconciled so far is the settled starting point."""
        total = self.transactions.filter(reconciled=True).aggregate(total=Sum("amount"))["total"]
        return (total or ZERO).quantize(CENT)

    @property
    def group(self):
        """(key, name, explainer) of the account list group this account is shown in."""
        if not self.is_active:
            return INACTIVE_GROUP
        return ACCOUNT_GROUPS[_GROUP_INDEX.get(self.account_type, len(ACCOUNT_GROUPS) - 1)][:3]

    @property
    def group_order(self):
        return (
            len(ACCOUNT_GROUPS) if not self.is_active else _GROUP_INDEX.get(self.account_type, len(ACCOUNT_GROUPS) - 1)
        )

    def ensure_transfer_category(self):
        """Every account owns exactly one transfer category, named after it (D7). Returns it."""
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
    """Accounts split into Physical Liquid → Digital Liquid → Illiquid → Inactive, by name within each group."""
    groups = []
    for account in sorted(accounts, key=lambda a: (a.group_order, a.name.lower())):
        key, name, explainer = account.group
        if not groups or groups[-1]["key"] != key:
            groups.append({"key": key, "name": name, "explainer": explainer, "accounts": []})
        groups[-1]["accounts"].append(account)
    return groups


class Category(models.Model):
    class CategoryType(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"
        TRANSFER = "transfer", "Transfer"

    name = models.CharField(max_length=120)
    # name_key(name), kept by save(): the database checks uniqueness on this column.
    name_key = models.CharField(max_length=120, editable=False)
    # One level of subcategories only: a subcategory can't itself have children (see clean()).
    parent = models.ForeignKey("self", null=True, blank=True, related_name="subcategories", on_delete=models.CASCADE)
    category_type = models.CharField(max_length=10, choices=CategoryType.choices, default=CategoryType.EXPENSE)
    account = models.OneToOneField(
        Account,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.CASCADE,
        related_name="transfer_category",
        help_text="Set only on an account's own transfer category, which Stashy creates and deletes with the account.",
    )
    # Set when an import (Phase 9) created this category itself because the file named one
    # Stashy didn't already have - flagged for visibility/later cleanup rather than baked into
    # the name forever, same idea as system-managed categories getting a badge, not a label.
    is_auto_generated = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["parent__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["parent", "name_key"], name="unique_subcategory_name_ci"),
            # NULL parents never clash in SQL, so top-level names need their own rule. An account's
            # transfer category is left out: it's named after its account, and two accounts may
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

    def clean(self):
        super().clean()
        errors = {}
        parent = self.parent if self.parent_id else None
        if parent is not None:
            if parent.pk == self.pk:
                errors["parent"] = "A category can't be placed under itself."
            elif parent.parent_id:
                errors["parent"] = "A subcategory can't have its own subcategories - pick a main category."
            elif parent.account_id:
                errors["parent"] = "An account's transfer category can't have subcategories."
            elif self.pk and self.subcategories.exists():
                errors["parent"] = (
                    "This category already has its own subcategories, so it can't become one itself "
                    "— move or remove them first."
                )
        is_transfer = self.category_type == self.CategoryType.TRANSFER
        if is_transfer and not self.account_id:
            errors["category_type"] = "Transfer categories belong to an account and are created with it."
        elif self.account_id and not is_transfer:
            errors["category_type"] = "This category belongs to an account, so its type stays Transfer."
        if "parent" not in errors and not self.account_id:
            same_name = Category.objects.filter(name_key=name_key(self.name)).exclude(pk=self.pk)
            if parent is not None and same_name.filter(parent=parent).exists():
                errors["name"] = f"'{parent.name}' already has a subcategory with this name."
            elif parent is None and same_name.filter(parent__isnull=True, account__isnull=True).exists():
                errors["name"] = "A category with this name already exists."
        if errors:
            raise ValidationError(errors)

    @property
    def indented_name(self):
        """For dropdowns: a subcategory is indented under its main category (which is listed
        first, un-indented) instead of repeating the parent's name - the same look as the tick-list
        on the Transactions filter. Non-breaking spaces, since <option> text collapses normal ones."""
        return f"    {self.name}" if self.parent_id else self.name

    @property
    def is_system_managed(self):
        return self.account_id is not None

    def descendants(self):
        """All subcategories at any depth — the ones a delete would cascade to."""
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


# Income first, then Expense, then the system-managed Transfer categories last (Phase 4).
_CATEGORY_TYPE_ORDER = {
    Category.CategoryType.INCOME: 0,
    Category.CategoryType.EXPENSE: 1,
    Category.CategoryType.TRANSFER: 2,
}


def categories_alphabetical(queryset):
    """Order a Category queryset so a subcategory sorts right next to its own parent instead of
    in a separate "all subcategories" block after every top-level one - so typing a letter into
    a <select> (its built-in type-ahead) or scanning it by eye finds every category under, say,
    "Personal Care" together, in one alphabetical run (2026-09-14 follow-up). Grouped by type
    first - Income, then Expense, then Transfer last (2026-09-15 follow-up) - matching the
    category list page's own Income/Expense/Transfer grouping (grouped_categories(), below);
    a type with no categories in the queryset simply contributes no rows, no empty group to
    skip. For dropdowns only - the category *list page* keeps using grouped_categories() itself."""
    return queryset.annotate(
        _sort_type=Case(
            When(category_type=Category.CategoryType.INCOME, then=Value(0)),
            When(category_type=Category.CategoryType.EXPENSE, then=Value(1)),
            When(category_type=Category.CategoryType.TRANSFER, then=Value(2)),
            default=Value(99),
        ),
        _sort_group=Lower(Coalesce("parent__name", "name")),
        _sort_child=Case(When(parent__isnull=True, then=Value("")), default=Lower("name")),
    ).order_by("_sort_type", "_sort_group", "_sort_child")


def grouped_categories(categories):
    """Categories split into Income -> Expense -> Transfer groups; within each group, ordered
    by (top-level) parent name, then by subcategory name under it."""

    def sort_key(category):
        parent_name = (category.parent.name if category.parent_id else category.name).lower()
        return (
            _CATEGORY_TYPE_ORDER.get(category.category_type, 99),
            parent_name,
            category.parent_id is not None,
            category.name.lower(),
        )

    groups = []
    for category in sorted(categories, key=sort_key):
        key = category.category_type
        if not groups or groups[-1]["key"] != key:
            groups.append({"key": key, "name": category.get_category_type_display(), "categories": []})
        groups[-1]["categories"].append(category)
    return groups


class PayeeQuerySet(models.QuerySet):
    def named(self, name):
        """The payee called `name`, ignoring upper/lower case (at most one, by constraint)."""
        return self.filter(name_key=name_key(name))

    def resolve(self, name, reason="Payee created automatically from a new payee name"):
        """The payee called `name` in any case - the spelling already saved wins - or a new one.
        A new payee is logged as automatic with `reason`; reason=None keeps the caller's own
        system_action (an import's "Imported from <file>")."""
        payee = self.named(name).first()
        if payee is None:
            with system_action(reason) if reason else contextlib.nullcontext():
                payee = self.create(name=name.strip())
        return payee


class Payee(models.Model):
    """A payee Stashy has seen on a transaction. Memorization (Phase 6) is computed live from
    transaction history — one category/memo/amount per (payee, category) pair, not a cached
    default field — so it's never stale and there's nothing to keep in sync."""

    # Shared payees Stashy uses for the transactions it creates for you.
    OPENING_BALANCE = "Opening Balance"
    TRANSFER_MONEY = "Transfer Money"
    RECONCILIATION_ADJUSTMENT = "Reconciliation Adjustment"
    SYSTEM_NAMES = (OPENING_BALANCE, TRANSFER_MONEY, RECONCILIATION_ADJUSTMENT)

    name = models.CharField(max_length=150)
    # name_key(name), kept by save(): the database checks uniqueness on this column.
    name_key = models.CharField(max_length=150, editable=False)
    is_active = models.BooleanField(default=True)

    objects = PayeeQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
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

    @classmethod
    def system(cls, name):
        return cls.objects.resolve(name, reason=f"Payee '{name}' created for transactions Stashy makes")

    def category_history(self):
        """One entry per category ever used with this payee (most recently used first): the
        most recent Transaction in each — its category/memo/amount are what a new transaction
        against this payee would offer to autofill from."""
        seen, history = set(), []
        for transaction in self.transactions.select_related("category__parent", "account").order_by(
            "-date", "-transaction_number", "-created_at"
        ):
            if transaction.category_id not in seen:
                seen.add(transaction.category_id)
                history.append(transaction)
        return history


class TransactionQuerySet(models.QuerySet):
    """The building blocks every report query is made of (finance/reporting.py)."""

    def in_range(self, start, end):
        """Dated from `start` to `end`, both days included."""
        return self.filter(date__gte=start, date__lte=end)

    def reportable(self):
        """What counts as income or spending: it has a category, and that category isn't an
        account's Transfer category (D7). Opening balances use the Transfer category too (D1)."""
        return self.filter(category__isnull=False).exclude(category__account__isnull=False)

    def of_kind(self, expense_only=False, income_only=False):
        """Only expense or only income categories, or both when neither flag is set. The
        category's type decides, never the amount's sign (R1)."""
        if expense_only:
            return self.filter(category__category_type=Category.CategoryType.EXPENSE)
        if income_only:
            return self.filter(category__category_type=Category.CategoryType.INCOME)
        return self

    def for_account(self, account):
        """Only `account`'s transactions, or every account's when `account` is None."""
        return self.filter(account=account) if account else self


class Transaction(models.Model):
    """Sign convention: positive amount = money in (income/deposit),
    negative amount = money out (expense/withdrawal)."""

    account = models.ForeignKey(Account, related_name="transactions", on_delete=models.CASCADE)
    date = models.DateField()
    transaction_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Shared across all accounts, starting again at 1 every calendar year. You can change it.",
    )
    payee = models.ForeignKey(Payee, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions")
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions"
    )
    memo = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reconciled = models.BooleanField(default=False)
    import_batch = models.ForeignKey(
        "ImportBatch", null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions"
    )
    import_fingerprint = models.CharField(
        max_length=64, blank=True, db_index=True, help_text="Hash used to detect duplicate imports"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TransactionQuerySet.as_manager()

    class Meta:
        # transaction_number is an integer column, so it sorts 1, 2, 10 - never "1, 10, 2" like text would (D9).
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
        """The next transaction number for `on`'s calendar year - shared across every account,
        not scoped to one (D9). Duplicates are allowed on purpose: numbers are editable and
        imports reuse them. Two requests can't both read the same MAX() either, since every
        write request holds the stash's write lock (BEGIN IMMEDIATE) from its start."""
        highest = Transaction.objects.filter(date__year=on.year).aggregate(highest=Max("transaction_number"))[
            "highest"
        ]
        return (highest or 0) + 1


class ImportBatch(models.Model):
    """Tracks a single CSV/OFX/QFX import run so it can be reviewed or undone."""

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


AMOUNT_NOT_NEGATIVE = (
    "Enter the amount as a positive number - the type already says whether it's income or an expense."
)


class Reminder(models.Model):
    """A recurring bill or income reminder."""

    class Frequency(models.TextChoices):
        ONE_TIME = "one_time", "One-time"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        YEARLY = "yearly", "Yearly"

    # Frequency value -> the unit word shown next to the "Every N ..." interval field
    # (2026-09-14 follow-up, replacing the old fixed "Every 2 weeks" choice with a free
    # interval - "weekly" + interval=2 now means the same thing "biweekly" used to).
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

    # What a "whole series" edit copies onto the series head and its still-open occurrences:
    # the same fields regenerate_occurrences() copies onto a new occurrence. The schedule
    # (frequency, interval, dates) stays per row, since an occurrence's own date is what makes it
    # that occurrence.
    SERIES_FIELDS = ["account", "payee", "category", "amount", "reminder_type", "notes"]

    # "Due soon" is treated as urgent enough to need action; anything further out than this
    # many days (and not already overdue) is either "paid" (see status()) or plain pending.
    DUE_SOON_DAYS = 7

    reminder_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=Category.CategoryType.EXPENSE)
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="reminders")
    payee = models.ForeignKey(Payee, null=True, blank=True, on_delete=models.SET_NULL, related_name="reminders")
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0, message=AMOUNT_NOT_NEGATIVE)],
        help_text="Enter as a positive number, or leave blank for a variable-amount bill",
    )
    frequency = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.ONE_TIME)
    # How many periods between occurrences - "weekly" + interval=2 is "every 2 weeks" (2026-09-14
    # follow-up, replacing the old fixed BIWEEKLY choice). Meaningless for ONE_TIME; always 1 there.
    interval = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    next_due_date = models.DateField()
    end_of_period = models.BooleanField(default=False, verbose_name="Last day of period")
    end_date = models.DateField(null=True, blank=True)
    # Points at the transaction created the last time this reminder was entered (Enter, not
    # Skip) - the basis for the computed "paid" status (D6 follow-up, 2026-09-13). Deleting
    # that transaction clears this automatically (SET_NULL) and the status reverts; deleting
    # the reminder never touches the transaction, since the FK points the other way.
    last_entered_transaction = models.ForeignKey(
        "Transaction", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # A one-time reminder has nothing to advance to once acted on, so Skip marks it done
    # instead of moving a due date that doesn't exist for it (2026-09-14 follow-up).
    skipped = models.BooleanField(default=False)
    # Set only on an auto-generated future occurrence of a recurring reminder (below), pointing
    # back at the recurring "series head" that generated it. Null on every reminder you create
    # by hand, recurring or not.
    series = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="occurrences")
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["next_due_date"]
        constraints = [
            # The reminder's type says income or expense, so the amount itself is never negative.
            models.CheckConstraint(
                condition=Q(amount__isnull=True) | Q(amount__gte=0),
                name="reminder_amount_not_negative",
            ),
            models.CheckConstraint(condition=Q(interval__gte=1), name="reminder_interval_at_least_one"),
        ]

    def __str__(self):
        # No separate name (removed 13/09/2026) - identified like a pending transaction, by
        # who it's for and how much, the same way Transaction.__str__ identifies a real one.
        who = self.payee.name if self.payee_id else (self.category.full_name if self.category_id else "Reminder")
        amount = self.signed_amount()
        return f"{regional.format_date(self.next_due_date)} | {who} | {amount if amount is not None else 'varies'}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Same pattern as Account.save() -> ensure_transfer_category(): a recurring reminder
        # keeps its own future occurrences in sync on every save, not just through the form,
        # so direct ORM use (admin, scripts, shell) gets the same auto-generation for free.
        if self.frequency != self.Frequency.ONE_TIME:
            self.regenerate_occurrences()

    def delete(self, *args, **kwargs):
        # Deleting a whole series takes its still-open occurrences with it (series is
        # on_delete=CASCADE) - but an occurrence you've already paid or skipped is a real
        # historical record now, not just a preview, so it's detached (series -> null) and
        # kept standing on its own instead of disappearing with the series (2026-09-14
        # follow-up).
        if self.frequency != self.Frequency.ONE_TIME:
            resolved = self.occurrences.filter(Q(last_entered_transaction__isnull=False) | Q(skipped=True))
            with system_action(f"Occurrence detached from deleted recurring reminder ({self})"):
                for occurrence in resolved:
                    occurrence.series = None
                    occurrence.save(update_fields=["series"])
        return super().delete(*args, **kwargs)

    def signed_amount(self):
        if self.amount is None:
            return None
        return self.amount if self.reminder_type == Category.CategoryType.INCOME else -self.amount

    def status(self, today=None):
        """One of "overdue", "due_soon", "paid", "skipped" or "" (= pending).

        A one-time reminder (a real one-off, or an auto-generated future occurrence of a
        recurring one, 2026-09-14) has no next period to fall back into once it's been acted
        on, so Paid/Skipped there is permanent - unlike a recurring reminder, which just moves
        on to its next period and so only ever shows overdue/due soon/pending.
        """
        if self.skipped:
            return "skipped"
        if self.frequency == self.Frequency.ONE_TIME:
            return "paid" if self.last_entered_transaction_id else self._date_status(today)
        # A recurring reminder always stands for its next, not-yet-entered period (entering or
        # skipping advances it), so it is never "paid" itself - even if an older row still
        # carries a stale link from before that was cleared on advance.
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
        """How far ahead to auto-generate occurrences when a reminder has no end date:
        90 days out, or through 31 December of this year - whichever is further. Late in the
        year the 90-day floor wins and spills into next year; earlier in the year, year-end is
        further out, matching what the "Yearly" filter button on the reminder list shows."""
        today = today or timezone.localdate()
        return max(today + timedelta(days=90), date(today.year, 12, 31))

    def _period_end(self, on):
        """Last day of `on`'s period for this reminder's frequency, for end-of-period mode (D5).

        The period itself is always the natural calendar month/quarter/year `on` falls in,
        regardless of `interval` - "every 3 months, last day of period" still means the last
        day of whichever month it lands on, not a 3-month span. Weekly is the one case where
        the interval genuinely changes what "the period" spans (2026-09-14 follow-up)."""
        if self.frequency == self.Frequency.YEARLY:
            return date(on.year, 12, 31)
        if self.frequency == self.Frequency.QUARTERLY:
            quarter_end_month = ((on.month - 1) // 3) * 3 + 3
            next_month = date(on.year, quarter_end_month, 1) + relativedelta(months=1)
            return next_month - timedelta(days=1)
        if self.frequency == self.Frequency.MONTHLY:
            next_month = date(on.year, on.month, 1) + relativedelta(months=1)
            return next_month - timedelta(days=1)
        # WEEKLY: last day of the `interval`-week span containing `on`, anchored so weeks
        # start Monday (D10's convention) - interval=1 is just "last day of this week".
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
        """The next due date after `from_date` for this reminder's own frequency/interval/
        end-of-period settings - pure (no save), so `regenerate_occurrences` can walk it
        forward repeatedly."""
        unit = self._FREQUENCY_UNITS[self.frequency]
        count = self.interval * (self._QUARTERLY_MONTH_MULTIPLIER if self.frequency == self.Frequency.QUARTERLY else 1)
        advanced = from_date + relativedelta(**{unit: count})
        return self._period_end(advanced) if self.end_of_period else advanced

    def record_period(self, transaction=None, skipped=False):
        """Leave a standing Paid/Skipped record of the period this recurring reminder is about
        to move past. Entering or skipping advances the reminder itself to the next period, so
        without this the period you just dealt with would vanish from the list."""
        with system_action(f"Resolved period recorded for recurring reminder ({self})"):
            return Reminder.objects.create(
                series=self,
                frequency=self.Frequency.ONE_TIME,
                next_due_date=self.next_due_date,
                account=self.account,
                payee=self.payee,
                category=self.category,
                amount=self.amount,
                reminder_type=self.reminder_type,
                notes=self.notes,
                last_entered_transaction=transaction,
                skipped=skipped,
            )

    def advance_due_date(self):
        self.next_due_date = self._advance(self.next_due_date)
        who = self.payee.name if self.payee_id else (self.category.full_name if self.category_id else "Reminder")
        with system_action(f"Next due date moved forward one period for reminder ({who})"):
            self.save(update_fields=["next_due_date"])  # save() -> regenerate_occurrences()

    def mark_entered(self, transaction):
        """Enter (D6): `transaction` pays this reminder's current period. A one-time reminder
        links to it and is "Paid" for good; a recurring one records the period and moves on."""
        if self.frequency == self.Frequency.ONE_TIME:
            self.last_entered_transaction = transaction
            self.save(update_fields=["last_entered_transaction"])
        else:
            self._move_past_period(transaction=transaction)

    def skip(self):
        """Skip (D6): this period won't be paid. A one-time reminder is marked done, since it
        has no next period; a recurring one records the period as skipped and moves on."""
        if self.frequency == self.Frequency.ONE_TIME:
            self.skipped = True
            self.save(update_fields=["skipped"])
        else:
            self._move_past_period(skipped=True)

    def apply_to_series(self):
        """A "whole series" edit: copy this reminder's SERIES_FIELDS onto its series head (when
        it's an occurrence) and onto every occurrence not yet paid or skipped."""
        root = self.series if self.series_id else self
        if root.pk != self.pk:
            for field in self.SERIES_FIELDS:
                setattr(root, field, getattr(self, field))
            root.save(update_fields=self.SERIES_FIELDS)
        targets = root.occurrences.filter(last_entered_transaction__isnull=True, skipped=False).exclude(pk=self.pk)
        with system_action(f"Series-wide edit applied from reminder ({self})"):
            for occurrence in targets:
                for field in self.SERIES_FIELDS:
                    setattr(occurrence, field, getattr(self, field))
                occurrence.save(update_fields=self.SERIES_FIELDS)

    def _move_past_period(self, transaction=None, skipped=False):
        self.record_period(transaction=transaction, skipped=skipped)
        # The reminder now stands for the next period, which nobody has paid yet - an old link
        # left from an earlier Enter would make it show as "Paid".
        if self.last_entered_transaction_id:
            self.last_entered_transaction = None
            self.save(update_fields=["last_entered_transaction"])
        self.advance_due_date()

    def regenerate_occurrences(self):
        """Keep this recurring reminder's auto-generated future occurrences in sync with its
        own due date, frequency and end date (2026-09-14 follow-up) - one real `Reminder` row
        per future occurrence, so each can be entered/skipped/edited on its own, same as any
        other reminder. Only ever called on a series head (never on a one-time reminder or an
        occurrence itself, both of which have nothing to generate).

        Already-resolved occurrences (paid or skipped) are never touched, even if a shortened
        end date would otherwise drop them - and an occurrence that's still just a preview but
        already exists for a wanted date is left alone too, so hand-editing one instance (e.g.
        a higher amount one month) survives the next regeneration.
        """
        if self.frequency == self.Frequency.ONE_TIME:
            return
        horizon = self.end_date or self.default_generation_horizon()
        wanted_dates = []
        cursor = self.next_due_date
        for _ in range(500):  # safety cap - far more occurrences than any real horizon needs
            cursor = self._advance(cursor)
            if cursor > horizon:
                break
            wanted_dates.append(cursor)
        existing = {o.next_due_date: o for o in self.occurrences.all()}
        with system_action(f"Auto-generated occurrences updated for recurring reminder ({self})"):
            for occ_date, occ in existing.items():
                if occ_date not in wanted_dates and not occ.last_entered_transaction_id and not occ.skipped:
                    occ.delete()
            for occ_date in wanted_dates:
                if occ_date not in existing:
                    Reminder.objects.create(
                        series=self,
                        frequency=self.Frequency.ONE_TIME,
                        next_due_date=occ_date,
                        account=self.account,
                        payee=self.payee,
                        category=self.category,
                        amount=self.amount,
                        reminder_type=self.reminder_type,
                        notes=self.notes,
                    )


class ExchangeRate(models.Model):
    """1 unit of base_currency was worth `rate` units of quote_currency on `date` (D2)."""

    class Source(models.TextChoices):
        MANUAL = "manual", "Typed in"
        FRANKFURTER = "Frankfurter", "Frankfurter (ECB)"

    base_currency = models.CharField("from", max_length=3, choices=CURRENCY_CHOICES)
    quote_currency = models.CharField("to", max_length=3, choices=CURRENCY_CHOICES)
    rate = models.DecimalField(
        max_digits=20, decimal_places=10, help_text="How much 1 unit of the first currency is worth in the second."
    )
    date = models.DateField()
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.MANUAL)

    class Meta:
        ordering = ["-date", "base_currency", "quote_currency"]
        constraints = [
            models.UniqueConstraint(
                fields=["base_currency", "quote_currency", "date", "source"], name="unique_rate_per_day_and_source"
            ),
            models.CheckConstraint(condition=Q(rate__gt=0), name="exchange_rate_positive"),
        ]

    def __str__(self):
        rate = f"{self.rate.normalize():f}"
        return f"1 {self.base_currency} = {rate} {self.quote_currency} ({regional.format_date(self.date)})"


class Reconciliation(models.Model):
    """A record of one reconciliation session (Phase 8) - kept purely as an audit trail
    ("why is this balance off") the same way the rest of the app already treats history;
    the actual settled/unsettled state of each transaction lives on Transaction.reconciled."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="reconciliations")
    statement_date = models.DateField()
    statement_balance = models.DecimalField(max_digits=12, decimal_places=2)
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2)
    # Set only when the statement balance and the reconciled transactions didn't already
    # match exactly and you chose to accept the difference (SET_NULL if that adjustment
    # transaction is later deleted by hand - the session record itself still stands).
    adjustment_transaction = models.ForeignKey(
        Transaction, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-statement_date", "-created_at"]

    def __str__(self):
        return f"{self.account.name} reconciled to {regional.format_date(self.statement_date)}"
