"""The reminder form, and the rules for what a one-time reminder and an
auto-generated occurrence may hold. The dates a recurring reminder makes
are the model's work (`Reminder.save()`); the form only cleans what was
typed.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from ..models import Category, Reminder, categories_alphabetical
from ._base import BootstrapFormMixin, DateInput
from .categories import CategoryParentSelect


class ReminderForm(BootstrapFormMixin, forms.ModelForm):
    # Not a model field - a friendlier affordance than "just leave the
    # date blank" for a reminder with no end date.
    no_end_date = forms.BooleanField(required=False, label="No end date")

    class Meta:
        model = Reminder
        fields = [
            "reminder_type",
            "account",
            "payee",
            "category",
            "amount",
            "frequency",
            "interval",
            "next_due_date",
            "end_of_period",
            "end_date",
            "no_end_date",
            "notes",
        ]
        widgets = {
            "next_due_date": DateInput(),
            "end_date": DateInput(),
        }
        labels = {
            "interval": "Every",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount"].required = False
        self.fields["interval"].required = False
        category_field = self.fields["category"]
        categories = categories_alphabetical(
            Category.objects.filter(Q(account__isnull=True) | Q(pk=self.instance.category_id))
        )
        # Picking Income/Expense narrows the Category list to that type
        # only (same type-tagged-option pattern CategoryForm already
        # uses for its own Parent field).
        category_field.widget = CategoryParentSelect(
            attrs=category_field.widget.attrs, categories_by_id={c.pk: c for c in categories}
        )
        category_field.queryset = categories
        category_field.label_from_instance = lambda category: category.indented_name
        self.fields["no_end_date"].initial = (
            not self.instance.end_date if self.instance.pk else True
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("no_end_date"):
            cleaned["end_date"] = None
        if cleaned.get("frequency") == Reminder.Frequency.ONE_TIME:
            # The page hides these two for a one-time reminder, and the
            # model says they mean nothing there: a value typed before
            # switching to One-time must not be an error in a row nobody
            # can see.
            cleaned["interval"] = 1
            cleaned["end_date"] = None
        if not cleaned.get("interval"):
            cleaned["interval"] = 1
        # An auto-generated occurrence (series is set) can't become its
        # own new recurring series head - it exists to represent one
        # specific future date.
        if self.instance.series_id and cleaned.get("frequency") != Reminder.Frequency.ONE_TIME:
            raise ValidationError(
                "This reminder was auto-generated from a recurring one, so it can't have its "
                "own frequency - edit the original recurring reminder instead."
            )
        return cleaned
