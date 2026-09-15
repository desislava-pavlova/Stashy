from django.conf import settings
from django.shortcuts import redirect

from . import db_router, regional, stashes
from .models import Profile


class StashSelectionMiddleware:
    """Multi-stash login (Phase 12, rewritten 2026-09-15): decides which stash's database
    this request's ORM queries land in, from the session rather than a file read once at
    process start - what lets someone switch stashes without closing and reopening Stashy.bat.
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
            if stashes.stash_exists(alias):
                stashes.ensure_registered(alias)
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
    """Multi-stash login (Phase 12): nothing in the app is reachable until a stash has been
    chosen for this session. Everything redirects to the picker until then, except the
    picker's own pages, static files, and the admin (useful for a from-scratch troubleshooting
    session)."""
    EXEMPT_PREFIXES = ("/stashes/", "/static/", "/admin/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.TESTING:
            return self.get_response(request)
        if not request.session.get("stash") and not request.path.startswith(self.EXEMPT_PREFIXES):
            return redirect("stash_picker")
        return self.get_response(request)


class RegionalPreferencesMiddleware:
    """Show every page in the logged-in person's own date, number and time zone preferences (D10)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)
        with regional.activate(Profile.for_user(user).preferences):
            return self.get_response(request)
