"""The project's top-level URLs: the admin (whose login is the PIN
screen), the PIN login, logout and Forgot PIN, and the stash picker.
The picker and the login run before any stash's own data is reachable.
Everything else is in finance/urls.py.
"""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path
from django.views.generic import RedirectView

from finance.views import auth as stash_auth_views


admin.site.site_header = "Stashy admin"
admin.site.site_title = "Stashy admin"
admin.site.index_title = "Data, users & audit log"

urlpatterns = [
    # Before admin.site.urls, so they replace the admin's own login and
    # change-password pages, which know nothing about the PIN lockout.
    # The admin's change-password page would also take any password,
    # even one the PIN screen can't accept, so it sends you to Change
    # PIN.
    path("admin/login/", stash_auth_views.admin_login, name="admin_login"),
    path("admin/password_change/", RedirectView.as_view(pattern_name="finance:change_pin")),
    path("admin/password_change/done/", RedirectView.as_view(pattern_name="finance:change_pin")),
    path("admin/", admin.site.urls),
    path("accounts/login/", stash_auth_views.StashLoginView.as_view(), name="login"),
    # Logging out never needs a login: an expired session still lands on
    # the PIN page.
    path(
        "accounts/logout/",
        login_not_required(auth_views.LogoutView.as_view(next_page="login")),
        name="logout",
    ),
    path("accounts/forgot-pin/", stash_auth_views.forgot_pin, name="forgot_pin"),
    # The stash picker: where someone with no stash chosen is sent (see
    # finance.middleware.RequireStashMiddleware), and where a logged-in
    # one switches stash. These are its whole URL surface, outside
    # finance/urls.py since they run before any stash's own data (or
    # even its database) is reachable.
    path("stashes/", stash_auth_views.stash_picker, name="stash_picker"),
    path("stashes/create/", stash_auth_views.stash_create, name="stash_create"),
    path("stashes/switch/", stash_auth_views.switch_stash, name="switch_stash"),
    path("", include("finance.urls")),
]
