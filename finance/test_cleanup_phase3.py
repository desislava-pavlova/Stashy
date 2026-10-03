"""Plan 02, Phase 3 (tooling and settings): what Stashy shows and records when something goes
wrong with DEBUG off - the friendly 404 and 500 pages and the error log - and the static file
references the fingerprinted-name storage depends on."""

import copy
import logging
import logging.config
import re
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import path

from stashy.settings import base


# Public, so the crash happens for an anonymous client too: this URLconf has no login page
# for LoginRequiredMiddleware to redirect to.
@login_not_required
def _broken_view(request):
    raise RuntimeError("deliberate failure for the 500 page test")


urlpatterns = [path("broken/", _broken_view)]

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
LEAKED_TEMPLATE_SYNTAX = ("{%", "%}", "{#", "#}")


class ErrorPageTests(TestCase):
    """Tests always run with DEBUG off, the way Stashy.bat now runs too."""

    def test_a_missing_page_shows_the_friendly_404(self):
        response = self.client.get("/no-such-page/")

        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, "404.html")
        content = response.content.decode()
        self.assertIn("Nothing stashed here", content)
        self.assertIn("stashy.css", content)
        for syntax in LEAKED_TEMPLATE_SYNTAX:
            self.assertNotIn(syntax, content)

    @override_settings(ROOT_URLCONF=__name__)
    def test_a_crash_shows_the_friendly_500_and_logs_the_traceback(self):
        self.client.raise_request_exception = False

        with self.assertLogs("django.request", level="ERROR") as logs:
            response = self.client.get("/broken/")

        self.assertEqual(response.status_code, 500)
        content = response.content.decode()
        self.assertIn("Something went wrong", content)
        self.assertIn(r"logs\stashy.log", content)
        for syntax in LEAKED_TEMPLATE_SYNTAX:
            self.assertNotIn(syntax, content)
        self.assertIn("deliberate failure for the 500 page test", "\n".join(logs.output))


class LogFileTests(SimpleTestCase):
    """The app's own LOGGING (base.py, not the test settings) writes errors, with their
    traceback, to a file."""

    def test_errors_and_their_traceback_land_in_the_log_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log_file = Path(tmp.name) / "stashy.log"
        config = copy.deepcopy(base.LOGGING)
        config["handlers"]["file"]["filename"] = log_file
        config["root"]["handlers"] = ["file"]
        logging.config.dictConfig(config)
        # Runs before tmp.cleanup (cleanups run last-in, first-out), which closes the file
        # so Windows lets the folder be deleted.
        self.addCleanup(logging.config.dictConfig, settings.LOGGING)

        try:
            raise RuntimeError("deliberate failure for the log file test")
        except RuntimeError:
            logging.getLogger("django.request").exception("Internal Server Error: /broken/")
        logging.getLogger("finance").info("routine detail, below the log's level")
        logging.shutdown()

        text = log_file.read_text(encoding="utf-8")
        self.assertIn("ERROR django.request: Internal Server Error: /broken/", text)
        self.assertIn("Traceback (most recent call last):", text)
        self.assertIn("RuntimeError: deliberate failure for the log file test", text)
        self.assertNotIn("routine detail", text)

    def test_the_log_rotates_instead_of_growing_forever(self):
        handler = base.LOGGING["handlers"]["file"]

        self.assertEqual(handler["class"], "logging.handlers.RotatingFileHandler")
        self.assertGreater(handler["maxBytes"], 0)
        self.assertGreater(handler["backupCount"], 0)
        self.assertEqual(Path(handler["filename"]).parent, base.LOG_DIR)


class StaticFileTests(SimpleTestCase):
    def test_collectstatic_succeeds_with_the_apps_own_storage(self):
        """Stashy.bat runs collectstatic on every launch, so if it fails, Stashy doesn't start.
        Fingerprinting stops at any file a CSS or JS file points to that doesn't exist (the
        vendored libraries' missing .map files did exactly that before StashyStaticFilesStorage)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with override_settings(STATIC_ROOT=tmp.name, STORAGES=base.STORAGES):
            call_command("collectstatic", interactive=False, verbosity=0)
            stylesheet = staticfiles_storage.stored_name("finance/css/stashy.css")
            icons = staticfiles_storage.stored_name("finance/vendor/bootstrap-icons/bootstrap-icons.min.css")

        self.assertRegex(stylesheet, r"^finance/css/stashy\.[0-9a-f]{12}\.css$")
        # References inside CSS are rewritten too, so the icon font is fetched by its new name.
        icons_css = (Path(tmp.name) / icons).read_text(encoding="utf-8")
        self.assertRegex(icons_css, r'url\("fonts/bootstrap-icons\.[0-9a-f]{12}\.woff2')

    def test_every_static_file_a_template_names_exists(self):
        """With fingerprinted names, `{% static %}` raises for a file that doesn't exist, so a
        typo would turn the whole page into an error instead of one missing image."""
        pattern = re.compile(r"""\{%\s*static\s+['"]([^'"]+)['"]""")
        names = set()
        for template in TEMPLATES_DIR.rglob("*.html"):
            names.update(pattern.findall(template.read_text(encoding="utf-8")))

        self.assertTrue(names)
        missing = sorted(name for name in names if finders.find(name) is None)
        self.assertEqual(missing, [])


class TestSettingsTests(SimpleTestCase):
    def test_the_suite_runs_with_its_own_settings(self):
        self.assertTrue(settings.TESTING)
        self.assertFalse(base.TESTING)
        self.assertEqual(settings.PASSWORD_HASHERS, ["django.contrib.auth.hashers.MD5PasswordHasher"])

    def test_the_app_never_falls_back_to_debug_or_a_built_in_secret_key(self):
        source = (Path(base.__file__)).read_text(encoding="utf-8")

        self.assertIn('SECRET_KEY = config("SECRET_KEY")\n', source)
        self.assertIn('DEBUG = config("DEBUG", default=False, cast=bool)', source)
        self.assertFalse(hasattr(base, "AUTH_PASSWORD_VALIDATORS"))


class StashyLocalhostTests(TestCase):
    """Stashy.bat opens http://stashy.localhost:<port>/ (02/10/2026): browsers send every
    *.localhost name to this computer, so the name needs no hosts-file entry."""

    def test_stashy_localhost_is_always_an_allowed_name(self):
        response = self.client.get("/no-such-page/", HTTP_HOST="stashy.localhost:8001")

        self.assertEqual(response.status_code, 404)
        self.assertIn("stashy.localhost", base.ALLOWED_HOSTS)

    def test_a_name_stashy_was_not_given_is_still_refused(self):
        response = self.client.get("/no-such-page/", HTTP_HOST="stashy.app")

        self.assertEqual(response.status_code, 400)
