"""The Settings page and everything reached from it.

The page holds several separate forms. Each one posts to its own URL (preferences to the page
itself, the others to check_rates, create_backup and save_watched_expenses), and each
redirects back to the page when it succeeds.
"""

import secrets
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.core.cache import cache
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .. import backup as finance_backup
from ..auth_forms import ChangePinForm
from ..currency import RateFetchError, RateTable, fetch_latest_rates
from ..forms import (
    CheckRatesForm,
    ProfileForm,
    WatchedExpensesForm,
    convertible_currencies,
    watched_expenses_picker_context,
)
from ..models import Account, Profile
from ..stashes import registry, security


def _stash_backup_root(request):
    """Each stash keeps its own backups/ folder next to its own db.sqlite3 - reachable only
    once a stash is selected for this session (finance.middleware.RequireStashMiddleware
    blocks every finance URL, this one included, until then). Always BASE_DIR under
    `manage.py test`: TESTING also bypasses that middleware gate (see finance/middleware.py),
    and whatever a real session happens to have selected on the machine running the suite must
    never change what a test expects - this project's fixtures all assume BASE_DIR."""
    stash_name = request.session.get("stash") if not settings.TESTING else None
    if stash_name:
        return registry.stash_path(stash_name)
    return Path(settings.BASE_DIR)


def _current_stash_db_path(request):
    """The actual db.sqlite3 file behind this session's selected stash - not
    settings.DATABASES["default"]["NAME"], which is always the inert picker placeholder,
    never a real stash's own file."""
    stash_name = request.session.get("stash") if not settings.TESTING else None
    if stash_name:
        return registry.stash_db_path(stash_name)
    return settings.DATABASES["default"]["NAME"]


def _rate_currencies(profile):
    return convertible_currencies(Account.objects.all()) or [profile.main_currency]


