"""The middleware and the stash router: which stash a request uses, its transaction, and
the gates in front of every page (choose a stash, then finish the welcome wizard)."""

import threading
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections, transaction
from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .. import db_router
from ..middleware import (
    RequireSetupMiddleware,
    RequireStashMiddleware,
    StashAtomicRequestMiddleware,
    StashSelectionMiddleware,
)
from ..models import Profile
from ..stashes import registry
from .base import StashTestCase, TemporaryStashesMixin


class StashSelectionMiddlewareTests(SimpleTestCase):
    """The current stash this middleware hands the router (a ContextVar, see
    finance/db_router.py) is what lets a session's own stash resolve without a restart - these
    check the plumbing around it: no stash in the session, and a session naming a stash that
    no longer exists on disk."""

    def setUp(self):
        self.factory = RequestFactory()
        self.addCleanup(db_router.clear_current_stash_alias)

    def test_no_stash_in_session_leaves_the_alias_unset(self):
        seen = {}
        middleware = StashSelectionMiddleware(lambda r: seen.setdefault("alias", db_router.current_stash_alias()))
        request = self.factory.get("/")
        request.session = {}
        middleware(request)
        self.assertIsNone(seen["alias"])

    def test_a_session_naming_a_stash_that_no_longer_exists_is_treated_as_none_chosen(self):
        seen = {}
        middleware = StashSelectionMiddleware(lambda r: seen.setdefault("alias", db_router.current_stash_alias()))
        request = self.factory.get("/")
        request.session = {"stash": "does-not-exist-on-disk"}
        middleware(request)
        self.assertIsNone(seen["alias"])
        self.assertNotIn("stash", request.session)

    def test_the_alias_is_cleared_again_after_the_response(self):
        middleware = StashSelectionMiddleware(lambda r: "ok")
        request = self.factory.get("/")
        request.session = {}
        middleware(request)
        self.assertIsNone(db_router.current_stash_alias())


class RequireStashMiddlewareTests(SimpleTestCase):
    """Which stash is current lives in request.session, so these tests attach a plain dict as
    the session - the middleware only reads and sets its "stash" key."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_redirects_to_the_picker_when_no_stash_is_current(self):
        middleware = RequireStashMiddleware(get_response=lambda r: "should not be reached")
        request = self.factory.get("/transactions/")
        request.session = {}
        response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("stash_picker"))

    def test_the_pickers_own_urls_are_exempt(self):
        middleware = RequireStashMiddleware(get_response=lambda r: "ok")
        request = self.factory.get("/stashes/")
        request.session = {}
        response = middleware(request)
        self.assertEqual(response, "ok")

    def test_passes_through_once_a_stash_is_current(self):
        middleware = RequireStashMiddleware(get_response=lambda r: "ok")
        request = self.factory.get("/transactions/")
        request.session = {"stash": "alex"}
        response = middleware(request)
        self.assertEqual(response, "ok")


class RememberedStashCookieTests(SimpleTestCase):
    """Phase 15 L3: the "last stash used" cookie lets RequireStashMiddleware skip the picker
    and land straight on that stash's own PIN screen - a browser cookie, separate from the
    session logout() flushes. Same pattern as RequireStashMiddlewareTests above (a plain dict
    stands in for the session)."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_a_remembered_stash_that_still_exists_is_selected_and_the_request_redirected(self):
        with mock.patch.object(registry, "stash_exists", return_value=True):
            middleware = RequireStashMiddleware(get_response=lambda r: "should not be reached")
            request = self.factory.get("/accounts/login/")
            request.session = {}
            request.COOKIES = {registry.LAST_STASH_COOKIE: "alex"}
            response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/accounts/login/")
        self.assertEqual(request.session["stash"], "alex")

    def test_a_remembered_stash_that_no_longer_exists_falls_back_to_the_picker(self):
        with mock.patch.object(registry, "stash_exists", return_value=False):
            middleware = RequireStashMiddleware(get_response=lambda r: "should not be reached")
            request = self.factory.get("/accounts/login/")
            request.session = {}
            request.COOKIES = {registry.LAST_STASH_COOKIE: "gone"}
            response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("stash_picker"))
        self.assertNotIn("stash", request.session)

    def test_no_cookie_falls_back_to_the_picker_as_before(self):
        middleware = RequireStashMiddleware(get_response=lambda r: "should not be reached")
        request = self.factory.get("/accounts/login/")
        request.session = {}
        request.COOKIES = {}
        response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("stash_picker"))

    def test_the_pickers_own_urls_stay_exempt_even_with_a_remembered_cookie(self):
        # A cookie doesn't force-select a stash on the picker's own pages - picking one there
        # is still an explicit action, per stash_picker()'s "already selected -> redirect to
        # login" guard, not something this middleware should short-circuit.
        with mock.patch.object(registry, "stash_exists", return_value=True):
            middleware = RequireStashMiddleware(get_response=lambda r: "ok")
            request = self.factory.get("/stashes/")
            request.session = {}
            request.COOKIES = {registry.LAST_STASH_COOKIE: "alex"}
            response = middleware(request)
        self.assertEqual(response, "ok")
        self.assertNotIn("stash", request.session)


class RequireSetupMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_unfinished_setup_redirects_to_its_own_next_step(self):
        middleware = RequireSetupMiddleware(get_response=lambda r: "should not be reached")
        request = self.factory.get("/transactions/")
        request.stashy_profile = Profile(welcome_step=1)
        response = middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("finance:welcome_step", args=[2]))

    def test_finished_setup_passes_through(self):
        middleware = RequireSetupMiddleware(get_response=lambda r: "ok")
        request = self.factory.get("/transactions/")
        request.stashy_profile = Profile(welcome_step=Profile.WELCOME_STEPS_DONE)
        self.assertEqual(middleware(request), "ok")

    def test_the_wizards_own_urls_are_exempt(self):
        middleware = RequireSetupMiddleware(get_response=lambda r: "ok")
        request = self.factory.get("/welcome/2/")
        request.stashy_profile = Profile(welcome_step=0)
        self.assertEqual(middleware(request), "ok")

    def test_logout_stays_reachable_mid_setup(self):
        middleware = RequireSetupMiddleware(get_response=lambda r: "ok")
        request = self.factory.get("/accounts/logout/")
        request.stashy_profile = Profile(welcome_step=0)
        self.assertEqual(middleware(request), "ok")

    def test_no_profile_on_the_request_passes_through(self):
        # Regional preferences middleware never sets stashy_profile for an unauthenticated
        # request - nothing here for this middleware to act on either.
        middleware = RequireSetupMiddleware(get_response=lambda r: "ok")
        request = self.factory.get("/transactions/")
        self.assertEqual(middleware(request), "ok")


class StashAtomicRequestTests(TemporaryStashesMixin, SimpleTestCase):
    """1.1/1.2 (F1): only the stash the request uses gets a transaction."""

    def setUp(self):
        self.make_stashes("alpha", "beta")
        self.middleware = StashAtomicRequestMiddleware(lambda request: HttpResponse())
        self.request = RequestFactory().get("/")

    def test_registered_stashes_never_get_djangos_per_request_transaction(self):
        self.assertFalse(connections.settings["alpha"]["ATOMIC_REQUESTS"])
        self.assertFalse(connections.settings["beta"]["ATOMIC_REQUESTS"])

    def test_only_the_active_stash_is_in_a_transaction_during_the_view(self):
        seen = {}

        def view(request):
            seen["alpha_atomic"] = connections["alpha"].in_atomic_block
            seen["beta_connected"] = connections["beta"].connection is not None
            seen["beta_atomic"] = connections["beta"].in_atomic_block
            return HttpResponse("ok")

        db_router.set_current_stash_alias("alpha")
        response = self.middleware.process_view(self.request, view, (), {})

        self.assertEqual(response.content, b"ok")
        self.assertEqual(seen, {"alpha_atomic": True, "beta_connected": False, "beta_atomic": False})
        self.assertFalse(connections["alpha"].in_atomic_block)

    def test_an_error_in_the_view_rolls_back_its_changes(self):
        with connections["alpha"].cursor() as cursor:
            cursor.execute("CREATE TABLE note (content TEXT)")

        def view(request):
            with connections["alpha"].cursor() as cursor:
                cursor.execute("INSERT INTO note VALUES ('half-done')")
            raise ValueError("something broke")

        db_router.set_current_stash_alias("alpha")
        with self.assertRaises(ValueError):
            self.middleware.process_view(self.request, view, (), {})
        with connections["alpha"].cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM note")
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_a_non_atomic_view_runs_without_a_transaction(self):

        @transaction.non_atomic_requests
        def view(request):
            return HttpResponse(str(connections["alpha"].in_atomic_block))

        db_router.set_current_stash_alias("alpha")
        self.assertEqual(self.middleware.process_view(self.request, view, (), {}).content, b"False")

    def test_without_a_stash_django_handles_the_request_as_usual(self):
        self.assertIsNone(self.middleware.process_view(self.request, lambda request: HttpResponse(), (), {}))

    def test_it_runs_after_every_other_view_middleware(self):
        self.assertEqual(settings.MIDDLEWARE[-1], "finance.middleware.StashAtomicRequestMiddleware")


