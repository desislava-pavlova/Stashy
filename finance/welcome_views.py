"""The post-login welcome wizard (Phase 15 / L4): four steps run once, right after a brand
new stash's first login - money & region, starter categories, watched expenses, first
account. Each step saves on Next; Profile.welcome_step tracks how far along, so closing
Stashy mid-setup resumes at the right step next time (RequireSetupMiddleware enforces this
everywhere else in the app). Kept out of finance/views.py, same reasoning as auth_views.py:
this is its own layer, not an ordinary in-app page.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from . import starter_categories
from .forms import AccountCreateForm, ProfileForm, WatchedExpensesForm
from .models import Category, Profile, grouped_categories

STEP_COUNT = Profile.WELCOME_STEPS_DONE


def _watched_expenses_context(profile):
    selected_values = {str(pk) for pk in profile.watched_expenses_categories.values_list("pk", flat=True)}
    count = len(selected_values)
    if count == 0:
        button_label = "No categories chosen"
    elif count == 1:
        button_label = next(iter(profile.watched_expenses_categories.all())).full_name
    else:
        button_label = f"{count} categories selected"
    return {
        "watched_category_groups": grouped_categories(
            Category.objects.filter(category_type=Category.CategoryType.EXPENSE, account__isnull=True)
        ),
        "watched_selected_values": selected_values,
        "watched_button_label": button_label,
    }


def _advance(profile, completed_step):
    if profile.welcome_step < completed_step:
        profile.welcome_step = completed_step
        profile.save(update_fields=["welcome_step"])


def _to_step(step):
    return redirect("finance:welcome_step", step=step)


@login_required
def welcome_step(request, step):
    profile = Profile.for_user(request.user)
    if profile.setup_finished:
        return redirect("finance:dashboard")
    # Only an already-completed step, or the very next one, can be viewed - typing a later
    # step's URL by hand just bounces back to wherever setup actually is.
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
    return render(
        request,
        "finance/welcome_money_region.html",
        {
            "form": form,
            "step_number": step,
            "step_total": STEP_COUNT,
        },
    )


def _step_categories(request, profile, step):
    available = starter_categories.available_sheets()
    if request.method == "POST":
        sheet = request.POST.get("sheet")
        # available is empty only when starter_categories.xlsx itself is missing - "required"
        # then means nothing, so an explicit "skip" is the only way forward.
        if sheet in available or (not available and "skip" in request.POST):
            if sheet in available:
                created = starter_categories.add_starter_categories(sheet)
                if created:
                    messages.success(
                        request, f"Added {len(created)} categor{'y' if len(created) == 1 else 'ies'} from {sheet}."
                    )
            _advance(profile, step)
            return _to_step(step + 1)
    return render(
        request,
        "finance/welcome_categories.html",
        {
            "available": available,
            "step_number": step,
            "step_total": STEP_COUNT,
        },
    )


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
        # The model's "Bills & Utilities" default shouldn't be pre-typed for someone who hasn't
        # picked a title yet; coming Back to an already-completed step keeps what they saved.
        initial = {"watched_expenses_title": ""} if profile.welcome_step < step else {}
        form = WatchedExpensesForm(instance=profile, initial=initial)
    return render(
        request,
        "finance/welcome_watched_expenses.html",
        {
            "form": form,
            "step_number": step,
            "step_total": STEP_COUNT,
            **_watched_expenses_context(profile),
        },
    )


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
    return render(
        request,
        "finance/welcome_first_account.html",
        {
            "form": form,
            "step_number": step,
            "step_total": STEP_COUNT,
        },
    )


_STEP_HANDLERS = {
    1: _step_money_region,
    2: _step_categories,
    3: _step_watched_expenses,
    4: _step_first_account,
}
