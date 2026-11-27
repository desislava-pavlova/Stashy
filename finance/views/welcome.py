"""The post-login welcome wizard: four steps run once, right after a
brand new stash's first login - money & region, starter categories,
watched expenses, first account. Each step saves on Next;
Profile.welcome_step tracks how far along, so closing Stashy mid-setup
resumes at the right step next time (RequireSetupMiddleware enforces
this everywhere else in the app).
"""

from django.contrib import messages
from django.shortcuts import redirect, render

from .. import starter_categories
from ..forms.accounts import AccountCreateForm
from ..forms.settings import ProfileForm, WatchedExpensesForm
from ..models import Profile
from ._helpers import plural, watched_expenses_picker_context


STEP_COUNT = Profile.WELCOME_STEPS_DONE


def _advance(profile, completed_step):
    if profile.welcome_step < completed_step:
        profile.welcome_step = completed_step
        profile.save(update_fields=["welcome_step"])


def _to_step(step):
    return redirect("finance:welcome_step", step=step)


def _step_context(step, **context):
    """What every wizard page needs to draw its progress, plus the
    step's own values."""
    return {"step_number": step, "step_total": STEP_COUNT, **context}


def welcome_step(request, step):
    profile = request.stashy_profile
    if profile.setup_finished:
        return redirect("finance:dashboard")
    # Only an already-completed step, or the very next one, can be
    # viewed - typing a later step's URL by hand just bounces back to
    # wherever setup actually is.
    if step < 1 or step > STEP_COUNT or step > profile.welcome_step + 1:
        return _to_step(min(max(step, 1), profile.welcome_step + 1))

    handler = _STEP_HANDLERS[step]
    return handler(request, profile, step)


def _step_money_region(request, profile, step):
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            _advance(profile, step)
            return _to_step(step + 1)
    else:
        form = ProfileForm(instance=profile)

    context = _step_context(step, form=form)
    return render(request, "finance/welcome_money_region.html", context)


def _step_categories(request, profile, step):
    available = starter_categories.available_sheets()

    if request.method == "POST":
        sheet = request.POST.get("sheet")
        # available is empty only when starter_categories.xlsx itself is
        # missing - "required" then means nothing, so an explicit "skip"
        # is the only way forward.
        if sheet in available or (not available and "skip" in request.POST):
            if sheet in available:
                created = starter_categories.add_starter_categories(sheet)
                if created:
                    added = plural(len(created), "category", "categories")
                    messages.success(request, f"Added {added} from {sheet}.")

            _advance(profile, step)
            return _to_step(step + 1)

    context = _step_context(step, available=available)
    return render(request, "finance/welcome_categories.html", context)


def _step_watched_expenses(request, profile, step):
    if request.method == "POST":
        if "skip" in request.POST:
            _advance(profile, step)
            return _to_step(step + 1)
        form = WatchedExpensesForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            _advance(profile, step)
            return _to_step(step + 1)
    else:
        form = WatchedExpensesForm(instance=profile)

    context = _step_context(step, form=form, **watched_expenses_picker_context(profile))
    return render(request, "finance/welcome_watched_expenses.html", context)


def _step_first_account(request, profile, step):
    if request.method == "POST":
        if "skip" in request.POST:
            _advance(profile, step)
            return redirect("finance:dashboard")
        form = AccountCreateForm(request.POST)
        if form.is_valid():
            form.save()
            _advance(profile, step)
            return redirect("finance:dashboard")
    else:
        form = AccountCreateForm()

    context = _step_context(step, form=form)
    return render(request, "finance/welcome_first_account.html", context)


_STEP_HANDLERS = {
    1: _step_money_region,
    2: _step_categories,
    3: _step_watched_expenses,
    4: _step_first_account,
}
