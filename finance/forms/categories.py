"""The category form, and the dropdown that tags each category with
its type, so a page's script can narrow the list to one type (the
reminder form uses it too).

The one-level rule and the duplicate-name checks are on
`Category.clean()`, which a ModelForm runs, so the admin has them too.
"""

from django import forms
from django.core.exceptions import ValidationError

from ..models import Category
from ._base import BootstrapFormMixin


class CategoryParentSelect(forms.Select):
    """Tags each <option> with its category's own type, so JS can narrow
    the Parent list to only categories matching whichever type is
    currently chosen above."""

    def __init__(self, *args, categories_by_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.categories_by_id = categories_by_id or {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
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
        # Transfer is a system-managed type (D7/D4) — never chosen by
        # hand.
        self.fields["category_type"].choices = [
            choice
            for choice in self.fields["category_type"].choices
            if choice[0] != Category.CategoryType.TRANSFER
        ]
        parent_field = self.fields["parent"]
        # Only a top-level category can be a parent (one level of
        # subcategories, no tree) — this alone also rules out a category
        # ever being its own ancestor.
        parents = Category.objects.main_categories()
        # Build the type-aware widget before assigning the queryset, so
        # the queryset setter's own "widget.choices = ..." (Django's
        # ModelChoiceField internals) lands on this widget.
        parent_field.widget = CategoryParentSelect(
            attrs=parent_field.widget.attrs, categories_by_id={c.pk: c for c in parents}
        )
        parent_field.queryset = parents

    # The one-level rule and duplicate names are checked by
    # Category.clean(), which a ModelForm runs for you - so the admin
    # and this form give the same messages.

    def clean(self):
        cleaned = super().clean()
        if self.instance.is_system_managed:
            raise ValidationError(
                "This category belongs to an account and is managed automatically."
            )
        return cleaned
