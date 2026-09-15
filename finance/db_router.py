"""Multi-stash login (Phase 12, rewritten 2026-09-15): which stash's SQLite file a request's
ORM queries land in is decided per-request, from a thread-local set by
finance.middleware.StashSelectionMiddleware off the session - not once at process start off a
file, as the original restart-based design worked. This is what lets someone switch stashes
without closing and reopening Stashy.bat, at the cost of logging in again each time (by
design: each stash is a fully separate login, so there's no "switch without re-authenticating"
to offer).

Every stash is registered as its own connection alias, named after the stash itself, in
finance.stashes.ensure_registered() - this router just says "route here" for whichever alias
is current; it never registers anything itself.
"""
import threading

_local = threading.local()


def set_current_stash_alias(alias):
    _local.alias = alias or None


def clear_current_stash_alias():
    _local.alias = None


def current_stash_alias():
    return getattr(_local, "alias", None)


class StashRouter:
    """Returning None (no opinion) for anything means Django falls back to `default` - the
    picker's own inert placeholder database, used only before a stash has been chosen for this
    request's session. Sessions themselves are signed-cookie based (see SESSION_ENGINE in
    settings.py) specifically so they never need routing here at all - the very session lookup
    that would tell us "which stash" can't itself live inside a stash's own database."""

    def db_for_read(self, model, **hints):
        return current_stash_alias()

    def db_for_write(self, model, **hints):
        return current_stash_alias()

    def allow_relation(self, obj1, obj2, **hints):
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # No opinion - Django's own default applies: a bare `migrate` only touches `default`,
        # while `migrate --database=<alias>` (how every stash gets its schema, in
        # finance.stashes.create_stash() and scripts/bootstrap.py) still fully applies to that
        # alias. Restricting this would break the second case.
        return None
