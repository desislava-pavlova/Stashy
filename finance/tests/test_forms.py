"""The pieces the form modules share (`forms/_base.py`): the Bootstrap
classes every form gets, and the plain forms that take them from the
mixin like the model forms do."""

from django import forms
from django.test import SimpleTestCase

from ..forms._base import BootstrapFormMixin
from ..forms.accounts import ReconcileStartForm, TransferForm
from ..forms.filters import DateRangeFilterForm
from ..forms.imports import ImportUploadForm
from ..models import Account
from .base import StashTestCase


class SampleForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField()
    kind = forms.ChoiceField(choices=[("a", "A")])
    agreed = forms.BooleanField(required=False)
    own = forms.CharField(widget=forms.TextInput(attrs={"class": "mine"}))


class SmallSampleForm(SampleForm):
    control_size = "sm"


class MoreWidgetsForm(BootstrapFormMixin, forms.Form):
    notes = forms.CharField(widget=forms.Textarea)
    number = forms.IntegerField()
    upload = forms.FileField()
    several = forms.MultipleChoiceField(choices=[("a", "A")])
    own_select = forms.ChoiceField(
        choices=[("a", "A")], widget=forms.Select(attrs={"class": "mine"})
    )
    own_tick = forms.BooleanField(widget=forms.CheckboxInput(attrs={"class": "mine"}))


def classes(form):
    """{field name: the CSS classes of its widget}."""
    return {name: field.widget.attrs.get("class") for name, field in form.fields.items()}


class BootstrapFormMixinTests(SimpleTestCase):
    def test_each_kind_of_field_gets_its_bootstrap_class_and_a_chosen_one_stays(self):
        self.assertEqual(
            classes(SampleForm()),
            {
                "name": "form-control",
                "kind": "form-select",
                "agreed": "form-check-input",
                "own": "mine",
            },
        )

    def test_a_small_form_gets_the_small_sizes_but_a_tick_box_has_none(self):
        self.assertEqual(
            classes(SmallSampleForm()),
            {
                "name": "form-control form-control-sm",
                "kind": "form-select form-select-sm",
                "agreed": "form-check-input",
                "own": "mine",
            },
        )

    def test_text_areas_number_and_file_boxes_are_form_controls_and_multi_selects_selects(self):
        self.assertEqual(
            classes(MoreWidgetsForm()),
            {
                "notes": "form-control",
                "number": "form-control",
                "upload": "form-control",
                "several": "form-select",
                "own_select": "mine",
                "own_tick": "mine",
            },
        )

    def test_a_chosen_class_stays_exactly_as_written_in_a_small_form_too(self):
        class SmallMoreWidgetsForm(MoreWidgetsForm):
            control_size = "sm"

        small = classes(SmallMoreWidgetsForm())
        self.assertEqual((small["own_select"], small["own_tick"]), ("mine", "mine"))
        self.assertEqual(small["several"], "form-select form-select-sm")
        self.assertEqual(small["notes"], "form-control form-control-sm")

    def test_building_a_form_never_changes_the_class_or_a_sibling_form(self):
        """The mixin writes to widgets. It works on the copies each form
        gets, so a small form built first can't leave its small classes
        on the plain form that shares its fields."""
        SmallSampleForm()
        self.assertEqual(classes(SampleForm())["name"], "form-control")
        self.assertNotIn("class", SampleForm.base_fields["name"].widget.attrs)
        self.assertEqual(classes(SmallSampleForm())["name"], "form-control form-control-sm")

    def test_a_form_built_twice_gets_the_same_classes(self):
        self.assertEqual(classes(SampleForm()), classes(SampleForm()))


class PlainFormsUseTheMixinTests(StashTestCase):
    def test_transfer(self):
        form = TransferForm(from_account=Account.objects.create(name="Checking"))
        self.assertEqual(
            classes(form),
            {"to_account": "form-select", "amount": "form-control", "date": "form-control"},
        )

    def test_import_upload(self):
        self.assertEqual(
            classes(ImportUploadForm()),
            {"account": "form-select", "file": "form-control", "date_format": "form-select"},
        )

    def test_date_range_filter_is_small(self):
        self.assertEqual(
            classes(DateRangeFilterForm()),
            {
                "range": "form-select form-select-sm",
                "start": "form-control form-control-sm",
                "end": "form-control form-control-sm",
                "interval": "form-select form-select-sm",
                "subtotal": "form-select form-select-sm",
                "account": "form-select form-select-sm",
            },
        )

    def test_reconcile_start_is_styled_by_hand_so_its_date_box_is_not_a_form_control(self):
        # The mixin would give the date box Bootstrap's form-control
        # class too, which changes how that box looks (see the form).
        self.assertEqual(
            classes(ReconcileStartForm()),
            {"statement_date": None, "statement_balance": "form-control"},
        )


def forms_using_the_mixin(parent=BootstrapFormMixin):
    """Every form of the app's `forms/` package that uses the mixin
    (not the sample forms of this module)."""
    for form_class in parent.__subclasses__():
        if form_class.__module__.startswith("finance.forms."):
            yield form_class
        yield from forms_using_the_mixin(form_class)


class EveryFormUsingTheMixinTests(StashTestCase):
    """The forms in `forms/` that take their Bootstrap classes from the
    mixin: found by looking for its subclasses, so a new form is checked
    without anyone adding it to a list."""

    def build(self, form_class):
        if form_class is TransferForm:
            return form_class(from_account=Account.objects.create(name="Checking"))
        return form_class()

    def test_there_are_forms_to_check(self):
        self.assertGreaterEqual(len(set(forms_using_the_mixin())), 10)

    def test_every_visible_field_has_a_bootstrap_class(self):
        for form_class in set(forms_using_the_mixin()):
            form = self.build(form_class)
            for name, field in form.fields.items():
                if field.widget.is_hidden:
                    continue
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertRegex(
                        field.widget.attrs.get("class", ""),
                        r"\bform-(control|select|check-input)\b",
                    )
