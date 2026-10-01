"""Forms for the multi-stash login screens (Phase 12) - kept separate from finance/forms.py
since these belong to the picker/login layer, not the finance app's own data forms."""
from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from . import stashes


def _pin_widget(autofocus=False):
    return forms.PasswordInput(attrs={
        "inputmode": "numeric", "pattern": "[0-9]*", "maxlength": 4,
        "autocomplete": "off", "class": "form-control form-control-lg pin-input",
        **({"autofocus": True} if autofocus else {}),
    })


class StashCreateForm(forms.Form):
    owner_name = forms.CharField(
        max_length=60, label="Whose stash?",
        widget=forms.TextInput(attrs={"class": "form-control", "autofocus": True, "placeholder": "Alex, Acme Ltd, The Smiths…"}),
        help_text="A person, a business or a family - shown everywhere instead of an internal name.",
    )
    pin = forms.CharField(max_length=4, min_length=4, label="PIN", widget=_pin_widget())
    pin_confirm = forms.CharField(max_length=4, min_length=4, label="Confirm PIN", widget=_pin_widget())

    def clean_owner_name(self):
        owner_name = self.cleaned_data["owner_name"].strip()
        try:
            return stashes.validate_owner_name(owner_name)
        except stashes.StashError as error:
            raise ValidationError(str(error))

    def clean_pin(self):
        pin = self.cleaned_data.get("pin", "")
        if not stashes.PIN_RE.match(pin):
            raise ValidationError("PIN must be exactly 4 digits.")
        return pin

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("pin") and cleaned.get("pin_confirm") and cleaned["pin"] != cleaned["pin_confirm"]:
            self.add_error("pin_confirm", "PINs don't match.")
        return cleaned


class StashLoginForm(AuthenticationForm):
    """The normal AuthenticationForm, restyled: username is fixed to the current stash (set
    hidden by the view, never typed) and password becomes a 4-digit PIN field."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget = forms.HiddenInput()
        self.fields["password"].label = "PIN"
        self.fields["password"].widget = _pin_widget(autofocus=True)

    def clean_password(self):
        pin = self.cleaned_data.get("password", "")
        if not stashes.PIN_RE.match(pin):
            raise ValidationError("PIN must be exactly 4 digits.")
        return pin

    def get_invalid_login_error(self):
        return ValidationError("That PIN isn't right. Try again?", code="invalid_login")


class ForgotPinForm(forms.Form):
    recovery_code = forms.CharField(
        label="Recovery code",
        widget=forms.TextInput(attrs={"class": "form-control form-control-lg", "autofocus": True, "autocomplete": "off"}),
    )
    new_pin = forms.CharField(max_length=4, min_length=4, label="New PIN", widget=_pin_widget())
    new_pin_confirm = forms.CharField(max_length=4, min_length=4, label="Confirm new PIN", widget=_pin_widget())

    def clean_recovery_code(self):
        return self.cleaned_data["recovery_code"].strip().upper()

    def clean_new_pin(self):
        pin = self.cleaned_data.get("new_pin", "")
        if not stashes.PIN_RE.match(pin):
            raise ValidationError("PIN must be exactly 4 digits.")
        return pin

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("new_pin") and cleaned.get("new_pin_confirm") and cleaned["new_pin"] != cleaned["new_pin_confirm"]:
            self.add_error("new_pin_confirm", "PINs don't match.")
        return cleaned


class ChangePinForm(forms.Form):
    """Settings page: change the PIN while already logged in, without needing the recovery
    code - that's reserved for when the current PIN itself is what's been forgotten."""
    current_pin = forms.CharField(max_length=4, min_length=4, label="Current PIN", widget=_pin_widget())
    new_pin = forms.CharField(max_length=4, min_length=4, label="New PIN", widget=_pin_widget())
    new_pin_confirm = forms.CharField(max_length=4, min_length=4, label="Confirm new PIN", widget=_pin_widget())

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_pin(self):
        pin = self.cleaned_data.get("current_pin", "")
        if not self.user or not self.user.check_password(pin):
            raise ValidationError("That's not your current PIN.")
        return pin

    def clean_new_pin(self):
        pin = self.cleaned_data.get("new_pin", "")
        if not stashes.PIN_RE.match(pin):
            raise ValidationError("PIN must be exactly 4 digits.")
        return pin

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("new_pin") and cleaned.get("new_pin_confirm") and cleaned["new_pin"] != cleaned["new_pin_confirm"]:
            self.add_error("new_pin_confirm", "PINs don't match.")
        return cleaned
