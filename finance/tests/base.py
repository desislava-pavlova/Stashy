"""Base classes shared by finance's tests (and audit's).

Every test that uses the database runs inside the test stash: the
throwaway stash that stashy/settings/test.py makes for each test run
(settings.TEST_STASH). The test's own queries go to that stash's
database, and its test client starts with the stash chosen, as if picked
on the picker - so every request goes through the real middleware, the
way it does in Stashy itself.
"""

import ast
import csv
import io
import re
import shutil
import sqlite3
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import MD5PasswordHasher
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import URLPattern, reverse

from openpyxl import Workbook

from .. import db_router, starter_categories
from .. import urls as finance_urls
from ..models import Account, Category, Payee, Profile, Reminder, Transaction
from ..stashes import registry


# Tests that patch timezone.localdate pretend today is one of these
# days, so the fixed dates they set up keep their meaning (overdue, due
# soon, this month).
TODAY_12_SEPT = date(2026, 9, 12)
TODAY_13_SEPT = date(2026, 9, 13)
TODAY_14_SEPT = date(2026, 9, 14)
TODAY_19_SEPT = date(2026, 9, 19)

# Django template syntax that shows up as visible text when a tag or
# comment goes wrong (a {# #} comment is one line only). A rendered page
# must contain none of it.
LEAKED_TEMPLATE_SYNTAX = ("{%", "%}", "{#", "#}")


def leaked_syntax_in(page):
    """The Django template syntax left in a rendered page (its text, as
    a string), as a list: the four of LEAKED_TEMPLATE_SYNTAX, and "{{"
    and "}}" outside <script> blocks, where JavaScript uses them for
    itself."""
    outside_scripts = re.sub(r"<script\b.*?</script>", "", page, flags=re.DOTALL | re.IGNORECASE)
    return [s for s in LEAKED_TEMPLATE_SYNTAX if s in page] + [
        s for s in ("{{", "}}") if s in outside_scripts
    ]


def imported_modules(source):
    """Every module an import in the Python `source` names, as a list:
    relative ones with their dots, and `from . import x` as `.x`. For
    the tests that keep a file's imports to a rule."""
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            names += [base if base.strip(".") else base + alias.name for alias in node.names]
    return names


# Three amounts that add up to zero. SQLite adds decimals as floats, so
# their sum comes back as a tiny negative number and not 0: what the
# tests of the money rule start from.
AMOUNTS_THAT_CANCEL_OUT = ("-0.10", "-0.20", "0.30")

# Values for the URL parameters other than <pk>, so every pattern in
# finance/urls.py can be reversed.
OTHER_URL_VALUES = {"step": 1, "report": "spending"}


def every_finance_url(pk_of=lambda name: 1):
    """Yields (name, url) for every pattern in finance/urls.py.
    pk_of(name) gives the value for a pattern's <pk>, so a test can
    point the URLs at rows it made."""
    for pattern in finance_urls.urlpatterns:
        assert isinstance(pattern, URLPattern)
        kwargs = {}
        for parameter in pattern.pattern.converters:
            kwargs[parameter] = (
                pk_of(pattern.name) if parameter == "pk" else OTHER_URL_VALUES[parameter]
            )
        yield pattern.name, reverse(f"finance:{pattern.name}", kwargs=kwargs)


def csv_rows(response):
    """The rows of a CSV reply, as lists of text. The exports start with
    a byte order mark, which "utf-8-sig" drops."""
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))


def csv_upload(text, name="bank.csv", encoding="utf-8"):
    """A CSV file as the import page's file box would send it."""
    return SimpleUploadedFile(name, text.encode(encoding), content_type="text/csv")


def post_import(client, account, upload, *, follow=False, **form_data):
    """Posts `upload` to the import page for `account`. `form_data` adds
    to the form, say date_format="mdy"."""
    data = {"account": account.pk, "file": upload, **form_data}
    return client.post(reverse("finance:import_transactions"), data, follow=follow)