def _render_settings(request, profile, form=None, watched_form=None):
    """The Settings page. A form that failed validation is passed in, so its errors show;
    every other form starts fresh."""
    rates = RateTable(profile.main_currency)
    known_rates = [(code, rates.known(code)) for code in _rate_currencies(profile) if code != profile.main_currency]
    recent_backups = finance_backup.list_backups(_stash_backup_root(request), limit=5)
    return render(
        request,
        "finance/settings.html",
        {
            "form": form or ProfileForm(instance=profile),
            "watched_form": watched_form or WatchedExpensesForm(instance=profile),
            **watched_expenses_picker_context(profile),
            "check_rates_form": CheckRatesForm(),
            "profile": profile,
            "known_rates": known_rates,
            "recent_backups": [{"name": p.name, "size_kb": p.stat().st_size // 1024} for p in recent_backups],
        },
    )


def settings_view(request):
    """Shows the page; a POST saves the currency and regional preferences."""
    profile = Profile.for_user(request.user)
    form = None
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Preferences saved.")
            return redirect("finance:settings")
    return _render_settings(request, profile, form=form)


@require_POST
@transaction.non_atomic_requests
def check_rates(request):
    """Runs without the per-request transaction so it never holds the stash's write lock
    while it waits on the internet. fetch_latest_rates() takes its own short one for the
    writes."""
    profile = Profile.for_user(request.user)
    try:
        stored, unavailable = fetch_latest_rates(profile.main_currency, _rate_currencies(profile))
    except RateFetchError as error:
        messages.error(request, str(error))
    else:
        if stored:
            messages.success(request, f"Updated {len(stored)} exchange rate{'s' if len(stored) != 1 else ''}.")
        if unavailable:
            messages.warning(
                request,
                f"No rate available for: {', '.join(unavailable)}. Enter it by hand in the admin if you need one.",
            )
    return redirect("finance:settings")


@require_POST
@transaction.non_atomic_requests
def create_backup(request):
    """Runs without the per-request transaction: the backup reads the database through its
    own connection, and nothing here writes to it."""
    backup_root = _stash_backup_root(request)
    backup = finance_backup.backup_now(_current_stash_db_path(request), backup_root)
    if backup:
        messages.success(request, f"Backed up to {backup.relative_to(backup_root)}.")
    else:
        messages.error(request, "Nothing to back up yet - there's no database file.")
    return redirect("finance:settings")


@require_POST
def save_watched_expenses(request):
    profile = Profile.for_user(request.user)
    watched_form = WatchedExpensesForm(request.POST, instance=profile)
    if watched_form.is_valid():
        watched_form.save()
        messages.success(request, "Watched expenses saved.")
        return redirect("finance:settings")
    messages.error(request, "Watched expenses weren't saved - see the errors below.")
    return _render_settings(request, profile, watched_form=watched_form)


def change_pin_view(request):
    """Its own page, reached from Settings: current PIN, new PIN, confirm."""
    if request.method == "POST":
        form = ChangePinForm(request.POST, user=request.user)
        if form.is_valid():
            request.user.set_password(form.cleaned_data["new_pin"])
            request.user.save(update_fields=["password"])
            update_session_auth_hash(request, request.user)
            messages.success(request, "PIN changed.")
            return redirect("finance:settings")
    else:
        form = ChangePinForm(user=request.user)
    return render(request, "finance/change_pin.html", {"form": form})


RECOVERY_CODE_CACHE_SECONDS = 5 * 60


def _recovery_code_cache_key(token):
    return f"stashy:new-recovery-code:{token}"


def recovery_code_view(request):
    """Its own page: a warning with Generate/Back first; Generate mints a new code and
    redirects back to this same URL - a real page reload, so refreshing afterward can't
    regenerate a second code by resubmitting a form.

    The code itself waits in the server's memory cache, and the session (a signed but readable
    cookie) only carries a random one-time key to it. The page shows it once and deletes it.
    Stashy is one process, so the local-memory cache is shared by every request."""
    if request.method == "POST":
        profile = Profile.for_user(request.user)
        token = secrets.token_urlsafe(16)
        cache.set(
            _recovery_code_cache_key(token), security.regenerate_recovery_code(profile), RECOVERY_CODE_CACHE_SECONDS
        )
        request.session["new_recovery_code_token"] = token
        return redirect("finance:recovery_code")
    new_code = None
    token = request.session.pop("new_recovery_code_token", None)
    if token:
        key = _recovery_code_cache_key(token)
        new_code = cache.get(key)
        cache.delete(key)
    return render(request, "finance/recovery_code.html", {"new_code": new_code})


def _backup_path_or_404(request, name):
    """Only ever resolves to a file that's actually inside the current stash's own backups/
    - `name` comes straight off the querystring, so this guards against it smuggling in a
    path (e.g. "../../other-stash/db.sqlite3")."""
    backup_root = _stash_backup_root(request)
    candidate = backup_root / "backups" / Path(name).name
    if candidate not in finance_backup.list_backups(backup_root):
        raise Http404("No such backup.")
    return candidate


@transaction.non_atomic_requests
def backup_restore(request):
    """Restoring overwrites the live database, so this is a confirm-then-act page (like a
    delete) rather than a one-click button on Settings - a wrong pick here is far more costly
    than a wrong pick almost anywhere else in the app. Runs without the per-request
    transaction: the restore needs the stash's write lock itself."""
    name = request.POST.get("name") or request.GET.get("name", "")
    backup_path = _backup_path_or_404(request, name)

    if request.method == "POST":
        stash_name = request.session.get("stash") if not settings.TESTING else None
        if stash_name:
            registry.restore_stash(stash_name, backup_path)
        else:
            finance_backup.restore_from(backup_path, _current_stash_db_path(request), _stash_backup_root(request))
        messages.success(
            request,
            f"Restored from {backup_path.name}. A safety backup of what was there before is in backups/ too.",
        )
        return redirect("finance:settings")

    return render(request, "finance/backup_restore_confirm.html", {"backup": backup_path.name})
