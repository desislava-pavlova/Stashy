"""What the form modules share: the regional date widget and field,
`BootstrapFormMixin`, and a multiple-choice field that copes with an id
too big for the database.

It holds no form of its own and imports no model, so every form module
can import it.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.db import connections

from .. import regional
from ..limits import validate_plausible_date


class DateInput(forms.DateInput):
    """A text input in the active regional date format;
    `stashyDatepicker()` (stashy.js) puts a date picker on it that
    writes the same format."""

    def __init__(self, attrs=None):
        prefs = regional.current()
        defaults = {
            "data-datepicker": "",
            "placeholder": prefs.date_placeholder,
            "autocomplete": "off",
        }
        super().__init__(attrs={**defaults, **(attrs or {})}, format=prefs.date_pattern)


class PlausibleDateField(forms.DateField):
    """A date box that holds the stored dates' limits (see limits.py).
    Use it for every date a person types that is stored; a form built
    from a model gets the same limits from the column."""

    default_validators = [validate_plausible_date]


def use_regional_dates(form):
    """Point every DateField at the active regional format, for parsing
    and for its widget."""
    prefs = regional.current()
    for field in form.fields.values():
        if isinstance(field, forms.DateField):
            field.input_formats = prefs.date_input_formats
            if isinstance(field.widget, DateInput):
                field.widget.attrs["placeholder"] = prefs.date_placeholder
                field.widget.format = prefs.date_pattern


class BootstrapFormMixin:
    """Adds Bootstrap 5 form-control classes to every field
    automatically, and regional date formats. A form for a filter bar
    sets `control_size = "sm"` to get the small controls."""

    control_size = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs.setdefault("class", self._control_class("form-select"))
            else:
                widget.attrs.setdefault("class", self._control_class("form-control"))
        use_regional_dates(self)

    def _control_class(self, base):
        return f"{base} {base}-{self.control_size}" if self.control_size else base


class SafeModelMultipleChoiceField(forms.ModelMultipleChoiceField):
    """A ModelMultipleChoiceField that answers an id too big for the
    database like any other id that isn't a choice. Django looks the ids
    up with `pk__in=[...]`, and SQLite refuses a number over 2**63 - 1
    with an OverflowError: a hand-made request got a 500 page. So an id
    outside what the database can hold is refused first, before any
    query. (A single-choice field already copes, because Django's `pk=`
    lookup returns nothing for it, and ids read from a URL go through
    `as_id()` in ids.py, which guards the same limit.)

    This overrides `_check_values()`, a private Django method, because
    it is the one place the ids are checked; the tests for both forms
    using it would show a Django upgrade renaming it."""

    def _check_values(self, value):
        # The pk is a BigAutoField on every model here, and this is the
        # range that field can hold.
        lowest, highest = connections[self.queryset.db].ops.integer_field_range("BigIntegerField")
        for pk in value:
            try:
                number = int(pk)
            except (TypeError, ValueError):
                continue  # not a number at all: Django's own check below says so, in its words
            if not lowest <= number <= highest:
                raise ValidationError(
                    self.error_messages["invalid_choice"],
                    code="invalid_choice",
                    params={"value": pk},
                )
        return super()._check_values(value)
