"""The payee form: a payee's name, and whether it is still in use. The
rule about duplicate names is on `Payee.clean()`, not here.
"""

from django import forms

from ..models import Payee
from ._base import BootstrapFormMixin


class PayeeForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Payee
        fields = ["name", "is_active"]
