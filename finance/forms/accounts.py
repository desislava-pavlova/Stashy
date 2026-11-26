"""The account forms: add and edit an account, Transfer, and the two
reconcile steps. A rule that needs more than the form's own fields (the
opening balance, a transfer's two legs) is in services/accounts.py, so
a form only collects and checks what was typed.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .. import regional
from ..models import Account, Transaction
from ..money import CENT, ZERO
from ..services.accounts import open_account, transfer
from ._base import (
    BootstrapFormMixin,
    DateInput,
    PlausibleDateField,
    SafeModelMultipleChoiceField,
    use_regional_dates,
)


class AccountForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Account
        fields = ["name", "account_type", "currency", "institution", "is_active", "notes"]
        # Django's default Textarea is 10 rows - taller than this form
        # needs, and (with the opening-balance fields on top, for a new
        # account) enough to push Save off-screen.
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # Currency conversion only ever uses the latest known rate,
            # not per-transaction history, so changing it after
            # transactions exist would misstate every past one.
            self.fields["currency"].disabled = True

    def clean_is_active(self):
        is_active = self.cleaned_data["is_active"]
        # Only ever checks an *existing* account's real, already-saved
        # balance - a brand new account (no pk yet) has no transactions
        # to have a balance from regardless of what's typed into this
        # same submission (AccountCreateForm.clean() below covers that
        # case).
        if not is_active and self.instance.pk and self.instance.balance != ZERO:
            raise ValidationError(
                "This account still has a balance - it can't be made inactive until its "
                "balance is zero."
            )
        return is_active


class AccountCreateForm(AccountForm):
    """Account creation also asks for an opening balance, which becomes
    a real transaction (D1) when the account is saved. A blank amount
    or date means zero, dated today (clean() fills them in), and
    services/accounts.py does the saving. With commit=False nothing
    is saved and no opening balance is made."""

    opening_balance = forms.DecimalField(
        max_digits=12, decimal_places=2, required=False, initial=0
    )
    opening_balance_date = PlausibleDateField(
        required=False, widget=DateInput(), initial=timezone.localdate
    )

    field_order = [
        "name",
        "account_type",
        "currency",
        "institution",
        "opening_balance",
        "opening_balance_date",
        "is_active",
        "notes",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"].disabled = False
        self.fields["currency"].initial = regional.current().main_currency

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("opening_balance") is None:
            cleaned["opening_balance"] = ZERO
        if not cleaned.get("opening_balance_date"):
            cleaned["opening_balance_date"] = timezone.localdate()
        if not cleaned.get("is_active", True) and cleaned.get("opening_balance"):
            raise ValidationError("A new account can't be inactive if it has an opening balance.")
        return cleaned

    def save(self, commit=True):
        account = super().save(commit=False)
        if not commit:
            return account
        return open_account(
            account,
            self.cleaned_data["opening_balance"],
            self.cleaned_data["opening_balance_date"],
        )


class TransferForm(BootstrapFormMixin, forms.Form):
    """Backs the account page's Transfer button (D7): creates the paired
    transactions."""

    to_account = forms.ModelChoiceField(queryset=Account.objects.none(), label="Transfer to")
    amount = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=CENT, help_text="Enter a positive amount."
    )
    date = PlausibleDateField(widget=DateInput(), initial=timezone.localdate)

    def __init__(self, *args, from_account, **kwargs):
        super().__init__(*args, **kwargs)
        self.from_account = from_account
        # transfer() converts nothing, so only offer accounts in this
        # account's currency.
        self.fields["to_account"].queryset = Account.objects.filter(
            is_active=True, currency=from_account.currency
        ).exclude(pk=from_account.pk)
        if not self.fields["to_account"].queryset.exists():
            self.fields[
                "to_account"
            ].help_text = f"No other active {from_account.currency} account to transfer to yet."

    def save(self):
        return transfer(
            self.from_account,
            self.cleaned_data["to_account"],
            self.cleaned_data["amount"],
            self.cleaned_data["date"],
        )


class ReconcileStartForm(forms.Form):
    """Step 1 of the account page's Reconcile button: what your
    statement says."""

    statement_date = PlausibleDateField(widget=DateInput(), initial=timezone.localdate)
    statement_balance = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="The ending balance shown on your bank or card statement.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Styled by hand, not with BootstrapFormMixin: the mixin would
        # give the date box Bootstrap's form-control class too, which
        # changes how that box looks.
        self.fields["statement_balance"].widget.attrs.setdefault("class", "form-control")
        use_regional_dates(self)


class ReconcileFinishForm(forms.Form):
    """Step 2's "Finish reconciling": the statement figures carried over
    from step 1 in hidden fields, plus the ticked transactions.
    Validated like any other form, so a tampered or half-sent submission
    gets a message instead of a server error."""

    statement_date = PlausibleDateField(input_formats=["%Y-%m-%d"])
    statement_balance = forms.DecimalField(max_digits=12, decimal_places=2)
    transaction = SafeModelMultipleChoiceField(queryset=Transaction.objects.none(), required=False)
    accept_difference = forms.BooleanField(required=False)

    def __init__(self, *args, account, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["transaction"].queryset = account.transactions.filter(reconciled=False)
