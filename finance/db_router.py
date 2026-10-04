"""Which stash's SQLite file a request's ORM queries land in is decided per request, from the
"current stash" set by finance.middleware.StashSelectionMiddleware off the session. That's what
lets someone switch stashes without restarting Stashy.bat, at the cost of logging in again each
time (by design: each stash is a fully separate login).

Every stash is registered as its own connection alias, named after the stash itself, in
finance.stashes.registry.ensure_registered() - this router just says "route here" for whichever
alias is current; it never registers anything itself.

The current stash is kept in a ContextVar, the same mechanism audit.context uses: a value
private to the current request's thread (and to its async task, if Django ever runs one).
"""

from contextlib import contextmanager
from contextvars import ContextVar

_current_alias = ContextVar("stash_alias", default=None)


@contextmanager
def use_stash(alias):
    """Route the queries made inside this block to stash `alias` (None: the placeholder), then
    put back whichever stash was current before."""
    token = _current_alias.set(alias or None)
    try:
        yield
    finally:
        _current_alias.reset(token)


def set_current_stash_alias(alias):
    _current_alias.set(alias or None)


def clear_current_stash_alias():
    _current_alias.set(None)


def current_stash_alias():
    return _current_alias.get()


class StashRouter:
    """Returning None (no opinion) for anything means Django falls back to `default` - the
    picker's own inert placeholder database, used only before a stash has been chosen for this
    request's session. Sessions themselves are signed-cookie based (see SESSION_ENGINE in
    settings/base.py) specifically so they never need routing here at all - the very session lookup
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
        # finance.stashes.registry.create_stash() and scripts/bootstrap.py) still fully applies to that
        # alias. Restricting this would break the second case.
        return None