def ofx_file(posted="20260105", amount="-12.50"):
    """A small OFX file (bytes) with one transaction, Lidl's "weekly
    shop". `posted` and `amount` are text, so a test can put in a date
    or an amount that the importer has to refuse."""
    return f"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX><BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS><CURDEF>EUR<BANKACCTFROM><BANKID>1<ACCTID>2<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260101<DTEND>20260131
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>{posted}<TRNAMT>{amount}<FITID>1
<NAME>Lidl<MEMO>weekly shop</STMTTRN>
</BANKTRANLIST><LEDGERBAL><BALAMT>100<DTASOF>20260131</LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
""".encode()


def create_accounts_with_a_transaction(count):
    """`count` new accounts with one 10.00 transaction each. A number in
    each name keeps two calls from clashing, so a test can compare the
    queries of one account and of several."""
    for i in range(count):
        account = Account.objects.create(name=f"Account {count}-{i}")
        Transaction.objects.create(account=account, date=date(2026, 1, 5), amount=Decimal("10.00"))


def create_reminders(account, count):
    """`count` new one-time reminders, half of them with a payee and the
    rest with only a category (a subcategory of "Food"), so a page that
    shows either one can be checked for the queries it makes."""
    food = Category.objects.get_or_create(
        name="Food", category_type=Category.CategoryType.EXPENSE
    )[0]
    for i in range(count):
        Reminder.objects.create(
            account=account,
            payee=Payee.objects.create(name=f"Shop {count}-{i}") if i % 2 else None,
            category=food.create_subcategory(f"Groceries {count}-{i}"),
            amount=Decimal("10.00"),
            next_due_date=date(2026, 9, 14),
            frequency="one_time",
        )


class NewerMD5PasswordHasher(MD5PasswordHasher):
    """Stands in for the stronger hasher a Django update brings."""

    algorithm = "md5_newer"


# For override_settings(PASSWORD_HASHERS=...): with the newer hasher
# listed first, Django re-saves an MD5-hashed PIN in the newer format
# the next time the PIN is checked - which changes the hash the login is
# tied to.
REHASHING_PASSWORD_HASHERS = [
    f"{__name__}.NewerMD5PasswordHasher",
    "django.contrib.auth.hashers.MD5PasswordHasher",
]


class StashClient(Client):
    """A test client that has already chosen the test stash, the way the
    picker does it: by putting the stash's name in the session."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.choose_stash(settings.TEST_STASH)

    def choose_stash(self, name):
        session = self.session
        session["stash"] = name
        session.save()
        # The session lives in a signed cookie, so a saved session is a
        # new cookie value.
        self.cookies[settings.SESSION_COOKIE_NAME] = session.session_key

    def force_login(self, user, backend=None):
        # Django's login() empties a session whose earlier login no
        # longer matches (after a PIN change, say), and the chosen stash
        # goes with it. A browser finds its way back through the "last
        # stash used" cookie; this client simply chooses the same stash
        # again.
        stash = self.session.get("stash")
        super().force_login(user, backend)
        if stash and self.session.get("stash") != stash:
            self.choose_stash(stash)


class StashTestCase(TestCase):
    """A test inside the test stash. Its own queries and its requests
    use the stash's database. `default`, the picker's empty placeholder,
    is off limits unless a class adds it to `databases` - so code that
    loses track of its stash fails here instead of quietly using the
    placeholder."""

    databases = {settings.TEST_STASH}
    client_class = StashClient

    @classmethod
    def setUpClass(cls):
        # Before super(), which already runs setUpTestData().
        cls.enterClassContext(db_router.use_stash(settings.TEST_STASH))
        super().setUpClass()

    def get_and_count_queries(self, url):
        """(the response, the number of queries it took) of a page's
        second visit: a session's first visit also makes its Profile,
        which would count as extra queries."""
        self.client.get(url)
        with CaptureQueriesContext(connections[settings.TEST_STASH]) as queries:
            response = self.client.get(url)
        return response, len(queries)


