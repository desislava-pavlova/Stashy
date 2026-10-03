import json
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView

from .. import regional
from ..content import REMINDER_TIPS
from ..forms import ReminderForm
from ..models import Reminder


def _reminder_window_cutoff(window, today):
    """Same "yearly = through 31 December" meaning as Reminder.default_generation_horizon.
    None means no cutoff at all (the explicit "All" choice)."""
    if window == "month":
        return date(today.year, today.month, 1) + relativedelta(months=1, days=-1)
    if window in ("15", "30", "90"):
        return today + timedelta(days=int(window))
    if window == "yearly":
        return date(today.year, 12, 31)
    return None


def reminder_list(request):
    today = timezone.localdate()
    # A fresh visit (no "window" param at all) defaults to this calendar month; once anything's
    # been picked - including "All" (window=all) - that stays sticky rather than snapping back,
    # same "unset vs. explicitly chosen" pattern the transaction list uses for its default range.
    window = request.GET.get("window")
    if window is None:
        window = "month"
    # Paid/skipped rows show by default on "This month" (the whole month's picture) and stay
    # hidden by default elsewhere; the form always sends an explicit 0/1 once it's used.
    show_done_param = request.GET.get("show_done")
    show_done = (window == "month") if show_done_param is None else show_done_param == "1"
    reminders = Reminder.objects.select_related("account", "category", "payee")
    cutoff = _reminder_window_cutoff(window, today)
    if cutoff is not None:
        reminders = reminders.filter(next_due_date__lte=cutoff)
    if window == "month":
        reminders = reminders.filter(next_due_date__gte=today.replace(day=1))
    reminders = list(reminders)
    if not show_done:
        reminders = [r for r in reminders if r.status(today) not in ("paid", "skipped")]
    return render(
        request,
        "finance/reminder_list.html",
        {
            "reminders": reminders,
            "window": window,
            "show_done": show_done,
            "today": today,
        },
    )


REMINDER_FORM_EXTRA_CONTEXT = {
    "tips": REMINDER_TIPS,
    "reminder_type_filter": True,
    "unit_words_json": json.dumps(Reminder.FREQUENCY_UNIT_WORDS),
}


class ReminderCreateView(CreateView):
    model = Reminder
    form_class = ReminderForm
    template_name = "finance/generic_form.html"
    success_url = reverse_lazy("finance:reminder_list")
    extra_context = {"title": "Add reminder", **REMINDER_FORM_EXTRA_CONTEXT}


def reminder_edit(request, pk):
    """Edit a reminder - if it's connected to a series (it's a series head with occurrences,
    or it's an occurrence itself), ask whether the edit applies to just this one or the whole
    series before showing the form."""
    reminder = get_object_or_404(Reminder, pk=pk)
    root = reminder.series if reminder.series_id else reminder
    has_connection = bool(reminder.series_id) or root.occurrences.exists()
    scope = request.POST.get("scope") or request.GET.get("scope")
    if has_connection and scope not in ("this", "series"):
        return render(request, "finance/reminder_edit_scope.html", {"reminder": reminder})

    if request.method == "POST":
        form = ReminderForm(request.POST, instance=reminder)
        if form.is_valid():
            saved = form.save()
            if has_connection and scope == "series":
                saved.apply_to_series()
            messages.success(request, "Reminder updated.")
            return redirect("finance:reminder_list")
    else:
        form = ReminderForm(instance=reminder)

    return render(
        request,
        "finance/generic_form.html",
        {
            "form": form,
            "title": "Edit reminder",
            "scope": scope if has_connection else None,
            **REMINDER_FORM_EXTRA_CONTEXT,
        },
    )


class ReminderDeleteView(DeleteView):
    model = Reminder
    template_name = "finance/reminder_confirm_delete.html"
    success_url = reverse_lazy("finance:reminder_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Only the ones that would actually be deleted - a paid/skipped occurrence is detached
        # and kept, not removed (Reminder.delete()).
        ctx["occurrence_count"] = self.object.occurrences.filter(
            last_entered_transaction__isnull=True, skipped=False
        ).count()
        return ctx


def reminder_skip(request, pk):
    """Move a reminder past its current occurrence without creating a transaction (D6)."""
    if request.method != "POST":
        return redirect("finance:reminder_list")
    reminder = get_object_or_404(Reminder, pk=pk)
    label = str(reminder)
    reminder.skip()
    if reminder.frequency == Reminder.Frequency.ONE_TIME:
        messages.success(request, f"Skipped {label}.")
    else:
        messages.success(request, f"Skipped {label}; moved it to {regional.format_date(reminder.next_due_date)}.")
    return redirect("finance:reminder_list")
