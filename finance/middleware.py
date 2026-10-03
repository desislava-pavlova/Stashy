from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, transaction
from django.shortcuts import redirect
from django.urls import reverse

from . import db_router, regional
from .models import Profile
from .stashes import registry


def _folder_of(url):
    """The folder part of a URL: "/welcome/1/" -> "/welcome/"."""
    return url.rstrip("/").rsplit("/", 1)[0] + "/"


class StashAtomicRequestMiddleware:
    """ATOMIC_REQUESTS, for the one stash this request uses.

    Django's own ATOMIC_REQUESTS wraps the view in atomic() for every database that has the
    flag. Stashes are registered at runtime and any of them could be the active one, so the
    flag is off for them (registry.ensure_registered) and this wraps the view for the active
    stash only - every change the view makes is kept or rolled back together, and the other
    stashes' files are never locked. Before a stash is chosen there's no active alias, and
    Django's normal handling of `default` applies.

    It works in process_view (the last view middleware to run, so CSRF has already been
    checked) and calls the view itself inside the transaction: Django skips its own
    atomic-wrapping when a view middleware returns the response. A view marked with
    @transaction.non_atomic_requests (no `using`) runs without a transaction, because the
    stash's alias isn't known when the decorator runs.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        alias = db_router.current_stash_alias()
        if alias is None:
            return None
        non_atomic = getattr(view_func, "_non_atomic_requests", set())
        if alias in non_atomic or DEFAULT_DB_ALIAS in non_atomic:
            return view_func(request, *view_args, **view_kwargs)
        with transaction.atomic(using=alias):
            return view_func(request, *view_args, **view_kwargs)


class StashSelectionMiddleware:
    """Decides which stash's database this request's ORM queries land in, from the session
    rather than a file read once at process start - what lets someone switch stashes without
    closing and reopening Stashy.bat.
    Must run before AuthenticationMiddleware, since even loading request.user is itself a
    routed query. Sets a thread-local finance.db_router.StashRouter reads for the duration of
    this request/thread, and clears it again afterwards so a worker thread never carries a
    stale stash into a later request it happens to handle."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.TESTING:
            return self.get_response(request)
        alias = request.session.get("stash") or None
        if alias:
            if registry.stash_exists(alias):
                registry.ensure_registered(alias)
            else:
                # Named a stash whose folder no longer exists (deleted by hand) - treat it the
                # same as none chosen rather than routing queries at a dead alias.
                alias = None
                request.session.pop("stash", None)
        db_router.set_current_stash_alias(alias)
        try:
            return self.get_response(request)
        finally:
            db_router.clear_current_stash_alias()


class RequireStashMiddleware:
    """Nothing in the app is reachable until a stash has been
    chosen for this session. Everything redirects to the picker until then, except the
    picker's own pages, static files, and the admin (useful for a from-scratch troubleshooting
    session).

    Before falling back to the picker, check the "last stash used" cookie -
    logging out flushes the session (and with it session['stash']) but that cookie survives,
    so Stashy opens straight on that stash's own PIN screen instead of the picker. Redirects
    back to the same URL rather than just setting the session and carrying on, so the *next*
    request is the one StashSelectionMiddleware (which runs before this one) sees with the
    session already populated - otherwise this request's own queries would still be routed by
    the stale, pre-fallback thread-local it already set."""

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def exempt_prefixes():
        """The picker's own pages, static files and the admin. Built from the URL names, so
        renaming a URL can't silently lock its page behind the picker."""
        return (reverse("stash_picker"), settings.STATIC_URL, reverse("admin:index"))

    def __call__(self, request):
        if settings.TESTING:
            return self.get_response(request)
        if not request.session.get("stash") and not request.path.startswith(self.exempt_prefixes()):
            remembered = request.COOKIES.get(registry.LAST_STASH_COOKIE)
            if remembered and registry.stash_exists(remembered):
                request.session["stash"] = remembered
                return redirect(request.get_full_path())
            return redirect("stash_picker")
        return self.get_response(request)


class RequireSetupMiddleware:
    """Once logged in, an unfinished welcome wizard blocks everything else -
    redirects to wherever it left off (Profile.welcome_step), so closing Stashy mid-setup
    resumes there next time instead of dropping into a half-configured dashboard. Placed after
    RegionalPreferencesMiddleware in settings.py so it can reuse the Profile already fetched
    there (`request.stashy_profile`) instead of a second query."""

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def exempt_prefixes():
        """The wizard's own steps, static files, the admin and logging out."""
        return (
            _folder_of(reverse("finance:welcome_step", kwargs={"step": 1})),
            settings.STATIC_URL,
            reverse("admin:index"),
            reverse("logout"),
        )

    def __call__(self, request):
        if settings.TESTING:
            return self.get_response(request)
        profile = getattr(request, "stashy_profile", None)
        if profile is not None and not profile.setup_finished and not request.path.startswith(self.exempt_prefixes()):
            return redirect("finance:welcome_step", step=profile.welcome_step + 1)
        return self.get_response(request)


class RegionalPreferencesMiddleware:
    """Show every page in the logged-in person's own date, number and time zone preferences
    (D10). Stashes the fetched Profile on the request (`request.stashy_profile`) so nothing
    else needs to query for it again this request - the owner-name context processor
    (finance.context_processors.stash_owner) reuses it rather than fetching a second time."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)
        request.stashy_profile = Profile.for_user(user)
        with regional.activate(request.stashy_profile.preferences):
            return self.get_response(request)
