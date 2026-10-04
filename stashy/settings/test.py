"""Settings for `manage.py test`, which picks this module by itself (see manage.py).

Starts from base.py and changes only what tests need to be different."""

import os
import tempfile
from pathlib import Path

# Tests never need the real key, so they don't depend on .env existing. python-decouple
# reads the process environment before .env, so this wins over a key in .env.
os.environ.setdefault("SECRET_KEY", "test-only-key-never-used-for-real-data")

from .base import *  # noqa: F403

# Tests never see your stashes. Each test run gets a new temporary folder, deleted again when
# the run ends, holding one stash: the test stash. finance/apps.py registers it like any other
# stash. Its database is the test runner's own in-memory one, so the empty db.sqlite3 file only
# marks the folder as a stash. finance/tests/base.py runs the tests inside this stash.
_test_run_folder = tempfile.TemporaryDirectory(prefix="stashy-tests-", ignore_cleanup_errors=True)
STASHES_DIR = Path(_test_run_folder.name)
TEST_STASH = "tester"
(STASHES_DIR / TEST_STASH / "backups").mkdir(parents=True)
(STASHES_DIR / TEST_STASH / "db.sqlite3").touch()

# Every stash's connection settings are a copy of `default`'s, the test stash's included. By
# default Django creates an extra test database only after `default`'s, and a run whose tests
# never touch `default` doesn't create that one at all - so neither waits for the other here.
DATABASES = {"default": {**DATABASES["default"], "TEST": {"DEPENDENCIES": []}}}  # noqa: F405

# Tests create many users. Django's normal hasher is slow on purpose (to slow down guessing),
# which makes each one cost a noticeable fraction of a second; MD5 is instant. Fine here,
# because test users and their PINs only ever exist in a throwaway test database.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# The fingerprinted-name storage needs `collectstatic` to have run first; tests use plain names.
STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Some tests make views fail on purpose. Their errors stay out of the real logs/stashy.log
# and out of the test output; a test that checks logging captures it with assertLogs().
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "WARNING"},
}
