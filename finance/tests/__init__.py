"""Stashy's finance tests, one module per area, named after the code they test: test_accounts.py
goes with views/accounts.py, test_backup.py with backup.py, and so on. Easy to mix up:
test_reporting.py is the report maths (reporting.py) and test_reports.py the Reports page;
test_settings.py is the Settings page and test_project.py Django's own settings, logging and the
rules the code follows; test_pages.py holds checks every page must pass. base.py holds the base
classes these tests and audit/tests.py share.

A class docstring that starts with an item number like "1.7 (F6)" points to that item (and
finding) in plans/02_architecture_cleanup.md. "Plan 03 R4" and "Phase 13 R4" (a phase of plan
01) name their plan.

Run one module with `manage.py test finance.tests.test_reminders`.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# The tests run inside a throwaway test stash that only the test settings make. This stops a
# run started with the app's own settings (an editor set up that way, say) before any test can
# get near your real stashes.
if not hasattr(settings, "TEST_STASH"):
    raise ImproperlyConfigured("Run the tests with stashy.settings.test (`manage.py test` picks it by itself).")
