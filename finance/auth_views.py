"""Views for the multi-stash picker/login screens (Phase 12; rewritten 2026-09-15 so
switching stashes needs no restart; rewritten again in Phase 15 for owner names and remembering
the last stash used). Kept out of finance/views.py since these run *before* a stash's own data
is reachable at all - the picker in particular has to work when this session hasn't picked a
stash yet and the app is pointed at the placeholder database rather than any real stash.
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render

from . import stashes
from .auth_forms import ForgotPinForm, StashCreateForm, StashLoginForm
from .stashes import LAST_STASH_COOKIE, LAST_STASH_COOKIE_AGE


def stash_picker(request):
    """Pick an existing stash (no PIN here - that's the login page). With no stashes at all,
    there's nothing to pick, so this goes straight to creating one (L3). Redirects away once a
    stash is already selected for this session, since from then on switching means an explicit
    "Choose a different stash" action, not landing back here by accident."""
    if request.session.get("stash"):
        return redirect("login")

    choices = stashes.stash_choices()
    if not choices:
        return redirect("stash_create")

    if request.method == "POST" and "select_stash" in request.POST:
        folder_name = request.POST.get("name", "")
        if folder_name in dict(choices):
            request.session["stash"] = folder_name
            return redirect("login")
        messages.error(request, "Pick a stash from the list.")

    return render(request, "registration/stash_picker.html", {"choices": choices})


def stash_create(request):
    """Create a new stash: whose it is, plus a PIN (L1, L3) - its own page now, not embedded
    in the picker, so "no stashes yet" can land directly here."""
    if request.session.get("stash"):
        return redirect("login")

    form = StashCreateForm()
    if request.method == "POST":
        form = StashCreateForm(request.POST)
        if form.is_valid():
            try:
                folder_name, recovery_code = stashes.create_stash(
                    form.cleaned_data["owner_name"], form.cleaned_data["pin"]
                )
            except stashes.StashError as error:
                form.add_error(None, str(error))
            else:
                request.session["stash"] = folder_name
                owner_name = form.cleaned_data["owner_name"]
                return render(request, "registration/stash_recovery_code.html", {
                    "owner_name": owner_name, "owner_possessive": stashes.possessive(owner_name),
                    "recovery_code": recovery_code,
                })

    return render(request, "registration/stash_create.html", {"form": form})


def switch_stash(request):
    """Logs out and forgets this session's chosen stash, straight back to the picker - no
    restart needed. logout() already flushes the whole session (a fresh session belonging to
    no stash's user), which is exactly what letting go of the current stash means here too.
    Also used by the PIN screen's "Choose a different stash" link (L3)."""
    logout(request)
    return redirect("stash_picker")


class StashLoginView(LoginView):
    """The normal Django LoginView, aimed at the one user in whatever stash is current -
    username is fixed (the stash's own name) rather than typed, and adds the lockout this
    app's PIN needs that Django's stock login doesn't provide on its own."""
    form_class = StashLoginForm
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        # Tests log in via Client.force_login() and never exercise this view's own PIN check
        # or lockout - fixed, harmless placeholders here keep that true regardless of whatever
        # stashes/ happens to really contain on the machine running the suite (a couple of
        # pages do render this view directly, e.g. an offline-assets check).
        if settings.TESTING:
            self.stash_name, self.profile = "test", None
            return super().dispatch(request, *args, **kwargs)
        self.stash_name = request.session.get("stash", "")
        if not self.stash_name:
            return redirect("stash_picker")
        self.profile = stashes.profile_for_current_stash()
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {**super().get_initial(), "username": self.stash_name}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["stash_name"] = self.stash_name
        context["owner_name"] = self.profile.owner_name if self.profile else self.stash_name
        context["locked_out"] = bool(self.profile) and stashes.is_locked_out(self.profile)
        context["lockout_minutes"] = (stashes.lockout_seconds_remaining(self.profile) // 60 + 1) if self.profile else 0
        return context

    def post(self, request, *args, **kwargs):
        if self.profile and stashes.is_locked_out(self.profile):
            return self.render_to_response(self.get_context_data())
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        if self.profile:
            stashes.register_successful_login(self.profile)
        response = super().form_valid(form)
        response.set_cookie(
            LAST_STASH_COOKIE, self.stash_name, max_age=LAST_STASH_COOKIE_AGE, samesite="Lax"
        )
        return response

    def form_invalid(self, form):
        if self.profile and not stashes.is_locked_out(self.profile):
            stashes.register_failed_attempt(self.profile)
        return super().form_invalid(form)


def forgot_pin(request):
    stash_name = request.session.get("stash", "")
    if not stash_name:
        return redirect("stash_picker")
    profile = stashes.profile_for_current_stash()
    if profile is None:
        return redirect("login")

    locked_out = stashes.is_locked_out(profile)
    form = ForgotPinForm()
    if request.method == "POST" and not locked_out:
        form = ForgotPinForm(request.POST)
        if form.is_valid():
            ok, new_recovery_code = stashes.reset_pin_with_recovery_code(
                profile, form.cleaned_data["recovery_code"], form.cleaned_data["new_pin"]
            )
            if ok:
                return render(request, "registration/stash_recovery_code.html", {
                    "owner_name": profile.owner_name, "owner_possessive": profile.owner_possessive,
                    "recovery_code": new_recovery_code, "pin_was_reset": True,
                })
            form.add_error("recovery_code", "That recovery code isn't right.")
            locked_out = stashes.is_locked_out(profile)

    return render(request, "registration/forgot_pin.html", {
        "owner_name": profile.owner_name, "owner_possessive": profile.owner_possessive, "form": form,
        "locked_out": locked_out,
        "lockout_minutes": (stashes.lockout_seconds_remaining(profile) // 60 + 1) if locked_out else 0,
    })
