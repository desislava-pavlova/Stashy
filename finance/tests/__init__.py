"""Stashy's finance tests, one module per area, named after the code
they test: test_accounts.py goes with views/accounts.py, test_backup.py
with backup.py, and so on. Easy to mix up: test_reporting.py is the
report maths (reporting.py) and test_reports.py the Reports page;
test_reminder_rules.py is the reminder rules (on the model) and
test_reminders.py the pages; test_settings.py is the Settings page and
test_project.py Django's own settings, logging and the rules the code
follows; test_pages.py holds checks every page must pass. base.py holds
the base classes these tests and audit/tests.py share.

A docstring or comment that starts with a plan reference says where the
test came from: an item number like "1.7 (F6)" is plan 02
(plans/02_architecture_cleanup.md); "S75" or "Plan 03, Q4" is plan 03
(plans/03_structure_review.md); "Plan 04 R4" is plan 04, and "Phase 13
R4" a phase of plan 01. Only tests carry such references: the
application code describes its rules in words, apart from D1 to D10,
plan 01's settled domain decisions, which stay in code comments.

Run one module with `manage.py test finance.tests.test_reminders`.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# The tests run inside a throwaway test stash that only the test
# settings make. This stops a run started with the app's own settings
# (an editor set up that way, say) before any test can get near your
# real stashes.
if not hasattr(settings, "TEST_STASH"):
    raise ImproperlyConfigured("Run the tests with stashy.settings.test (`manage.py test` picks it by itself).")