class TemporaryStashesMixin:
    """Real stash aliases backed by files in a temporary folder -
    registered at runtime the way the live app does it, and removed
    again afterwards. A test calls make_stashes() first, then makes the
    stashes it needs: bare folders with make_stashes(names), or whole
    ones (migrated, with a user and a Profile) with create_stash(). A
    class whose tests all need the same stash can build it once in
    setUpClass instead (see ResetPinCommandTests)."""

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
            self.addCleanup(self.unregister, name)
        self.addCleanup(db_router.set_current_stash_alias, None)
        self.allow_databases(*names)

    def allow_databases(self, *names):
        """Django's test runner only lets a test touch the databases it
        lists. A stash's alias doesn't exist until the test makes it, so
        it is added for this one test."""
        test_class = type(self)
        self.addCleanup(setattr, test_class, "databases", test_class.databases)
        test_class.databases = frozenset(test_class.databases) | set(names)

    def create_stash(self, owner_name, pin):
        """registry.create_stash() in the temporary folder. It registers
        the stash's alias itself, so the test has to be allowed that
        alias up front; it is unregistered again afterwards. Returns
        (folder name, recovery code), like registry.create_stash()."""
        self.allow_databases(registry._folder_name_from_owner(owner_name))
        folder, recovery_code = registry.create_stash(owner_name, pin)
        # Cleanups run last in, first out: the stash is unregistered,
        # then its folder goes, so a test class that shares one
        # temporary folder never finds the next test's leftovers.
        self.addCleanup(shutil.rmtree, registry.stash_path(folder), ignore_errors=True)
        self.addCleanup(self.unregister, folder)
        return folder, recovery_code

    @staticmethod
    def unregister(name):
        connections[name].close()
        del connections[name]
        del connections.databases[name]


class StarterCategoriesFileMixin:
    """Swaps the spreadsheet of starter categories for one in a
    temporary folder. A test calls use_a_starter_categories_file() in
    setUp(), then fills the file with write_starter_categories({"Basic":
    [("Expense", "Groceries", None)]}): each sheet is a name and its
    (type, category, subcategory) rows."""

    def use_a_starter_categories_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.starter_categories_file = Path(tmp.name) / "starter_categories.xlsx"
        patcher = mock.patch.object(
            starter_categories,
            "starter_categories_path",
            return_value=self.starter_categories_file,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_starter_categories(self, sheets):
        workbook = Workbook()
        workbook.remove(workbook.active)
        for name, rows in sheets.items():
            sheet = workbook.create_sheet(name)
            sheet.append(["Type", "Category", "Subcategory"])
            for row in rows:
                sheet.append(row)
        workbook.save(self.starter_categories_file)


class LoggedInTestCase(StashTestCase):
    """Logged in as "tester" (self.user), the test stash's one user - a
    stash's user is named after its folder.

    The user is made once per class, in setUpTestData(): Django undoes
    each test's database changes and hands every test its own copy of
    self.user, so no test sees another's. Logging in is per test,
    because each test gets a new client."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "tester", password="not-a-real-password-123"
        )

    def setUp(self):
        self.client.force_login(self.user)


class LoggedInAsSuperuserTestCase(LoggedInTestCase):
    """Logged in as the stash's superuser "tester" (self.user), whom the
    admin needs."""

    @classmethod
    def setUpTestData(cls):
        # Not LoggedInTestCase's own: that one makes an ordinary user
        # with the same name.
        cls.user = get_user_model().objects.create_superuser(
            "tester", password="not-a-real-password-123"
        )


class LoggedInWithAccountTestCase(LoggedInTestCase):
    """Logged in, with one account called "Checking" (self.account)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.account = Account.objects.create(name="Checking")


class AlexsStashTestCase(LoggedInTestCase):
    """Logged in, and the stash's owner is called Alex (self.profile),
    so pages say "Alex's stash"."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.profile = Profile.for_user(cls.user)
        cls.profile.owner_name = "Alex"
        cls.profile.save(update_fields=["owner_name"])
