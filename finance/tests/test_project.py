"""The project around the app: settings, logging, static files, the stashy.localhost address,
and rules the code itself follows (imports at the top, the audit app on its own)."""

import ast
import copy
import logging.config
import re
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from stashy.settings import base

from ..stashes import registry
from .base import StashTestCase

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


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
        self.assertEqual(settings.SETTINGS_MODULE, "stashy.settings.test")
        self.assertEqual(settings.PASSWORD_HASHERS, ["django.contrib.auth.hashers.MD5PasswordHasher"])

    def test_the_suite_only_ever_sees_its_own_test_stash(self):
        self.assertNotEqual(Path(settings.STASHES_DIR), Path(base.STASHES_DIR))
        self.assertEqual(registry.list_stashes(), [settings.TEST_STASH])

    def test_the_app_code_never_asks_whether_it_is_under_test(self):
        # Plan 02, 7.1 (F19): tests run the same code paths as Stashy itself.
        self.assertFalse(hasattr(base, "TESTING"))
        for folder in ("finance", "audit", "stashy"):
            for path in (Path(settings.BASE_DIR) / folder).rglob("*.py"):
                if "tests" in path.parts or path.name.startswith("test") or "migrations" in path.parts:
                    continue
                with self.subTest(file=path.name):
                    self.assertNotIn("TESTING", path.read_text(encoding="utf-8"))

    def test_the_app_never_falls_back_to_debug_or_a_built_in_secret_key(self):
        source = (Path(base.__file__)).read_text(encoding="utf-8")

        self.assertIn('SECRET_KEY = config("SECRET_KEY")\n', source)
        self.assertIn('DEBUG = config("DEBUG", default=False, cast=bool)', source)
        self.assertFalse(hasattr(base, "AUTH_PASSWORD_VALIDATORS"))


class StashyLocalhostTests(StashTestCase):
    """Stashy.bat opens http://stashy.localhost:<port>/ (02/10/2026): browsers send every
    *.localhost name to this computer, so the name needs no hosts-file entry."""

    def test_stashy_localhost_is_always_an_allowed_name(self):
        response = self.client.get("/no-such-page/", HTTP_HOST="stashy.localhost:8001")

        self.assertEqual(response.status_code, 404)
        self.assertIn("stashy.localhost", base.ALLOWED_HOSTS)

    def test_a_name_stashy_was_not_given_is_still_refused(self):
        response = self.client.get("/no-such-page/", HTTP_HOST="stashy.app")

        self.assertEqual(response.status_code, 400)


class AuditStandsAloneTests(SimpleTestCase):
    """6.8 (F36): the audit app imports nothing from the finance app."""

    def test_no_audit_module_imports_finance(self):
        audit_dir = Path(settings.BASE_DIR) / "audit"
        for path in audit_dir.rglob("*.py"):
            if path.name == "tests.py" or "migrations" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                elif isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                else:
                    continue
                for name in names:
                    with self.subTest(file=path.name, module=name):
                        self.assertFalse(name == "finance" or name.startswith("finance."))


class NoHiddenImportCyclesTests(SimpleTestCase):
    """6.10 (F50): every import that stays inside a function says why."""

    def test_every_function_level_import_has_a_reason(self):
        root = Path(settings.BASE_DIR)
        for folder in ("finance", "audit"):
            for path in (root / folder).rglob("*.py"):
                if path.name.startswith("test") or "migrations" in path.parts:
                    continue
                lines = path.read_text(encoding="utf-8").splitlines()
                tree = ast.parse("\n".join(lines))
                for function in ast.walk(tree):
                    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    for node in function.body:
                        if not isinstance(node, (ast.Import, ast.ImportFrom)):
                            continue
                        comments_above = []
                        index = node.lineno - 2
                        while index >= 0 and lines[index].strip().startswith("#"):
                            comments_above.append(lines[index].strip())
                            index -= 1
                        with self.subTest(file=str(path.relative_to(root)), line=node.lineno):
                            self.assertTrue(any(c.startswith("# Imported here") for c in comments_above))
