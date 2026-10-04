"""Base classes shared by finance's tests (and audit's).

Every test that uses the database runs inside the test stash: the throwaway stash that
stashy/settings/test.py makes for each test run (settings.TEST_STASH). The test's own queries go
to that stash's database, and its test client starts with the stash chosen, as if picked on the
picker - so every request goes through the real middleware, the way it does in Stashy itself.
"""

import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from django.test import Client, TestCase

from .. import db_router
from ..models import Account, Profile
from ..stashes import registry


class StashClient(Client):
    """A test client that has already chosen the test stash, the way the picker does it: by
    putting the stash's name in the session."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.choose_stash(settings.TEST_STASH)

    def choose_stash(self, name):
        session = self.session
        session["stash"] = name
        session.save()
        # The session lives in a signed cookie, so a saved session is a new cookie value.
        self.cookies[settings.SESSION_COOKIE_NAME] = session.session_key

    def force_login(self, user, backend=None):
        # Django's login() empties a session whose earlier login no longer matches (after a PIN
        # change, say), and the chosen stash goes with it. A browser finds its way back through
        # the "last stash used" cookie; this client simply chooses the same stash again.
        stash = self.session.get("stash")
        super().force_login(user, backend)
        if stash and self.session.get("stash") != stash:
            self.choose_stash(stash)


class StashTestCase(TestCase):
    """A test inside the test stash. Its own queries and its requests use the stash's database.
    `default`, the picker's empty placeholder, is off limits unless a class adds it to
    `databases` - so code that loses track of its stash fails here instead of quietly using
    the placeholder."""

    databases = {settings.TEST_STASH}
    client_class = StashClient

    @classmethod
    def setUpClass(cls):
        # Before super(), which already runs setUpTestData().
        cls.enterClassContext(db_router.use_stash(settings.TEST_STASH))
        super().setUpClass()


class TemporaryStashesMixin:
    """Real stash aliases backed by files in a temporary folder - registered at runtime the way
    the live app does it, and removed again afterwards."""

    def make_stashes(self, *names):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(registry, "stashes_dir", return_value=Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in names:
            registry.stash_path(name).mkdir(parents=True)
            sqlite3.connect(registry.stash_db_path(name)).close()
            registry.ensure_registered(name)
            self.addCleanup(self._unregister, name)
        self.addCleanup(db_router.clear_current_stash_alias)
        # Django's test runner only lets a test touch the databases it lists. These aliases
        # don't exist until now, so they're added for this one test.
        test_class = type(self)
        self.addCleanup(setattr, test_class, "databases", test_class.databases)
        test_class.databases = frozenset(test_class.databases) | set(names)

    @staticmethod
    def _unregister(name):
        connections[name].close()
        del connections[name]
        del connections.databases[name]


class LoggedInTestCase(StashTestCase):
    """Logged in as "tester" (self.user), the test stash's one user - a stash's user is named
    after its folder.

    The user is made once per class, in setUpTestData(): Django undoes each test's database
    changes and hands every test its own copy of self.user, so no test sees another's. Logging
    in is per test, because each test gets a new client."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("tester", password="not-a-real-password-123")

    def setUp(self):
        self.client.force_login(self.user)


class LoggedInWithAccountTestCase(LoggedInTestCase):
    """Logged in, with one account called "Checking" (self.account)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.account = Account.objects.create(name="Checking")


class AlexsStashTestCase(LoggedInTestCase):
    """Logged in, and the stash's owner is called Alex (self.profile), so pages say "Alex's
    stash"."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.profile = Profile.for_user(cls.user)
        cls.profile.owner_name = "Alex"
        cls.profile.save(update_fields=["owner_name"])
