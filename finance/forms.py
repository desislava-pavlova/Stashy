from datetime import date

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from audit.context import system_action

from . import regional
from .models import Account, Category, Payee, Profile, Reminder, Transaction, categories_alphabetical
from .money import ZERO


class DateInput(forms.DateInput):
    """A text input in the active regional date format; base.html's flatpickr matches it."""

    def __init__(self, attrs=None):
        prefs = regional.current()
        defaults = {"data-datepicker": "", "placeholder": prefs.date_placeholder, "autocomplete": "off"}
        super().__init__(attrs={**defaults, **(attrs or {})}, format=prefs.date_pattern)


def _use_regional_dates(form):
    """Point every DateField at the active regional format, for parsing and for its widget."""
    prefs = regional.current()
    for field in form.fields.values():
        if isinstance(field, forms.DateField):
            field.input_formats = prefs.date_input_formats
            if isinstance(field.widget, DateInput):
                field.widget.attrs["placeholder"] = prefs.date_placeholder
                field.widget.format = prefs.date_pattern


class BootstrapFormMixin:
    """Adds Bootstrap 5 form-control classes to every field automatically, and regional date formats."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs.setdefault("class", "form-select")
            else:
                widget.attrs.setdefault("class", "form-control")
        _use_regional_dates(self)


class AccountForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Account
        fields = ["name", "account_type", "currency", "institution", "is_active", "notes"]
        # Django's default Textarea is 10 rows - taller than this form needs, and (with the
        # opening-balance fields on top, for a new account) enough to push Save off-screen.
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # Currency conversion only ever uses the latest known rate, not per-transaction
            # history, so changing it after transactions exist would misstate every past one.
            self.fields["currency"].disabled = True

    def clean_is_active(self):
        is_active = self.cleaned_data["is_active"]
        # Only ever checks an *existing* account's real, already-saved balance - a brand new
        # account (no pk yet) has no transactions to have a balance from regardless of what's
        # typed into this same submission (AccountCreateForm.clean() below covers that case).
        if not is_active and self.instance.pk and self.instance.balance != ZERO:
            raise ValidationError(
                "This account still has a balance - it can't be made inactive until its balance is zero."
            )
        return is_active


class AccountCreateForm(AccountForm):
    """Account creation also asks for an opening balance, which becomes a real transaction (D1).
    Every new account gets one, even a zero-amount one dated today by default (2026-09-15
    follow-up) - so a fresh account's register never starts empty, and reconciliation always
    has a starting transaction to reconcile from."""

    opening_balance = forms.DecimalField(max_digits=12, decimal_places=2, required=False, initial=0)
    opening_balance_date = forms.DateField(required=False, widget=DateInput(), initial=date.today)

    field_order = ["name", "account_type", "currency", "institution", "opening_balance", "opening_balance_date", "is_active", "notes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"].disabled = False
        self.fields["currency"].initial = regional.current().main_currency

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("opening_balance") is None:
            cleaned["opening_balance"] = ZERO
        if not cleaned.get("opening_balance_date"):
            cleaned["opening_balance_date"] = date.today()
        if not cleaned.get("is_active", True) and cleaned.get("opening_balance"):
            raise ValidationError("A new account can't be inactive if it has an opening balance.")
        return cleaned

    def save(self, commit=True):
        account = super().save(commit=commit)
        amount = self.cleaned_data.get("opening_balance", ZERO)
        if commit:
            with system_action(f"Opening balance transaction created for account '{account.name}'"):
                Transaction.objects.create(
                    account=account,
                    date=self.cleaned_data["opening_balance_date"],
                    payee=Payee.system(Payee.OPENING_BALANCE),
                    category=account.ensure_transfer_category(),
                    memo="Opening Balance",
                    amount=amount,
                )
        return account


class TransferForm(forms.Form):
    """Backs the account page's Transfer button (D7): creates the paired transactions."""

    to_account = forms.ModelChoiceField(queryset=Account.objects.none(), label="Transfer to")
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0.01, help_text="Enter a positive amount.")
    date = forms.DateField(widget=DateInput(), initial=date.today)

    def __init__(self, *args, from_account=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.from_account = from_account
        # The same amount is used on both legs (no conversion happens here), so the
        # destination must share this account's currency — otherwise "100" would leave as
        # 100 EUR and arrive as 100 USD, silently inventing or destroying money.
        self.fields["to_account"].queryset = Account.objects.filter(
            is_active=True, currency=from_account.currency
        ).exclude(pk=from_account.pk)
        if not self.fields["to_account"].queryset.exists():
            self.fields["to_account"].help_text = (
                f"No other active {from_account.currency} account to transfer to yet."
            )
        for field in self.fields.values():
            if not isinstance(field.widget, forms.DateInput):
                field.widget.attrs.setdefault("class", "form-control")
            else:
                field.widget.attrs.setdefault("class", "form-control")
        self.fields["to_account"].widget.attrs["class"] = "form-select"
        _use_regional_dates(self)

    def save(self):
        to_account = self.cleaned_data["to_account"]
        amount, on = self.cleaned_data["amount"], self.cleaned_data["date"]
        payee = Payee.system(Payee.TRANSFER_MONEY)
        with system_action(f"Transfer from '{self.from_account.name}' to '{to_account.name}'"):
            out = Transaction.objects.create(
                account=self.from_account, date=on, payee=payee,
                category=self.from_account.ensure_transfer_category(),
                memo=f"Transfer to {to_account.name}", amount=-amount,
            )
            into = Transaction.objects.create(
                account=to_account, date=on, payee=payee,
                category=to_account.ensure_transfer_category(),
                memo=f"Transfer from {self.from_account.name}", amount=amount,
            )
        return out, into


class ReconcileStartForm(forms.Form):
    """Step 1 of the account page's Reconcile button (Phase 8): what your statement says."""

    statement_date = forms.DateField(widget=DateInput(), initial=date.today)
    statement_balance = forms.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="The ending balance shown on your bank or card statement.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["statement_balance"].widget.attrs.setdefault("class", "form-control")
        _use_regional_dates(self)


class ReconcileFinishForm(forms.Form):
    """Step 2's "Finish reconciling" (Phase 8): the statement figures carried over from step 1
    in hidden fields, plus the ticked transactions. Validated like any other form, so a
    tampered or half-sent submission gets a message instead of a server error."""

    statement_date = forms.DateField(input_formats=["%Y-%m-%d"])
    statement_balance = forms.DecimalField(max_digits=12, decimal_places=2)
    transaction = forms.ModelMultipleChoiceField(queryset=Transaction.objects.none(), required=False)
    accept_difference = forms.BooleanField(required=False)

    def __init__(self, *args, account, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["transaction"].queryset = account.transactions.filter(reconciled=False)


class CategoryParentSelect(forms.Select):
    """Tags each <option> with its category's own type, so JS can narrow the Parent list to
    only categories matching whichever type is currently chosen above (Phase 4)."""

    def __init__(self, *args, categories_by_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.categories_by_id = categories_by_id or {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        category = self.categories_by_id.get(getattr(value, "value", value))
        if category:
            option["attrs"]["data-category-type"] = category.category_type
        return option


class CategoryForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "parent", "category_type"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Transfer is a system-managed type (D7/D4) — never chosen by hand.
        self.fields["category_type"].choices = [
            choice for choice in self.fields["category_type"].choices if choice[0] != Category.CategoryType.TRANSFER
        ]
        parent_field = self.fields["parent"]
        # Only a top-level category can be a parent (one level of subcategories, no tree) —
        # this alone also rules out a category ever being its own ancestor.
        parents = Category.objects.filter(parent__isnull=True, account__isnull=True)
        # Build the type-aware widget before assigning the queryset, so the queryset setter's
        # own "widget.choices = ..." (Django's ModelChoiceField internals) lands on this widget.
        parent_field.widget = CategoryParentSelect(
            attrs=parent_field.widget.attrs, categories_by_id={c.pk: c for c in parents}
        )
        parent_field.queryset = parents

    # The one-level rule and duplicate names are checked by Category.clean(), which a ModelForm
    # runs for you - so the admin and this form give the same messages.

    def clean(self):
        cleaned = super().clean()
        if self.instance.is_system_managed:
            raise ValidationError("This category belongs to an account and is managed automatically.")
        return cleaned


class PayeeForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Payee
        fields = ["name", "is_active"]


class TransactionForm(BootstrapFormMixin, forms.ModelForm):
    payee_name = forms.CharField(
        required=False,
        label="Payee",
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
        help_text="Start typing — matching memorized payees will autofill category/memo/amount.",
    )

    class Meta:
        model = Transaction
        fields = [
            "account", "date", "transaction_number", "payee_name", "category", "memo", "amount",
            "reconciled",
        ]
        widgets = {
            "date": DateInput(),
        }
        labels = {"transaction_number": "Number"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "transaction_number" in self.fields:
            self.fields["transaction_number"].help_text = "Prefilled with the next available number (shared across accounts, starting over each year, D9) — change it, or clear it to have Stashy pick again."
        # Transfer categories are created automatically by the Transfer button, not picked by hand
        # here — except editing a transaction that already has one (an opening balance or transfer
        # leg) must still offer its own category, or saving the form would reject it as invalid.
        self.fields["category"].queryset = categories_alphabetical(self.fields["category"].queryset.filter(
            Q(account__isnull=True) | Q(pk=self.instance.category_id)
        ))
        self.fields["category"].label_from_instance = lambda category: category.indented_name

    def clean_payee_name(self):
        return (self.cleaned_data.get("payee_name") or "").strip()

    def save(self, commit=True):
        # Payees are resolved here, not in clean(), so a form with errors never creates one.
        instance = super().save(commit=False)
        name = self.cleaned_data["payee_name"]
        # Names that differ only in case are one payee, and the spelling already saved wins.
        instance.payee = Payee.objects.resolve(name) if name else None
        if instance.transaction_number is None:
            # Transaction.save() only auto-assigns on create; clearing the field on an edit
            # (e.g. after moving a transaction to a different account) should renumber it too.
            instance.transaction_number = Transaction.next_number(instance.date)
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class ReminderForm(BootstrapFormMixin, forms.ModelForm):
    # Not a model field - a friendlier affordance than "just leave the date blank" for a
    # reminder with no end date (Phase 7).
    no_end_date = forms.BooleanField(required=False, label="No end date")

    class Meta:
        model = Reminder
        fields = [
            "reminder_type", "account", "payee", "category", "amount", "frequency", "interval",
            "next_due_date", "end_of_period", "end_date", "no_end_date", "notes",
        ]
        widgets = {
            "next_due_date": DateInput(),
            "end_date": DateInput(),
        }
        labels = {
            "interval": "Every",
        }
        help_texts = {
            "amount": "Enter as a positive number, or leave blank for a variable-amount bill",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount"].required = False
        self.fields["interval"].required = False
        category_field = self.fields["category"]
        categories = categories_alphabetical(Category.objects.filter(
            Q(account__isnull=True) | Q(pk=self.instance.category_id)
        ))
        # Picking Income/Expense narrows the Category list to that type only (same
        # type-tagged-option pattern CategoryForm already uses for its own Parent field).
        category_field.widget = CategoryParentSelect(
            attrs=category_field.widget.attrs, categories_by_id={c.pk: c for c in categories}
        )
        category_field.queryset = categories
        category_field.label_from_instance = lambda category: category.indented_name
        self.fields["no_end_date"].initial = not self.instance.end_date if self.instance.pk else True

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("no_end_date"):
            cleaned["end_date"] = None
        if not cleaned.get("interval"):
            cleaned["interval"] = 1
        # An auto-generated occurrence (series is set) can't become its own new recurring
        # series head - it exists to represent one specific future date (2026-09-14 follow-up).
        if self.instance.series_id and cleaned.get("frequency") != Reminder.Frequency.ONE_TIME:
            raise ValidationError(
                "This reminder was auto-generated from a recurring one, so it can't have its "
                "own frequency - edit the original recurring reminder instead."
            )
        return cleaned


class ImportUploadForm(forms.Form):
    DATE_FORMAT_CHOICES = [("auto", "Detect automatically (recommended)")] + regional.DATE_FORMAT_CHOICES

    account = forms.ModelChoiceField(
        queryset=Account.objects.filter(is_active=True),
        help_text="Used for every row, unless the file has its own Account column (Phase 9) - that takes over per row.",
    )
    file = forms.FileField(
        help_text="CSV or XLSX (Date, Payee, Memo, Amount, optionally Num/Account/Category/Reconciled) or OFX/QFX"
    )
    date_format = forms.ChoiceField(
        choices=DATE_FORMAT_CHOICES, required=False, initial="auto",
        help_text="Stashy checks the file's own dates first - override only if it guesses wrong.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].widget.attrs["class"] = "form-select"
        self.fields["file"].widget.attrs["class"] = "form-control"
        self.fields["date_format"].widget.attrs["class"] = "form-select"


class DateRangeFilterForm(forms.Form):
    RANGE_CHOICES = [
        ("this_month", "This month"),
        ("last_month", "Last month"),
        ("this_quarter", "This quarter"),
        ("ytd", "Year to date"),
        ("last_12_months", "Last 12 months"),
        ("this_year", "This year"),
        ("last_year", "Last year"),
        ("earliest", "Earliest to date"),
        ("custom", "Custom range"),
    ]
    # How many periods the same length as the selected date range to average against,
    # immediately preceding it (2026-09-15 follow-up) - not a separate bucketing granularity;
    # see reporting.vs_average_periods().
    INTERVAL_CHOICES = [
        ("3", "Last 3 periods"),
        ("6", "Last 6 periods"),
        ("9", "Last 9 periods"),
        ("12", "Last 12 periods"),
    ]
    # Same granularities as Interval, plus half-year - only meaningful for slicing a date
    # range into columns (Interval instead sizes the trailing window for "vs. average", which
    # a half-year is too coarse to average 12 months of data over usefully).
    SUBTOTAL_CHOICES = [
        ("", "No subtotal"),
        ("week", "Week"),
        ("month", "Month"),
        ("quarter", "Quarter"),
        ("half_year", "Half-year"),
        ("year", "Year"),
    ]

    # R3 (Phase 13): reports default to This month, Last 3 periods - a fresh, unbound form
    # shows these as selected even before any filter is submitted.
    range = forms.ChoiceField(choices=RANGE_CHOICES, required=False, initial="this_month")
    start = forms.DateField(required=False, widget=DateInput())
    end = forms.DateField(required=False, widget=DateInput())
    interval = forms.ChoiceField(choices=INTERVAL_CHOICES, required=False, initial="3", label="Average interval")
    subtotal = forms.ChoiceField(choices=SUBTOTAL_CHOICES, required=False, label="Subtotal by")
    account = forms.ModelChoiceField(queryset=Account.objects.all(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs["class"] = "form-select form-select-sm"
            else:
                field.widget.attrs["class"] = "form-control form-control-sm"
        _use_regional_dates(self)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("range") == "custom":
            start, end = cleaned.get("start"), cleaned.get("end")
            if not (start and end):
                raise ValidationError("A custom range needs both a start and an end date.")
            if start > end:
                raise ValidationError("The start date must be on or before the end date.")
        return cleaned


class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    """The Settings page: regional preferences + main currency (D2, D10)."""

    class Meta:
        model = Profile
        fields = ["main_currency", "time_zone", "date_format", "number_format", "first_day_of_week"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["time_zone"].widget = forms.Select(choices=[(z, z) for z in regional.time_zone_choices()])
        self.fields["time_zone"].widget.attrs["class"] = "form-select"


class CheckRatesForm(forms.Form):
    """No fields — just the button that triggers a Frankfurter fetch; needs an internet connection."""


class WatchedExpensesForm(BootstrapFormMixin, forms.ModelForm):
    """Settings: which expense categories the "Watched expenses" report tracks (R8), replacing
    the old hardcoded "Bills & Utilities" report. Categories are rendered by hand in
    includes/category_checkbox_filter.html (same tick-the-subcategories widget the Transactions
    filter uses), not by this field's own default widget - Django still binds/validates against
    this field's name either way."""

    class Meta:
        model = Profile
        fields = ["watched_expenses_title", "watched_expenses_categories"]
        labels = {"watched_expenses_title": "Title", "watched_expenses_categories": "Categories"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["watched_expenses_categories"].queryset = categories_alphabetical(
            Category.objects.filter(category_type=Category.CategoryType.EXPENSE, account__isnull=True)
        )
        self.fields["watched_expenses_title"].widget.attrs["class"] = "form-control"


def convertible_currencies(accounts):
    """Currencies actually in use, for the settings page's 'known rates' list."""
    return sorted({account.currency for account in accounts})
