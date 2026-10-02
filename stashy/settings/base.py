"""Stashy's settings: everything the app itself runs with.

`manage.py`, Waitress (`stashy/wsgi.py`) and `Stashy.bat` use this module. `manage.py test`
uses `stashy/settings/test.py`, which starts from this one and changes a few test-only
values. Per-computer values (the secret key, DEBUG, allowed hosts, port) come from `.env`.

Django's reference for every setting: https://docs.djangoproject.com/en/6.1/ref/settings/
"""

from pathlib import Path

from decouple import config
from django.contrib.messages import constants as message_constants

# --- Folders --------------------------------------------------------------------------------
# Every folder Stashy reads or writes is named here and nowhere else, so moving the data out
# of the program folder (plan 04, the .exe) only changes these lines.
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STASHES_DIR = BASE_DIR / "stashes"
STATIC_ROOT = BASE_DIR / "staticfiles"
LOG_DIR = BASE_DIR / "logs"

# True only in stashy/settings/test.py. The stash and setup middleware skip their gates
# under tests, so a test doesn't have to pick a stash first (plan 02 phase 7 removes this).
TESTING = False


# No fallback: Stashy.bat writes a random key into .env on first run, and a missing key
# should stop Stashy rather than quietly use a key that's public in the source code.
SECRET_KEY = config("SECRET_KEY")

# Off unless .env turns it on. With DEBUG on, an error shows the code and settings to
# anyone who sees the page, so it's for working on Stashy, not for using it.
DEBUG = config("DEBUG", default=False, cast=bool)

# stashy.localhost is the address Stashy.bat opens: browsers send every *.localhost name to
# this computer by themselves. It's always allowed, whatever .env lists.
ALLOWED_HOSTS = [*config("ALLOWED_HOSTS", default="localhost,127.0.0.1").split(","), "stashy.localhost"]


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "audit",
    "finance",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "finance.middleware.StashSelectionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "finance.middleware.RequireStashMiddleware",
    "audit.middleware.AuditContextMiddleware",
    "finance.middleware.RegionalPreferencesMiddleware",
    "finance.middleware.RequireSetupMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Last, so its process_view runs after every other one (CSRF included).
    "finance.middleware.StashAtomicRequestMiddleware",
]

ROOT_URLCONF = "stashy.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "finance.context_processors.regional_preferences",
                "finance.context_processors.stash_owner",
            ],
        },
    },
]

WSGI_APPLICATION = "stashy.wsgi.application"


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

# SQLite only, no database server to install. Each stash is its own db.sqlite3 under
# STASHES_DIR/<name>/, registered as its own connection alias (finance.stashes.ensure_registered())
# and chosen per request, from the session, by DATABASE_ROUTERS below - so switching stashes
# needs no restart. `default` is only the empty placeholder Django falls back to before a
# session has picked a stash (see finance.middleware.RequireStashMiddleware); no app data
# is ever stored in it.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "_picker.sqlite3",
        # Every request is all-or-nothing: if anything fails part-way, nothing it changed is kept.
        "ATOMIC_REQUESTS": True,
        "OPTIONS": {
            # Take the write lock up front and wait for it, instead of failing with
            # "database is locked" when the multi-threaded server overlaps two requests.
            "transaction_mode": "IMMEDIATE",
            "timeout": 20,
        },
    }
}
DATABASE_ROUTERS = ["finance.db_router.StashRouter"]

# Signed-cookie sessions (not the default DB-backed ones): the session itself has to work
# *before* a stash - the only place a DB-backed session could live - has even been chosen, to
# hold the picker's own "which stash did you pick" state. Fine for a local, single-user app;
# session data here is small (a user id, the stash name, an auth hash).
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"

# Django's default, spelled out because Stashy relies on it: a signed cookie can be read by
# anyone holding it, so a secret shown once (a new recovery code) waits here, in this one
# server process's memory, and the session only carries a random key to it.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}


# No AUTH_PASSWORD_VALIDATORS: a stash's login is its 4-digit PIN, checked by
# finance.stashes, and Django's password rules never apply to it.


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Europe/Sofia"

USE_I18N = True

USE_TZ = True

# European date formats (DD/MM/YYYY, weeks start Monday) — see stashy/formats/en/formats.py
FORMAT_MODULE_PATH = ["stashy.formats"]


# Static files (CSS, JavaScript, images): served by WhiteNoise from STATIC_ROOT, which
# Stashy.bat fills with `collectstatic` on every launch. The "Manifest" storage gives each
# file a name containing a fingerprint of its content (stashy.3f9a1c.css), so the browser
# can keep files cached for good and still fetches the new one the moment a file changes.
# With DEBUG on, `{% static %}` keeps the plain names, so edits show up without collectstatic.
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "stashy.storage.StashyStaticFilesStorage"},
}

# Bootstrap's name for the red alert style is 'danger', not 'error'.
MESSAGE_TAGS = {message_constants.ERROR: "danger"}

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "finance:dashboard"
LOGOUT_REDIRECT_URL = "login"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Audit log: every create/update/delete of models in these apps is recorded (see the audit app).
AUDIT_TRACKED_APPS = ["finance"]


# Logging: warnings and errors (with the full traceback) go to the console window and to
# LOG_DIR/stashy.log. With DEBUG off, the browser only shows the friendly 500 page, so this
# file is where a crash's details end up. It rotates at 2 MB, keeping 3 older files.
LOG_DIR.mkdir(exist_ok=True)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "detailed": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "detailed"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "stashy.log",
            "maxBytes": 2 * 1024 * 1024,
            "backupCount": 3,
            "encoding": "utf-8",
            # Opens the file on the first message, so a run that logs nothing never touches it.
            "delay": True,
            "formatter": "detailed",
        },
    },
    "root": {"handlers": ["console", "file"], "level": "WARNING"},
}
