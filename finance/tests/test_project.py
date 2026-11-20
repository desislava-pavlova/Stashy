"""The project around the app: settings, logging, static files, the
stashy.localhost address, and rules the code itself follows (imports at
the top, the audit app on its own)."""

import ast
import copy
import logging.config
import os
import re
import runpy
import secrets
import shutil
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from scripts import bootstrap
from stashy.settings import base

from ..models import Profile
from ..stashes import registry
from .base import StashTestCase


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


class LogFileTests(SimpleTestCase):
    """The app's own LOGGING (base.py, not the test settings) writes
    errors, with their traceback, to a file."""

    def test_errors_and_their_traceback_land_in_the_log_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log_file = Path(tmp.name) / "stashy.log"
        config = copy.deepcopy(base.LOGGING)
        config["handlers"]["file"]["filename"] = log_file
        config["root"]["handlers"] = ["file"]
        logging.config.dictConfig(config)
        # Runs before tmp.cleanup (cleanups run last-in, first-out),
        # which closes the file so Windows lets the folder be deleted.
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
        """Stashy.bat runs collectstatic on every launch, so if it
        fails, Stashy doesn't start. Fingerprinting stops at any file a
        CSS or JS file points to that doesn't exist (the vendored
        libraries' missing .map files did exactly that before
        StashyStaticFilesStorage)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with override_settings(STATIC_ROOT=tmp.name, STORAGES=base.STORAGES):
            call_command("collectstatic", interactive=False, verbosity=0)
            stylesheet = staticfiles_storage.stored_name("finance/css/stashy.css")
            icons = staticfiles_storage.stored_name("finance/vendor/bootstrap-icons/bootstrap-icons.min.css")

        self.assertRegex(stylesheet, r"^finance/css/stashy\.[0-9a-f]{12}\.css$")
        # References inside CSS are rewritten too, so the icon font is
        # fetched by its new name.
        icons_css = (Path(tmp.name) / icons).read_text(encoding="utf-8")
        self.assertRegex(icons_css, r'url\("fonts/bootstrap-icons\.[0-9a-f]{12}\.woff2')

    def test_every_stashy_variable_the_stylesheet_uses_is_defined_in_it(self):
        """An undefined `var(--stashy-x)` quietly uses its fallback, or
        nothing: S6's Edit-mode flash named `--stashy-success-rgb`,
        which was never defined, so it was always Bootstrap's fixed
        green and ignored dark mode."""
        css = finders.find("finance/css/stashy.css")
        text = Path(css).read_text(encoding="utf-8")
        used = set(re.findall(r"var\(\s*(--stashy-[a-z0-9-]+)", text))
        defined = set(re.findall(r"(--stashy-[a-z0-9-]+)\s*:", text))
        self.assertTrue(used)
        self.assertEqual(sorted(used - defined), [])

    def test_the_edit_mode_flash_uses_the_pale_green_that_has_a_dark_mode_twin(self):
        text = Path(finders.find("finance/css/stashy.css")).read_text(encoding="utf-8")
        flash = text[text.index("@keyframes quick-edit-flash") :].split("}")[0]  # the `from` step
        self.assertIn("var(--stashy-positive-soft)", flash)

    def test_every_stashy_token_the_light_theme_sets_is_set_in_the_dark_theme_too(self):
        """A colour defined only for light mode stays that colour on the
        dark page. The tokens that are the same in both themes (the
        fonts) are the only ones left out on purpose. The flash above is
        the example: its pale green needs its own dark twin."""
        text = Path(finders.find("finance/css/stashy.css")).read_text(encoding="utf-8")
        light_start = text.index('[data-bs-theme="light"] {')
        dark_start = text.index('[data-bs-theme="dark"] {')
        token = r"(--stashy-[a-z0-9-]+)\s*:"
        light = set(re.findall(token, text[light_start:dark_start]))
        dark = set(re.findall(token, text[dark_start:].split("\n}\n")[0]))
        self.assertIn("--stashy-positive-soft", light)  # so a change to the file's layout can't empty this
        self.assertEqual(sorted(light - dark), ["--stashy-font-body", "--stashy-font-display"])
        self.assertEqual(sorted(dark - light), [])

    def test_every_static_file_a_template_names_exists(self):
        """With fingerprinted names, `{% static %}` raises for a file
        that doesn't exist, so a typo would turn the whole page into an
        error instead of one missing image."""
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
        # Plan 02, 7.1 (F19): tests run the same code paths as Stashy
        # itself.
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

    def test_an_empty_secret_key_stops_stashy_at_the_start(self):
        """Plan 03, S8: Django alone would start, then show an error
        page on the first visit. "" is the line .env.example ships; only
        spaces count as empty too. The last value is the placeholder the
        old .env.example shipped, public on GitHub."""
        for value in ("", " ", "change-me-to-a-long-random-string"):
            with (
                self.subTest(value=value),
                mock.patch.dict(os.environ, {"SECRET_KEY": value}),
                self.assertRaisesMessage(ImproperlyConfigured, "SECRET_KEY in .env is empty or still the example's"),
            ):
                runpy.run_path(base.__file__)


class DeployCheckTests(StashTestCase):
    """Django's checks ask every database whether it supports JSON
    fields. That is a query, so this test needs the databases the checks
    can reach (as PendingMigrationsTests does); in a SimpleTestCase it
    passed only when another test had already asked."""

    databases = {"default", settings.TEST_STASH}

    def test_the_deploy_check_only_finds_what_it_was_told_to_skip(self):
        """Plan 03, S10: the four HTTPS warnings can't apply to Stashy
        and are silenced by name, so a new warning fails this test."""
        with override_settings(SECRET_KEY=secrets.token_urlsafe(50)):
            call_command("check", deploy=True, fail_level="WARNING", stdout=StringIO(), stderr=StringIO())


class LoginCookieTests(StashTestCase):
    """Plan 03, Q4: a login lasts until the browser closes."""

    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(settings.TEST_STASH, password="1234")
        Profile.objects.create(user=user, owner_name="Alex")

    def test_the_login_cookie_carries_no_expiry_date(self):
        """So the browser drops it when it closes. Django's default kept
        it for two weeks."""
        self.client.post(reverse("login"), {"username": settings.TEST_STASH, "password": "1234"})
        cookie = self.client.cookies[settings.SESSION_COOKIE_NAME]
        self.assertEqual(cookie["max-age"], "")
        self.assertEqual(cookie["expires"], "")


class PendingMigrationsTests(StashTestCase):
    """A model change that never got its migration. The tests build
    their database from the migrations, so a change that adds no column
    (a default, a help text, an ordering) can be missing without any
    other test noticing; Stashy.bat would then keep asking for it, or a
    later `makemigrations` would quietly put it in an unrelated
    migration. `--dry-run` writes nothing, and under the test settings
    the only databases it can see are the throwaway ones.

    The migrations are not in git yet, so a fresh clone has none, and
    then every model would count as new: the test skips itself there."""

    # makemigrations looks at the migration history of every database
    # the router knows.
    databases = {"default", settings.TEST_STASH}

    def test_no_model_change_is_waiting_for_its_migration(self):
        for label in ("finance", "audit"):
            folder = Path(apps.get_app_config(label).path) / "migrations"
            if not any(folder.glob("0*.py")):
                self.skipTest(f"{label} has no migration files here (they are kept out of git)")
        output = StringIO()
        try:
            call_command("makemigrations", check=True, dry_run=True, stdout=output, stderr=output)
        except SystemExit:
            self.fail(f"A model changed without a migration:\n{output.getvalue()}")


class EnvFileTests(SimpleTestCase):
    """Plan 03, S8: .env.example ships no usable secret key, and
    Stashy.bat's first run (scripts/bootstrap.py) makes .env from it."""

    EXAMPLE = Path(settings.BASE_DIR) / ".env.example"

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for name, value in [("ROOT", self.root), ("step", mock.Mock())]:
            patcher = mock.patch.object(bootstrap, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_example_leaves_the_secret_key_empty(self):
        lines = self.EXAMPLE.read_text(encoding="utf-8").splitlines()
        self.assertIn("SECRET_KEY=", lines)

    def test_the_first_run_copies_the_example_with_a_new_secret_key(self):
        shutil.copy(self.EXAMPLE, self.root / ".env.example")
        bootstrap.ensure_env_file()

        example = self.EXAMPLE.read_text(encoding="utf-8").splitlines()
        created = (self.root / ".env").read_text(encoding="utf-8").splitlines()
        key_line = example.index("SECRET_KEY=")
        self.assertRegex(created[key_line], r"^SECRET_KEY=[\w-]{60,}$")
        self.assertEqual(created[:key_line] + created[key_line + 1 :], example[:key_line] + example[key_line + 1 :])

    def test_without_the_example_the_first_run_stops_with_a_message_and_makes_no_env(self):
        with self.assertRaisesMessage(bootstrap.SetupError, ".env.example is missing"):
            bootstrap.ensure_env_file()
        self.assertFalse((self.root / ".env").exists())

    def test_an_existing_env_file_is_never_touched(self):
        shutil.copy(self.EXAMPLE, self.root / ".env.example")
        (self.root / ".env").write_text("SECRET_KEY=mine\nPORT=8001\n", encoding="utf-8")
        bootstrap.ensure_env_file()
        self.assertEqual((self.root / ".env").read_text(encoding="utf-8"), "SECRET_KEY=mine\nPORT=8001\n")


class LaunchScriptFindsTheSameStashesTests(SimpleTestCase):
    """Plan 03, S110: scripts/bootstrap.py scans stashes/ while it is
    still setting things up (before Django is configured), so it must
    find the same folders the app's own scan does. Otherwise a reserved
    name would be migrated at launch as a database nothing has
    registered, and Stashy would refuse to start. Both go through
    folder_names.stash_folders(); these tests are what notices one of
    them going its own way again."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        patcher = mock.patch.object(bootstrap, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        # _stash_dirs() puts ROOT on the path; this keeps the temporary
        # folder out of it afterwards.
        patcher = mock.patch.object(sys, "path", [*sys.path])
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_folder(self, name, *, database=True):
        folder = self.root / "stashes" / name
        folder.mkdir(parents=True)
        if database:
            (folder / "db.sqlite3").touch()

    def test_only_real_stashes_are_found(self):
        self.make_folder("alex")
        self.make_folder("default-dan")
        self.make_folder("default")
        self.make_folder("no-database-yet", database=False)
        (self.root / "stashes" / "notes.txt").write_text("not a stash", encoding="utf-8")

        self.assertEqual([folder.name for folder in bootstrap._stash_dirs()], ["alex", "default-dan"])

    def test_no_stashes_folder_means_no_stashes(self):
        self.assertEqual(bootstrap._stash_dirs(), [])

    def test_the_app_and_the_script_find_the_same_folders(self):
        """Each scan is checked against a fixed list above and in
        test_stashes.py. This one sets them against each other, so a
        rule added to only one of them (a new kind of folder to skip) is
        caught, whatever the fixed lists say."""
        for name in ("alex", "alex copy", "Default", "default-dan", "console", "com10"):
            self.make_folder(name)
        self.make_folder("no-database-yet", database=False)
        (self.root / "stashes" / "notes.txt").write_text("not a stash", encoding="utf-8")

        with mock.patch.object(registry, "stashes_dir", return_value=self.root / "stashes"):
            from_the_app = registry.list_stashes()

        self.assertEqual([folder.name for folder in bootstrap._stash_dirs()], from_the_app)
        self.assertEqual(from_the_app, ["alex", "alex copy", "com10", "console", "default-dan"])


class LaunchScriptImportsNeedNoDjangoTests(SimpleTestCase):
    """scripts/bootstrap.py takes a few modules from the app (the backup
    code, the reserved folder names) while it is still setting things
    up, before Django is configured, and each says it uses the standard
    library only. Nothing else checks that: the suite always has Django
    loaded, so an import of it added to one of them would pass here and
    break the launch."""

    @staticmethod
    def modules_the_script_takes_from_the_app():
        tree = ast.parse(Path(bootstrap.__file__).read_text(encoding="utf-8"))
        return sorted(
            {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "finance"
            }
        )

    @staticmethod
    def files_loaded_by(module):
        """The module's own file and the `__init__.py` of every package
        above it, which Python runs first."""
        parts = module.split(".")
        root = Path(settings.BASE_DIR)
        packages = [root.joinpath(*parts[:depth], "__init__.py") for depth in range(1, len(parts))]
        return [*packages, root.joinpath(*parts[:-1], f"{parts[-1]}.py")]

    @staticmethod
    def imports_outside_the_standard_library(path):
        """Every import in the file, even one inside a function. A
        relative import counts: it names something of the app, which
        could be anything."""
        found = []
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = ["." * node.level + (node.module or "")]
            else:
                continue
            found += [
                name for name in names if name.startswith(".") or name.split(".")[0] not in sys.stdlib_module_names
            ]
        return found

    def test_what_the_script_takes_from_the_app_uses_the_standard_library_only(self):
        modules = self.modules_the_script_takes_from_the_app()
        # So that a change to how the script imports can't leave the
        # loop below with nothing to check.
        self.assertIn("finance.backup", modules)
        self.assertIn("finance.stashes.folder_names", modules)
        for module in modules:
            for path in self.files_loaded_by(module):
                with self.subTest(file=str(path.relative_to(settings.BASE_DIR))):
                    self.assertEqual(self.imports_outside_the_standard_library(path), [])

    def test_the_check_does_notice_an_import_of_django_or_of_the_app(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "not_standard_library_only.py"
        path.write_text(
            "import json\nfrom django.conf import settings\n\n\ndef later():\n    from . import registry\n",
            encoding="utf-8",
        )
        self.assertEqual(self.imports_outside_the_standard_library(path), ["django.conf", "."])


class TestRunFolderIsRemovedAtExitTests(SimpleTestCase):
    """Plan 03, S72: the test settings make a temporary folder for each
    run. Left to Python's own safety net to delete it, a run with
    warnings switched on (python -Wa) ended with a ResourceWarning,
    which could hide a real one. What happens at exit can only be seen
    from outside, so this loads the settings in a Python of its own,
    with its temporary folder pointed at a scratch folder."""

    def test_the_folder_is_deleted_when_python_exits_and_no_warning_is_printed(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        result = subprocess.run(
            [sys.executable, "-W", "always", "-c", "import stashy.settings.test as s; print(s.STASHES_DIR)"],
            cwd=settings.BASE_DIR,
            env={**os.environ, "TMP": scratch.name, "TEMP": scratch.name, "TMPDIR": scratch.name},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ResourceWarning", result.stderr)
        run_folder = Path(result.stdout.strip())
        # So that the test can't pass by the settings using some other
        # folder entirely.
        self.assertEqual(run_folder.parent.resolve(), Path(scratch.name).resolve())
        self.assertFalse(run_folder.exists())


class StashyLocalhostTests(StashTestCase):
    """Stashy.bat opens http://stashy.localhost:<port>/. Browsers send
    every *.localhost name to this computer, so the name needs no
    hosts-file entry."""

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


class OneTodayTests(SimpleTestCase):
    """8.1 (F8): "today" is the stash's own date (its time zone
    preference), so app code asks timezone.localdate(), never the
    computer's clock. backup.py is the one exception: it runs before
    Django is set up, and a backup's name records when the file was made
    here."""

    def test_app_code_never_reads_the_computer_clock_for_today(self):
        root = Path(settings.BASE_DIR)
        for folder in ("finance", "audit", "stashy"):
            for path in (root / folder).rglob("*.py"):
                if "tests" in path.parts or path.name.startswith("test") or "migrations" in path.parts:
                    continue
                if path.name == "backup.py":
                    continue
                with self.subTest(file=str(path.relative_to(root))):
                    self.assertNotRegex(path.read_text(encoding="utf-8"), r"\bdate\.today\(|\bdatetime\.(today|now)\(")


class NoHiddenImportCyclesTests(SimpleTestCase):
    """6.10 (F50): an import inside a function always says why."""

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
