"""Who/why information for audit entries, carried implicitly through the code that makes changes.

A ContextVar is like a global variable that is private to the current request
(or thread), so model signals can find out "who is doing this and why" without
every function having to pass that information along.
"""

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace

USER = "user"
SYSTEM = "system"


@dataclass(frozen=True)
class AuditContext:
    source: str = SYSTEM
    reason: str = ""
    operation: str = ""
    request: object = None


# AuditContext is frozen, so sharing one default instance is safe.
_current = ContextVar("audit_context", default=AuditContext())  # noqa: B039


def current_context():
    return _current.get()


def new_operation_id():
    return uuid.uuid4().hex[:12]


@contextmanager
def _activate(context):
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


def request_context(request):
    return _activate(AuditContext(source=USER, operation=new_operation_id(), request=request))


@contextmanager
def system_action(reason):
    """Log changes made inside this block as automatic, with a human-readable reason."""
    parent = _current.get()
    context = replace(parent, source=SYSTEM, reason=reason, operation=parent.operation or new_operation_id())
    with _activate(context):
        yield
