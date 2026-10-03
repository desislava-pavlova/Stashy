"""
URL configuration for stashy project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.urls import include, path

from finance.views import auth as stash_auth_views

admin.site.site_header = "Stashy admin"
admin.site.site_title = "Stashy admin"
admin.site.index_title = "Data, users & audit log"

urlpatterns = [
    # Before admin.site.urls, so it replaces the admin's own login page, which knows nothing
    # about the PIN lockout.
    path("admin/login/", stash_auth_views.admin_login, name="admin_login"),
    path("admin/", admin.site.urls),
    path("accounts/login/", stash_auth_views.StashLoginView.as_view(), name="login"),
    # Logging out never needs a login: an expired session still lands on the PIN page.
    path("accounts/logout/", login_not_required(auth_views.LogoutView.as_view(next_page="login")), name="logout"),
    path("accounts/forgot-pin/", stash_auth_views.forgot_pin, name="forgot_pin"),
    # The stash picker only works before a stash is chosen for this session (see
    # finance.middleware.RequireStashMiddleware) - these are its whole URL surface, outside
    # finance/urls.py since they run before any stash's own data (or even its database) is
    # reachable.
    path("stashes/", stash_auth_views.stash_picker, name="stash_picker"),
    path("stashes/create/", stash_auth_views.stash_create, name="stash_create"),
    path("stashes/switch/", stash_auth_views.switch_stash, name="switch_stash"),
    path("", include("finance.urls")),
]
