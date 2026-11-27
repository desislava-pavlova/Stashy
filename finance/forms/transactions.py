"""The transaction form, used to add, edit and enter a reminder as a
transaction. The payee is typed as a name and resolved when the form is
saved, never before, so a form with errors creates no payee.
"""

from django import forms
from django.db.models import Q

from ..models import Payee, Transaction, categories_alphabetical
from ._base import BootstrapFormMixin, DateInput


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
            "account",
            "date",
            "transaction_number",
            "payee_name",
            "category",
            "memo",
            "amount",
            "reconciled",
        ]
        widgets = {
            "date": DateInput(),
        }
        labels = {"transaction_number": "Number"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Transfer categories are created automatically by the Transfer
        # button, not picked by hand here — except editing a transaction
        # that already has one (an opening balance or transfer leg) must
        # still offer its own category, or saving the form would reject
        # it as invalid.
        self.fields["category"].queryset = categories_alphabetical(
            self.fields["category"].queryset.filter(
                Q(account__isnull=True) | Q(pk=self.instance.category_id)
            )
        )
        self.fields["category"].label_from_instance = lambda category: category.indented_name

    def clean_payee_name(self):
        return (self.cleaned_data.get("payee_name") or "").strip()

    def save(self, commit=True):
        # Payees are resolved here, not in clean(), so a form with
        # errors never creates one.
        instance = super().save(commit=False)
        name = self.cleaned_data["payee_name"]
        # Names that differ only in case are one payee, and the spelling
        # already saved wins.
        instance.payee = Payee.objects.resolve(name) if name else None

        if instance.transaction_number is None:
            # Transaction.save() only auto-assigns on create; clearing
            # the field on an edit (e.g. after moving a transaction to a
            # different account) should renumber it too.
            instance.transaction_number = Transaction.next_number(instance.date)

        if commit:
            instance.save()
            self.save_m2m()
        return instance
