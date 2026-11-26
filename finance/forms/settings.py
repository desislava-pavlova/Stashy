"""The Settings page's forms: regional preferences, and the Watched
expenses report. They save only their own columns of the stash's one
Profile, never the whole row.
"""

from django import forms

from .. import regional
from ..models import Category, Profile, categories_alphabetical
from ._base import BootstrapFormMixin, SafeModelMultipleChoiceField


class ProfileColumnsOnlyMixin:
    """For forms that edit the stash's one Profile: save() writes only
    this form's own columns. The Profile comes from
    request.stashy_profile, read before the request took the stash's
    write lock, so saving the whole row could put back old lockout or
    recovery-code values another request had just changed."""

    def save(self, commit=True):
        profile = super().save(commit=False)
        if commit:
            # The form's own fields, not Meta.fields, which is None for
            # a form written with exclude.
            columns = [
                name for name in self.fields if not profile._meta.get_field(name).many_to_many
            ]
            profile.save(update_fields=columns)
            self.save_m2m()
        return profile


class ProfileForm(ProfileColumnsOnlyMixin, BootstrapFormMixin, forms.ModelForm):
    """The Settings page: regional preferences + main currency (D2,
    D10)."""

    # The model stores the zone as plain text. A ChoiceField refuses any
    # zone that isn't in the list, which a dropdown on a text field
    # wouldn't: a hand-made request could still save any text there.
    time_zone = forms.ChoiceField()

    class Meta:
        model = Profile
        fields = [
            "main_currency",
            "time_zone",
            "date_format",
            "number_format",
            "first_day_of_week",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The stash's own zone stays a choice even when the list doesn't
        # have it, so the dropdown shows it and a save keeps it, instead
        # of quietly taking the first zone.
        zones = regional.time_zone_choices(keep=self.instance.time_zone)
        self.fields["time_zone"].choices = [(zone, zone) for zone in zones]


class WatchedExpensesForm(ProfileColumnsOnlyMixin, BootstrapFormMixin, forms.ModelForm):
    """Settings: which expense categories the "Watched expenses" report
    tracks. Categories are rendered by hand in
    includes/category_checkbox_filter.html (same tick-the-subcategories
    widget the Transactions filter uses), not by this field's own
    default widget - Django still binds/validates against this field's
    name either way."""

    class Meta:
        model = Profile
        fields = ["watched_expenses_title", "watched_expenses_categories"]
        labels = {"watched_expenses_title": "Title", "watched_expenses_categories": "Categories"}
        field_classes = {"watched_expenses_categories": SafeModelMultipleChoiceField}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["watched_expenses_categories"].queryset = categories_alphabetical(
            Category.objects.filter(
                category_type=Category.CategoryType.EXPENSE, account__isnull=True
            )
        )