class MiddlewareExemptPrefixesTests(SimpleTestCase):
    """5.6: the prefixes come from the URL names and settings, and match the old strings."""

    def test_require_stash_exempts_the_picker_static_files_and_the_admin(self):
        self.assertEqual(RequireStashMiddleware.exempt_prefixes(), ("/stashes/", "/static/", "/admin/"))

    def test_require_setup_exempts_the_wizard_static_files_the_admin_and_logout(self):
        self.assertEqual(
            RequireSetupMiddleware.exempt_prefixes(), ("/welcome/", "/static/", "/admin/", "/accounts/logout/")
        )


class RouterContextVarTests(SimpleTestCase):
    """6.4 (F18): the current stash is private to its own thread."""

    def tearDown(self):
        db_router.clear_current_stash_alias()

    def test_another_thread_does_not_see_this_threads_stash(self):
        db_router.set_current_stash_alias("alpha")
        seen = {}
        thread = threading.Thread(target=lambda: seen.update(other=db_router.current_stash_alias()))
        thread.start()
        thread.join()
        self.assertEqual(db_router.current_stash_alias(), "alpha")
        self.assertIsNone(seen["other"])

    def test_clearing_goes_back_to_no_stash(self):
        db_router.set_current_stash_alias("alpha")
        db_router.clear_current_stash_alias()
        self.assertIsNone(db_router.current_stash_alias())


class MiddlewareChainTests(StashTestCase):
    """7.2 (F19): the whole middleware chain, the way a browser meets it. `Client()` is a fresh
    browser that hasn't chosen a stash yet; self.client has (see base.py)."""

    # The picker runs before any stash is chosen, on `default`, the empty placeholder.
    databases = {"default", settings.TEST_STASH}

    def test_with_no_stash_chosen_every_page_goes_to_the_picker(self):
        for name in ("finance:dashboard", "finance:transaction_list", "finance:settings", "login"):
            with self.subTest(page=name):
                response = Client().get(reverse(name))
                self.assertRedirects(response, reverse("stash_picker"), fetch_redirect_response=False)

    def test_the_picker_lists_each_stash_by_its_owner_name(self):
        Profile.objects.create(user=get_user_model().objects.create_user(settings.TEST_STASH), owner_name="Alex")
        response = Client().get(reverse("stash_picker"))
        self.assertEqual(response.context["choices"], [(settings.TEST_STASH, "Alex")])

    def test_the_remembered_stash_opens_on_its_own_pin_screen(self):
        browser = Client()
        browser.cookies[registry.LAST_STASH_COOKIE] = settings.TEST_STASH
        response = browser.get(reverse("finance:dashboard"), follow=True)
        # First the same page again with the stash chosen, then the PIN screen: not logged in.
        self.assertEqual(response.redirect_chain[0], (reverse("finance:dashboard"), 302))
        self.assertTemplateUsed(response, "registration/login.html")
        self.assertEqual(response.context["stash_name"], settings.TEST_STASH)

    def test_picking_a_stash_and_typing_its_pin_opens_the_dashboard(self):
        get_user_model().objects.create_user(settings.TEST_STASH, password="1234")
        browser = Client()
        response = browser.post(reverse("stash_picker"), {"select_stash": "", "name": settings.TEST_STASH})
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        response = browser.post(reverse("login"), {"username": settings.TEST_STASH, "password": "1234"}, follow=True)
        self.assertEqual(response.redirect_chain, [(reverse("finance:dashboard"), 302)])
        self.assertTemplateUsed(response, "finance/dashboard.html")
        self.assertEqual(browser.cookies[registry.LAST_STASH_COOKIE].value, settings.TEST_STASH)

    def test_an_unfinished_welcome_wizard_comes_before_every_page(self):
        user = get_user_model().objects.create_user(settings.TEST_STASH)
        Profile.objects.create(user=user, welcome_step=1)
        self.client.force_login(user)
        for name in ("finance:dashboard", "finance:transaction_list", "finance:settings"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertRedirects(
                    response, reverse("finance:welcome_step", args=[2]), fetch_redirect_response=False
                )

    def test_a_page_in_a_chosen_stash_never_touches_the_placeholder(self):
        self.client.force_login(get_user_model().objects.create_user(settings.TEST_STASH))
        with CaptureQueriesContext(connections["default"]) as placeholder_queries:
            response = self.client.get(reverse("finance:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(placeholder_queries), 0)
