"""The import page's upload form: which account, which file and how
its dates are written. Reading the file is services/importing.py's job,
so this form only checks what was picked.
"""

from django import forms
from django.core.validators import FileExtensionValidator

from .. import regional
from ..models import Account
from ..services.importing import SUPPORTED_EXTENSIONS, UNSUPPORTED_FILE_MESSAGE
from ._base import BootstrapFormMixin


class ImportUploadForm(BootstrapFormMixin, forms.Form):
    DATE_FORMAT_CHOICES = [
        ("auto", "Detect automatically (recommended)"),
        *regional.DATE_FORMAT_CHOICES,
    ]

    account = forms.ModelChoiceField(
        queryset=Account.objects.filter(is_active=True),
        help_text=(
            "Used for every row, unless the file has its own Account column - "
            "that takes over per row."
        ),
    )
    file = forms.FileField(
        help_text=(
            "CSV or XLSX (Date, Payee, Memo, Amount, optionally "
            "Num/Account/Category/Reconciled) or OFX/QFX"
        ),
        validators=[
            FileExtensionValidator(SUPPORTED_EXTENSIONS, message=UNSUPPORTED_FILE_MESSAGE)
        ],
    )
    date_format = forms.ChoiceField(
        choices=DATE_FORMAT_CHOICES,
        required=False,
        initial="auto",
        help_text="Stashy checks the file's own dates first - override only if it guesses wrong.",
    )
