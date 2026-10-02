import sys

from django.apps import AppConfig


class FinanceConfig(AppConfig):
    name = "finance"

    def ready(self):
        # Multi-stash login (Phase 12): every stash that already exists on disk needs to be a
        # real Django connection alias before finance.db_router.StashRouter can route a
        # session's queries to it - registering them here, once, at process start covers
        # everything that existed before this launch; StashSelectionMiddleware registers a
        # stash lazily too, for the rare case one was added after this process started.
        #
        # Never under `manage.py test`: Django's test runner manages connection isolation
        # (which alias a given TestCase may touch) off the *live* connections.databases
        # registry, not just settings.DATABASES - registering a real stash here would make it
        # a third, unmanaged alias the test isolation machinery trips over, regardless of
        # whether any test actually queries it. This reads the command line rather than
        # settings.TESTING on purpose: a test run must never register the real stashes, even
        # one started with the app's own settings by mistake.
        if len(sys.argv) > 1 and sys.argv[1] == "test":
            return

        from . import stashes

        for name in stashes.list_stashes():
            stashes.ensure_registered(name)
