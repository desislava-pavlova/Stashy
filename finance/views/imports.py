from django.contrib import messages
from django.shortcuts import redirect, render

from .. import regional
from ..content import IMPORT_TIPS
from ..forms import ImportUploadForm
from ..models import ImportBatch
from ..services import importing
from ._helpers import _plural


def import_transactions(request):
    if request.method == "POST":
        form = ImportUploadForm(request.POST, request.FILES)
        if form.is_valid():
            account = form.cleaned_data["account"]
            date_format = form.cleaned_data.get("date_format") or "auto"
            try:
                result = importing.import_file(form.cleaned_data["file"], account, date_format)
            except importing.ImportFileError as error:
                messages.error(request, str(error))
                return redirect("finance:import_transactions")
            messages.success(request, _import_summary(result))
            return redirect("finance:account_detail", pk=account.pk)
    else:
        form = ImportUploadForm()

    recent_batches = ImportBatch.objects.select_related("account")[:15]
    return render(
        request, "finance/import_form.html", {"form": form, "recent_batches": recent_batches, "tips": IMPORT_TIPS}
    )


def _import_summary(result):
    summary = f"Imported {_plural(result.created, 'transaction')} ({result.duplicates} duplicates skipped)."
    if result.unknown_account:
        summary += f" {_plural(result.unknown_account, 'row')} skipped - named an account that doesn't exist yet."
    if result.unreadable:
        summary += f" {_plural(result.unreadable, 'row')} skipped - unreadable date or amount."
    if result.date_format:
        label = dict(regional.DATE_FORMAT_CHOICES).get(result.date_format, result.date_format)
        summary += f" Read dates as {label}."
    if result.encoding_note:
        summary += f" {result.encoding_note}"
    return summary
