from django import forms

from .models import Account, Category, Payee, Reminder, Transaction


class BootstrapFormMixin:
    """Adds Bootstrap 5 form-control classes to every field automatically."""

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


class AccountForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Account
        fields = ["name", "account_type", "institution", "starting_balance", "is_active", "notes"]


class CategoryForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "parent", "category_type", "is_active"]


class PayeeForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Payee
        fields = ["name", "default_category", "default_memo", "default_amount", "is_active"]


class TransactionForm(BootstrapFormMixin, forms.ModelForm):
    payee_name = forms.CharField(
        required=False,
        label="Payee",
        widget=forms.TextInput(attrs={"list": "payee-list", "autocomplete": "off"}),
        help_text="Start typing — matching memorized payees will autofill category/memo/amount.",
    )

    class Meta:
        model = Transaction
        fields = [
            "account", "date", "payee_name", "category", "memo", "amount",
            "cleared", "reconciled", "transfer_account", "tags",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "tags": forms.SelectMultiple,
        }

    def clean(self):
        cleaned = super().clean()
        name = (cleaned.get("payee_name") or "").strip()
        if name:
            payee, _ = Payee.objects.get_or_create(name=name)
            cleaned["_resolved_payee"] = payee
        else:
            cleaned["_resolved_payee"] = None
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.payee = self.cleaned_data.get("_resolved_payee")
        instance.payee_name = ""  # canonical name now lives on the Payee record
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class ReminderForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Reminder
        fields = [
            "name", "reminder_type", "account", "payee", "category", "amount",
            "frequency", "next_due_date", "end_date", "auto_enter_days_before", "is_active", "notes",
        ]
        widgets = {
            "next_due_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }


class ImportUploadForm(forms.Form):
    account = forms.ModelChoiceField(queryset=Account.objects.filter(is_active=True))
    file = forms.FileField(help_text="CSV (Date, Payee, Memo, Amount) or OFX/QFX")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].widget.attrs["class"] = "form-select"
        self.fields["file"].widget.attrs["class"] = "form-control"


class DateRangeFilterForm(forms.Form):
    RANGE_CHOICES = [
        ("this_month", "This month"),
        ("last_month", "Last month"),
        ("this_quarter", "This quarter"),
        ("ytd", "Year to date"),
        ("last_12_months", "Last 12 months"),
        ("this_year", "This year"),
        ("last_year", "Last year"),
        ("custom", "Custom range"),
    ]
    INTERVAL_CHOICES = [
        ("day", "Daily"),
        ("week", "Weekly"),
        ("month", "Monthly"),
        ("quarter", "Quarterly"),
        ("year", "Yearly"),
    ]

    range = forms.ChoiceField(choices=RANGE_CHOICES, required=False, initial="last_12_months")
    start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    interval = forms.ChoiceField(choices=INTERVAL_CHOICES, required=False, initial="month")
    account = forms.ModelChoiceField(queryset=Account.objects.all(), required=False)
    category = forms.ModelChoiceField(queryset=Category.objects.all(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs["class"] = "form-select form-select-sm"
            else:
                field.widget.attrs["class"] = "form-control form-control-sm"
