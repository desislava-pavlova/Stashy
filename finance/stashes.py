"""Multi-stash login (Phase 12; rewritten 2026-09-15 to switch live, no restart needed): each
"stash" is a fully separate SQLite database under stashes/<name>/db.sqlite3, with its own
backups/ folder next to it. Which stash a request's queries land in is decided per-request by
finance.db_router.StashRouter off the current session (finance.middleware.
StashSelectionMiddleware) - every stash is registered as a real Django connection alias, named
after the stash itself, via ensure_registered() below, so the router always has something real
to point at.

A stash's existence *is* its folder existing: the picker lists stashes by scanning
directories, since choosing one is what puts a database in play for the session at all -
before that, nothing can query a "list of stashes" the way it could query a normal table.
"""
import re
import secrets
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.db.models import signals as model_signals
from django.utils import timezone

from . import regional

STASHES_DIR_NAME = "stashes"
BACKUPS_DIR_NAME = "backups"

# Letters/digits only, no leading dot or dash - safe to use directly as a folder name and as
# a Django username.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")

PIN_RE = re.compile(r"^\d{4}$")

LOCKOUT_THRESHOLD = 5
LOCKOUT_MINUTES = 5

# No 0/O/1/I - easy to tell apart when handwritten or read off a screen once.
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class StashError(Exception):
    """A stash operation couldn't be completed - message is safe to show the user directly."""


def stashes_dir():
    return Path(settings.BASE_DIR) / STASHES_DIR_NAME


def stash_path(name):
    return stashes_dir() / name


def stash_db_path(name):
    return stash_path(name) / "db.sqlite3"


def stash_backups_dir(name):
    return stash_path(name) / BACKUPS_DIR_NAME


def stash_exists(name):
    return stash_db_path(name).exists()


def list_stashes():
    """Every stash's name, alphabetically - a stash's existence *is* its own folder (holding
    a db.sqlite3) existing, so this is a directory scan, not a database query."""
    directory = stashes_dir()
    if not directory.exists():
        return []
    return sorted(p.name for p in directory.iterdir() if p.is_dir() and (p / "db.sqlite3").exists())


def ensure_registered(name):
    """Registers `name`'s db.sqlite3 as a real Django connection alias, named after the stash
    itself, if it isn't already - live stash switching needs every stash a session might pick
    to be a real registered connection for finance.db_router.StashRouter to point requests at,
    not just a file on disk. Safe to call repeatedly (a no-op once registered). Called at
    process start for every stash that already exists (finance/apps.py's ready()), again by
    StashSelectionMiddleware the moment a session picks one (covers a stash created after this
    process started), and directly by create_stash() below for its own brand new stash."""
    if name in connections.databases:
        return
    template = deepcopy(connections.databases["default"])
    template["NAME"] = str(stash_db_path(name))
    connections.databases[name] = template


