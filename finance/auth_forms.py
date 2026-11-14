"""Forms for the multi-stash login screens - kept separate from finance/forms.py since these
belong to the picker/login layer, not the finance app's own data forms."""

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from .stashes import StashError, registry, security


def _pin_widget(autofocus=False):
    return forms.PasswordInput(
        attrs={
            "inputmode": "numeric",
            "pattern": "[0-9]*",
            "minlength": 4,
            "maxlength": 4,
            "autocomplete": "off",
            "class": "form-control form-control-lg pin-input",
            **({"autofocus": True} if autofocus else {}),
        }
    )


class PinField(forms.CharField):
    """A 4-digit PIN box. Anything else gets the one message "PIN must be exactly 4 digits.",
    from the same rule stashes.security.validate_pin() uses."""

    default_validators = [security.pin_validator]

    def __init__(self, *, autofocus=False, **kwargs):
        kwargs.setdefault("widget", _pin_widget(autofocus=autofocus))
        super().__init__(**kwargs)


class PinConfirmMixin:
    """The second PIN box must repeat the first. `pin_fields` names the pair."""

    pin_fields = ("new_pin", "new_pin_confirm")

    def clean(self):
        cleaned = super().clean()
        pin_name, confirm_name = self.pin_fields
        pin, confirm = cleaned.get(pin_name), cleaned.get(confirm_name)
        if pin and confirm and pin != confirm:
            self.add_error(confirm_name, "PINs don't match.")
        return cleaned


class StashCreateForm(PinConfirmMixin, forms.Form):
    pin_fields = ("pin", "pin_confirm")

    owner_name = forms.CharField(
        max_length=60,
        label="Whose stash?",
        widget=forms.TextInput(
            attrs={"class": "form-control", "autofocus": True, "placeholder": "Alex, Biz Ltd, The Smiths…"}
        ),
        help_text="A person, a business or a family - shown everywhere instead of an internal name.",
    )
    pin = PinField(label="PIN")
    pin_confirm = PinField(label="Confirm PIN")

    def clean_owner_name(self):
        owner_name = self.cleaned_data["owner_name"].strip()
        try:
            return registry.validate_owner_name(owner_name)
        except StashError as error:
            raise ValidationError(str(error)) from error


class StashLoginForm(AuthenticationForm):
    """The normal AuthenticationForm, restyled: username is fixed to the current stash (set
    hidden by the view, never typed) and password becomes a 4-digit PIN field."""

    password = PinField(label="PIN", strip=False, autofocus=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget = forms.HiddenInput()

    def get_invalid_login_error(self):
        return ValidationError("That PIN isn't right. Try again?", code="invalid_login")


class ForgotPinForm(PinConfirmMixin, forms.Form):
    recovery_code = forms.CharField(
        label="Recovery code",
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-lg", "autofocus": True, "autocomplete": "off"}
        ),
    )
    new_pin = PinField(label="New PIN")
    new_pin_confirm = PinField(label="Confirm new PIN")

    def clean_recovery_code(self):
        return self.cleaned_data["recovery_code"].strip().upper()


class CurrentPinForm(forms.Form):
    """The PIN asked again before a change only the stash's owner should make (a new
    recovery code, and Change PIN below). A wrong PIN counts toward the same lockout as the
    PIN screen (security.check_pin), so these boxes can't be used to guess it."""

    current_pin = PinField(label="Current PIN")

    def __init__(self, *args, profile, **kwargs):
        self.profile = profile
        super().__init__(*args, **kwargs)

    def clean_current_pin(self):
        pin = self.cleaned_data["current_pin"]
        if not security.check_pin(self.profile, pin):
            raise ValidationError("That's not your current PIN.")
        return pin


class ChangePinForm(PinConfirmMixin, CurrentPinForm):
    """Settings page: change the PIN while already logged in, without needing the recovery
    code - that's reserved for when the current PIN itself is what's been forgotten."""

    new_pin = PinField(label="New PIN")
    new_pin_confirm = PinField(label="Confirm new PIN")
