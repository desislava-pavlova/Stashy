from decimal import Decimal

from django.db import models
from django.urls import reverse


class Account(models.Model):
    class AccountType(models.TextChoices):
        CHECKING = "checking", "Checking"
        SAVINGS = "savings", "Savings"
        CREDIT_CARD = "credit_card", "Credit Card"
        CASH = "cash", "Cash"
        INVESTMENT = "investment", "Investment"
        LOAN = "loan", "Loan"

    name = models.CharField(max_length=120)
    account_type = models.CharField(max_length=20, choices=AccountType.choices, default=AccountType.CHECKING)
    institution = models.CharField(max_length=120, blank=True)
    starting_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("finance:account_detail", args=[self.pk])

    @property
    def balance(self):
        total = self.transactions.aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")
        return self.starting_balance + total


class Category(models.Model):
    class CategoryType(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    name = models.CharField(max_length=120)
    parent = models.ForeignKey(
        "self", null=True, blank=True, related_name="subcategories", on_delete=models.CASCADE
    )
    category_type = models.CharField(max_length=10, choices=CategoryType.choices, default=CategoryType.EXPENSE)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["parent__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["name", "parent"], name="unique_category_name_per_parent")
        ]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f"{self.parent.name}: {self.name}" if self.parent_id else self.name


class Payee(models.Model):
    """A 'memorized payee' - remembers the last/default category, memo and amount
    so new transactions can autofill from it (Quicken's QuickFill)."""

    name = models.CharField(max_length=150, unique=True)
    default_category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    default_memo = models.CharField(max_length=255, blank=True)
    default_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def refresh_defaults_from_transaction(self, transaction):
        """Update memorized defaults whenever a transaction against this payee is saved."""
        self.default_category = transaction.category
        self.default_memo = transaction.memo
        self.default_amount = transaction.amount
        self.save(update_fields=["default_category", "default_memo", "default_amount"])


class Tag(models.Model):
    name = models.CharField(max_length=60, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Transaction(models.Model):
    """Sign convention: positive amount = money in (income/deposit),
    negative amount = money out (expense/withdrawal)."""

    account = models.ForeignKey(Account, related_name="transactions", on_delete=models.CASCADE)
    date = models.DateField()
    payee = models.ForeignKey(Payee, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions")
    payee_name = models.CharField(max_length=150, blank=True, help_text="Free-text payee if not memorized")
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions"
    )
    memo = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    cleared = models.BooleanField(default=False)
    reconciled = models.BooleanField(default=False)
    transfer_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.SET_NULL, related_name="incoming_transfers"
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="transactions")
    import_batch = models.ForeignKey(
        "ImportBatch", null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions"
    )
    import_fingerprint = models.CharField(
        max_length=64, blank=True, db_index=True,
        help_text="Hash used to detect duplicate imports"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [
            models.Index(fields=["date"]),
            models.Index(fields=["account", "date"]),
        ]

    def __str__(self):
        return f"{self.date} | {self.display_payee} | {self.amount}"

    @property
    def display_payee(self):
        return self.payee.name if self.payee_id else self.payee_name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.payee_id:
            self.payee.refresh_defaults_from_transaction(self)


class ImportBatch(models.Model):
    """Tracks a single CSV/OFX/QFX import run so it can be reviewed or undone."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="import_batches")
    source_filename = models.CharField(max_length=255)
    imported_at = models.DateTimeField(auto_now_add=True)
    row_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-imported_at"]

    def __str__(self):
        return f"{self.source_filename} ({self.imported_at:%Y-%m-%d %H:%M})"


class Reminder(models.Model):
    """A recurring bill or income reminder."""

    class Frequency(models.TextChoices):
        WEEKLY = "weekly", "Weekly"
        BIWEEKLY = "biweekly", "Every 2 weeks"
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        YEARLY = "yearly", "Yearly"

    name = models.CharField(max_length=150)
    reminder_type = models.CharField(
        max_length=10, choices=Category.CategoryType.choices, default=Category.CategoryType.EXPENSE
    )
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="reminders")
    payee = models.ForeignKey(Payee, null=True, blank=True, on_delete=models.SET_NULL, related_name="reminders")
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text="Enter as a positive number")
    frequency = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.MONTHLY)
    next_due_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    auto_enter_days_before = models.PositiveSmallIntegerField(
        default=0, help_text="0 = never auto-enter, just remind"
    )
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["next_due_date"]

    def __str__(self):
        return f"{self.name} ({self.get_frequency_display()})"

    def signed_amount(self):
        return self.amount if self.reminder_type == Category.CategoryType.INCOME else -self.amount

    def advance_due_date(self):
        from dateutil.relativedelta import relativedelta

        deltas = {
            self.Frequency.WEEKLY: relativedelta(weeks=1),
            self.Frequency.BIWEEKLY: relativedelta(weeks=2),
            self.Frequency.MONTHLY: relativedelta(months=1),
            self.Frequency.QUARTERLY: relativedelta(months=3),
            self.Frequency.YEARLY: relativedelta(years=1),
        }
        self.next_due_date = self.next_due_date + deltas[self.frequency]
        self.save(update_fields=["next_due_date"])