def _generate_recovery_code():
    groups = ["".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "-".join(groups)


@contextmanager
def _audit_signals_suspended():
    """The audit log's signal handlers always write to whatever finance.db_router.StashRouter
    currently resolves for this thread, which would be wrong here - that's this request's own
    session's stash (or `default`, the picker's placeholder, if none is selected yet), not the
    brand new stash being built. There's nothing to audit yet in a database that was empty a
    moment ago anyway, so this just turns the signals off for the moment."""
    from audit import signals as audit_signals

    uids = ("audit.pre_save", "audit.post_save", "audit.pre_delete", "audit.post_delete", "audit.m2m_changed")
    for uid in uids:
        model_signals.pre_save.disconnect(dispatch_uid=uid)
        model_signals.post_save.disconnect(dispatch_uid=uid)
        model_signals.pre_delete.disconnect(dispatch_uid=uid)
        model_signals.post_delete.disconnect(dispatch_uid=uid)
        model_signals.m2m_changed.disconnect(dispatch_uid=uid)
    try:
        yield
    finally:
        model_signals.pre_save.connect(audit_signals.remember_old_values, dispatch_uid="audit.pre_save")
        model_signals.post_save.connect(audit_signals.log_save, dispatch_uid="audit.post_save")
        model_signals.pre_delete.connect(audit_signals.remember_deleted, dispatch_uid="audit.pre_delete")
        model_signals.post_delete.connect(audit_signals.log_delete, dispatch_uid="audit.post_delete")
        model_signals.m2m_changed.connect(audit_signals.log_m2m, dispatch_uid="audit.m2m_changed")


def validate_stash_name(name):
    if not NAME_RE.match(name or ""):
        raise StashError("Names can only use letters, numbers, hyphens and underscores, and must start with a letter or number.")
    if stash_exists(name):
        raise StashError(f'A stash named "{name}" already exists.')


def validate_pin(pin):
    if not PIN_RE.match(pin or ""):
        raise StashError("PIN must be exactly 4 digits.")


def create_stash(name, pin):
    """Builds stashes/<name>/ (a fresh, fully migrated db.sqlite3 plus its own backups/
    folder), a single superuser named `name` with `pin` as its password, and a Profile row
    holding a freshly generated recovery code's hash. Returns the recovery code in the
    clear - the only time it's ever available, since only its (hashed) fingerprint is kept.

    Registers the new stash as a permanent connection alias (ensure_registered()) rather than
    a throwaway one - the whole point of the live-switching rewrite is that the caller's own
    session can select this stash and use it immediately, with nothing to restart."""
    validate_stash_name(name)
    validate_pin(pin)

    stash_path(name).mkdir(parents=True, exist_ok=True)
    stash_backups_dir(name).mkdir(exist_ok=True)
    ensure_registered(name)
    call_command("migrate", database=name, verbosity=0, interactive=False, run_syncdb=True)

    from django.contrib.auth import get_user_model

    from .models import Profile

    recovery_code = _generate_recovery_code()
    with _audit_signals_suspended():
        user = get_user_model().objects.db_manager(name).create_superuser(
            username=name, email="", password=pin
        )
        detected = regional.detect_from_computer()
        Profile.objects.using(name).create(
            user=user,
            time_zone=detected.time_zone,
            date_format=detected.date_format,
            number_format=detected.number_format,
            first_day_of_week=detected.first_day_of_week,
            recovery_code_hash=_hash(recovery_code),
        )

    return recovery_code


def _hash(secret_value):
    from django.contrib.auth.hashers import make_password

    return make_password(secret_value)


def _check(secret_value, hashed):
    from django.contrib.auth.hashers import check_password

    return bool(hashed) and check_password(secret_value, hashed)


def profile_for_current_stash():
    """The single Profile row in whatever stash this request's session has selected -
    finance.db_router.StashRouter routes this plain, unqualified query there automatically.
    Every stash has exactly one user, so there's exactly one Profile, no `request.user`
    needed to find it."""
    from .models import Profile

    return Profile.objects.select_related("user").first()


def is_locked_out(profile):
    return bool(profile.locked_until) and profile.locked_until > timezone.now()


def lockout_seconds_remaining(profile):
    if not is_locked_out(profile):
        return 0
    return max(0, int((profile.locked_until - timezone.now()).total_seconds()))


def register_failed_attempt(profile):
    """Counts a wrong PIN/recovery code. Locks out at the threshold rather than extending an
    already-active lockout indefinitely if someone keeps guessing while locked."""
    if is_locked_out(profile):
        return
    profile.failed_pin_attempts += 1
    if profile.failed_pin_attempts >= LOCKOUT_THRESHOLD:
        profile.locked_until = timezone.now() + timedelta(minutes=LOCKOUT_MINUTES)
        profile.failed_pin_attempts = 0
    profile.save(update_fields=["failed_pin_attempts", "locked_until"])


def register_successful_login(profile):
    if profile.failed_pin_attempts or profile.locked_until:
        profile.failed_pin_attempts = 0
        profile.locked_until = None
        profile.save(update_fields=["failed_pin_attempts", "locked_until"])


def reset_pin_with_recovery_code(profile, recovery_code, new_pin):
    """True (and rotates in a fresh recovery code, invalidating the old one) if the recovery
    code matches; False otherwise, counting the same as a wrong PIN toward lockout. Returns
    (ok, new_recovery_code_or_None)."""
    if is_locked_out(profile):
        return False, None
    if not _check(recovery_code, profile.recovery_code_hash):
        register_failed_attempt(profile)
        return False, None
    new_recovery_code = _generate_recovery_code()
    profile.user.set_password(new_pin)
    profile.user.save(update_fields=["password"])
    profile.recovery_code_hash = _hash(new_recovery_code)
    profile.failed_pin_attempts = 0
    profile.locked_until = None
    profile.save(update_fields=["recovery_code_hash", "failed_pin_attempts", "locked_until"])
    return True, new_recovery_code


def reset_pin_directly(name, new_pin):
    """The manage.py reset_pin CLI last resort, for when the recovery code is lost too -
    operates on the named stash directly rather than whatever a session might currently have
    selected, since the locked-out stash isn't necessarily that one. Running this already
    requires file-level access to the stash, so it needs no authentication of its own."""
    validate_pin(new_pin)
    if not stash_exists(name):
        raise StashError(f'No stash named "{name}".')

    ensure_registered(name)
    from django.contrib.auth import get_user_model

    from .models import Profile

    # User isn't a finance-app model (not audit-tracked) and .update() sends no signals at
    # all, so neither of these needs the audit signals suspended the way create_stash() does -
    # this command only ever runs standalone (manage.py), never alongside the live server, so
    # there's no risk of it racing another request's own audit entries either.
    user = get_user_model().objects.db_manager(name).get(username=name)
    user.set_password(new_pin)
    user.save(using=name, update_fields=["password"])
    Profile.objects.using(name).filter(user=user).update(failed_pin_attempts=0, locked_until=None)
